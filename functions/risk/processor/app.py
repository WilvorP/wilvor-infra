import hashlib
import json
import os
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

import boto3
from botocore.exceptions import ClientError


dynamodb = boto3.resource("dynamodb")
eventbridge = boto3.client("events")
cloudwatch = boto3.client("cloudwatch")


ENVIRONMENT = os.environ.get(
    "ENVIRONMENT",
    "dev",
)

AIRCRAFT_HAZARD_ENCOUNTER_TABLE_NAME = os.environ[
    "AIRCRAFT_HAZARD_ENCOUNTER_TABLE_NAME"
]

RISK_RESULTS_TABLE_NAME = os.environ[
    "RISK_RESULTS_TABLE_NAME"
]

EVENT_BUS_NAME = os.environ.get(
    "EVENT_BUS_NAME",
    "default",
)

RISK_SCHEMA_VERSION = os.environ.get(
    "RISK_SCHEMA_VERSION",
    "wilvor.risk_results.v4.0",
)

SCORING_RULESET_VERSION = os.environ.get(
    "SCORING_RULESET_VERSION",
    "wilvor.risk.ruleset.v2",
)

SCORING_CONFIG_VERSION = os.environ.get(
    "SCORING_CONFIG_VERSION",
    "wilvor.risk.config.dev.v1",
)

RISK_RETENTION_SECONDS = int(
    os.environ.get(
        "RISK_RETENTION_SECONDS",
        "86400",
    )
)


encounter_table = dynamodb.Table(
    AIRCRAFT_HAZARD_ENCOUNTER_TABLE_NAME
)

risk_table = dynamodb.Table(
    RISK_RESULTS_TABLE_NAME
)


def now_epoch() -> int:
    return int(
        datetime.now(
            timezone.utc
        ).timestamp()
    )


def epoch_to_utc(
    epoch: int,
) -> str:
    return (
        datetime.fromtimestamp(
            epoch,
            tz=timezone.utc,
        )
        .isoformat()
        .replace(
            "+00:00",
            "Z",
        )
    )


def parse_iso_epoch(
    value: Any,
) -> int | None:
    if value is None:
        return None

    try:
        text = str(value).strip()

        if text.endswith("Z"):
            text = (
                text[:-1]
                + "+00:00"
            )

        parsed = datetime.fromisoformat(
            text
        )

        if parsed.tzinfo is None:
            parsed = parsed.replace(
                tzinfo=timezone.utc
            )

        return int(
            parsed.timestamp()
        )

    except (
        TypeError,
        ValueError,
    ):
        return None


def json_default(
    value: Any,
) -> Any:
    if isinstance(
        value,
        Decimal,
    ):
        if value % 1 == 0:
            return int(value)

        return float(value)

    raise TypeError(
        f"Unsupported type: {type(value)}"
    )


def get_encounter(
    encounter_id: str,
) -> dict[str, Any] | None:
    response = encounter_table.get_item(
        Key={
            "encounter_id": encounter_id
        },
        ConsistentRead=True,
    )

    return response.get(
        "Item"
    )


def normalize(
    value: Any,
    default: str = "UNKNOWN",
) -> str:
    if value is None:
        return default

    text = str(
        value
    ).strip().upper()

    return text or default


# ---------------------------------------------------------------------------
# Risk component scoring
#
# Advisory MVP model. Stronger operational risk requires stronger evidence.
# Unknown evidence contributes 0 positive points. Weak components cannot
# accumulate into HIGH.
#
# Intended effects:
# - Geometry: inside-now is strong; corridor-only is moderate; unknown is 0.
# - Time: already-inside / persisted horizon is strongest; window overlap
#   without entry time is moderate; unknown is 0.
# - Altitude: OVERLAP is positive evidence; UNKNOWN is 0 and lowers
#   confidence; NO_OVERLAP gates the result down.
# - Confidence / freshness change the final confidence and HIGH eligibility
#   more than they add score, to avoid double-counting projection uncertainty.
# ---------------------------------------------------------------------------

TERMINAL_ENCOUNTER_STATES = {
    "RESOLVED",
    "SUPERSEDED",
    "EXPIRED",
}


