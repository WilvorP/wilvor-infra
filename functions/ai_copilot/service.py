import json
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Callable

from context import material_context
from insight_repository import canonical_fingerprint
from model_client import ModelError
from prompts import (
    chat_message,
    context_message,
)
from schemas import (
    ValidationError,
    now_iso,
    validate_model_output,
)


@dataclass
class ServiceResult:
    payload: dict[str, Any]
    input_tokens: int = 0
    output_tokens: int = 0
    bedrock_latency_ms: int = 0


class UnsafeContextError(RuntimeError):
    pass


class AiCopilotService:
    def __init__(
        self,
        *,
        settings,
        builders,
        model_client,
        insight_repository,
        agent,
    ) -> None:
        self.settings = settings
        self.builders = builders
        self.model_client = model_client
        self.insights = insight_repository
        self.agent = agent

    def _fixed(
        self,
        insight_type: str,
        builder: Callable[[], dict[str, Any]],
    ) -> ServiceResult:
        context = builder()
        context_size = len(
            json.dumps(
                context,
                separators=(",", ":"),
                default=str,
            ).encode("utf-8")
        )
        if context_size > self.settings.max_context_bytes:
            raise UnsafeContextError(
                "Decision Context exceeded the configured size limit"
            )
        if (
            insight_type
            == "AIRCRAFT_RISK_EXPLANATION"
            and not context.get("risks")
        ):
            raise UnsafeContextError(
                "No deterministic risk result is available for this aircraft"
            )
        if (
            insight_type == "AIRPORT_SUMMARY"
            and not context.get("airport")
        ):
            raise UnsafeContextError(
                "No deterministic airport status is available"
            )
        if (
            insight_type
            == "RECOMMENDATION_EXPLANATION"
            and not context.get("recommendation")
        ):
            raise UnsafeContextError(
                "No deterministic recommendation is available"
            )
        if (
            insight_type == "INCIDENT_SUMMARY"
            and not context.get("alert")
        ):
            raise UnsafeContextError(
                "No deterministic alert is available"
            )
        subject = context["subject"]
        fingerprint = canonical_fingerprint(
            insight_type=insight_type,
            material_context=material_context(context),
            model_id=self.settings.bedrock_model_id,
            prompt_version=self.settings.prompt_version,
        )
        cached = self.insights.get_cached(
            subject_type=subject["type"],
            subject_id=subject["id"],
            insight_type=insight_type,
            fingerprint=fingerprint,
        )
        if cached:
            payload = deepcopy(cached)
            payload["cache"] = {"hit": True}
            return ServiceResult(payload=payload)

        response = self.model_client.converse(
            [
                {
                    "role": "user",
                    "content": [
                        {
                            "text": context_message(
                                insight_type,
                                context,
                            )
                        }
                    ],
                }
            ]
        )
        try:
            validated = validate_model_output(
                response.text,
                evidence_catalog=context.get(
                    "evidenceCatalog",
                    [],
                ),
                required_limitations=context.get(
                    "limitations",
                    [],
                ),
                required_freshness_warnings=context.get(
                    "dataFreshnessWarnings",
                    [],
                ),
            )
        except ValidationError as exc:
            raise ModelError(
                "Model returned an invalid structured response"
            ) from exc
        payload = self._public_payload(
            validated,
            tool_calls=[],
            cache_hit=False,
        )
        self.insights.store(
            subject_type=subject["type"],
            subject_id=subject["id"],
            insight_type=insight_type,
            fingerprint=fingerprint,
            model_id=self.settings.bedrock_model_id,
            prompt_version=self.settings.prompt_version,
            output=payload,
            input_tokens=int(response.input_tokens or 0),
            output_tokens=int(
                response.output_tokens or 0
            ),
            latency_ms=int(response.latency_ms or 0),
            cache_ttl_seconds=(
                self.settings.cache_ttl_seconds
            ),
            retention_seconds=(
                self.settings.insight_retention_seconds
            ),
        )
        return ServiceResult(
            payload=payload,
            input_tokens=int(response.input_tokens or 0),
            output_tokens=int(
                response.output_tokens or 0
            ),
            bedrock_latency_ms=int(
                response.latency_ms or 0
            ),
        )

    def network_summary(self) -> ServiceResult:
        return self._fixed(
            "NETWORK_SUMMARY",
            self.builders.build_network_context,
        )

    def aircraft_explanation(
        self,
        aircraft_id: str,
    ) -> ServiceResult:
        return self._fixed(
            "AIRCRAFT_RISK_EXPLANATION",
            lambda: self.builders.build_aircraft_context(
                aircraft_id
            ),
        )

    def airport_summary(
        self,
        airport_id: str,
    ) -> ServiceResult:
        return self._fixed(
            "AIRPORT_SUMMARY",
            lambda: self.builders.build_airport_context(
                airport_id
            ),
        )

    def recommendation_explanation(
        self,
        recommendation_id: str,
    ) -> ServiceResult:
        return self._fixed(
            "RECOMMENDATION_EXPLANATION",
            lambda: (
                self.builders
                .build_recommendation_context(
                    recommendation_id
                )
            ),
        )

    def incident_summary(
        self,
        alert_id: str,
    ) -> ServiceResult:
        return self._fixed(
            "INCIDENT_SUMMARY",
            lambda: self.builders.build_alert_context(
                alert_id
            ),
        )

    def chat(
        self,
        request: dict[str, Any],
    ) -> ServiceResult:
        messages = [
            {
                "role": item["role"],
                "content": [
                    {"text": item["content"]}
                ],
            }
            for item in request["history"]
        ]
        messages.append(
            {
                "role": "user",
                "content": [
                    {
                        "text": chat_message(
                            request["message"],
                            request.get("subject"),
                        )
                    }
                ],
            }
        )
        subject = request.get("subject") or {}
        grounding = {
            "AIRCRAFT": (
                "get_aircraft_context",
                "aircraft_id",
            ),
            "AIRPORT": (
                "get_airport_context",
                "airport_id",
            ),
            "RECOMMENDATION": (
                "get_recommendation_context",
                "recommendation_id",
            ),
            "ALERT": (
                "get_alert_context",
                "alert_id",
            ),
        }.get(subject.get("type"))
        required_tool = (
            grounding[0] if grounding else None
        )
        required_input = (
            {grounding[1]: subject["id"]}
            if grounding
            else None
        )
        result = self.agent.run(
            messages,
            require_tools=True,
            required_tool_name=required_tool,
            required_tool_input=required_input,
        )
        try:
            validated = validate_model_output(
                result.output,
                evidence_catalog=(
                    result.evidence_catalog
                ),
                required_limitations=(
                    result.limitations
                ),
                required_freshness_warnings=(
                    result.freshness_warnings
                ),
            )
        except ValidationError as exc:
            raise ModelError(
                "Model returned an invalid structured response"
            ) from exc
        return ServiceResult(
            payload=self._public_payload(
                validated,
                tool_calls=result.tool_calls,
                cache_hit=False,
            ),
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            bedrock_latency_ms=(
                result.bedrock_latency_ms
            ),
        )

    def _public_payload(
        self,
        validated: dict[str, Any],
        *,
        tool_calls: list[dict[str, Any]],
        cache_hit: bool,
    ) -> dict[str, Any]:
        return {
            "answer": validated["answer"],
            "evidence": validated["evidence"],
            "confidence": validated["confidence"],
            "limitations": validated["limitations"],
            "dataFreshnessWarnings": validated[
                "dataFreshnessWarnings"
            ],
            "toolCalls": tool_calls,
            "advisoryOnly": True,
            "humanReviewRequired": True,
            "generatedAt": now_iso(),
            "modelId": self.settings.bedrock_model_id,
            "promptVersion": (
                self.settings.prompt_version
            ),
            "cache": {"hit": cache_hit},
        }
