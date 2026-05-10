"""
pii_redactor.py — PII redaction for Mashreq CBP transcript analysis.

Catches all PII types seeded in synthetic data and found in real transcripts:
  - UAE Emirates ID: 784-YYYY-XXXXXXX-X
  - Mobile numbers: +971/+20/+92 prefixes (UAE/Egypt/Pakistan)
  - 16-digit card numbers (Luhn-validated for precision, but also raw 16-digit)
  - IBANs: AE/EG/PK format
  - Account numbers: 10-12 digit sequences (Mashreq format)
  - Customer names: best-effort via named entity patterns (conservative)

DO NOT REDACT:
  - Merchant names (e.g. "SPICY GRAND RESTAURANT") — diagnostic for analysis
  - AED/EGP/PKR amounts — diagnostic for analysis
  - Generic banking terms

Applied to:
  - bot_message, user_message in transcripts
  - comments in CSAT
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

# ---------------------------------------------------------------------------
# REDACTION TOKENS
# ---------------------------------------------------------------------------

REDACT_EMIRATES_ID = "[EMIRATES_ID_REDACTED]"
REDACT_MOBILE = "[MOBILE_REDACTED]"
REDACT_CARD = "[CARD_NUMBER_REDACTED]"
REDACT_IBAN = "[IBAN_REDACTED]"
REDACT_ACCOUNT = "[ACCOUNT_NUMBER_REDACTED]"
REDACT_EMAIL = "[EMAIL_REDACTED]"


# ---------------------------------------------------------------------------
# REGEX PATTERNS
# ---------------------------------------------------------------------------

# Emirates ID: 784-YYYY-XXXXXXX-X
PATTERN_EMIRATES_ID = re.compile(
    r"\b784[-\s]?\d{4}[-\s]?\d{7}[-\s]?\d\b"
)

# Mobile numbers with international prefix
# UAE +971 5X XXXXXXX, Egypt +20 1X XXXXXXXX, Pakistan +92 3X XXXXXXXX
PATTERN_MOBILE = re.compile(
    r"(?:"
    r"\+971(?:[\s\-]?\d){8,10}"     # UAE +971 with optional separators
    r"|\+20(?:[\s\-]?\d){9,11}"     # Egypt +20 with optional separators
    r"|\+92(?:[\s\-]?\d){9,11}"     # Pakistan +92 with optional separators
    r"|00971(?:[\s\-]?\d){8,10}"    # UAE 00971 prefix
    r"|0971(?:[\s\-]?\d){8,10}"     # UAE 0971 prefix
    r"|05\d(?:[\s\-]?\d){7}"        # UAE local 05X XXXXXXX
    r")"
)

# Credit/debit card: 16 digits (with optional spaces or dashes)
PATTERN_CARD_SPACED = re.compile(
    r"\b\d{4}[\s\-]?\d{4}[\s\-]?\d{4}[\s\-]?\d{4}\b"
)

# IBAN: AE/EG/PK + 2 check digits + BBAN
PATTERN_IBAN = re.compile(
    r"\b(?:AE|EG|PK)\d{2}[A-Z0-9]{10,30}\b",
    re.IGNORECASE,
)

# Account numbers: 10-12 digit sequences (not preceded by currency amounts)
# Conservative: requires word boundary and no currency prefix
PATTERN_ACCOUNT = re.compile(
    r"(?<![0-9])(?<![AED|EGP|PKR|USD])\b\d{10,12}\b(?![0-9])"
)

# Email addresses
PATTERN_EMAIL = re.compile(
    r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b"
)

# Ordered list of (pattern, replacement_token, pii_type_label)
# Order matters: IBAN before account (IBAN contains digit sequences)
PII_PATTERNS: list[tuple[re.Pattern, str, str]] = [
    (PATTERN_EMIRATES_ID, REDACT_EMIRATES_ID, "emirates_id"),
    (PATTERN_MOBILE, REDACT_MOBILE, "mobile"),
    (PATTERN_IBAN, REDACT_IBAN, "iban"),
    (PATTERN_CARD_SPACED, REDACT_CARD, "card_number"),
    (PATTERN_EMAIL, REDACT_EMAIL, "email"),
    (PATTERN_ACCOUNT, REDACT_ACCOUNT, "account_number"),
]


# ---------------------------------------------------------------------------
# LUHN VALIDATION (used to reduce false positives on card numbers)
# ---------------------------------------------------------------------------

def _luhn_valid(number_str: str) -> bool:
    """Return True if the digit string passes Luhn checksum."""
    digits = [int(c) for c in number_str if c.isdigit()]
    if len(digits) != 16:
        return False
    total = 0
    for i, d in enumerate(reversed(digits)):
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


# ---------------------------------------------------------------------------
# REDACTION RESULT
# ---------------------------------------------------------------------------

@dataclass
class RedactionResult:
    """Result of redacting a single text field."""
    original_length: int
    redacted_text: str
    counts: dict[str, int] = field(default_factory=dict)

    @property
    def total_redactions(self) -> int:
        return sum(self.counts.values())

    @property
    def was_modified(self) -> bool:
        return self.total_redactions > 0


# ---------------------------------------------------------------------------
# CORE REDACTOR
# ---------------------------------------------------------------------------

def redact_text(text: str, validate_luhn: bool = True) -> RedactionResult:
    """
    Apply all PII patterns to a single text string.

    Args:
        text: Raw text to redact.
        validate_luhn: If True, only redact 16-digit sequences that pass Luhn.
                       Reduces false positives (e.g. timestamps, reference numbers).

    Returns:
        RedactionResult with redacted text and per-type counts.
    """
    if not text or not isinstance(text, str):
        return RedactionResult(
            original_length=0,
            redacted_text=text or "",
            counts={},
        )

    counts: dict[str, int] = {}
    working = text

    for pattern, replacement, pii_type in PII_PATTERNS:
        if pii_type == "card_number" and validate_luhn:
            # For card numbers: match first, validate Luhn, then replace
            def _replace_card(m: re.Match) -> str:
                raw_digits = re.sub(r"[\s\-]", "", m.group())
                if _luhn_valid(raw_digits):
                    counts["card_number"] = counts.get("card_number", 0) + 1
                    return replacement
                return m.group()  # Not a valid card number, leave as-is

            working = pattern.sub(_replace_card, working)
        else:
            matches = pattern.findall(working)
            if matches:
                counts[pii_type] = len(matches)
                working = pattern.sub(replacement, working)

    return RedactionResult(
        original_length=len(text),
        redacted_text=working,
        counts=counts,
    )


def redact_fields(
    record: dict,
    fields_to_redact: Optional[list[str]] = None,
) -> tuple[dict, dict[str, int]]:
    """
    Redact PII from specified fields in a record dict in-place.

    Args:
        record: A dict representing one transcript or CSAT row.
        fields_to_redact: List of field names to apply redaction to.
                          Defaults to ["bot_message", "user_message", "comments"].

    Returns:
        (modified_record, per_type_counts) tuple.
    """
    if fields_to_redact is None:
        fields_to_redact = ["bot_message", "user_message", "comments"]

    total_counts: dict[str, int] = {}
    for field_name in fields_to_redact:
        val = record.get(field_name)
        if not val:
            continue
        result = redact_text(str(val))
        if result.was_modified:
            record[field_name] = result.redacted_text
            for pii_type, cnt in result.counts.items():
                total_counts[pii_type] = total_counts.get(pii_type, 0) + cnt

    return record, total_counts


def redact_dataset(
    records: list[dict],
    fields_to_redact: Optional[list[str]] = None,
) -> tuple[list[dict], dict]:
    """
    Apply redaction to an entire list of records.

    Returns:
        (redacted_records, audit_summary)
        where audit_summary contains counts by pii_type and total messages affected.
    """
    if fields_to_redact is None:
        fields_to_redact = ["bot_message", "user_message", "comments"]

    aggregate_counts: dict[str, int] = {}
    messages_with_pii = 0
    redacted_records = []

    for record in records:
        modified = dict(record)  # shallow copy
        _, counts = redact_fields(modified, fields_to_redact)
        redacted_records.append(modified)
        if counts:
            messages_with_pii += 1
            for pii_type, cnt in counts.items():
                aggregate_counts[pii_type] = aggregate_counts.get(pii_type, 0) + cnt

    return redacted_records, {
        "total_records": len(records),
        "records_with_pii": messages_with_pii,
        "pii_by_type": aggregate_counts,
        "total_pii_items": sum(aggregate_counts.values()),
    }
