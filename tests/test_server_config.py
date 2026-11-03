# ruff: noqa: PT009, PT027
"""Offline YAML validation and application configuration tests."""

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from gemini_webapi.server_config import load_server_config


class TestServerConfig(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / "bridge.yaml"
        self.env = patch.dict(
            os.environ, {"GEMINI_BRIDGE_CONFIG": str(self.path), "GEMINI_BRIDGE_ADMIN_KEY": ""}
        )
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.directory.cleanup()

    def test_defaults_and_partial_values(self):
        with patch.dict(os.environ, {"GEMINI_BRIDGE_CONFIG": ""}):
            self.assertEqual(load_server_config().scheduler.queue_capacity, 4)
        self.path.write_text("scheduler:\n  queue_capacity: 9\n")
        result = load_server_config()
        self.assertEqual(result.scheduler.queue_capacity, 9)
        self.assertEqual(result.scheduler.execution_timeout_seconds, 300)

    def test_rejects_missing_invalid_and_unsupported_settings(self):
        with self.assertRaises(ValueError):
            load_server_config()
        for text in [
            "scheduler: [",
            "scheduler: []",
            "scheduler:\n  queue_capacity: 0",
            "scheduler:\n  execution_timeout_seconds: -2",
            "scheduler:\n  queue_capacity: true",
            'scheduler:\n  queue_capacity: "4"',
            "scheduler:\n  execution_timeout_seconds: 1.2",
            "scheduler:\n  per_account_concurrency: 2",
            "scheduler:\n  strategy: round_robin",
            "!!python/object/apply:os.system ['echo unsafe']",
        ]:
            self.path.write_text(text)
            with self.assertRaises(ValueError, msg=text):
                load_server_config()

    async def test_yaml_applies_to_actual_queue_and_startup_snapshot(self):
        from test_admin import FakeClient

        from gemini_webapi.server import Account, create_app

        self.path.write_text("scheduler:\n  queue_capacity: 7\n  execution_timeout_seconds: 90\n")
        app = create_app(api_key="test", accounts=[Account("a", FakeClient("fake"))])
        async with app.router.lifespan_context(app):
            self.assertEqual(app.state.queue.maxsize, 7)
            self.assertEqual(app.state.scheduler["execution_timeout_seconds"], 90)
            self.path.write_text("scheduler:\n  queue_capacity: 2\n")
            self.assertEqual(app.state.queue.maxsize, 7)

    def test_explicit_python_arguments_override_yaml(self):
        from test_admin import FakeClient

        from gemini_webapi.server import Account, create_app

        self.path.write_text("scheduler:\n  queue_capacity: 7\n  execution_timeout_seconds: 90\n")
        app = create_app(
            api_key="test", accounts=[Account("a", FakeClient("fake"))], timeout=0.03, queue_size=1
        )
        self.assertEqual(
            app.state.scheduler, {"queue_capacity": 1, "execution_timeout_seconds": 0.03}
        )
