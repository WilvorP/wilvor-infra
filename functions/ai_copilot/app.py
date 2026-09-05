import base64
import json
import logging
import time
from decimal import Decimal
from typing import Any
from urllib.parse import unquote

from agent import AgentOrchestrator
from config import load_settings
from context import ContextBuilders
from insight_repository import InsightRepository
from model_client import (
    BedrockConverseClient,
    ModelError,
    ModelThrottled,
    ModelUnavailable,
)
from operational_api_client import (
    OperationalApiClient,
    OperationalApiInvalidResponse,
    OperationalApiNotFound,
    OperationalApiUnavailable,
)
from schemas import ValidationError, validate_chat_request
from service import AiCopilotService, UnsafeContextError
from tools import ToolRegistry


LOGGER = logging.getLogger()
LOGGER.setLevel(logging.INFO)

_RUNTIME = None


def _json_default(value: Any) -> Any:
    if isinstance(value, Decimal):
        return int(value) if value % 1 == 0 else float(
            value
        )
    raise TypeError(
        f"{type(value).__name__} is not serializable"
    )


def _response(
    status_code: int,
    body: dict[str, Any],
) -> dict[str, Any]:
    return {
        "statusCode": status_code,
        "headers": {
            "content-type": "application/json",
            "cache-control": "no-store",
        },
        "body": json.dumps(
            body,
            separators=(",", ":"),
            default=_json_default,
        ),
    }


def _request_id(event: dict[str, Any]) -> str | None:
    return (
        (event.get("requestContext") or {}).get(
            "requestId"
        )
        or event.get("id")
    )


def _runtime():
    global _RUNTIME
    if _RUNTIME is not None:
        return _RUNTIME

    settings = load_settings()
    client = OperationalApiClient(
        settings.operational_api_base_url,
        timeout_seconds=(
            settings.operational_timeout_seconds
        ),
        max_attempts=2,
    )
    builders = ContextBuilders(client)
    model = BedrockConverseClient(
        model_id=settings.bedrock_model_id,
        max_output_tokens=(
            settings.max_output_tokens
        ),
        temperature=settings.temperature,
        connect_timeout_seconds=(
            settings.bedrock_connect_timeout_seconds
        ),
        read_timeout_seconds=(
            settings.bedrock_read_timeout_seconds
        ),
    )
    insights = InsightRepository(
        settings.insights_table_name
    )
    registry = ToolRegistry(client, builders)
    agent = AgentOrchestrator(
        model_client=model,
        tool_registry=registry,
        max_tool_rounds=settings.max_tool_rounds,
        max_tool_result_bytes=(
            settings.max_tool_result_bytes
        ),
        max_operation_seconds=(
            settings.max_operation_seconds
        ),
    )
    service = AiCopilotService(
        settings=settings,
        builders=builders,
        model_client=model,
        insight_repository=insights,
        agent=agent,
    )
    _RUNTIME = {
        "settings": settings,
        "service": service,
        "insights": insights,
    }
    return _RUNTIME


def _body(
    event: dict[str, Any],
    maximum_bytes: int,
) -> dict[str, Any]:
    raw = event.get("body")
    if raw in (None, ""):
        return {}
    if not isinstance(raw, str):
        raise ValidationError(
            "Request body must be JSON"
        )
    try:
        data = (
            base64.b64decode(raw, validate=True)
            if event.get("isBase64Encoded")
            else raw.encode("utf-8")
        )
    except (ValueError, UnicodeEncodeError) as exc:
        raise ValidationError(
            "Request body encoding is invalid"
        ) from exc
    if len(data) > maximum_bytes:
        raise ValidationError(
            "Request body exceeds the size limit"
        )
    try:
        payload = json.loads(data.decode("utf-8"))
    except (
        UnicodeDecodeError,
        json.JSONDecodeError,
    ) as exc:
        raise ValidationError(
            "Request body must be valid JSON"
        ) from exc
    if not isinstance(payload, dict):
        raise ValidationError(
            "Request body must be a JSON object"
        )
    return payload


def _empty_body(payload: dict[str, Any]) -> None:
    if payload:
        raise ValidationError(
            "This endpoint does not accept request fields"
        )


def _path_id(
    path: str,
    *,
    prefix: str,
    suffix: str,
    name: str,
) -> str | None:
    if not path.startswith(prefix) or not path.endswith(
        suffix
    ):
        return None
    value = unquote(
        path[len(prefix) : len(path) - len(suffix)]
    ).strip()
    if (
        not value
        or "/" in value
        or len(value) > 256
        or any(ord(char) < 32 for char in value)
    ):
        raise ValidationError(f"{name} is invalid")
    return value


def _http_event(event: dict[str, Any]) -> bool:
    return isinstance(
        (event.get("requestContext") or {}).get(
            "http"
        ),
        dict,
    )


