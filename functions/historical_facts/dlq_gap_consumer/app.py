"""Copy Domain-2 DLQ messages to durable unbound incidents, then delete.

No automatic Firehose replay. The SQS message is deleted only after the
S3 write succeeds. A failed write raises so the message stays visible.

The DLQ consumer does not invent a collection epoch. coverage_control binds
these incidents to the current epoch as COLLECTION_GAP records.
"""

from __future__ import annotations

import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from wilvor_historical.coverage_contracts import (
    CONTROL_SCHEMA_VERSION,
    STAGING_STATE,
    ControlRecordType,
    GapDomain,
    UncertaintyClass,
    UnboundCollectionIncident,
)
from wilvor_historical.time import canonicalize_utc_z


_RUNTIME_DIR = Path(__file__).resolve().parents[1] / "runtime"
if str(_RUNTIME_DIR) not in sys.path:
    sys.path.insert(0, str(_RUNTIME_DIR))

from gap_writer import write_unbound_incident_fail_open  # noqa: E402


def _now_utc() -> str:
    return canonicalize_utc_z(datetime.now(timezone.utc))


def _gap_interval_end(now_utc: str) -> str:
    parsed = datetime.fromisoformat(now_utc.replace("Z", "+00:00"))
    return canonicalize_utc_z(parsed + timedelta(seconds=1))


def incident_from_dlq_record(
    record: dict[str, Any],
    *,
    now_utc: str,
) -> UnboundCollectionIncident:
    message_id = str(record.get("messageId") or uuid.uuid4())
    return UnboundCollectionIncident(
        control_schema_version=CONTROL_SCHEMA_VERSION,
        record_type=ControlRecordType.UNBOUND_COLLECTION_INCIDENT,
        staging_state=STAGING_STATE,
        affected_datasets=("encounter", "risk", "hazard_version"),
        gap_domain=GapDomain.DOMAIN_2,
        reason="EVENTBRIDGE_TARGET_FAILURE",
        uncertainty_class=UncertaintyClass.KNOWN_MISSING,
        detected_at_utc=now_utc,
        interval_start_utc=now_utc,
        interval_end_utc=_gap_interval_end(now_utc),
        created_at_utc=now_utc,
        dedup_id=f"domain2|{message_id}",
        evidence_refs=(message_id, str(record.get("body") or "")[:2000]),
        source_subsystem="eventbridge",
        producer_source="wilvor.historical.dlq",
        producer_detail_type="domain2",
    )


def lambda_handler(event: dict[str, Any], context: Any = None) -> dict[str, Any]:
    now_utc = _now_utc()
    written = 0
    for record in event.get("Records") or []:
        if not isinstance(record, dict):
            raise RuntimeError("DLQ record is not an object")
        key = write_unbound_incident_fail_open(
            incident_from_dlq_record(record, now_utc=now_utc)
        )
        if not key:
            raise RuntimeError("Domain-2 incident write failed; leaving SQS message")
        written += 1
    return {"written": written}
