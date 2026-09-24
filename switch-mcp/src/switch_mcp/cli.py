"""Command line: ``enfocus-switch-mcp [serve|check] [--env-file PATH]``."""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys

from . import __version__
from .client import SwitchClient, SwitchError
from .config import ConfigError, Settings

log = logging.getLogger("switch_mcp")


def _load(env_file: str | None) -> Settings:
    try:
        return Settings.load(env_file)
    except ConfigError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc


def _describe(settings: Settings) -> list[str]:
    return [
        f"Config file      : {settings.env_file or '(none; using environment variables only)'}",
        f"Switch URL       : {settings.url}",
        f"Switch user      : {settings.username or '(not set)'}",
        f"Password         : {'set' if settings.password else '(not set)'}",
        f"Write tools      : {'ON' if settings.allow_write else 'off'}",
        f"Flow start/stop  : {'ON' if settings.allow_flow_control else 'off'}",
        f"Upload folders   : {', '.join(map(str, settings.upload_dirs)) or '(none: file tools disabled)'}",
        f"Download folder  : {settings.download_dir}",
        f"Auto-fix map     : {settings.autofix_map or '(not set)'}",
        f"Pace database    : {'set (read-only queries: ' + settings.pace_queries_file + ')' if settings.pace_db_dsn else '(not set)'}",
        f"Pace API writes  : {_pace_writes(settings)}",
        f"Audit log        : {settings.audit_log or '(application log only)'}",
        f"Digest webhook   : {'set' if settings.digest_webhook_url else '(not set)'}",
        f"Customer rules   : {settings.customer_rules or '(not set)'}",
        f"Analytics DB     : {settings.analytics_db or '(off)'}",
    ]


async def _probe(settings: Settings) -> list[str]:
    client = SwitchClient(settings)
    lines: list[str] = []
    try:
        info = await client.ensure_login()
        lines.append(f"OK  Logged in to Switch as '{info.get('user', settings.username)}'.")
        perms = {k: v for k, v in info.items() if k.endswith("Access")}
        if perms:
            granted = ", ".join(k.removesuffix("Access") for k, v in perms.items() if v) or "none"
            lines.append(f"    Switch permissions: {granted}")
        await client.ping()
        flows = await client.list_flows(fields=["id", "name", "status"])
        running = sum(1 for f in flows if str(f.get("status", "")).lower() == "running")
        lines.append(f"OK  Flows visible: {len(flows)} ({running} running).")
        points = await client.list_submit_points()
        lines.append(f"OK  Submit points visible: {len(points)}.")
        jobs = await client.list_jobs(filter_query={"and": [{"status": {"is": "alert"}}]}, fields=["id"], limit=1000)
        lines.append(f"OK  Jobs waiting in checkpoints: {len(jobs.get('data') or [])}.")
        try:
            await client.messages(period="1h", limit=1)
            lines.append("OK  Message log readable.")
        except SwitchError as exc:
            lines.append(f"--  Message log not readable ({exc}); recent_messages/problem_summary won't work.")
    finally:
        await client.aclose()
    if settings.pace_enabled:
        lines.append(_probe_pace(settings))
    return lines


def _pace_writes(settings: Settings) -> str:
    if not settings.pace_api_config:
        return "(not configured: Pace steps are reported as manual)"
    if not settings.pace_allow_write:
        return f"configured ({settings.pace_api_config}) but OFF (PACE_ALLOW_WRITE)"
    return f"ON; allowed statuses: {settings.pace_allowed_statuses or '(none)'}"


def _probe_pace(settings: Settings) -> str:
    from .automation.pace import PaceError, gateway_from_env

    try:
        gw = gateway_from_env(settings.pace_env())
        if gw is None or not getattr(gw, "can_read", True):
            # Write-only: don't send a test write; the config file was loaded and validated above.
            return "OK  Pace API config loaded (no database: nothing to read; writes are not test-sent)."
        gw.get_job_status("__connector_check__")  # any result (usually none) proves the query runs
    except (PaceError, OSError, ValueError) as exc:
        return f"--  Pace: {exc}"
    except Exception as exc:  # noqa: BLE001 - driver/network errors, reported plainly
        return f"--  Pace database not reachable: {exc.__class__.__name__}: {exc}"
    return "OK  Pace database reachable; job_status query runs (read-only)."


