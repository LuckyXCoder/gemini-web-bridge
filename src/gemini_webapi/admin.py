"""Local administration API; credentials are write-only and separate from service keys."""

import asyncio
import hmac
import os
import shutil
import time
import uuid
from contextlib import suppress
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Literal

import orjson
from fastapi import Depends, Header, HTTPException
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

from .exceptions import AuthError
from .server import DisplayName


def write_private_json(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(orjson.dumps(value, option=orjson.OPT_INDENT_2))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        with suppress(FileNotFoundError):
            temporary.unlink()


class Credentials(BaseModel):
    model_config = ConfigDict(extra="forbid")
    secure_1psid: SecretStr = Field(min_length=1, max_length=8192)
    secure_1psidts: SecretStr = Field(min_length=1, max_length=8192)


class NewAccount(Credentials):
    id: str = Field(
        default_factory=lambda: "account-" + uuid.uuid4().hex[:16], pattern=r"^[A-Za-z0-9_-]{1,64}$"
    )
    display_name: DisplayName | None = None


class AccountName(BaseModel):
    model_config = ConfigDict(extra="forbid")
    display_name: DisplayName


class UpdateCredentials(Credentials):
    display_name: DisplayName | None = None


class Enabled(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool = Field(strict=True)


class ImageTestRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    model: Literal["gemini-web-image", "gemini-web-pro-image"] = "gemini-web-image"
    prompt: str | None = Field(default=None, min_length=1, max_length=16000)

    @field_validator("prompt")
    @classmethod
    def nonempty_prompt(cls, value):
        if value is not None and not value.strip():
            raise ValueError("Prompt must not be blank")
        return value.strip() if value is not None else None


def register_admin(app, pool, config_path, admin_key, initialize, close, spawn, drain, test_image):
    from .server import Account, AccountConfig, SingleSubmissionClient

    lock = asyncio.Lock()
    config_path = Path(config_path).expanduser().resolve()
    entries = [
        AccountConfig.model_validate(x).model_dump()
        for x in orjson.loads(config_path.read_bytes())["accounts"]
    ]
    initial = {a.id for a in pool}
    for entry in entries:
        if entry["id"] not in initial:
            pool.append(
                Account(
                    entry["id"],
                    None,
                    status="disabled",
                    enabled=False,
                    display_name=entry.get("display_name"),
                )
            )

    def snapshot():
        return {
            "data": [
                {
                    "id": a.id,
                    "display_name": a.display_name or a.id,
                    "enabled": a.enabled,
                    "status": a.status,
                    "last_checked_at": a.last_checked_at,
                    "login_status": a.login_status,
                    "checking": a.checking,
                    "removing": a.removing,
                    "recent": list(a.recent),
                    "image_test": a.image_test,
                }
                for a in pool
            ],
            "queue_depth": app.state.queue.qsize(),
        }

    async def authorize(authorization: str | None = Header(default=None)):
        if not hmac.compare_digest((authorization or "").encode(), f"Bearer {admin_key}".encode()):
            raise HTTPException(401, "Invalid administrator key")

    def persist(updated):
        try:
            write_private_json(config_path, {"accounts": updated})
        except OSError:
            raise HTTPException(
                500, "Configuration could not be saved; no change applied"
            ) from None
        entries[:] = updated

    def find(account_id):
        account = next((a for a in pool if a.id == account_id), None)
        if account is None:
            raise HTTPException(404, "Account not found")
        return account

    async def stop_idle(account):
        if account.task and not account.task.done():
            account.task.cancel()
            with suppress(asyncio.CancelledError):
                await account.task
        await close(account)

    def verify_duplicate(psid, exclude=None):
        for account in pool:
            if account.id != exclude and getattr(account.client, "_base_psid", None) == psid:
                raise HTTPException(409, "These credentials are already configured")
        for entry in entries:
            if entry["id"] == exclude:
                continue
            # Check disabled accounts too, but never return their credentials.
            try:
                data = orjson.loads((config_path.parent / entry["cookies_file"]).read_bytes())
                data = data.get("cookies", data)
                if data.get("__Secure-1PSID") == psid:
                    raise HTTPException(409, "These credentials are already configured")
            except (OSError, ValueError, AttributeError):
                continue

    async def candidate(account_id, credentials):
        psid = credentials.secure_1psid.get_secret_value().strip()
        psidts = credentials.secure_1psidts.get_secret_value().strip()
        if not psid or not psidts:
            raise HTTPException(400, "Both Cookie values are required")
        verify_duplicate(psid, exclude=account_id)
        private_dir = config_path.parent / "managed" / account_id / uuid.uuid4().hex
        cache = private_dir / "cache"
        cache.mkdir(parents=True, mode=0o700)
        client = SingleSubmissionClient(
            psid,
            psidts,
            proxy=os.environ.get("GEMINI_PROXY") or None,
            cookie_cache_dir=cache,
            use_browser_cookies=False,
        )
        account = Account(account_id, client)
        try:
            async with asyncio.timeout(60):
                await initialize(account)
            account.status = "idle"
            account.last_checked_at = time.time()
            cookie_file = private_dir / "cookies.json"
            write_private_json(
                cookie_file, {"cookies": {"__Secure-1PSID": psid, "__Secure-1PSIDTS": psidts}}
            )
        except asyncio.CancelledError:
            await close(account)
            raise
        except Exception:
            await close(account)
            raise HTTPException(
                400, "Login validation failed; existing account unchanged"
            ) from None
        entry = {
            "id": account_id,
            "cookies_file": str(cookie_file),
            "cache_dir": str(cache),
            "enabled": True,
        }
        return account, entry

    @app.get("/admin/api/accounts", dependencies=[Depends(authorize)])
    async def accounts():
        return snapshot()

    @app.post("/admin/api/accounts/{account_id}/image-test", dependencies=[Depends(authorize)])
    async def check_image(account_id: str, request: ImageTestRequest):
        return await test_image(find(account_id), request.model, request.prompt)

    @app.post("/admin/api/accounts/{account_id}/check", dependencies=[Depends(authorize)])
    async def check_login(account_id: str):
        # Separate client and disposable cache: no generation, no modification of
        # the worker's session, enabled state, credentials or persistent cache.
        account = find(account_id)
        if account.status == "busy" or account.checking or account.removing:
            raise HTTPException(409, "Account is busy or already being checked")
        account.checking = True
        probe = None
        try:
            entry = next(x for x in entries if x["id"] == account_id)
            with TemporaryDirectory(prefix="gemini-login-check-") as directory:
                try:
                    path = (config_path.parent / Path(entry["cookies_file"]).expanduser()).resolve()
                    raw = orjson.loads(path.read_bytes())
                    cookies = raw.get("cookies", raw)
                    psid = cookies.get("__Secure-1PSID")
                    if not isinstance(psid, str) or not psid.strip():
                        raise ValueError()
                    source = (config_path.parent / Path(entry["cache_dir"]).expanduser()).resolve()
                    for file in source.glob(".cached_cookies*"):
                        if file.is_file():
                            target = Path(directory) / file.name
                            shutil.copyfile(file, target)
                            target.chmod(0o600)
                    probe = SingleSubmissionClient(
                        psid,
                        cookies.get("__Secure-1PSIDTS", ""),
                        proxy=os.environ.get("GEMINI_PROXY") or None,
                        cookie_cache_dir=directory,
                        use_browser_cookies=False,
                    )
                    async with asyncio.timeout(45):
                        await probe.init(
                            timeout=30, auto_close=False, auto_refresh=False, verbose=False
                        )
                    account.login_status = (
                        "valid"
                        if probe.access_token and probe._check_account_status()
                        else "invalid"
                    )
                except asyncio.CancelledError:
                    raise
                except AuthError:
                    account.login_status = "invalid"
                except Exception:
                    account.login_status = "unavailable"
                finally:
                    if probe:
                        with suppress(Exception):
                            await probe.close()
                account.last_checked_at = time.time()
        finally:
            account.checking = False
        return {
            "id": account.id,
            "login_status": account.login_status,
            "last_checked_at": account.last_checked_at,
        }

    @app.post("/admin/api/accounts", dependencies=[Depends(authorize)])
    async def add(request: NewAccount):
        async with lock:
            if any(a.id == request.id for a in pool):
                raise HTTPException(409, "Account ID already exists")
            account, entry = await candidate(request.id, request)
            account.display_name = request.display_name
            entry["display_name"] = request.display_name
            try:
                persist([*entries, entry])
            except Exception:
                await close(account)
                raise
            pool.append(account)
            spawn(account)
            return {"id": account.id, "status": account.status}

    @app.patch("/admin/api/accounts/{account_id}", dependencies=[Depends(authorize)])
    async def rename(account_id: str, request: AccountName):
        async with lock:
            account = find(account_id)
            if account.removing:
                raise HTTPException(409, "Account is pending removal")
            persist(
                [
                    {**entry, "display_name": request.display_name}
                    if entry["id"] == account_id
                    else entry
                    for entry in entries
                ]
            )
            account.display_name = request.display_name
            return {"id": account.id, "display_name": account.display_name}

    @app.put("/admin/api/accounts/{account_id}/cookies", dependencies=[Depends(authorize)])
    async def update(account_id: str, request: UpdateCredentials):
        async with lock:
            old = find(account_id)
            if old.status == "busy" or old.checking or old.removing:
                raise HTTPException(409, "Account is busy; update cookies after its task finishes")
            candidate_account, entry = await candidate(account_id, request)
            # Generation can start while the replacement is being validated.
            if old.status == "busy" or old.checking or old.removing:
                await close(candidate_account)
                raise HTTPException(
                    409, "Account became busy; update cookies after its task finishes"
                )
            entry["enabled"] = old.enabled
            candidate_account.display_name = (
                request.display_name if request.display_name is not None else old.display_name
            )
            entry["display_name"] = candidate_account.display_name
            try:
                persist([entry if x["id"] == account_id else x for x in entries])
            except Exception:
                await close(candidate_account)
                raise
            old.enabled = False
            await stop_idle(old)
            candidate_account.enabled = entry["enabled"]
            candidate_account.recent = old.recent
            pool[pool.index(old)] = candidate_account
            if candidate_account.enabled:
                spawn(candidate_account)
            else:
                await close(candidate_account)
                candidate_account.status = "disabled"
            return {"id": account_id, "status": candidate_account.status}

    @app.delete("/admin/api/accounts/{account_id}", dependencies=[Depends(authorize)])
    async def remove(account_id: str):
        async with lock:
            account = find(account_id)
            if account.checking:
                raise HTTPException(
                    409, "Account is busy checking credentials; try again afterward"
                )
            if account.removing:
                return {"id": account_id, "pending": True}
            # Persist first. A failed save must not stop a working account.
            persist([entry for entry in entries if entry["id"] != account_id])
            account.enabled = False
            account.removing = True
            pending = account.status == "busy"
            if not pending:
                await stop_idle(account)
                pool.remove(account)
            # The active worker removes busy accounts after the current task finishes.
            drain(app.state.queue)
            return {"id": account_id, "pending": pending}

    @app.put("/admin/api/accounts/{account_id}/enabled", dependencies=[Depends(authorize)])
    async def toggle(account_id: str, request: Enabled):
        async with lock:
            account = find(account_id)
            if account.checking or account.removing:
                raise HTTPException(409, "Account is busy checking login; try again afterward")
            if not request.enabled:
                persist([{**x, "enabled": False} if x["id"] == account_id else x for x in entries])
                account.enabled = False
                if account.status != "busy":
                    await stop_idle(account)
                    account.status = "disabled"
                drain(app.state.queue)
                return {"id": account_id, "enabled": False, "status": account.status}
            if (account.status == "busy" and not account.enabled) or (
                account.status == "paused" and account.task and not account.task.done()
            ):
                raise HTTPException(409, "Account is busy finishing a task; try again afterward")
            if account.enabled and account.status in ("idle", "busy"):
                return {"id": account_id, "enabled": True, "status": account.status}
            entry = next(x for x in entries if x["id"] == account_id)
            target_cache = (config_path.parent / entry["cache_dir"]).resolve()
            if any(
                x["id"] != account_id
                and (config_path.parent / x["cache_dir"]).resolve() == target_cache
                for x in entries
            ):
                raise HTTPException(409, "Accounts must not share cache directories")
            if account.client is None:
                try:
                    data = orjson.loads((config_path.parent / entry["cookies_file"]).read_bytes())
                    data = data.get("cookies", data)
                    verify_duplicate(data["__Secure-1PSID"], exclude=account_id)
                    account.client = SingleSubmissionClient(
                        data["__Secure-1PSID"],
                        data.get("__Secure-1PSIDTS", ""),
                        proxy=os.environ.get("GEMINI_PROXY") or None,
                        cookie_cache_dir=config_path.parent / entry["cache_dir"],
                        use_browser_cookies=False,
                    )
                except HTTPException:
                    raise
                except Exception:
                    raise HTTPException(400, "Account credentials could not be loaded") from None
            try:
                async with asyncio.timeout(60):
                    await initialize(account)
                persist([{**x, "enabled": True} if x["id"] == account_id else x for x in entries])
            except HTTPException:
                await close(account)
                raise
            except Exception:
                await close(account)
                raise HTTPException(
                    400, "Login validation failed; account remains paused or disabled"
                ) from None
            account.enabled = True
            account.status = "idle"
            spawn(account)
            return {"id": account_id, "enabled": True, "status": "idle"}
