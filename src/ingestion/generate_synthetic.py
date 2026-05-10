"""
generate_synthetic.py — Synthetic transcript and CSAT data generator.

Produces realistic-but-fake Mashreq Bank chatbot conversation data for pipeline
development and testing. ALL output records carry _synthetic: true.

Output files:
  data/synthetic/sample/transcripts.csv  (500 sessions)
  data/synthetic/sample/csat.csv
  data/synthetic/full/transcripts.csv    (50,000 sessions — only when --full is passed)
  data/synthetic/full/csat.csv

Usage:
  python -m src.ingestion.generate_synthetic --sessions 500 --output sample
  python -m src.ingestion.generate_synthetic --sessions 50000 --output full

WARNING: This is SYNTHETIC DATA. It does not represent real customers.
Every record carries _synthetic=True. The README prominently states this.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import logging
import os
import random
import re
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# CONSTANTS
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).parent.parent.parent
DATA_DIR = REPO_ROOT / "data"

START_DATE = datetime(2025, 11, 1, tzinfo=timezone.utc)
END_DATE = datetime(2026, 4, 30, 23, 59, 59, tzinfo=timezone.utc)
TOTAL_DAYS = (END_DATE - START_DATE).days

# Pareto: these journey IDs carry disproportionate traffic
HIGH_VOLUME_JOURNEYS = [
    "ACCT_001", "ACCT_002", "ACCT_003", "CARD_001", "CARD_011",
    "CARD_012", "CARD_004", "REMI_003", "REMI_001", "REMI_002",
    "DIGI_001", "LOAN_001", "ACCT_005", "CARD_008", "DIGI_004",
]

ALL_JOURNEY_IDS = [
    # Cards
    "CARD_001","CARD_002","CARD_003","CARD_004","CARD_005",
    "CARD_006","CARD_007","CARD_008","CARD_009","CARD_010",
    "CARD_011","CARD_012","CARD_013","CARD_014","CARD_015",
    # Loans
    "LOAN_001","LOAN_002","LOAN_003","LOAN_004","LOAN_005",
    "LOAN_006","LOAN_007","LOAN_008","LOAN_009","LOAN_010",
    "LOAN_011","LOAN_012","LOAN_013","LOAN_014","LOAN_015",
    # Accounts
    "ACCT_001","ACCT_002","ACCT_003","ACCT_004","ACCT_005",
    "ACCT_006","ACCT_007","ACCT_008","ACCT_009","ACCT_010",
    "ACCT_011","ACCT_012","ACCT_013","ACCT_014","ACCT_015",
    # Wealth
    "WLTH_001","WLTH_002","WLTH_003","WLTH_004","WLTH_005",
    "WLTH_006","WLTH_007","WLTH_008","WLTH_009","WLTH_010",
    # Remittances
    "REMI_001","REMI_002","REMI_003","REMI_004","REMI_005",
    "REMI_006","REMI_007","REMI_008","REMI_009","REMI_010",
    # Digital
    "DIGI_001","DIGI_002","DIGI_003","DIGI_004","DIGI_005",
    "DIGI_006","DIGI_007","DIGI_008","DIGI_009","DIGI_010",
    # Complaints
    "COMP_001","COMP_002","COMP_003","COMP_004","COMP_005",
    # Wellness
    "WELL_001","WELL_002","WELL_003","WELL_004","WELL_005",
    # Internal
    "INTL_001","INTL_002","INTL_003","INTL_004","INTL_005",
]

JOURNEY_NAMES = {
    "CARD_001": "Credit Card Balance Enquiry",
    "CARD_002": "Credit Card Statement Request",
    "CARD_003": "Credit Card Limit Enhancement Request",
    "CARD_004": "Credit Card Block / Replacement",
    "CARD_005": "Credit Card Instalment Plan Setup",
    "CARD_006": "Credit Card Reward Points Redemption",
    "CARD_007": "Credit Card Transaction Dispute",
    "CARD_008": "Debit Card Block / Replacement",
    "CARD_009": "Debit Card PIN Reset",
    "CARD_010": "Supplementary Card Request",
    "CARD_011": "Credit Card Payment Due Date Enquiry",
    "CARD_012": "Credit Card Minimum Payment Enquiry",
    "CARD_013": "Credit Card International Usage Activation",
    "CARD_014": "Credit Card Annual Fee Waiver Request",
    "CARD_015": "Credit Card Closure Request",
    "LOAN_001": "Personal Loan Balance Enquiry",
    "LOAN_002": "Personal Loan Statement Request",
    "LOAN_003": "Personal Loan Eligibility Enquiry",
    "LOAN_004": "Personal Loan Application Status",
    "LOAN_005": "Personal Loan Early Settlement Enquiry",
    "LOAN_006": "Personal Loan Restructuring Request",
    "LOAN_007": "Personal Loan EMI Deferral Request",
    "LOAN_008": "Auto Loan Balance Enquiry",
    "LOAN_009": "Auto Loan Early Settlement",
    "LOAN_010": "Home Loan Balance Enquiry",
    "LOAN_011": "Home Loan NOC Request",
    "LOAN_012": "Loan Insurance Enquiry",
    "LOAN_013": "Loan Top-Up Eligibility Enquiry",
    "LOAN_014": "Loan Payment History Request",
    "LOAN_015": "Loan Pre-closure Certificate Request",
    "ACCT_001": "Current Account Balance Enquiry",
    "ACCT_002": "Savings Account Balance Enquiry",
    "ACCT_003": "Account Statement Request",
    "ACCT_004": "Account Closure Request",
    "ACCT_005": "IBAN Enquiry",
    "ACCT_006": "Cheque Book Request",
    "ACCT_007": "Cheque Status Enquiry",
    "ACCT_008": "Account Freeze / Unfreeze Enquiry",
    "ACCT_009": "Standing Order Setup / Cancellation",
    "ACCT_010": "Direct Debit Enquiry",
    "ACCT_011": "Account Interest Rate Enquiry",
    "ACCT_012": "Salary Account Conversion Request",
    "ACCT_013": "Joint Account Holder Addition",
    "ACCT_014": "Account Upgrade Request",
    "ACCT_015": "Overdraft Facility Enquiry",
    "WLTH_001": "Investment Portfolio Balance Enquiry",
    "WLTH_002": "Fixed Deposit Maturity Enquiry",
    "WLTH_003": "Fixed Deposit Renewal Request",
    "WLTH_004": "Fixed Deposit Premature Withdrawal Request",
    "WLTH_005": "Mutual Fund Portfolio Enquiry",
    "WLTH_006": "Wealth Management Advisory Appointment",
    "WLTH_007": "Sukuk / Bond Enquiry",
    "WLTH_008": "Gold Savings Plan Enquiry",
    "WLTH_009": "Insurance Product Enquiry",
    "WLTH_010": "Structured Product Enquiry",
    "REMI_001": "International Remittance Enquiry",
    "REMI_002": "International Remittance Status Tracking",
    "REMI_003": "Exchange Rate Enquiry",
    "REMI_004": "Beneficiary Addition / Management",
    "REMI_005": "Remittance Recall / Cancellation Request",
    "REMI_006": "WPS (Wage Protection System) Enquiry",
    "REMI_007": "Instant Transfer (UAEFTS) Status Enquiry",
    "REMI_008": "Transfer Limit Enquiry",
    "REMI_009": "Standing Remittance Order Enquiry",
    "REMI_010": "Remittance Fee Enquiry",
    "DIGI_001": "Mobile App Login / Access Issue",
    "DIGI_002": "Mobile App Password Reset",
    "DIGI_003": "Internet Banking Registration",
    "DIGI_004": "OTP / Authentication Issues",
    "DIGI_005": "Mobile App Feature Navigation Help",
    "DIGI_006": "Cardless Withdrawal Enquiry",
    "DIGI_007": "Digital Wallet Setup (Apple Pay / Google Pay)",
    "DIGI_008": "E-statement Registration",
    "DIGI_009": "Push Notification / Alert Setup",
    "DIGI_010": "Device Management / Trusted Device Issues",
    "COMP_001": "Complaint Registration — General",
    "COMP_002": "Complaint Status Followup",
    "COMP_003": "Fraudulent Transaction Report",
    "COMP_004": "Unauthorized Account Access Report",
    "COMP_005": "Service Quality Feedback",
    "WELL_001": "Financial Health Check Enquiry",
    "WELL_002": "Spending Analysis Request",
    "WELL_003": "Budget Planning Assistance",
    "WELL_004": "Savings Goal Setup",
    "WELL_005": "Product Recommendation Enquiry",
    "INTL_001": "Branch Locator",
    "INTL_002": "ATM Locator",
    "INTL_003": "Contact Centre Hours Enquiry",
    "INTL_004": "General Banking FAQ",
    "INTL_005": "Unknown / Out of Scope",
}

# Per-journey failure mode profiles — some journeys skew heavily to one failure mode
JOURNEY_FAILURE_PROFILES: dict[str, dict[str, float]] = {
    # Account closure: Reddy's canonical pattern — heavily bot_has_data_communicates_poorly
    "ACCT_004": {
        "bot_has_data_communicates_poorly": 0.55,
        "policy_routed": 0.20,
        "customer_demanded_human": 0.10,
        "bot_fallback_exhausted": 0.05,
        "trust_or_ux_failure": 0.05,
        "backend_integration_gap": 0.03,
        "silent_failure": 0.02,
    },
    # Authentication journeys: heavy authentication friction
    "DIGI_001": {
        "authentication_friction": 0.45,
        "bot_fallback_exhausted": 0.20,
        "customer_demanded_human": 0.15,
        "trust_or_ux_failure": 0.10,
        "backend_integration_gap": 0.10,
    },
    "DIGI_004": {
        "authentication_friction": 0.60,
        "bot_fallback_exhausted": 0.15,
        "customer_demanded_human": 0.15,
        "trust_or_ux_failure": 0.10,
    },
    "CARD_009": {
        "authentication_friction": 0.50,
        "customer_demanded_human": 0.20,
        "bot_fallback_exhausted": 0.15,
        "trust_or_ux_failure": 0.15,
    },
    # Regulatory journeys: heavy policy routing
    "LOAN_006": {
        "policy_routed": 0.80,
        "customer_demanded_human": 0.10,
        "bot_fallback_exhausted": 0.10,
    },
    "LOAN_007": {
        "policy_routed": 0.75,
        "customer_demanded_human": 0.15,
        "bot_fallback_exhausted": 0.10,
    },
    "ACCT_008": {
        "policy_routed": 0.85,
        "customer_demanded_human": 0.10,
        "bot_fallback_exhausted": 0.05,
    },
    "COMP_003": {
        "policy_routed": 0.50,
        "customer_demanded_human": 0.30,
        "trust_or_ux_failure": 0.20,
    },
    # Exchange rate: simple enquiry, high language/dialect failure
    "REMI_003": {
        "language_dialect_gap": 0.30,
        "bot_fallback_exhausted": 0.25,
        "trust_or_ux_failure": 0.20,
        "customer_demanded_human": 0.15,
        "bot_has_data_communicates_poorly": 0.10,
    },
    # Backend integration gaps for transfer journeys
    "REMI_005": {
        "backend_integration_gap": 0.45,
        "policy_routed": 0.30,
        "customer_demanded_human": 0.15,
        "bot_fallback_exhausted": 0.10,
    },
    # Card block: high volume, backend integration gap common
    "CARD_004": {
        "backend_integration_gap": 0.30,
        "bot_has_data_communicates_poorly": 0.25,
        "customer_demanded_human": 0.20,
        "bot_fallback_exhausted": 0.15,
        "trust_or_ux_failure": 0.10,
    },
    # Transaction dispute: complex, high human demand
    "CARD_007": {
        "customer_demanded_human": 0.40,
        "policy_routed": 0.30,
        "trust_or_ux_failure": 0.15,
        "bot_fallback_exhausted": 0.15,
    },
}

# Default failure mode distribution for journeys without a specific profile
DEFAULT_FAILURE_PROFILE = {
    "bot_fallback_exhausted": 0.20,
    "customer_demanded_human": 0.15,
    "policy_routed": 0.10,
    "silent_failure": 0.10,
    "bot_has_data_communicates_poorly": 0.15,
    "trust_or_ux_failure": 0.10,
    "backend_integration_gap": 0.10,
    "authentication_friction": 0.05,
    "language_dialect_gap": 0.05,
}

# Transfer (escalation) rate per journey (fraction of sessions that escalate)
JOURNEY_ESCALATION_RATES: dict[str, float] = {
    "ACCT_001": 0.08, "ACCT_002": 0.08, "ACCT_003": 0.12, "ACCT_004": 0.65,
    "ACCT_005": 0.05, "ACCT_006": 0.18, "ACCT_007": 0.22, "ACCT_008": 0.85,
    "ACCT_009": 0.25, "ACCT_010": 0.20, "ACCT_011": 0.10, "ACCT_012": 0.40,
    "ACCT_013": 0.70, "ACCT_014": 0.35, "ACCT_015": 0.55,
    "CARD_001": 0.07, "CARD_002": 0.12, "CARD_003": 0.45, "CARD_004": 0.30,
    "CARD_005": 0.28, "CARD_006": 0.15, "CARD_007": 0.72, "CARD_008": 0.25,
    "CARD_009": 0.48, "CARD_010": 0.40, "CARD_011": 0.06, "CARD_012": 0.06,
    "CARD_013": 0.20, "CARD_014": 0.50, "CARD_015": 0.60,
    "LOAN_001": 0.07, "LOAN_002": 0.12, "LOAN_003": 0.38, "LOAN_004": 0.22,
    "LOAN_005": 0.42, "LOAN_006": 0.88, "LOAN_007": 0.82, "LOAN_008": 0.08,
    "LOAN_009": 0.45, "LOAN_010": 0.10, "LOAN_011": 0.65, "LOAN_012": 0.35,
    "LOAN_013": 0.32, "LOAN_014": 0.10, "LOAN_015": 0.28,
    "WLTH_001": 0.18, "WLTH_002": 0.15, "WLTH_003": 0.30, "WLTH_004": 0.45,
    "WLTH_005": 0.35, "WLTH_006": 0.20, "WLTH_007": 0.40, "WLTH_008": 0.22,
    "WLTH_009": 0.50, "WLTH_010": 0.70,
    "REMI_001": 0.15, "REMI_002": 0.18, "REMI_003": 0.12, "REMI_004": 0.35,
    "REMI_005": 0.55, "REMI_006": 0.25, "REMI_007": 0.10, "REMI_008": 0.12,
    "REMI_009": 0.15, "REMI_010": 0.08,
    "DIGI_001": 0.52, "DIGI_002": 0.45, "DIGI_003": 0.38, "DIGI_004": 0.58,
    "DIGI_005": 0.12, "DIGI_006": 0.18, "DIGI_007": 0.22, "DIGI_008": 0.10,
    "DIGI_009": 0.08, "DIGI_010": 0.40,
    "COMP_001": 0.45, "COMP_002": 0.20, "COMP_003": 0.80, "COMP_004": 0.90,
    "COMP_005": 0.15,
    "WELL_001": 0.25, "WELL_002": 0.15, "WELL_003": 0.22, "WELL_004": 0.18,
    "WELL_005": 0.55,
    "INTL_001": 0.03, "INTL_002": 0.03, "INTL_003": 0.04, "INTL_004": 0.10,
    "INTL_005": 0.50,
}

# ---------------------------------------------------------------------------
# PII GENERATION
# ---------------------------------------------------------------------------

UAE_FIRST_NAMES = [
    "Mohammed", "Ahmed", "Ali", "Omar", "Khalid", "Saeed", "Hamdan", "Rashid",
    "Fatima", "Mariam", "Aisha", "Hessa", "Latifa", "Noura", "Maitha", "Sara",
    "James", "Michael", "Sarah", "Emma", "David", "Robert", "Jennifer", "Lisa",
    "Priya", "Rahul", "Ananya", "Vikram", "Deepak", "Suresh", "Kavita", "Anjali",
]
UAE_LAST_NAMES = [
    "Al Mansoori", "Al Maktoum", "Al Nahyan", "Al Rashidi", "Al Shamsi",
    "Al Mazrouei", "Al Kaabi", "Al Dhaheri", "Smith", "Johnson", "Patel",
    "Kumar", "Singh", "Sharma", "Williams", "Brown",
]
EGYPT_FIRST_NAMES = [
    "Mohamed", "Ahmed", "Khaled", "Mahmoud", "Youssef", "Hassan", "Ibrahim",
    "Fatma", "Nour", "Rana", "Dina", "Hana", "Marwa", "Amira", "Sara", "Yasmine",
]
EGYPT_LAST_NAMES = [
    "El-Sayed", "Mohamed", "Ahmed", "Ibrahim", "Hassan", "Mahmoud", "Ali",
    "Khalil", "Mansour", "Farouk", "Nasser", "Saad", "Zaki", "Amin",
]
PAKISTAN_FIRST_NAMES = [
    "Muhammad", "Ahmed", "Ali", "Hassan", "Usman", "Imran", "Bilal", "Tariq",
    "Fatima", "Ayesha", "Zainab", "Hina", "Sana", "Nadia", "Rabia", "Asma",
]
PAKISTAN_LAST_NAMES = [
    "Khan", "Ahmed", "Ali", "Malik", "Chaudhry", "Iqbal", "Hussain", "Sheikh",
    "Mirza", "Qureshi", "Siddiqui", "Baig", "Rizvi", "Butt", "Raza",
]

MERCHANT_NAMES = [
    "SPICY GRAND RESTAURANT", "AL MANZIL HOTEL", "CARREFOUR HYPERMARKET",
    "DUBAI MALL PARKING", "EMIRATES AIRLINES", "ETISALAT UAE",
    "NOON.COM", "AMAZON AE", "TALABAT DELIVERY", "DELIVEROO UAE",
    "LULU HYPERMARKET", "IKEA UAE", "H&M FASHION", "ZARA DUBAI",
    "DAMAS JEWELLERY", "EMIRATES NBD ATM", "ADIB POS", "PARK N SHOP",
    "SHAWARMA PALACE", "COSTA COFFEE DUBAI", "STARBUCKS UAE",
    "DUBAI ELECTRICITY (DEWA)", "SALIK TOLL", "RTA TRANSPORT",
    "AL NAHDA PHARMACY", "LIFE PHARMACY UAE", "ASTER CLINIC",
    "MASHREQ BANK FEE", "DU TELECOM", "VIRGIN MEGASTORE",
]


def _luhn_checksum(card_number: str) -> bool:
    """Verify Luhn checksum for a card number string."""
    digits = [int(d) for d in card_number]
    digits.reverse()
    total = 0
    for i, d in enumerate(digits):
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


def generate_card_number() -> str:
    """Generate a 16-digit card number passing Luhn checksum. Prefix 4111 (Visa-style)."""
    prefix = "4111"
    while True:
        middle = "".join([str(random.randint(0, 9)) for _ in range(11)])
        partial = prefix + middle
        # Compute check digit
        check = (10 - sum(
            (int(d) * 2 - 9 if i % 2 == 0 and int(d) * 2 > 9 else int(d) * 2 if i % 2 == 0 else int(d))
            for i, d in enumerate(reversed(partial))
        ) % 10) % 10
        candidate = partial + str(check)
        if _luhn_checksum(candidate):
            return candidate


def generate_emirates_id() -> str:
    """Generate a plausible UAE Emirates ID: 784-YYYY-XXXXXXX-C format."""
    year = random.randint(1960, 2005)
    mid = "".join([str(random.randint(0, 9)) for _ in range(7)])
    check = random.randint(0, 9)
    return f"784-{year}-{mid}-{check}"


def generate_mobile(geography: str) -> str:
    """Generate a mobile number with the correct country prefix."""
    if geography == "UAE":
        prefix = "+971"
        number = f"5{random.randint(0,9)}{random.randint(1000000,9999999)}"
    elif geography == "Egypt":
        prefix = "+20"
        number = f"1{random.randint(0,2)}{random.randint(10000000,99999999)}"
    else:  # Pakistan
        prefix = "+92"
        number = f"3{random.randint(0,4)}{random.randint(10000000,99999999)}"
    return f"{prefix}{number}"


def generate_iban(geography: str) -> str:
    """Generate a plausible IBAN for the given geography."""
    if geography == "UAE":
        bban = "".join([str(random.randint(0, 9)) for _ in range(19)])
        return f"AE{random.randint(10,99)}{bban}"
    elif geography == "Egypt":
        bban = "".join([str(random.randint(0, 9)) for _ in range(25)])
        return f"EG{random.randint(10,99)}{bban}"
    else:  # Pakistan
        bban = "".join([str(random.randint(0, 9)) for _ in range(20)])
        return f"PK{random.randint(10,99)}{bban}"


def generate_account_number() -> str:
    """Generate a Mashreq-style account number (10-12 digits)."""
    return "".join([str(random.randint(0, 9)) for _ in range(random.choice([10, 11, 12]))])


def get_name(geography: str) -> str:
    if geography == "UAE":
        return f"{random.choice(UAE_FIRST_NAMES)} {random.choice(UAE_LAST_NAMES)}"
    elif geography == "Egypt":
        return f"{random.choice(EGYPT_FIRST_NAMES)} {random.choice(EGYPT_LAST_NAMES)}"
    else:
        return f"{random.choice(PAKISTAN_FIRST_NAMES)} {random.choice(PAKISTAN_LAST_NAMES)}"


def get_language(geography: str) -> str:
    if geography == "UAE":
        return random.choices(["en", "ar", "ur"], weights=[0.75, 0.20, 0.05])[0]
    elif geography == "Egypt":
        return random.choices(["ar", "en"], weights=[0.70, 0.30])[0]
    else:
        return random.choices(["ur", "en"], weights=[0.60, 0.40])[0]


# ---------------------------------------------------------------------------
# CONVERSATION GENERATION
# ---------------------------------------------------------------------------

@dataclass
class Session:
    session_id: str
    user_id: str
    geography: str
    language: str
    channel: str
    journey_id: str
    journey_name: str
    failure_mode: Optional[str]
    is_escalated: bool
    start_time: datetime
    customer_name: str
    pii_items: dict  # seeded PII for redactor testing
    messages: list[dict] = field(default_factory=list)


def _make_session_id(user_id: str, ts: datetime) -> str:
    raw = f"{user_id}_{ts.isoformat()}"
    return "sess-" + hashlib.md5(raw.encode()).hexdigest()[:16]


def _make_user_id() -> str:
    return "u-" + hashlib.md5(str(random.random()).encode()).hexdigest()[:26]


CHANNELS = ["Web/Mobile Client", "WhatsApp", "IVR", "Internet Banking"]
CHANNEL_WEIGHTS = [0.60, 0.20, 0.10, 0.10]


def _random_channel() -> str:
    return random.choices(CHANNELS, weights=CHANNEL_WEIGHTS)[0]


def _random_timestamp() -> datetime:
    """Uniform random timestamp in the 6-month window."""
    offset_seconds = random.randint(0, TOTAL_DAYS * 86400)
    ts = START_DATE + timedelta(seconds=offset_seconds)
    # Add business-hour weighting: 8am-10pm Gulf time more likely
    return ts


def _format_ts(ts: datetime) -> str:
    return ts.strftime("%Y-%m-%d %H:%M:%S")


# ---------------------------------------------------------------------------
# CONVERSATION TEMPLATES PER JOURNEY + FAILURE MODE
# ---------------------------------------------------------------------------

def build_conversation(session: Session) -> list[dict]:
    """
    Generate a realistic multi-turn conversation for the given session.
    Returns a list of message dicts.
    """
    msgs = []
    ts = session.start_time
    pii = session.pii_items
    j = session.journey_id
    fm = session.failure_mode
    lang = session.language
    name = session.customer_name

    def add_bot(text: str, template_type: Optional[str] = None, turn_offset_secs: int = 30):
        nonlocal ts
        ts = ts + timedelta(seconds=turn_offset_secs)
        if template_type:
            payload = json.dumps({
                "type": "template",
                "payload": {"template_type": template_type, "text": text}
            })
        else:
            payload = text
        msgs.append({
            "bot_name": "SmartAssist Instance",
            "user_id": session.user_id,
            "session_id": session.session_id,
            "channel": session.channel,
            "language": lang,
            "bot_message": payload,
            "user_message": "",
            "timestamp": _format_ts(ts),
            "_synthetic": True,
        })

    def add_user(text: str, turn_offset_secs: int = 45):
        nonlocal ts
        ts = ts + timedelta(seconds=turn_offset_secs)
        msgs.append({
            "bot_name": "SmartAssist Instance",
            "user_id": session.user_id,
            "session_id": session.session_id,
            "channel": session.channel,
            "language": lang,
            "bot_message": "",
            "user_message": text,
            "timestamp": _format_ts(ts),
            "_synthetic": True,
        })

    def add_transfer(reason_text: str):
        add_bot(reason_text, template_type="live_agent")
        add_bot(
            f"Transferring you to our {_journey_team(j)} team. Average wait time: 3-5 minutes.",
            template_type="SYSTEM"
        )

    def add_quick_replies(text: str, options: list[str]):
        payload = json.dumps({
            "type": "template",
            "payload": {
                "template_type": "quick_replies",
                "text": text,
                "quick_replies": options,
            }
        })
        nonlocal ts
        ts = ts + timedelta(seconds=30)
        msgs.append({
            "bot_name": "SmartAssist Instance",
            "user_id": session.user_id,
            "session_id": session.session_id,
            "channel": session.channel,
            "language": lang,
            "bot_message": payload,
            "user_message": "",
            "timestamp": _format_ts(ts),
            "_synthetic": True,
        })

    # --- WELCOME ---
    add_bot(
        "Welcome to Mashreq Bank. How can I assist you today?",
        template_type="welcome_template"
    )
    add_quick_replies(
        "Please select a category:",
        ["Account Services", "Card Services", "Loans", "Remittances", "Other"]
    )

    # --- CUSTOMER DEMANDED HUMAN (early exit) ---
    if fm == "customer_demanded_human":
        demands = [
            "I need to speak to an agent",
            "Connect me to a human please",
            "I want to talk to someone",
            "Agent please",
            "Transfer me to customer service",
        ]
        if lang == "ar":
            demands += ["أريد التحدث مع موظف", "وصلني بممثل خدمة العملاء"]
        elif lang == "ur":
            demands += ["مجھے ایجنٹ سے بات کرنی ہے", "انسانی نمائندے سے ملائیں"]
        add_user(random.choice(demands))
        add_bot("I understand you'd prefer to speak with one of our team members.")
        add_transfer("Connecting you to a live agent as requested.")
        if random.random() < 0.5:
            add_bot(
                f"Hello, this is {_agent_name()}. How can I assist you today?",
                turn_offset_secs=180
            )
            add_user(f"Yes, I wanted to {_journey_intent_short(j)} but the bot couldn't help.")
            add_bot(
                f"Of course, I can help with that. Let me pull up your account. Can you confirm your Emirates ID?",
                turn_offset_secs=30
            )
        return msgs

    # --- JOURNEY-SPECIFIC FLOWS ---
    # ACCOUNT CLOSURE — Reddy's canonical example
    if j == "ACCT_004":
        add_user("I want to close my savings account")
        add_bot("I can help with account closure. Let me check your account status.")
        if fm == "bot_has_data_communicates_poorly":
            # Bot KNOWS about the block but communicates poorly
            add_bot(
                "There is a balance in your account. Account closure cannot be processed at this time. "
                "Please contact us at a later time or visit a branch.",
            )
            add_user("What does that mean? I have checked and my balance is zero")
            add_bot("Your account cannot be closed at this time. Please try again later or visit a branch for assistance.")
            add_user("This is not helpful. Let me talk to an agent.")
            add_transfer("Connecting you with a specialist who can explain the account closure process.")
            # Agent post-handoff — knows the actual reason
            amount = round(random.uniform(50, 800), 2)
            merchant = random.choice(MERCHANT_NAMES)
            add_bot(
                f"Hello, I'm {_agent_name()} from Mashreq Bank. I can see why the bot couldn't explain this clearly. "
                f"Your account has a pending merchant claim of AED {amount:.2f} from {merchant}. "
                f"This is an authorization hold and not an actual balance — it will clear automatically within 3-5 business days. "
                f"Once cleared, your account will be ready for closure. I'll add a note to your account.",
                turn_offset_secs=210
            )
            add_user("Oh I see, that makes sense. So it will clear on its own?")
            add_bot(
                f"Yes, exactly. The {merchant} hold will expire automatically. "
                f"I'd recommend calling back in 5 business days or checking the app — the hold will show as cleared. "
                f"Is there anything else I can help you with?",
                turn_offset_secs=30
            )
        else:
            add_user("My balance is zero, why can't you close it?")
            add_bot("I'll connect you to a specialist who can assist with this request.")
            add_transfer("Connecting you to our accounts team.")
        return msgs

    # AUTHENTICATION FRICTION
    if fm == "authentication_friction":
        add_user(_journey_intent(j, pii))
        add_bot("To process this request, I need to verify your identity. Please provide your Emirates ID or mobile number.")
        add_user(f"My mobile is {pii['mobile']}")
        add_bot("Thank you. I've sent an OTP to your registered mobile number. Please enter it here.")
        add_user("I did not receive any OTP")
        add_bot("Please wait a moment and try again. Make sure your number ends in " + pii['mobile'][-4:])
        add_user("Still nothing. My number is correct.")
        add_bot("I'm sorry for the inconvenience. Let me try an alternative verification method. Please provide your Emirates ID.")
        add_user(f"My Emirates ID is {pii['emirates_id']}")
        add_bot("I'm unable to verify your identity at this time. For security purposes, I need to transfer you to an agent.")
        add_transfer("Transferring you to our authentication support team.")
        add_bot(
            f"Hi, this is {_agent_name()}. I can see you're having trouble with OTP. "
            f"Can you confirm your registered email address so I can verify your identity?",
            turn_offset_secs=200
        )
        return msgs

    # BOT FALLBACK EXHAUSTED
    if fm == "bot_fallback_exhausted":
        add_user(_journey_intent(j, pii))
        for i in range(random.randint(2, 4)):
            add_bot("I'm sorry, I didn't quite understand that. Could you please rephrase your request?")
            variations = _rephrase_intent(j, lang, i)
            add_user(variations)
        add_bot("I'm having trouble understanding your request. Let me connect you to an agent who can assist you better.")
        add_transfer("Connecting you to a live agent.")
        return msgs

    # BACKEND INTEGRATION GAP
    if fm == "backend_integration_gap":
        add_user(_journey_intent(j, pii))
        add_bot("Let me check that for you. One moment please...")
        add_bot("I'm sorry, I'm unable to retrieve your information at this time due to a system issue. Please try again later.")
        add_user("I've tried multiple times. I need this resolved now.")
        add_bot("I apologize for the inconvenience. Our systems seem to be experiencing a temporary disruption. "
                "I'll connect you to an agent who can access your information directly.")
        add_transfer("Connecting you to a banking specialist.")
        add_bot(
            f"Good day, this is {_agent_name()}. I can see there was a system issue. "
            f"Let me access your account directly. I can confirm {_journey_agent_resolution(j, pii)}",
            turn_offset_secs=180
        )
        return msgs

    # POLICY ROUTED
    if fm == "policy_routed":
        add_user(_journey_intent(j, pii))
        add_bot(_journey_policy_response(j))
        add_bot("This request requires review by our specialist team. I'll connect you now.")
        add_transfer("Connecting you to our " + _journey_team(j) + " specialist.")
        return msgs

    # TRUST OR UX FAILURE
    if fm == "trust_or_ux_failure":
        add_user(_journey_intent(j, pii))
        add_bot(_journey_correct_response(j, pii))
        add_user("Are you sure? I'm not convinced by this information.")
        add_bot("I understand your concern. The information I've provided is accurate and up-to-date.")
        add_user("I'd prefer to confirm this with a human agent to be safe.")
        add_bot("Of course, I understand. Let me connect you with one of our team members to confirm.")
        add_transfer("Connecting you with a banking specialist.")
        add_bot(
            f"Hello, I'm {_agent_name()}. I can confirm what our assistant told you — {_journey_correct_response(j, pii)}",
            turn_offset_secs=190
        )
        return msgs

    # BOT HAS DATA BUT COMMUNICATES POORLY
    if fm == "bot_has_data_communicates_poorly":
        add_user(_journey_intent(j, pii))
        add_bot(_journey_poor_response(j))
        add_user("That's not helpful. Can you be more specific?")
        add_bot("Please contact your nearest branch or call our customer service line for further assistance.")
        add_user("I am calling customer service right now. I need an answer.")
        add_transfer("Let me connect you to a specialist who can provide more detailed information.")
        add_bot(
            f"Hi there, I'm {_agent_name()}. I can help you with that. {_journey_agent_resolution(j, pii)}",
            turn_offset_secs=200
        )
        return msgs

    # SILENT FAILURE — session looks resolved but user returns
    if fm == "silent_failure":
        add_user(_journey_intent(j, pii))
        add_bot(_journey_correct_response(j, pii))
        add_user("OK thank you")
        add_bot("You're welcome! Is there anything else I can help you with today?")
        add_user("No, that's all")
        add_bot("Thank you for banking with Mashreq. Have a great day!")
        # (The re-contact is a separate session — flagged by metadata, not in this transcript)
        return msgs

    # LANGUAGE DIALECT GAP
    if fm == "language_dialect_gap":
        add_user(_journey_intent_dialect(j, lang))
        add_bot("I'm sorry, I didn't quite understand. Could you please rephrase in standard Arabic or English?")
        add_user(_journey_intent_dialect(j, lang, attempt=2))
        add_bot("I'm having difficulty understanding your request. Let me connect you with an agent who can assist.")
        add_transfer("Connecting you to our multilingual support team.")
        return msgs

    # SUCCESSFUL CONTAINMENT (no escalation)
    add_user(_journey_intent(j, pii))
    add_bot(_journey_correct_response(j, pii))
    if random.random() < 0.4:
        add_user("Thank you, that was helpful")
        add_bot("You're welcome! Is there anything else I can help you with?")
        add_user("No that's all")
    add_bot("Thank you for banking with Mashreq. Have a great day!")
    return msgs


def _journey_team(journey_id: str) -> str:
    domain = journey_id.split("_")[0].lower()
    teams = {
        "card": "Cards",
        "loan": "Loans",
        "acct": "Accounts",
        "wlth": "Wealth Management",
        "remi": "Remittances",
        "digi": "Digital Banking",
        "comp": "Customer Relations",
        "well": "Financial Advisory",
        "intl": "General Banking",
    }
    return teams.get(domain, "General Banking")


def _agent_name() -> str:
    agents = [
        "Ahmed Al Mansoori", "Sara Johnson", "Priya Nair", "Khalid Al Rashidi",
        "Fatima Al Mazrouei", "James Wilson", "Nour Ibrahim", "Vikram Patel",
    ]
    return random.choice(agents)


def _journey_intent_short(j: str) -> str:
    shorts = {
        "ACCT_004": "close my account",
        "CARD_001": "check my credit card balance",
        "CARD_004": "block my credit card",
        "DIGI_001": "log into my mobile app",
        "LOAN_001": "check my loan balance",
        "REMI_003": "get the current exchange rate",
    }
    return shorts.get(j, "get help with my account")


def _journey_intent(j: str, pii: dict) -> str:
    """Generate a realistic opening user message for this journey."""
    intents = {
        "ACCT_001": f"What is my current account balance?",
        "ACCT_002": f"Can you show me my savings account balance?",
        "ACCT_003": f"I need my account statement for last 3 months",
        "ACCT_004": f"I want to close my savings account please",
        "ACCT_005": f"What is my IBAN number?",
        "ACCT_006": f"I need to request a new cheque book",
        "ACCT_007": f"What is the status of cheque number {random.randint(100000,999999)}?",
        "ACCT_008": f"Why is my account frozen?",
        "ACCT_009": f"I want to set up a standing order",
        "ACCT_010": f"I have a question about a direct debit on my account",
        "ACCT_011": f"What interest rate does my account earn?",
        "ACCT_012": f"I want to convert to a salary account",
        "ACCT_013": f"I want to add my wife to my account",
        "ACCT_014": f"How do I upgrade my account to Mashreq Gold?",
        "ACCT_015": f"What is the maximum I can overdraft on my account?",
        "CARD_001": f"What is my credit card balance?",
        "CARD_002": f"I need my credit card statement",
        "CARD_003": f"I want to increase my credit card limit",
        "CARD_004": f"I need to block my credit card urgently",
        "CARD_005": f"Can I convert my purchase to instalments?",
        "CARD_006": f"How many reward points do I have?",
        "CARD_007": f"There's a transaction on my card I don't recognise",
        "CARD_008": f"My debit card was lost, I need to block it",
        "CARD_009": f"I forgot my debit card PIN, how do I reset it?",
        "CARD_010": f"I want to get a supplementary card for my family",
        "CARD_011": f"When is my credit card payment due?",
        "CARD_012": f"What is the minimum payment on my credit card?",
        "CARD_013": f"I need to activate my card for international use",
        "CARD_014": f"Can you waive my annual credit card fee?",
        "CARD_015": f"I want to close my credit card",
        "LOAN_001": f"What is my outstanding personal loan balance?",
        "LOAN_002": f"I need my loan statement",
        "LOAN_003": f"Am I eligible for a personal loan?",
        "LOAN_004": f"What is the status of my loan application?",
        "LOAN_005": f"How much would it cost to settle my loan early?",
        "LOAN_006": f"I'm struggling with my loan payments, can we restructure?",
        "LOAN_007": f"Can I defer my EMI payment this month?",
        "LOAN_008": f"What is my outstanding auto loan balance?",
        "LOAN_009": f"What are the charges for early settlement of my car loan?",
        "LOAN_010": f"What is my home loan balance?",
        "LOAN_011": f"I need a No Objection Certificate for my home loan",
        "LOAN_012": f"What does my loan insurance cover?",
        "LOAN_013": f"Can I get a top-up on my existing personal loan?",
        "LOAN_014": f"Can you show me my loan payment history?",
        "LOAN_015": f"I paid off my loan, I need a pre-closure certificate",
        "WLTH_001": f"Can you show me my investment portfolio?",
        "WLTH_002": f"When does my fixed deposit mature?",
        "WLTH_003": f"I want to renew my fixed deposit",
        "WLTH_004": f"I need to withdraw my fixed deposit before maturity",
        "WLTH_005": f"Show me my mutual fund portfolio",
        "WLTH_006": f"I'd like to book an appointment with a wealth manager",
        "WLTH_007": f"What Sukuk options are currently available?",
        "WLTH_008": f"Tell me about the gold savings plan",
        "WLTH_009": f"I need information about life insurance options",
        "WLTH_010": f"What structured products does Mashreq offer?",
        "REMI_001": f"I want to send money to {random.choice(['India', 'Pakistan', 'Egypt', 'Philippines', 'UK'])}",
        "REMI_002": f"My transfer hasn't arrived. Reference number {random.randint(10000000,99999999)}",
        "REMI_003": f"What is the current exchange rate for USD to AED?",
        "REMI_004": f"I want to add a new beneficiary for transfers",
        "REMI_005": f"I made a mistake in my transfer, I need to cancel it",
        "REMI_006": f"I want to check my WPS salary payment",
        "REMI_007": f"My UAEFTS transfer is not showing in the recipient's account",
        "REMI_008": f"What is the daily transfer limit for international transfers?",
        "REMI_009": f"I have a standing monthly transfer, I want to check on it",
        "REMI_010": f"How much are the fees for sending money to India?",
        "DIGI_001": f"I can't log into my Mashreq NEO app",
        "DIGI_002": f"I forgot my mobile banking password",
        "DIGI_003": f"How do I register for internet banking?",
        "DIGI_004": f"I'm not receiving my OTP",
        "DIGI_005": f"How do I find the transfer option in the app?",
        "DIGI_006": f"How does cardless ATM withdrawal work?",
        "DIGI_007": f"How do I add my Mashreq card to Apple Pay?",
        "DIGI_008": f"How do I sign up for e-statements?",
        "DIGI_009": f"How do I set up transaction alerts?",
        "DIGI_010": f"I'm trying to remove an old phone from my trusted devices",
        "COMP_001": f"I want to file a complaint about my recent experience",
        "COMP_002": f"What is the status of complaint {random.randint(100000,999999)}?",
        "COMP_003": f"There is a fraudulent transaction on my card",
        "COMP_004": f"I think my account has been hacked",
        "COMP_005": f"I want to share feedback about your service",
        "WELL_001": f"Can you do a financial health check for me?",
        "WELL_002": f"Show me an analysis of my spending",
        "WELL_003": f"Can you help me create a budget?",
        "WELL_004": f"I want to set a savings goal",
        "WELL_005": f"What products do you recommend for me?",
        "INTL_001": f"Where is the nearest Mashreq branch to {random.choice(['Dubai Marina', 'Deira', 'Downtown Dubai', 'Business Bay'])}?",
        "INTL_002": f"Where is the nearest Mashreq ATM?",
        "INTL_003": f"What are your customer service hours?",
        "INTL_004": f"Can you explain how your interest calculation works?",
        "INTL_005": f"I have a question about cryptocurrency",
    }
    return intents.get(j, "I need help with my account")


def _journey_intent_dialect(j: str, lang: str, attempt: int = 1) -> str:
    """Dialect-variant intent messages that challenge NLU."""
    if lang == "ar":
        dialects = {
            1: ["عايز أعرف رصيدي", "كم رصيدي", "محتاج أشوف حسابي", "فلوسي كام"],
            2: ["قلتلك رصيدي", "مش فاهم ردك", "بدي أعرف الباقي في حسابي"],
        }
        opts = dialects.get(attempt, dialects[1])
    elif lang == "ur":
        dialects = {
            1: ["میرا بیلنس کیا ہے", "کھاتے میں کتنے پیسے ہیں", "بیلنس چیک کریں"],
            2: ["پھر بتاؤ", "سمجھ نہیں آیا", "دوبارہ بتائیں"],
        }
        opts = dialects.get(attempt, dialects[1])
    else:
        opts = [_journey_intent(j, {})]
    return random.choice(opts)


def _rephrase_intent(j: str, lang: str, attempt: int) -> str:
    base = {
        0: "Let me try again. " + _journey_intent(j, {}),
        1: "I mean, I need help with " + JOURNEY_NAMES.get(j, "my account").lower(),
        2: "Can you understand me? " + _journey_intent(j, {}),
        3: "Fine. Connect me to someone who can help.",
    }
    return base.get(attempt, "I need assistance please")


def _journey_correct_response(j: str, pii: dict) -> str:
    """Correct, specific bot response — used in trust/ux failure and successful sessions."""
    amount = round(random.uniform(500, 50000), 2)
    responses = {
        "ACCT_001": f"Your current account balance is AED {amount:,.2f} as of today.",
        "ACCT_002": f"Your savings account balance is AED {amount:,.2f} as of today.",
        "ACCT_003": f"Your account statement for the last 3 months has been sent to your registered email address.",
        "ACCT_005": f"Your IBAN number is {pii.get('iban', 'AE070331234567890123456')}.",
        "CARD_001": f"Your credit card outstanding balance is AED {round(random.uniform(0, 20000), 2):,.2f}.",
        "CARD_011": f"Your next payment due date is {(datetime.now() + timedelta(days=random.randint(3,28))).strftime('%d %B %Y')}.",
        "CARD_012": f"Your minimum payment due is AED {round(random.uniform(100, 1000), 2):,.2f}.",
        "LOAN_001": f"Your outstanding personal loan balance is AED {round(random.uniform(5000, 150000), 2):,.2f}.",
        "REMI_003": f"The current exchange rate is 1 USD = {round(random.uniform(3.65, 3.68), 4)} AED. This rate is subject to change.",
        "DIGI_005": f"To make a transfer, tap the 'Transfer' icon on the home screen, then select 'New Transfer' or 'Saved Beneficiary'.",
        "WLTH_002": f"Your fixed deposit of AED {round(random.uniform(10000, 200000), 2):,.2f} matures on {(datetime.now() + timedelta(days=random.randint(30,365))).strftime('%d %B %Y')}.",
    }
    return responses.get(j, f"I've processed your request for {JOURNEY_NAMES.get(j, 'your query')}. Your reference number is REF{random.randint(10000000, 99999999)}.")


def _journey_poor_response(j: str) -> str:
    """Vague/poor bot response — used in bot_has_data_communicates_poorly failure mode."""
    responses = {
        "ACCT_004": "There is a balance in your account. Account closure cannot be processed at this time. Please contact us at a later time or visit a branch.",
        "CARD_003": "Your credit card limit enhancement request cannot be processed at this time.",
        "LOAN_005": "Early settlement information is not available through this channel. Please visit a branch.",
        "CARD_007": "Your dispute cannot be filed at this time. Please try again later.",
        "REMI_005": "Transfer cancellation requests must be submitted through official channels.",
        "ACCT_008": "Your account has a restriction. Please contact us for more information.",
        "DIGI_001": "Your login issue cannot be resolved at this time. Please try again.",
        "LOAN_006": "Loan restructuring requests require additional processing. Please contact our team.",
    }
    return responses.get(j, "This request cannot be completed at this time. Please contact our customer service for assistance. Reference: N/A.")


def _journey_policy_response(j: str) -> str:
    responses = {
        "LOAN_006": "Loan restructuring requests require review by our credit management team as per Mashreq Bank policy.",
        "LOAN_007": "EMI deferral requests require verification of eligibility under current UAE Central Bank guidelines.",
        "ACCT_008": "Account freeze enquiries must be handled by our compliance team due to regulatory requirements.",
        "ACCT_013": "Adding a joint account holder requires full KYC documentation for the new party per UAE regulations.",
        "COMP_003": "Fraud report investigations are handled exclusively by our dedicated fraud team for your security.",
        "COMP_004": "Security incidents require immediate escalation to our cybersecurity and fraud prevention team.",
    }
    return responses.get(j, f"This request must be handled by a specialist as per Mashreq Bank's service policies.")


def _journey_agent_resolution(j: str, pii: dict) -> str:
    amount = round(random.uniform(100, 10000), 2)
    resolutions = {
        "ACCT_004": f"Your account shows a pending merchant hold of AED {amount:.2f}. This will clear in 3-5 business days.",
        "CARD_007": f"I've raised a dispute for the transaction. You'll receive a confirmation email within 24 hours.",
        "DIGI_001": f"I've reset your mobile banking access. You'll receive an SMS with reset instructions.",
        "LOAN_005": f"Your early settlement amount would be AED {amount:,.2f} including all applicable charges. Valid for 30 days.",
        "REMI_005": f"I've initiated the transfer recall. The process takes 3-7 business days depending on the destination bank.",
    }
    return resolutions.get(j, f"I've resolved your query and updated your account accordingly. Your reference is REF{random.randint(10000000,99999999)}.")


# ---------------------------------------------------------------------------
# SESSION FACTORY
# ---------------------------------------------------------------------------

def _build_journey_weights(n_journeys: int = 90) -> list[float]:
    """
    Pareto-style weight vector: top 15 journeys carry 60% of traffic,
    remaining 75 share the other 40%.
    """
    weights = []
    n_high = len(HIGH_VOLUME_JOURNEYS)
    high_per = 0.60 / n_high
    low_total = 0.40
    n_low = len(ALL_JOURNEY_IDS) - n_high
    low_per = low_total / n_low

    # Add mild variance within each group
    for jid in ALL_JOURNEY_IDS:
        if jid in HIGH_VOLUME_JOURNEYS:
            w = high_per * random.uniform(0.7, 1.3)
        else:
            w = low_per * random.uniform(0.5, 1.5)
        weights.append(w)

    # Normalize
    total = sum(weights)
    return [w / total for w in weights]


def _pick_failure_mode(journey_id: str) -> str:
    profile = JOURNEY_FAILURE_PROFILES.get(journey_id, DEFAULT_FAILURE_PROFILE)
    codes = list(profile.keys())
    weights = list(profile.values())
    # Normalize in case they don't sum to 1
    total = sum(weights)
    weights = [w / total for w in weights]
    return random.choices(codes, weights=weights)[0]


def generate_sessions(n_sessions: int, seed: int = 42) -> list[Session]:
    """Generate N Session objects with realistic distributions."""
    random.seed(seed)
    np.random.seed(seed)

    journey_weights = _build_journey_weights()

    # Generate user pool: ~200K unique users for 50K sessions → avg 4 sessions each
    # For 500 sessions: scaled proportionally
    n_users = max(200, int(n_sessions * 4))
    user_pool = [_make_user_id() for _ in range(n_users)]

    sessions = []
    for _ in range(n_sessions):
        # Select user (some users appear multiple times)
        user_id = random.choice(user_pool)

        # Geography
        geography = random.choices(
            ["UAE", "Egypt", "Pakistan"],
            weights=[0.60, 0.25, 0.15]
        )[0]

        language = get_language(geography)
        channel = _random_channel()

        # Journey
        journey_id = random.choices(ALL_JOURNEY_IDS, weights=journey_weights)[0]
        journey_name = JOURNEY_NAMES[journey_id]

        # Timestamp
        start_time = _random_timestamp()

        # Session ID
        session_id = _make_session_id(user_id, start_time)

        # Customer name and PII
        customer_name = get_name(geography)
        pii = {
            "name": customer_name,
            "emirates_id": generate_emirates_id(),
            "mobile": generate_mobile(geography),
            "card_number": generate_card_number(),
            "iban": generate_iban(geography),
            "account_number": generate_account_number(),
        }

        # Escalation decision
        escalation_rate = JOURNEY_ESCALATION_RATES.get(journey_id, 0.25)
        is_escalated = random.random() < escalation_rate

        # Failure mode
        failure_mode = _pick_failure_mode(journey_id) if is_escalated else None

        sess = Session(
            session_id=session_id,
            user_id=user_id,
            geography=geography,
            language=language,
            channel=channel,
            journey_id=journey_id,
            journey_name=journey_name,
            failure_mode=failure_mode,
            is_escalated=is_escalated,
            start_time=start_time,
            customer_name=customer_name,
            pii_items=pii,
        )
        sess.messages = build_conversation(sess)
        sessions.append(sess)

    return sessions


def sessions_to_transcript_rows(sessions: list[Session]) -> list[dict]:
    """Flatten sessions to transcript rows (one row per message turn)."""
    rows = []
    for sess in sessions:
        for msg in sess.messages:
            rows.append(msg)
    return rows


def generate_csat(sessions: list[Session], seed: int = 99) -> list[dict]:
    """
    Generate CSAT survey responses for ~12% of sessions.
    Score correlates weakly with failure mode; substantial noise.
    """
    random.seed(seed)
    csat_rows = []

    for sess in sessions:
        if random.random() > 0.12:
            continue

        # Base score by outcome
        if sess.is_escalated:
            if sess.failure_mode in ("bot_fallback_exhausted", "backend_integration_gap", "authentication_friction"):
                base_score = random.choices([1, 2, 3, 4, 5], weights=[0.30, 0.30, 0.20, 0.15, 0.05])[0]
            elif sess.failure_mode == "policy_routed":
                base_score = random.choices([1, 2, 3, 4, 5], weights=[0.10, 0.15, 0.30, 0.30, 0.15])[0]
            else:
                base_score = random.choices([1, 2, 3, 4, 5], weights=[0.20, 0.25, 0.25, 0.20, 0.10])[0]
        else:
            # Successful containment
            base_score = random.choices([1, 2, 3, 4, 5], weights=[0.05, 0.08, 0.17, 0.35, 0.35])[0]

        # CSAT submitted 5-60 minutes after session end
        last_msg_time = max(
            datetime.strptime(m["timestamp"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
            for m in sess.messages
        ) if sess.messages else sess.start_time
        csat_time = last_msg_time + timedelta(minutes=random.randint(5, 60))

        # Comment (30% of responses)
        comment = ""
        if random.random() < 0.30:
            comment = _generate_csat_comment(sess, base_score)

        csat_rows.append({
            "user_id": sess.user_id,
            "channel_id": f"ch-{sess.channel.lower().replace('/', '-').replace(' ', '-')}-{random.randint(1000,9999)}",
            "session_id": sess.session_id,
            "channel": sess.channel,
            "language": sess.language,
            "score": base_score,
            "date_time": _format_ts(csat_time),
            "comments": comment,
            "_synthetic": True,
        })

    return csat_rows


def _generate_csat_comment(sess: Session, score: int) -> str:
    """Generate realistic CSAT comment text. 5% chance of containing PII."""
    low_comments = [
        "The bot was completely unhelpful",
        "Had to repeat myself multiple times before getting transferred",
        "Very frustrating experience, took too long",
        "Bot couldn't understand my question at all",
        "Why can't the bot just answer simple questions?",
        "The agent was helpful but the bot wasted my time",
        "System kept asking for OTP I never received",
        "Not satisfied with the service",
    ]
    mid_comments = [
        "Service was okay but could be faster",
        "Agent resolved my issue but bot couldn't",
        "Average experience, nothing special",
        "Needs improvement in understanding Arabic",
        "The transfer was eventually completed",
    ]
    high_comments = [
        "Very helpful and quick service",
        "Agent was professional and resolved my issue",
        "Happy with the service",
        "Easy to use and informative",
        "Great experience overall",
        "My issue was resolved on the first call",
    ]

    if score <= 2:
        comment = random.choice(low_comments)
    elif score == 3:
        comment = random.choice(mid_comments)
    else:
        comment = random.choice(high_comments)

    # 5% chance of PII in comment
    if random.random() < 0.05:
        pii_type = random.choice(["mobile", "card_snippet"])
        if pii_type == "mobile":
            comment += f". Please call me on {sess.pii_items['mobile']}"
        else:
            comment += f". My card ending {sess.pii_items['card_number'][-4:]} was affected"

    return comment


# ---------------------------------------------------------------------------
# OUTPUT
# ---------------------------------------------------------------------------

TRANSCRIPT_COLUMNS = [
    "bot_name", "user_id", "session_id", "channel", "language",
    "bot_message", "user_message", "timestamp", "_synthetic"
]

CSAT_COLUMNS = [
    "user_id", "channel_id", "session_id", "channel", "language",
    "score", "date_time", "comments", "_synthetic"
]


def write_csv(rows: list[dict], path: Path, columns: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    logger.info("Wrote %d rows to %s", len(rows), path)


def print_summary(sessions: list[Session], csat_rows: list[dict]) -> None:
    """Print Gate 1 summary statistics."""
    print("\n" + "=" * 70)
    print("GATE 1: SYNTHETIC DATA SUMMARY — 500-SESSION SAMPLE")
    print("=" * 70)

    total = len(sessions)
    print(f"\nTotal sessions: {total}")
    print(f"Total transcript messages: {sum(len(s.messages) for s in sessions)}")
    print(f"Total CSAT responses: {len(csat_rows)}")
    print(f"CSAT response rate: {len(csat_rows)/total:.1%}")

    # Sessions per journey (top 15)
    journey_counts: dict[str, int] = defaultdict(int)
    for s in sessions:
        journey_counts[s.journey_id] += 1

    print("\n--- TOP 15 JOURNEYS BY VOLUME ---")
    for jid, cnt in sorted(journey_counts.items(), key=lambda x: -x[1])[:15]:
        escalated = sum(1 for s in sessions if s.journey_id == jid and s.is_escalated)
        print(f"  {jid:10s} {JOURNEY_NAMES[jid][:40]:40s}  {cnt:4d} sessions  ({escalated/cnt:.0%} escalation rate)")

    # Failure mode distribution
    fm_counts: dict[str, int] = defaultdict(int)
    for s in sessions:
        if s.failure_mode:
            fm_counts[s.failure_mode] += 1
    total_escalated = sum(fm_counts.values())

    print(f"\n--- FAILURE MODE DISTRIBUTION ({total_escalated} escalated sessions) ---")
    for fm, cnt in sorted(fm_counts.items(), key=lambda x: -x[1]):
        print(f"  {fm:40s}  {cnt:4d}  ({cnt/total_escalated:.1%})")

    # Language/Geography distribution
    geo_counts: dict[str, int] = defaultdict(int)
    lang_counts: dict[str, int] = defaultdict(int)
    for s in sessions:
        geo_counts[s.geography] += 1
        lang_counts[s.language] += 1

    print("\n--- GEOGRAPHY DISTRIBUTION ---")
    for geo, cnt in sorted(geo_counts.items(), key=lambda x: -x[1]):
        print(f"  {geo:12s}  {cnt:4d}  ({cnt/total:.1%})")

    print("\n--- LANGUAGE DISTRIBUTION ---")
    for lang, cnt in sorted(lang_counts.items(), key=lambda x: -x[1]):
        print(f"  {lang:6s}  {cnt:4d}  ({cnt/total:.1%})")

    # PII counts (items seeded)
    print(f"\n--- PII ITEMS SEEDED ---")
    print(f"  Emirates IDs:    {total} (1 per session)")
    print(f"  Mobile numbers:  {total} (1 per session)")
    print(f"  Card numbers:    {total} (1 per session, Luhn-valid)")
    print(f"  IBANs:           {total} (1 per session)")
    print(f"  Account numbers: {total} (1 per session)")
    print(f"  CSAT comments with PII: ~{int(len(csat_rows) * 0.30 * 0.05)} (estimated)")

    # 3 example sessions
    print("\n" + "=" * 70)
    print("3 EXAMPLE SESSIONS")
    print("=" * 70)

    examples = {
        "bot_fallback_exhausted": None,
        "bot_has_data_communicates_poorly": None,
        "customer_demanded_human": None,
    }
    for s in sessions:
        if s.failure_mode in examples and examples[s.failure_mode] is None:
            examples[s.failure_mode] = s
        if all(v is not None for v in examples.values()):
            break

    for fm_label, sess in examples.items():
        if sess is None:
            print(f"\n[No example found for {fm_label}]")
            continue
        print(f"\n{'='*60}")
        print(f"EXAMPLE: {fm_label}")
        print(f"Journey:   {sess.journey_id} — {sess.journey_name}")
        print(f"Geography: {sess.geography} | Language: {sess.language}")
        print(f"Session:   {sess.session_id}")
        print(f"Started:   {sess.start_time}")
        print(f"{'—'*60}")
        for msg in sess.messages[:10]:  # First 10 turns
            if msg["user_message"]:
                print(f"  USER: {msg['user_message'][:120]}")
            if msg["bot_message"]:
                display = msg["bot_message"]
                if display.startswith("{"):
                    try:
                        obj = json.loads(display)
                        display = obj.get("payload", {}).get("text", display)[:120]
                    except Exception:
                        display = display[:120]
                print(f"  BOT:  {display}")


# ---------------------------------------------------------------------------
# CLI ENTRY POINT
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Generate synthetic Mashreq CBP transcript data.")
    parser.add_argument("--sessions", type=int, default=500, help="Number of sessions to generate")
    parser.add_argument("--output", choices=["sample", "full"], default="sample",
                        help="Output directory: sample or full")
    parser.add_argument("--seed", type=int, default=42, help="Random seed")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    out_dir = DATA_DIR / "synthetic" / args.output
    transcript_path = out_dir / "transcripts.csv"
    csat_path = out_dir / "csat.csv"

    print(f"Generating {args.sessions} sessions (seed={args.seed})...")
    sessions = generate_sessions(args.sessions, seed=args.seed)

    print("Building CSAT responses...")
    csat_rows = generate_csat(sessions, seed=args.seed + 57)

    print("Writing transcript CSV...")
    transcript_rows = sessions_to_transcript_rows(sessions)
    write_csv(transcript_rows, transcript_path, TRANSCRIPT_COLUMNS)

    print("Writing CSAT CSV...")
    write_csv(csat_rows, csat_path, CSAT_COLUMNS)

    print_summary(sessions, csat_rows)

    print(f"\n✓ Output: {transcript_path} ({len(transcript_rows)} rows)")
    print(f"✓ Output: {csat_path} ({len(csat_rows)} rows)")

    if args.output == "sample":
        print("\n" + "=" * 70)
        print("==== GATE 1: AWAITING USER REVIEW ====")
        print("=" * 70)
        print("""
Review the summary above and the 3 example sessions.
Confirm the following before proceeding:
  1. Failure mode distribution looks realistic
  2. Example sessions read like real banking conversations
  3. PII patterns are correct for the redactor to catch

Type 'approved' to proceed to 50,000-session full generation.
        """)


if __name__ == "__main__":
    main()
