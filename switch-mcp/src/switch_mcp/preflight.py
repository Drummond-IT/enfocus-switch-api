"""Parse preflight reports and turn them into plain language.

Understands the PitStop report formats:

* XML v2 - ``EnfocusReport/Report/PreflightResult/PreflightResultEntry``
  items with a ``level`` attribute (error, warning, fix, ...);
* XML v3 - ``EnfocusReport/PreflightReport/{Errors,Warnings,Fixes,...}/PreflightReportItem``;
* JSON (PitStop 2023+) - ``preflightReport.{errors,warnings,fixes,...}.preflightReportItem[]``.

Element names are matched without namespaces and case-insensitively, so
close variants also work. Anything else is treated as plain text with one
finding per line, so a CSR can paste the messages from a PDF report.
"""

from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any, Literal

from defusedxml import DefusedXmlException
from defusedxml.ElementTree import fromstring as safe_fromstring

from .knowledge import IssueCategory, categorize

Audience = Literal["customer", "csr", "prepress"]

# Category container (v3 XML / JSON) or item level (v2 XML) -> normalized severity.
SEVERITY_NAMES = {
    "errors": "error", "error": "error",
    "criticalfailures": "error", "criticalfailure": "error", "failures": "error", "failure": "error",
    "warnings": "warning", "warning": "warning",
    "noncriticalfailures": "warning", "noncriticalfailure": "warning",
    "fixes": "fixed", "fix": "fixed", "fixed": "fixed",
    "signoffs": "signed_off", "signoff": "signed_off",
    "informations": "info", "information": "info", "info": "info",
}

# "(3x on pages 1, 2)" / "(1x on page 1)" at the end of PitStop messages.
_OCCURRENCES = re.compile(r"\((\d+)\s*x\b[^()\d]*([\d,\s\-]*)\)\s*$", re.IGNORECASE)

# Reports are untrusted input: bound everything that scales with their content.
MAX_MESSAGE_CHARS = 500
MAX_PAGES = 10_000
MAX_RANGE = 10_000
MAX_OCCURRENCES = 10_000_000
MAX_FINDINGS = 5_000


@dataclass
class Finding:
    severity: str
    message: str
    pages: list[int] = field(default_factory=list)
    occurrences: int = 1


@dataclass
class ParsedReport:
    source_format: str
    summary: dict[str, Any]
    findings: list[Finding]


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def _text(el: ET.Element | None) -> str:
    return " ".join("".join(el.itertext()).split()) if el is not None else ""


def _int_list(values: str) -> list[int]:
    pages: list[int] = []
    for part in re.split(r"[,\s;]+|\band\b", values[:2000]):
        if len(pages) >= MAX_PAGES:
            break
        if re.fullmatch(r"\d{1,7}-\d{1,7}", part):
            a, b = (int(x) for x in part.split("-"))
            if a <= b and b - a <= MAX_RANGE:  # ignore absurd ranges instead of expanding them
                pages.extend(range(a, b + 1))
        elif part.isdigit() and len(part) <= 7:
            pages.append(int(part))
    return pages[:MAX_PAGES]


def _make_finding(severity: str, message: str, zero_based_pages: list[int], locations: int) -> Finding:
    """Build a finding, taking counts/pages from a trailing "(Nx on page P)" when present."""
    pages = sorted({p + 1 for p in zero_based_pages[:MAX_PAGES]})
    occurrences = max(1, locations)
    message = " ".join(message.split())  # collapse whitespace runs
    # The suffix is short, so only look at the end of the message (keeps matching linear).
    tail_start = max(0, len(message) - 200)
    m = _OCCURRENCES.search(message, tail_start)
    if m:
        occurrences = min(int(m.group(1)[:9]), MAX_OCCURRENCES)
        pages = sorted(set(pages) | set(_int_list(m.group(2))))[:MAX_PAGES]
        message = message[: m.start()].strip()
    if len(message) > MAX_MESSAGE_CHARS:
        message = message[:MAX_MESSAGE_CHARS] + "…"
    return Finding(severity, message, pages, min(occurrences, MAX_OCCURRENCES))


# ------------------------------------------------------------------------ XML

def _xml_item(item: ET.Element, severity: str) -> Finding | None:
    message = ""
    for el in item.iter():
        if _local(el.tag) == "message" and _text(el):
            message = _text(el)
            break
    if not message:
        return None
    pages: list[int] = []
    locations = 0
    for el in item.iter():
        if _local(el.tag) == "location":
            locations += 1
            page = el.attrib.get("page") or el.attrib.get("Page")
            if page and page.lstrip("-").isdigit():
                pages.append(int(page))
    return _make_finding(severity, message, pages, locations)


