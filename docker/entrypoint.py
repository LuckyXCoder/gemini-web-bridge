"""Single-process container startup with persistent, separately generated keys."""

import os
import runpy
import secrets
from pathlib import Path


def configure_keys(directory: Path):
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    for variable, filename in (
        ("GEMINI_BRIDGE_API_KEY", "service-key.txt"),
        ("GEMINI_BRIDGE_ADMIN_KEY", "admin-key.txt"),
    ):
        if os.environ.get(variable):
            continue
        path = directory / filename
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            pass
        else:
            with os.fdopen(fd, "w") as stream:
                stream.write(secrets.token_urlsafe(32) + "\n")
        path.chmod(0o600)
        value = path.read_text().strip()
        if not value:
            raise RuntimeError(f"Empty key file: {filename}")
        os.environ[variable] = value
    if os.environ["GEMINI_BRIDGE_API_KEY"] == os.environ["GEMINI_BRIDGE_ADMIN_KEY"]:
        raise RuntimeError("Administrator and service keys must differ")


if __name__ == "__main__":
    configure_keys(Path("/data/keys"))
    print("Gemini Bridge: /admin/ (keys in /data/keys; values are never printed)", flush=True)
    runpy.run_module("gemini_webapi.server", run_name="__main__")
