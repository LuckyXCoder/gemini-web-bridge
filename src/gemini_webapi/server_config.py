"""Startup-only YAML settings for the optional image server."""

import os
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, ValidationError


class SchedulerConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    queue_capacity: int = Field(default=4, strict=True, ge=1)
    execution_timeout_seconds: int = Field(default=300, strict=True, ge=1)


class ServerConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scheduler: SchedulerConfig = Field(default_factory=SchedulerConfig)


def load_server_config() -> ServerConfig:
    path = os.environ.get("GEMINI_BRIDGE_CONFIG")
    if not path:
        return ServerConfig()
    import yaml

    try:
        text = Path(path).expanduser().read_text(encoding="utf-8")
    except OSError:
        raise ValueError("Cannot read GEMINI_BRIDGE_CONFIG file") from None
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError:
        raise ValueError("Invalid YAML in GEMINI_BRIDGE_CONFIG") from None
    try:
        return ServerConfig.model_validate({} if data is None else data)
    except ValidationError as exc:
        fields = ", ".join(".".join(map(str, error["loc"])) or "root" for error in exc.errors())
        raise ValueError(
            f"Invalid server configuration fields: {fields}. "
            "Only scheduler.queue_capacity and scheduler.execution_timeout_seconds "
            "are supported; values must be positive integers."
        ) from None
