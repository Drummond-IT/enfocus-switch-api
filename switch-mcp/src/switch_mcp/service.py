"""Automation web service: the same logic as the MCP tools, callable by Switch and the web portal.

Run it with ``enfocus-switch-mcp service``. Endpoints (all JSON, all need an API key except /health):

* ``POST /preflight``        (scope ``preflight``)     A2: client self-serve check of an uploaded PDF.
* ``POST /switch/events``    (scope ``switch_events``) Switch "HTTP request" element -> Pace status sync,
                                                       plain-language explanation, optional auto-fix route.
* ``POST /switch/match-job`` (scope ``match_job``)     B4: find the job number in a file name / email.
* ``POST /proof/approve``    (scope ``proof_approve``) A5: route the Switch job + update Pace.
* ``POST /rfq/validate``     (scope ``rfq``)           C2: is a quote request complete? what to ask.
* ``GET  /health``           (no key)                  liveness for monitoring.

Safety defaults: listens on 127.0.0.1; ``SERVICE_DRY_RUN=true`` (plans changes but makes none);
``SERVICE_AUTO_ROUTE=false``; Pace writes also need ``PACE_ALLOW_WRITE`` and a status in
``PACE_ALLOWED_STATUSES``. API keys are stored hashed (``SERVICE_KEYS_FILE``), each with scopes and a
per-minute rate limit. Every request is written to the audit log (``AUDIT_LOG``) without file contents.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import re
import secrets
import time
from collections import deque
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from . import __version__, preflight
from .automation import analytics, autofix, job_matching, rfq, ticket_check, workflows
from .automation.audit import AuditLog
from .automation.customer_rules import CustomerRules, apply_to_analysis, customer_from_job, same_customer
from .automation.pace import PaceError, PaceGateway, PaceWriteNotImplemented, gateway_from_env
from .automation.pdf_facts import analyse_pdf
from .automation.specs import JobSpec, SpecError, parse_size
from .client import SwitchClient, SwitchError, safe_id
from .config import Settings
from .pdf_check import check_pdf

log = logging.getLogger("switch_mcp.service")

SCOPES = ("preflight", "switch_events", "match_job", "proof_approve", "rfq")
MAX_JSON_BYTES = 64 * 1024
PARSE_TIMEOUT = 60
PARALLEL_PARSES = 2
JOB_NUMBER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,39}")
EVENT_NAME = re.compile(r"[a-z0-9_]{1,40}")
STATUS_RULE_KEYS = {"pace_status", "note", "explain", "auto_route"}


class ServiceError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status, self.message = status, message


# ---------------------------------------------------------------- API keys


@dataclass
class ApiKey:
    name: str
    sha256: str
    scopes: list[str]


def hash_key(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def new_key(name: str, scopes: list[str]) -> tuple[str, dict[str, Any]]:
    """A fresh random key and the entry to put in SERVICE_KEYS_FILE (only the hash is stored)."""
    bad = [s for s in scopes if s not in SCOPES]
    if bad or not scopes:
        raise ValueError(f"Scopes must be some of: {', '.join(SCOPES)}.")
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,40}", name):
        raise ValueError("Key names use letters, digits, '.', '-' or '_' (max 40).")
    key = "swk_" + secrets.token_urlsafe(32)
    return key, {name: {"sha256": hash_key(key), "scopes": scopes}}


def load_keys(path: str | Path) -> list[ApiKey]:
    data = json.loads(Path(path).expanduser().read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path} must be a JSON object: key name -> {{sha256, scopes}}.")  # noqa: TRY004
    keys = []
    for name, entry in data.items():
        if name.startswith("_"):
            continue
        if not isinstance(entry, dict) or not re.fullmatch(r"[0-9a-f]{64}", str(entry.get("sha256", ""))):
            raise ValueError(f"Key '{name}' needs a 'sha256' (64 hex characters): use `enfocus-switch-mcp service-key`.")
        scopes = entry.get("scopes")
        if not isinstance(scopes, list) or not scopes or any(s not in SCOPES for s in scopes):
            raise ValueError(f"Key '{name}': scopes must be a non-empty list of {', '.join(SCOPES)}.")
        keys.append(ApiKey(name, entry["sha256"], list(scopes)))
    if not keys:
        raise ValueError(f"{path} has no keys: create one with `enfocus-switch-mcp service-key`.")
    return keys


class RateLimiter:
    """Sliding one-minute window per key name."""

    def __init__(self, per_minute: int):
        self.per_minute = per_minute
        self.hits: dict[str, deque[float]] = {}

    def allow(self, name: str, now: float | None = None) -> bool:
        now = time.monotonic() if now is None else now
        q = self.hits.setdefault(name, deque())
        while q and now - q[0] >= 60:
            q.popleft()
        if len(q) >= self.per_minute:
            return False
        q.append(now)
        return True


# ---------------------------------------------------------------- Switch event -> action map


@dataclass
class EventRule:
    pace_status: str | None = None
    note: str | None = None
    explain: bool = False
    auto_route: bool = False


def load_status_map(path: str | Path | None) -> dict[str, EventRule]:
    """SERVICE_STATUS_MAP: {"events": {"proof_sent": {"pace_status": "Proof Sent", "note": "...", ...}}}."""
    if not path:
        return {}
    data = json.loads(Path(path).expanduser().read_text(encoding="utf-8"))
    events = data.get("events") if isinstance(data, dict) else None
    if not isinstance(events, dict):
        raise ValueError(f"{path} must be an object with an 'events' object.")  # noqa: TRY004 - config error
    out: dict[str, EventRule] = {}
    for name, raw in events.items():
        if name.startswith("_"):
            continue
        if not EVENT_NAME.fullmatch(name):
            raise ValueError(f"{path}: event names use lower-case letters, digits and '_' ({name!r}).")
        if not isinstance(raw, dict) or set(raw) - STATUS_RULE_KEYS:
            raise ValueError(f"{path}: event '{name}' may only set {', '.join(sorted(STATUS_RULE_KEYS))}.")
        out[name] = EventRule(raw.get("pace_status") or None, raw.get("note") or None,
                              bool(raw.get("explain")), bool(raw.get("auto_route")))
    return out


# ---------------------------------------------------------------- helpers


async def read_body(request: Request, limit: int) -> bytes:
    length = request.headers.get("content-length", "")
    if length.isdigit() and int(length) > limit:
        raise ServiceError(413, f"Upload is larger than the limit ({limit // (1024 * 1024) or 1} MB).")
    buf = bytearray()
    async for chunk in request.stream():
        buf += chunk
        if len(buf) > limit:
            raise ServiceError(413, f"Upload is larger than the limit ({limit // (1024 * 1024) or 1} MB).")
    return bytes(buf)


async def read_json(request: Request) -> dict[str, Any]:
    if "json" not in request.headers.get("content-type", ""):
        raise ServiceError(415, "Send JSON (Content-Type: application/json).")
    try:
        data = json.loads(await read_body(request, MAX_JSON_BYTES))
    except ValueError as exc:
        raise ServiceError(400, "Body is not valid JSON.") from exc
    if not isinstance(data, dict):
        raise ServiceError(400, "Body must be a JSON object.")
    return data


def text_field(data: Any, key: str, *, required: bool = False, max_len: int = 200) -> str | None:
    value = data.get(key)
    if value is None or (isinstance(value, str) and not value.strip()):
        if required:
            raise ServiceError(400, f"'{key}' is required.")
        return None
    if not isinstance(value, (str, int)) or isinstance(value, bool):
        raise ServiceError(400, f"'{key}' must be text.")
    value = str(value).strip()
    if len(value) > max_len or any(ord(c) < 32 for c in value):
        raise ServiceError(400, f"'{key}' is too long or has control characters.")
    return value


def job_number_field(data: Any, key: str = "pace_job_number") -> str | None:
    value = text_field(data, key, max_len=40)
    if value is not None and not JOB_NUMBER.fullmatch(value):
        raise ServiceError(400, f"'{key}' may only contain letters, digits, '.', '-' or '_'.")
    return value


def job_id_field(data: Any) -> str | None:
    value = text_field(data, "job_id", max_len=100)
    if value is None:
        return None
    try:
        return safe_id(value, "job_id")
    except SwitchError as exc:
        raise ServiceError(400, str(exc)) from exc


def step(system: str, action: str, detail: str, status: str = "planned", result: str | None = None) -> dict[str, Any]:
    return {"system": system, "action": action, "detail": detail, "status": status, "result": result}


def customer_issue(issue: dict[str, Any]) -> dict[str, Any]:
    return {"title": issue["title"], "severity": issue["severity"], "pages": issue["pages"][:50],
            "what_to_do": issue["explanation"]["customer"]}


# ---------------------------------------------------------------- the app


@dataclass
class ServiceState:
    settings: Settings
    switch: SwitchClient
    pace: PaceGateway | None
    keys: list[ApiKey]
    rules: CustomerRules
    status_map: dict[str, EventRule]
    audit: AuditLog
    limiter: RateLimiter
    parse_slots: asyncio.Semaphore = field(default_factory=lambda: asyncio.Semaphore(PARALLEL_PARSES))

    @property
    def pace_reader(self) -> PaceGateway | None:
        return self.pace if self.pace is not None and getattr(self.pace, "can_read", True) else None

    def authenticate(self, request: Request) -> ApiKey | None:
        auth = request.headers.get("authorization", "")
        token = auth[7:].strip() if auth.lower().startswith("bearer ") else request.headers.get("x-api-key", "")
        if not token:
            return None
        digest = hash_key(token)
        found = None
        for key in self.keys:  # compare against every key so timing doesn't reveal which one matched
            if hmac.compare_digest(digest, key.sha256):
                found = key
        return found

    def record(self, analysis: dict[str, Any], source: str, job_ref: str | None, customer: str | None) -> None:
        if not self.settings.analytics_db:
            return
        try:
            analytics.record(self.settings.analytics_db, analysis, source, job_ref, customer,
                             self.settings.analytics_retention_days)
        except Exception as exc:  # noqa: BLE001 - analytics must never fail a request
            log.warning("Could not record preflight analytics: %s", exc)

    async def parse(self, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        async with self.parse_slots:
            try:
                return await asyncio.wait_for(asyncio.to_thread(fn, *args, **kwargs), timeout=PARSE_TIMEOUT)
            except asyncio.TimeoutError as exc:
                raise ServiceError(422, f"Reading the file took longer than {PARSE_TIMEOUT} seconds.") from exc
            except ServiceError:
                raise
            except Exception as exc:  # pypdf raises many types for damaged files
                raise ServiceError(422, "The file could not be read as a PDF (damaged or not a PDF).") from exc


Handler = Callable[[Request, ApiKey, dict[str, Any]], Awaitable[dict[str, Any]]]


def endpoint(state: ServiceState, scope: str, handler: Handler) -> Callable[[Request], Awaitable[JSONResponse]]:
    async def run(request: Request) -> JSONResponse:
        audit: dict[str, Any] = {"endpoint": request.url.path, "scope": scope}
        key = state.authenticate(request)
        if key is None:
            state.audit(audit | {"key": None, "status": 401})
            return JSONResponse({"error": "Missing or invalid API key."}, 401, headers={"WWW-Authenticate": "Bearer"})
        audit["key"] = key.name
        if scope not in key.scopes:
            state.audit(audit | {"status": 403})
            return JSONResponse({"error": f"This API key may not use {request.url.path}."}, 403)
        if not state.limiter.allow(key.name):
            state.audit(audit | {"status": 429})
            return JSONResponse({"error": "Too many requests; try again in a minute."}, 429,
                                headers={"Retry-After": "60"})
        try:
            body, status = await handler(request, key, audit), 200
        except ServiceError as exc:
            body, status = {"error": exc.message}, exc.status
        except SwitchError as exc:
            body, status = {"error": f"Switch: {exc}"}, 502
        except (httpx.HTTPError, httpx.InvalidURL) as exc:
            body, status = {"error": f"Switch is not reachable ({exc.__class__.__name__})."}, 502
        except Exception:
            log.exception("Unhandled error in %s", request.url.path)
            body, status = {"error": "Internal error; see the service log."}, 500
        state.audit(audit | {"status": status} | ({"error": body["error"][:300]} if "error" in body else {}))
        return JSONResponse(body, status)
    return run


def create_app(settings: Settings, switch: SwitchClient | None = None, pace: PaceGateway | None = None,
               keys: list[ApiKey] | None = None) -> Starlette:
    """Build the service. Raises ValueError/PaceError/OSError for bad configuration (fail at start-up)."""
    audit = AuditLog(settings.audit_log, "service")
    state = ServiceState(
        settings=settings,
        switch=switch or SwitchClient(settings),
        pace=pace if pace is not None else (gateway_from_env(settings.pace_env(), audit=audit)
                                            if settings.pace_enabled else None),
        keys=keys if keys is not None else load_keys(settings.service_keys_file),
        rules=CustomerRules.load(settings.customer_rules or None),
        status_map=load_status_map(settings.service_status_map or None),
        audit=audit,
        limiter=RateLimiter(settings.service_rate_limit_per_min),
    )
    fix_map = autofix.load_autofix_map(settings.autofix_map or None)
    patterns = job_matching.compile_patterns(settings.job_number_patterns or None)
    dry_run = settings.service_dry_run

    # ---------------------------------------------------------- /preflight (A2)

    async def preflight_upload(request: Request, key: ApiKey, audit: dict[str, Any]) -> dict[str, Any]:
        q = request.query_params
        name = Path(q.get("name") or "upload.pdf").name[:120] or "upload.pdf"
        customer = text_field(q, "customer")
        job_number = job_number_field(q, "job_number")
        audit.update(customer=customer, job_number=job_number, file=name)
        try:
            trim = parse_size(q["trim"]) if q.get("trim") else None
            bleed = float(q["bleed_in"]) if q.get("bleed_in") else None
            pages = int(q["pages"]) if q.get("pages") else None
        except (SpecError, ValueError) as exc:
            raise ServiceError(400, f"Ticket details not understood: {exc}") from exc
        if bleed is not None and not 0 <= bleed <= 1:
            raise ServiceError(400, "bleed_in must be between 0 and 1 inch.")
        data = await read_body(request, settings.service_max_upload_mb * 1024 * 1024)
        if b"%PDF-" not in data[:1024]:
            raise ServiceError(415, "Upload a PDF file (the body must be the PDF itself).")
        audit["bytes"] = len(data)

        rule = state.rules.find(customer)
        required_bleed = bleed if bleed is not None else (rule.ticket_defaults.get("bleed_in") if rule else None)
        result = await state.parse(check_pdf, data, trim[0] if trim else None, trim[1] if trim else None,
                                   0.125 if required_bleed is None else required_bleed, name=name)
        parsed = preflight.ParsedReport("quick-check", {"file": name}, [
            preflight.Finding(f["severity"], f["message"], f["pages"]) for f in result["findings"]])
        analysis = apply_to_analysis(preflight.analyze(parsed), rule)
        state.record(analysis, "portal", job_number, customer)
        audit["verdict"] = analysis["verdict"]

        ticket = None
        base: dict[str, Any] = {}
        if job_number and state.pace_reader is not None:
            spec = await asyncio.to_thread(state.pace_reader.get_job_spec, job_number)
            # Only compare against a Pace job that belongs to the customer the portal says it is.
            if spec is not None and same_customer(spec.customer, customer, state.rules):
                base = {k: v for k, v in spec.as_dict().items() if k not in ("colors", "customer")}
            else:
                ticket = {"verdict": "unknown", "issues": [],
                          "message": "We couldn't find that job number for your account; we checked the file only."}
        overrides = {"trim": q.get("trim"), "pages": pages, "colors": q.get("colors"), "bleed_in": bleed}
        if base or any(v is not None for v in overrides.values()):
            try:
                merged = {**({"bleed_in": required_bleed} if required_bleed else {}), **base,
                          **{k: v for k, v in overrides.items() if v is not None}}
                if "trim_in" in merged and "trim" not in merged:
                    merged["trim"] = merged.pop("trim_in")
                spec = JobSpec.from_dict(merged)
            except SpecError as exc:
                raise ServiceError(400, f"Ticket details not understood: {exc}") from exc
            facts = await state.parse(analyse_pdf, data, name)
            compared = ticket_check.compare(facts, spec)
            ticket = {"verdict": compared["verdict"],
                      "issues": [{"severity": i["severity"], "message": i["message"], "pages": i["pages"]}
                                 for i in compared["issues"]]}
        return {
            "file": name,
            "verdict": analysis["verdict"],
            "message": preflight.render(analysis, "customer", name),
            "issues": [customer_issue(i) for i in analysis["issues"] if i["severity"] in ("error", "warning")],
            "ticket": ticket,
            "pages": result["summary"].get("pages"),
            "note": "This is a quick automatic check. Our prepress team runs a full preflight before printing.",
        }

    # ---------------------------------------------------------- /switch/match-job (B4)

    async def match_job(request: Request, key: ApiKey, audit: dict[str, Any]) -> dict[str, Any]:
        data = await read_json(request)
        text = text_field(data, "text", required=True, max_len=job_matching.MAX_TEXT) or ""
        candidates = [{"job_number": c.job_number, "matched_text": c.matched_text}
                      for c in job_matching.find_job_numbers(text, patterns)[:10]]
        if state.pace_reader is not None:
            for c in candidates[:5]:
                c["pace"] = await asyncio.to_thread(state.pace_reader.get_job_status, c["job_number"])
        audit["candidates"] = [c["job_number"] for c in candidates]
        best = candidates[0]["job_number"] if len(candidates) == 1 else None
        return {"job_number": best, "candidates": candidates,
                "ambiguous": len(candidates) > 1, "found": bool(candidates)}

    async def resolve_job(data: dict[str, Any]) -> dict[str, Any]:
        """The Switch job from 'job_id' (Web Services ID) or, failing that, its exact 'job_name'."""
        job_id = job_id_field(data)
        if job_id:
            job = await state.switch.get_job(job_id)
            if not job:
                raise ServiceError(404, f"No Switch job {job_id}.")
            return job
        name = text_field(data, "job_name", max_len=250)
        if not name:
            raise ServiceError(400, "Send 'job_id' (or 'job_name').")
        found = (await state.switch.list_jobs(filter_query={"and": [{"name": {"is": name}}]}, limit=10)).get("data") or []
        waiting = [j for j in found if j.get("status") == "alert"]
        matches = waiting if len(found) > 1 and waiting else found
        if not matches:
            raise ServiceError(404, f"No Switch job named {name!r}.")
        if len(matches) > 1:
            raise ServiceError(409, f"{len(matches)} Switch jobs are named {name!r}; send 'job_id' instead.")
        return matches[0]

    # ---------------------------------------------------------- /proof/approve (A5)

    async def proof_approve(request: Request, key: ApiKey, audit: dict[str, Any]) -> dict[str, Any]:
        data = await read_json(request)
        approved_by = text_field(data, "approved_by", required=True) or ""
        job_id = str((await resolve_job(data))["id"])
        pace_job = job_number_field(data)
        run_dry = dry_run or bool(data.get("dry_run"))
        audit.update(job_id=job_id, approved_by=approved_by, pace_job_number=pace_job, dry_run=run_dry)
        try:
            result = await workflows.approve_proof(
                state.switch, state.pace, job_id, approved_by, pace_job, settings.approve_connection,
                settings.pace_proof_approved_status, dry_run=run_dry)
        except SwitchError as exc:
            if "not waiting at a checkpoint" in str(exc) or str(exc).startswith("No Switch job"):
                raise ServiceError(409, str(exc)) from exc
            raise
        audit["steps"] = [f"{s['system']}:{s['action']}:{s['status']}" for s in result["steps"]]
        return result

    # ---------------------------------------------------------- /switch/events

    async def switch_event(request: Request, key: ApiKey, audit: dict[str, Any]) -> dict[str, Any]:
        data = await read_json(request)
        event = text_field(data, "event", required=True, max_len=40) or ""
        rule = state.status_map.get(event)
        if rule is None:
            known = ", ".join(sorted(state.status_map)) or "none: set SERVICE_STATUS_MAP"
            raise ServiceError(400, f"Unknown event '{event}'. Known events: {known}.")
        job = await resolve_job(data)
        job_id = str(job["id"])
        job_name = str(job.get("name", job_id))
        customer = text_field(data, "customer") or customer_from_job(job)
        pace_job = job_number_field(data)
        if not pace_job:
            found = job_matching.find_job_numbers(job_name, patterns)
            pace_job = found[0].job_number if len(found) == 1 else None
        audit.update(event=event, job_id=job_id, pace_job_number=pace_job, dry_run=dry_run)
        out: dict[str, Any] = {"event": event, "job": job_name, "pace_job_number": pace_job, "customer": customer,
                               "dry_run": dry_run, "steps": []}
        steps: list[dict[str, Any]] = out["steps"]

        analysis = None
        if rule.explain or rule.auto_route:
            try:
                content = await state.switch.download_report(job_id, settings.max_file_mb * 1024 * 1024)
                analysis, _ = await state.parse(preflight.analyze_bytes, content)
            except (SwitchError, ServiceError) as exc:
                steps.append(step("switch", "explain", "Read the preflight report", "skipped",
                                  f"No readable report: {getattr(exc, 'message', exc)}"))
            if analysis is not None:
                analysis = apply_to_analysis(analysis, state.rules.find(customer))
                state.record(analysis, "switch_event", job_name, customer)
                out["explanation"] = {"verdict": analysis["verdict"], "verdict_text": analysis["verdict_text"],
                                      "csr": preflight.render(analysis, "csr", job_name),
                                      "customer": preflight.render(analysis, "customer", job_name)}
                audit["verdict"] = analysis["verdict"]

        if rule.auto_route and analysis is not None:
            plan = autofix.plan_fixes(analysis, fix_map, job.get("outConnections"))
            suggestion = plan.get("suggestion") or {}
            conn_name = suggestion.get("connection")
            conn = next((c for c in job.get("outConnections") or []
                         if str(c.get("name", "")).lower() == str(conn_name or "").lower()), None)
            s = step("switch", "auto_route", f"Route to '{conn_name}'" if conn_name else "Auto-fix route")
            if not settings.service_auto_route:
                s.update(status="skipped", result="SERVICE_AUTO_ROUTE is off.")
            elif suggestion.get("action") != "route" or conn is None or job.get("status") != "alert":
                s.update(status="skipped", result=suggestion.get("reason") or "Nothing to auto-fix.")
            elif dry_run:
                s["result"] = "SERVICE_DRY_RUN is on."
            else:
                await state.switch.route_job(job_id, [str(conn["id"])], [], job.get("updated"))
                s.update(status="done", result="routed")
            steps.append(s)

        if rule.pace_status:
            note = (rule.note or f"Switch: {event.replace('_', ' ')}") + f" (Switch job {job_name})."
            if analysis is not None:
                note += f" Preflight: {analysis['verdict_text']}"
            s = step("pace", "update_status", f"Set Pace job {pace_job or '?'} status to '{rule.pace_status}'")
            if not pace_job:
                s.update(status="skipped", result="No Pace job number: send pace_job_number or put it in the file name.")
            elif state.pace is None:
                s.update(status="manual", result="Pace is not connected: do this step in Pace.")
            elif dry_run:
                s["result"] = "SERVICE_DRY_RUN is on."
            else:
                try:
                    await asyncio.to_thread(state.pace.update_job_status, pace_job, rule.pace_status, note)
                    s.update(status="done", result="ok")
                except PaceWriteNotImplemented as exc:
                    s.update(status="manual", result=str(exc))
                except PaceError as exc:
                    s.update(status="failed", result=str(exc))
            steps.append(s)
        audit["steps"] = [f"{s['system']}:{s['action']}:{s['status']}" for s in steps]
        return out

    # ---------------------------------------------------------- /rfq/validate (C2)

    async def rfq_validate(request: Request, key: ApiKey, audit: dict[str, Any]) -> dict[str, Any]:
        data = await read_json(request)
        fields = {k: v for k, v in data.items() if k in rfq.KNOWN_FIELDS
                  and (isinstance(v, (str, int, float)) and not isinstance(v, bool)) and len(str(v)) <= 500}
        result = rfq.validate_rfq(fields)
        audit.update(ready=result["ready_to_quote"], missing=result["missing"])
        return result

    async def health(request: Request) -> JSONResponse:
        return JSONResponse({"ok": True, "version": __version__})

    @asynccontextmanager
    async def lifespan(_: Starlette) -> AsyncIterator[None]:
        try:
            yield
        finally:
            await state.switch.aclose()

    app = Starlette(routes=[
        Route("/health", health, methods=["GET"]),
        Route("/preflight", endpoint(state, "preflight", preflight_upload), methods=["POST"]),
        Route("/switch/events", endpoint(state, "switch_events", switch_event), methods=["POST"]),
        Route("/switch/match-job", endpoint(state, "match_job", match_job), methods=["POST"]),
        Route("/proof/approve", endpoint(state, "proof_approve", proof_approve), methods=["POST"]),
        Route("/rfq/validate", endpoint(state, "rfq", rfq_validate), methods=["POST"]),
    ], lifespan=lifespan)
    app.state.service = state
    return app


def run(settings: Settings) -> None:  # pragma: no cover - thin wrapper around uvicorn
    import uvicorn

    app = create_app(settings)
    uvicorn.run(app, host=settings.service_host, port=settings.service_port, log_level="info",
                server_header=False, date_header=False, proxy_headers=False,
                ssl_certfile=settings.service_tls_cert or None, ssl_keyfile=settings.service_tls_key or None,
                limit_concurrency=64, timeout_keep_alive=5)
