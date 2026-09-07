"""Pure authoritative semantics for Wilvor operational current sets.

Every time-dependent function requires an explicit reference epoch. This
module deliberately performs no wall-clock, AWS, environment, or network I/O.
"""

from datetime import datetime, timezone
from typing import Any, Iterable


CURRENT_ENCOUNTER_STATES = (
    "DETECTED",
    "MONITORING",
)

TERMINAL_ENCOUNTER_STATES = (
    "RESOLVED",
    "SUPERSEDED",
    "EXPIRED",
)

CURRENT_ALERT_STATES = (
    "NEW",
    "MONITORING",
    "ESCALATED",
    "UPDATED",
)


def _as_int(value: Any) -> int | None:
    if value is None:
        return None

    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _text(value: Any) -> str:
    if value is None:
        return ""

    return str(value).strip()


def _parse_iso_epoch(value: Any) -> int | None:
    if not value:
        return None

    try:
        text = str(value).strip()

        if text.endswith("Z"):
            text = text[:-1] + "+00:00"

        parsed = datetime.fromisoformat(text)

        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)

        return int(parsed.timestamp())
    except (TypeError, ValueError):
        return None


def _is_strictly_future_iso(value: Any, now_epoch: int) -> bool:
    epoch = _parse_iso_epoch(value)
    return epoch is not None and epoch > now_epoch


def _is_unexpired_materialized_record(
    item: dict[str, Any] | None,
    now_epoch: int,
) -> bool:
    if not item:
        return False

    expires_at = _as_int(item.get("expires_at_epoch"))
    return expires_at is not None and expires_at > now_epoch


def is_current_aircraft(
    item: dict[str, Any] | None,
    now_epoch: int,
) -> bool:
    """Return whether an AircraftCurrentState row is current for API lists."""

    return _is_unexpired_materialized_record(item, now_epoch)


def is_current_airport_status(
    item: dict[str, Any] | None,
    now_epoch: int,
) -> bool:
    """Return whether an AirportStatus row is current for API lists."""

    return _is_unexpired_materialized_record(item, now_epoch)


def is_current_projection(
    item: dict[str, Any] | None,
    now_epoch: int,
) -> bool:
    if not item:
        return False

    if _text(item.get("projection_status")).upper() != "READY":
        return False

    valid_until = _as_int(item.get("valid_until_epoch"))
    return valid_until is not None and valid_until > now_epoch


def index_current_projections(
    projections: Iterable[dict[str, Any]],
    now_epoch: int,
) -> dict[str, str]:
    """Index the latest READY, still-valid projection ID per aircraft."""

    best: dict[str, tuple[int, str, str]] = {}

    for item in projections:
        if not is_current_projection(item, now_epoch):
            continue

        aircraft_id = _text(item.get("aircraft_id")).lower()
        projection_id = _text(item.get("projection_id"))

        if not aircraft_id or not projection_id:
            continue

        generated_at = _as_int(item.get("generated_at_epoch")) or 0
        previous = best.get(aircraft_id)

        if previous is None or (generated_at, projection_id) > (
            previous[0],
            previous[1],
        ):
            best[aircraft_id] = (
                generated_at,
                projection_id,
                projection_id,
            )

    return {
        aircraft_id: projection_id
        for aircraft_id, (_, _, projection_id) in best.items()
    }


def is_lifecycle_active_hazard(
    item: dict[str, Any] | None,
    now_epoch: int,
) -> bool:
    if not item:
        return False

    if _text(item.get("status")).upper() != "ACTIVE":
        return False

    valid_to = _as_int(item.get("valid_to_epoch"))
    return valid_to is not None and valid_to >= now_epoch


def is_current_hazard(
    item: dict[str, Any] | None,
    now_epoch: int,
) -> bool:
    if not is_lifecycle_active_hazard(item, now_epoch):
        return False

    return (
        _text((item or {}).get("materialization_status")).upper()
        == "READY"
    )


def index_current_hazard_versions(
    hazards: Iterable[dict[str, Any]],
    now_epoch: int,
) -> dict[str, str]:
    current: dict[str, str] = {}

    for item in hazards:
        if not is_current_hazard(item, now_epoch):
            continue

        hazard_id = _text(item.get("hazard_id"))
        source_version = _text(item.get("source_version"))

        if not hazard_id or not source_version:
            continue

        current[hazard_id] = source_version

    return current


def is_current_encounter(
    item: dict[str, Any] | None,
    *,
    current_projection_ids: dict[str, str],
    current_hazard_versions: dict[str, str],
) -> bool:
    if not item:
        return False

    state = _text(item.get("encounter_state")).upper()

    if state not in CURRENT_ENCOUNTER_STATES:
        return False

    aircraft_id = _text(item.get("aircraft_id")).lower()
    projection_id = _text(item.get("projection_id"))
    hazard_id = _text(item.get("hazard_id"))
    hazard_version = _text(
        item.get("hazard_source_version")
        or (
            str(item.get("hazard_version_key", "")).split("#")[-1]
            if item.get("hazard_version_key")
            else ""
        )
    )

    if not aircraft_id or not projection_id or not hazard_id:
        return False

    if current_projection_ids.get(aircraft_id) != projection_id:
        return False

    if current_hazard_versions.get(hazard_id) != hazard_version:
        return False

    return True