def _xml_summary(root: ET.Element) -> dict[str, Any]:
    wanted = {
        "preflightprofile": "profile", "profilename": "profile",
        "documentname": "file", "filename": "file",
        "numpages": "pages", "numberofpages": "pages", "pagecount": "pages",
        "pdfversion": "pdf_version",
        "outputconditionidentifier": "output_intent",
    }
    summary: dict[str, Any] = {}
    for el in root.iter():
        name = _local(el.tag)
        if name == "preflightresult":  # v2 counts live here as attributes
            summary["reported_counts"] = {k: int(v) for k, v in el.attrib.items() if v.isdigit()}
        key = wanted.get(name)
        if key and key not in summary:
            value = _text(el) or el.attrib.get("Name") or el.attrib.get("name") or el.attrib.get("Version") or ""
            if value:
                summary[key] = value
        # Var/Const name="..." style key/value pairs.
        attr_name = (el.attrib.get("name") or el.attrib.get("Name") or "").replace(" ", "").lower()
        key = wanted.get(attr_name)
        if key and key not in summary and _text(el):
            summary[key] = _text(el)
    return summary


def parse_xml_report(xml_text: str | bytes) -> ParsedReport:
    # defusedxml refuses DTD entity tricks (billion laughs, external entities) in untrusted reports.
    root = safe_fromstring(xml_text)
    findings: list[Finding] = []
    fmt = "pitstop-xml"
    for el in root.iter():
        name = _local(el.tag)
        if name == "preflightresultentry":  # v2: flat list, severity on the item
            fmt = "pitstop-xml-v2"
            level = (el.attrib.get("level") or el.attrib.get("Level") or "").lower()
            finding = _xml_item(el, SEVERITY_NAMES.get(level, "info"))
        elif name == "preflightreportitem":  # v3: severity from the parent container
            fmt = "pitstop-xml-v3"
            continue  # handled via the container below
        elif name in SEVERITY_NAMES and any(_local(c.tag) == "preflightreportitem" for c in el):
            for item in el:
                if _local(item.tag) == "preflightreportitem" and (f := _xml_item(item, SEVERITY_NAMES[name])):
                    findings.append(f)
            continue
        else:
            continue
        if finding:
            findings.append(finding)
    return ParsedReport(fmt, _xml_summary(root), findings[:MAX_FINDINGS])


# ----------------------------------------------------------------------- JSON

def _find_key(data: Any, key: str) -> Any:
    key = key.lower()
    if isinstance(data, dict):
        for k, v in data.items():
            if k.lower() == key:
                return v
        for v in data.values():
            found = _find_key(v, key)
            if found is not None:
                return found
    elif isinstance(data, list):
        for v in data:
            found = _find_key(v, key)
            if found is not None:
                return found
    return None


def parse_json_report(data: dict[str, Any]) -> ParsedReport:
    report = _find_key(data, "preflightReport") or {}
    findings: list[Finding] = []
    for category, items in report.items():
        severity = SEVERITY_NAMES.get(category.lower())
        if not severity or not isinstance(items, dict):
            continue
        entries = items.get("preflightReportItem") or []
        for item in entries if isinstance(entries, list) else [entries]:
            message = item.get("message") or _find_key(item, "baseString") or ""
            if not message:
                continue
            locations = item.get("location") or []
            if isinstance(locations, dict):
                locations = [locations]
            pages = [int(loc["page"]) for loc in locations if isinstance(loc.get("page"), (int, float))]
            findings.append(_make_finding(severity, message, pages, len(locations)))
    props = _find_key(data, "documentProperties") or {}
    version = props.get("pdfVersion")
    summary = {
        "profile": _find_key(data, "preflightProfile"),
        "file": props.get("documentName"),
        "pages": props.get("numPages"),
        "pdf_version": version.get("version") if isinstance(version, dict) else version,
        "reported_counts": {k: v for k, v in report.items() if k.endswith("Number")},
    }
    return ParsedReport("pitstop-json", {k: v for k, v in summary.items() if v not in (None, "", {})},
                        findings[:MAX_FINDINGS])


# ----------------------------------------------------------------------- text

