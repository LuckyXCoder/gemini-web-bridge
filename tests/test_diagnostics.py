# ruff: noqa: PT009
"""Offline diagnostic privacy and stage tests."""

import json
import logging
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock

from gemini_webapi.diagnostics import PrivateRotatingHandler, event, scope
from gemini_webapi.server import ImageRequest, generate_image


class TestDiagnostics(unittest.IsolatedAsyncioTestCase):
    async def test_sensitive_fields_are_not_logged_and_context_is_preserved(self):
        with self.assertLogs("gemini_bridge_diagnostics", level="INFO") as logs:
            with scope("account-test") as request, scope("account-test"):
                event(
                    "safe",
                    reason="failure",
                    error_type="ValueError",
                    cookies="SECRET",
                    prompt="SECRET",
                    headers="SECRET",
                    stage="https://private.example/SECRET",
                )
            event("outside")
        data = [json.loads(record.message) for record in logs.records]
        self.assertEqual(data[0]["request_id"], request)
        self.assertNotIn("SECRET", str(data))
        self.assertNotIn("request_id", data[1])

    async def test_generation_failure_logs_stage_without_exception_body(self):
        client = AsyncMock()
        client.generate_content.side_effect = RuntimeError("SECRET")
        with self.assertLogs("gemini_bridge_diagnostics", level="INFO") as logs:  # noqa: SIM117
            with self.assertRaises(RuntimeError):  # noqa: PT027
                await generate_image(client, ImageRequest(prompt="SECRET"), "model")
        data = [json.loads(record.message) for record in logs.records]
        self.assertEqual(data[-1]["stage"], "generation")
        self.assertEqual(data[-1]["error_type"], "RuntimeError")
        self.assertNotIn("SECRET", str(data))

    async def test_log_rotation_preserves_private_permissions(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "diagnostics.jsonl"
            handler = PrivateRotatingHandler(path, maxBytes=40, backupCount=2)
            try:
                for _ in range(5):
                    handler.emit(logging.LogRecord("test", 20, "", 0, "x" * 25, (), None))
            finally:
                handler.close()
            self.assertGreater(len(list(Path(directory).iterdir())), 1)
            for file in Path(directory).iterdir():
                self.assertEqual(file.stat().st_mode & 0o777, 0o600)
