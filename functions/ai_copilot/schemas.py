import json
from datetime import datetime, timezone
from typing import Any

from evidence import validate_evidence_references


CONFIDENCE_VALUES = {
    "HIGH",
    "MEDIUM",
    "LOW",
    "UNKNOWN",
}

SUBJECT_TYPES = {
    "AIRCRAFT",
    "AIRPORT",
    "RECOMMENDATION",
    "ALERT",
}

ADVISORY_NOTICE = (
    "Advisory only; qualified human review is required."
)


class ValidationError(ValueError):
    pass


def now_iso() -> str:
    return (
        datetime.now(timezone.utc)
        .isoformat()
        .replace("+00:00", "Z")
    )


def structured_output_schema() -> dict[str, Any]:
    string_list = {
        "type": "array",
        "items": {"type": "string"},
    }
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "answer": {"type": "string"},
            "evidence": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "evidenceId": {
                            "type": "string"
                        },
                        "label": {"type": "string"},
                    },
                    "required": [
                        "evidenceId",
                        "label",
                    ],
                },
            },
            "confidence": {
                "type": "string",
                "enum": [
                    "HIGH",
                    "MEDIUM",
                    "LOW",
                    "UNKNOWN",
                ],
            },
            "limitations": string_list,
            "dataFreshnessWarnings": string_list,
        },
        "required": [
            "answer",
            "evidence",
            "confidence",
            "limitations",
            "dataFreshnessWarnings",
        ],
    }


def parse_json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if not isinstance(value, str):
        raise ValidationError(
            "Model output must be a JSON object"
        )
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as exc:
        raise ValidationError(
            "Model output was not valid JSON"
        ) from exc
    if not isinstance(parsed, dict):
        raise ValidationError(
            "Model output must be a JSON object"
        )
    return parsed


def _bounded_text(
    value: Any,
    name: str,
    *,
    maximum: int,
    allow_empty: bool = False,
) -> str:
    if not isinstance(value, str):
        raise ValidationError(f"{name} must be a string")
    text = value.strip()
    if not text and not allow_empty:
        raise ValidationError(f"{name} is required")
    if len(text) > maximum:
        raise ValidationError(
            f"{name} exceeds the length limit"
        )
    return text


def validate_chat_request(
    payload: Any,
    *,
    max_message_chars: int,
    max_history_items: int,
    max_history_item_chars: int,
) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValidationError(
            "Request body must be a JSON object"
        )
    allowed = {"message", "history", "subject"}
    if any(key not in allowed for key in payload):
        raise ValidationError(
            "Request body contains unknown fields"
        )

    message = _bounded_text(
        payload.get("message"),
        "message",
        maximum=max_message_chars,
    )
    history = payload.get("history", [])
    if not isinstance(history, list):
        raise ValidationError("history must be an array")
    if len(history) > max_history_items:
        raise ValidationError(
            "history contains too many items"
        )

    clean_history = []
    for index, item in enumerate(history):
        if not isinstance(item, dict):
            raise ValidationError(
                f"history[{index}] must be an object"
            )
        if set(item) != {"role", "content"}:
            raise ValidationError(
                f"history[{index}] has invalid fields"
            )
        role = item.get("role")
        if role not in {"user", "assistant"}:
            raise ValidationError(
                f"history[{index}].role is invalid"
            )
        content = _bounded_text(
            item.get("content"),
            f"history[{index}].content",
            maximum=max_history_item_chars,
        )
        clean_history.append(
            {"role": role, "content": content}
        )

    subject = payload.get("subject")
    clean_subject = None
    if subject is not None:
        if (
            not isinstance(subject, dict)
            or set(subject) != {"type", "id"}
        ):
            raise ValidationError(
                "subject must contain only type and id"
            )
        subject_type = str(
            subject.get("type") or ""
        ).strip().upper()
        if subject_type not in SUBJECT_TYPES:
            raise ValidationError(
                "subject.type is invalid"
            )
        subject_id = _bounded_text(
            subject.get("id"),
            "subject.id",
            maximum=256,
        )
        clean_subject = {
            "type": subject_type,
            "id": subject_id,
        }

    return {
        "message": message,
        "history": clean_history,
        "subject": clean_subject,
    }


def validate_model_output(
    value: Any,
    *,
    evidence_catalog: list[dict[str, Any]],
    required_limitations: list[str],
    required_freshness_warnings: list[str],
) -> dict[str, Any]:
    payload = parse_json_object(value)
    answer = _bounded_text(
        payload.get("answer"),
        "answer",
        maximum=12000,
    )
    if ADVISORY_NOTICE.lower() not in answer.lower():
        answer = f"{answer}\n\n{ADVISORY_NOTICE}"

    confidence = str(
        payload.get("confidence") or "UNKNOWN"
    ).strip().upper()
    if confidence not in CONFIDENCE_VALUES:
        confidence = "UNKNOWN"

    def strings(name: str, maximum_items: int) -> list[str]:
        raw = payload.get(name, [])
        if not isinstance(raw, list):
            return []
        result = []
        for item in raw[:maximum_items]:
            if isinstance(item, str):
                text = item.strip()
                if text and len(text) <= 1000:
                    result.append(text)
        return result

    limitations = _merge_unique(
        strings("limitations", 30),
        required_limitations,
    )
    warnings = _merge_unique(
        strings("dataFreshnessWarnings", 20),
        required_freshness_warnings,
    )
    if warnings and confidence in {"HIGH", "MEDIUM"}:
        confidence = "LOW"
    evidence = validate_evidence_references(
        payload.get("evidence"),
        evidence_catalog,
    )
    if evidence_catalog and not evidence:
        answer = (
            "A grounded explanation could not be produced "
            "because the model supplied no valid Wilvor "
            "evidence references.\n\n"
            f"{ADVISORY_NOTICE}"
        )
        confidence = "UNKNOWN"
        limitations = _merge_unique(
            limitations,
            [
                "The model response contained no validated evidence references."
            ],
        )

    return {
        "answer": answer,
        "evidence": evidence,
        "confidence": confidence,
        "limitations": limitations,
        "dataFreshnessWarnings": warnings,
    }


def _merge_unique(*values: list[str]) -> list[str]:
    result = []
    seen = set()
    for collection in values:
        for value in collection:
            text = str(value).strip()
            if text and text not in seen:
                seen.add(text)
                result.append(text)
    return result