def _log_result(
    *,
    request_id: str | None,
    operation: str,
    subject_type: str | None,
    subject_id: str | None,
    result,
    total_latency_ms: int,
) -> None:
    payload = result.payload
    LOGGER.info(
        json.dumps(
            {
                "event": "ai_copilot.completed",
                "request_id": request_id,
                "operation": operation,
                "subject_type": subject_type,
                "subject_id": subject_id,
                "model_id": payload.get("modelId"),
                "prompt_version": payload.get(
                    "promptVersion"
                ),
                "cache_hit": (
                    payload.get("cache") or {}
                ).get("hit"),
                "tool_count": len(
                    payload.get("toolCalls") or []
                ),
                "tool_names": [
                    item.get("name")
                    for item in payload.get(
                        "toolCalls",
                        [],
                    )
                ],
                "bedrock_latency_ms": (
                    result.bedrock_latency_ms
                ),
                "total_latency_ms": total_latency_ms,
                "input_tokens": result.input_tokens,
                "output_tokens": result.output_tokens,
                "success": True,
            },
            separators=(",", ":"),
        )
    )


def _execute(
    *,
    request_id: str | None,
    operation: str,
    subject_type: str | None,
    subject_id: str | None,
    callable_,
):
    started = time.perf_counter()
    result = callable_()
    _log_result(
        request_id=request_id,
        operation=operation,
        subject_type=subject_type,
        subject_id=subject_id,
        result=result,
        total_latency_ms=int(
            (time.perf_counter() - started) * 1000
        ),
    )
    return result.payload


def _dispatch_http(
    event: dict[str, Any],
) -> dict[str, Any]:
    runtime = _runtime()
    settings = runtime["settings"]
    service = runtime["service"]
    method = str(
        event["requestContext"]["http"].get(
            "method",
            "",
        )
    ).upper()
    path = str(event.get("rawPath") or "/")
    request_id = _request_id(event)

    if method == "GET" and path == "/health":
        return _response(
            200,
            {
                "service": "wilvor-ai-copilot",
                "status": "ok",
                "requestId": request_id,
            },
        )

    if (
        method == "POST"
        and path == "/ai/summaries/network"
    ):
        _empty_body(
            _body(
                event,
                settings.max_request_bytes,
            )
        )
        return _response(
            200,
            _execute(
                request_id=request_id,
                operation="NETWORK_SUMMARY",
                subject_type="NETWORK",
                subject_id="CURRENT",
                callable_=service.network_summary,
            ),
        )

    aircraft_id = _path_id(
        path,
        prefix="/ai/aircraft/",
        suffix="/explain",
        name="aircraftId",
    )
    if method == "POST" and aircraft_id is not None:
        _empty_body(
            _body(
                event,
                settings.max_request_bytes,
            )
        )
        return _response(
            200,
            _execute(
                request_id=request_id,
                operation="AIRCRAFT_RISK_EXPLANATION",
                subject_type="AIRCRAFT",
                subject_id=aircraft_id,
                callable_=lambda: (
                    service.aircraft_explanation(
                        aircraft_id
                    )
                ),
            ),
        )

    airport_id = _path_id(
        path,
        prefix="/ai/airports/",
        suffix="/summarize",
        name="airportId",
    )
    if method == "POST" and airport_id is not None:
        _empty_body(
            _body(
                event,
                settings.max_request_bytes,
            )
        )
        return _response(
            200,
            _execute(
                request_id=request_id,
                operation="AIRPORT_SUMMARY",
                subject_type="AIRPORT",
                subject_id=airport_id,
                callable_=lambda: service.airport_summary(
                    airport_id
                ),
            ),
        )

    recommendation_id = _path_id(
        path,
        prefix="/ai/recommendations/",
        suffix="/explain",
        name="recommendationId",
    )
    if (
        method == "POST"
        and recommendation_id is not None
    ):
        _empty_body(
            _body(
                event,
                settings.max_request_bytes,
            )
        )
        return _response(
            200,
            _execute(
                request_id=request_id,
                operation=(
                    "RECOMMENDATION_EXPLANATION"
                ),
                subject_type="RECOMMENDATION",
                subject_id=recommendation_id,
                callable_=lambda: (
                    service.recommendation_explanation(
                        recommendation_id
                    )
                ),
            ),
        )

    alert_id = _path_id(
        path,
        prefix="/ai/alerts/",
        suffix="/incident-summary",
        name="alertId",
    )
    if method == "POST" and alert_id is not None:
        _empty_body(
            _body(
                event,
                settings.max_request_bytes,
            )
        )
        return _response(
            200,
            _execute(
                request_id=request_id,
                operation="INCIDENT_SUMMARY",
                subject_type="ALERT",
                subject_id=alert_id,
                callable_=lambda: (
                    service.incident_summary(alert_id)
                ),
            ),
        )

    if method == "POST" and path == "/ai/chat":
        request = validate_chat_request(
            _body(
                event,
                settings.max_request_bytes,
            ),
            max_message_chars=(
                settings.max_message_chars
            ),
            max_history_items=(
                settings.max_history_items
            ),
            max_history_item_chars=(
                settings.max_history_item_chars
            ),
        )
        subject = request.get("subject") or {}
        return _response(
            200,
            _execute(
                request_id=request_id,
                operation="CHAT",
                subject_type=subject.get("type"),
                subject_id=subject.get("id"),
                callable_=lambda: service.chat(request),
            ),
        )

    if (
        method == "GET"
        and path.startswith("/ai/insights/")
    ):
        parts = [
            unquote(item)
            for item in path.split("/")
            if item
        ]
        if len(parts) != 4:
            raise ValidationError(
                "Insight path is invalid"
            )
        subject_type = parts[2].strip().upper()
        subject_id = parts[3].strip()
        if subject_type not in {
            "NETWORK",
            "AIRCRAFT",
            "AIRPORT",
            "RECOMMENDATION",
            "ALERT",
        }:
            raise ValidationError(
                "subjectType is invalid"
            )
        if (
            not subject_id
            or len(subject_id) > 256
        ):
            raise ValidationError(
                "subjectId is invalid"
            )
        items = runtime["insights"].list_for_subject(
            subject_type=subject_type,
            subject_id=subject_id,
            limit=20,
        )
        return _response(
            200,
            {
                "items": items,
                "count": len(items),
                "requestId": request_id,
            },
        )

    if method not in {"GET", "POST"}:
        return _response(
            405,
            {
                "message": "Method not allowed",
                "requestId": request_id,
            },
        )
    return _response(
        404,
        {
            "message": "Route not found",
            "requestId": request_id,
        },
    )


