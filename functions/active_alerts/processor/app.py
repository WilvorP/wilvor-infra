import hashlib
import json
import os
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

import boto3
from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError

dynamodb = boto3.resource("dynamodb")
events = boto3.client("events")

recommendations_table = dynamodb.Table(os.environ["RECOMMENDATIONS_TABLE_NAME"])
risk_table = dynamodb.Table(os.environ["RISK_RESULTS_TABLE_NAME"])
alerts_table = dynamodb.Table(os.environ["ACTIVE_ALERTS_TABLE_NAME"])

RISK_RESULTS_ENCOUNTER_INDEX_NAME = os.environ.get(
    "RISK_RESULTS_ENCOUNTER_INDEX_NAME",
    "encounter_id-generated_at_epoch-index",
)
AIRCRAFT_ALERT_INDEX_NAME = os.environ.get(
    "AIRCRAFT_ALERT_INDEX_NAME",
    "aircraft_id-updated_at_epoch-index",
)
EVENT_BUS_NAME = os.environ.get("EVENT_BUS_NAME", "default")
RETENTION_SECONDS = int(os.environ.get("RETENTION_SECONDS", "86400"))
SCHEMA_VERSION = os.environ.get("SCHEMA_VERSION", "wilvor.active_alert.v4.0")

ACTIVE_STATES = {"NEW", "MONITORING", "ESCALATED", "UPDATED"}
RISK_RANK = {"UNKNOWN": 0, "LOW": 1, "MEDIUM": 2, "HIGH": 3}


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def json_default(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    raise TypeError()


def digest(payload: Any) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=json_default)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def get_recommendation(recommendation_id: str) -> dict[str, Any] | None:
    return recommendations_table.get_item(
        Key={"recommendation_id": recommendation_id},
        ConsistentRead=True,
    ).get("Item")


def get_risk(risk_id: str) -> dict[str, Any] | None:
    return risk_table.get_item(
        Key={"risk_id": risk_id},
        ConsistentRead=True,
    ).get("Item")


def risks_for_encounter(encounter_id: str) -> list[dict[str, Any]]:
    items = []
    kwargs = {
        "IndexName": RISK_RESULTS_ENCOUNTER_INDEX_NAME,
        "KeyConditionExpression": Key("encounter_id").eq(encounter_id),
    }
    while True:
        response = risk_table.query(**kwargs)
        items.extend(response.get("Items", []))
        last = response.get("LastEvaluatedKey")
        if not last:
            return items
        kwargs["ExclusiveStartKey"] = last


def alerts_for_aircraft(aircraft_id: str) -> list[dict[str, Any]]:
    items = []
    kwargs = {
        "IndexName": AIRCRAFT_ALERT_INDEX_NAME,
        "KeyConditionExpression": Key("aircraft_id").eq(aircraft_id),
        "ScanIndexForward": False,
    }
    while True:
        response = alerts_table.query(**kwargs)
        items.extend(response.get("Items", []))
        last = response.get("LastEvaluatedKey")
        if not last:
            return items
        kwargs["ExclusiveStartKey"] = last


def fingerprint(rec: dict[str, Any]) -> str:
    material = {
        "aircraft_id": rec["aircraft_id"],
        "hazard_id": rec["hazard_id"],
        "hazard_source_version": rec.get("hazard_source_version", "UNKNOWN"),
        "risk_level": rec.get("risk_level", "UNKNOWN"),
        "primary_action_type": rec.get("primary_action_type", "MONITOR"),
        "preferred_airport_id": rec.get("preferred_airport_id"),
    }
    return f"alertfp#{digest(material)[:48]}"


def message(rec: dict[str, Any]) -> str:
    value = (
        f"Aircraft {rec['aircraft_id']} has {rec.get('risk_level', 'UNKNOWN')} "
        f"weather-hazard risk. Advisory action: {rec.get('primary_action_type', 'MONITOR')}."
    )
    if rec.get("preferred_airport_id"):
        value += f" Preferred airport option: {rec['preferred_airport_id']}."
    return value


