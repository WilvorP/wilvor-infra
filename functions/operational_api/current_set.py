"""
Shared operational current-set definitions.

TTL / expires_at is physical retention only. It is never sufficient to
decide that a row is operationally current.
"""

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


def is_current_projection(
    item: dict[str, Any] | None,
    now_epoch: int,
) -> bool:
    if not item:
        return False

    if _text(item.get("projection_status")).upper() != "READY":
        return False

    valid_until = _as_int(item.get("valid_until_epoch"))

    if valid_until is None or valid_until <= now_epoch:
        return False

    return True


def index_current_projections(
    projections: Iterable[dict[str, Any]],
    now_epoch: int,
) -> dict[str, str]:
    """
    Latest READY, still-valid projection_id per aircraft.

    "Latest" is generated_at_epoch, then projection_id for stability.
    """

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


def is_current_hazard(
    item: dict[str, Any] | None,
    now_epoch: int,
) -> bool:
    if not item:
        return False

    if _text(item.get("status")).upper() != "ACTIVE":
        return False

    if _text(item.get("materialization_status")).upper() != "READY":
        return False

    valid_to = _as_int(item.get("valid_to_epoch"))

    if valid_to is None or valid_to < now_epoch:
        return False

    return True


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
    """
    A current encounter is a still-valid aircraft-hazard relationship
    supported by the aircraft's current projection and the hazard's
    current source version.
    """

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


def is_current_recommendation(
    item: dict[str, Any] | None,
    *,
    current_risk_ids: set[str],
) -> bool:
    if not item:
        return False

    if _text(item.get("recommendation_status")).upper() != "ACTIVE":
        return False

    risk_id = _text(item.get("risk_id"))

    if not risk_id:
        return False

    return risk_id in current_risk_ids


def is_current_alert(
    item: dict[str, Any] | None,
    *,
    current_risk_ids: set[str],
    current_recommendation_ids: set[str],
) -> bool:
    if not item:
        return False

    if _text(item.get("alert_state")).upper() not in CURRENT_ALERT_STATES:
        return False

    risk_id = _text(item.get("risk_id"))
    recommendation_id = _text(item.get("recommendation_id"))

    if risk_id and risk_id in current_risk_ids:
        return True

    if recommendation_id and recommendation_id in current_recommendation_ids:
        return True

    return False
