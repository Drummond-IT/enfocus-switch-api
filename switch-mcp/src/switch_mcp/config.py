"""Runtime configuration.

Settings come from, in order of precedence:

1. environment variables (``SWITCH_URL`` ...), e.g. set in the MCP client config;
2. a config file of ``KEY=value`` lines: ``--env-file PATH``, else ``SWITCH_ENV_FILE``,
   else ``~/.config/enfocus-switch-mcp/config.env`` if it exists.
"""

from __future__ import annotations

import ipaddress
import os
import stat
import sys
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

DEFAULT_ENV_FILE = Path.home() / ".config" / "enfocus-switch-mcp" / "config.env"
DEFAULT_DOWNLOAD_DIR = Path.home() / "switch-mcp-downloads"


class ConfigError(ValueError):
    """The configuration can't work; the message says what to fix."""


def _bool(name: str, value: str | None, default: bool) -> bool:
    if value is None or value.strip() == "":
        return default
    v = value.strip().lower()
    if v in {"1", "true", "yes", "on"}:
        return True
    if v in {"0", "false", "no", "off"}:
        return False
    raise ConfigError(f"{name} must be true or false, got {value!r}.")


def _paths(value: str | None) -> list[Path]:
    if not value:
        return []
    return [Path(p.strip()).expanduser().resolve() for p in value.split(os.pathsep) if p.strip()]


def read_env_file(path: Path) -> dict[str, str]:
    """Parse ``KEY=value`` lines. Blank lines and ``#`` comments are ignored; values may be quoted."""
    values: dict[str, str] = {}
    for n, raw in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        key, sep, value = line.partition("=")
        if not sep or not key.strip():
            raise ConfigError(f"{path}, line {n}: expected KEY=value, got {raw!r}.")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[key.strip()] = value
    return values


def env_file_is_private(path: Path) -> bool:
    """False when a POSIX config file is readable by group/others (it may hold the password)."""
    if sys.platform == "win32":
        return True
    return not (path.stat().st_mode & (stat.S_IRWXG | stat.S_IRWXO))


def _is_local(host: str) -> bool:
    if host in ("localhost", ""):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


