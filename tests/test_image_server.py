# ruff: noqa: PT009, PT027
"""Offline HTTP contract, queue and single-submission tests."""

import asyncio
import base64
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
from PIL import Image

from gemini_webapi import GeminiClient
from gemini_webapi.server import SingleSubmissionClient, create_app
from gemini_webapi.types import GeneratedImage


class TestImageServer(unittest.IsolatedAsyncioTestCase):
    def client(self):
        return SimpleNamespace(
            init=AsyncMock(),
            close=AsyncMock(),
            access_token="test",
            _check_account_status=lambda: True,
            generate_content=AsyncMock(),
        )

    async def test_auth_validation_and_models(self):
        client = self.client()
        app = create_app(client, api_key="test")
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as http,
        ):
            self.assertEqual((await http.get("/v1/models")).status_code, 401)
            headers = {"Authorization": "Bearer test"}
            self.assertEqual(len((await http.get("/v1/models", headers=headers)).json()["data"]), 2)
            for extra in [
                {"n": 2},
                {"size": "1024x1024"},
                {"response_format": "url"},
                {"model": "unknown"},
                {"aspect_ratio": "7:13"},
                {"prompt": " "},
                {"quality": "high"},
            ]:
                response = await http.post(
                    "/v1/images/generations", headers=headers, json={"prompt": "cat", **extra}
                )
                self.assertEqual(response.status_code, 400)
            client.generate_content.assert_not_called()

    async def test_success_encodes_real_image_and_passes_options(self):
        client = self.client()
        client.generate_content.return_value = SimpleNamespace(
            images=[GeneratedImage.model_construct(url="https://example.invalid/image")]
        )
        app = create_app(client, api_key="test")
        with tempfile.TemporaryDirectory() as directory:
            p = Path(directory) / "test.png"
            Image.new("RGB", (2, 3)).save(p)
            with patch.object(GeneratedImage, "save", new=AsyncMock(return_value=str(p))):
                async with (
                    app.router.lifespan_context(app),
                    httpx.AsyncClient(
                        transport=httpx.ASGITransport(app=app),
                        base_url="http://test",
                        headers={"Authorization": "Bearer test"},
                    ) as http,
                ):
                    response = await http.post(
                        "/v1/images/generations",
                        json={
                            "model": "gemini-web-pro-image",
                            "prompt": "cat",
                            "aspect_ratio": "1:8",
                        },
                    )
                    self.assertEqual(response.status_code, 200)
                    self.assertEqual(
                        base64.b64decode(response.json()["data"][0]["b64_json"]), p.read_bytes()
                    )
            client.generate_content.assert_awaited_once_with(
                "cat", model="gemini-pro", pro_image=True, aspect_ratio="1:8"
            )

    async def test_no_image_is_failure(self):
        client = self.client()
        client.generate_content.return_value = SimpleNamespace(images=[])
        app = create_app(client, api_key="test")
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://test",
                headers={"Authorization": "Bearer test"},
            ) as http,
        ):
            response = await http.post("/v1/images/generations", json={"prompt": "cat"})
            self.assertEqual(response.status_code, 502)
            self.assertEqual(client.generate_content.await_count, 1)

    async def test_serial_queue_full_and_disconnected_caller(self):
        client = self.client()
        started = asyncio.Event()
        release = asyncio.Event()
        calls = 0

        async def generate(*args):
            nonlocal calls
            calls += 1
            started.set()
            await release.wait()
            return {"created": 0, "data": [{"b64_json": "test"}]}

        app = create_app(client, api_key="test", queue_size=1)
        with patch("gemini_webapi.server.generate_image", generate):
            async with (
                app.router.lifespan_context(app),
                httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app),
                    base_url="http://test",
                    headers={"Authorization": "Bearer test"},
                ) as http,
            ):

                async def post():
                    return await http.post("/v1/images/generations", json={"prompt": "cat"})

                first = asyncio.create_task(post())
                await asyncio.wait_for(started.wait(), 1)
                first.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await first
                second = asyncio.create_task(post())
                async with asyncio.timeout(1):
                    while app.state.queue.qsize() != 1:
                        await asyncio.sleep(0)
                self.assertEqual((await post()).status_code, 429)
                self.assertEqual(calls, 1)
                release.set()
                self.assertEqual((await second).status_code, 200)
                self.assertEqual(calls, 2)

    async def test_timeout_does_not_retry(self):
        client = self.client()

        async def slow(*args, **kwargs):
            await asyncio.sleep(10)

        client.generate_content.side_effect = slow
        app = create_app(client, api_key="test", timeout=0.03)
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://test",
                headers={"Authorization": "Bearer test"},
            ) as http,
        ):
            response = await http.post("/v1/images/generations", json={"prompt": "cat"})
            self.assertEqual(response.status_code, 504)
            self.assertEqual(client.generate_content.await_count, 1)

    async def test_generation_retry_decorator_is_bypassed(self):
        calls = []

        async def underlying(*args, **kwargs):
            calls.append(1)
            raise RuntimeError("failure")
            yield

        def decorated(*args, **kwargs):
            raise AssertionError("retry wrapper called")

        decorated.__wrapped__ = underlying
        with patch.object(GeminiClient, "_generate", decorated), self.assertRaises(RuntimeError):
            async for _ in SingleSubmissionClient()._generate("cat"):
                pass
        self.assertEqual(len(calls), 1)