def _dispatch_event(
    event: dict[str, Any],
):
    source = event.get("source")
    detail_type = event.get("detail-type")
    detail = event.get("detail") or {}
    service = _runtime()["service"]
    request_id = _request_id(event)

    if (
        source == "aws.events"
        and detail_type == "Scheduled Event"
    ):
        return _execute(
            request_id=request_id,
            operation="NETWORK_SUMMARY",
            subject_type="NETWORK",
            subject_id="CURRENT",
            callable_=service.network_summary,
        )
    if (
        source == "wilvor.risk"
        and detail_type == "risk.updated"
        and detail.get("aircraft_id")
    ):
        aircraft_id = str(detail["aircraft_id"])
        return _execute(
            request_id=request_id,
            operation="AIRCRAFT_RISK_EXPLANATION",
            subject_type="AIRCRAFT",
            subject_id=aircraft_id,
            callable_=lambda: (
                service.aircraft_explanation(
                    aircraft_id
                )
            ),
        )
    if (
        source == "wilvor.recommendation"
        and detail_type == "recommendation.updated"
        and detail.get("recommendation_id")
    ):
        recommendation_id = str(
            detail["recommendation_id"]
        )
        return _execute(
            request_id=request_id,
            operation="RECOMMENDATION_EXPLANATION",
            subject_type="RECOMMENDATION",
            subject_id=recommendation_id,
            callable_=lambda: (
                service.recommendation_explanation(
                    recommendation_id
                )
            ),
        )
    if (
        source == "wilvor.alert"
        and detail_type
        in {"alert.updated", "alert.resolved"}
        and detail.get("alert_id")
    ):
        alert_id = str(detail["alert_id"])
        return _execute(
            request_id=request_id,
            operation="INCIDENT_SUMMARY",
            subject_type="ALERT",
            subject_id=alert_id,
            callable_=lambda: (
                service.incident_summary(alert_id)
            ),
        )
    if (
        source == "wilvor.airport"
        and detail_type == "airport.status.updated"
        and detail.get("airport_id")
    ):
        airport_id = str(detail["airport_id"])
        return _execute(
            request_id=request_id,
            operation="AIRPORT_SUMMARY",
            subject_type="AIRPORT",
            subject_id=airport_id,
            callable_=lambda: service.airport_summary(
                airport_id
            ),
        )
    return {
        "processed": False,
        "reason": "UNSUPPORTED_EVENT",
    }


def lambda_handler(
    event: dict[str, Any],
    context: Any,
):
    request_id = _request_id(event)
    if not _http_event(event):
        return _dispatch_event(event)

    try:
        return _dispatch_http(event)
    except ValidationError as exc:
        return _response(
            400,
            {
                "message": str(exc),
                "requestId": request_id,
            },
        )
    except OperationalApiNotFound:
        return _response(
            404,
            {
                "message": "Operational subject not found",
                "requestId": request_id,
            },
        )
    except UnsafeContextError:
        return _response(
            422,
            {
                "message": (
                    "Deterministic context is insufficient "
                    "for this insight"
                ),
                "requestId": request_id,
            },
        )
    except ModelThrottled:
        return _response(
            429,
            {
                "message": "AI inference is throttled",
                "requestId": request_id,
            },
        )
    except (
        OperationalApiUnavailable,
        OperationalApiInvalidResponse,
    ):
        return _response(
            503,
            {
                "message": (
                    "Operational data service is unavailable"
                ),
                "requestId": request_id,
            },
        )
    except (ModelUnavailable, ModelError):
        return _response(
            502,
            {
                "message": "AI inference failed",
                "requestId": request_id,
            },
        )
    except Exception:
        LOGGER.exception(
            json.dumps(
                {
                    "event": "ai_copilot.unhandled_error",
                    "request_id": request_id,
                    "success": False,
                },
                separators=(",", ":"),
            )
        )
        return _response(
            500,
            {
                "message": "Internal server error",
                "requestId": request_id,
            },
        )