def publish(detail_type: str, item: dict[str, Any]) -> None:
    detail = {
        "alert_id": item["alert_id"],
        "fingerprint": item["fingerprint"],
        "aircraft_id": item["aircraft_id"],
        "hazard_id": item["hazard_id"],
        "risk_id": item["risk_id"],
        "recommendation_id": item["recommendation_id"],
        "risk_level": item["risk_level"],
        "alert_state": item["alert_state"],
        "primary_action_type": item["primary_action_type"],
        "preferred_airport_id": item.get("preferred_airport_id"),
        "state_reason": item["state_reason"],
        "correlation_id": item["correlation_id"],
        "schema_version": item["schema_version"],
    }
    response = events.put_events(
        Entries=[
            {
                "EventBusName": EVENT_BUS_NAME,
                "Source": "wilvor.alert",
                "DetailType": detail_type,
                "Detail": json.dumps(detail, default=json_default, separators=(",", ":")),
            }
        ]
    )
    if int(response.get("FailedEntryCount", 0) or 0):
        raise RuntimeError(f"Failed to publish {detail_type}: {response}")


def resolve_item(
    item: dict[str, Any],
    reason: str,
    superseded_by_alert_id: str | None = None,
) -> bool:
    if item.get("alert_state") not in ACTIVE_STATES:
        return False

    now = now_utc()
    epoch = int(now.timestamp())
    values = {
        ":resolved": "RESOLVED",
        ":reason": reason,
        ":resolved_at": iso(now),
        ":updated_epoch": epoch,
        ":updated_utc": iso(now),
        ":expires": epoch + RETENTION_SECONDS,
        ":new": "NEW",
        ":monitoring": "MONITORING",
        ":escalated": "ESCALATED",
        ":updated": "UPDATED",
    }
    update = (
        "SET alert_state = :resolved, state_reason = :reason, "
        "resolved_at_utc = :resolved_at, updated_at_epoch = :updated_epoch, "
        "updated_at_utc = :updated_utc, expires_at_epoch = :expires"
    )
    if superseded_by_alert_id:
        update += ", superseded_by_alert_id = :superseded"
        values[":superseded"] = superseded_by_alert_id

    try:
        response = alerts_table.update_item(
            Key={"fingerprint": item["fingerprint"]},
            UpdateExpression=update,
            ExpressionAttributeValues=values,
            ConditionExpression=(
                "alert_state IN (:new, :monitoring, :escalated, :updated)"
            ),
            ReturnValues="ALL_NEW",
        )
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
            return False
        raise

    publish("alert.resolved", response["Attributes"])
    return True


