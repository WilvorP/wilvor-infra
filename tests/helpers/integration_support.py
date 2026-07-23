"""Reusable support for deployed Wilvor integration tests."""

from __future__ import annotations

import base64
import json
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Callable


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def decimal_to_native(value: Any) -> Any:
    if isinstance(value, Decimal):
        if value == value.to_integral_value():
            return int(value)
        return float(value)

    if isinstance(value, dict):
        return {
            key: decimal_to_native(inner)
            for key, inner in value.items()
        }

    if isinstance(value, list):
        return [decimal_to_native(inner) for inner in value]

    return value


def recursively_contains(value: Any, expected: str) -> bool:
    if isinstance(value, str):
        return expected in value

    if isinstance(value, dict):
        return any(
            recursively_contains(key, expected)
            or recursively_contains(inner, expected)
            for key, inner in value.items()
        )

    if isinstance(value, list):
        return any(
            recursively_contains(inner, expected)
            for inner in value
        )

    return False


def wait_until(
    description: str,
    operation: Callable[[], Any],
    *,
    timeout_seconds: float,
    interval_seconds: float,
) -> Any:
    deadline = time.monotonic() + timeout_seconds
    last_error: Exception | None = None

    while time.monotonic() < deadline:
        try:
            result = operation()
            if result:
                return result
        except Exception as error:  # eventual consistency/transient read
            last_error = error

        time.sleep(interval_seconds)

    suffix = f" Last error: {last_error}" if last_error else ""
    raise AssertionError(
        f"Timed out after {timeout_seconds:.0f}s waiting for "
        f"{description}.{suffix}"
    )


def kinesis_event(
    payload: Any,
    *,
    sequence_number: str | None = None,
) -> dict[str, Any]:
    identifier = sequence_number or f"it-{uuid.uuid4().hex}"
    encoded = base64.b64encode(
        json.dumps(payload, separators=(",", ":"), default=str)
        .encode("utf-8")
    ).decode("ascii")

    return {
        "Records": [
            {
                "eventSource": "aws:kinesis",
                "eventID": f"event-{identifier}",
                "kinesis": {
                    "sequenceNumber": identifier,
                    "approximateArrivalTimestamp": time.time(),
                    "data": encoded,
                },
            }
        ]
    }


