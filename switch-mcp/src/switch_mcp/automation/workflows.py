"""Multi-system workflows, each with a dry-run plan.

``approve_proof``: a customer (or CSR on their behalf) approves a proof.
    1. Route the Switch job out of its proof checkpoint along the approve connection.
    2. Set the Pace job status (e.g. "Proof Approved").
    3. Add a Pace job note recording who approved and when.

Dry run (the default) returns the plan without changing anything. Steps
that can't be automated yet (Pace writes until ``PaceApiWriter`` exists) come
back as ``manual`` with the exact instruction for a person.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any

from ..client import SwitchClient, SwitchError
from .pace import PaceError, PaceGateway, PaceWriteNotImplemented


@dataclass
class Step:
    system: str        # switch | pace
    action: str
    detail: str
    status: str = "planned"   # planned | done | manual | failed | skipped
    result: str | None = None


async def approve_proof(
    switch: SwitchClient,
    pace: PaceGateway | None,
    switch_job_id: str,
    approved_by: str,
    pace_job_number: str | None = None,
    approve_connection: str = "Approve",
    pace_status: str = "Proof Approved",
    metadata: list[dict[str, str]] | None = None,
    dry_run: bool = True,
) -> dict[str, Any]:
    job = await switch.get_job(switch_job_id)
    if not job:
        raise SwitchError(f"No Switch job {switch_job_id}.")
    options = job.get("outConnections") or []
    conn = next((c for c in options if str(c.get("name", "")).lower() == approve_connection.lower()), None)
    if job.get("status") != "alert" or conn is None:
        names = ", ".join(str(c.get("name")) for c in options) or "none"
        raise SwitchError(f"Job {job.get('name')} is not waiting at a checkpoint with a '{approve_connection}' "
                          f"connection (available: {names}).")

    when = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    note = f"Proof approved by {approved_by} on {when} (Switch job {job.get('name')})."
    steps = [Step("switch", "route", f"Route '{job.get('name')}' from '{job.get('checkpointName')}' "
                                     f"via '{conn.get('name')}'")]
    if pace_job_number:
        steps.append(Step("pace", "update_status", f"Set Pace job {pace_job_number} status to '{pace_status}'"))
        steps.append(Step("pace", "add_note", f"Add note to Pace job {pace_job_number}: {note}"))
    else:
        steps.append(Step("pace", "update_status", "No Pace job number given", status="skipped"))

    if dry_run:
        return {"dry_run": True, "job": job.get("name"), "steps": [asdict(s) for s in steps]}

    try:
        await switch.route_job(switch_job_id, [conn["id"]], metadata or [], job.get("updated"))
        steps[0].status, steps[0].result = "done", "routed"
    except SwitchError as exc:
        steps[0].status, steps[0].result = "failed", str(exc)
        for s in steps[1:]:
            if s.status == "planned":
                s.status, s.result = "skipped", "Switch routing failed; nothing else was changed."
        return {"dry_run": False, "job": job.get("name"), "steps": [asdict(s) for s in steps]}

    for s in steps[1:]:
        if s.status != "planned":
            continue
        if pace is None:
            s.status, s.result = "manual", "Pace is not connected: do this step in Pace."
            continue
        try:
            if s.action == "update_status":
                pace.update_job_status(pace_job_number, pace_status, note)  # type: ignore[arg-type]
            else:
                pace.add_job_note(pace_job_number, note)  # type: ignore[arg-type]
            s.status, s.result = "done", "ok"
        except PaceWriteNotImplemented as exc:
            s.status, s.result = "manual", str(exc)
        except PaceError as exc:
            s.status, s.result = "failed", str(exc)
    return {"dry_run": False, "job": job.get("name"), "steps": [asdict(s) for s in steps]}
