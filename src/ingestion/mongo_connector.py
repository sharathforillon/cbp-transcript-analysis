"""
mongo_connector.py — MongoDB connector with mock-vs-real toggle.

In MOCK mode (default): reads from local CSV files in data/synthetic/.
In REAL mode: connects to the bank's MongoDB instance using credentials from .env.

Toggle via env var:  MONGO_MODE=real | mock
Or via CLI:          python -m src.ingestion.mongo_connector --validate

Dev team runbook:
  1. Set MONGO_URI in .env
  2. Set MONGO_MODE=real in .env
  3. Run: python -m src.ingestion.mongo_connector --validate
     This pulls 10 sample sessions and validates field presence against SCHEMA_CONTRACT.
"""

from __future__ import annotations

import csv
import json
import logging
import os
import sys
from pathlib import Path
from typing import Iterator, Optional

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).parent.parent.parent
DATA_DIR = REPO_ROOT / "data"

# ---------------------------------------------------------------------------
# ABSTRACT CONNECTOR
# ---------------------------------------------------------------------------


class BaseConnector:
    """Abstract base for transcript data connectors."""

    def iter_transcript_rows(self, limit: Optional[int] = None) -> Iterator[dict]:
        raise NotImplementedError

    def iter_csat_rows(self, limit: Optional[int] = None) -> Iterator[dict]:
        raise NotImplementedError

    def count_transcripts(self) -> int:
        raise NotImplementedError

    def validate(self) -> dict:
        """Pull 10 sample rows and check required fields are present."""
        raise NotImplementedError


# ---------------------------------------------------------------------------
# MOCK CONNECTOR (reads from CSV)
# ---------------------------------------------------------------------------

REQUIRED_TRANSCRIPT_FIELDS = [
    "bot_name", "user_id", "channel", "language", "bot_message", "user_message", "timestamp"
]
REQUIRED_CSAT_FIELDS = [
    "user_id", "channel_id", "session_id", "channel", "language", "score", "date_time"
]