def handle_recommendation(detail: dict[str, Any]) -> dict[str, Any]:
    recommendation_id = str(detail.get("recommendation_id") or "").strip()
    if not recommendation_id:
        raise ValueError("recommendation.updated missing recommendation_id.")

    rec = get_recommendation(recommendation_id)
    if not rec:
        return {"processed": False, "reason": "RECOMMENDATION_NOT_FOUND"}

    level = str(rec.get("risk_level", "UNKNOWN")).upper()
    if level not in {"MEDIUM", "HIGH"}:
        return {
            "processed": False,
            "reason": "RISK_LEVEL_BELOW_ALERT_THRESHOLD",
            "risk_level": level,
        }

    fp = fingerprint(rec)
    existing = alerts_table.get_item(
        Key={"fingerprint": fp},
        ConsistentRead=True,
    ).get("Item")

    if existing and existing.get("recommendation_id") == recommendation_id:
        return {
            "processed": True,
            "deduplicated": True,
            "alert_id": existing["alert_id"],
        }

    now = now_utc()
    epoch = int(now.timestamp())

    if existing:
        count = int(existing.get("notification_count", 0)) + 1
        response = alerts_table.update_item(
            Key={"fingerprint": fp},
            UpdateExpression=(
                "SET recommendation_id = :rec, risk_id = :risk, risk_level = :level, "
                "risk_score = :score, primary_action_type = :action, "
                "alert_state = :state, state_reason = :reason, message = :message, "
                "notification_count = :count, last_notified_at_utc = :notified, "
                "updated_at_epoch = :epoch, updated_at_utc = :utc, "
                "valid_until_utc = :valid, correlation_id = :corr, "
                "expires_at_epoch = :expires"
            ),
            ExpressionAttributeValues={
                ":rec": recommendation_id,
                ":risk": rec["risk_id"],
                ":level": level,
                ":score": rec.get("risk_score", Decimal("0")),
                ":action": rec.get("primary_action_type", "MONITOR"),
                ":state": "UPDATED",
                ":reason": "Supporting recommendation changed materially.",
                ":message": message(rec),
                ":count": count,
                ":notified": iso(now),
                ":epoch": epoch,
                ":utc": iso(now),
                ":valid": rec["valid_until_utc"],
                ":corr": rec.get("correlation_id") or existing["alert_id"],
                ":expires": int(rec.get("expires_at_epoch", epoch + RETENTION_SECONDS)),
            },
            ReturnValues="ALL_NEW",
        )
        updated = response["Attributes"]
        publish("alert.updated", updated)
        return {
            "processed": True,
            "deduplicated": False,
            "alert_id": updated["alert_id"],
            "alert_state": updated["alert_state"],
        }

    related = [
        x for x in alerts_for_aircraft(rec["aircraft_id"])
        if x.get("hazard_id") == rec["hazard_id"]
        and x.get("alert_state") in ACTIVE_STATES
    ]
    previous_rank = max(
        (RISK_RANK.get(str(x.get("risk_level", "UNKNOWN")).upper(), 0) for x in related),
        default=-1,
    )
    state = "ESCALATED" if previous_rank >= 0 and RISK_RANK[level] > previous_rank else "NEW"

    alert_id = f"alert#{digest({'fingerprint': fp})[:40]}"
    item = {
        "fingerprint": fp,
        "alert_id": alert_id,
        "aircraft_id": rec["aircraft_id"],
        "hazard_id": rec["hazard_id"],
        "hazard_source_version": rec.get("hazard_source_version", "UNKNOWN"),
        "recommendation_id": recommendation_id,
        "risk_id": rec["risk_id"],
        "risk_level": level,
        "risk_score": rec.get("risk_score", Decimal("0")),
        "primary_action_type": rec.get("primary_action_type", "MONITOR"),
        "alert_type": "WEATHER_HAZARD_RISK",
        "alert_state": state,
        "state_reason": (
            "Risk severity increased."
            if state == "ESCALATED"
            else "New material weather-hazard advisory condition."
        ),
        "message": message(rec),
        "notification_count": 1,
        "last_notified_at_utc": iso(now),
        "created_at_utc": iso(now),
        "updated_at_epoch": epoch,
        "updated_at_utc": iso(now),
        "valid_until_utc": rec["valid_until_utc"],
        "correlation_id": rec.get("correlation_id") or alert_id,
        "schema_version": SCHEMA_VERSION,
        "expires_at_epoch": int(rec.get("expires_at_epoch", epoch + RETENTION_SECONDS)),
    }
    if rec.get("preferred_airport_id"):
        item["preferred_airport_id"] = rec["preferred_airport_id"]

    try:
        alerts_table.put_item(
            Item=item,
            ConditionExpression="attribute_not_exists(fingerprint)",
        )
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") != "ConditionalCheckFailedException":
            raise
        stored = alerts_table.get_item(Key={"fingerprint": fp}).get("Item")
        return {
            "processed": True,
            "deduplicated": True,
            "alert_id": stored["alert_id"] if stored else alert_id,
        }

    for prior in related:
        if prior.get("fingerprint") != fp:
            resolve_item(
                prior,
                "Superseded by a materially changed recommendation.",
                superseded_by_alert_id=alert_id,
            )

    publish("alert.updated", item)
    return {
        "processed": True,
        "deduplicated": False,
        "alert_id": alert_id,
        "alert_state": state,
    }


def resolve_risk(risk_id: str, aircraft_id: str | None = None) -> int:
    if not aircraft_id:
        risk = get_risk(risk_id)
        if not risk:
            return 0
        aircraft_id = risk.get("aircraft_id")
    if not aircraft_id:
        return 0

    count = 0
    for item in alerts_for_aircraft(str(aircraft_id)):
        if item.get("risk_id") == risk_id:
            count += int(resolve_item(item, "Supporting risk result resolved."))
    return count


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    detail_type = event.get("detail-type")
    detail = event.get("detail") or {}

    if detail_type == "recommendation.updated":
        return handle_recommendation(detail)

    if detail_type == "risk.resolved":
        risk_id = str(detail.get("risk_id") or "").strip()
        if not risk_id:
            raise ValueError("risk.resolved missing risk_id.")
        return {
            "processed": True,
            "risk_id": risk_id,
            "resolved_alert_count": resolve_risk(
                risk_id,
                aircraft_id=detail.get("aircraft_id"),
            ),
        }

    if detail_type == "encounter.resolved":
        encounter_id = str(detail.get("encounter_id") or "").strip()
        if not encounter_id:
            raise ValueError("encounter.resolved missing encounter_id.")
        risks = risks_for_encounter(encounter_id)
        resolved = sum(
            resolve_risk(r["risk_id"], aircraft_id=r.get("aircraft_id"))
            for r in risks
        )
        return {
            "processed": True,
            "encounter_id": encounter_id,
            "resolved_alert_count": resolved,
        }

    return {"processed": False, "reason": "UNSUPPORTED_EVENT"}
