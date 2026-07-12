"""PII redaction — the deterministic gate between org data and a vendor.

Runs BEFORE the payload leaves the process, in pure code, with no model in the
loop: asking an LLM to strip PII means sending it the PII. Patterns favour
precision over recall (a redactor that mangles half the prompt gets switched
off by annoyed users, which is worse than imperfect coverage), and every hit is
counted on the run record so redaction volume is itself a visible metric.

Covers the identifiers that appear in everyday business text — emails, phone
numbers, IBANs, credit cards (Luhn-checked), national ids, API keys/secrets.
Org-specific patterns (employee ids, customer codes) can be added per org file.
This is a seatbelt, not a DLP suite; docs/governance.md spells out the boundary.
"""

import re
from dataclasses import dataclass, field

# Order matters: the more specific digit shapes (iban, card) must claim their text
# before the generic phone pattern gets a chance to eat it.
_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("email", re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")),
    ("iban", re.compile(r"\b[A-Z]{2}\d{2}(?: ?[A-Z0-9]{4}){3,7}(?: ?[A-Z0-9]{1,3})?\b")),
    # 13–19 digits, optional space/dash between digits; anchored on a digit at
    # both ends so a trailing separator is never swallowed into the redaction.
    ("card", re.compile(r"\b\d(?:[ -]?\d){12,18}\b")),  # candidates; Luhn filters below
    # bearer tokens / API keys: long, entropy-shaped strings with vendor prefixes
    ("secret", re.compile(r"\b(?:sk|pk|rk|key|token)[-_][A-Za-z0-9_\-]{16,}\b")),
    ("dni", re.compile(r"\b\d{8}[A-HJ-NP-TV-Z]\b")),  # Spanish national id
    # formatted phone numbers, tolerant of separators; digit count checked below (E.164: 9–15)
    ("phone", re.compile(r"(?<![\w/])\+?\d[\d ()\-.]{7,}\d(?![\w/])")),
]


def _luhn_ok(digits: str) -> bool:
    total, parity = 0, len(digits) % 2
    for index, char in enumerate(digits):
        digit = int(char)
        if index % 2 == parity:
            digit *= 2
            if digit > 9:
                digit -= 9
        total += digit
    return total % 10 == 0


@dataclass
class RedactionResult:
    text: str
    hits: int = 0
    by_kind: dict[str, int] = field(default_factory=dict)


def redact(text: str, extra_patterns: list[str] | None = None) -> RedactionResult:
    """Replace every match with ``[REDACTED:<kind>]``. Order matters: emails go
    first so a phone-looking fragment inside an address never splits it."""
    by_kind: dict[str, int] = {}

    def _sub(kind: str, pattern: re.Pattern[str], value: str) -> str:
        def _replace(match: re.Match[str]) -> str:
            digits = re.sub(r"\D", "", match.group(0))
            if kind == "card" and not (13 <= len(digits) <= 19 and _luhn_ok(digits)):
                return match.group(0)  # long number, but not a card — leave it
            if kind == "phone" and not (9 <= len(digits) <= 15):
                return match.group(0)  # too long/short for E.164 — likely an id, leave it
            by_kind[kind] = by_kind.get(kind, 0) + 1
            return f"[REDACTED:{kind}]"

        return pattern.sub(_replace, value)

    for kind, pattern in _PATTERNS:
        text = _sub(kind, pattern, text)
    for index, raw in enumerate(extra_patterns or []):
        text = _sub(f"custom{index}", re.compile(raw), text)

    return RedactionResult(text=text, hits=sum(by_kind.values()), by_kind=by_kind)
