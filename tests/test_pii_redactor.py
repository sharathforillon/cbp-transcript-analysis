"""
Unit tests for src/redaction/pii_redactor.py

Covers each PII type, recall measurement, non-redaction of safe terms.
"""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.redaction.pii_redactor import (
    redact_text, redact_fields, redact_dataset,
    REDACT_EMIRATES_ID, REDACT_MOBILE, REDACT_CARD,
    REDACT_IBAN, REDACT_ACCOUNT, REDACT_EMAIL,
)


# ---------------------------------------------------------------------------
# EMIRATES ID
# ---------------------------------------------------------------------------

def test_emirates_id_standard_format():
    text = "My Emirates ID is 784-1990-1234567-8"
    result = redact_text(text)
    assert REDACT_EMIRATES_ID in result.redacted_text
    assert "784-1990-1234567-8" not in result.redacted_text
    assert result.counts.get("emirates_id") == 1


def test_emirates_id_no_dashes():
    text = "ID: 784199012345678"
    result = redact_text(text)
    assert REDACT_EMIRATES_ID in result.redacted_text


def test_multiple_emirates_ids():
    text = "Customer 784-1985-7654321-0 and spouse 784-1990-1111111-2"
    result = redact_text(text)
    assert result.counts.get("emirates_id") == 2
    assert "784" not in result.redacted_text


# ---------------------------------------------------------------------------
# MOBILE NUMBERS
# ---------------------------------------------------------------------------

def test_uae_mobile_plus_prefix():
    text = "Please call me on +971501234567"
    result = redact_text(text)
    assert REDACT_MOBILE in result.redacted_text
    assert "+971" not in result.redacted_text
    assert result.counts.get("mobile") == 1


def test_egypt_mobile():
    text = "My number is +201012345678"
    result = redact_text(text)
    assert REDACT_MOBILE in result.redacted_text


def test_pakistan_mobile():
    text = "Contact me at +923001234567"
    result = redact_text(text)
    assert REDACT_MOBILE in result.redacted_text


def test_mobile_with_spaces():
    text = "Call +971 50 123 4567 for more info"
    result = redact_text(text)
    assert REDACT_MOBILE in result.redacted_text


# ---------------------------------------------------------------------------
# CARD NUMBERS
# ---------------------------------------------------------------------------

def test_card_number_luhn_valid():
    # 4111111111111111 is a well-known test Visa number (Luhn valid)
    text = "My card number is 4111111111111111"
    result = redact_text(text)
    assert REDACT_CARD in result.redacted_text
    assert "4111111111111111" not in result.redacted_text


def test_card_number_with_spaces():
    text = "Card: 4111 1111 1111 1111"
    result = redact_text(text)
    assert REDACT_CARD in result.redacted_text


def test_card_number_luhn_invalid_not_redacted():
    # 1234567890123456 fails Luhn — should NOT be redacted as a card
    text = "Reference 1234567890123456 for your transaction"
    result = redact_text(text)
    # Should not be redacted as card (fails Luhn) — may be caught as account number
    assert REDACT_CARD not in result.redacted_text


# ---------------------------------------------------------------------------
# IBAN
# ---------------------------------------------------------------------------

def test_uae_iban():
    text = "Please transfer to IBAN AE070331234567890123456"
    result = redact_text(text)
    assert REDACT_IBAN in result.redacted_text
    assert "AE07" not in result.redacted_text
    assert result.counts.get("iban") == 1


def test_egypt_iban():
    text = "My Egyptian IBAN: EG380011000000000123456789"
    result = redact_text(text)
    assert REDACT_IBAN in result.redacted_text


def test_pakistan_iban():
    text = "Send to PK36SCBL0000001123456702"
    result = redact_text(text)
    assert REDACT_IBAN in result.redacted_text


# ---------------------------------------------------------------------------
# ACCOUNT NUMBERS
# ---------------------------------------------------------------------------

def test_account_number_10_digits():
    text = "Account number 1234567890 for your reference"
    result = redact_text(text)
    assert REDACT_ACCOUNT in result.redacted_text


def test_account_number_12_digits():
    text = "Please credit account 123456789012"
    result = redact_text(text)
    assert REDACT_ACCOUNT in result.redacted_text


# ---------------------------------------------------------------------------
# EMAIL
# ---------------------------------------------------------------------------

def test_email_redaction():
    text = "Send the statement to john.smith@example.com please"
    result = redact_text(text)
    assert REDACT_EMAIL in result.redacted_text
    assert "john.smith@example.com" not in result.redacted_text


