# ruff: noqa: PT009, PT027, SIM117
"""Container bootstrap tests with disposable synthetic keys only."""

import importlib.util
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

spec = importlib.util.spec_from_file_location(
    "container_entrypoint", Path(__file__).resolve().parents[1] / "docker" / "entrypoint.py"
)
entrypoint = importlib.util.module_from_spec(spec)
spec.loader.exec_module(entrypoint)


class TestContainerEntrypoint(unittest.TestCase):
    def test_keys_are_distinct_private_and_reused(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "keys"
            with patch.dict(
                os.environ, {"GEMINI_BRIDGE_API_KEY": "", "GEMINI_BRIDGE_ADMIN_KEY": ""}
            ):
                entrypoint.configure_keys(path)
                service = os.environ["GEMINI_BRIDGE_API_KEY"]
                admin = os.environ["GEMINI_BRIDGE_ADMIN_KEY"]
                self.assertNotEqual(service, admin)
                self.assertEqual(path.stat().st_mode & 0o777, 0o700)
                for file in path.iterdir():
                    self.assertEqual(file.stat().st_mode & 0o777, 0o600)
                os.environ["GEMINI_BRIDGE_API_KEY"] = ""
                os.environ["GEMINI_BRIDGE_ADMIN_KEY"] = ""
                entrypoint.configure_keys(path)
                self.assertEqual(os.environ["GEMINI_BRIDGE_API_KEY"], service)
                self.assertEqual(os.environ["GEMINI_BRIDGE_ADMIN_KEY"], admin)

    def test_explicit_keys_are_not_saved(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)
            with patch.dict(
                os.environ,
                {"GEMINI_BRIDGE_API_KEY": "fake-service", "GEMINI_BRIDGE_ADMIN_KEY": "fake-admin"},
            ):
                entrypoint.configure_keys(path)
                self.assertEqual(list(path.iterdir()), [])

    def test_empty_or_equal_keys_fail_without_replacing_them(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)
            (path / "service-key.txt").write_text("")
            with patch.dict(
                os.environ, {"GEMINI_BRIDGE_API_KEY": "", "GEMINI_BRIDGE_ADMIN_KEY": ""}
            ):
                with self.assertRaises(RuntimeError):
                    entrypoint.configure_keys(path)
                self.assertEqual((path / "service-key.txt").read_text(), "")
            with patch.dict(
                os.environ, {"GEMINI_BRIDGE_API_KEY": "same", "GEMINI_BRIDGE_ADMIN_KEY": "same"}
            ):
                with self.assertRaises(RuntimeError):
                    entrypoint.configure_keys(path)
