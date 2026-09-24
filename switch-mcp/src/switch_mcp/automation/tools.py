"""MCP tools and prompts for the automations. Registered by ``server.build_server``."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

from ..client import SwitchClient
from ..config import Settings
from . import analytics, autofix, digest, estimate_draft, job_matching, rfq, ticket_check, workflows
from .customer_rules import CustomerRules, spec_defaults
from .pace import PaceError, PaceGateway
from .pdf_facts import PdfFacts, analyse_pdf
from .specs import JobSpec, SpecError

READ = ToolAnnotations(read_only_hint=True, open_world_hint=True)
LOCAL_READ = ToolAnnotations(read_only_hint=True, open_world_hint=False)
RISKY = ToolAnnotations(read_only_hint=False, destructive_hint=True, open_world_hint=True)
PARSE_TIMEOUT = 60


@dataclass
class Context:
    settings: Settings
    switch: SwitchClient
    pace: PaceGateway | None
    call: Callable[[Any], Awaitable[Any]]
    read_local: Callable[..., tuple[Path, bytes, float]]
    require_job: Callable[[str], Awaitable[dict[str, Any]]]
    analyze_report: Callable[[bytes], Awaitable[tuple[dict[str, Any], str]]]
    customer_rules: CustomerRules = field(default_factory=lambda: CustomerRules([]))


def register(mcp: MCPServer, ctx: Context) -> None:
    settings = ctx.settings
    # Pace reads need the database; a write-only (API) gateway can't look jobs up.
    pace_reader = ctx.pace if ctx.pace is not None and getattr(ctx.pace, "can_read", True) else None

    async def facts_for(file_path: str) -> PdfFacts:
        path, data, _ = ctx.read_local(file_path)
        try:
            return await asyncio.wait_for(asyncio.to_thread(analyse_pdf, data, path.name), timeout=PARSE_TIMEOUT)
        except asyncio.TimeoutError as exc:
            raise ToolError(f"Analysing {path.name} took longer than {PARSE_TIMEOUT} seconds; gave up.") from exc
        except Exception as exc:  # pypdf raises many types for damaged files
            raise ToolError(f"Could not read {path.name} as a PDF: {exc}") from exc

    async def pace_call(fn: Callable[..., Any], *args: Any) -> Any:
        """Run a (blocking) Pace gateway call in a worker thread."""
        try:
            return await asyncio.to_thread(fn, *args)
        except PaceError as exc:
            raise ToolError(f"Pace: {exc}") from exc
        except Exception as exc:  # database driver errors
            raise ToolError(f"Pace database error: {exc.__class__.__name__}: {exc}") from exc

    # ---------------------------------------------------------- file vs ticket

    @mcp.tool(annotations=READ if pace_reader else LOCAL_READ)
    async def compare_file_to_ticket(
        file_path: str,
        pace_job_number: str | None = None,
        trim: str | None = None,
        pages: int | None = None,
        colors: str | None = None,
        spot_colors: str | None = None,
        bleed_in: float | None = None,
        binding: str | None = None,
        product: str | None = None,
        customer: str | None = None,
    ) -> dict[str, Any]:
        """Check a customer PDF against what was ordered: size, page count, sides, inks, spot colors, bleed.

        The ticket comes from Pace (pace_job_number, when Pace is connected) and/or the arguments,
        which override Pace values: trim "8.5 x 11" or "85 x 55 mm", colors "4/4", "4/0", "1/1",
        "4/4 + PMS 185 C". The customer's standing agreements (e.g. their bleed) apply as defaults. Returns a verdict (matches_ticket / check_with_customer / does_not_match)
        and plain-language issues a CSR can pass on.
        """
        base: dict[str, Any] = {}
        if pace_job_number:
            if pace_reader is None:
                raise ToolError("Pace database isn't connected; pass the ticket details as arguments instead "
                                "(or look them up with the Pace MCP first).")
            spec = await pace_call(pace_reader.get_job_spec, pace_job_number)
            if spec is None:
                raise ToolError(f"No Pace job {pace_job_number}.")
            base = spec.as_dict()
            base.pop("colors", None)
            if colors is not None:  # an explicit colors argument replaces Pace's inks entirely
                for key in ("front_inks", "back_inks", "spot_colors"):
                    base.pop(key, None)
        overrides = {"trim": trim, "pages": pages, "colors": colors, "spot_colors": spot_colors,
                     "bleed_in": bleed_in, "binding": binding, "product": product}
        try:
            customer = customer or base.get("customer")
            merged = {**spec_defaults(ctx.customer_rules.find(customer)), **base,
                      **{k: v for k, v in overrides.items() if v is not None}}
            if "trim_in" in merged and "trim" not in merged:
                merged["trim"] = merged.pop("trim_in")
            spec = JobSpec.from_dict(merged)
        except SpecError as exc:
            raise ToolError(str(exc)) from exc
        if not any([spec.trim_in, spec.pages, spec.front_inks is not None, spec.spot_colors]):
            raise ToolError("Give at least one ticket detail (trim, pages, colors, spot_colors) or a Pace job number.")
        return ticket_check.compare(await facts_for(file_path), spec)

    @mcp.tool(annotations=LOCAL_READ)
    async def pdf_facts(file_path: str) -> dict[str, Any]:
        """What a PDF contains, page by page: trim size, bleed, process inks, RGB, spot colors."""
        return (await facts_for(file_path)).as_dict()

    # ---------------------------------------------------------- estimating

    @mcp.tool(annotations=LOCAL_READ)
    async def draft_item_from_pdf(file_path: str, item_template: str | None = None) -> dict[str, Any]:
        """Draft an estimate item from a print file: product guess, size, pages, sides, inks, bleed.

        Also returns a Pace item-template payload (field names from PACE_ITEM_TEMPLATE_MAP) for an
        estimator to review. Nothing is priced or sent to Pace. Quantity, stock and finishing still
        have to come from the customer.
        """
        draft = estimate_draft.draft_from_facts(await facts_for(file_path))
        try:
            field_map = estimate_draft.load_field_map(settings.pace_item_template_map or None)
        except (OSError, ValueError) as exc:
            raise ToolError(f"PACE_ITEM_TEMPLATE_MAP: {exc}") from exc
        result: dict[str, Any] = {
            "draft": draft.as_dict(),
            "pace_item_template": estimate_draft.to_item_template(draft, field_map, item_template),
            "field_map_is_placeholder": not settings.pace_item_template_map,
        }
        if pace_reader:
            spec = JobSpec(trim_in=draft.trim_in, pages=draft.pages)
            result["similar_pace_jobs"] = await pace_call(pace_reader.find_similar_jobs, spec, 5)
        return result

    @mcp.tool(annotations=LOCAL_READ)
    async def validate_job_spec(
        product: str | None = None,
        quantity: str | None = None,
        trim: str | None = None,
        pages: int | None = None,
        colors: str | None = None,
        stock: str | None = None,
        binding: str | None = None,
        folding: str | None = None,
        finishing: str | None = None,
        due_date: str | None = None,
        delivery: str | None = None,
        artwork: str | None = None,
        customer: str | None = None,
        notes: str | None = None,
    ) -> dict[str, Any]:
        """Check a request for quote is complete before it goes to an estimator.

        Pass what the customer asked for (e.g. from an RFQ email): quantity can list several
        ("500, 1000, 2500"), trim "8.5 x 11", colors "4/4" or "4/0", due_date as YYYY-MM-DD when known.
        Returns the normalized spec, what's missing, production problems (e.g. saddle stitch needs a
        multiple of 4 pages) and plain-language questions to send the customer. Nothing is priced.
        """
        return rfq.validate_rfq({
            "product": product, "quantity": quantity, "trim": trim, "pages": pages, "colors": colors,
            "stock": stock, "binding": binding, "folding": folding, "finishing": finishing,
            "due_date": due_date, "delivery": delivery, "artwork": artwork, "customer": customer, "notes": notes,
        })

    # ---------------------------------------------------------- auto-fix plan

    @mcp.tool(annotations=READ)
    async def plan_autofixes(job_id: str) -> dict[str, Any]:
        """For a checkpoint job: which preflight issues the auto-fix branch can handle, which need
        prepress by hand, which need the customer, and the suggested next routing step."""
        try:
            fix_map = autofix.load_autofix_map(settings.autofix_map or None)
        except (OSError, ValueError) as exc:
            raise ToolError(f"SWITCH_AUTOFIX_MAP: {exc}") from exc
        job = await ctx.require_job(job_id)
        content = await ctx.call(ctx.switch.download_report(job_id, settings.max_file_mb * 1024 * 1024))
        analysis, _ = await ctx.analyze_report(content)
        plan = autofix.plan_fixes(analysis, fix_map, job.get("outConnections"))
        if not fix_map:
            plan["note"] = "SWITCH_AUTOFIX_MAP isn't set, so nothing is treated as auto-fixable yet."
        return plan

    # ---------------------------------------------------------- job matching

    @mcp.tool(annotations=READ if pace_reader else LOCAL_READ)
    async def find_job_numbers(text: str) -> dict[str, Any]:
        """Find job numbers in a file name, email subject or body. With Pace connected, each candidate
        is looked up so you can see which one is a real, open job."""
        try:
            patterns = job_matching.compile_patterns(settings.job_number_patterns or None)
        except ValueError as exc:
            raise ToolError(f"PACE_JOB_NUMBER_PATTERNS: {exc}") from exc
        candidates = [asdict(c) for c in job_matching.find_job_numbers(text, patterns)]
        if pace_reader:
            for c in candidates[:10]:
                c["pace"] = await pace_call(pace_reader.get_job_status, c["job_number"])
        return {"candidates": candidates}

    # ---------------------------------------------------------- digest

    @mcp.tool(annotations=READ)
    async def morning_digest(hours: int = 16, stuck_after_hours: float | None = None) -> dict[str, Any]:
        """Digest for the prepress team: jobs waiting in checkpoints (flagging stuck ones), flows not
        running, and recurring errors. Includes a ready-to-post Markdown version."""
        d = await ctx.call(digest.build_digest(ctx.switch, hours, stuck_after_hours or settings.digest_stuck_hours))
        d["markdown"] = digest.render_markdown(d)
        return d

    # ---------------------------------------------------------- analytics (only when enabled)

    if settings.analytics_db:

        @mcp.tool(annotations=LOCAL_READ)
        async def preflight_stats(days: int = 30, customer: str | None = None) -> dict[str, Any]:
            """Preflight results over time: how often files needed the customer, the most common issues,
            and which customers' files most often have problems (for targeted client education)."""
            r = await asyncio.to_thread(analytics.report, settings.analytics_db, max(1, min(days, 3650)), customer)
            r["markdown"] = analytics.render_markdown(r)
            return r

    # ---------------------------------------------------------- Pace (only when connected)

    if pace_reader is not None:
        pace = pace_reader

        @mcp.tool(annotations=READ)
        async def pace_job(job_number: str) -> dict[str, Any]:
            """Job ticket and status from Pace (read-only)."""
            spec = await pace_call(pace.get_job_spec, job_number)
            status = await pace_call(pace.get_job_status, job_number)
            if spec is None and status is None:
                raise ToolError(f"No Pace job {job_number}.")
            return {"spec": spec.as_dict() if spec else None, "status": status}

    # ---------------------------------------------------------- proof approval (write)

    if settings.allow_write:

        @mcp.tool(annotations=RISKY)
        async def approve_proof(
            job_id: str,
            approved_by: str,
            pace_job_number: str | None = None,
            dry_run: bool = True,
        ) -> dict[str, Any]:
            """Proof approved: route the Switch job via the approve connection, then update the Pace job
            status and add a note. dry_run=True (default) only shows the plan; call again with
            dry_run=False only after the user confirms. Pace steps that can't be automated yet come
            back as 'manual' with instructions."""
            if not approved_by.strip():
                raise ToolError("approved_by is required (who approved the proof).")
            return await ctx.call(workflows.approve_proof(
                ctx.switch, ctx.pace, job_id, approved_by.strip(), pace_job_number,
                approve_connection=settings.approve_connection,
                pace_status=settings.pace_proof_approved_status, dry_run=dry_run,
            ))

    # ---------------------------------------------------------- prompts

    @mcp.prompt(title="Check a customer file against the job ticket")
    def check_file_against_ticket(file_path: str, job_number: str) -> str:
        return (
            f"Check {file_path} against job {job_number}. First get the ticket: use pace_job if it's "
            "available, otherwise the Pace MCP tools (finished size, page count, colors per side, spot "
            "colors, binding). Then call compare_file_to_ticket with those details. Report the verdict, "
            "then each mismatch in one plain sentence with what to tell the customer. If it matches, say so briefly."
        )

    @mcp.prompt(title="Draft an estimate from a print file")
    def quote_from_file(file_path: str, customer: str = "", quantity: str = "") -> str:
        return (
            f"Use draft_item_from_pdf on {file_path}. Summarize the item for an estimator (product, finished "
            "size, pages, colors, bleed, binding). List what's still needed from the customer"
            + (f" (quantity given: {quantity})" if quantity else "")
            + (f" for {customer}" if customer else "")
            + ". If similar Pace jobs are returned, list the 3 closest with their price and quantity as a "
            "sanity check. Do not invent prices; this is a draft for a person to price in Pace."
        )

    @mcp.prompt(title="Reply to a client who uploaded a file")
    def client_preflight_reply(file_path: str, trim: str = "", colors: str = "") -> str:
        return (
            f"Run quick_check_pdf on {file_path}"
            + (f" with the ordered size {trim}" if trim else "")
            + (f", and compare_file_to_ticket with trim '{trim}' and colors '{colors}'" if trim and colors else "")
            + ". Write a short, friendly reply to the client: whether the file is ready, what (if anything) "
            "they need to change, in plain language with page numbers. No jargon."
        )

    @mcp.prompt(title="Turn an RFQ email into an estimate request")
    def rfq_to_estimate(email_text: str) -> str:
        return (
            "Extract the print specs from this request for quote: product, quantity (all quantities if "
            "several), finished size, pages, colors per side (as '4/4', '4/0', ...), spot colors, stock, "
            "finishing, binding, due date, delivery. Only use what the email says; never guess. Pass what you "
            "found to validate_job_spec, then show its missing details, problems and questions to send back "
            "to the customer. Use find_job_numbers if it references an earlier job.\n\n"
            f"Email:\n{email_text}"
        )