# ---------------------------------------------------------------------------
# NON-REDACTION: SAFE TERMS
# ---------------------------------------------------------------------------

def test_merchant_name_not_redacted():
    """Merchant names must NOT be redacted — they are diagnostic."""
    text = "Pending charge from SPICY GRAND RESTAURANT for AED 347.00"
    result = redact_text(text)
    assert "SPICY GRAND RESTAURANT" in result.redacted_text
    assert result.total_redactions == 0


def test_aed_amount_not_redacted():
    text = "Your balance is AED 12,450.00"
    result = redact_text(text)
    assert "AED 12,450.00" in result.redacted_text


def test_generic_banking_terms_not_redacted():
    text = "Your credit card is due on the 15th of the month"
    result = redact_text(text)
    assert "credit card" in result.redacted_text
    assert result.total_redactions == 0


def test_short_numbers_not_redacted():
    """Short numbers (< 10 digits) should not be caught as account numbers."""
    text = "Your OTP is 123456 and is valid for 5 minutes"
    result = redact_text(text)
    assert "123456" in result.redacted_text  # 6 digits — not an account number


def test_reference_number_not_redacted_as_card():
    """REF numbers that don't pass Luhn should not be redacted as card numbers."""
    text = "Your reference is REF20261234567890"
    result = redact_text(text)
    assert REDACT_CARD not in result.redacted_text


# ---------------------------------------------------------------------------
# MULTIPLE PII IN ONE TEXT
# ---------------------------------------------------------------------------

def test_multiple_pii_types():
    text = (
        "Customer 784-1990-1234567-8 called from +971501234567. "
        "Card 4111111111111111. IBAN AE070331234567890123456."
    )
    result = redact_text(text)
    assert REDACT_EMIRATES_ID in result.redacted_text
    assert REDACT_MOBILE in result.redacted_text
    assert REDACT_CARD in result.redacted_text
    assert REDACT_IBAN in result.redacted_text
    assert result.total_redactions >= 4


# ---------------------------------------------------------------------------
# REDACT_FIELDS
# ---------------------------------------------------------------------------

def test_redact_fields_on_record():
    record = {
        "user_message": "My Emirates ID is 784-1990-1234567-8",
        "bot_message": "Thank you. How can I help?",
        "comments": "Call me on +971501234567",
    }
    modified, counts = redact_fields(record, ["user_message", "comments"])
    assert REDACT_EMIRATES_ID in modified["user_message"]
    assert REDACT_MOBILE in modified["comments"]
    assert modified["bot_message"] == "Thank you. How can I help?"  # untouched


# ---------------------------------------------------------------------------
# REDACT_DATASET
# ---------------------------------------------------------------------------

def test_redact_dataset_returns_counts():
    records = [
        {"user_message": "My ID is 784-1990-1234567-8", "bot_message": ""},
        {"user_message": "Clean message", "bot_message": ""},
        {"user_message": "My card 4111111111111111 is blocked", "bot_message": ""},
    ]
    redacted, audit = redact_dataset(records, ["user_message"])
    assert audit["records_with_pii"] == 2
    assert audit["total_pii_items"] >= 2
    assert "emirates_id" in audit["pii_by_type"] or "card_number" in audit["pii_by_type"]


# ---------------------------------------------------------------------------
# RECALL MEASUREMENT (seeded PII format validation)
# ---------------------------------------------------------------------------

def test_recall_all_seeded_pii_types():
    """
    Simulate a seeded record with all PII types present.
    All should be caught.
    """
    text = (
        "Name: Ahmed Al Mansoori. "
        "Emirates ID: 784-1985-7654321-0. "
        "Mobile: +971501234567. "
        "Card: 4111111111111111. "
        "IBAN: AE070331234567890123456. "
        "Account: 12345678901."
    )
    result = redact_text(text)
    pii_tokens = [REDACT_EMIRATES_ID, REDACT_MOBILE, REDACT_CARD, REDACT_IBAN]
    for token in pii_tokens:
        assert token in result.redacted_text, f"Expected {token} in redacted text"

    # At least 4 PII types caught
    assert result.total_redactions >= 4


def test_empty_text():
    result = redact_text("")
    assert result.redacted_text == ""
    assert result.total_redactions == 0


def test_none_text():
    result = redact_text(None)  # type: ignore[arg-type]
    assert result.redacted_text == ""