def hazard_component(
    encounter: dict[str, Any],
) -> int:
    hazard_type = normalize(
        encounter.get(
            "hazard_type"
        )
    )

    base_scores = {
        "VOLCANIC_ASH": 22,
        "CONVECTION": 18,
        "CONVECTIVE": 18,
        "TURBULENCE": 14,
        "ICING": 13,
        "IFR": 8,
        "MOUNTAIN_OBSCURATION": 8,
        "UNKNOWN": 10,
    }

    score = base_scores.get(
        hazard_type,
        10,
    )

    severity = normalize(
        encounter.get(
            "severity"
        ),
        "",
    )

    if severity in {
        "SEV",
        "SEVERE",
        "EXTREME",
    }:
        score += 3

    elif severity in {
        "MOD",
        "MODERATE",
    }:
        score += 2

    elif severity in {
        "LIGHT",
        "LGT",
    }:
        score += 1

    return min(
        score,
        25,
    )


def geometry_component(
    encounter: dict[str, Any],
) -> int:
    status = normalize(
        encounter.get(
            "geometry_overlap_status"
        )
    )

    if (
        encounter.get(
            "inside_now"
        )
        is True
        or status == "INSIDE_NOW"
    ):
        return 25

    if (
        encounter.get(
            "centerline_intersects"
        )
        is True
        or status
        == "CENTERLINE_INTERSECTION"
    ):
        return 25

    if (
        encounter.get(
            "corridor_intersects"
        )
        is True
        or status
        == "CORRIDOR_ONLY_INTERSECTION"
    ):
        return 12

    # Unknown / no intersection is not
    # positive geometry evidence.
    return 0


def threat_horizon_minutes(
    encounter: dict[str, Any],
) -> float | None:
    value = encounter.get(
        "first_intersection_horizon_min"
    )

    if value is None:
        value = encounter.get(
            "closest_approach_horizon_min"
        )

    if value is None:
        return None

    try:
        return float(
            value
        )

    except (
        TypeError,
        ValueError,
    ):
        return None


def time_component(
    encounter: dict[str, Any],
) -> int:
    if (
        encounter.get("inside_now") is True
        or normalize(
            encounter.get(
                "geometry_overlap_status"
            )
        )
        == "INSIDE_NOW"
    ):
        return 20

    horizon = threat_horizon_minutes(
        encounter
    )

    if horizon is not None:
        if horizon <= 5:
            return 20

        if horizon <= 10:
            return 16

        if horizon <= 20:
            return 10

        if horizon <= 30:
            return 6

        return 3

    status = normalize(
        encounter.get(
            "time_overlap_status"
        )
    )

    if status == "OVERLAP":
        return 8

    return 0


def altitude_component(
    encounter: dict[str, Any],
) -> int:
    status = normalize(
        encounter.get(
            "altitude_overlap_status"
        )
    )

    if status == "OVERLAP":
        return 12

    # UNKNOWN and NO_OVERLAP contribute
    # no positive altitude evidence.
    return 0


def confidence_component(
    encounter: dict[str, Any],
) -> int:
    confidence = normalize(
        encounter.get(
            "encounter_confidence"
        )
        or encounter.get(
            "trajectory_confidence"
        )
    )

    mapping = {
        "HIGH": 2,
        "MEDIUM": 1,
        "LOW": 0,
        "UNKNOWN": 0,
    }

    return mapping.get(
        confidence,
        0,
    )


def freshness_component(
    encounter: dict[str, Any],
) -> int:
    freshness = normalize(
        encounter.get(
            "freshness_status"
        ),
        "UNAVAILABLE",
    )

    mapping = {
        "FRESH": 3,
        "ACCEPTABLE": 2,
        "STALE": 0,
        "UNAVAILABLE": 0,
        "UNKNOWN": 0,
    }

    return mapping.get(
        freshness,
        0,
    )