_TEXT_SEVERITY = (
    (re.compile(r"^\s*(?:(?:errors?|failures?|failed)\b|[✖✗❌])[:\s-]*", re.IGNORECASE), "error"),
    (re.compile(r"^\s*(?:warnings?\b|⚠️?)[:\s-]*", re.IGNORECASE), "warning"),
    (re.compile(r"^\s*(?:fix(?:es|ed)?\b|[✔✓])[:\s-]*", re.IGNORECASE), "fixed"),
    (re.compile(r"^\s*(?:info|information|note)\b[:\s-]*", re.IGNORECASE), "info"),
)
_PAGES_IN_TEXT = re.compile(r"\(?\bpages?\s*:?\s*([\d,\s\-]+)\)?", re.IGNORECASE)
_HEADING = re.compile(
    r"(errors?|warnings?|fixes|fixed|sign[- ]?offs?|info|informations?|(?:non[- ]?)?critical failures?)"
    r"\s*(\(\d+\))?:?",
    re.IGNORECASE,
)


def parse_text_report(text: str) -> ParsedReport:
    """One finding per line. Lines may start with 'Error:', 'Warning:', 'Fixed:' etc.

    A line that is only a section heading ("Errors", "Warnings (3)") sets the
    severity for the lines below it.
    """
    findings: list[Finding] = []
    current = "warning"
    for raw in text.splitlines():
        line = raw.strip(" \t-•*")[:4000]
        if not line:
            continue
        heading = _HEADING.fullmatch(line)
        if heading:
            word = re.sub(r"[\s-]", "", heading.group(1).lower())
            current = SEVERITY_NAMES.get(word, SEVERITY_NAMES.get(word + "s", current))
            continue
        severity = current
        for pattern, sev in _TEXT_SEVERITY:
            m = pattern.match(line)
            if m and m.end() < len(line):
                severity, line = sev, line[m.end():].strip()
                break
        finding = _make_finding(severity, line, [], 0)
        if not finding.pages and (pm := _PAGES_IN_TEXT.search(finding.message)):
            finding.pages = _int_list(pm.group(1))
            finding.occurrences = max(finding.occurrences, len(finding.pages))
        findings.append(finding)
        if len(findings) >= MAX_FINDINGS:
            break
    return ParsedReport("text", {}, findings)


def parse_report(content: str | bytes) -> ParsedReport:
    if isinstance(content, bytes):
        content = content.decode("utf-8", errors="replace")
    stripped = content.lstrip("﻿ \r\n\t")
    if stripped.startswith("<"):
        try:
            return parse_xml_report(stripped.encode("utf-8"))
        except DefusedXmlException as exc:
            raise ValueError(f"Refused to parse the XML report: it uses DTD/entity features ({exc}).") from exc
        except ET.ParseError:
            pass
    if stripped.startswith("{"):
        try:
            return parse_json_report(json.loads(stripped))
        except ValueError:
            pass
    return parse_text_report(content)


# --------------------------------------------------------------------- explain

SEVERITY_ORDER = {"error": 0, "warning": 1, "info": 2, "fixed": 3, "signed_off": 4, "accepted": 5}


def _page_text(pages: list[int]) -> str:
    if not pages:
        return ""
    pages = sorted(set(pages))
    ranges: list[str] = []
    start = prev = pages[0]
    for p in pages[1:] + [None]:  # type: ignore[list-item]
        if p is not None and p == prev + 1:
            prev = p
            continue
        ranges.append(str(start) if start == prev else f"{start}-{prev}")
        if p is not None:
            start = prev = p
    return ("page " if len(pages) == 1 else "pages ") + ", ".join(ranges)


def decide_verdict(issues: list[dict[str, Any]]) -> tuple[str, str]:
    """Overall verdict from grouped issues. Used by ``analyze`` and by anything that adjusts issues later
    (customer rules), so every path decides the same way."""
    open_issues = [i for i in issues if i["severity"] in ("error", "warning")]
    blocking = [i for i in open_issues if i["severity"] == "error"]
    customer_needed = [i for i in open_issues if i["fix_owner"] == "customer"
                       or (i["severity"] == "error" and i["fix_owner"] == "either" and not i["auto_fixable"])]
    if not open_issues:
        return "ready", "Ready to print."
    if customer_needed:
        return "needs_customer", ("Needs something from the customer before it can print." if blocking else
                                  "Can print, but the customer should approve or improve a few things first.")
    return "prepress_can_fix", "Prepress can fix the remaining items in-house; no customer action needed."


