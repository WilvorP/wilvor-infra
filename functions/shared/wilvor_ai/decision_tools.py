"""Closed doorway from a future model to the four Decision Tools.

The catalog is static. Runtime tables, clocks, and ids are constructor or
invoke inputs. This module does not search, score, choose, or acquire a clock.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import wilvor_ai.decision_context as decision_context
import wilvor_ai.persisted_airport_evidence as persisted_airport_evidence
from wilvor_ai.contracts import (
    AgentAuthorityMode,
    AgentCapability,
    ContractValidationError,
    TemporalScope,
    ToolInputField,
    ToolInputValueType,
    ToolResult,
    _validate_text,
    _validate_utc,
)
from wilvor_ai.decision_context import DecisionContextCall
from wilvor_ai.decision_contracts import (
    OPERATIONAL_ID_MAX_LENGTH,
    DecisionEvaluationState,
    DecisionEvidenceKind,
    validate_decision_tool_result,
)
from wilvor_ai.decision_tool_projection import (
    DecisionToolProjection,
    project_decision_tool_result,
)
from wilvor_ai.persisted_airport_contracts import validate_persisted_airport_tool_result
from wilvor_ai.persisted_airport_evidence import PersistedAirportEvidenceCall
from wilvor_ai.tool_schema import (
    FORBIDDEN_TOOL_SCHEMA_FIELD_NAMES,
    ToolSchema,
    build_tool_schemas,
)
from wilvor_operational.context import normalize_aircraft_id


_READ_ONLY = AgentAuthorityMode.READ_ONLY_ADVISORY
_RETRIEVE = (AgentCapability.RETRIEVE_DETERMINISTIC_OPERATIONAL_CONTEXT,)


@dataclass(frozen=True)
class DecisionToolSpec:
    """One trusted Decision Tool. ``input_fields`` is the model allowlist."""

    name: str
    description: str
    authority_mode: AgentAuthorityMode
    capabilities: tuple[AgentCapability, ...]
    input_fields: tuple[ToolInputField, ...]

    def __post_init__(self) -> None:
        errors = _validate_text(self.name, "name")
        errors.extend(_validate_text(self.description, "description"))
        if self.authority_mode is not _READ_ONLY:
            errors.append("invalid_authority_mode")
        if self.capabilities != _RETRIEVE:
            errors.append("invalid_capabilities")
        if not isinstance(self.input_fields, tuple) or any(
            not isinstance(item, ToolInputField) for item in self.input_fields
        ):
            errors.append("invalid_input_fields")
        if errors:
            raise ContractValidationError(errors)


def _field(name: str, description: str) -> ToolInputField:
    return ToolInputField(
        name=name,
        required=True,
        value_type=ToolInputValueType.STRING,
        description=description,
    )


DECISION_TOOLS = (
    DecisionToolSpec(
        name="get_current_decision_context",
        description=(
            "Return the current deterministic operational decision chain for "
            "one aircraft. This does not calculate risk, choose a "
            "recommendation, select an encounter, or validate a route."
        ),
        authority_mode=_READ_ONLY,
        capabilities=_RETRIEVE,
        input_fields=(
            _field(
                "aircraft_id",
                "Stored aircraft identity for the current decision chain",
            ),
        ),
    ),
    DecisionToolSpec(
        name="get_current_risk_evidence",
        description=(
            "Return the current persisted deterministic risk evidence for one "
            "aircraft. Absent risk is not LOW. This does not recalculate risk."
        ),
        authority_mode=_READ_ONLY,
        capabilities=_RETRIEVE,
        input_fields=(
            _field(
                "aircraft_id",
                "Stored aircraft identity for current risk evidence",
            ),
        ),
    ),
    DecisionToolSpec(
        name="get_current_recommendation",
        description=(
            "Return the current persisted recommendation evidence for one "
            "aircraft. Zero, one, or many recommendations may exist, and none "
            "is selected. EVALUATE_DIVERSION is stored advisory evidence, not "
            "a clearance or a route."
        ),
        authority_mode=_READ_ONLY,
        capabilities=_RETRIEVE,
        input_fields=(
            _field(
                "aircraft_id",
                "Stored aircraft identity for current recommendation evidence",
            ),
        ),
    ),
    DecisionToolSpec(
        name="get_persisted_airport_candidate_evidence",
        description=(
            "Return persisted airport-assessment evidence linked from one "
            "stored recommendation. This is not current airport suitability, "
            "not a selected diversion, and not route validation. COMPLETE "
            "means persisted assessment scoring completed. Route, runway, and "
            "congestion evidence stay unavailable."
        ),
        authority_mode=_READ_ONLY,
        capabilities=_RETRIEVE,
        input_fields=(
            _field(
                "recommendation_id",
                "Stored recommendation identity for persisted airport evidence",
            ),
        ),
    ),
)

_CATALOG_BY_NAME = {item.name: item for item in DECISION_TOOLS}
_CURRENT_KINDS = {
    "get_current_decision_context": DecisionEvidenceKind.DECISION_CONTEXT,
    "get_current_risk_evidence": DecisionEvidenceKind.RISK_EVIDENCE,
    "get_current_recommendation": DecisionEvidenceKind.RECOMMENDATION_EVIDENCE,
}
_CURRENT_TOOLS = frozenset(_CURRENT_KINDS)


def decision_tool_schemas() -> tuple[ToolSchema, ...]:
    """Build provider-neutral schemas from the Decision catalog only."""

    return build_tool_schemas(DECISION_TOOLS)


@dataclass(frozen=True)
class DecisionToolsRuntime:
    """Trusted clocks and table handles. Not model arguments."""

    tables: Any
    now_epoch: int
    query_timestamp_utc: str
    correlation_id: str | None = None

    def __post_init__(self) -> None:
        if self.tables is None:
            raise ContractValidationError("invalid_tables")
        if type(self.now_epoch) is not int:
            raise ContractValidationError("invalid_now_epoch")
        timestamp_errors = _validate_utc(
            self.query_timestamp_utc,
            "query_timestamp_utc",
        )
        if timestamp_errors:
            raise ContractValidationError(timestamp_errors)
        if self.correlation_id is not None and (
            not isinstance(self.correlation_id, str)
            or not self.correlation_id
            or self.correlation_id != self.correlation_id.strip()
        ):
            raise ContractValidationError("invalid_correlation_id")


@dataclass(frozen=True)
class DecisionToolInvocation:
    """Raw deterministic result plus a separate model-facing projection."""

    raw_tool_result: ToolResult
    model_projection: DecisionToolProjection

    def __post_init__(self) -> None:
        if not isinstance(self.raw_tool_result, ToolResult):
            raise ContractValidationError("invalid_tool_result")
        if not isinstance(self.model_projection, DecisionToolProjection):
            raise ContractValidationError("invalid_model_projection")


class DecisionToolsAdapter:
    """Validate, dispatch, validate again, then project."""

    def __init__(self, runtime: DecisionToolsRuntime) -> None:
        if not isinstance(runtime, DecisionToolsRuntime):
            raise TypeError("runtime must be a DecisionToolsRuntime")
        self._runtime = runtime

    def invoke(
        self,
        tool_name: str,
        arguments: Any,
        *,
        tool_call_id: str,
    ) -> DecisionToolInvocation:
        spec = _CATALOG_BY_NAME.get(tool_name)
        if spec is None:
            raise ContractValidationError("unknown_decision_tool")
        _validate_arguments(spec, arguments)
        _validate_tool_call_id(tool_call_id)
        result = _execute(self._runtime, spec.name, arguments, tool_call_id)
        _validate_returned_result(
            result,
            tool_name=spec.name,
            tool_call_id=tool_call_id,
            correlation_id=self._runtime.correlation_id,
            requested_identity=arguments[spec.input_fields[0].name],
        )
        return DecisionToolInvocation(
            raw_tool_result=result,
            model_projection=project_decision_tool_result(result),
        )


def _validate_tool_call_id(tool_call_id: str) -> None:
    if (
        not isinstance(tool_call_id, str)
        or not tool_call_id
        or tool_call_id != tool_call_id.strip()
    ):
        raise ContractValidationError("invalid_tool_call_id")


def _validate_arguments(spec: DecisionToolSpec, arguments: Any) -> None:
    if not isinstance(arguments, Mapping) or isinstance(arguments, (str, bytes)):
        raise ContractValidationError("invalid_arguments")
    if any(not isinstance(key, str) for key in arguments):
        raise ContractValidationError("invalid_argument_name")
    allowed = {field.name: field for field in spec.input_fields}
    for name in arguments:
        if name.casefold() in FORBIDDEN_TOOL_SCHEMA_FIELD_NAMES:
            raise ContractValidationError("trusted_argument_forbidden")
    for name in arguments:
        if name not in allowed:
            raise ContractValidationError("unknown_argument")
    for field in spec.input_fields:
        if field.required and field.name not in arguments:
            raise ContractValidationError("missing_required_argument")
    for name, value in arguments.items():
        if not isinstance(value, str):
            raise ContractValidationError("invalid_argument_type")
        if name == "aircraft_id" and not _aircraft_id_ok(value):
            raise ContractValidationError("invalid_aircraft_id")
        if name == "recommendation_id" and not _recommendation_id_ok(value):
            raise ContractValidationError("invalid_recommendation_id")


def _aircraft_id_ok(value: str) -> bool:
    if not value or value != value.strip():
        return False
    if any(character.isspace() for character in value):
        return False
    return len(value) <= OPERATIONAL_ID_MAX_LENGTH


def _recommendation_id_ok(value: str) -> bool:
    return bool(value) and value == value.strip()


def _execute(
    runtime: DecisionToolsRuntime,
    tool_name: str,
    arguments: Mapping[str, Any],
    tool_call_id: str,
) -> Any:
    if tool_name in _CURRENT_TOOLS:
        call = DecisionContextCall(
            tables=runtime.tables,
            now_epoch=runtime.now_epoch,
            tool_call_id=tool_call_id,
            correlation_id=runtime.correlation_id,
        )
        aircraft_id = arguments["aircraft_id"]
        if tool_name == "get_current_decision_context":
            return decision_context.get_current_decision_context(call, aircraft_id)
        if tool_name == "get_current_risk_evidence":
            return decision_context.get_current_risk_evidence(call, aircraft_id)
        return decision_context.get_current_recommendation(call, aircraft_id)

    call = PersistedAirportEvidenceCall(
        tables=runtime.tables,
        tool_call_id=tool_call_id,
        query_timestamp_utc=runtime.query_timestamp_utc,
        correlation_id=runtime.correlation_id,
    )
    return persisted_airport_evidence.get_persisted_airport_candidate_evidence(
        call,
        arguments["recommendation_id"],
    )


def _validate_returned_result(
    result: Any,
    *,
    tool_name: str,
    tool_call_id: str,
    correlation_id: str | None,
    requested_identity: str,
) -> None:
    if not isinstance(result, ToolResult):
        raise ContractValidationError("invalid_tool_result")
    if result.tool_name != tool_name:
        raise ContractValidationError("decision_tool_name_mismatch")
    if result.tool_call_id != tool_call_id:
        raise ContractValidationError("decision_tool_call_id_mismatch")
    if result.correlation_id != correlation_id:
        raise ContractValidationError("decision_correlation_id_mismatch")
    expected_scope = (
        TemporalScope.CURRENT
        if tool_name in _CURRENT_TOOLS
        else TemporalScope.PERSISTED
    )
    if result.temporal_scope is not expected_scope:
        raise ContractValidationError("decision_temporal_scope_mismatch")
    if tool_name in _CURRENT_TOOLS:
        evidence = validate_decision_tool_result(result)
        if evidence.kind is not _CURRENT_KINDS[tool_name]:
            raise ContractValidationError("decision_evidence_kind_mismatch")
        _bind_aircraft(requested_identity, evidence.aircraft_id, evidence.evaluation_state)
        return
    persisted = validate_persisted_airport_tool_result(result)
    if persisted.recommendation_id != requested_identity:
        raise ContractValidationError("persisted_recommendation_identity_mismatch")


def _bind_aircraft(
    requested: str,
    returned: str | None,
    evaluation_state: DecisionEvaluationState,
) -> None:
    if returned is None:
        if evaluation_state is not DecisionEvaluationState.SOURCE_UNAVAILABLE:
            raise ContractValidationError("decision_aircraft_identity_mismatch")
        return
    if normalize_aircraft_id(requested) != normalize_aircraft_id(returned):
        raise ContractValidationError("decision_aircraft_identity_mismatch")