def check(settings: Settings) -> int:
    print(f"enfocus-switch-mcp {__version__}")
    print("\n".join(_describe(settings)))
    errors, warnings = settings.validate()
    for w in warnings:
        print(f"WARNING: {w}")
    if errors:
        for e in errors:
            print(f"ERROR: {e}")
        print("\nFix the errors above, then run this check again.")
        return 2
    print(f"\nConnecting to {settings.url} ...")
    try:
        for line in asyncio.run(_probe(settings)):
            print(line)
    except SwitchError as exc:
        print(f"FAILED: Switch refused the request: {exc}")
        if exc.status_code in (401, 403):
            print("Check SWITCH_USERNAME / SWITCH_PASSWORD and the user's permissions in Switch (Users pane).")
        return 1
    except Exception as exc:  # noqa: BLE001 - report any network/TLS error plainly
        print(f"FAILED: Could not reach Switch at {settings.url}: {exc.__class__.__name__}: {exc}")
        print("Check that the Switch Web Services are running, the host/port are right, and no firewall blocks it.")
        return 1
    print("\nAll checks passed. The connector is ready.")
    return 0


def run_digest(settings: Settings, hours: int, post: bool) -> int:
    from .automation import digest

    errors, _ = settings.validate()
    if errors:
        print("\n".join(f"ERROR: {e}" for e in errors), file=sys.stderr)
        return 2

    async def build() -> dict:
        client = SwitchClient(settings)
        try:
            return await digest.build_digest(client, hours, settings.digest_stuck_hours)
        finally:
            await client.aclose()

    try:
        text = digest.render_markdown(asyncio.run(build()))
    except Exception as exc:  # noqa: BLE001 - scheduled job: report plainly and fail
        print(f"FAILED: {exc.__class__.__name__}: {exc}", file=sys.stderr)
        return 1
    print(text)
    if post:
        if not settings.digest_webhook_url:
            print("ERROR: --post needs DIGEST_WEBHOOK_URL.", file=sys.stderr)
            return 2
        try:
            digest.post_webhook(settings.digest_webhook_url, text)
        except Exception as exc:  # noqa: BLE001
            print(f"FAILED to post the digest: {exc.__class__.__name__}", file=sys.stderr)
            return 1
    return 0


def run_report(settings: Settings, days: int, customer: str | None) -> int:
    from .automation import analytics

    if not settings.analytics_db:
        print("ERROR: set ANALYTICS_DB to use reports.", file=sys.stderr)
        return 2
    print(analytics.render_markdown(analytics.report(settings.analytics_db, days, customer)))
    return 0


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="enfocus-switch-mcp",
        description="MCP server for Enfocus Switch. With no command it runs the server over stdio "
                    "(this is what Claude Desktop / Claude Code start).",
    )
    parser.add_argument("command", nargs="?", default="serve", choices=["serve", "check", "digest", "report"],
                        help="serve (default): run the MCP server; check: verify config and connection; "
                             "digest: print the checkpoint/error digest; report: preflight analytics")
    parser.add_argument("--post", action="store_true", help="digest: also send it to DIGEST_WEBHOOK_URL")
    parser.add_argument("--hours", type=int, default=16, help="digest: look back this many hours for errors")
    parser.add_argument("--days", type=int, default=30, help="report: look back this many days")
    parser.add_argument("--customer", help="report: only this customer")
    parser.add_argument("--env-file", help="config file of KEY=value lines (overrides SWITCH_ENV_FILE)")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    args = parser.parse_args(argv)

    # stdout carries the MCP protocol, so all diagnostics go to stderr.
    logging.basicConfig(level=logging.INFO, stream=sys.stderr, format="enfocus-switch-mcp: %(levelname)s %(message)s")
    # httpx logs full request URLs at INFO; Switch download links embed a session key, so keep them out of logs.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    settings = _load(args.env_file)

    if args.command == "check":
        raise SystemExit(check(settings))
    if args.command == "digest":
        raise SystemExit(run_digest(settings, args.hours, args.post))
    if args.command == "report":
        raise SystemExit(run_report(settings, args.days, args.customer))

    from .server import build_server

    errors, warnings = settings.validate()
    for w in warnings:
        log.warning(w)
    for e in errors:
        log.error("%s Switch tools will report this until it is fixed.", e)
    log.info("Starting (Switch %s, user %s, config %s)", settings.url, settings.username or "-",
             settings.env_file or "environment")
    build_server(settings).run()
