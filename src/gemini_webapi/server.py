"""Optional multi-account HTTP image service. Run with python -m gemini_webapi.server."""

import asyncio
import base64
import hmac
import os
import sys
import time
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass
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
from .exceptions import AuthError, UsageLimitExceededError
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


@dataclass
class Account:
    id: str
    client: object
    status: str = "starting"


class AccountConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")
    cookies_file: str
    cache_dir: str
    enabled: bool = True


def load_accounts():
    """Read account files once; never mutate process-wide cache settings."""
    config_path = os.environ.get("GEMINI_BRIDGE_ACCOUNTS")
    cookie_path = os.environ.get("GEMINI_BRIDGE_COOKIES")
    if config_path and cookie_path:
        raise ValueError("Set GEMINI_BRIDGE_ACCOUNTS or GEMINI_BRIDGE_COOKIES, not both")
    if not config_path:
        if not cookie_path:
            raise ValueError("GEMINI_BRIDGE_ACCOUNTS or GEMINI_BRIDGE_COOKIES is required")
        specs = [("default", Path(cookie_path).expanduser(), os.environ.get("GEMINI_COOKIE_PATH"))]
    else:
        path = Path(config_path).expanduser().resolve()
        try:
            raw = orjson.loads(path.read_bytes())
            if (
                not isinstance(raw, dict)
                or set(raw) != {"accounts"}
                or not isinstance(raw["accounts"], list)
            ):
                raise ValueError()
            configs = [AccountConfig.model_validate(entry) for entry in raw["accounts"]]
        except Exception:
            raise ValueError("Invalid account configuration file") from None
        if len({entry.id for entry in configs}) != len(configs):
            raise ValueError("Account IDs must be unique")
        specs = []
        for entry in configs:
            if entry.enabled:
                cookies = (path.parent / Path(entry.cookies_file).expanduser()).resolve()
                cache = (path.parent / Path(entry.cache_dir).expanduser()).resolve()
                specs.append((entry.id, cookies, cache))
    if not specs:
        raise ValueError("At least one enabled account is required")
    accounts = []
    seen_psids = set()
    seen_dirs = set()
    for account_id, cookie_file, cache_dir in specs:
        try:
            raw = orjson.loads(cookie_file.read_bytes())
            cookies = raw.get("cookies", raw)
            psid = cookies.get("__Secure-1PSID")
            psidts = cookies.get("__Secure-1PSIDTS", "")
            if not isinstance(psid, str) or not psid.strip() or not isinstance(psidts, str):
                raise ValueError()
        except Exception:
            raise ValueError(f"Invalid cookie file for account {account_id}") from None
        if psid in seen_psids:
            raise ValueError("Duplicate account cookies are not allowed")
        seen_psids.add(psid)
        if cache_dir is not None:
            cache_dir = Path(cache_dir).expanduser().resolve()
            if cache_dir in seen_dirs:
                raise ValueError("Each account must have a distinct cache directory")
            seen_dirs.add(cache_dir)
        accounts.append(
            Account(
                account_id,
                SingleSubmissionClient(
                    psid,
                    psidts,
                    proxy=os.environ.get("GEMINI_PROXY") or None,
                    cookie_cache_dir=cache_dir,
                    use_browser_cookies=False,
                ),
            )
        )
    # Validate the complete configuration before creating directories.
    for directory in seen_dirs:
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        directory.chmod(0o700)
    return accounts


