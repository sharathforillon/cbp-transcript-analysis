"""
Unit tests for src/ingestion/template_parser.py

Covers:
  - Plain text bot_message
  - welcome_template
  - quick_replies template
  - live_agent template (primary transfer signal)
  - SYSTEM template (primary transfer signal)
  - Malformed JSON (graceful degradation)
  - Empty / None input
  - Non-template JSON
  - is_transfer_signal detection
"""

import json
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.ingestion.template_parser import parse_bot_message, is_transfer_signal, ParsedMessage


# ---------------------------------------------------------------------------
# PLAIN TEXT
# ---------------------------------------------------------------------------

def test_plain_text_message():
    raw = "Your current balance is AED 12,450.00."
    msg = parse_bot_message(raw)
    assert msg.is_template is False
    assert msg.template_type is None
    assert msg.text == raw.strip()
    assert msg.quick_replies == []
    assert msg.parse_error is None


def test_plain_text_strips_whitespace():
    raw = "   Hello there.   "
    msg = parse_bot_message(raw)
    assert msg.text == "Hello there."


# ---------------------------------------------------------------------------
# WELCOME TEMPLATE
# ---------------------------------------------------------------------------

def test_welcome_template():
    payload = {
        "type": "template",
        "payload": {
            "template_type": "welcome_template",
            "text": "Welcome to Mashreq Bank. How can I help you today?",
        },
    }
    raw = json.dumps(payload)
    msg = parse_bot_message(raw)
    assert msg.is_template is True
    assert msg.template_type == "welcome_template"
    assert msg.text == "Welcome to Mashreq Bank. How can I help you today?"
    assert msg.quick_replies == []
    assert msg.parse_error is None
    assert msg.payload == payload["payload"]


def test_welcome_template_with_welcome_message_key():
    """Handles alternative 'welcome_message' key."""
    payload = {
        "type": "template",
        "payload": {
            "template_type": "welcome_template",
            "welcome_message": "Ahlan wa sahlan!",
        },
    }
    msg = parse_bot_message(json.dumps(payload))
    assert msg.text == "Ahlan wa sahlan!"


# ---------------------------------------------------------------------------
# QUICK REPLIES TEMPLATE
# ---------------------------------------------------------------------------

def test_quick_replies_string_options():
    payload = {
        "type": "template",
        "payload": {
            "template_type": "quick_replies",
            "text": "How can I help you today?",
            "quick_replies": ["Check Balance", "Card Services", "Speak to Agent"],
        },
    }
    msg = parse_bot_message(json.dumps(payload))
    assert msg.is_template is True
    assert msg.template_type == "quick_replies"
    assert msg.text == "How can I help you today?"
    assert msg.quick_replies == ["Check Balance", "Card Services", "Speak to Agent"]


def test_quick_replies_object_options():
    """Handles quick_replies as list of {title, payload} objects."""
    payload = {
        "type": "template",
        "payload": {
            "template_type": "quick_replies",
            "text": "Please choose an option:",
            "quick_replies": [
                {"title": "Check Balance", "payload": "BALANCE"},
                {"title": "Block Card", "payload": "BLOCK"},
            ],
        },
    }
    msg = parse_bot_message(json.dumps(payload))
    assert msg.quick_replies == ["Check Balance", "Block Card"]


# ---------------------------------------------------------------------------
# LIVE_AGENT TEMPLATE (transfer signal)
# ---------------------------------------------------------------------------

def test_live_agent_template():
    payload = {
        "type": "template",
        "payload": {
            "template_type": "live_agent",
            "text": "Please wait while we connect you to a Mashreq agent.",
        },
    }
    msg = parse_bot_message(json.dumps(payload))
    assert msg.is_template is True
    assert msg.template_type == "live_agent"
    assert "connect you to a Mashreq agent" in msg.text
    assert is_transfer_signal(msg) is True


# ---------------------------------------------------------------------------
# SYSTEM TEMPLATE (transfer signal)
# ---------------------------------------------------------------------------

