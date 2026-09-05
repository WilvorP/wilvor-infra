import json
import time
from dataclasses import dataclass, field
from typing import Any, Protocol

import boto3
from botocore.config import Config
from botocore.exceptions import (
    BotoCoreError,
    ClientError,
)

from schemas import structured_output_schema


class ModelError(RuntimeError):
    pass


class ModelThrottled(ModelError):
    pass


class ModelUnavailable(ModelError):
    pass


@dataclass
class ToolRequest:
    tool_use_id: str
    name: str
    input: Any


@dataclass
class ModelResponse:
    message: dict[str, Any]
    text: str | None
    tool_requests: list[ToolRequest] = field(
        default_factory=list
    )
    stop_reason: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    latency_ms: int | None = None


class ModelClient(Protocol):
    model_id: str

    def converse(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
    ) -> ModelResponse:
        ...


class BedrockConverseClient:
    def __init__(
        self,
        *,
        model_id: str,
        max_output_tokens: int,
        temperature: float,
        connect_timeout_seconds: float = 2.0,
        read_timeout_seconds: float = 20.0,
        client=None,
    ) -> None:
        if not model_id.strip():
            raise ValueError("model_id is required")
        if (
            max_output_tokens < 128
            or max_output_tokens > 4096
        ):
            raise ValueError(
                "max_output_tokens is out of bounds"
            )
        if temperature < 0 or temperature > 1:
            raise ValueError(
                "temperature is out of bounds"
            )
        self.model_id = model_id
        self.max_output_tokens = max_output_tokens
        self.temperature = temperature
        self.client = client or boto3.client(
            "bedrock-runtime",
            config=Config(
                connect_timeout=connect_timeout_seconds,
                read_timeout=read_timeout_seconds,
                retries={
                    "total_max_attempts": 1,
                    "mode": "standard",
                },
            ),
        )

    @staticmethod
    def _strict_tools(
        tools: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        result = []
        for tool in tools:
            if not isinstance(tool, dict):
                raise ValueError(
                    "Tool definition must be an object"
                )
            copied = dict(tool)
            specification = dict(
                copied.get("toolSpec") or {}
            )
            if not specification.get("name"):
                raise ValueError(
                    "Tool definition is missing a name"
                )
            specification["strict"] = True
            copied["toolSpec"] = specification
            result.append(copied)
        return result

    def converse(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
    ) -> ModelResponse:
        request: dict[str, Any] = {
            "modelId": self.model_id,
            "system": [
                {
                    "text": self.system_prompt
                }
            ],
            "messages": messages,
            "inferenceConfig": {
                "maxTokens": self.max_output_tokens,
                "temperature": self.temperature,
            },
            "outputConfig": {
                "textFormat": {
                    "type": "json_schema",
                    "structure": {
                        "jsonSchema": {
                            "name": "wilvor_ai_response",
                            "description": (
                                "Grounded Wilvor advisory response"
                            ),
                            "schema": json.dumps(
                                structured_output_schema(),
                                separators=(",", ":"),
                            ),
                        }
                    },
                }
            },
        }
        if tools:
            request["toolConfig"] = {
                "tools": self._strict_tools(tools),
                "toolChoice": {"auto": {}},
            }

        started = time.perf_counter()
        try:
            response = self.client.converse(**request)
        except ClientError as exc:
            code = str(
                exc.response.get("Error", {}).get(
                    "Code",
                    "",
                )
            )
            if code in {
                "ThrottlingException",
                "TooManyRequestsException",
                "ServiceQuotaExceededException",
            }:
                raise ModelThrottled(
                    "Bedrock request was throttled"
                ) from exc
            raise ModelUnavailable(
                "Bedrock inference is unavailable"
            ) from exc
        except BotoCoreError as exc:
            raise ModelUnavailable(
                "Bedrock inference is unavailable"
            ) from exc

        elapsed_ms = int(
            (time.perf_counter() - started) * 1000
        )
        message = (
            response.get("output", {}).get("message")
        )
        if not isinstance(message, dict):
            raise ModelError(
                "Bedrock response did not contain a message"
            )

        text_parts = []
        tool_requests = []
        for block in message.get("content", []):
            if not isinstance(block, dict):
                continue
            if isinstance(block.get("text"), str):
                text_parts.append(block["text"])
            tool_use = block.get("toolUse")
            if isinstance(tool_use, dict):
                tool_requests.append(
                    ToolRequest(
                        tool_use_id=str(
                            tool_use.get("toolUseId")
                            or ""
                        ),
                        name=str(
                            tool_use.get("name") or ""
                        ),
                        input=tool_use.get("input"),
                    )
                )

        usage = response.get("usage") or {}
        metrics = response.get("metrics") or {}
        return ModelResponse(
            message=message,
            text=(
                "".join(text_parts)
                if text_parts
                else None
            ),
            tool_requests=tool_requests,
            stop_reason=response.get("stopReason"),
            input_tokens=usage.get("inputTokens"),
            output_tokens=usage.get("outputTokens"),
            latency_ms=metrics.get(
                "latencyMs",
                elapsed_ms,
            ),
        )

    @property
    def system_prompt(self) -> str:
        from prompts import SYSTEM_PROMPT

        return SYSTEM_PROMPT
