"""Runway metadata Lambda entry point — parser-only implementation batch.

AWS download, S3 archival, DynamoDB snapshot replacement, metrics, and EventBridge
publishing are intentionally added in the next implementation batch. Keeping the first
batch deterministic makes the FAA parsing contract easy to test locally before deployment.
"""

from __future__ import annotations

import json
import os
from typing import Any

try:
    from .faa_parser import parse_faa_directory, parse_faa_zip
except ImportError:  # Lambda ZIP places modules at the package root.
    from faa_parser import parse_faa_directory, parse_faa_zip


def _supported_airports() -> set[str]:
    raw = os.environ.get("SUPPORTED_AIRPORT_IDS_JSON", "[]")
    parsed = json.loads(raw)
    if not isinstance(parsed, list):
        raise RuntimeError("SUPPORTED_AIRPORT_IDS_JSON must be a JSON list")
    return {str(value).strip().upper() for value in parsed if str(value).strip()}


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Local-test handler for an extracted directory or an FAA ZIP in /tmp.

    Examples:
      {"source_directory": "/tmp/faa"}
      {"source_zip_path": "/tmp/source.zip"}
    """
    event = event or {}
    supported = _supported_airports()
    if event.get("source_directory"):
        result = parse_faa_directory(
            str(event["source_directory"]), supported_airport_ids=supported
        )
    elif event.get("source_zip_path"):
        result = parse_faa_zip(str(event["source_zip_path"]), supported_airport_ids=supported)
    else:
        raise RuntimeError("source_directory or source_zip_path is required in this implementation batch")

    response = {
        "ok": True,
        "runway_count": len(result.runways),
        "airport_count": len({runway.airport_id for runway in result.runways}),
        "rejected_record_count": len(result.rejected_records),
        "runways": [runway.material_dict() | {"source_record_hash": runway.source_record_hash} for runway in result.runways],
        "rejected_records": [record.to_dict() for record in result.rejected_records],
    }
    print(json.dumps({key: value for key, value in response.items() if key not in {"runways", "rejected_records"}}))
    return response
