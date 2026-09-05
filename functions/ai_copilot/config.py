import os
from dataclasses import dataclass


class ConfigurationError(ValueError):
    pass


def _required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise ConfigurationError(f"{name} is required")
    return value


def _bounded_int(
    name: str,
    default: int,
    minimum: int,
    maximum: int,
) -> int:
    raw = os.environ.get(name, str(default))
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise ConfigurationError(
            f"{name} must be an integer"
        ) from exc
    if value < minimum or value > maximum:
        raise ConfigurationError(
            f"{name} must be between {minimum} and {maximum}"
        )
    return value


def _bounded_float(
    name: str,
    default: float,
    minimum: float,
    maximum: float,
) -> float:
    raw = os.environ.get(name, str(default))
    try:
        value = float(raw)
    except (TypeError, ValueError) as exc:
        raise ConfigurationError(
            f"{name} must be a number"
        ) from exc
    if value < minimum or value > maximum:
        raise ConfigurationError(
            f"{name} must be between {minimum} and {maximum}"
        )
    return value


@dataclass(frozen=True)
class Settings:
    operational_api_base_url: str
    insights_table_name: str
    bedrock_model_id: str
    prompt_version: str
    max_output_tokens: int
    temperature: float
    max_tool_rounds: int
    max_message_chars: int
    max_history_items: int
    max_history_item_chars: int
    max_request_bytes: int
    max_context_bytes: int
    max_tool_result_bytes: int
    operational_timeout_seconds: float
    bedrock_connect_timeout_seconds: float
    bedrock_read_timeout_seconds: float
    max_operation_seconds: float
    cache_ttl_seconds: int
    insight_retention_seconds: int


def load_settings() -> Settings:
    return Settings(
        operational_api_base_url=_required(
            "OPERATIONAL_API_BASE_URL"
        ).rstrip("/"),
        insights_table_name=_required(
            "AI_INSIGHTS_TABLE_NAME"
        ),
        bedrock_model_id=_required(
            "BEDROCK_MODEL_ID"
        ),
        prompt_version=os.environ.get(
            "PROMPT_VERSION",
            "wilvor-ai-v1",
        ).strip()
        or "wilvor-ai-v1",
        max_output_tokens=_bounded_int(
            "AI_MAX_OUTPUT_TOKENS",
            1200,
            128,
            4096,
        ),
        temperature=_bounded_float(
            "AI_TEMPERATURE",
            0.1,
            0.0,
            1.0,
        ),
        max_tool_rounds=_bounded_int(
            "AI_MAX_TOOL_ROUNDS",
            4,
            1,
            8,
        ),
        max_message_chars=_bounded_int(
            "AI_MAX_MESSAGE_CHARS",
            4000,
            100,
            12000,
        ),
        max_history_items=_bounded_int(
            "AI_MAX_HISTORY_ITEMS",
            10,
            0,
            30,
        ),
        max_history_item_chars=_bounded_int(
            "AI_MAX_HISTORY_ITEM_CHARS",
            2000,
            100,
            6000,
        ),
        max_request_bytes=_bounded_int(
            "AI_MAX_REQUEST_BYTES",
            32768,
            1024,
            131072,
        ),
        max_context_bytes=_bounded_int(
            "AI_MAX_CONTEXT_BYTES",
            131072,
            8192,
            524288,
        ),
        max_tool_result_bytes=_bounded_int(
            "AI_MAX_TOOL_RESULT_BYTES",
            65536,
            4096,
            262144,
        ),
        operational_timeout_seconds=_bounded_float(
            "OPERATIONAL_API_TIMEOUT_SECONDS",
            5.0,
            0.5,
            8.0,
        ),
        bedrock_connect_timeout_seconds=_bounded_float(
            "BEDROCK_CONNECT_TIMEOUT_SECONDS",
            2.0,
            0.5,
            10.0,
        ),
        bedrock_read_timeout_seconds=_bounded_float(
            "BEDROCK_READ_TIMEOUT_SECONDS",
            10.0,
            2.0,
            20.0,
        ),
        max_operation_seconds=_bounded_float(
            "AI_MAX_OPERATION_SECONDS",
            25.0,
            5.0,
            28.0,
        ),
        cache_ttl_seconds=_bounded_int(
            "AI_CACHE_TTL_SECONDS",
            300,
            0,
            86400,
        ),
        insight_retention_seconds=_bounded_int(
            "AI_INSIGHT_RETENTION_SECONDS",
            604800,
            3600,
            31536000,
        ),
    )
