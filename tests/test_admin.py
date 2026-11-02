# ruff: noqa: PT009
"""Offline management API tests; no actual Cookie values or Google calls."""

import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

from gemini_webapi.server import create_app


class FakeClient:
    def __init__(self, psid, psidts="", **kwargs):
        self.psid = psid
        self.access_token = "fake"
        self._running = False
        self.closed = 0
        self.options = kwargs

    async def init(self, **kwargs):
        if self.psid == "invalid":
            raise ValueError("private credential details")
        self._running = True

    def _check_account_status(self):
        return True

    async def close(self):
        self.closed += 1
        self._running = False


class TestAdmin(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / "accounts.json"
        self.env = patch.dict(
            os.environ,
            {
                "GEMINI_BRIDGE_ADMIN_KEY": "admin-test",
                "GEMINI_BRIDGE_ACCOUNTS": str(self.path),
                "GEMINI_BRIDGE_COOKIES": "",
            },
        )
        self.env.start()
        self.factory = patch("gemini_webapi.server.SingleSubmissionClient", FakeClient)
        self.factory.start()
        self.app = create_app(api_key="service-test")
        self.life = self.app.router.lifespan_context(self.app)
        await self.life.__aenter__()
        self.http = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app),
            base_url="http://test",
            headers={"Authorization": "Bearer admin-test"},
        )

    async def asyncTearDown(self):
        await self.http.aclose()
        await self.life.__aexit__(None, None, None)
        self.factory.stop()
        self.env.stop()
        self.directory.cleanup()

    async def add(self, name="a", psid="cookie-a"):
        return await self.http.post(
            "/admin/api/accounts",
            json={"id": name, "secure_1psid": psid, "secure_1psidts": "test-ts"},
        )

    async def test_separate_auth_and_no_secret_echo(self):
        r = await self.http.get(
            "/admin/api/accounts", headers={"Authorization": "Bearer service-test"}
        )
        self.assertEqual(r.status_code, 401)
        self.assertEqual((await self.add()).status_code, 200)
        r = await self.http.get("/admin/api/accounts")
        self.assertNotIn("cookie-a", r.text)
        self.assertNotIn("cookies_file", r.text)
        self.assertEqual(r.json()["data"][0]["enabled"], True)
        self.assertEqual((await self.http.get("/v1/models")).status_code, 401)
        row = json.loads(self.path.read_text())["accounts"][0]
        self.assertEqual(Path(row["cookies_file"]).stat().st_mode & 0o777, 0o600)
        self.assertEqual((await self.add("b")).status_code, 409)

    async def test_invalid_add_and_update_preserve_config(self):
        self.assertEqual((await self.add("bad", "invalid")).status_code, 400)
        self.assertEqual(json.loads(self.path.read_text())["accounts"], [])
        await self.add()
        before = self.path.read_bytes()
        old = self.app.state.accounts[0].client
        r = await self.http.put(
            "/admin/api/accounts/a/cookies",
            json={"secure_1psid": "invalid", "secure_1psidts": "bad"},
        )
        self.assertEqual(r.status_code, 400)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertIs(self.app.state.accounts[0].client, old)

    async def test_update_replaces_client_and_cache(self):
        await self.add()
        old = self.app.state.accounts[0].client
        r = await self.http.put(
            "/admin/api/accounts/a/cookies",
            json={"secure_1psid": "new-cookie", "secure_1psidts": "new-ts"},
        )
        self.assertEqual(r.status_code, 200)
        self.assertGreater(old.closed, 0)
        new = self.app.state.accounts[0].client
        self.assertNotEqual(old.options["cookie_cache_dir"], new.options["cookie_cache_dir"])
        self.assertEqual(new.psid, "new-cookie")

    async def test_disable_busy_finishes_current_and_prevents_next(self):
        await self.add()
        started = asyncio.Event()
        finish = asyncio.Event()
        calls = []

        async def generate(*args):
            calls.append(1)
            started.set()
            await finish.wait()
            return {"data": []}

        with patch("gemini_webapi.server.generate_image", generate):
            task = asyncio.create_task(
                self.http.post(
                    "/v1/images/generations",
                    headers={"Authorization": "Bearer service-test"},
                    json={"prompt": "test"},
                )
            )
            await asyncio.wait_for(started.wait(), 1)
            r = await self.http.put(
                "/admin/api/accounts/a/cookies",
                json={"secure_1psid": "new", "secure_1psidts": "new"},
            )
            self.assertEqual(r.status_code, 409)
            r = await self.http.put("/admin/api/accounts/a/enabled", json={"enabled": False})
            self.assertEqual(r.status_code, 200)
            self.assertFalse(task.done())
            r = await self.http.post(
                "/v1/images/generations",
                headers={"Authorization": "Bearer service-test"},
                json={"prompt": "new"},
            )
            self.assertEqual(r.status_code, 503)
            finish.set()
            self.assertEqual((await task).status_code, 200)
            self.assertEqual(len(calls), 1)
            self.assertEqual(self.app.state.accounts[0].status, "disabled")
            self.assertEqual(len(self.app.state.accounts[0].recent), 1)
        self.assertEqual(
            (
                await self.http.put("/admin/api/accounts/a/enabled", json={"enabled": True})
            ).status_code,
            200,
        )

    async def test_history_bounded_and_failure_redacted(self):
        await self.add()

        async def generate(client, request, model):
            if request.prompt == "fail":
                raise RuntimeError("SECRET")
            return {"data": []}

        with patch("gemini_webapi.server.generate_image", generate):
            for i in range(12):
                await self.http.post(
                    "/v1/images/generations",
                    headers={"Authorization": "Bearer service-test"},
                    json={"prompt": "fail" if i == 11 else "ok"},
                )
        r = await self.http.get("/admin/api/accounts")
        recent = r.json()["data"][0]["recent"]
        self.assertEqual(len(recent), 10)
        self.assertFalse(recent[-1]["success"])
        self.assertNotIn("SECRET", r.text)

    async def test_persist_failure_does_not_add_live_account(self):
        from gemini_webapi.admin import write_private_json

        def write(path, value):
            if path.resolve() == self.path.resolve():
                raise OSError("disk full")
            return write_private_json(path, value)

        with patch("gemini_webapi.admin.write_private_json", write):
            self.assertEqual((await self.add()).status_code, 500)
        self.assertEqual(self.app.state.accounts, [])
        self.assertEqual(json.loads(self.path.read_text())["accounts"], [])

    async def test_disabled_account_persists_on_restart(self):
        await self.add()
        await self.http.put("/admin/api/accounts/a/enabled", json={"enabled": False})
        other = create_app(api_key="service-test")
        async with other.router.lifespan_context(other):
            self.assertEqual(other.state.accounts[0].status, "disabled")
            self.assertIsNone(other.state.accounts[0].client)

    async def test_manual_login_check_isolated_and_no_generation(self):
        await self.add()
        account = self.app.state.accounts[0]
        old_client = account.client
        entry = json.loads(self.path.read_text())["accounts"][0]
        source = Path(entry["cache_dir"]) / ".cached_cookies_test.json"
        source.write_text('{"test":"unchanged"}')
        before = source.read_bytes()
        probes = []
        original_init = FakeClient.__init__

        def factory(self, *args, **kwargs):
            original_init(self, *args, **kwargs)
            probes.append(self)

        with patch.object(FakeClient, "__init__", factory):
            result = await self.http.post("/admin/api/accounts/a/check")
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json()["login_status"], "valid")
        self.assertIs(account.client, old_client)
        self.assertTrue(account.enabled)
        self.assertEqual(account.status, "idle")
        self.assertEqual(list(account.recent), [])
        self.assertEqual(source.read_bytes(), before)
        self.assertFalse(Path(probes[0].options["cookie_cache_dir"]).exists())
        self.assertFalse(probes[0].options["use_browser_cookies"])
        self.assertEqual(old_client.closed, 0)
        self.assertGreater(probes[0].closed, 0)
        denied = await self.http.post(
            "/admin/api/accounts/a/check", headers={"Authorization": "Bearer service-test"}
        )
        self.assertEqual(denied.status_code, 401)

    async def test_failed_check_preserves_worker_and_disabled_state(self):
        await self.add()
        await self.http.put("/admin/api/accounts/a/enabled", json={"enabled": False})

        async def unauthenticated(self, **kwargs):
            self.access_token = None

        with patch.object(FakeClient, "init", unauthenticated):
            result = await self.http.post("/admin/api/accounts/a/check")
        self.assertEqual(result.json()["login_status"], "invalid")
        account = self.app.state.accounts[0]
        self.assertFalse(account.enabled)
        self.assertEqual(account.status, "disabled")
        self.assertFalse(account.checking)

    async def test_check_blocks_duplicate_check_and_account_update(self):
        await self.add()
        entered = asyncio.Event()
        release = asyncio.Event()

        async def slow(self, **kwargs):
            entered.set()
            await release.wait()

        with patch.object(FakeClient, "init", slow):
            task = asyncio.create_task(self.http.post("/admin/api/accounts/a/check"))
            await asyncio.wait_for(entered.wait(), 1)
            self.assertEqual((await self.http.post("/admin/api/accounts/a/check")).status_code, 409)
            self.assertEqual(
                (
                    await self.http.put("/admin/api/accounts/a/enabled", json={"enabled": False})
                ).status_code,
                409,
            )
            self.assertEqual(
                (
                    await self.http.put(
                        "/admin/api/accounts/a/cookies",
                        json={"secure_1psid": "x", "secure_1psidts": "y"},
                    )
                ).status_code,
                409,
            )
            release.set()
            self.assertEqual((await task).status_code, 200)

    async def test_delete_idle_preserves_credentials_and_requires_admin(self):
        await self.add()
        row = json.loads(self.path.read_text())["accounts"][0]
        cookie_file = Path(row["cookies_file"])
        cache = Path(row["cache_dir"])
        before = cookie_file.read_bytes()
        denied = await self.http.delete(
            "/admin/api/accounts/a", headers={"Authorization": "Bearer service-test"}
        )
        self.assertEqual(denied.status_code, 401)
        result = await self.http.delete("/admin/api/accounts/a")
        self.assertEqual(result.status_code, 200)
        self.assertFalse(result.json()["pending"])
        self.assertEqual(self.app.state.accounts, [])
        self.assertEqual(json.loads(self.path.read_text())["accounts"], [])
        self.assertEqual(cookie_file.read_bytes(), before)
        self.assertTrue(cache.exists())
        self.assertEqual((await self.http.delete("/admin/api/accounts/a")).status_code, 404)

    async def test_delete_busy_drains_without_interrupting(self):
        await self.add()
        started = asyncio.Event()
        finish = asyncio.Event()

        async def generate(*args):
            started.set()
            await finish.wait()
            return {"data": []}

        with patch("gemini_webapi.server.generate_image", generate):
            task = asyncio.create_task(
                self.http.post(
                    "/v1/images/generations",
                    headers={"Authorization": "Bearer service-test"},
                    json={"prompt": "cat"},
                )
            )
            await asyncio.wait_for(started.wait(), 1)
            response = await self.http.delete("/admin/api/accounts/a")
            self.assertTrue(response.json()["pending"])
            self.assertFalse(task.done())
            self.assertFalse(self.app.state.accounts[0].enabled)
            self.assertEqual((await self.add()).status_code, 409)
            self.assertEqual((await self.http.post("/admin/api/accounts/a/check")).status_code, 409)
            finish.set()
            self.assertEqual((await task).status_code, 200)
            self.assertEqual(self.app.state.accounts, [])

    async def test_delete_save_failure_preserves_account(self):
        await self.add()
        with patch("gemini_webapi.admin.write_private_json", side_effect=OSError("disk full")):
            self.assertEqual((await self.http.delete("/admin/api/accounts/a")).status_code, 500)
        self.assertTrue(self.app.state.accounts[0].enabled)
        self.assertFalse(self.app.state.accounts[0].removing)
        self.assertEqual(len(json.loads(self.path.read_text())["accounts"]), 1)

    async def test_display_name_accepts_email_and_chinese_without_path_changes(self):
        response = await self.http.post(
            "/admin/api/accounts",
            json={
                "display_name": "工作账号 user@example.com",
                "secure_1psid": "new-cookie",
                "secure_1psidts": "test-ts",
            },
        )
        self.assertEqual(response.status_code, 200)
        account_id = response.json()["id"]
        self.assertRegex(account_id, r"^account-[a-f0-9]{16}$")
        entry = json.loads(self.path.read_text())["accounts"][0]
        self.assertNotIn("工作账号", entry["cookies_file"])
        self.assertNotIn("@", entry["cookies_file"])
        account = self.app.state.accounts[0]
        old_client = account.client
        response = await self.http.patch(
            "/admin/api/accounts/" + account_id,
            json={"display_name": "新名称 中文邮箱@example.com"},
        )
        self.assertEqual(response.status_code, 200)
        after = json.loads(self.path.read_text())["accounts"][0]
        self.assertEqual(after["cookies_file"], entry["cookies_file"])
        self.assertEqual(after["cache_dir"], entry["cache_dir"])
        self.assertIs(account.client, old_client)
        self.assertEqual(account.display_name, "新名称 中文邮箱@example.com")
        self.assertEqual(
            (await self.http.get("/admin/api/accounts")).json()["data"][0]["display_name"],
            account.display_name,
        )

    async def test_invalid_display_name_rejected(self):
        await self.add()
        for name in ["", "   ", "bad\nname", "x" * 101]:
            response = await self.http.patch("/admin/api/accounts/a", json={"display_name": name})
            self.assertEqual(response.status_code, 400)
        self.assertEqual(
            (
                await self.http.patch(
                    "/admin/api/accounts/a",
                    headers={"Authorization": "Bearer service-test"},
                    json={"display_name": "中文"},
                )
            ).status_code,
            401,
        )

    async def test_name_and_credentials_update_atomically_and_resume_paused(self):
        await self.add()
        account = self.app.state.accounts[0]
        account.status = "paused"
        task = account.task
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):  # noqa: PT027
            await task
        before = self.path.read_bytes()
        response = await self.http.put(
            "/admin/api/accounts/a/cookies",
            json={
                "display_name": "new@example.com",
                "secure_1psid": "invalid",
                "secure_1psidts": "bad",
            },
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.path.read_bytes(), before)
        response = await self.http.put(
            "/admin/api/accounts/a/cookies",
            json={
                "display_name": "new@example.com",
                "secure_1psid": "valid-replacement",
                "secure_1psidts": "new-ts",
            },
        )
        self.assertEqual(response.status_code, 200)
        updated = self.app.state.accounts[0]
        self.assertTrue(updated.enabled)
        self.assertEqual(updated.status, "idle")
        self.assertEqual(updated.display_name, "new@example.com")
        self.assertEqual(
            json.loads(self.path.read_text())["accounts"][0]["display_name"], "new@example.com"
        )

    async def test_image_test_targets_account_and_separates_login_status(self):
        await self.add("a", "cookie-a")
        await self.add("b", "cookie-b")
        used = []

        async def generate(client, request, model):
            used.append((client.psid, request.model, request.n))
            return {"data": [{"b64_json": "synthetic-image"}]}

        with patch("gemini_webapi.server.generate_image", generate):
            denied = await self.http.post(
                "/admin/api/accounts/b/image-test",
                json={},
                headers={"Authorization": "Bearer service-test"},
            )
            self.assertEqual(denied.status_code, 401)
            result = await self.http.post(
                "/admin/api/accounts/b/image-test", json={"model": "gemini-web-pro-image"}
            )
        self.assertEqual(result.status_code, 200)
        self.assertEqual(used, [("cookie-b", "gemini-web-pro-image", 1)])
        self.assertEqual(result.json()["image_test"]["status"], "success")
        state = (await self.http.get("/admin/api/accounts")).json()["data"]
        self.assertIsNone(state[0]["image_test"])
        self.assertEqual(state[1]["login_status"], "valid")
        self.assertNotIn("synthetic-image", str(state))

    async def test_image_failure_does_not_invalidate_login_or_retry(self):
        await self.add()
        called = []

        async def generate(*args):
            called.append(1)
            raise RuntimeError("private upstream content")

        with patch("gemini_webapi.server.generate_image", generate):
            response = await self.http.post("/admin/api/accounts/a/image-test", json={})
        self.assertEqual(len(called), 1)
        self.assertEqual(response.json()["image_test"]["error"], "upstream_failure")
        self.assertNotIn("private upstream", response.text)
        state = (await self.http.get("/admin/api/accounts")).json()["data"][0]
        self.assertEqual(state["login_status"], "valid")
        self.assertEqual(state["status"], "paused")

    async def test_image_test_reserves_account_after_http_cancellation(self):
        await self.add()
        entered, release = asyncio.Event(), asyncio.Event()

        async def generate(*args):
            entered.set()
            await release.wait()
            return {"data": [{"b64_json": "synthetic"}]}

        with patch("gemini_webapi.server.generate_image", generate):
            request = asyncio.create_task(
                self.http.post("/admin/api/accounts/a/image-test", json={})
            )
            await entered.wait()
            request.cancel()
            with self.assertRaises(asyncio.CancelledError):  # noqa: PT027
                await request
            for suffix in ["/image-test", "/check"]:
                result = await self.http.post("/admin/api/accounts/a" + suffix, json={})
                self.assertEqual(result.status_code, 409)
            self.assertEqual((await self.http.delete("/admin/api/accounts/a")).status_code, 409)
            self.assertEqual(
                (
                    await self.http.put(
                        "/admin/api/accounts/a/cookies",
                        json={"secure_1psid": "new", "secure_1psidts": "new"},
                    )
                ).status_code,
                409,
            )
            self.assertEqual(self.app.state.accounts[0].status, "busy")
            release.set()
            for _ in range(20):
                await asyncio.sleep(0)
                if self.app.state.accounts[0].status == "idle":
                    break
            self.assertEqual(self.app.state.accounts[0].image_test["status"], "success")
            self.assertEqual(self.app.state.accounts[0].status, "idle")

    async def test_image_prompt_defaults_custom_and_invalid(self):
        from gemini_webapi.server import DEFAULT_TEST_PROMPT

        await self.add()
        prompts = []

        async def generate(client, request, model):
            prompts.append(request.prompt)
            return {"data": [{"b64_json": "synthetic"}]}

        with patch("gemini_webapi.server.generate_image", generate):
            for bad in ["", "  ", "x" * 16001]:
                response = await self.http.post(
                    "/admin/api/accounts/a/image-test", json={"prompt": bad}
                )
                self.assertEqual(response.status_code, 400)
            self.assertEqual(prompts, [])
            default = await self.http.post("/admin/api/accounts/a/image-test", json={})
            custom = await self.http.post(
                "/admin/api/accounts/a/image-test", json={"prompt": "  一只小狗  "}
            )
        self.assertEqual(prompts, [DEFAULT_TEST_PROMPT, "一只小狗"])
        self.assertEqual(default.json()["image_test"]["prompt"], DEFAULT_TEST_PROMPT)
        self.assertEqual(custom.json()["image_test"]["prompt"], "一只小狗")
