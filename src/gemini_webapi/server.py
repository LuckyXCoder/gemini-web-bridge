"""Optional multi-account HTTP image service. Run with python -m gemini_webapi.server."""

import asyncio
import base64
import hmac
import os
import sys
import time
from collections import deque
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass, field
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Annotated, Literal

import orjson
from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from PIL import Image as PillowImage
from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from . import GeminiClient
from .client import _SUPPORTED_IMAGE_ASPECT_RATIOS
from .exceptions import AuthError, UsageLimitExceededError
from .types import GeneratedImage
from .utils import logger

DisplayName = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True, min_length=1, max_length=100, pattern=r"^[^\x00-\x1f\x7f]+$"
    ),
]

DEFAULT_TEST_PROMPT = "生成一张窗边橘猫的图片，温暖自然光，完整展示猫的身体，不要文字或水印。"  # noqa: RUF001

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
    enabled: bool = True
    recent: deque = field(default_factory=lambda: deque(maxlen=10))
    last_checked_at: float | None = None
    login_status: str = "unknown"
    checking: bool = False
    removing: bool = False
    task: asyncio.Task | None = None
    display_name: str | None = None
    image_test: dict | None = None


class AccountConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")
    display_name: DisplayName | None = None
    cookies_file: str
    cache_dir: str
    enabled: bool = True


