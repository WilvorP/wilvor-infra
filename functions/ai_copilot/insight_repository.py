import hashlib
import json
import time
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

import boto3
from botocore.config import Config
from boto3.dynamodb.conditions import Key


def _json_default(value: Any) -> Any:
    if isinstance(value, Decimal):
        return int(value) if value % 1 == 0 else float(
            value
        )
    raise TypeError(
        f"{type(value).__name__} is not serializable"
    )


def canonical_fingerprint(
    *,
    insight_type: str,
    material_context: dict[str, Any],
    model_id: str,
    prompt_version: str,
) -> str:
    canonical = json.dumps(
        {
            "insightType": insight_type,
            "context": material_context,
            "modelId": model_id,
            "promptVersion": prompt_version,
        },
        sort_keys=True,
        separators=(",", ":"),
        default=_json_default,
    )
    return hashlib.sha256(
        canonical.encode("utf-8")
    ).hexdigest()


def _to_decimal(value: Any) -> Any:
    if isinstance(value, float):
        return Decimal(str(value))
    if isinstance(value, dict):
        return {
            key: _to_decimal(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_to_decimal(item) for item in value]
    return value


def _iso(epoch: int) -> str:
    return (
        datetime.fromtimestamp(
            epoch,
            tz=timezone.utc,
        )
        .isoformat()
        .replace("+00:00", "Z")
    )


class InsightRepository:
    def __init__(
        self,
        table_name: str,
        *,
        table=None,
    ) -> None:
        self.table = table or boto3.resource(
            "dynamodb",
            config=Config(
                connect_timeout=2,
                read_timeout=3,
                retries={
                    "max_attempts": 2,
                    "mode": "standard",
                },
            ),
        ).Table(table_name)

    @staticmethod
    def subject_key(
        subject_type: str,
        subject_id: str,
    ) -> str:
        return (
            f"{subject_type.strip().upper()}#"
            f"{subject_id.strip()}"
        )

    def get_cached(
        self,
        *,
        subject_type: str,
        subject_id: str,
        insight_type: str,
        fingerprint: str,
        now_epoch: int | None = None,
    ) -> dict[str, Any] | None:
        now_epoch = now_epoch or int(time.time())
        response = self.table.query(
            KeyConditionExpression=(
                Key("subject_key").eq(
                    self.subject_key(
                        subject_type,
                        subject_id,
                    )
                )
                & Key("sort_key").begins_with(
                    f"{insight_type}#"
                )
            ),
            ScanIndexForward=False,
            Limit=1,
            ConsistentRead=True,
        )
        items = response.get("Items", [])
        if not items:
            return None
        item = items[0]
        if (
            item.get("context_fingerprint")
            != fingerprint
            or int(
                item.get(
                    "cache_valid_until_epoch",
                    0,
                )
            )
            < now_epoch
        ):
            return None
        output = item.get("output")
        return output if isinstance(output, dict) else None

    def store(
        self,
        *,
        subject_type: str,
        subject_id: str,
        insight_type: str,
        fingerprint: str,
        model_id: str,
        prompt_version: str,
        output: dict[str, Any],
        input_tokens: int,
        output_tokens: int,
        latency_ms: int,
        cache_ttl_seconds: int,
        retention_seconds: int,
        now_epoch: int | None = None,
    ) -> dict[str, Any]:
        now_epoch = now_epoch or int(time.time())
        insight_id = f"insight#{uuid.uuid4().hex}"
        generated_at = _iso(now_epoch)
        item = {
            "subject_key": self.subject_key(
                subject_type,
                subject_id,
            ),
            "sort_key": (
                f"{insight_type}#{generated_at}#"
                f"{insight_id}"
            ),
            "insight_id": insight_id,
            "subject_type": subject_type,
            "subject_id": subject_id,
            "insight_type": insight_type,
            "generated_at": generated_at,
            "generated_at_epoch": now_epoch,
            "context_fingerprint": fingerprint,
            "model_id": model_id,
            "prompt_version": prompt_version,
            "output": output,
            "evidence_references": output.get(
                "evidence",
                [],
            ),
            "limitations": output.get(
                "limitations",
                [],
            ),
            "freshness_warnings": output.get(
                "dataFreshnessWarnings",
                [],
            ),
            "token_usage": {
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
            },
            "latency_ms": latency_ms,
            "cache_valid_until_epoch": (
                now_epoch + cache_ttl_seconds
            ),
            "expires_at_epoch": (
                now_epoch + retention_seconds
            ),
        }
        self.table.put_item(
            Item=_to_decimal(item)
        )
        return item

    def list_for_subject(
        self,
        *,
        subject_type: str,
        subject_id: str,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        if limit < 1 or limit > 50:
            raise ValueError(
                "limit must be between 1 and 50"
            )
        response = self.table.query(
            KeyConditionExpression=Key(
                "subject_key"
            ).eq(
                self.subject_key(
                    subject_type,
                    subject_id,
                )
            ),
            ScanIndexForward=False,
            Limit=limit,
        )
        return response.get("Items", [])
