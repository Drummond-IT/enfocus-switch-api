"""Pace (EFI/ePS Pace MIS) gateway.

Everything that needs Pace goes through ``PaceGateway``, so workflows don't
care where the data comes from:

* ``PacePostgresGateway`` - READ-ONLY queries against the Pace PostgreSQL
  database (use a read-only role, ideally on a replica). The SQL lives in a
  queries file you fill in for your Pace schema (see ``pace_queries.example.sql``);
  each query must return the column names documented there.
* Writes (status updates, notes) go through the official Pace API, never straight
  into the database: ``PaceApiWriter`` sends requests the Pace team defines in
  ``pace_api.json``, only when ``PACE_ALLOW_WRITE=true`` and only for statuses in
  ``PACE_ALLOWED_STATUSES``. Otherwise every write reports "manual step".
* ``FakePaceGateway`` - in-memory data for tests and demos.

Claude can also reach Pace through a separate Pace MCP server. The MCP
prompts in ``server.py`` show how to combine the two without this gateway.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import quote
from xml.sax.saxutils import escape as xml_escape

import httpx

from .specs import JobSpec

# Query name -> columns it must return (aliases in your SELECT).
QUERY_CONTRACT: dict[str, list[str]] = {
    "job_spec": ["job_number", "customer", "product", "trim_width_in", "trim_height_in", "pages",
                 "front_inks", "back_inks", "spot_colors", "quantity", "stock", "binding"],
    "job_status": ["job_number", "customer", "status", "due_date", "csr", "description"],
    "similar_jobs": ["job_number", "customer", "description", "trim_width_in", "trim_height_in", "pages",
                     "quantity", "price", "created"],
}


log = logging.getLogger("switch_mcp.pace")

class PaceError(RuntimeError):
    pass


class PaceNotConfigured(PaceError):
    pass


class PaceWriteNotImplemented(PaceError):
    """Raised by writes until ``PaceApiWriter`` is implemented; workflows turn it into a manual step."""


class PaceGateway(Protocol):
    def get_job_spec(self, job_number: str) -> JobSpec | None: ...
    def get_job_status(self, job_number: str) -> dict[str, Any] | None: ...
    def find_similar_jobs(self, spec: JobSpec, limit: int = 10) -> list[dict[str, Any]]: ...
    def update_job_status(self, job_number: str, status: str, note: str | None = None) -> dict[str, Any]: ...
    def add_job_note(self, job_number: str, note: str) -> dict[str, Any]: ...


def load_queries(path: str | Path) -> dict[str, str]:
    """Parse a SQL file of ``-- name: <query>`` sections. Placeholders use %(name)s (psycopg style)."""
    text = Path(path).expanduser().read_text(encoding="utf-8")
    queries: dict[str, str] = {}
    current: str | None = None
    buf: list[str] = []
    for line in text.splitlines():
        m = re.match(r"\s*--\s*name:\s*(\w+)\s*$", line)
        if m:
            if current:
                queries[current] = "\n".join(buf).strip()
            current, buf = m.group(1), []
        elif current:
            buf.append(line)
    if current:
        queries[current] = "\n".join(buf).strip()
    for name, sql in queries.items():
        statement = re.sub(r"--[^\n]*", "", sql).strip().rstrip(";").strip()
        if not re.match(r"(?is)^(select|with)\b", statement) or ";" in statement:
            raise PaceError(f"Query '{name}' must be a single SELECT statement.")
        queries[name] = statement
    return queries


class PaceApiWriter:
    """Writes to Pace through its HTTP API, driven by a config file (``PACE_API_CONFIG``).

    The Pace team fills in, per operation, the HTTP method, path and body template from the
    Pace API documentation (see ``pace_api.example.json``). Placeholders ``{job_number}``,
    ``{status}`` and ``{note}`` are escaped for the body type (JSON or XML/SOAP) and URL-quoted
    in the path. Safety:

    * nothing is sent unless ``PACE_ALLOW_WRITE=true``; otherwise writes report "manual step";
    * only statuses in ``PACE_ALLOWED_STATUSES`` can be set;
    * every attempt is written to the audit callback (``audit``), success or not.
    """

    OPERATIONS = ("update_job_status", "add_job_note")

    def __init__(self, config: dict[str, Any] | None = None, base_url: str | None = None,
                 username: str | None = None, password: str | None = None, allow_write: bool = False,
                 allowed_statuses: list[str] | None = None, transport: Any = None,
                 audit: Callable[[dict[str, Any]], None] | None = None):
        self.config = config or {}
        self.base_url = (base_url or self.config.get("base_url") or "").rstrip("/")
        self.username, self._password = username, password
        self.allow_write = allow_write
        self.allowed_statuses = [s.strip() for s in (allowed_statuses or []) if s.strip()]
        self._transport = transport
        self._audit = audit or (lambda event: None)

    @classmethod
    def load_config(cls, path: str | Path) -> dict[str, Any]:
        data = json.loads(Path(path).expanduser().read_text(encoding="utf-8"))
        ops = data.get("operations") if isinstance(data, dict) else None
        if not isinstance(ops, dict):
            raise PaceError(f"{path}: expected an object with an 'operations' object.")
        for name, op in ops.items():
            if name.startswith("_"):
                continue
            if name not in cls.OPERATIONS:
                raise PaceError(f"{path}: unknown operation '{name}' (known: {', '.join(cls.OPERATIONS)}).")
            missing = [k for k in ("method", "path", "body") if not op.get(k)]
            if missing:
                raise PaceError(f"{path}: operation '{name}' is missing {', '.join(missing)}.")
            if str(op["method"]).upper() not in ("POST", "PUT", "PATCH"):
                raise PaceError(f"{path}: operation '{name}' method must be POST, PUT or PATCH.")
            content_type = str(op.get("content_type", "application/json")).lower()
            if "json" not in content_type and "xml" not in content_type:
                # Only JSON and XML bodies can be escaped safely; values come partly from customers.
                raise PaceError(f"{path}: operation '{name}' content_type must be JSON or XML.")
        return data

    @property
    def configured(self) -> bool:
        return bool(self.base_url and self.config.get("operations"))

    def _fill(self, template: str, values: dict[str, str], content_type: str) -> str:
        def esc(v: str) -> str:
            if "json" in content_type:
                return json.dumps(v)
            if "xml" in content_type:
                return xml_escape(v, {'"': "&quot;", "'": "&apos;"})
            return v
        out = template
        for key, value in values.items():
            out = out.replace("{" + key + "}", esc(value))
        return out

    def _send(self, operation: str, values: dict[str, str]) -> dict[str, Any]:
        event = {"operation": operation, **{k: v for k, v in values.items() if k != "note"},
                 "note_chars": len(values.get("note", ""))}
        if not self.configured:
            self._audit(event | {"result": "manual", "reason": "not configured"})
            raise PaceWriteNotImplemented(
                f"Pace writes aren't configured (PACE_API_CONFIG): do '{operation}' for job "
                f"{values.get('job_number')} in Pace by hand.")
        if not self.allow_write:
            self._audit(event | {"result": "manual", "reason": "PACE_ALLOW_WRITE is off"})
            raise PaceWriteNotImplemented(
                f"Pace writes are turned off (PACE_ALLOW_WRITE): do '{operation}' for job "
                f"{values.get('job_number')} in Pace by hand.")
        op = self.config["operations"].get(operation)
        if not op:
            self._audit(event | {"result": "manual", "reason": "operation not configured"})
            raise PaceWriteNotImplemented(f"No '{operation}' operation in PACE_API_CONFIG: do it in Pace by hand.")
        content_type = op.get("content_type", "application/json")
        path = op["path"]
        for key, value in values.items():
            path = path.replace("{" + key + "}", quote(value, safe=""))
        body = self._fill(op["body"], values, content_type)
        headers = {"Content-Type": content_type, **{str(k): str(v) for k, v in op.get("headers", {}).items()}}
        auth = (self.username, self._password) if self.username else None
        try:
            with httpx.Client(base_url=self.base_url, timeout=float(self.config.get("timeout", 30)),
                              transport=self._transport, follow_redirects=False) as client:
                resp = client.request(str(op["method"]).upper(), path, content=body.encode("utf-8"),
                                      headers=headers, auth=auth)
        except httpx.HTTPError as exc:
            self._audit(event | {"result": "failed", "reason": exc.__class__.__name__})
            raise PaceError(f"Pace API not reachable: {exc.__class__.__name__}") from exc
        if resp.status_code >= 400:
            self._audit(event | {"result": "failed", "http_status": resp.status_code})
            log.warning("Pace API refused %s (HTTP %s): %s", operation, resp.status_code, resp.text[:500])
            raise PaceError(f"Pace API refused '{operation}' (HTTP {resp.status_code}); details are in the service log.")
        self._audit(event | {"result": "done", "http_status": resp.status_code})
        return {"ok": True, "http_status": resp.status_code}

    def update_job_status(self, job_number: str, status: str, note: str | None = None) -> dict[str, Any]:
        if self.allowed_statuses and status not in self.allowed_statuses:
            raise PaceError(f"Status '{status}' is not in PACE_ALLOWED_STATUSES "
                            f"({', '.join(self.allowed_statuses)}).")
        if not self.allowed_statuses and self.allow_write:
            raise PaceError("PACE_ALLOWED_STATUSES is empty: list the statuses automation may set.")
        return self._send("update_job_status", {"job_number": job_number, "status": status, "note": note or ""})

    def add_job_note(self, job_number: str, note: str) -> dict[str, Any]:
        return self._send("add_job_note", {"job_number": job_number, "note": note[:4000]})


class PacePostgresGateway:
    """Read-only access to the Pace database. Requires ``pip install 'enfocus-switch-mcp[pace]'``."""

    can_read = True

    def __init__(self, dsn: str, queries: dict[str, str], writer: PaceApiWriter | None = None,
                 statement_timeout_ms: int = 10_000):
        missing = [q for q in ("job_spec", "job_status") if q not in queries]
        if missing:
            raise PaceNotConfigured(f"Pace queries file is missing: {', '.join(missing)}.")
        self.dsn, self.queries, self.writer = dsn, queries, writer or PaceApiWriter()
        self.statement_timeout_ms = statement_timeout_ms

    def _rows(self, name: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        if name not in self.queries:
            raise PaceNotConfigured(f"No '{name}' query in the Pace queries file.")
        try:
            import psycopg
            from psycopg.rows import dict_row
        except ImportError as exc:  # pragma: no cover - depends on the optional extra
            raise PaceNotConfigured("Install the Pace extra: uv tool install './switch-mcp[pace]'.") from exc
        with psycopg.connect(self.dsn, row_factory=dict_row, autocommit=False, connect_timeout=5) as conn:
            conn.read_only = True  # the session refuses writes even if the role could
            with conn.cursor() as cur:
                cur.execute(f"SET LOCAL statement_timeout = {int(self.statement_timeout_ms)}")
                cur.execute(self.queries[name], params)
                rows = cur.fetchmany(200)
            conn.rollback()
        return [dict(r) for r in rows]

    def get_job_spec(self, job_number: str) -> JobSpec | None:
        rows = self._rows("job_spec", {"job_number": job_number})
        return JobSpec.from_dict(rows[0]) if rows else None

    def get_job_status(self, job_number: str) -> dict[str, Any] | None:
        rows = self._rows("job_status", {"job_number": job_number})
        return rows[0] if rows else None

    def find_similar_jobs(self, spec: JobSpec, limit: int = 10) -> list[dict[str, Any]]:
        w, h = spec.trim_in or (None, None)
        return self._rows("similar_jobs", {
            "trim_width_in": w, "trim_height_in": h, "pages": spec.pages, "customer": spec.customer,
            "limit": max(1, min(limit, 50)),
        })

    def update_job_status(self, job_number: str, status: str, note: str | None = None) -> dict[str, Any]:
        return self.writer.update_job_status(job_number, status, note)

    def add_job_note(self, job_number: str, note: str) -> dict[str, Any]:
        return self.writer.add_job_note(job_number, note)


@dataclass
class FakePaceGateway:
    """In-memory Pace for tests and demos. Records writes instead of making them."""

    jobs: dict[str, dict[str, Any]] = field(default_factory=dict)
    writes: list[dict[str, Any]] = field(default_factory=list)
    allow_writes: bool = True
    can_read: bool = True

    def get_job_spec(self, job_number: str) -> JobSpec | None:
        job = self.jobs.get(job_number)
        return JobSpec.from_dict({"job_number": job_number, **job["spec"]}) if job else None

    def get_job_status(self, job_number: str) -> dict[str, Any] | None:
        job = self.jobs.get(job_number)
        return {"job_number": job_number, **job.get("status", {})} if job else None

    def find_similar_jobs(self, spec: JobSpec, limit: int = 10) -> list[dict[str, Any]]:
        out = []
        for number, job in self.jobs.items():
            other = JobSpec.from_dict(job["spec"])
            if spec.trim_in and other.trim_in and sorted(spec.trim_in) == sorted(other.trim_in):
                out.append({"job_number": number, **job.get("status", {}), **job["spec"]})
        return out[:limit]

    def _write(self, kind: str, **data: Any) -> dict[str, Any]:
        if not self.allow_writes:
            raise PaceWriteNotImplemented(f"Pace write not implemented yet: {kind} {data}")
        self.writes.append({"kind": kind, **data})
        return {"ok": True}

    def update_job_status(self, job_number: str, status: str, note: str | None = None) -> dict[str, Any]:
        return self._write("status", job_number=job_number, status=status, note=note)

    def add_job_note(self, job_number: str, note: str) -> dict[str, Any]:
        return self._write("note", job_number=job_number, note=note)


class PaceWriteOnlyGateway:
    """Pace API writes without database reads (reads report that PACE_DB_DSN is needed)."""

    can_read = False

    def __init__(self, writer: PaceApiWriter):
        self.writer = writer

    def _no_reads(self, *args: Any) -> Any:
        raise PaceNotConfigured("Reading from Pace needs PACE_DB_DSN and PACE_QUERIES_FILE.")

    get_job_spec = get_job_status = find_similar_jobs = _no_reads

    def update_job_status(self, job_number: str, status: str, note: str | None = None) -> dict[str, Any]:
        return self.writer.update_job_status(job_number, status, note)

    def add_job_note(self, job_number: str, note: str) -> dict[str, Any]:
        return self.writer.add_job_note(job_number, note)


def gateway_from_env(env: dict[str, str], audit: Callable[[dict[str, Any]], None] | None = None,
                     transport: Any = None) -> PaceGateway | None:
    """Build the configured gateway, or None when Pace isn't set up (Pace tools stay hidden)."""
    dsn = env.get("PACE_DB_DSN", "").strip()
    api_config_file = env.get("PACE_API_CONFIG", "").strip()
    config = PaceApiWriter.load_config(api_config_file) if api_config_file else None
    writer = PaceApiWriter(
        config, env.get("PACE_API_URL"), env.get("PACE_API_USERNAME"), env.get("PACE_API_PASSWORD"),
        allow_write=env.get("PACE_ALLOW_WRITE", "").strip().lower() in ("1", "true", "yes", "on"),
        allowed_statuses=[s for s in env.get("PACE_ALLOWED_STATUSES", "").split(",") if s.strip()],
        transport=transport, audit=audit,
    )
    if not dsn:
        return PaceWriteOnlyGateway(writer) if writer.configured else None
    queries_file = env.get("PACE_QUERIES_FILE", "").strip()
    if not queries_file:
        raise PaceNotConfigured("PACE_DB_DSN is set but PACE_QUERIES_FILE is not.")
    return PacePostgresGateway(dsn, load_queries(queries_file), writer)