def load_accounts(allow_empty=False):
    """Read account files once; never mutate process-wide cache settings."""
    names = {}
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
        names = {entry.id: entry.display_name for entry in configs}
        specs = []
        for entry in configs:
            if entry.enabled:
                cookies = (path.parent / Path(entry.cookies_file).expanduser()).resolve()
                cache = (path.parent / Path(entry.cache_dir).expanduser()).resolve()
                specs.append((entry.id, cookies, cache))
    if not specs and not allow_empty:
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
                display_name=names.get(account_id),
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
    admin_key = os.environ.get("GEMINI_BRIDGE_ADMIN_KEY")
    admin_path = os.environ.get("GEMINI_BRIDGE_ACCOUNTS")
    if admin_key:
        if admin_key == key:
            raise ValueError("Administrator and service keys must differ")
        if not admin_path or os.environ.get("GEMINI_BRIDGE_COOKIES"):
            raise ValueError("Admin mode requires GEMINI_BRIDGE_ACCOUNTS only")
        if not Path(admin_path).expanduser().exists():
            from .admin import write_private_json

            write_private_json(Path(admin_path).expanduser(), {"accounts": []})
    if client is not None and accounts is not None:
        raise ValueError("Pass client or accounts, not both")
    pool = (
        accounts
        if accounts is not None
        else (
            [Account("default", client)]
            if client is not None
            else load_accounts(allow_empty=bool(admin_key))
        )
    )
    if (
        (not pool and not admin_key)
        or len({a.id for a in pool}) != len(pool)
        or len({id(a.client) for a in pool}) != len(pool)
    ):
        raise ValueError("Accounts must have distinct IDs and clients")
    chat_model = os.environ.get("GEMINI_BRIDGE_CHAT_MODEL", "gemini-pro")

    async def initialize(account):
        was_running = getattr(account.client, "_running", False)
        await account.client.init(
            timeout=timeout, auto_close=False, auto_refresh=True, verbose=False
        )
        if not was_running:
            account.last_checked_at = time.time()
        if not account.client.access_token or not account.client._check_account_status():
            account.login_status = "invalid"
            raise AuthError("Gemini account is not authenticated or available")
        if not was_running:
            account.login_status = "valid"

    async def close(account):
        if account.client is None:
            return
        try:
            await account.client.close()
        except Exception:
            # Keep cleanup failures from killing workers and stranding queued callers.
            account.status = "paused"
            logger.warning("Account {} cleanup failed; paused", account.id)

    def available():
        return any(a.enabled and a.status in ("idle", "busy") for a in pool)

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
        while account.enabled and account.status != "paused":
            request, future = await queue.get()
            initialized = False
            started = None
            outcome = "failed"
            failure = None
            try:
                if future.cancelled():
                    continue
                account.status = "busy"
                started = time.time()
                async with asyncio.timeout(timeout):
                    await initialize(account)
                    initialized = True
                    result = await generate_image(account.client, request, chat_model)
                outcome = "success"
                if not future.done():
                    future.set_result(result)
            except asyncio.CancelledError:
                failure = "service_stopping"
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
                if not initialized:
                    failure = "account_unavailable"
                elif isinstance(exc, (AuthError,)) or getattr(exc, "status_code", None) == 401:
                    failure = "authentication_failed"
                elif isinstance(exc, UsageLimitExceededError):
                    failure = "quota_exceeded"
                else:
                    failure = "timeout" if isinstance(exc, TimeoutError) else "upstream_failure"
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
                if started is not None:
                    account.recent.append(
                        {
                            "time": started,
                            "success": outcome == "success",
                            "duration": round(time.time() - started, 2),
                            "error": failure,
                        }
                    )
                if account.status != "paused":
                    account.status = "idle"
                queue.task_done()
                drain_unavailable(queue)
            if not account.enabled:
                await close(account)
                account.status = "disabled"
                if account.removing and account in pool:
                    pool.remove(account)

    async def start_account(account):
        if not account.enabled:
            account.status = "disabled"
            return
        try:
            async with asyncio.timeout(timeout):
                await initialize(account)
            account.status = "idle"
        except Exception:
            account.status = "paused"
            await close(account)
            logger.warning("Account {} initialization failed; paused", account.id)

    tasks = set()

    def spawn(account):
        account.task = asyncio.create_task(worker(account, app.state.queue))
        tasks.add(account.task)
        account.task.add_done_callback(tasks.discard)

    async def test_image(account, model, prompt=None):
        # Reserve before awaiting: the idle worker cannot take a shared-queue item.
        if not account.enabled or account.status != "idle" or account.checking or account.removing:
            raise HTTPException(409, "Account is busy, paused or disabled")
        prompt = DEFAULT_TEST_PROMPT if prompt is None else prompt.strip()
        old_worker = account.task
        if old_worker:
            old_worker.cancel()
        account.status = "busy"
        account.checking = True
        account.image_test = {
            "time": time.time(),
            "model": model,
            "prompt": prompt,
            "status": "running",
            "error": None,
        }

        async def run():
            started = time.monotonic()
            cancelled = False
            result = None
            try:
                if old_worker:
                    with suppress(asyncio.CancelledError):
                        await old_worker
                async with asyncio.timeout(timeout):
                    await initialize(account)
                    result = await generate_image(
                        account.client,
                        ImageRequest(
                            model=model,
                            prompt=prompt,
                            aspect_ratio="1:1",
                        ),
                        chat_model,
                    )
                account.image_test.update(status="success")
            except asyncio.CancelledError:
                cancelled = True
                account.image_test.update(status="failed", error="service_stopping")
                raise
            except Exception as exc:
                error = (
                    "authentication_failed"
                    if isinstance(exc, AuthError)
                    else "quota_exceeded"
                    if isinstance(exc, UsageLimitExceededError)
                    else "timeout"
                    if isinstance(exc, TimeoutError)
                    else "upstream_failure"
                )
                account.image_test.update(status="failed", error=error)
                if error == "authentication_failed":
                    account.login_status = "invalid"
                    account.last_checked_at = time.time()
                await close(account)
                account.status = "paused"
            finally:
                account.image_test["duration"] = round(time.monotonic() - started, 2)
                account.checking = False
                if not cancelled and account.status == "busy":
                    account.status = "idle"
                    spawn(account)
                drain_unavailable(app.state.queue)
            return {"id": account.id, "image_test": dict(account.image_test), "image": result}

        task = asyncio.create_task(run())
        tasks.add(task)
        task.add_done_callback(tasks.discard)
        # Disconnects must not release the account or cause a second submission.
        return await asyncio.shield(task)

    @asynccontextmanager
    async def lifespan(app):
        queue = asyncio.Queue(maxsize=queue_size)
        app.state.queue = queue
        app.state.accounts = pool
        try:
            await asyncio.gather(*(start_account(a) for a in pool))
            for account in pool:
                if account.enabled and account.status == "idle":
                    spawn(account)
            yield
        finally:
            pending = list(tasks)
            for task in pending:
                task.cancel()
            for task in pending:
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
            "Unsupported or invalid request parameters. See the service documentation.",
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

    if admin_key:
        from .admin import register_admin

        register_admin(
            app,
            pool,
            admin_path,
            admin_key,
            initialize,
            close,
            spawn,
            drain_unavailable,
            test_image,
        )
        static = Path(
            os.environ.get(
                "GEMINI_BRIDGE_WEB_DIR", Path(__file__).resolve().parents[2] / "web" / "dist"
            )
        )
        if static.is_dir():
            app.mount("/admin", StaticFiles(directory=static, html=True), name="admin-ui")
    return app


def error_response(status, code, message):
    return JSONResponse(
        status_code=status, content={"error": {"message": message, "type": code, "code": code}}
    )


if __name__ == "__main__":
    import uvicorn

    logger.remove()
    logger.add(sys.stderr, level="WARNING")
    uvicorn.run(
        create_app(),
        host=os.environ.get("GEMINI_BRIDGE_HOST", "127.0.0.1"),
        port=int(os.environ.get("GEMINI_BRIDGE_PORT", "18088")),
        workers=1,
    )
