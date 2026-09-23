"""Runtime configuration, read from environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _bool(value: str | None, default: bool = False) -> bool:
    if value is None or value.strip() == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _paths(value: str | None) -> list[Path]:
    if not value:
        return []
    return [Path(p).expanduser().resolve() for p in value.split(os.pathsep) if p.strip()]


@dataclass
class Settings:
    """Connector settings.

    Every value can be set with an environment variable of the same name
    prefixed with ``SWITCH_`` (for example ``SWITCH_URL``).
    """

    url: str = "http://127.0.0.1:51088"
    username: str = ""
    password: str = ""
    # Switch expects the password RSA-encrypted with Enfocus' published key.
    # Leave empty to use the key bundled with this package.
    public_key_path: str = ""
    # Language for Switch error messages (enUS, deDE, frFR, ...).
    lang: str = "enUS"
    timeout: float = 60.0
    verify_tls: bool = True
    # Safety switches. Out of the box the connector can only look, not touch.
    allow_write: bool = False
    allow_flow_control: bool = False
    # Local folders jobs may be submitted from. Empty = submission disabled.
    upload_dirs: list[Path] = field(default_factory=list)
    # Where downloaded jobs and reports are written.
    download_dir: Path = field(default_factory=lambda: Path("switch-downloads").resolve())

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> Settings:
        env = dict(os.environ if env is None else env)
        s = cls()
        s.url = env.get("SWITCH_URL", s.url).rstrip("/")
        s.username = env.get("SWITCH_USERNAME", "")
        s.password = env.get("SWITCH_PASSWORD", "")
        s.public_key_path = env.get("SWITCH_PUBLIC_KEY_PATH", "")
        s.lang = env.get("SWITCH_LANG", s.lang)
        s.timeout = float(env.get("SWITCH_TIMEOUT", s.timeout))
        s.verify_tls = _bool(env.get("SWITCH_VERIFY_TLS"), True)
        s.allow_write = _bool(env.get("SWITCH_ALLOW_WRITE"), False)
        s.allow_flow_control = _bool(env.get("SWITCH_ALLOW_FLOW_CONTROL"), False)
        s.upload_dirs = _paths(env.get("SWITCH_UPLOAD_DIRS"))
        if env.get("SWITCH_DOWNLOAD_DIR"):
            s.download_dir = Path(env["SWITCH_DOWNLOAD_DIR"]).expanduser().resolve()
        return s
