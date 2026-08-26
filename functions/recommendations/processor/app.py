import hashlib
import json
import os
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

import boto3
from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError

dynamodb = boto3.resource("dynamodb")
events = boto3.client("events")

risk_table = dynamodb.Table(os.environ["RISK_RESULTS_TABLE_NAME"])
assessment_table = dynamodb.Table(os.environ["AIRPORT_ASSESSMENT_TABLE_NAME"])
recommendations_table = dynamodb.Table(os.environ["RECOMMENDATIONS_TABLE_NAME"])

EVENT_BUS_NAME = os.environ.get("EVENT_BUS_NAME", "default")
RETENTION_SECONDS = int(os.environ.get("RETENTION_SECONDS", "86400"))
TOP_CANDIDATE_COUNT = int(os.environ.get("TOP_CANDIDATE_COUNT", "5"))
RULESET_VERSION = os.environ.get("RULESET_VERSION", "wilvor.recommendation.ruleset.v1")
SCHEMA_VERSION = os.environ.get("SCHEMA_VERSION", "wilvor.recommendation.v4.0")

ADVISORY_NOTICE = (
    "Advisory decision support only. Human operational review is required. "
    "Wilvor does not issue autonomous flight-control, diversion, landing, dispatch, or ATC instructions."
)


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def parse_iso(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        text = str(value)
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None


def json_default(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    raise TypeError()


def to_decimal(value: Any) -> Any:
    if isinstance(value, float):
        return Decimal(str(value))
    if isinstance(value, dict):
        return {k: to_decimal(v) for k, v in value.items()}
    if isinstance(value, list):
        return [to_decimal(v) for v in value]
    return value


def digest(payload: Any) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=json_default)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def get_risk(risk_id: str) -> dict[str, Any] | None:
    return risk_table.get_item(
        Key={"risk_id": risk_id},
        ConsistentRead=True,
    ).get("Item")


def get_assessments(evaluation_id: str) -> list[dict[str, Any]]:
    items = []
    kwargs = {
        "KeyConditionExpression": Key("evaluation_id").eq(evaluation_id),
        "ConsistentRead": True,
    }
    while True:
        response = assessment_table.query(**kwargs)
        items.extend(response.get("Items", []))
        last = response.get("LastEvaluatedKey")
        if not last:
            return items
        kwargs["ExclusiveStartKey"] = last


def action_for(level: str) -> str:
    if level == "HIGH":
        return "EVALUATE_DIVERSION"
    if level == "MEDIUM":
        return "MONITOR_AND_PREPARE_OPTIONS"
    return "MONITOR"


def build_recommendation(
    risk: dict[str, Any],
    evaluation_id: str | None,
    assessments: list[dict[str, Any]],
) -> dict[str, Any]:
    now = now_utc()
    epoch = int(now.timestamp())
    level = str(risk.get("risk_level", "UNKNOWN")).upper()
    action = action_for(level)

    complete = [x for x in assessments if x.get("assessment_status") == "COMPLETE"]
    complete.sort(
        key=lambda x: (
            int(x.get("rank", 999999)),
            -float(x.get("total_airport_score", 0)),
        )
    )
    top = complete[:TOP_CANDIDATE_COUNT]
    preferred = top[0] if top else None

    summaries = []
    for row in top:
        summary = {
            "airport_id": row.get("airport_id"),
            "airport_assessment_id": row.get("airport_assessment_id"),
            "rank": row.get("rank"),
            "total_airport_score": row.get("total_airport_score"),
            "distance_nm": row.get("distance_nm"),
            "eta_minutes": row.get("eta_minutes"),
            "weather_risk_level": row.get("weather_risk_level"),
        }
        summaries.append({k: v for k, v in summary.items() if v is not None})

    reasons = list(risk.get("reasons") or [])
    limitations = list(risk.get("limitations") or [])
    for row in top:
        for limitation in row.get("known_limitations") or []:
            if limitation not in limitations:
                limitations.append(limitation)

    standard_limits = [
        "Fuel state is unavailable.",
        "Aircraft-specific performance limits are unavailable.",
        "Filed route and ATC clearance are unavailable.",
        "Airline operational policy is unavailable.",
    ]
    for value in standard_limits:
        if value not in limitations:
            limitations.append(value)

    if action == "EVALUATE_DIVERSION":
        primary_details = {
            "advisory": "Evaluate diversion options using ranked airport evidence.",
            "candidate_count": len(top),
            "requires_human_review": True,
        }
    elif action == "MONITOR_AND_PREPARE_OPTIONS":
        primary_details = {
            "advisory": "Continue monitoring and prepare operational alternatives.",
            "candidate_count": len(top),
            "requires_human_review": True,
        }
    else:
        primary_details = {
            "advisory": "Continue monitoring the aircraft-hazard condition.",
            "requires_human_review": True,
        }

    risk_valid = parse_iso(risk.get("valid_until_utc"))
    valid_until = risk_valid or (now + timedelta(minutes=15))
    if valid_until <= now:
        valid_until = now + timedelta(minutes=5)

    material = {
        "risk_id": risk["risk_id"],
        "action": action,
        "evaluation_id": evaluation_id,
        "preferred_airport_id": preferred.get("airport_id") if preferred else None,
        "candidate_summaries": summaries,
        "ruleset_version": RULESET_VERSION,
    }
    version_hash = digest(material)
    recommendation_id = f"rec#{version_hash[:40]}"

    confidence = str(risk.get("confidence", "LOW")).upper()
    no_candidate_reason = None
    if action in {"EVALUATE_DIVERSION", "MONITOR_AND_PREPARE_OPTIONS"} and not preferred:
        confidence = "LOW"
        no_candidate_reason = (
            "No candidate airport currently has a COMPLETE assessment."
            if assessments
            else "No candidate airport assessment was available."
        )
        if "No COMPLETE airport assessment is currently available." not in limitations:
            limitations.append("No COMPLETE airport assessment is currently available.")

    evidence = [{"type": "RISK_RESULT", "id": risk["risk_id"]}]
    if evaluation_id:
        evidence.append({"type": "AIRPORT_ASSESSMENT_EVALUATION", "id": evaluation_id})
    for row in top:
        evidence.append(
            {
                "type": "AIRPORT_ASSESSMENT",
                "id": row.get("airport_assessment_id"),
                "airport_id": row.get("airport_id"),
            }
        )

    item = {
        "recommendation_id": recommendation_id,
        "recommendation_version": version_hash[:16],
        "recommendation_status": "ACTIVE",
        "risk_id": risk["risk_id"],
        "aircraft_id": risk["aircraft_id"],
        "hazard_id": risk["hazard_id"],
        "hazard_source_version": risk.get("hazard_source_version", "UNKNOWN"),
        "risk_level": level,
        "risk_score": risk.get("risk_score", Decimal("0")),
        "confidence": confidence,
        "primary_action_type": action,
        "primary_action_details": primary_details,
        "alternative_actions": [
            {
                "type": "AIRPORT_OPTION",
                "airport_id": row.get("airport_id"),
                "airport_assessment_id": row.get("airport_assessment_id"),
                "score": row.get("total_airport_score"),
                "rank": row.get("rank"),
            }
            for row in top[1:]
        ],
        "reasons": reasons,
        "limitations": limitations,
        "evidence_references": evidence,
        "source_versions": {
            "hazard_source_version": risk.get("hazard_source_version", "UNKNOWN"),
            "risk_schema_version": risk.get("schema_version", "UNKNOWN"),
            "airport_evaluation_id": evaluation_id,
        },
        "ruleset_version": RULESET_VERSION,
        "valid_from_utc": iso(now),
        "valid_until_utc": iso(valid_until),
        "advisory_notice": ADVISORY_NOTICE,
        "created_at_epoch": epoch,
        "created_at_utc": iso(now),
        "updated_at_epoch": epoch,
        "updated_at_utc": iso(now),
        "correlation_id": risk.get("correlation_id") or recommendation_id,
        "schema_version": SCHEMA_VERSION,
        "expires_at_epoch": int(valid_until.timestamp()) + RETENTION_SECONDS,
    }

    if evaluation_id:
        item["airport_evaluation_id"] = evaluation_id
    if preferred:
        item["preferred_airport_id"] = preferred.get("airport_id")
        item["preferred_airport_assessment_id"] = preferred.get("airport_assessment_id")
        item["preferred_airport_score"] = preferred.get("total_airport_score")
        item["candidate_airport_summaries"] = summaries
    if no_candidate_reason:
        item["no_suitable_candidate_reason"] = no_candidate_reason

    return to_decimal(item)


def persist(item: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    try:
        recommendations_table.put_item(
            Item=item,
            ConditionExpression="attribute_not_exists(recommendation_id)",
        )
        return item, True
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") != "ConditionalCheckFailedException":
            raise
        existing = recommendations_table.get_item(
            Key={"recommendation_id": item["recommendation_id"]},
            ConsistentRead=True,
        ).get("Item")
        if not existing:
            raise RuntimeError("Recommendation idempotency conflict without stored item.")
        return existing, False


def publish(item: dict[str, Any]) -> None:
    detail = {
        "recommendation_id": item["recommendation_id"],
        "risk_id": item["risk_id"],
        "aircraft_id": item["aircraft_id"],
        "hazard_id": item["hazard_id"],
        "hazard_source_version": item["hazard_source_version"],
        "risk_level": item["risk_level"],
        "risk_score": item["risk_score"],
        "primary_action_type": item["primary_action_type"],
        "preferred_airport_id": item.get("preferred_airport_id"),
        "valid_until_utc": item["valid_until_utc"],
        "correlation_id": item["correlation_id"],
        "schema_version": item["schema_version"],
    }
    response = events.put_events(
        Entries=[
            {
                "EventBusName": EVENT_BUS_NAME,
                "Source": "wilvor.recommendation",
                "DetailType": "recommendation.updated",
                "Detail": json.dumps(detail, default=json_default, separators=(",", ":")),
            }
        ]
    )
    if int(response.get("FailedEntryCount", 0) or 0):
        raise RuntimeError(f"Failed to publish recommendation.updated: {response}")


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    detail_type = event.get("detail-type")
    detail = event.get("detail") or {}
    risk_id = str(detail.get("risk_id") or "").strip()
    if not risk_id:
        raise ValueError("Recommendation trigger missing risk_id.")

    risk = get_risk(risk_id)
    if not risk:
        return {"processed": False, "reason": "RISK_NOT_FOUND", "risk_id": risk_id}

    level = str(risk.get("risk_level", "UNKNOWN")).upper()
    evaluation_id = None
    assessments = []

    if detail_type == "risk.updated":
        if level in {"MEDIUM", "HIGH"}:
            return {
                "processed": False,
                "reason": "WAITING_FOR_AIRPORT_ASSESSMENT",
                "risk_id": risk_id,
                "risk_level": level,
            }
    elif detail_type == "airport.assessment.completed":
        evaluation_id = str(detail.get("evaluation_id") or "").strip()
        if not evaluation_id:
            raise ValueError("airport.assessment.completed missing evaluation_id.")
        assessments = get_assessments(evaluation_id)
    else:
        return {"processed": False, "reason": "UNSUPPORTED_EVENT"}

    item = build_recommendation(risk, evaluation_id, assessments)
    stored, created = persist(item)
    if created:
        publish(stored)

    return {
        "processed": True,
        "recommendation_id": stored["recommendation_id"],
        "risk_id": risk_id,
        "primary_action_type": stored["primary_action_type"],
        "preferred_airport_id": stored.get("preferred_airport_id"),
        "created": created,
    }