def is_current_risk(
    item: dict[str, Any] | None,
    *,
    current_encounter_ids: set[str],
    now_epoch: int,
) -> bool:
    """Return current-risk eligibility, preserving missing-validity behavior."""

    if not item:
        return False

    encounter_id = item.get("encounter_id")

    if not encounter_id or encounter_id not in current_encounter_ids:
        return False

    valid_until = item.get("valid_until_utc")

    if valid_until and not _is_strictly_future_iso(valid_until, now_epoch):
        return False

    return True


def choose_latest_current_risk(
    existing: dict[str, Any] | None,
    candidate: dict[str, Any] | None,
    *,
    current_encounter_ids: set[str],
    now_epoch: int,
) -> dict[str, Any] | None:
    """Choose a candidate only when newer; equal epochs retain ``existing``."""

    if not is_current_risk(
        candidate,
        current_encounter_ids=current_encounter_ids,
        now_epoch=now_epoch,
    ):
        return existing

    current_epoch = int((candidate or {}).get("generated_at_epoch", 0) or 0)
    existing_epoch = int(
        (existing or {}).get("generated_at_epoch", 0) or 0
    )

    if existing is None or current_epoch > existing_epoch:
        return candidate

    return existing


def index_latest_current_risks(
    risks: Iterable[dict[str, Any]],
    *,
    current_encounter_ids: set[str],
    now_epoch: int,
) -> dict[str, dict[str, Any]]:
    """Index current risks by encounter while preserving input-order ties."""

    latest_by_encounter: dict[str, dict[str, Any]] = {}

    for risk in risks:
        encounter_id = risk.get("encounter_id")
        existing = latest_by_encounter.get(encounter_id)
        selected = choose_latest_current_risk(
            existing,
            risk,
            current_encounter_ids=current_encounter_ids,
            now_epoch=now_epoch,
        )

        if selected is not existing and encounter_id:
            latest_by_encounter[encounter_id] = selected

    return latest_by_encounter


def is_current_recommendation(
    item: dict[str, Any] | None,
    *,
    current_risk_ids: set[str],
) -> bool:
    """Compatibility meaning: ACTIVE status plus current-risk lineage."""

    if not item:
        return False

    if _text(item.get("recommendation_status")).upper() != "ACTIVE":
        return False

    risk_id = _text(item.get("risk_id"))
    return bool(risk_id) and risk_id in current_risk_ids


def is_lifecycle_active_recommendation(
    item: dict[str, Any] | None,
    now_epoch: int,
) -> bool:
    if not item:
        return False

    if _text(item.get("recommendation_status")).upper() != "ACTIVE":
        return False

    return _is_strictly_future_iso(item.get("valid_until_utc"), now_epoch)


def is_current_recommendation_at(
    item: dict[str, Any] | None,
    *,
    current_risk_ids: set[str],
    now_epoch: int,
) -> bool:
    return is_lifecycle_active_recommendation(
        item,
        now_epoch,
    ) and is_current_recommendation(
        item,
        current_risk_ids=current_risk_ids,
    )


def is_current_alert(
    item: dict[str, Any] | None,
    *,
    current_risk_ids: set[str],
    current_recommendation_ids: set[str],
) -> bool:
    """Compatibility meaning: active state plus risk/recommendation OR join."""

    if not item:
        return False

    if _text(item.get("alert_state")).upper() not in CURRENT_ALERT_STATES:
        return False

    risk_id = _text(item.get("risk_id"))
    recommendation_id = _text(item.get("recommendation_id"))

    if risk_id and risk_id in current_risk_ids:
        return True

    if (
        recommendation_id
        and recommendation_id in current_recommendation_ids
    ):
        return True

    return False


def is_lifecycle_active_alert(
    item: dict[str, Any] | None,
    now_epoch: int,
) -> bool:
    if not item:
        return False

    if _text(item.get("alert_state")).upper() not in CURRENT_ALERT_STATES:
        return False

    return _is_strictly_future_iso(item.get("valid_until_utc"), now_epoch)


def is_current_alert_at(
    item: dict[str, Any] | None,
    *,
    current_risk_ids: set[str],
    current_recommendation_ids: set[str],
    now_epoch: int,
) -> bool:
    return is_lifecycle_active_alert(
        item,
        now_epoch,
    ) and is_current_alert(
        item,
        current_risk_ids=current_risk_ids,
        current_recommendation_ids=current_recommendation_ids,
    )