def data_quality_component(
    encounter: dict[str, Any],
) -> int:
    score = 4

    checks = [
        normalize(
            encounter.get(
                "geometry_overlap_status"
            )
        ),
        normalize(
            encounter.get(
                "time_overlap_status"
            )
        ),
        normalize(
            encounter.get(
                "altitude_overlap_status"
            )
        ),
        normalize(
            encounter.get(
                "freshness_status"
            ),
            "UNAVAILABLE",
        ),
    ]

    for value in checks:
        if value in {
            "UNKNOWN",
            "UNAVAILABLE",
            "STALE",
        }:
            score -= 1

    return max(
        score,
        0,
    )


def build_limitations(
    encounter: dict[str, Any],
) -> list[str]:
    limitations: list[str] = []

    if normalize(
        encounter.get(
            "geometry_overlap_status"
        )
    ) == "UNKNOWN":
        limitations.append(
            "Exact geometry relationship is unknown."
        )

    if normalize(
        encounter.get(
            "time_overlap_status"
        )
    ) == "UNKNOWN":
        limitations.append(
            "Hazard time overlap is unknown."
        )

    if normalize(
        encounter.get(
            "altitude_overlap_status"
        )
    ) == "UNKNOWN":
        limitations.append(
            "Aircraft-to-hazard altitude overlap is unknown."
        )

    if normalize(
        encounter.get(
            "altitude_overlap_status"
        )
    ) == "NO_OVERLAP":
        limitations.append(
            "Hazard altitude does not overlap the projected aircraft altitude."
        )

    confidence = normalize(
        encounter.get(
            "encounter_confidence"
        )
        or encounter.get(
            "trajectory_confidence"
        )
    )

    if confidence == "LOW":
        limitations.append(
            "Projection horizon is long and trajectory confidence is low."
        )
    elif not encounter.get(
        "encounter_confidence"
    ) and not encounter.get(
        "trajectory_confidence"
    ):
        limitations.append(
            "Encounter-specific confidence is unavailable."
        )

    freshness = normalize(
        encounter.get(
            "freshness_status"
        ),
        "UNAVAILABLE",
    )

    if freshness == "STALE":
        limitations.append(
            "Aircraft state is stale."
        )
    elif freshness in {
        "UNAVAILABLE",
        "UNKNOWN",
    }:
        limitations.append(
            "Source freshness is not confirmed as current."
        )

    return limitations


def build_reasons(
    encounter: dict[str, Any],
    *,
    hazard_score: int,
    geometry_score: int,
    time_score: int,
    altitude_score: int,
) -> list[str]:
    reasons: list[str] = []

    if encounter.get("inside_now") is True or normalize(
        encounter.get("geometry_overlap_status")
    ) == "INSIDE_NOW":
        reasons.append(
            "Aircraft is already inside active hazard geometry."
        )
    elif encounter.get("centerline_intersects") is True:
        reasons.append(
            "Projected centerline intersects the hazard geometry."
        )
    elif (
        encounter.get("corridor_intersects") is True
        or normalize(
            encounter.get("geometry_overlap_status")
        )
        == "CORRIDOR_ONLY_INTERSECTION"
    ):
        reasons.append(
            "Projected corridor intersects an active hazard."
        )

    altitude = normalize(
        encounter.get("altitude_overlap_status")
    )

    if altitude == "OVERLAP":
        reasons.append(
            "Aircraft altitude overlaps the hazard altitude band."
        )
    elif altitude == "NO_OVERLAP":
        reasons.append(
            "Hazard altitude does not overlap projected aircraft altitude."
        )
    elif altitude == "UNKNOWN":
        reasons.append(
            "Altitude relationship is unknown and is not treated as overlap."
        )

    horizon = threat_horizon_minutes(
        encounter
    )

    if horizon is not None:
        reasons.append(
            f"Closest confirmed threat timing is {horizon:g} minutes."
        )
    elif normalize(
        encounter.get("time_overlap_status")
    ) == "OVERLAP":
        reasons.append(
            "Projection validity window overlaps the hazard valid time."
        )

    freshness = normalize(
        encounter.get("freshness_status"),
        "UNAVAILABLE",
    )

    if freshness == "STALE":
        reasons.append(
            "Aircraft state is stale and cannot increase operational risk."
        )

    confidence = normalize(
        encounter.get("encounter_confidence")
        or encounter.get("trajectory_confidence")
    )

    if confidence == "LOW":
        reasons.append(
            "Projection horizon is long and confidence is low."
        )

    reasons.extend(
        [
            f"Hazard component contributed {hazard_score} points.",
            f"Geometry component contributed {geometry_score} points.",
            f"Time-to-threat component contributed {time_score} points.",
            f"Altitude component contributed {altitude_score} points.",
        ]
    )

    return reasons