def put_json_record(
    kinesis_client,
    *,
    stream_name: str,
    partition_key: str,
    payload: Any,
) -> dict[str, Any]:
    return kinesis_client.put_record(
        StreamName=stream_name,
        PartitionKey=partition_key,
        Data=json.dumps(
            payload,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8"),
    )


def invoke_lambda(
    lambda_client,
    *,
    function_name: str,
    event: dict[str, Any],
) -> dict[str, Any]:
    response = lambda_client.invoke(
        FunctionName=function_name,
        InvocationType="RequestResponse",
        Payload=json.dumps(event, default=str).encode("utf-8"),
    )

    payload_bytes = response["Payload"].read()
    payload_text = payload_bytes.decode("utf-8") if payload_bytes else ""

    if response.get("FunctionError"):
        raise AssertionError(
            f"Lambda {function_name} returned "
            f"{response['FunctionError']}: {payload_text}"
        )

    if response["StatusCode"] != 200:
        raise AssertionError(
            f"Lambda {function_name} returned status "
            f"{response['StatusCode']}: {payload_text}"
        )

    if not payload_text:
        return {}

    decoded = json.loads(payload_text)

    if isinstance(decoded, str):
        try:
            decoded = json.loads(decoded)
        except json.JSONDecodeError:
            pass

    if not isinstance(decoded, dict):
        raise AssertionError(
            f"Lambda {function_name} returned a non-object payload: "
            f"{decoded!r}"
        )

    return decoded


def get_item(
    dynamodb_resource,
    *,
    table_name: str,
    key: dict[str, Any],
) -> dict[str, Any] | None:
    response = dynamodb_resource.Table(table_name).get_item(
        Key=key,
        ConsistentRead=True,
    )
    item = response.get("Item")
    return item if isinstance(item, dict) else None


def wait_for_item(
    dynamodb_resource,
    *,
    table_name: str,
    key: dict[str, Any],
    predicate: Callable[[dict[str, Any]], bool],
    timeout_seconds: float,
    interval_seconds: float,
) -> dict[str, Any]:
    def read_matching_item() -> dict[str, Any] | None:
        item = get_item(
            dynamodb_resource,
            table_name=table_name,
            key=key,
        )

        if item and predicate(item):
            return item

        return None

    return wait_until(
        f"DynamoDB item {table_name} {key}",
        read_matching_item,
        timeout_seconds=timeout_seconds,
        interval_seconds=interval_seconds,
    )


def delete_item(
    dynamodb_resource,
    *,
    table_name: str,
    key: dict[str, Any],
) -> None:
    dynamodb_resource.Table(table_name).delete_item(Key=key)


def _recent_s3_objects(
    s3_client,
    *,
    bucket: str,
    prefix: str,
    started_at: datetime,
) -> list[dict[str, Any]]:
    objects: list[dict[str, Any]] = []
    paginator = s3_client.get_paginator("list_objects_v2")

    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for item in page.get("Contents", []):
            last_modified = item.get("LastModified")

            if (
                isinstance(last_modified, datetime)
                and last_modified >= started_at - timedelta(seconds=5)
            ):
                objects.append(item)

    return sorted(
        objects,
        key=lambda item: item["LastModified"],
        reverse=True,
    )


def wait_for_s3_json(
    s3_client,
    *,
    bucket: str,
    prefix: str,
    started_at: datetime,
    predicate: Callable[[dict[str, Any]], bool],
    timeout_seconds: float,
    interval_seconds: float,
) -> tuple[str, dict[str, Any]]:
    examined: set[str] = set()

    def find_object() -> tuple[str, dict[str, Any]] | None:
        for item in _recent_s3_objects(
            s3_client,
            bucket=bucket,
            prefix=prefix,
            started_at=started_at,
        ):
            key = item["Key"]

            if key in examined:
                continue

            examined.add(key)
            response = s3_client.get_object(Bucket=bucket, Key=key)
            body = json.loads(response["Body"].read().decode("utf-8"))

            if isinstance(body, dict) and predicate(body):
                return key, body

        return None

    return wait_until(
        f"S3 JSON object under s3://{bucket}/{prefix}",
        find_object,
        timeout_seconds=timeout_seconds,
        interval_seconds=interval_seconds,
    )


def event_detail(message: str) -> dict[str, Any] | None:
    try:
        envelope = json.loads(message)
    except json.JSONDecodeError:
        return None

    if not isinstance(envelope, dict):
        return None

    detail = envelope.get("detail")

    if isinstance(detail, str):
        try:
            detail = json.loads(detail)
        except json.JSONDecodeError:
            return None

    return detail if isinstance(detail, dict) else None


def weather_events(
    logs_client,
    *,
    log_group_name: str,
    started_at: datetime,
    token: str,
) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    paginator = logs_client.get_paginator("filter_log_events")

    for page in paginator.paginate(
        logGroupName=log_group_name,
        startTime=int(
            (started_at - timedelta(seconds=5)).timestamp() * 1000
        ),
        filterPattern=f'"{token}"',
        interleaved=True,
    ):
        for event in page.get("events", []):
            message = event.get("message", "")

            if token not in message:
                continue

            detail = event_detail(message)

            if detail is not None:
                events.append(
                    {
                        "event_id": event.get("eventId"),
                        "timestamp": event.get("timestamp"),
                        "detail": detail,
                        "message": message,
                    }
                )

    unique: dict[str, dict[str, Any]] = {}

    for event in events:
        key = str(
            event.get("event_id")
            or f"{event.get('timestamp')}:{event.get('message')}"
        )
        unique[key] = event

    return list(unique.values())


def wait_for_weather_event(
    logs_client,
    *,
    log_group_name: str,
    started_at: datetime,
    token: str,
    predicate: Callable[[dict[str, Any]], bool],
    timeout_seconds: float,
    interval_seconds: float,
) -> dict[str, Any]:
    def find_event() -> dict[str, Any] | None:
        for event in weather_events(
            logs_client,
            log_group_name=log_group_name,
            started_at=started_at,
            token=token,
        ):
            if predicate(event["detail"]):
                return event

        return None

    return wait_until(
        f"Weather.changed event containing {token}",
        find_event,
        timeout_seconds=timeout_seconds,
        interval_seconds=interval_seconds,
    )


@dataclass
class CleanupRegistry:
    keep_artifacts: bool = False
    callbacks: list[Callable[[], None]] = field(default_factory=list)

    def add(self, callback: Callable[[], None]) -> None:
        self.callbacks.append(callback)

    def run(self) -> None:
        if self.keep_artifacts:
            return

        errors: list[str] = []

        for callback in reversed(self.callbacks):
            try:
                callback()
            except Exception as error:
                errors.append(str(error))

        if errors:
            raise AssertionError(
                "Integration cleanup failed:\n- "
                + "\n- ".join(errors)
            )
