"""Pace (EFI/ePS Pace MIS) gateway.

Everything that needs Pace goes through ``PaceGateway``, so workflows don't
care where the data comes from:

* ``PacePostgresGateway`` - READ-ONLY queries against the Pace PostgreSQL
  database (use a read-only role, ideally on a replica). The SQL lives in a
  queries file you fill in for your Pace schema (see ``pace_queries.example.sql``);
  each query must return the column names documented there.
* Writes (status updates, notes, estimates) must go through the official Pace
  API, never straight into the database. ``PaceApiWriter`` is the placeholder
  for that; until it is implemented every write reports "manual step" instead.
* ``FakePaceGateway`` - in-memory data for tests and demos.

Claude can also reach Pace through a separate Pace MCP server. The MCP
prompts in ``server.py`` show how to combine the two without this gateway.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from .specs import JobSpec

# Query name -> columns it must return (aliases in your SELECT).
QUERY_CONTRACT: dict[str, list[str]] = {
    "job_spec": ["job_number", "customer", "product", "trim_width_in", "trim_height_in", "pages",
                 "front_inks", "back_inks", "spot_colors", "quantity", "stock", "binding"],
    "job_status": ["job_number", "customer", "status", "due_date", "csr", "description"],
    "similar_jobs": ["job_number", "customer", "description", "trim_width_in", "trim_height_in", "pages",
                     "quantity", "price", "created"],
}


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
    """Placeholder for writes through the official Pace API.

    TODO(pace-team): implement with the Pace web services (or delegate to the Pace MCP):
      - update_job_status: set the job's status / milestone (e.g. "Proof Approved")
      - add_job_note: append a job note visible to CSRs
    Keep writes here, behind an allow-list of statuses, never as SQL against the database.
    """

    def __init__(self, base_url: str | None = None, username: str | None = None, password: str | None = None):
        self.base_url, self.username, self._password = base_url, username, password

    def update_job_status(self, job_number: str, status: str, note: str | None = None) -> dict[str, Any]:
        raise PaceWriteNotImplemented(
            f"Pace write not implemented yet: set job {job_number} status to '{status}' in Pace by hand.")

    def add_job_note(self, job_number: str, note: str) -> dict[str, Any]:
        raise PaceWriteNotImplemented(f"Pace write not implemented yet: add this note to job {job_number} by hand.")


class PacePostgresGateway:
    """Read-only access to the Pace database. Requires ``pip install 'enfocus-switch-mcp[pace]'``."""

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
        with psycopg.connect(self.dsn, row_factory=dict_row, autocommit=False) as conn:
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


def gateway_from_env(env: dict[str, str]) -> PaceGateway | None:
    """Build the configured gateway, or None when Pace isn't set up (Pace tools stay hidden)."""
    dsn = env.get("PACE_DB_DSN", "").strip()
    if not dsn:
        return None
    queries_file = env.get("PACE_QUERIES_FILE", "").strip()
    if not queries_file:
        raise PaceNotConfigured("PACE_DB_DSN is set but PACE_QUERIES_FILE is not.")
    writer = PaceApiWriter(env.get("PACE_API_URL"), env.get("PACE_API_USERNAME"), env.get("PACE_API_PASSWORD"))
    return PacePostgresGateway(dsn, load_queries(queries_file), writer)