def geometry_is_strong(
    encounter: dict[str, Any],
) -> bool:
    status = normalize(
        encounter.get(
            "geometry_overlap_status"
        )
    )

    return (
        encounter.get("inside_now") is True
        or status == "INSIDE_NOW"
        or (
            (
                encounter.get("corridor_intersects")
                is True
                or status
                == "CORRIDOR_ONLY_INTERSECTION"
            )
            and encounter.get(
                "exact_intersection_confirmed"
            )
            is True
        )
    )


def geometry_is_weak(
    encounter: dict[str, Any],
) -> bool:
    status = normalize(
        encounter.get(
            "geometry_overlap_status"
        )
    )

    if (
        encounter.get("inside_now") is True
        or status == "INSIDE_NOW"
    ):
        return False

    if (
        encounter.get("corridor_intersects")
        is True
        or status
        == "CORRIDOR_ONLY_INTERSECTION"
    ):
        return False

    return True


def high_risk_gates_pass(
    encounter: dict[str, Any],
) -> bool:
    altitude = normalize(
        encounter.get(
            "altitude_overlap_status"
        )
    )

    confidence = normalize(
        encounter.get(
            "encounter_confidence"
        )
        or encounter.get(
            "trajectory_confidence"
        )
    )

    freshness = normalize(
        encounter.get(
            "freshness_status"
        ),
        "UNAVAILABLE",
    )

    time_status = normalize(
        encounter.get(
            "time_overlap_status"
        )
    )

    return (
        geometry_is_strong(encounter)
        and not geometry_is_weak(encounter)
        and altitude == "OVERLAP"
        and confidence in {"HIGH", "MEDIUM"}
        and freshness in {"FRESH", "ACCEPTABLE"}
        and (
            time_status == "OVERLAP"
            or encounter.get("inside_now") is True
        )
    )


def classify_risk(
    score: int,
    *,
    encounter: dict[str, Any],
) -> str:
    encounter_state = normalize(
        encounter.get(
            "encounter_state"
        )
    )

    if encounter_state in TERMINAL_ENCOUNTER_STATES:
        return "LOW"

    altitude = normalize(
        encounter.get(
            "altitude_overlap_status"
        )
    )

    freshness = normalize(
        encounter.get(
            "freshness_status"
        ),
        "UNAVAILABLE",
    )

    # Stale state and confirmed altitude
    # separation cannot increase risk.
    if freshness == "STALE" or altitude == "NO_OVERLAP":
        return "LOW"

    confidence = normalize(
        encounter.get(
            "encounter_confidence"
        )
        or encounter.get(
            "trajectory_confidence"
        )
    )

    inside = (
        encounter.get("inside_now") is True
        or normalize(
            encounter.get(
                "geometry_overlap_status"
            )
        )
        == "INSIDE_NOW"
    )

    # Corridor-only, unknown altitude, and
    # low-confidence long-horizon evidence
    # is not a meaningful MEDIUM encounter.
    if (
        not inside
        and altitude != "OVERLAP"
        and confidence == "LOW"
    ):
        return "LOW"

    if score >= 70 and high_risk_gates_pass(
        encounter
    ):
        return "HIGH"

    if score >= 40:
        return "MEDIUM"

    return "LOW"


