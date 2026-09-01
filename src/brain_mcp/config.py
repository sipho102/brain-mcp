"""Environment configuration for brain-mcp. Fails fast and loudly."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


class ConfigError(RuntimeError):
    """Raised when the environment is missing or invalid configuration."""


@dataclass(frozen=True)
class Config:
    root: Path
    name: str
    transport: str
    # repr=False: dataclasses auto-generate __repr__ from every field by
    # default, which would otherwise print the bearer token in plaintext
    # anywhere a Config ends up in a log line, traceback, or debugger.
    token: str | None = field(repr=False)
    port: int
    bind_address: str
    log_level: str

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> "Config":
        env = env if env is not None else os.environ

        transport = env.get("BRAIN_TRANSPORT", "http").lower()
        if transport not in {"http", "stdio"}:
            raise ConfigError(f"BRAIN_TRANSPORT must be 'http' or 'stdio', got: {transport!r}")

        root_raw = _require(env, "BRAIN_ROOT")
        name = _require(env, "BRAIN_NAME")
        # The bearer token gates network access; stdio is a local pipe to a
        # single parent process with no network exposure, so it has nothing
        # to gate and BRAIN_TOKEN is not required in that mode.
        token = _require(env, "BRAIN_TOKEN") if transport == "http" else env.get("BRAIN_TOKEN") or None

        root = Path(root_raw)
        if not root.is_absolute():
            raise ConfigError(f"BRAIN_ROOT must be an absolute path, got: {root_raw!r}")
        if not root.is_dir():
            raise ConfigError(f"BRAIN_ROOT does not exist or is not a directory: {root}")
        if not os.access(root, os.R_OK | os.X_OK):
            raise ConfigError(f"BRAIN_ROOT is not readable: {root}")

        port_raw = env.get("PORT", "3100")
        try:
            port = int(port_raw)
        except ValueError as exc:
            raise ConfigError(f"PORT must be an integer, got: {port_raw!r}") from exc
        if not (0 < port < 65536):
            raise ConfigError(f"PORT out of range: {port}")

        bind_address = env.get("BIND_ADDRESS", "0.0.0.0")

        log_level = env.get("LOG_LEVEL", "INFO").upper()
        if log_level not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            raise ConfigError(
                f"LOG_LEVEL must be one of DEBUG/INFO/WARNING/ERROR/CRITICAL, got: {log_level!r}"
            )

        return cls(
            root=root.resolve(),
            name=name,
            transport=transport,
            token=token,
            port=port,
            bind_address=bind_address,
            log_level=log_level,
        )


def _require(env: dict[str, str], key: str) -> str:
    value = env.get(key)
    if not value:
        raise ConfigError(f"Required environment variable {key} is not set")
    return value
