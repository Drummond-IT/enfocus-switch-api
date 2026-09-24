"""MCP server exposing Enfocus Switch to AI assistants.

Tools are grouped by risk:

* read-only tools are always available;
* job-changing tools (submit, route/approve, replace, lock, rush) need
  ``SWITCH_ALLOW_WRITE=true``;
* flow start/stop needs ``SWITCH_ALLOW_FLOW_CONTROL=true``.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import os
import re
import stat
import tempfile
from collections import Counter
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

import httpx
from mcp.server.mcpserver import Image, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

from . import preflight
from .automation import analytics
from .automation import tools as automation_tools
from .automation.audit import AuditLog
from .automation.customer_rules import CustomerRules, apply_to_analysis, customer_from_job
from .automation.pace import PaceError, PaceGateway, gateway_from_env
from .client import SwitchClient, SwitchError
from .config import Settings
from .pdf_check import check_pdf

log = logging.getLogger("switch_mcp")
PARSE_TIMEOUT = 60  # seconds for parsing a report or checking a PDF
READ = ToolAnnotations(read_only_hint=True, open_world_hint=True)
LOCAL_READ = ToolAnnotations(read_only_hint=True, open_world_hint=False)
WRITE = ToolAnnotations(read_only_hint=False, destructive_hint=False, open_world_hint=True)
RISKY = ToolAnnotations(read_only_hint=False, destructive_hint=True, open_world_hint=True)

INSTRUCTIONS = """\
Tools for an Enfocus Switch prepress automation server used by a commercial printer.

Vocabulary: a *flow* is an automated workflow; jobs enter through *submit points* and
wait for a human decision in *checkpoints* (status "alert"). Leaving a checkpoint means
routing the job to one of its outgoing connections (e.g. "Approve" / "Reject").