def risk_confidence(
    encounter: dict[str, Any],
) -> str:
    altitude = normalize(
        encounter.get(
            "altitude_overlap_status"
        )
    )

    freshness = normalize(
        encounter.get(
            "freshness_status"
        ),
        "UNAVAILABLE",
    )

    confidence = normalize(
        encounter.get(
            "encounter_confidence"
        )
        or encounter.get(
            "trajectory_confidence"
        )
    )

    if (
        altitude == "UNKNOWN"
        or freshness in {"STALE", "UNAVAILABLE", "UNKNOWN"}
        or confidence in {"LOW", "UNKNOWN"}
        or geometry_is_weak(encounter)
    ):
        return "LOW"

    if confidence in {"HIGH", "MEDIUM"}:
        return confidence

    return "LOW"


def input_fingerprint(
    encounter: dict[str, Any],
) -> str:
    payload = {
        "encounter_id": encounter.get(
            "encounter_id"
        ),
        "projection_id": encounter.get(
            "projection_id"
        ),
        "aircraft_state_version": encounter.get(
            "aircraft_state_version"
        ),
        "hazard_source_version": encounter.get(
            "hazard_source_version"
        ),
        "encounter_state": encounter.get(
            "encounter_state"
        ),
        "geometry_overlap_status": encounter.get(
            "geometry_overlap_status"
        ),
        "time_overlap_status": encounter.get(
            "time_overlap_status"
        ),
        "altitude_overlap_status": encounter.get(
            "altitude_overlap_status"
        ),
        "inside_now": encounter.get(
            "inside_now"
        ),
        "corridor_intersects": encounter.get(
            "corridor_intersects"
        ),
        "exact_intersection_confirmed": encounter.get(
            "exact_intersection_confirmed"
        ),
        "first_intersection_horizon_min": encounter.get(
            "first_intersection_horizon_min"
        ),
        "trajectory_confidence": encounter.get(
            "trajectory_confidence"
        ),
        "freshness_status": encounter.get(
            "freshness_status"
        ),
        "ruleset": SCORING_RULESET_VERSION,
        "config": SCORING_CONFIG_VERSION,
    }

    canonical = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        default=json_default,
    )

    return hashlib.sha256(
        canonical.encode(
            "utf-8"
        )
    ).hexdigest()