def test_system_template():
    payload = {
        "type": "template",
        "payload": {
            "template_type": "SYSTEM",
            "text": "You are being transferred to the Mashreq Cards team.",
        },
    }
    msg = parse_bot_message(json.dumps(payload))
    assert msg.is_template is True
    assert msg.template_type == "SYSTEM"
    assert is_transfer_signal(msg) is True


# ---------------------------------------------------------------------------
# TRANSFER SIGNAL DETECTION
# ---------------------------------------------------------------------------

def test_plain_text_transfer_keyword_detected():
    raw = "Please hold while we are connecting you to an agent."
    msg = parse_bot_message(raw)
    assert msg.is_template is False
    assert is_transfer_signal(msg) is True


def test_non_transfer_plain_text_not_flagged():
    raw = "Your account balance is AED 5,200.00."
    msg = parse_bot_message(raw)
    assert is_transfer_signal(msg) is False


def test_custom_transfer_indicators():
    payload = {
        "type": "template",
        "payload": {"template_type": "CUSTOM_TRANSFER", "text": "Transferring..."},
    }
    msg = parse_bot_message(json.dumps(payload))
    # Not a transfer with default indicators
    assert is_transfer_signal(msg) is False
    # But IS a transfer with custom indicators
    assert is_transfer_signal(msg, transfer_indicators=["CUSTOM_TRANSFER"]) is True


# ---------------------------------------------------------------------------
# MALFORMED JSON — graceful degradation
# ---------------------------------------------------------------------------

def test_malformed_json_does_not_crash():
    raw = '{"type":"template","payload":{"template_type":"live_agent","text":"broken'
    msg = parse_bot_message(raw)
    # Should not raise; should return raw as text
    assert msg.is_template is False
    assert msg.parse_error is not None
    assert msg.text == raw  # falls back to raw string


def test_malformed_json_looks_like_python_dict():
    """Single-quoted JSON (Python repr) is invalid JSON — handle gracefully."""
    raw = "{'type':'template','payload':{'template_type':'welcome_template','text':'Hello'}}"
    msg = parse_bot_message(raw)
    # Python-style dicts are not valid JSON; parse_error should be set
    assert msg.parse_error is not None
    assert msg.text == raw


def test_malformed_json_partial_template():
    raw = '{"type":"template"}'  # No payload key
    msg = parse_bot_message(raw)
    # Parses OK as a template, but no text available — returns [template] fallback label
    assert msg.text == "[template]"
    assert msg.is_template is True
    assert msg.parse_error is None


# ---------------------------------------------------------------------------
# EDGE CASES
# ---------------------------------------------------------------------------

def test_empty_string():
    msg = parse_bot_message("")
    assert msg.text == ""
    assert msg.is_template is False


def test_none_input():
    msg = parse_bot_message(None)  # type: ignore[arg-type]
    assert msg.text == ""
    assert msg.is_template is False


def test_non_template_json_treated_as_plain_text():
    """JSON that is not a Kore.ai template should be treated as plain text."""
    raw = '{"status": "ok", "code": 200}'
    msg = parse_bot_message(raw)
    assert msg.is_template is False
    assert msg.template_type is None


def test_raw_preserved():
    """raw field always contains the original unmodified string."""
    raw = '{"type":"template","payload":{"template_type":"quick_replies","text":"Choose:","quick_replies":["A","B"]}}'
    msg = parse_bot_message(raw)
    assert msg.raw == raw


def test_no_text_field_falls_back_to_template_type():
    """If no text can be extracted, template_type is used as fallback."""
    payload = {
        "type": "template",
        "payload": {"template_type": "some_new_type"},
    }
    msg = parse_bot_message(json.dumps(payload))
    assert msg.text == "[some_new_type]"


def test_whitespace_only_text_falls_back():
    payload = {
        "type": "template",
        "payload": {
            "template_type": "welcome_template",
            "text": "   ",  # whitespace only — should not be used
        },
    }
    msg = parse_bot_message(json.dumps(payload))
    # text field is whitespace-only, so should fall through to next key or fallback
    # The parser strips and checks: "   ".strip() == "" → falsy → skip
    assert msg.text != "   "