def create_app(client=None, api_key=None, timeout=300, queue_size=4, accounts=None):
    """One process, one worker per account, one bounded shared queue."""
    key = api_key or os.environ.get("GEMINI_BRIDGE_API_KEY")
    if not key:
        raise ValueError("GEMINI_BRIDGE_API_KEY is required")
    if timeout <= 0 or queue_size < 1:
        raise ValueError("timeout and queue_size must be positive")
    if client is not None and accounts is not None:
        raise ValueError("Pass client or accounts, not both")
    pool = (
        accounts
        if accounts is not None
        else ([Account("default", client)] if client is not None else load_accounts())
    )
    if (
        not pool
        or len({a.id for a in pool}) != len(pool)
        or len({id(a.client) for a in pool}) != len(pool)
    ):
        raise ValueError("Accounts must have distinct IDs and clients")
    chat_model = os.environ.get("GEMINI_BRIDGE_CHAT_MODEL", "gemini-pro")

    async def initialize(account):
        await account.client.init(
            timeout=timeout, auto_close=False, auto_refresh=True, verbose=False
        )
        if not account.client.access_token or not account.client._check_account_status():
            raise AuthError("Gemini account is not authenticated or available")

    async def close(account):
        try:
            await account.client.close()
        except Exception:
            # Keep cleanup failures from killing workers and stranding queued callers.
            account.status = "paused"
            logger.warning("Account {} cleanup failed; paused", account.id)

    def available():
        return any(a.status in ("idle", "busy") for a in pool)

    def drain_unavailable(queue):
        if available():
            return
        while not queue.empty():
            _, future = queue.get_nowait()
            if not future.done():
                future.set_result(
                    error_response(
                        503,
                        "no_available_accounts",
                        "No available accounts. No generation was submitted.",
                    )
                )
            queue.task_done()

    async def worker(account, queue):
        while account.status != "paused":
            request, future = await queue.get()
            initialized = False
            try:
                if future.cancelled():
                    continue
                account.status = "busy"
                async with asyncio.timeout(timeout):
                    await initialize(account)
                    initialized = True
                    result = await generate_image(account.client, request, chat_model)
                if not future.done():
                    future.set_result(result)
            except asyncio.CancelledError:
                if not future.done():
                    future.set_result(
                        error_response(
                            503,
                            "service_stopping",
                            "Service is stopping; generation outcome may be unknown.",
                        )
                    )
                raise
            except Exception as exc:
                if (
                    not initialized
                    or isinstance(exc, (AuthError, UsageLimitExceededError))
                    or getattr(exc, "status_code", None) == 401
                    or not account.client._check_account_status()
                ):
                    account.status = "paused"
                if not initialized:
                    result = error_response(
                        503,
                        "account_unavailable",
                        "Selected account is unavailable. No generation was submitted.",
                    )
                elif isinstance(exc, TimeoutError):
                    result = error_response(
                        504,
                        "generation_timeout",
                        "Generation timed out; upstream may still be processing. No automatic resubmission.",
                    )
                else:
                    result = error_response(
                        502,
                        "generation_failed",
                        "Generation or image download failed. No automatic resubmission.",
                    )
                if not future.done():
                    future.set_result(result)
                await close(account)
            finally:
                if account.status != "paused":
                    account.status = "idle"
                queue.task_done()
                drain_unavailable(queue)

    async def start_account(account):
        try:
            async with asyncio.timeout(timeout):
                await initialize(account)
            account.status = "idle"
        except Exception:
            account.status = "paused"
            await close(account)
            logger.warning("Account {} initialization failed; paused", account.id)

    @asynccontextmanager
    async def lifespan(app):
        queue = asyncio.Queue(maxsize=queue_size)
        app.state.queue = queue
        app.state.accounts = pool
        tasks = []
        try:
            await asyncio.gather(*(start_account(a) for a in pool))
            tasks = [asyncio.create_task(worker(a, queue)) for a in pool if a.status == "idle"]
            yield
        finally:
            for task in tasks:
                task.cancel()
            for task in tasks:
                with suppress(asyncio.CancelledError):
                    await task
            for account in pool:
                account.status = "paused"
            drain_unavailable(queue)
            await asyncio.gather(*(close(a) for a in pool))

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

    @app.get("/v1/accounts", dependencies=[Depends(authorize)])
    async def account_status():
        return {"data": [{"id": a.id, "status": a.status} for a in pool]}

    @app.post("/v1/images/generations", dependencies=[Depends(authorize)])
    async def images(request: ImageRequest):
        if not request.prompt.strip() or (
            request.aspect_ratio is not None
            and request.aspect_ratio not in _SUPPORTED_IMAGE_ASPECT_RATIOS
        ):
            raise HTTPException(
                status_code=400, detail="Invalid prompt or unsupported aspect_ratio"
            )
        if not available():
            return error_response(
                503, "no_available_accounts", "No available accounts. No generation was submitted."
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
        try:
            return await asyncio.shield(future)
        except asyncio.CancelledError:
            future.cancel()
            raise

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
