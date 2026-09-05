import json
import time
from dataclasses import dataclass, field
from typing import Any

from model_client import ModelError


@dataclass
class AgentResult:
    output: Any
    evidence_catalog: list[dict[str, Any]] = field(
        default_factory=list
    )
    limitations: list[str] = field(
        default_factory=list
    )
    freshness_warnings: list[str] = field(
        default_factory=list
    )
    tool_calls: list[dict[str, Any]] = field(
        default_factory=list
    )
    input_tokens: int = 0
    output_tokens: int = 0
    bedrock_latency_ms: int = 0


def _merge_catalog(
    target: dict[str, dict[str, Any]],
    value: Any,
) -> None:
    if not isinstance(value, list):
        return
    for item in value:
        if (
            isinstance(item, dict)
            and isinstance(
                item.get("evidenceId"),
                str,
            )
        ):
            target[item["evidenceId"]] = item


def _merge_strings(
    target: list[str],
    value: Any,
) -> None:
    if not isinstance(value, list):
        return
    for item in value:
        if (
            isinstance(item, str)
            and item.strip()
            and item.strip() not in target
        ):
            target.append(item.strip())


class AgentOrchestrator:
    def __init__(
        self,
        *,
        model_client,
        tool_registry,
        max_tool_rounds: int,
        max_tool_result_bytes: int,
        max_operation_seconds: float = 25.0,
    ) -> None:
        if max_tool_rounds < 1 or max_tool_rounds > 8:
            raise ValueError(
                "max_tool_rounds is out of bounds"
            )
        self.model_client = model_client
        self.tool_registry = tool_registry
        self.max_tool_rounds = max_tool_rounds
        self.max_tool_result_bytes = (
            max_tool_result_bytes
        )
        if (
            max_operation_seconds < 5
            or max_operation_seconds > 28
        ):
            raise ValueError(
                "max_operation_seconds is out of bounds"
            )
        self.max_operation_seconds = (
            max_operation_seconds
        )

    def run(
        self,
        messages: list[dict[str, Any]],
        *,
        require_tools: bool = False,
        required_tool_name: str | None = None,
        required_tool_input: dict[str, str] | None = None,
    ) -> AgentResult:
        working_messages = list(messages)
        tool_metadata = []
        successful_tool_inputs: list[
            tuple[str, dict[str, Any]]
        ] = []
        catalog: dict[str, dict[str, Any]] = {}
        limitations: list[str] = []
        warnings: list[str] = []
        input_tokens = 0
        output_tokens = 0
        latency_ms = 0
        tool_rounds = 0
        grounding_reminder_used = False
        failure_limitation = (
            "The bounded agent tool-call limit was reached."
        )
        deadline = (
            time.monotonic()
            + self.max_operation_seconds
        )

        while time.monotonic() < deadline:
            if deadline - time.monotonic() < 10.5:
                failure_limitation = (
                    "Insufficient invocation time remained "
                    "for another bounded model call."
                )
                break
            response = self.model_client.converse(
                working_messages,
                tools=self.tool_registry.definitions(),
            )
            input_tokens += int(
                response.input_tokens or 0
            )
            output_tokens += int(
                response.output_tokens or 0
            )
            latency_ms += int(
                response.latency_ms or 0
            )

            if not response.tool_requests:
                successful_tools = [
                    name
                    for name, _ in successful_tool_inputs
                ]
                grounding_satisfied = bool(
                    successful_tools
                ) and (
                    required_tool_name is None
                    or any(
                        name == required_tool_name
                        and (
                            required_tool_input is None
                            or value
                            == required_tool_input
                        )
                        for (
                            name,
                            value,
                        ) in successful_tool_inputs
                    )
                )
                if require_tools and not grounding_satisfied:
                    if grounding_reminder_used:
                        failure_limitation = (
                            "The model did not use an approved "
                            "tool to ground the operational answer."
                        )
                        break
                    working_messages.append(
                        response.message
                    )
                    working_messages.append(
                        {
                            "role": "user",
                            "content": [
                                {
                                    "text": (
                                        "Do not answer from memory. "
                                        "Use one or more approved "
                                        "Wilvor tools to retrieve "
                                        "current evidence"
                                        + (
                                            f", including {required_tool_name}"
                                            if required_tool_name
                                            else ""
                                        )
                                        + "."
                                    )
                                }
                            ],
                        }
                    )
                    grounding_reminder_used = True
                    continue
                if response.text is None:
                    raise ModelError(
                        "Model returned neither text nor tools"
                    )
                return AgentResult(
                    output=response.text,
                    evidence_catalog=[
                        catalog[key]
                        for key in sorted(catalog)
                    ],
                    limitations=limitations,
                    freshness_warnings=warnings,
                    tool_calls=tool_metadata,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    bedrock_latency_ms=latency_ms,
                )

            if len(response.tool_requests) > 8:
                failure_limitation = (
                    "The model requested more tools than "
                    "the per-round safety limit."
                )
                break

            if tool_rounds >= self.max_tool_rounds:
                break

            working_messages.append(
                response.message
            )
            tool_results = []
            for request in response.tool_requests:
                if deadline - time.monotonic() < 5.5:
                    failure_limitation = (
                        "Insufficient invocation time remained "
                        "for another bounded operational tool."
                    )
                    break
                execution = self.tool_registry.execute(
                    tool_use_id=request.tool_use_id,
                    name=request.name,
                    value=request.input,
                )
                raw = json.dumps(
                    execution.result,
                    separators=(",", ":"),
                    default=str,
                ).encode("utf-8")
                if len(raw) > self.max_tool_result_bytes:
                    execution.status = "FAILURE"
                    execution.result = {
                        "error": (
                            "Approved tool result exceeded "
                            "the configured size limit"
                        )
                    }

                tool_metadata.append(
                    execution.metadata()
                )
                if (
                    execution.status == "SUCCESS"
                    and isinstance(
                        request.input,
                        dict,
                    )
                ):
                    successful_tool_inputs.append(
                        (
                            request.name,
                            request.input,
                        )
                    )
                _merge_catalog(
                    catalog,
                    execution.result.get(
                        "evidenceCatalog"
                    ),
                )
                _merge_strings(
                    limitations,
                    execution.result.get(
                        "limitations"
                    ),
                )
                _merge_strings(
                    warnings,
                    execution.result.get(
                        "dataFreshnessWarnings"
                    ),
                )
                tool_results.append(
                    {
                        "toolResult": {
                            "toolUseId": (
                                execution.tool_use_id
                            ),
                            "content": [
                                {
                                    "json": (
                                        execution.result
                                    )
                                }
                            ],
                            "status": (
                                "success"
                                if execution.status
                                == "SUCCESS"
                                else "error"
                            ),
                        }
                    }
                )
            if len(tool_results) != len(
                response.tool_requests
            ):
                break
            working_messages.append(
                {
                    "role": "user",
                    "content": tool_results,
                }
            )
            tool_rounds += 1

        return AgentResult(
            output={
                "answer": (
                    "A grounded answer could not be completed "
                    "within the configured agent safety limits."
                ),
                "evidence": [],
                "confidence": "UNKNOWN",
                "limitations": [
                    failure_limitation
                ],
                "dataFreshnessWarnings": warnings,
            },
            evidence_catalog=[
                catalog[key]
                for key in sorted(catalog)
            ],
            limitations=limitations
            + [
                failure_limitation
            ],
            freshness_warnings=warnings,
            tool_calls=tool_metadata,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            bedrock_latency_ms=latency_ms,
        )
