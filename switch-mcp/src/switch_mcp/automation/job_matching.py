"""Find job numbers in file names, email subjects and bodies.

Patterns are house-specific, so they are configurable (``PACE_JOB_NUMBER_PATTERNS``,
a ``;``-separated list of regexes with one capture group). The defaults cover
common forms: "J123456", "Job 123456", "Job#123456", "123456_Brochure.pdf",
"ORD1001".
"""

from __future__ import annotations

import re
from dataclasses import dataclass

DEFAULT_PATTERNS = [
    # "(?!\d)" rather than "\b" at the end, so "J123456_Brochure.pdf" matches ("_" is a word character).
    r"\bjob\s*(?:no\.?|number|#)?\s*[:#-]?\s*(\d{4,8})(?!\d)",   # "Job 123456", "job #123456"
    r"(?<![A-Za-z0-9])J[-_ ]?(\d{4,8})(?!\d)",                     # "J123456", "J-123456", "J123456_x"
    r"(?<![A-Za-z0-9])(ORD\d{3,8})(?!\d)",                         # "ORD1001", "ORD1001_Brochure.pdf"
    r"(?:^|[\\/_\s])(\d{5,8})(?=[_\-\s.])",                    # "123456_Brochure.pdf"
]
MAX_TEXT = 20_000


@dataclass
class JobCandidate:
    job_number: str
    matched_text: str
    pattern: int  # index of the pattern that matched (lower = more specific)


def compile_patterns(spec: str | None) -> list[re.Pattern[str]]:
    raw = [p for p in (spec.split(";") if spec else DEFAULT_PATTERNS) if p.strip()]
    compiled = []
    for p in raw:
        try:
            rx = re.compile(p, re.IGNORECASE)
        except re.error as exc:
            raise ValueError(f"Invalid job number pattern {p!r}: {exc}") from exc
        if rx.groups < 1:
            raise ValueError(f"Job number pattern {p!r} needs one capture group around the number.")
        compiled.append(rx)
    return compiled


def find_job_numbers(text: str, patterns: list[re.Pattern[str]] | None = None) -> list[JobCandidate]:
    """Candidates in order of confidence (most specific pattern first), de-duplicated."""
    patterns = patterns or compile_patterns(None)
    text = (text or "")[:MAX_TEXT]
    seen: dict[str, JobCandidate] = {}
    for i, rx in enumerate(patterns):
        for m in rx.finditer(text):
            number = m.group(1).upper()
            if number not in seen:
                seen[number] = JobCandidate(number, m.group(0).strip(), i)
    return sorted(seen.values(), key=lambda c: c.pattern)
