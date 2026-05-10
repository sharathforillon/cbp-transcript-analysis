"""
template_parser.py — Kore.ai bot_message format parser.

Kore.ai stores bot_message as either:
  - Plain text: "Your balance is AED 12,450.00."
  - Stringified JSON template payload: '{"type":"template","payload":{...}}'

This module normalises both into a consistent ParsedMessage dataclass,
extracting human-visible text and preserving the template_type for downstream
transfer detection.

Template types handled:
  - welcome_template
  - quick_replies
  - live_agent         ← primary transfer signal
  - SYSTEM             ← primary transfer signal
  - (any unknown type) ← treated as plain text with a logged warning
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Optional

logger = logging.getLogger(__name__)


@dataclass
class ParsedMessage:
    """Normalised representation of a Kore.ai bot_message."""

    raw: str                           # Original bot_message string (unchanged)
    is_template: bool                  # True if the message was a JSON template
    template_type: Optional[str]       # e.g. "live_agent", "SYSTEM", "quick_replies"
    text: str                          # Human-visible text extracted from the message
    quick_replies: list[str]           # Populated for quick_replies templates; else []
    payload: Optional[dict[str, Any]]  # Full parsed payload (for debugging); else None
    parse_error: Optional[str]         # Non-None if JSON parse failed gracefully


def parse_bot_message(raw: str) -> ParsedMessage:
    """
    Parse a single Kore.ai bot_message string into a ParsedMessage.

    Args:
        raw: The raw string from the bot_message column / field.

    Returns:
        ParsedMessage with text extracted and template_type set.
        Never raises — malformed JSON is handled gracefully (logged, treated as plain text).
    """
    if not raw or not isinstance(raw, str):
        return ParsedMessage(
            raw=raw or "",
            is_template=False,
            template_type=None,
            text=raw or "",
            quick_replies=[],
            payload=None,
            parse_error=None,
        )

    stripped = raw.strip()

    # Fast heuristic: JSON template payloads start with '{' and contain "template"
    if not (stripped.startswith("{") and "template" in stripped):
        return _plain_text_message(raw)

    # Attempt JSON parse
    try:
        obj = json.loads(stripped)
    except json.JSONDecodeError as exc:
        logger.warning(
            "template_parser: JSON decode failed for message starting with '%s...': %s",
            stripped[:60],
            exc,
        )
        return ParsedMessage(
            raw=raw,
            is_template=False,
            template_type=None,
            text=raw,  # Fall back: use raw string as text
            quick_replies=[],
            payload=None,
            parse_error=str(exc),
        )

    # Handle non-template JSON (e.g. {"status": "ok"}) — treat as plain text
    if not isinstance(obj, dict) or obj.get("type") != "template":
        return _plain_text_message(raw)

    payload = obj.get("payload", {})
    if not isinstance(payload, dict):
        logger.warning("template_parser: payload is not a dict in message: %s...", stripped[:60])
        return _plain_text_message(raw)

    template_type = payload.get("template_type")
    text = _extract_text(payload, template_type)
    quick_replies = _extract_quick_replies(payload, template_type)

    return ParsedMessage(
        raw=raw,
        is_template=True,
        template_type=template_type,
        text=text,
        quick_replies=quick_replies,
        payload=payload,
        parse_error=None,
    )


def _extract_text(payload: dict[str, Any], template_type: Optional[str]) -> str:
    """Extract the human-visible text from a parsed payload dict."""

    # Most templates carry a top-level "text" field
    if "text" in payload and isinstance(payload["text"], str) and payload["text"].strip():
        return payload["text"].strip()

    # welcome_template may use "welcome_message" or "message"
    for key in ("welcome_message", "message", "body", "content"):
        if key in payload and isinstance(payload[key], str) and payload[key].strip():
            return payload[key].strip()

    # Fallback: template_type itself as a descriptor
    if template_type:
        logger.debug("template_parser: no text field found for template_type=%s", template_type)
        return f"[{template_type}]"

    return "[template]"


def _extract_quick_replies(payload: dict[str, Any], template_type: Optional[str]) -> list[str]:
    """Extract quick reply options from a quick_replies template."""
    if template_type != "quick_replies":
        return []

    qrs = payload.get("quick_replies", [])
    result = []
    for item in qrs:
        if isinstance(item, str):
            result.append(item)
        elif isinstance(item, dict):
            # Kore.ai quick_replies may be {title: ..., payload: ...} objects
            title = item.get("title") or item.get("text") or item.get("value") or str(item)
            result.append(str(title))
    return result


def _plain_text_message(raw: str) -> ParsedMessage:
    """Construct a ParsedMessage for a non-template plain-text bot_message."""
    return ParsedMessage(
        raw=raw,
        is_template=False,
        template_type=None,
        text=raw.strip(),
        quick_replies=[],
        payload=None,
        parse_error=None,
    )


def is_transfer_signal(parsed: ParsedMessage, transfer_indicators: Optional[list[str]] = None) -> bool:
    """
    Return True if this parsed message indicates a human handoff.

    Args:
        parsed: A ParsedMessage as returned by parse_bot_message().
        transfer_indicators: List of template_types that signal a transfer.
                             Defaults to ["live_agent", "SYSTEM"].
    """
    if transfer_indicators is None:
        transfer_indicators = ["live_agent", "SYSTEM"]

    if parsed.is_template and parsed.template_type in transfer_indicators:
        return True

    # Keyword fallback for plain-text messages
    transfer_keywords = [
        "connecting you to an agent",
        "transferring you",
        "transfer you to",
        "live agent",
        "human agent",
        "agent will assist",
        "representative will",
    ]
    text_lower = parsed.text.lower()
    return any(kw in text_lower for kw in transfer_keywords)
