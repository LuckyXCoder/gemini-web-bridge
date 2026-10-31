"""Optional single-account HTTP image service. Run with python -m gemini_webapi.server."""

import asyncio
import base64
import hmac
import os
import sys
import time
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Literal

import orjson
from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from PIL import Image as PillowImage
from pydantic import BaseModel, ConfigDict, Field

from . import GeminiClient
from .client import _SUPPORTED_IMAGE_ASPECT_RATIOS
from .types import GeneratedImage
from .utils import logger

MODELS = {"gemini-web-image": False, "gemini-web-pro-image": True}


class SingleSubmissionClient(GeminiClient):
    async def _generate(self, *args, **kwargs):
        # Initialization is explicit in the worker. Bypass only the generation retry
        # decorator; read-only recovery and cookie refresh remain library-owned.
        async for output in GeminiClient._generate.__wrapped__(self, *args, **kwargs):
            yield output


class ImageRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    model: Literal["gemini-web-image", "gemini-web-pro-image"] = "gemini-web-image"
    prompt: str = Field(min_length=1, max_length=16000)
    n: Literal[1] = 1
    response_format: Literal["b64_json"] = "b64_json"
    size: Literal["auto"] = "auto"
    aspect_ratio: str | None = None


async def generate_image(client, request, chat_model):
    response = await client.generate_content(
        request.prompt,
        model=chat_model,
        pro_image=MODELS[request.model],
        aspect_ratio=request.aspect_ratio,
    )
    generated = next((x for x in response.images if isinstance(x, GeneratedImage)), None)
    if generated is None:
        raise RuntimeError("No generated image")
    with TemporaryDirectory(prefix="gemini-image-") as directory:
        saved = Path(await generated.save(path=directory, filename="image.bin", verbose=False))
        # A successful HTTP response or an image-shaped URL is not enough.
        with PillowImage.open(saved) as image:
            image.verify()
        encoded = base64.b64encode(saved.read_bytes()).decode("ascii")
    return {"created": int(time.time()), "data": [{"b64_json": encoded}]}


def create_app(client=None, api_key=None, timeout=300, queue_size=4):
    """One process, one client, one worker; client injection is for offline tests."""
    key = api_key or os.environ.get("GEMINI_BRIDGE_API_KEY")
    if not key:
        raise ValueError("GEMINI_BRIDGE_API_KEY is required")
    if timeout <= 0 or queue_size < 1:
        raise ValueError("timeout and queue_size must be positive")
    if client is None:
        path = os.environ.get("GEMINI_BRIDGE_COOKIES")
        if not path:
            raise ValueError("GEMINI_BRIDGE_COOKIES must point to a cookies JSON file")
        data = orjson.loads(Path(path).read_bytes())
        cookies = data.get("cookies", data)
        if not isinstance(cookies, dict) or not cookies.get("__Secure-1PSID"):
            raise ValueError("Cookie file must contain __Secure-1PSID")
        client = SingleSubmissionClient(
            cookies["__Secure-1PSID"],
            cookies.get("__Secure-1PSIDTS", ""),
            proxy=os.environ.get("GEMINI_PROXY") or None,
        )
    chat_model = os.environ.get("GEMINI_BRIDGE_CHAT_MODEL", "gemini-pro")

    async def initialize():
        await client.init(timeout=timeout, auto_close=False, auto_refresh=True, verbose=False)
        if not client.access_token or not client._check_account_status():
            raise RuntimeError("Gemini account is not authenticated or available")

    async def worker(queue):
        while True:
            request, future = await queue.get()
            try:
                async with asyncio.timeout(timeout):
                    await initialize()
                    result = await generate_image(client, request, chat_model)
                if not future.done():
                    future.set_result(result)
            except TimeoutError:
                if not future.done():
                    future.set_result(
                        error_response(
                            504,
                            "generation_timeout",
                            "Generation timed out; upstream may still be processing. No automatic resubmission.",
                        )
                    )
                await client.close()
            except Exception:
                if not future.done():
                    future.set_result(
                        error_response(
                            502,
                            "generation_failed",
                            "Generation or image download failed. No automatic resubmission.",
                        )
                    )
                await client.close()
            finally:
                queue.task_done()

    @asynccontextmanager
    async def lifespan(app):
        queue = asyncio.Queue(maxsize=queue_size)
        app.state.queue = queue
        task = None
        try:
            async with asyncio.timeout(timeout):
                await initialize()
            task = asyncio.create_task(worker(queue))
            yield
        finally:
            if task:
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task
            await client.close()

    async def authorize(authorization: str | None = Header(default=None)):
        provided = authorization or ""
        if not hmac.compare_digest(provided.encode(), f"Bearer {key}".encode()):
            raise HTTPException(status_code=401, detail="Invalid API key")

    app = FastAPI(
        title="Gemini Web Bridge",
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )

    @app.exception_handler(HTTPException)
    async def http_error(request, exc):
        return error_response(
            exc.status_code,
            "authentication_error" if exc.status_code == 401 else "invalid_request",
            str(exc.detail),
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        # Do not echo request bodies or credentials in validation responses.
        return error_response(
            400,
            "invalid_request",
            "Unsupported or invalid image parameters. See the service documentation.",
        )

    @app.get("/v1/models", dependencies=[Depends(authorize)])
    async def models():
        return {
            "object": "list",
            "data": [
                {"id": name, "object": "model", "created": 0, "owned_by": "gemini-web-bridge"}
                for name in MODELS
            ],
        }

    @app.post("/v1/images/generations", dependencies=[Depends(authorize)])
    async def images(request: ImageRequest):
        if not request.prompt.strip() or (
            request.aspect_ratio is not None
            and request.aspect_ratio not in _SUPPORTED_IMAGE_ASPECT_RATIOS
        ):
            raise HTTPException(
                status_code=400, detail="Invalid prompt or unsupported aspect_ratio"
            )
        future = asyncio.get_running_loop().create_future()
        try:
            app.state.queue.put_nowait((request, future))
        except asyncio.QueueFull:
            return error_response(
                429, "queue_full", "Image queue is full. No generation was submitted."
            )
        # Keep the worker independent from client cancellation. A disconnected caller
        # must not release the single-account slot while its generation is still active.
        return await asyncio.shield(future)

    return app


def error_response(status, code, message):
    return JSONResponse(
        status_code=status, content={"error": {"message": message, "type": code, "code": code}}
    )


if __name__ == "__main__":
    import uvicorn

    logger.remove()
    logger.add(sys.stderr, level="WARNING")
    uvicorn.run(create_app(), host="127.0.0.1", port=18088, workers=1)