def build_risk_result(
    encounter: dict[str, Any],
) -> dict[str, Any]:
    generated_epoch = now_epoch()

    hazard_score = hazard_component(
        encounter
    )

    geometry_score = geometry_component(
        encounter
    )

    time_score = time_component(
        encounter
    )

    altitude_score = altitude_component(
        encounter
    )

    confidence_score = confidence_component(
        encounter
    )

    freshness_score = freshness_component(
        encounter
    )

    quality_score = data_quality_component(
        encounter
    )

    score = min(
        100,
        (
            hazard_score
            + geometry_score
            + time_score
            + altitude_score
            + confidence_score
            + freshness_score
            + quality_score
        ),
    )

    encounter_state = normalize(
        encounter.get(
            "encounter_state"
        )
    )

    if encounter_state in TERMINAL_ENCOUNTER_STATES:
        score = 0

    risk_level = classify_risk(
        score,
        encounter=encounter,
    )

    fingerprint = input_fingerprint(
        encounter
    )

    risk_id = (
        "risk#"
        f"{fingerprint[:40]}"
    )

    valid_until_utc = encounter.get(
        "valid_to_utc"
    )

    valid_until_epoch = parse_iso_epoch(
        valid_until_utc
    )

    if valid_until_epoch is None:
        valid_until_epoch = (
            generated_epoch
            + 900
        )

        valid_until_utc = epoch_to_utc(
            valid_until_epoch
        )

    expires_at_epoch = (
        max(
            generated_epoch,
            valid_until_epoch,
        )
        + RISK_RETENTION_SECONDS
    )

    freshness_status = normalize(
        encounter.get(
            "freshness_status"
        ),
        "UNAVAILABLE",
    )

    item = {
        "risk_id": risk_id,

        "encounter_id": (
            encounter[
                "encounter_id"
            ]
        ),

        "aircraft_id": (
            encounter[
                "aircraft_id"
            ]
        ),

        "hazard_id": (
            encounter[
                "hazard_id"
            ]
        ),

        "hazard_source_version": (
            encounter[
                "hazard_source_version"
            ]
        ),

        "projection_id": (
            encounter[
                "projection_id"
            ]
        ),

        "hazard_type": (
            encounter.get(
                "hazard_type",
                "UNKNOWN",
            )
        ),

        "risk_score": (
            Decimal(
                str(score)
            )
        ),

        "risk_level": (
            risk_level
        ),

        "hazard_component_score": (
            Decimal(
                str(hazard_score)
            )
        ),

        "geometry_component_score": (
            Decimal(
                str(geometry_score)
            )
        ),

        "time_component_score": (
            Decimal(
                str(time_score)
            )
        ),

        "altitude_component_score": (
            Decimal(
                str(altitude_score)
            )
        ),

        "confidence_component_score": (
            Decimal(
                str(confidence_score)
            )
        ),

        "freshness_component_score": (
            Decimal(
                str(freshness_score)
            )
        ),

        "data_quality_component_score": (
            Decimal(
                str(quality_score)
            )
        ),

        "confidence": (
            risk_confidence(
                encounter
            )
        ),

        "freshness_status": (
            freshness_status
        ),

        "reasons": (
            build_reasons(
                encounter,
                hazard_score=(
                    hazard_score
                ),
                geometry_score=(
                    geometry_score
                ),
                time_score=(
                    time_score
                ),
                altitude_score=(
                    altitude_score
                ),
            )
        ),

        "limitations": (
            build_limitations(
                encounter
            )
        ),

        "scoring_ruleset_version": (
            SCORING_RULESET_VERSION
        ),

        "scoring_config_version": (
            SCORING_CONFIG_VERSION
        ),

        "generated_at_epoch": (
            Decimal(
                str(
                    generated_epoch
                )
            )
        ),

        "generated_at_utc": (
            epoch_to_utc(
                generated_epoch
            )
        ),

        "valid_until_utc": (
            valid_until_utc
        ),

        "correlation_id": (
            encounter.get(
                "correlation_id"
            )
            or risk_id
        ),

        "schema_version": (
            RISK_SCHEMA_VERSION
        ),

        "expires_at_epoch": (
            Decimal(
                str(
                    expires_at_epoch
                )
            )
        ),
    }

    severity = encounter.get(
        "severity"
    )

    if severity is not None:
        item[
            "severity"
        ] = severity

    return item


def persist_risk_result(
    item: dict[str, Any],
) -> tuple[
    dict[str, Any],
    bool,
]:
    try:
        risk_table.put_item(
            Item=item,
            ConditionExpression=(
                "attribute_not_exists(risk_id)"
            ),
        )

        return (
            item,
            True,
        )

    except ClientError as exc:
        code = (
            exc.response
            .get(
                "Error",
                {},
            )
            .get(
                "Code"
            )
        )

        if (
            code
            != "ConditionalCheckFailedException"
        ):
            raise

        response = risk_table.get_item(
            Key={
                "risk_id": (
                    item[
                        "risk_id"
                    ]
                )
            },
            ConsistentRead=True,
        )

        existing = response.get(
            "Item"
        )

        if existing is None:
            raise RuntimeError(
                "Risk idempotency conflict but existing record was not found."
            )

        return (
            existing,
            False,
        )