def analyze(report: ParsedReport) -> dict[str, Any]:
    """Group findings by category and decide an overall verdict."""
    groups: OrderedDict[str, dict[str, Any]] = OrderedDict()
    for f in sorted(report.findings, key=lambda f: SEVERITY_ORDER.get(f.severity, 9)):
        cat = categorize(f.message)
        key = f"{cat.key}:{'fixed' if f.severity in ('fixed', 'signed_off') else 'open'}"
        g = groups.setdefault(key, {
            "category": cat, "severity": f.severity, "pages": set(), "occurrences": 0, "messages": [],
        })
        g["pages"].update(f.pages)
        g["occurrences"] += f.occurrences
        if f.message not in g["messages"]:
            g["messages"].append(f.message)
        if SEVERITY_ORDER.get(f.severity, 9) < SEVERITY_ORDER.get(g["severity"], 9):
            g["severity"] = f.severity

    issues = []
    for g in groups.values():
        cat: IssueCategory = g["category"]
        issues.append({
            "category": cat.key,
            "title": cat.title,
            "severity": g["severity"],
            "pages": sorted(g["pages"]),
            "occurrences": g["occurrences"],
            "fix_owner": cat.fix_owner,
            "auto_fixable": cat.auto_fixable,
            "original_messages": g["messages"][:10],
            "explanation": {"customer": cat.customer, "csr": cat.csr, "prepress": cat.prepress},
        })

    verdict, verdict_text = decide_verdict(issues)

    counts: dict[str, int] = {}
    for f in report.findings:
        counts[f.severity] = counts.get(f.severity, 0) + 1

    return {
        "source_format": report.source_format,
        "summary": report.summary,
        "counts": counts,
        "verdict": verdict,
        "verdict_text": verdict_text,
        "issues": issues,
    }


def render(analysis: dict[str, Any], audience: Audience = "customer", job_name: str | None = None) -> str:
    """Deterministic plain-language write-up of an ``analyze()`` result."""
    name = job_name or analysis["summary"].get("file") or "your file"
    open_issues = [i for i in analysis["issues"] if i["severity"] in ("error", "warning")]
    fixed = [i for i in analysis["issues"] if i["severity"] in ("fixed", "signed_off")]
    accepted = [i for i in analysis["issues"] if i["severity"] == "accepted"]
    lines: list[str] = []

    if audience == "customer":
        if not open_issues:
            lines.append(f"Good news: {name} passed our print check and is ready to go.")
        else:
            must = [i for i in open_issues if i["fix_owner"] == "customer" or i["severity"] == "error"]
            lines.append(f"We checked {name} and found {len(open_issues)} thing(s) worth knowing before we print:")
            lines.append("")
            for n, i in enumerate(open_issues, 1):
                where = _page_text(i["pages"])
                lines.append(f"{n}. {i['title']}{f' ({where})' if where else ''}")
                lines.append(f"   {i['explanation']['customer']}")
            lines.append("")
            if must:
                lines.append("Please reply with an updated file or let us know how you'd like to proceed.")
            else:
                lines.append("We'll take care of these for you. No action is needed unless you have questions.")
        if fixed:
            lines.append("")
            lines.append("We also made these routine adjustments for you: " + "; ".join(i["title"] for i in fixed) + ".")
        return "\n".join(lines)

    lines.append(f"{name}: {analysis['verdict_text']}")
    counts = analysis["counts"]
    lines.append(
        f"Errors {counts.get('error', 0)}, warnings {counts.get('warning', 0)}, auto-fixed {counts.get('fixed', 0)}."
    )
    for i in open_issues:
        where = _page_text(i["pages"])
        owner = {"customer": "customer", "prepress": "prepress", "either": "prepress first, else customer"}[i["fix_owner"]]
        lines.append("")
        lines.append(f"- [{i['severity'].upper()}] {i['title']}{f' ({where})' if where else ''} - owner: {owner}"
                     + (" - auto-fixable" if i["auto_fixable"] else ""))
        lines.append(f"  {i['explanation'][audience]}")
        if audience == "prepress":
            for m in i["original_messages"][:3]:
                lines.append(f"  > {m}")
    if accepted:
        lines.append("")
        rule = analysis.get("customer_rule") or {}
        lines.append("Accepted per customer agreement" + (f" ({rule['customer']})" if rule.get("customer") else "")
                     + ": " + "; ".join(i["title"] for i in accepted))
        if rule.get("notes"):
            lines.append(f"  Note: {rule['notes']}")
    if fixed:
        lines.append("")
        lines.append("Already fixed: " + "; ".join(i["title"] for i in fixed))
    return "\n".join(lines)
