from datetime import datetime, timezone
from typing import Any

from wilvor_aircraft.schemas import AIRCRAFT_BAD_RECORD_SCHEMA_VERSION


def now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def build_bad_record(
    *,
    source: str,
    poll_id: str | None,
    raw_index: int | None,
    reasons: list[str],
    raw_record: Any,
    stage: str,
) -> dict[str, Any]:
    return {
        "schema_version": AIRCRAFT_BAD_RECORD_SCHEMA_VERSION,
        "source": source,
        "poll_id": poll_id,
        "raw_index": raw_index,
        "stage": stage,
        "reasons": reasons,
        "rejected_at_utc": now_utc_iso(),
        "raw_record": raw_record,
    }