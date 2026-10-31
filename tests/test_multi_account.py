# ruff: noqa: PT009, PT027
"""Account scheduling and cache isolation tests; never contact Google."""

import asyncio
import importlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
from curl_cffi.requests import Cookies

from gemini_webapi.exceptions import AuthError
from gemini_webapi.server import Account, create_app, load_accounts
from gemini_webapi.utils.rotate_1psidts import (
    clear_cookies_cache,
    get_cookies_cache_path,
    rotate_1psidts,
    save_cookies,
)


class TestMultiAccount(unittest.IsolatedAsyncioTestCase):
    def account(self, name):
        return Account(
            name,
            SimpleNamespace(
                init=AsyncMock(),
                close=AsyncMock(),
                access_token="test",
                _check_account_status=lambda: True,
            ),
        )

    def http(self, app):
        return httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://test",
            headers={"Authorization": "Bearer test"},
        )

    async def test_parallel_accounts_serial_workers_and_queue(self):
        pool = [self.account("a"), self.account("b")]
        active = {id(a.client): 0 for a in pool}
        maximum = 0
        calls = []
        both_started = asyncio.Event()
        first_started = asyncio.Event()
        release = asyncio.Event()

        async def generate(client, *args):
            nonlocal maximum
            identity = id(client)
            active[identity] += 1
            calls.append(identity)
            self.assertEqual(active[identity], 1)
            maximum = max(maximum, sum(active.values()))
            first_started.set()
            if sum(active.values()) == 2:
                both_started.set()
            await release.wait()
            active[identity] -= 1
            return {"created": 0, "data": []}

        app = create_app(accounts=pool, api_key="test", queue_size=1)
        with patch("gemini_webapi.server.generate_image", generate):
            async with app.router.lifespan_context(app), self.http(app) as http:

                async def post():
                    return await http.post("/v1/images/generations", json={"prompt": "cat"})

                tasks = [asyncio.create_task(post())]
                await asyncio.wait_for(first_started.wait(), 1)
                tasks.append(asyncio.create_task(post()))
                await asyncio.wait_for(both_started.wait(), 1)
                third = asyncio.create_task(post())
                async with asyncio.timeout(1):
                    while app.state.queue.qsize() != 1:
                        await asyncio.sleep(0)
                self.assertEqual((await post()).status_code, 429)
                release.set()
                self.assertTrue(
                    all(r.status_code == 200 for r in await asyncio.gather(*tasks, third))
                )
        self.assertEqual(maximum, 2)
        self.assertEqual(len(calls), 3)

    async def test_bad_startup_account_isolated(self):
        a, b = self.account("a"), self.account("b")
        a.client.init.side_effect = AuthError("private details")
        app = create_app(accounts=[a, b], api_key="test")
        with patch(
            "gemini_webapi.server.generate_image", new=AsyncMock(return_value={"data": []})
        ) as generate:
            async with app.router.lifespan_context(app), self.http(app) as http:
                status = await http.get("/v1/accounts")
                self.assertEqual(
                    status.json()["data"],
                    [{"id": "a", "status": "paused"}, {"id": "b", "status": "idle"}],
                )
                r = await http.post("/v1/images/generations", json={"prompt": "cat"})
                self.assertEqual(r.status_code, 200)
                self.assertIs(generate.call_args.args[0], b.client)
                self.assertEqual(
                    (
                        await http.get("/v1/accounts", headers={"Authorization": "wrong"})
                    ).status_code,
                    401,
                )

    async def test_failed_generation_not_reassigned(self):
        a, b = self.account("a"), self.account("b")
        calls = []

        async def generate(client, *args):
            calls.append(client)
            if client is a.client:
                raise AuthError("private token")
            return {"data": []}

        app = create_app(accounts=[a, b], api_key="test")
        with patch("gemini_webapi.server.generate_image", generate):
            async with app.router.lifespan_context(app), self.http(app) as http:
                r = await http.post("/v1/images/generations", json={"prompt": "cat"})
                self.assertEqual(r.status_code, 502)
                self.assertEqual(len(calls), 1)
                self.assertNotIn("private token", r.text)
                self.assertEqual(a.status, "paused")
                r = await http.post("/v1/images/generations", json={"prompt": "another cat"})
                self.assertEqual(r.status_code, 200)
                self.assertEqual(len(calls), 2)
                self.assertIs(calls[1], b.client)

    async def test_all_paused_rejects_and_drains_waiters(self):
        a = self.account("a")
        started = asyncio.Event()
        release = asyncio.Event()

        async def fail(*args):
            started.set()
            await release.wait()
            raise AuthError()

        app = create_app(accounts=[a], api_key="test")
        with patch("gemini_webapi.server.generate_image", fail):
            async with app.router.lifespan_context(app), self.http(app) as http:

                async def post():
                    return await http.post("/v1/images/generations", json={"prompt": "cat"})

                first = asyncio.create_task(post())
                await asyncio.wait_for(started.wait(), 1)
                second = asyncio.create_task(post())
                async with asyncio.timeout(1):
                    while not app.state.queue.qsize():
                        await asyncio.sleep(0)
                release.set()
                self.assertEqual((await first).status_code, 502)
                self.assertEqual((await second).status_code, 503)
                self.assertEqual((await post()).status_code, 503)

    async def test_cache_rotation_and_clear_stay_in_account_directory(self):
        with tempfile.TemporaryDirectory() as t:
            directories = [Path(t) / "a", Path(t) / "b"]

            async def refresh(directory, suffix):
                jar = Cookies()
                jar.set("__Secure-1PSID", "same-initial", domain=".google.com", secure=True)
                jar.set("__Secure-1PSIDTS", suffix, domain=".google.com", secure=True)
                response = SimpleNamespace(status_code=200, raise_for_status=lambda: None)
                client = SimpleNamespace(cookies=jar, post=AsyncMock(return_value=response))
                with patch.dict(os.environ, {"GEMINI_COOKIE_PATH": str(Path(t) / "wrong")}):
                    await rotate_1psidts(client, base_psid="same-initial", cache_dir=directory)
                    jar.set(
                        "__Secure-1PSID", "rotated-" + suffix, domain=".google.com", secure=True
                    )
                    save_cookies(jar, base_psid="same-initial", cache_dir=directory)
                return jar

            jars = await asyncio.gather(refresh(directories[0], "a"), refresh(directories[1], "b"))
            paths = [
                get_cookies_cache_path(j, base_psid="same-initial", cache_dir=d)
                for j, d in zip(jars, directories, strict=True)
            ]
            self.assertNotEqual(paths[0].read_bytes(), paths[1].read_bytes())
            self.assertEqual(paths[0].stat().st_mode & 0o777, 0o600)
            clear_cookies_cache(jars[0], base_psid="same-initial", cache_dir=directories[0])
            self.assertFalse(paths[0].exists())
            self.assertTrue(paths[1].exists())
            self.assertFalse((Path(t) / "wrong").exists())

    async def test_initialization_never_reads_browser_when_disabled(self):
        module = importlib.import_module("gemini_webapi.utils.get_access_token")
        with (
            tempfile.TemporaryDirectory() as t,
            patch.object(module, "load_browser_cookies") as browser,
            patch.object(
                module,
                "_send_request",
                new=AsyncMock(side_effect=ValueError("stop before network")),
            ),
        ):
            with self.assertRaises(ValueError):
                await module.get_access_token(
                    {"__Secure-1PSID": "test"}, cache_dir=t, use_browser_cookies=False
                )
            browser.assert_not_called()

    def test_configuration_rejects_shared_credentials_and_cache(self):
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            for label in ["a", "b"]:
                (root / (label + ".json")).write_text(json.dumps({"__Secure-1PSID": label}))
            entries = [
                {"id": label, "cookies_file": label + ".json", "cache_dir": "cache-" + label}
                for label in ["a", "b"]
            ]
            config = root / "accounts.json"

            def load():
                config.write_text(json.dumps({"accounts": entries}))
                with patch.dict(
                    os.environ, {"GEMINI_BRIDGE_ACCOUNTS": str(config), "GEMINI_BRIDGE_COOKIES": ""}
                ):
                    return load_accounts()

            pool = load()
            self.assertEqual(len(pool), 2)
            self.assertNotEqual(pool[0].client.cookie_cache_dir, pool[1].client.cookie_cache_dir)
            self.assertFalse(pool[0].client.use_browser_cookies)
            self.assertEqual((root / "cache-a").stat().st_mode & 0o777, 0o700)
            entries[1]["cache_dir"] = "cache-a"
            with self.assertRaises(ValueError):
                load()
            entries[1]["cache_dir"] = "cache-b"
            entries[1]["cookies_file"] = "a.json"
            with self.assertRaises(ValueError):
                load()
            entries[1]["enabled"] = False
            self.assertEqual(len(load()), 1)

    async def test_restart_loads_only_explicit_account_cache(self):
        module = importlib.import_module("gemini_webapi.utils.get_access_token")
        with tempfile.TemporaryDirectory() as t:

            async def check(label):
                directory = Path(t) / label
                jar = Cookies()
                jar.set("__Secure-1PSID", "base", domain=".google.com", secure=True)
                jar.set("__Secure-1PSIDTS", label, domain=".google.com", secure=True)
                save_cookies(jar, cache_dir=directory)
                result = await module.get_access_token(
                    {"__Secure-1PSID": "base"}, cache_dir=directory, use_browser_cookies=False
                )
                await result.client.close()
                return result.access_token, result.cookie_source

            async def send(client, jar, **kwargs):
                await asyncio.sleep(0)
                return jar.get("__Secure-1PSIDTS", domain=".google.com")

            with (
                patch.object(module, "_send_request", side_effect=send),
                patch.object(
                    module,
                    "_extract_payload",
                    side_effect=lambda value: (value, None, None, "en", None),
                ),
            ):
                results = await asyncio.gather(check("a"), check("b"))
            self.assertEqual(results, [("a", "Cache"), ("b", "Cache")])