When explaining preflight results to a customer, use explain_* tools with
audience="customer": no jargon, say what they need to do. For CSRs, say who owns the
fix and whether the due date is at risk. Never route (approve/reject) a job, submit or
replace files unless the user explicitly asked for that specific job.
"""

JOB_SUMMARY_FIELDS = (
    "id", "name", "status", "flowName", "checkpointName", "stage", "state", "userName", "submittedTo",
    "type", "pages", "size", "initiated", "updated", "onAlertSince", "locked", "lockedBy",
    "processingId", "allowReplacing", "allowReportViewing", "hasMetadata", "outConnections", "customFields",
)


def _job_summary(job: dict[str, Any]) -> dict[str, Any]:
    out = {k: job[k] for k in JOB_SUMMARY_FIELDS if k in job and job[k] not in (None, "", [])}
    since = job.get("onAlertSince")
    if job.get("status") == "alert" and since:
        try:
            ts = datetime.fromisoformat(since.replace("Z", "+00:00"))
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            out["waiting_hours"] = round((datetime.now(timezone.utc) - ts).total_seconds() / 3600, 1)
        except ValueError:
            pass
    return out


def _parse_enum(field_type: str) -> list[str] | None:
    if field_type.startswith("enum:"):
        return [v for v in field_type[5:].split(";") if v]
    return None


def _describe_fields(fields: Any) -> list[dict[str, Any]]:
    if isinstance(fields, dict):
        fields = [fields]
    described = []
    for f in fields or []:
        if not f.get("displayField", True):
            continue
        ftype = str(f.get("type", "string"))
        d = {
            "id": f.get("id"), "name": f.get("name"), "type": ftype.split(":")[0],
            "required": bool(f.get("valueIsRequired")),
            "default": None if ftype.startswith("password") else (f.get("value") or None),
            "read_only": bool(f.get("readOnly")),
        }
        if options := _parse_enum(ftype):
            d["options"] = options
        if f.get("description"):
            d["description"] = f["description"]
        if f.get("format"):
            d["format"] = f["format"]
        if f.get("dependency"):
            d["shown_when"] = f"{f['dependency']} {f.get('dependencyCondition', '')} {f.get('dependencyValue', '')}".strip()
        described.append({k: v for k, v in d.items() if v not in (None, "")})
    return described


def _build_metadata(fields: Any, values: dict[str, str] | None) -> list[dict[str, str]]:
    """Map user-supplied {name or id: value} onto a Switch metadata definition and validate it."""
    if isinstance(fields, dict):
        fields = [fields]
    fields = fields or []
    values = dict(values or {})
    by_key = {}
    fields = [f for f in fields if isinstance(f, dict) and f.get("id")]
    for f in fields:
        f.setdefault("name", f["id"])
        by_key[str(f["id"]).lower()] = f
        by_key[str(f["name"]).lower()] = f
    unknown = [k for k in values if k.lower() not in by_key]
    if unknown:
        known = ", ".join(str(f.get("name")) for f in fields) or "none"
        raise ToolError(f"Unknown metadata field(s): {', '.join(unknown)}. Available: {known}.")
    supplied = {by_key[k.lower()]["id"]: v for k, v in values.items()}
    result = []
    problems = []
    for f in fields:
        value = supplied.get(f["id"], f.get("value") or "")
        options = _parse_enum(str(f.get("type", "")))
        if f.get("valueIsRequired") and f.get("displayField", True) and value in ("", None) and not f.get("dependency"):
            problems.append(f"'{f['name']}' is required")
        if options and value and str(value) not in options:
            problems.append(f"'{f['name']}' must be one of {options}")
        if f.get("format") and value:
            try:
                if not re.fullmatch(f["format"], str(value)):
                    problems.append(f"'{f['name']}' must match {f['format']}")
            except re.error:
                pass  # Switch's pattern syntax Python can't compile; leave the check to Switch
        if f["id"] in supplied or value:
            result.append({"id": f["id"], "name": f["name"], "value": str(value)})
    if problems:
        raise ToolError("Metadata is not valid: " + "; ".join(problems) + ".")
    return result


def build_server(
    settings: Settings | None = None,
    client: SwitchClient | None = None,
    pace: PaceGateway | None = None,
) -> MCPServer:
    settings = settings or Settings.load()
    switch = client or SwitchClient(settings)
    config_errors, _ = settings.validate()
    try:
        customer_rules = CustomerRules.load(settings.customer_rules or None)
    except (OSError, ValueError) as exc:
        customer_rules = CustomerRules([])
        config_errors.append(f"CUSTOMER_RULES: {exc}")
    if pace is None and settings.pace_enabled and not config_errors:
        try:
            pace = gateway_from_env(settings.pace_env(), audit=AuditLog(settings.audit_log, "mcp"))
        except (PaceError, OSError) as exc:
            config_errors.append(f"Pace: {exc}")
    max_bytes = settings.max_file_mb * 1024 * 1024

    @asynccontextmanager
    async def lifespan(_: MCPServer) -> AsyncIterator[None]:
        try:
            yield
        finally:
            await switch.aclose()

    mcp = MCPServer("enfocus-switch", instructions=INSTRUCTIONS, lifespan=lifespan)

    def _inside(p: Path, allowed: list[Path]) -> bool:
        return any(p == d or d in p.parents for d in allowed)

    def read_local(path: str, *, for_upload: bool = False) -> tuple[Path, bytes, float]:
        """Read a file the user allowed; returns (resolved path, content, mtime).

        The file is opened once, without following a final symlink, so it can't be swapped
        between the folder check and the read.
        """
        allowed = list(settings.upload_dirs) + ([] if for_upload else [settings.download_dir])
        if not allowed:
            raise ToolError("No local folders are enabled. Set SWITCH_UPLOAD_DIRS to allow file access.")
        raw = str(path).strip()
        # Network paths are refused before touching the filesystem: on Windows even resolving
        # \\host\share makes an SMB connection that can leak the user's login hash.
        # (Any two leading slashes of either kind, which also covers \\?\UNC\ and //?/ forms.)
        if (len(raw) >= 2 and raw[0] in "\\/" and raw[1] in "\\/") or "\x00" in raw:
            raise ToolError("Network paths (\\\\server\\share or //server/share) are not allowed.")
        p = Path(raw).expanduser().resolve()
        if not _inside(p, allowed):
            raise ToolError(f"{p} is outside the allowed folders: {', '.join(map(str, allowed))}.")
        if p.is_dir():  # Windows can't open a folder at all ("Permission denied"); say what it is
            raise ToolError(f"Not a regular file: {p}")
        try:
            fd = os.open(p, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0))
        except FileNotFoundError as exc:
            raise ToolError(f"File not found: {p}") from exc
        except OSError as exc:
            raise ToolError(f"Can't open {p}: {exc.strerror}") from exc
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            os.close(fd)
            raise ToolError(f"Not a regular file: {p}")
        with os.fdopen(fd, "rb") as fh:
            if st.st_size > max_bytes:
                raise ToolError(f"{p.name} is larger than SWITCH_MAX_FILE_MB ({settings.max_file_mb} MB).")
            data = fh.read(max_bytes + 1)
        if len(data) > max_bytes:
            raise ToolError(f"{p.name} is larger than SWITCH_MAX_FILE_MB ({settings.max_file_mb} MB).")
        return p, data, st.st_mtime

    job_customer = customer_from_job

    def record_analysis(analysis: dict[str, Any], source: str, job_ref: str | None = None,
                        customer: str | None = None) -> None:
        """Record a verdict for preflight analytics (ANALYTICS_DB). Never fails the calling tool."""
        if not settings.analytics_db:
            return
        try:
            analytics.record(settings.analytics_db, analysis, source, job_ref, customer,
                             settings.analytics_retention_days)
        except Exception as exc:  # noqa: BLE001 - analytics must never break a CSR's request
            log.warning("Could not record preflight analytics: %s", exc)

    def save_download(name: str, data: bytes) -> Path:
        """Write into the download folder via a temp file + rename (never through a planted symlink)."""
        settings.download_dir.mkdir(parents=True, exist_ok=True)
        target = settings.download_dir / name
        fd, tmp = tempfile.mkstemp(dir=settings.download_dir, prefix=".download-", suffix=".part")
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(data)
            os.replace(tmp, target)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)
        return target

    async def call(coro: Any) -> Any:
        if config_errors:
            coro.close()
            raise ToolError(
                "The Switch connector is not configured correctly: " + " ".join(config_errors)
                + " Run `enfocus-switch-mcp check` for details."
            )
        try:
            return await coro
        except SwitchError as exc:
            raise ToolError(f"Switch: {exc}") from exc
        except (httpx.HTTPError, httpx.InvalidURL) as exc:
            raise ToolError(f"Can't reach Switch at {settings.url}: {exc.__class__.__name__}: {exc}") from exc
        except OSError as exc:
            raise ToolError(f"File error: {exc.strerror or exc}") from exc
        except (ValueError, re.error) as exc:
            raise ToolError(f"Unexpected data from Switch: {exc}") from exc

    async def require_job(job_id: str) -> dict[str, Any]:
        job = await call(switch.get_job(job_id))
        if not job:
            raise ToolError(f"No job with id {job_id} (or you don't have access to it).")
        return job

    async def find_submit_point(submit_point: str) -> dict[str, Any]:
        points = await call(switch.list_submit_points())
        needle = submit_point.strip().lower()
        for sp in points:
            if needle in (f"{sp.get('flowId')}-{sp.get('objectId')}", str(sp.get("name", "")).lower()):
                return sp
        matches = [sp for sp in points if needle in str(sp.get("name", "")).lower()]
        if len(matches) == 1:
            return matches[0]
        names = ", ".join(f"{sp.get('name')} ({sp.get('flowId')}-{sp.get('objectId')})" for sp in points)
        raise ToolError(f"Submit point '{submit_point}' not found or ambiguous. Available: {names}")

    def report_to_analysis(content: bytes, content_type: str) -> tuple[dict[str, Any], str]:
        try:
            return preflight.analyze_bytes(content, content_type)
        except ValueError as exc:
            raise ToolError(str(exc)) from exc

    async def analyze_report(content: bytes) -> tuple[dict[str, Any], str]:
        """Parse off the event loop, with a time limit, so a hostile report can't stall other tools."""
        try:
            return await asyncio.wait_for(asyncio.to_thread(report_to_analysis, content, ""), timeout=PARSE_TIMEOUT)
        except asyncio.TimeoutError as exc:
            raise ToolError(f"Reading the report took longer than {PARSE_TIMEOUT} seconds; gave up.") from exc

    # ------------------------------------------------------------- status

    @mcp.tool(annotations=READ)
    async def switch_status() -> dict[str, Any]:
        """Check that the Switch server is reachable and show what this connector is allowed to do."""
        login = await call(switch.ensure_login())
        await call(switch.ping())
        return {
            "server": settings.url,
            "user": login.get("user"),
            "permissions": {k: v for k, v in login.items() if k.endswith("Access")},
            "connector": {
                "write_tools_enabled": settings.allow_write,
                "flow_control_enabled": settings.allow_flow_control,
                "upload_folders": [str(p) for p in settings.upload_dirs],
                "download_folder": str(settings.download_dir),
            },
        }

    @mcp.tool(annotations=READ)
    async def production_snapshot() -> dict[str, Any]:
        """One-glance overview: jobs per status and flow, and the jobs waiting longest for a decision."""
        data = await call(switch.list_jobs(
            fields=["id", "name", "status", "flowName", "checkpointName", "onAlertSince", "userName"],
            limit=5000,
        ))
        jobs = data.get("data") or []
        alerts = sorted((j for j in jobs if j.get("status") == "alert"), key=lambda j: j.get("onAlertSince") or "")
        return {
            "total_jobs": len(jobs),
            "by_status": dict(Counter(j.get("status") for j in jobs)),
            "by_flow": dict(Counter(j.get("flowName") for j in jobs).most_common(20)),
            "waiting_in_checkpoints": dict(Counter(j.get("checkpointName") for j in alerts)),
            "oldest_waiting": [_job_summary(j) for j in alerts[:10]],
        }

    # -------------------------------------------------------------- flows

    @mcp.tool(annotations=READ)
    async def list_flows(name_contains: str | None = None, status: str | None = None) -> list[dict[str, Any]]:
        """List Switch flows (workflows) with their status, groups and stages.

        status: running, stopped, scheduled or removed.
        """
        flows = await call(switch.list_flows(fields=["id", "name", "status", "groups", "stages", "customJobFields"]))
        if name_contains:
            flows = [f for f in flows if name_contains.lower() in str(f.get("name", "")).lower()]
        if status:
            flows = [f for f in flows if str(f.get("status", "")).lower() == status.lower()]
        return flows

    # ------------------------------------------------------ submit points

    @mcp.tool(annotations=READ)
    async def list_submit_points(name_contains: str | None = None) -> list[dict[str, Any]]:
        """List the submit points (job entry points) and the metadata fields each one asks for."""
        points = await call(switch.list_submit_points())
        out = []
        for sp in points:
            if name_contains and name_contains.lower() not in str(sp.get("name", "")).lower():
                continue
            out.append({
                "submit_point": f"{sp.get('flowId')}-{sp.get('objectId')}",
                "name": sp.get("name"),
                "flow": sp.get("flowName"),
                "flow_state": sp.get("flowState"),
                "description": sp.get("description"),
                "accepts": sp.get("accept"),
                "file_types": sp.get("acceptFileTypes"),
                "job_required": sp.get("jobRequired", True),
                "metadata_fields": _describe_fields(sp.get("metadata")),
            })
        return out

    # --------------------------------------------------------------- jobs

    @mcp.tool(annotations=READ)
    async def find_jobs(
        name_contains: str | None = None,
        status: Literal["processing", "alert", "completed", "error"] | None = None,
        flow_name: str | None = None,
        checkpoint_name: str | None = None,
        user_name: str | None = None,
        updated_within_days: int | None = None,
        limit: int = 50,
    ) -> dict[str, Any]:
        """Search jobs. status "alert" means the job is waiting in a checkpoint for a person.

        Use name_contains with an order or customer number to find a customer's job.
        """
        conditions: list[dict[str, Any]] = []
        if name_contains:
            conditions.append({"name": {"contains": name_contains}})
        if status:
            conditions.append({"status": {"is": status}})
        if flow_name:
            conditions.append({"flowName": {"contains": flow_name}})
        if checkpoint_name:
            conditions.append({"checkpointName": {"contains": checkpoint_name}})
        if user_name:
            conditions.append({"userName": {"is": user_name}})
        if updated_within_days:
            conditions.append({"updated": {"is_in_the_last": f"{updated_within_days};days"}})
        data = await call(switch.list_jobs(
            filter_query={"and": conditions} if conditions else None,
            sort="-updated",
            limit=max(1, min(limit, 500)),
        ))
        jobs = data.get("data") or []
        return {"count": len(jobs), "jobs": [_job_summary(j) for j in jobs]}

    @mcp.tool(annotations=READ)
    async def jobs_needing_attention(flow_name: str | None = None, limit: int = 50) -> dict[str, Any]:
        """Jobs waiting in checkpoints for a human decision, longest-waiting first, with their routing options."""
        conditions: list[dict[str, Any]] = [{"status": {"is": "alert"}}]
        if flow_name:
            conditions.append({"flowName": {"contains": flow_name}})
        data = await call(switch.list_jobs(filter_query={"and": conditions}, sort="onAlertSince",
                                           limit=max(1, min(limit, 500))))
        jobs = [_job_summary(j) for j in data.get("data") or []]
        return {"count": len(jobs), "jobs": jobs}

    @mcp.tool(annotations=READ)
    async def get_job(job_id: str) -> dict[str, Any]:
        """Full details for one job, including checkpoint routing options and editable metadata fields."""
        job = await require_job(job_id)
        result = _job_summary(job)
        result["raw"] = job
        if job.get("hasMetadata"):
            metadata = await call(switch.job_metadata([job_id]))
            result["checkpoint_fields"] = _describe_fields(metadata.get(job_id, []))
        return result

    @mcp.tool(annotations=READ)
    async def get_job_thumbnail(job_id: str) -> Image:
        """Preview image of a job (first page)."""
        thumbs = await call(switch.thumbnails([job_id]))
        if not thumbs or not thumbs[0].get("thumbnail"):
            raise ToolError("Switch has no thumbnail for this job.")
        data = base64.b64decode(thumbs[0]["thumbnail"])
        return Image(data=data, format="png" if data[:4] == b"\x89PNG" else "jpeg")

    @mcp.tool(annotations=READ)
    async def download_job(job_id: str) -> dict[str, Any]:
        """Download a job's file (or zipped job folder) into the connector's download folder."""
        job = await require_job(job_id)
        name = re.sub(r"[^\w.\- ]", "_", Path(job.get("name") or job_id).name).strip(". ")[:150] or "job"
        if not job.get("isFile", True) and not name.endswith(".zip"):
            name += ".zip"
        settings.download_dir.mkdir(parents=True, exist_ok=True)
        target = settings.download_dir / f"{job_id}_{name}"
        size = await call(switch.download_job_to(job_id, target, max_bytes))
        return {"saved_to": str(target), "bytes": size}

    # ---------------------------------------------------------- preflight

    @mcp.tool(annotations=READ)
    async def explain_job_report(
        job_id: str, audience: Literal["customer", "csr", "prepress"] = "customer", customer: str | None = None,
    ) -> dict[str, Any]:
        """Fetch the preflight report attached to a job in a checkpoint and explain it in plain language.

        Returns a verdict (ready / prepress_can_fix / needs_customer), grouped issues with pages,
        who owns each fix, and a ready-to-send write-up for the chosen audience. The customer's
        standing agreements (CUSTOMER_RULES) are applied; the customer comes from `customer` or a
        "Customer" custom field on the Switch job.
        """
        job = await require_job(job_id)
        content = await call(switch.download_report(job_id, max_bytes))
        analysis, fmt = await analyze_report(content)
        customer = customer or job_customer(job)
        analysis = apply_to_analysis(analysis, customer_rules.find(customer))
        analysis["customer"] = customer
        record_analysis(analysis, source="switch_job", job_ref=job.get("name"), customer=customer)
        ext = ".pdf" if fmt == "pdf-text" else ".json" if fmt == "pitstop-json" else \
            ".xml" if fmt.startswith("pitstop") else ".txt"
        saved = save_download(f"{job_id}_report{ext}", content)
        analysis["report_saved_to"] = str(saved)
        if fmt == "pdf-text":
            analysis["note"] = ("Report was a PDF; findings were read from its text and may be less precise. "
                                "Configure the preflight step to also produce an XML report for best results.")
        analysis["write_up"] = preflight.render(analysis, audience, job.get("name"))
        return analysis

    @mcp.tool(annotations=LOCAL_READ)
    async def explain_preflight_report(
        report: str | None = None,
        report_file: str | None = None,
        audience: Literal["customer", "csr", "prepress"] = "customer",
        job_name: str | None = None,
        customer: str | None = None,
    ) -> dict[str, Any]:
        """Explain a preflight report in plain language without needing Switch.

        Pass either `report` (PitStop XML, or pasted report text with one finding per line) or
        `report_file` (a .xml/.txt/.pdf report inside an allowed folder).
        """
        if report_file:
            _, content, _ = read_local(report_file)
            analysis, _ = await analyze_report(content)
        elif report:
            analysis, _ = await analyze_report(report.encode("utf-8"))
        else:
            raise ToolError("Provide report text or report_file.")
        analysis = apply_to_analysis(analysis, customer_rules.find(customer))
        record_analysis(analysis, source="tool", job_ref=job_name, customer=customer)
        analysis["write_up"] = preflight.render(analysis, audience, job_name)
        return analysis

    @mcp.tool(annotations=LOCAL_READ)
    async def quick_check_pdf(
        file_path: str,
        trim_width_in: float | None = None,
        trim_height_in: float | None = None,
        required_bleed_in: float = 0.125,
        audience: Literal["customer", "csr", "prepress"] = "csr",
    ) -> dict[str, Any]:
        """Fast local sanity check of a customer PDF (size, bleed, fonts, RGB images, security).

        Give the ordered trim size in inches to catch wrong-size files. This is an intake check,
        not a full PitStop preflight.
        """
        path, data, _ = read_local(file_path)
        try:
            result = await asyncio.wait_for(
                asyncio.to_thread(check_pdf, data, trim_width_in, trim_height_in, required_bleed_in, name=path.name),
                timeout=PARSE_TIMEOUT,
            )
        except asyncio.TimeoutError as exc:
            raise ToolError(f"Checking {path.name} took longer than {PARSE_TIMEOUT} seconds; gave up.") from exc
        except Exception as exc:  # pypdf raises many types for damaged files
            raise ToolError(f"Could not read {path.name} as a PDF: {exc}") from exc
        parsed = preflight.ParsedReport(
            "quick-check",
            {"file": result["summary"]["file"]},
            [preflight.Finding(f["severity"], f["message"], f["pages"]) for f in result["findings"]],
        )
        analysis = preflight.analyze(parsed)
        result["verdict"] = analysis["verdict"]
        result["write_up"] = preflight.render(analysis, audience)
        return result

    # ------------------------------------------------------------ messages

    @mcp.tool(annotations=READ)
    async def recent_messages(
        hours: int = 24,
        type: Literal["error", "warning", "info", "debug", "assert"] | None = None,
        flow: str | None = None,
        job: str | None = None,
        text_contains: str | None = None,
        limit: int = 200,
    ) -> dict[str, Any]:
        """Read the Switch message log (errors, warnings, info) for troubleshooting."""
        data = await call(switch.messages(
            period=f"{max(1, hours)}h", type=type, flow=flow, job=job, message=text_contains,
            limit=max(1, min(limit, 2000)), sort="-timestamp",
        ))
        return {"count": len(data.get("messages", [])), "messages": data.get("messages", [])}

    @mcp.tool(annotations=READ)
    async def problem_summary(hours: int = 24) -> dict[str, Any]:
        """Group recent errors and warnings by flow and element to spot what is breaking most."""
        data = await call(switch.messages(period=f"{max(1, hours)}h", type="error", limit=5000))
        errors = data.get("messages", [])
        warn = await call(switch.messages(period=f"{max(1, hours)}h", type="warning", limit=5000))
        warnings = warn.get("messages", [])

        def top(msgs: list[dict]) -> list[dict[str, Any]]:
            counter = Counter((m.get("flow"), m.get("element"), re.sub(r"\d+", "#", m.get("message", ""))[:160])
                              for m in msgs)
            return [{"flow": f, "element": e, "message_pattern": msg, "count": c}
                    for (f, e, msg), c in counter.most_common(15)]

        return {
            "hours": hours,
            "error_count": len(errors),
            "warning_count": len(warnings),
            "top_errors": top(errors),
            "top_warnings": top(warnings),
            "jobs_with_errors": sorted({m.get("job") for m in errors if m.get("job")})[:50],
        }

    @mcp.tool(annotations=READ)
    async def graphql_query(query: str) -> dict[str, Any]:
        """Run a read-only Switch GraphQL query (processing jobs; statistics if Reporting is licensed).

        Example: { jobs(filter: "") { count jobs { processingId name flowName stage state } } }
        """
        if re.search(r"\bmutation\b", query, re.IGNORECASE):
            raise ToolError("Only queries are allowed.")
        return await call(switch.graphql(query))

    # ----------------------------------------------------------- write ops

    if settings.allow_write:

        @mcp.tool(annotations=WRITE)
        async def submit_job(
            submit_point: str,
            file_path: str,
            job_name: str | None = None,
            metadata: dict[str, str] | None = None,
        ) -> dict[str, Any]:
            """Submit a local file to a submit point (by name or "flowId-objectId").

            metadata maps field name (or id) to value; see list_submit_points for the fields.
            """
            sp = await find_submit_point(submit_point)
            path, data, mtime = read_local(file_path, for_upload=True)
            accepted = [t.lower().lstrip(".") for t in (sp.get("acceptFileTypes") or [])]
            if accepted and path.suffix.lower().lstrip(".") not in accepted:
                raise ToolError(f"{sp.get('name')} only accepts: {', '.join(accepted)}")
            md = _build_metadata(sp.get("metadata"), metadata)
            result = await call(switch.submit_job(sp["flowId"], sp["objectId"], path.name, data, job_name, md, mtime))
            return {"job_id": result.get("jobId"), "submitted_to": sp.get("name"), "flow": sp.get("flowName")}

        @mcp.tool(annotations=RISKY)
        async def route_job(
            job_id: str,
            connection: str,
            metadata: dict[str, str] | None = None,
        ) -> dict[str, Any]:
            """Move a job out of its checkpoint along a named connection (e.g. approve or reject a proof).

            connection: connection name (e.g. "Approve") or id, from get_job's outConnections.
            Only do this when the user explicitly asked to route this specific job.
            """
            job = await require_job(job_id)
            options = job.get("outConnections") or []
            if job.get("status") != "alert" or not options:
                raise ToolError("This job is not waiting in a checkpoint.")
            chosen = [c for c in options if connection.lower() in (str(c.get("id")).lower(), str(c.get("name", "")).lower())]
            if not chosen:
                chosen = [c for c in options if connection.lower() in str(c.get("name", "")).lower()]
            if len(chosen) != 1:
                raise ToolError(f"Connection '{connection}' not found or ambiguous. Options: "
                                + ", ".join(str(c.get("name")) for c in options))
            md: list[dict[str, str]] = []
            if metadata or job.get("hasMetadata"):
                definition = (await call(switch.job_metadata([job_id]))).get(job_id, [])
                md = _build_metadata(definition, metadata)
            await call(switch.route_job(job_id, [chosen[0]["id"]], md, job.get("updated")))
            return {"job": job.get("name"), "routed_to": chosen[0].get("name")}

        @mcp.tool(annotations=RISKY)
        async def replace_job(job_id: str, file_path: str) -> dict[str, Any]:
            """Replace the file of a job waiting in a checkpoint (e.g. with a corrected PDF from the customer)."""
            job = await require_job(job_id)
            if not job.get("allowReplacing"):
                raise ToolError("This checkpoint does not allow replacing the job.")
            path, data, _ = read_local(file_path, for_upload=True)
            await call(switch.replace_job(job_id, path.name, data, job.get("updated")))
            return {"job": job.get("name"), "replaced_with": path.name}

        @mcp.tool(annotations=WRITE)
        async def set_job_lock(job_id: str, locked: bool = True) -> dict[str, Any]:
            """Lock a checkpoint job so others can't route it while you're working on it (or unlock it)."""
            await call(switch.set_job_lock(job_id, locked))
            return {"job_id": job_id, "locked": locked}

        @mcp.tool(annotations=WRITE)
        async def rush_job(job_id: str, rush: bool = True) -> dict[str, Any]:
            """Give a job top processing priority (or remove it). Needs the Switch 'Rush jobs' permission."""
            job = await require_job(job_id)
            if not job.get("processingId"):
                raise ToolError("Job has no processing id yet; it can't be rushed.")
            await call(switch.set_rush(job["processingId"], rush))
            return {"job": job.get("name"), "rushed": rush}

    if settings.allow_flow_control:

        @mcp.tool(annotations=RISKY)
        async def set_flow_running(flow_id: str, running: bool) -> dict[str, Any]:
            """Start or stop a flow. Stopping a flow halts production on it."""
            result = await call(switch.set_flow_state(flow_id, "start" if running else "stop"))
            return {"flow_id": flow_id, "flow_status": result.get("flowStatus", "running" if running else "stopped")}

    automation_tools.register(mcp, automation_tools.Context(
        settings=settings, switch=switch, pace=pace, call=call, read_local=read_local,
        require_job=require_job, analyze_report=analyze_report, customer_rules=customer_rules,
    ))

    # ------------------------------------------------------------- prompts

    @mcp.prompt(title="Customer email about preflight results")
    def customer_preflight_email(job_id: str, customer_name: str = "") -> str:
        return (
            f"Use explain_job_report with job_id={job_id} and audience='customer'. Then draft a short, "
            f"friendly email{f' to {customer_name}' if customer_name else ''} that: says whether the file is "
            "ready, lists only the issues that need their action (with page numbers), explains each in one "
            "or two plain sentences, and says exactly what to send back. Mention routine fixes we made in one "
            "line. No jargon (no 'TAC', 'ppi', 'TrimBox'); keep it under 200 words."
        )

    @mcp.prompt(title="CSR: where is this order?")
    def csr_order_status(order_or_customer: str) -> str:
        return (
            f"Find jobs matching '{order_or_customer}' with find_jobs (name_contains). For each, say in one "
            "line where it is (flow, checkpoint or stage), how long it has been waiting, and whether anyone "
            "needs to act. If a job is waiting in a checkpoint with a report, summarize it with "
            "explain_job_report audience='csr'. Finish with what the CSR should tell the customer."
        )

    @mcp.prompt(title="Morning prepress triage")
    def prepress_triage(hours: int = 16) -> str:
        return (
            "Build a morning triage for the prepress team: 1) production_snapshot, 2) jobs_needing_attention "
            f"(oldest first), 3) problem_summary for the last {hours} hours. Output: a table of waiting jobs "
            "with a recommended next action each (auto-fixable by prepress / needs customer / ready to "
            "approve), then the top recurring errors with a likely cause and who should look at it."
        )

    return mcp