@dataclass
class Settings:
    url: str = "http://127.0.0.1:51088"
    username: str = ""
    password: str = field(default="", repr=False)
    # Switch expects the password RSA-encrypted with Enfocus' published key.
    # Leave empty to use the key bundled with this package.
    public_key_path: str = ""
    lang: str = "enUS"
    timeout: float = 60.0
    verify_tls: bool = True
    ca_bundle: str = ""
    # Safety switches. Out of the box the connector can only look, not touch.
    allow_write: bool = False
    allow_flow_control: bool = False
    # Local folders files may be read from (submit, replace, checks). Empty = none.
    upload_dirs: list[Path] = field(default_factory=list)
    # Where downloaded jobs and reports are written.
    download_dir: Path = field(default_factory=lambda: DEFAULT_DOWNLOAD_DIR)
    # Largest local file the connector will read or upload.
    max_file_mb: int = 500
    # Which config file was loaded, if any (for `check` output).
    env_file: Path | None = None

    # ---- automations (all optional; see docs/ai-connector-research.md) ----
    # JSON map of preflight category -> auto-fix route (see automation/autofix.py).
    autofix_map: str = ""
    # JSON file of per-customer standing agreements (see automation/customer_rules.py).
    customer_rules: str = ""
    # SQLite file for preflight analytics (see automation/analytics.py). Empty = off.
    analytics_db: str = ""
    analytics_retention_days: int = 365
    # JSON-lines audit trail of automated changes (Pace writes, service actions). Empty = log only.
    audit_log: str = ""
    # Name of the checkpoint connection that means "proof approved".
    approve_connection: str = "Approve"
    # Regexes (";"-separated, one capture group) for job numbers in file names / emails.
    job_number_patterns: str = ""
    # Pace: read-only database + queries file; API settings for (future) writes.
    pace_db_dsn: str = field(default="", repr=False)
    pace_queries_file: str = ""
    pace_api_url: str = ""
    pace_api_username: str = ""
    pace_api_password: str = field(default="", repr=False)
    pace_item_template_map: str = ""
    # Pace API writes (see pace_api.example.json): off unless PACE_ALLOW_WRITE=true.
    pace_api_config: str = ""
    pace_allow_write: bool = False
    pace_allowed_statuses: str = ""
    pace_proof_approved_status: str = "Proof Approved"
    # Digest: incoming-webhook URL (Teams/Slack) and "stuck" threshold.
    digest_webhook_url: str = field(default="", repr=False)
    digest_stuck_hours: float = 4.0

    @classmethod
    def load(cls, env_file: str | Path | None = None, environ: dict[str, str] | None = None) -> Settings:
        environ = dict(os.environ if environ is None else environ)
        file_values: dict[str, str] = {}
        path = Path(env_file).expanduser() if env_file else None
        if path is None and environ.get("SWITCH_ENV_FILE"):
            path = Path(environ["SWITCH_ENV_FILE"]).expanduser()
        if path is not None and not path.is_file():
            raise ConfigError(f"Config file not found: {path}")
        if path is None and DEFAULT_ENV_FILE.is_file():
            path = DEFAULT_ENV_FILE
        if path is not None:
            file_values = read_env_file(path)
        settings = cls.from_env({**file_values, **environ})
        settings.env_file = path.resolve() if path else None
        return settings

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> Settings:
        env = dict(os.environ if env is None else env)
        s = cls()
        s.url = env.get("SWITCH_URL", s.url).strip().rstrip("/")
        s.username = env.get("SWITCH_USERNAME", "").strip()
        s.password = env.get("SWITCH_PASSWORD", "")
        if not s.password and env.get("SWITCH_PASSWORD_FILE"):
            pw_file = Path(env["SWITCH_PASSWORD_FILE"]).expanduser()
            if not pw_file.is_file():
                raise ConfigError(f"SWITCH_PASSWORD_FILE not found: {pw_file}")
            s.password = pw_file.read_text(encoding="utf-8").rstrip("\r\n")
        s.public_key_path = env.get("SWITCH_PUBLIC_KEY_PATH", "").strip()
        s.lang = env.get("SWITCH_LANG", s.lang).strip() or s.lang
        try:
            s.timeout = float(env.get("SWITCH_TIMEOUT", s.timeout))
            s.max_file_mb = int(env.get("SWITCH_MAX_FILE_MB", s.max_file_mb))
        except ValueError as exc:
            raise ConfigError(f"SWITCH_TIMEOUT / SWITCH_MAX_FILE_MB must be numbers: {exc}") from exc
        s.verify_tls = _bool("SWITCH_VERIFY_TLS", env.get("SWITCH_VERIFY_TLS"), True)
        s.ca_bundle = env.get("SWITCH_CA_BUNDLE", "").strip()
        s.allow_write = _bool("SWITCH_ALLOW_WRITE", env.get("SWITCH_ALLOW_WRITE"), False)
        s.allow_flow_control = _bool("SWITCH_ALLOW_FLOW_CONTROL", env.get("SWITCH_ALLOW_FLOW_CONTROL"), False)
        s.upload_dirs = _paths(env.get("SWITCH_UPLOAD_DIRS"))
        if env.get("SWITCH_DOWNLOAD_DIR", "").strip():
            s.download_dir = Path(env["SWITCH_DOWNLOAD_DIR"].strip()).expanduser().resolve()

        s.autofix_map = env.get("SWITCH_AUTOFIX_MAP", "").strip()
        s.customer_rules = env.get("CUSTOMER_RULES", "").strip()
        s.analytics_db = env.get("ANALYTICS_DB", "").strip()
        s.audit_log = env.get("AUDIT_LOG", "").strip()
        try:
            s.analytics_retention_days = int(env.get("ANALYTICS_RETENTION_DAYS", s.analytics_retention_days))
        except ValueError as exc:
            raise ConfigError(f"ANALYTICS_RETENTION_DAYS must be a whole number: {exc}") from exc
        s.approve_connection = env.get("SWITCH_APPROVE_CONNECTION", s.approve_connection).strip() or "Approve"
        s.job_number_patterns = env.get("PACE_JOB_NUMBER_PATTERNS", "").strip()
        s.pace_db_dsn = env.get("PACE_DB_DSN", "").strip()
        s.pace_queries_file = env.get("PACE_QUERIES_FILE", "").strip()
        s.pace_api_url = env.get("PACE_API_URL", "").strip()
        s.pace_api_username = env.get("PACE_API_USERNAME", "").strip()
        s.pace_api_password = env.get("PACE_API_PASSWORD", "")
        s.pace_item_template_map = env.get("PACE_ITEM_TEMPLATE_MAP", "").strip()
        s.pace_api_config = env.get("PACE_API_CONFIG", "").strip()
        s.pace_allow_write = _bool("PACE_ALLOW_WRITE", env.get("PACE_ALLOW_WRITE"), False)
        s.pace_allowed_statuses = env.get("PACE_ALLOWED_STATUSES", "").strip()
        s.pace_proof_approved_status = env.get("PACE_PROOF_APPROVED_STATUS", s.pace_proof_approved_status).strip()
        s.digest_webhook_url = env.get("DIGEST_WEBHOOK_URL", "").strip()
        try:
            s.digest_stuck_hours = float(env.get("DIGEST_STUCK_HOURS", s.digest_stuck_hours))
        except ValueError as exc:
            raise ConfigError(f"DIGEST_STUCK_HOURS must be a number: {exc}") from exc
        return s

    def pace_env(self) -> dict[str, str]:
        """Pace settings in the shape automation.pace.gateway_from_env expects."""
        return {
            "PACE_DB_DSN": self.pace_db_dsn, "PACE_QUERIES_FILE": self.pace_queries_file,
            "PACE_API_URL": self.pace_api_url, "PACE_API_USERNAME": self.pace_api_username,
            "PACE_API_PASSWORD": self.pace_api_password, "PACE_API_CONFIG": self.pace_api_config,
            "PACE_ALLOW_WRITE": "true" if self.pace_allow_write else "false",
            "PACE_ALLOWED_STATUSES": self.pace_allowed_statuses,
        }

    @property
    def pace_enabled(self) -> bool:
        return bool(self.pace_db_dsn or self.pace_api_config)

    @property
    def tls_verify(self) -> bool | str:
        """Value for httpx ``verify=``."""
        if self.ca_bundle:
            return self.ca_bundle
        return self.verify_tls

    def validate(self) -> tuple[list[str], list[str]]:
        """Return (errors, warnings). Errors mean the connector cannot work."""
        errors: list[str] = []
        warnings: list[str] = []
        parts = urlsplit(self.url)
        if parts.scheme not in ("http", "https") or not parts.hostname:
            errors.append(f"SWITCH_URL must look like http://host:51088 or https://host:port, got {self.url!r}.")
        elif parts.username or parts.password:
            errors.append("SWITCH_URL must not contain a user name or password; use SWITCH_USERNAME / SWITCH_PASSWORD.")
        elif parts.path not in ("", "/"):
            errors.append(f"SWITCH_URL must not contain a path ({parts.path!r}); use only scheme://host:port.")
        else:
            if parts.scheme == "http" and not _is_local(parts.hostname):
                warnings.append(
                    "SWITCH_URL uses plain http to another machine: the session token and the encrypted password "
                    "(which works as-is if captured) travel unprotected. Use https if your Switch Web Services "
                    "are behind TLS."
                )
            if parts.scheme == "https" and not self.verify_tls and not self.ca_bundle:
                warnings.append("TLS certificate checks are OFF (SWITCH_VERIFY_TLS=false). Prefer SWITCH_CA_BUNDLE.")
        if not self.username:
            errors.append("SWITCH_USERNAME is not set.")
        if not self.password:
            errors.append("SWITCH_PASSWORD (or SWITCH_PASSWORD_FILE) is not set.")
        if self.public_key_path and not Path(self.public_key_path).expanduser().is_file():
            errors.append(f"SWITCH_PUBLIC_KEY_PATH not found: {self.public_key_path}")
        if self.ca_bundle and not Path(self.ca_bundle).expanduser().is_file():
            errors.append(f"SWITCH_CA_BUNDLE not found: {self.ca_bundle}")
        for d in self.upload_dirs:
            if not d.is_dir():
                errors.append(f"SWITCH_UPLOAD_DIRS entry is not a folder: {d}")
        if self.timeout <= 0 or self.max_file_mb <= 0:
            errors.append("SWITCH_TIMEOUT and SWITCH_MAX_FILE_MB must be greater than 0.")
        if self.env_file and not env_file_is_private(self.env_file):
            warnings.append(f"{self.env_file} is readable by other users. Run: chmod 600 '{self.env_file}'")
        for name, value in (("SWITCH_AUTOFIX_MAP", self.autofix_map), ("CUSTOMER_RULES", self.customer_rules),
                            ("PACE_QUERIES_FILE", self.pace_queries_file),
                            ("PACE_ITEM_TEMPLATE_MAP", self.pace_item_template_map),
                            ("PACE_API_CONFIG", self.pace_api_config)):
            if value and not Path(value).expanduser().is_file():
                errors.append(f"{name} not found: {value}")
        if self.pace_db_dsn and not self.pace_queries_file:
            errors.append("PACE_DB_DSN is set but PACE_QUERIES_FILE is not.")
        if self.pace_api_url and not self.pace_api_url.startswith("https://"):
            api_host = urlsplit(self.pace_api_url).hostname or ""
            if not _is_local(api_host):
                warnings.append("PACE_API_URL is not https: the Pace API password travels unprotected.")
        if self.pace_allow_write and not self.pace_allowed_statuses:
            errors.append("PACE_ALLOW_WRITE is on but PACE_ALLOWED_STATUSES is empty: list the statuses "
                          "automation may set (comma separated).")
        if self.pace_allow_write:
            warnings.append("Pace writes are ON (PACE_ALLOW_WRITE=true): automation can change job statuses "
                            f"({self.pace_allowed_statuses}) and add notes in Pace.")
        if self.digest_webhook_url and not self.digest_webhook_url.startswith("https://"):
            errors.append("DIGEST_WEBHOOK_URL must start with https://.")
        if self.allow_write:
            warnings.append("Write tools are ON (SWITCH_ALLOW_WRITE=true): the assistant can submit, route and replace jobs.")
        if self.allow_flow_control:
            warnings.append("Flow control is ON (SWITCH_ALLOW_FLOW_CONTROL=true): the assistant can stop flows.")
        return errors, warnings