def publish_risk_event(
    *,
    item: dict[str, Any],
    encounter_state: str,
) -> str:
    detail_type = (
        "risk.resolved"
        if encounter_state
        in TERMINAL_ENCOUNTER_STATES
        else "risk.updated"
    )

    detail = {
        "risk_id": (
            item[
                "risk_id"
            ]
        ),

        "encounter_id": (
            item[
                "encounter_id"
            ]
        ),

        "aircraft_id": (
            item[
                "aircraft_id"
            ]
        ),

        "hazard_id": (
            item[
                "hazard_id"
            ]
        ),

        "hazard_source_version": (
            item[
                "hazard_source_version"
            ]
        ),

        "projection_id": (
            item[
                "projection_id"
            ]
        ),

        "risk_score": (
            item[
                "risk_score"
            ]
        ),

        "risk_level": (
            item[
                "risk_level"
            ]
        ),

        "confidence": (
            item[
                "confidence"
            ]
        ),

        "freshness_status": (
            item[
                "freshness_status"
            ]
        ),

        "generated_at_epoch": (
            item[
                "generated_at_epoch"
            ]
        ),

        "generated_at_utc": (
            item[
                "generated_at_utc"
            ]
        ),

        "valid_until_utc": (
            item[
                "valid_until_utc"
            ]
        ),

        "correlation_id": (
            item[
                "correlation_id"
            ]
        ),

        "schema_version": (
            item[
                "schema_version"
            ]
        ),
    }

    response = eventbridge.put_events(
        Entries=[
            {
                "EventBusName": (
                    EVENT_BUS_NAME
                ),

                "Source": (
                    "wilvor.risk"
                ),

                "DetailType": (
                    detail_type
                ),

                "Detail": json.dumps(
                    detail,
                    default=json_default,
                    separators=(",", ":"),
                ),
            }
        ]
    )

    if int(
        response.get(
            "FailedEntryCount",
            0,
        )
        or 0
    ):
        raise RuntimeError(
            (
                "Failed to publish "
                f"{detail_type}: "
                f"{response}"
            )
        )

    return detail_type


def emit_metrics(
    *,
    item: dict[str, Any],
    created: bool,
) -> None:
    cloudwatch.put_metric_data(
        Namespace=(
            "Wilvor/Pipeline"
        ),
        MetricData=[
            {
                "MetricName": (
                    "RiskResultsWritten"
                ),
                "Value": (
                    1
                    if created
                    else 0
                ),
                "Unit": "Count",
                "Dimensions": [
                    {
                        "Name": "Environment",
                        "Value": ENVIRONMENT,
                    },
                    {
                        "Name": "Pipeline",
                        "Value": "risk",
                    },
                    {
                        "Name": "Component",
                        "Value": "risk_processor",
                    },
                    {
                        "Name": "Stage",
                        "Value": "scoring",
                    },
                ],
            },
            {
                "MetricName": (
                    "RiskEvaluations"
                ),
                "Value": 1,
                "Unit": "Count",
                "Dimensions": [
                    {
                        "Name": "Environment",
                        "Value": ENVIRONMENT,
                    },
                    {
                        "Name": "Pipeline",
                        "Value": "risk",
                    },
                    {
                        "Name": "Component",
                        "Value": "risk_processor",
                    },
                    {
                        "Name": "Stage",
                        "Value": "scoring",
                    },
                ],
            },
        ],
    )


def lambda_handler(
    event: dict[str, Any],
    context: Any,
) -> dict[str, Any]:
    detail = (
        event.get(
            "detail",
            {}
        )
        or {}
    )

    encounter_id = str(
        detail.get(
            "encounter_id",
            "",
        )
    ).strip()

    if not encounter_id:
        raise ValueError(
            (
                "Encounter event missing "
                "encounter_id"
            )
        )

    encounter = get_encounter(
        encounter_id
    )

    if encounter is None:
        return {
            "processed": False,
            "reason": (
                "ENCOUNTER_NOT_FOUND"
            ),
            "encounter_id": (
                encounter_id
            ),
        }

    risk_item = build_risk_result(
        encounter
    )

    stored_item, created = (
        persist_risk_result(
            risk_item
        )
    )

    encounter_state = normalize(
        encounter.get(
            "encounter_state"
        )
    )

    detail_type = None

    if created or encounter_state in TERMINAL_ENCOUNTER_STATES:
        detail_type = publish_risk_event(
            item=stored_item,
            encounter_state=(
                encounter_state
            ),
        )

    emit_metrics(
        item=stored_item,
        created=created,
    )

    return {
        "processed": True,
        "risk_id": (
            stored_item[
                "risk_id"
            ]
        ),
        "encounter_id": (
            encounter_id
        ),
        "risk_score": (
            stored_item[
                "risk_score"
            ]
        ),
        "risk_level": (
            stored_item[
                "risk_level"
            ]
        ),
        "confidence": (
            stored_item[
                "confidence"
            ]
        ),
        "created": created,
        "published_event": (
            detail_type
        ),
    }