class MockConnector(BaseConnector):
    """Reads from synthetic CSV files. Used for development and testing."""

    def __init__(self, source: str = "sample"):
        """
        Args:
            source: 'sample' (500 sessions) or 'full' (10K sessions).
        """
        self.source = source
        self.transcript_path = DATA_DIR / "synthetic" / source / "transcripts.csv"
        self.csat_path = DATA_DIR / "synthetic" / source / "csat.csv"

        if not self.transcript_path.exists():
            raise FileNotFoundError(
                f"Transcript CSV not found at {self.transcript_path}. "
                f"Run: python -m src.ingestion.generate_synthetic --output {source}"
            )

    def iter_transcript_rows(self, limit: Optional[int] = None) -> Iterator[dict]:
        count = 0
        with open(self.transcript_path, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                yield dict(row)
                count += 1
                if limit and count >= limit:
                    break

    def iter_csat_rows(self, limit: Optional[int] = None) -> Iterator[dict]:
        if not self.csat_path.exists():
            logger.warning("CSAT file not found at %s", self.csat_path)
            return
        count = 0
        with open(self.csat_path, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                yield dict(row)
                count += 1
                if limit and count >= limit:
                    break

    def count_transcripts(self) -> int:
        count = 0
        with open(self.transcript_path, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for _ in reader:
                count += 1
        return count

    def validate(self) -> dict:
        sample = list(self.iter_transcript_rows(limit=10))
        missing = []
        for field in REQUIRED_TRANSCRIPT_FIELDS:
            if field not in sample[0]:
                missing.append(field)

        csat_sample = list(self.iter_csat_rows(limit=10))
        csat_missing = []
        if csat_sample:
            for field in REQUIRED_CSAT_FIELDS:
                if field not in csat_sample[0]:
                    csat_missing.append(field)

        return {
            "mode": "mock",
            "source": self.source,
            "transcript_sample_size": len(sample),
            "transcript_fields_found": list(sample[0].keys()) if sample else [],
            "transcript_required_missing": missing,
            "csat_sample_size": len(csat_sample),
            "csat_required_missing": csat_missing,
            "status": "OK" if not missing and not csat_missing else "FIELD_MISMATCH",
        }


# ---------------------------------------------------------------------------
# REAL MONGO CONNECTOR
# ---------------------------------------------------------------------------

class MongoConnector(BaseConnector):
    """
    Connects to the bank's real MongoDB instance.

    Requires:
      MONGO_URI=mongodb+srv://user:pass@cluster.mongodb.net/dbname
      MONGO_DB=cbp_analytics         (database name)
      MONGO_COLLECTION_TRANSCRIPTS=messages   (collection name)
      MONGO_COLLECTION_CSAT=csat_responses

    The dev team sets these in .env. The pipeline reads them here.
    """

    def __init__(self):
        try:
            from pymongo import MongoClient  # type: ignore
        except ImportError:
            raise ImportError(
                "pymongo is not installed. Run: pip install pymongo[srv]\n"
                "If connecting to Atlas, also run: pip install 'pymongo[srv]'"
            )

        uri = os.environ.get("MONGO_URI")
        if not uri:
            raise EnvironmentError(
                "MONGO_URI not set. Copy .env.example to .env and fill in your MongoDB URI."
            )

        db_name = os.environ.get("MONGO_DB", "cbp_analytics")
        self.transcript_collection = os.environ.get("MONGO_COLLECTION_TRANSCRIPTS", "messages")
        self.csat_collection = os.environ.get("MONGO_COLLECTION_CSAT", "csat_responses")

        logger.info("Connecting to MongoDB: %s (db=%s)", uri[:30] + "...", db_name)
        self.client = MongoClient(uri, serverSelectionTimeoutMS=5000)
        self.db = self.client[db_name]
        logger.info("Connected to MongoDB.")

    def iter_transcript_rows(self, limit: Optional[int] = None) -> Iterator[dict]:
        collection = self.db[self.transcript_collection]
        cursor = collection.find({}, {"_id": 0})
        if limit:
            cursor = cursor.limit(limit)
        for doc in cursor:
            # Convert ObjectId and other non-serializable types
            yield _serialize_mongo_doc(doc)

    def iter_csat_rows(self, limit: Optional[int] = None) -> Iterator[dict]:
        collection = self.db[self.csat_collection]
        cursor = collection.find({}, {"_id": 0})
        if limit:
            cursor = cursor.limit(limit)
        for doc in cursor:
            yield _serialize_mongo_doc(doc)

    def count_transcripts(self) -> int:
        return self.db[self.transcript_collection].count_documents({})

    def validate(self) -> dict:
        sample = list(self.iter_transcript_rows(limit=10))
        if not sample:
            return {"status": "ERROR", "message": "No documents returned from transcripts collection."}

        missing = [f for f in REQUIRED_TRANSCRIPT_FIELDS if f not in sample[0]]

        csat_sample = list(self.iter_csat_rows(limit=10))
        csat_missing = [f for f in REQUIRED_CSAT_FIELDS if csat_sample and f not in csat_sample[0]]

        session_id_present = "session_id" in sample[0]

        return {
            "mode": "real",
            "transcript_collection": self.transcript_collection,
            "csat_collection": self.csat_collection,
            "transcript_count_estimate": self.count_transcripts(),
            "transcript_fields_found": list(sample[0].keys()),
            "transcript_required_missing": missing,
            "session_id_present": session_id_present,
            "csat_sample_size": len(csat_sample),
            "csat_required_missing": csat_missing,
            "status": "OK" if not missing and not csat_missing else "FIELD_MISMATCH",
            "note": (
                "[VERIFY] Update config/schema_mapping.yaml to match any field names that differ. "
                f"session_id_present={session_id_present} — update schema_mapping.yaml accordingly."
            ),
        }


# ---------------------------------------------------------------------------
# FACTORY
# ---------------------------------------------------------------------------

def get_connector(mode: Optional[str] = None, source: str = "full") -> BaseConnector:
    """
    Return the appropriate connector based on MONGO_MODE env var or explicit arg.

    Args:
        mode: 'mock' | 'real' | None (reads from MONGO_MODE env var, defaults to 'mock')
        source: For mock mode: 'sample' | 'full'

    Returns:
        MockConnector or MongoConnector instance.
    """
    if mode is None:
        mode = os.environ.get("MONGO_MODE", "mock").lower()

    if mode == "real":
        logger.info("Using REAL MongoDB connector.")
        return MongoConnector()
    else:
        logger.info("Using MOCK connector (source=%s).", source)
        return MockConnector(source=source)


# ---------------------------------------------------------------------------
# HELPERS
# ---------------------------------------------------------------------------

def _serialize_mongo_doc(doc: dict) -> dict:
    """Convert MongoDB document to a JSON-serializable dict."""
    from datetime import datetime
    result = {}
    for k, v in doc.items():
        if isinstance(v, datetime):
            result[k] = v.strftime("%Y-%m-%d %H:%M:%S")
        else:
            try:
                json.dumps(v)
                result[k] = v
            except (TypeError, ValueError):
                result[k] = str(v)
    return result


# ---------------------------------------------------------------------------
# CLI — validation command
# ---------------------------------------------------------------------------

def main():
    import argparse
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    parser = argparse.ArgumentParser(description="Validate MongoDB connector against SCHEMA_CONTRACT.")
    parser.add_argument("--validate", action="store_true", help="Run validation and exit")
    parser.add_argument("--mode", choices=["mock", "real"], default=None)
    parser.add_argument("--source", choices=["sample", "full"], default="sample")
    args = parser.parse_args()

    connector = get_connector(mode=args.mode, source=args.source)
    result = connector.validate()

    print("\n=== MongoDB Connector Validation ===")
    print(json.dumps(result, indent=2))

    if result.get("status") == "OK":
        print("\n✓ All required fields present. Schema contract satisfied.")
    else:
        print("\n✗ FIELD MISMATCH — update config/schema_mapping.yaml before running pipeline.")
        sys.exit(1)


if __name__ == "__main__":
    main()
