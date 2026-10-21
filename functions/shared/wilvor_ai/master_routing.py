"""Deterministic Master routing and authorization.

This module validates an untrusted route proposal against trusted application
context and returns a bounded plan, a clarification, an unsupported result, or
a safety refusal. It does not call specialists, models, AWS, or LangGraph.

Exact equality between proposal text and the trusted user request does not
establish that a model understood every part of the natural-language request.
``request_coverage`` stays ``NOT_ESTABLISHED``. Deferred synthesis stays
non-executable and not yet fulfilled. ``execution_authority`` stays ``DENIED``.

An application-supplied aircraft or recommendation id is a comparison
candidate only. It is not authenticated identity, currentness, or an executable Decision target.
Model-supplied selection hints stay untrusted. A dependency is a planning
edge only; this module does not execute it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Mapping

from wilvor_ai.contracts import (
    PROVENANCE_TOKEN_MAX_LENGTH,
    ALLOWED_V1_CAPABILITIES,
    PROHIBITED_V1_CAPABILITIES,
    AgentCapability,
    ContractValidationError,
    JsonValue,
    TemporalScope,
    V1_AUTHORITY,
    _parse_enum,
    _required,
    _sequence,
    _validate_bounded_text,
    _validate_utc,
)
from wilvor_ai.master_contracts import (
    MasterInvocationAuthorization,
    MasterPurpose,
    MasterRequirement,
    MasterSpecialist,
)
from wilvor_ai.specialist_contracts import SPECIALIST_REQUEST_TEXT_MAX_LENGTH


MASTER_ROUTE_PROPOSAL_SCHEMA_VERSION = "wilvor.ai.master_route_proposal.v1"
MASTER_ROUTE_PLAN_SCHEMA_VERSION = "wilvor.ai.master_route_plan.v1"
MASTER_ROUTE_DECISION_SCHEMA_VERSION = "wilvor.ai.master_route_decision.v1"
MAX_ROUTE_STEPS = 4
MAX_DECISION_STEPS = 1
# A population hint records ambiguity for one step. It is not an execution
# list and cannot add Decision invocations. Eight is above the single-Decision
# limit and still rejects an unbounded fleet dump.
MAX_UNTRUSTED_POPULATION_HINTS = 8
MAX_HISTORICAL_WINDOW = timedelta(days=7)
REQUEST_UNDERSTANDING_LIMITATION = (
    "Exact agreement between the proposal text and the trusted user request "
    "does not establish that the proposal understood or covered every part of "
    "the natural-language request."
)

_SYNTHESIS_CAPABILITIES = frozenset(
    {
        AgentCapability.SUMMARIZE_OPERATIONAL_STATE,
        AgentCapability.COMPARE_VERIFIED_EVIDENCE,
        AgentCapability.GENERATE_BUSINESS_READABLE_SUMMARIES,
    }
)
_VISUALIZATION = AgentCapability.GENERATE_VISUALIZATION_SPECIFICATIONS
_EXPLAIN = frozenset(
    {
        AgentCapability.EXPLAIN_EXISTING_RISK,
        AgentCapability.EXPLAIN_EXISTING_RECOMMENDATIONS,
    }
)
_LIVE_OPS_CAPABILITY = AgentCapability.RETRIEVE_DETERMINISTIC_OPERATIONAL_CONTEXT
_HISTORICAL_CAPABILITY = AgentCapability.RETRIEVE_HISTORICAL_ANALYTICS
_SPECIALIST_CAPABILITIES = frozenset(
    {_LIVE_OPS_CAPABILITY, _HISTORICAL_CAPABILITY, *_EXPLAIN}
)
_ALLOWED_PAIRS = frozenset(
    {
        (MasterSpecialist.LIVE_OPS, MasterPurpose.DISCOVERY),
        (MasterSpecialist.LIVE_OPS, MasterPurpose.ANSWER),
        (MasterSpecialist.HISTORICAL_ANALYTICS, MasterPurpose.ANSWER),
        (MasterSpecialist.DECISION, MasterPurpose.ANSWER),
    }
)
_PAIR_CAPABILITIES = {
    (MasterSpecialist.LIVE_OPS, MasterPurpose.DISCOVERY): frozenset({_LIVE_OPS_CAPABILITY}),
    (MasterSpecialist.LIVE_OPS, MasterPurpose.ANSWER): frozenset({_LIVE_OPS_CAPABILITY}),
    (MasterSpecialist.HISTORICAL_ANALYTICS, MasterPurpose.ANSWER): frozenset(
        {_HISTORICAL_CAPABILITY}
    ),
    (MasterSpecialist.DECISION, MasterPurpose.ANSWER): _EXPLAIN,
}


class MasterRouteTemporalIntent(str, Enum):
    CURRENT = "CURRENT"
    HISTORICAL = "HISTORICAL"
    UNSPECIFIED = "UNSPECIFIED"


class MasterRouteOutcome(str, Enum):
    AUTHORIZED_PLAN = "AUTHORIZED_PLAN"
    CLARIFICATION_REQUIRED = "CLARIFICATION_REQUIRED"
    UNSUPPORTED = "UNSUPPORTED"
    REFUSED = "REFUSED"


class MasterRouteGate(str, Enum):
    NOT_REQUIRED = "NOT_REQUIRED"
    IDENTITY_DISCOVERY_NEEDED = "IDENTITY_DISCOVERY_NEEDED"
    TRUSTED_IDENTITY_VALIDATION_PENDING = "TRUSTED_IDENTITY_VALIDATION_PENDING"
    CURRENTNESS_VALIDATION_PENDING = "CURRENTNESS_VALIDATION_PENDING"
    APPLICATION_TARGET_CANDIDATE_PRESENT = "APPLICATION_TARGET_CANDIDATE_PRESENT"
    TARGET_AMBIGUOUS = "TARGET_AMBIGUOUS"
    TARGET_UNSUPPORTED = "TARGET_UNSUPPORTED"
    DEPENDENCY_BLOCKED = "DEPENDENCY_BLOCKED"
    REGION_VALIDATION_PENDING = "REGION_VALIDATION_PENDING"
    TEMPORAL_SCOPE_UNRESOLVED = "TEMPORAL_SCOPE_UNRESOLVED"


class MasterRouteExecutionAuthority(str, Enum):
    """Planning permission only. Production execution is not representable."""

    DENIED = "DENIED"


class MasterRequestCoverage(str, Enum):
    """The validator cannot certify natural-language coverage."""

    NOT_ESTABLISHED = "NOT_ESTABLISHED"


class MasterSynthesisFulfillment(str, Enum):
    NOT_YET_FULFILLED = "NOT_YET_FULFILLED"


class MasterSynthesisState(str, Enum):
    NON_EXECUTABLE = "NON_EXECUTABLE"


class MasterHintTrust(str, Enum):
    """Model-supplied selectors never become trusted inputs."""

    UNTRUSTED = "UNTRUSTED"


def _token(value: Any, field_name: str, *, optional: bool = False) -> str | None:
    if value is None and optional:
        return None
    errors = _validate_bounded_text(
        value,
        field_name,
        optional=optional,
        max_length=PROVENANCE_TOKEN_MAX_LENGTH,
    )
    if errors:
        raise ContractValidationError(errors)
    if value != value.strip() or any(character.isspace() for character in value):
        raise ContractValidationError(f"invalid_{field_name}")
    return value


def _prose(value: Any, field_name: str, *, max_length: int) -> str:
    errors = _validate_bounded_text(value, field_name, max_length=max_length)
    if errors:
        raise ContractValidationError(errors)
    if value != value.strip():
        raise ContractValidationError(f"invalid_{field_name}")
    return value


def _strict_bool(value: Any, field_name: str) -> bool:
    if not isinstance(value, bool):
        raise ContractValidationError(f"invalid_{field_name}")
    return value


def _deny_execution(authority: MasterRouteExecutionAuthority) -> MasterRouteExecutionAuthority:
    if authority is not MasterRouteExecutionAuthority.DENIED:
        raise ContractValidationError("execution_authority_not_granted")
    return authority


def _parse_whole_second_utc(value: str, field_name: str) -> datetime:
    if not isinstance(value, str) or len(value) != 20 or not value.endswith("Z"):
        raise ContractValidationError(f"invalid_{field_name}")
    if value[10] != "T":
        raise ContractValidationError(f"invalid_{field_name}")
    parsed_errors = _validate_utc(value, field_name)
    if parsed_errors:
        raise ContractValidationError(parsed_errors)
    try:
        parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError as exc:
        raise ContractValidationError(f"invalid_{field_name}") from exc
    return parsed


def _enum_tuple(values: tuple[Any, ...], enum_type: type[Enum], field_name: str) -> tuple[Any, ...]:
    if not isinstance(values, tuple) or any(not isinstance(item, enum_type) for item in values):
        raise ContractValidationError(f"invalid_{field_name}")
    return tuple(sorted(values, key=lambda item: item.value))


def _clock(value: str, field_name: str) -> str:
    errors = _validate_utc(value, field_name)
    if errors:
        raise ContractValidationError(errors)
    return value


@dataclass(frozen=True)
class MasterProposedCapability:
    """One capability the proposal claims. This is not authorization."""

    capability: AgentCapability
    requirement: MasterRequirement

    def __post_init__(self) -> None:
        if not isinstance(self.capability, AgentCapability):
            raise ContractValidationError("invalid_capability")
        if not isinstance(self.requirement, MasterRequirement):
            raise ContractValidationError("invalid_requirement")

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "capability": self.capability.value,
            "requirement": self.requirement.value,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "MasterProposedCapability":
        if not isinstance(data, Mapping):
            raise ContractValidationError("invalid_proposed_capability")
        if set(data) != {"capability", "requirement"}:
            raise ContractValidationError("unexpected_proposed_capability_field")
        return cls(
            capability=_parse_enum(AgentCapability, _required(data, "capability"), "capability"),
            requirement=_parse_enum(
                MasterRequirement,
                _required(data, "requirement"),
                "requirement",
            ),
        )


@dataclass(frozen=True)
class MasterRouteHints:
    """Untrusted hints. None of these fields is a trusted target."""

    callsign: str | None = None
    typed_aircraft_id: str | None = None
    recommendation_id: str | None = None
    region_text: str | None = None
    population_aircraft_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _token(self.callsign, "callsign", optional=True)
        _token(self.typed_aircraft_id, "typed_aircraft_id", optional=True)
        _token(self.recommendation_id, "recommendation_id", optional=True)
        if self.region_text is not None:
            _prose(self.region_text, "region_text", max_length=PROVENANCE_TOKEN_MAX_LENGTH)
        if not isinstance(self.population_aircraft_ids, tuple):
            raise ContractValidationError("invalid_population_aircraft_ids")
        if len(self.population_aircraft_ids) > MAX_UNTRUSTED_POPULATION_HINTS:
            raise ContractValidationError("population_aircraft_ids_exceed_bound")
        seen: set[str] = set()
        for item in self.population_aircraft_ids:
            parsed = _token(item, "population_aircraft_id")
            if parsed in seen:
                raise ContractValidationError("duplicate_population_aircraft_id")
            seen.add(parsed or "")
        if self.typed_aircraft_id is not None and self.recommendation_id is not None:
            raise ContractValidationError("contradictory_targets")

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "callsign": self.callsign,
            "typed_aircraft_id": self.typed_aircraft_id,
            "recommendation_id": self.recommendation_id,
            "region_text": self.region_text,
            "population_aircraft_ids": list(self.population_aircraft_ids),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "MasterRouteHints":
        if not isinstance(data, Mapping):
            raise ContractValidationError("invalid_route_hints")
        allowed = {
            "callsign",
            "typed_aircraft_id",
            "recommendation_id",
            "region_text",
            "population_aircraft_ids",
        }
        if set(data) - allowed:
            raise ContractValidationError("unexpected_route_hint_field")
        if allowed - set(data):
            raise ContractValidationError("invalid_route_hints")
        population = tuple(
            _sequence(data.get("population_aircraft_ids", ()), "population_aircraft_ids")
        )
        return cls(
            callsign=data.get("callsign"),
            typed_aircraft_id=data.get("typed_aircraft_id"),
            recommendation_id=data.get("recommendation_id"),
            region_text=data.get("region_text"),
            population_aircraft_ids=population,
        )


@dataclass(frozen=True)
class MasterProposedStep:
    """One untrusted specialist step. Dependencies are ids, not executions."""

    step_id: str
    specialist: MasterSpecialist
    purpose: MasterPurpose
    requirement: MasterRequirement
    capability: AgentCapability
    depends_on: tuple[str, ...] = ()
    temporal_intent: MasterRouteTemporalIntent = MasterRouteTemporalIntent.UNSPECIFIED
    historical_start_utc: str | None = None
    historical_end_utc: str | None = None
    hints: MasterRouteHints = MasterRouteHints()
    needs_persisted_evidence: bool = False

    def __post_init__(self) -> None:
        _token(self.step_id, "step_id")
        if not isinstance(self.specialist, MasterSpecialist):
            raise ContractValidationError("invalid_specialist")
        if not isinstance(self.purpose, MasterPurpose):
            raise ContractValidationError("invalid_purpose")
        if not isinstance(self.requirement, MasterRequirement):
            raise ContractValidationError("invalid_requirement")
        if not isinstance(self.capability, AgentCapability):
            raise ContractValidationError("invalid_capability")
        if not isinstance(self.temporal_intent, MasterRouteTemporalIntent):
            raise ContractValidationError("invalid_temporal_intent")
        if not isinstance(self.depends_on, tuple) or any(
            not isinstance(item, str) for item in self.depends_on
        ):
            raise ContractValidationError("invalid_depends_on")
        if len(self.depends_on) != len(set(self.depends_on)):
            raise ContractValidationError("duplicate_dependency_id")
        if self.step_id in self.depends_on:
            raise ContractValidationError("self_dependency")
        for item in self.depends_on:
            _token(item, "depends_on")
        for field_name in ("historical_start_utc", "historical_end_utc"):
            value = getattr(self, field_name)
            if value is not None:
                _parse_whole_second_utc(value, field_name)
        if not isinstance(self.hints, MasterRouteHints):
            raise ContractValidationError("invalid_route_hints")
        if not isinstance(self.needs_persisted_evidence, bool):
            raise ContractValidationError("invalid_needs_persisted_evidence")
        if self.needs_persisted_evidence and self.specialist is not MasterSpecialist.DECISION:
            raise ContractValidationError("persisted_scope_requires_decision")

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "step_id": self.step_id,
            "specialist": self.specialist.value,
            "purpose": self.purpose.value,
            "requirement": self.requirement.value,
            "capability": self.capability.value,
            "depends_on": list(self.depends_on),
            "temporal_intent": self.temporal_intent.value,
            "historical_start_utc": self.historical_start_utc,
            "historical_end_utc": self.historical_end_utc,
            "hints": self.hints.to_dict(),
            "needs_persisted_evidence": self.needs_persisted_evidence,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "MasterProposedStep":
        if not isinstance(data, Mapping):
            raise ContractValidationError("invalid_proposed_step")
        allowed = {
            "step_id",
            "specialist",
            "purpose",
            "requirement",
            "capability",
            "depends_on",
            "temporal_intent",
            "historical_start_utc",
            "historical_end_utc",
            "hints",
            "needs_persisted_evidence",
        }
        if set(data) - allowed:
            raise ContractValidationError("unexpected_proposed_step_field")
        hints = data.get("hints", {})
        return cls(
            step_id=_required(data, "step_id"),
            specialist=_parse_enum(MasterSpecialist, _required(data, "specialist"), "specialist"),
            purpose=_parse_enum(MasterPurpose, _required(data, "purpose"), "purpose"),
            requirement=_parse_enum(
                MasterRequirement,
                _required(data, "requirement"),
                "requirement",
            ),
            capability=_parse_enum(AgentCapability, _required(data, "capability"), "capability"),
            depends_on=tuple(_sequence(data.get("depends_on", ()), "depends_on")),
            temporal_intent=_parse_enum(
                MasterRouteTemporalIntent,
                data.get("temporal_intent", MasterRouteTemporalIntent.UNSPECIFIED.value),
                "temporal_intent",
            ),
            historical_start_utc=data.get("historical_start_utc"),
            historical_end_utc=data.get("historical_end_utc"),
            hints=MasterRouteHints.from_dict(hints) if hints else MasterRouteHints(),
            needs_persisted_evidence=(
                False
                if "needs_persisted_evidence" not in data
                else _strict_bool(data["needs_persisted_evidence"], "needs_persisted_evidence")
            ),
        )


@dataclass(frozen=True)
class MasterRouteProposal:
    """Untrusted model proposal. Construction does not authorize anything."""

    user_text: str
    capabilities: tuple[MasterProposedCapability, ...]
    steps: tuple[MasterProposedStep, ...] = ()
    schema_version: str = MASTER_ROUTE_PROPOSAL_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != MASTER_ROUTE_PROPOSAL_SCHEMA_VERSION:
            raise ContractValidationError("invalid_schema_version")
        _prose(self.user_text, "user_text", max_length=SPECIALIST_REQUEST_TEXT_MAX_LENGTH)
        if not isinstance(self.capabilities, tuple) or any(
            not isinstance(item, MasterProposedCapability) for item in self.capabilities
        ):
            raise ContractValidationError("invalid_capabilities")
        if not self.capabilities:
            raise ContractValidationError("empty_proposal")
        if len({item.capability for item in self.capabilities}) != len(self.capabilities):
            raise ContractValidationError("duplicate_capability")
        if not isinstance(self.steps, tuple) or any(
            not isinstance(item, MasterProposedStep) for item in self.steps
        ):
            raise ContractValidationError("invalid_steps")
        if len(self.steps) > MAX_ROUTE_STEPS:
            raise ContractValidationError("excessive_plan_size")
        if sum(item.specialist is MasterSpecialist.DECISION for item in self.steps) > MAX_DECISION_STEPS:
            raise ContractValidationError("decision_fanout_not_permitted")
        ids = [item.step_id for item in self.steps]
        if len(ids) != len(set(ids)):
            raise ContractValidationError("duplicate_step_id")
        known = set(ids)
        by_id = {item.step_id: item for item in self.steps}
        for step in self.steps:
            for dependency in step.depends_on:
                if dependency not in known:
                    raise ContractValidationError("missing_dependency_id")
        _reject_cycles(by_id)

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "schema_version": self.schema_version,
            "user_text": self.user_text,
            "capabilities": [item.to_dict() for item in self.capabilities],
            "steps": [item.to_dict() for item in self.steps],
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "MasterRouteProposal":
        if not isinstance(data, Mapping):
            raise ContractValidationError("invalid_master_route_proposal")
        allowed = {"schema_version", "user_text", "capabilities", "steps"}
        if set(data) - allowed:
            raise ContractValidationError("unexpected_proposal_field")
        return cls(
            user_text=_required(data, "user_text"),
            capabilities=tuple(
                MasterProposedCapability.from_dict(item)
                for item in _sequence(_required(data, "capabilities"), "capabilities")
            ),
            steps=tuple(
                MasterProposedStep.from_dict(item)
                for item in _sequence(data.get("steps", ()), "steps")
            ),
            schema_version=_required(data, "schema_version"),
        )


def _reject_cycles(by_id: Mapping[str, Any]) -> None:
    visiting: set[str] = set()
    visited: set[str] = set()

    def walk(step_id: str) -> None:
        if step_id in visiting:
            raise ContractValidationError("cyclic_dependency")
        if step_id in visited:
            return
        step = by_id.get(step_id)
        if step is None:
            raise ContractValidationError("missing_dependency_id")
        visiting.add(step_id)
        for dependency in step.depends_on:
            walk(dependency)
        visiting.remove(step_id)
        visited.add(step_id)

    for step_id in by_id:
        walk(step_id)


def _validate_plan_dependencies(steps: tuple["MasterRouteStepPlan", ...]) -> None:
    _reject_cycles({item.step_id: item for item in steps})


def _validate_request_identities(steps: tuple["MasterRouteStepPlan", ...]) -> None:
    seen: set[str] = set()
    for step in steps:
        invocation = step.invocation
        specialist_request_id = invocation.specialist_request_id
        if specialist_request_id is None:
            raise ContractValidationError("specialist_request_id_required")
        if invocation.correlation_id is None:
            raise ContractValidationError("correlation_id_required")
        if specialist_request_id in seen:
            raise ContractValidationError("duplicate_specialist_request_id")
        seen.add(specialist_request_id)
        identities = (
            invocation.master_request_id,
            invocation.invocation_id,
            specialist_request_id,
            invocation.correlation_id,
        )
        if len(set(identities)) != 4:
            raise ContractValidationError("request_identity_not_distinct")


def _validate_planned_scopes(step: "MasterRouteStepPlan") -> None:
    scopes = step.temporal_scopes
    unresolved = MasterRouteGate.TEMPORAL_SCOPE_UNRESOLVED in step.gates
    specialist = step.invocation.specialist
    if not scopes:
        if not unresolved or specialist is not MasterSpecialist.DECISION:
            raise ContractValidationError("temporal_scope_unresolved")
        return
    if unresolved:
        raise ContractValidationError("temporal_scope_contradiction")
    if specialist is MasterSpecialist.HISTORICAL_ANALYTICS:
        if scopes != (TemporalScope.HISTORICAL,):
            raise ContractValidationError("invalid_temporal_scopes")
        return
    if specialist is MasterSpecialist.LIVE_OPS:
        if scopes != (TemporalScope.CURRENT,):
            raise ContractValidationError("invalid_temporal_scopes")
        return
    allowed = {
        (TemporalScope.CURRENT,),
        (TemporalScope.PERSISTED,),
        (TemporalScope.CURRENT, TemporalScope.PERSISTED),
    }
    if scopes not in allowed:
        raise ContractValidationError("invalid_temporal_scopes")
    if step.needs_persisted_evidence and TemporalScope.PERSISTED not in scopes:
        raise ContractValidationError("persisted_scope_missing")
    if scopes == (TemporalScope.CURRENT, TemporalScope.PERSISTED) and not step.needs_persisted_evidence:
        raise ContractValidationError("persisted_scope_missing")


def _validate_planned_window(step: "MasterRouteStepPlan") -> None:
    start = step.historical_start_utc
    end = step.historical_end_utc
    historical = step.invocation.specialist is MasterSpecialist.HISTORICAL_ANALYTICS
    if not historical:
        if start is not None or end is not None:
            raise ContractValidationError("contradictory_temporal_bounds")
        return
    if start is None or end is None:
        raise ContractValidationError("missing_historical_interval")
    parsed_start = _parse_whole_second_utc(start, "historical_start_utc")
    parsed_end = _parse_whole_second_utc(end, "historical_end_utc")
    if parsed_start >= parsed_end:
        raise ContractValidationError("contradictory_temporal_bounds")
    if parsed_end - parsed_start > MAX_HISTORICAL_WINDOW:
        raise ContractValidationError("historical_window_exceeds_seven_days")


@dataclass(frozen=True)
class MasterPlanningContext:
    """Application-owned planning input. It has no serializer and grants nothing.

    ``target_candidate_aircraft_id`` and ``target_candidate_recommendation_id``
    are structural comparison candidates. Their presence does not authenticate
    identity, establish currentness, or authorize a Decision execution.
    """

    master_request_id: str
    correlation_id: str
    application_request_time_utc: str
    canonical_user_text: str
    target_candidate_aircraft_id: str | None = None
    target_candidate_recommendation_id: str | None = None

    def __post_init__(self) -> None:
        _token(self.master_request_id, "master_request_id")
        _token(self.correlation_id, "correlation_id")
        _clock(self.application_request_time_utc, "application_request_time_utc")
        _prose(
            self.canonical_user_text,
            "canonical_user_text",
            max_length=SPECIALIST_REQUEST_TEXT_MAX_LENGTH,
        )
        _token(self.target_candidate_aircraft_id, "target_candidate_aircraft_id", optional=True)
        _token(
            self.target_candidate_recommendation_id,
            "target_candidate_recommendation_id",
            optional=True,
        )
        if (
            self.target_candidate_aircraft_id is not None
            and self.target_candidate_recommendation_id is not None
        ):
            raise ContractValidationError("contradictory_targets")


@dataclass(frozen=True)
class MasterDeferredSynthesis:
    """Future Master-level composition. It is not a specialist result."""

    capability: AgentCapability
    requirement: MasterRequirement
    state: MasterSynthesisState = MasterSynthesisState.NON_EXECUTABLE
    fulfillment: MasterSynthesisFulfillment = MasterSynthesisFulfillment.NOT_YET_FULFILLED
    executable: bool = False

    def __post_init__(self) -> None:
        if self.capability not in _SYNTHESIS_CAPABILITIES:
            raise ContractValidationError("invalid_synthesis_capability")
        if not isinstance(self.requirement, MasterRequirement):
            raise ContractValidationError("invalid_requirement")
        if self.state is not MasterSynthesisState.NON_EXECUTABLE:
            raise ContractValidationError("synthesis_not_executable")
        if self.fulfillment is not MasterSynthesisFulfillment.NOT_YET_FULFILLED:
            raise ContractValidationError("synthesis_not_fulfilled")
        if self.executable:
            raise ContractValidationError("synthesis_not_executable")

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "capability": self.capability.value,
            "requirement": self.requirement.value,
            "state": self.state.value,
            "fulfillment": self.fulfillment.value,
            "executable": False,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "MasterDeferredSynthesis":
        if not isinstance(data, Mapping):
            raise ContractValidationError("invalid_deferred_synthesis")
        if data.get("fulfillment") not in (None, MasterSynthesisFulfillment.NOT_YET_FULFILLED.value):
            raise ContractValidationError("synthesis_not_fulfilled")
        if data.get("executable") not in (None, False):
            raise ContractValidationError("synthesis_not_executable")
        if data.get("state") not in (None, MasterSynthesisState.NON_EXECUTABLE.value):
            raise ContractValidationError("synthesis_not_executable")
        return cls(
            capability=_parse_enum(AgentCapability, _required(data, "capability"), "capability"),
            requirement=_parse_enum(
                MasterRequirement,
                _required(data, "requirement"),
                "requirement",
            ),
        )


@dataclass(frozen=True)
class MasterRouteStepPlan:
    """One planned step. ``ready_to_execute`` is structural, not production authority."""

    step_id: str
    invocation: MasterInvocationAuthorization
    capability: AgentCapability
    gates: tuple[MasterRouteGate, ...]
    ready_to_execute: bool
    temporal_scopes: tuple[TemporalScope, ...]
    depends_on: tuple[str, ...]
    region_text: str | None = None
    needs_persisted_evidence: bool = False
    target_authorization_granted: bool = False
    historical_start_utc: str | None = None
    historical_end_utc: str | None = None
    untrusted_hints: MasterRouteHints = field(default_factory=MasterRouteHints)
    hint_trust: MasterHintTrust = MasterHintTrust.UNTRUSTED

    def __post_init__(self) -> None:
        _token(self.step_id, "step_id")
        if not isinstance(self.invocation, MasterInvocationAuthorization):
            raise ContractValidationError("invalid_invocation_authorization")
        if not isinstance(self.capability, AgentCapability):
            raise ContractValidationError("invalid_capability")
        object.__setattr__(self, "gates", _enum_tuple(self.gates, MasterRouteGate, "gates"))
        if not isinstance(self.ready_to_execute, bool):
            raise ContractValidationError("invalid_ready_to_execute")
        blocking = tuple(item for item in self.gates if item is not MasterRouteGate.NOT_REQUIRED)
        if self.ready_to_execute and blocking:
            raise ContractValidationError("gated_step_not_executable")
        if not self.ready_to_execute and not blocking:
            raise ContractValidationError("ungated_step_not_marked_ready")
        if self.invocation.specialist is MasterSpecialist.DECISION and self.ready_to_execute:
            raise ContractValidationError("decision_step_not_executable")
        if MasterRouteGate.REGION_VALIDATION_PENDING in self.gates and self.ready_to_execute:
            raise ContractValidationError("region_step_not_executable")
        if not isinstance(self.temporal_scopes, tuple) or any(
            not isinstance(item, TemporalScope) or item is TemporalScope.HYBRID
            for item in self.temporal_scopes
        ):
            raise ContractValidationError("invalid_temporal_scopes")
        if self.target_authorization_granted:
            raise ContractValidationError("target_authorization_not_granted")
        if self.region_text is not None:
            _prose(self.region_text, "region_text", max_length=PROVENANCE_TOKEN_MAX_LENGTH)
        if not isinstance(self.needs_persisted_evidence, bool):
            raise ContractValidationError("invalid_needs_persisted_evidence")
        if not isinstance(self.depends_on, tuple) or any(
            not isinstance(item, str) for item in self.depends_on
        ):
            raise ContractValidationError("invalid_depends_on")
        if len(self.depends_on) != len(set(self.depends_on)):
            raise ContractValidationError("duplicate_dependency_id")
        if self.step_id in self.depends_on:
            raise ContractValidationError("self_dependency")
        for item in self.depends_on:
            _token(item, "depends_on")
        if self.depends_on and MasterRouteGate.DEPENDENCY_BLOCKED not in self.gates:
            raise ContractValidationError("dependency_not_blocked")
        if self.hint_trust is not MasterHintTrust.UNTRUSTED:
            raise ContractValidationError("selection_hints_not_trusted")
        if not isinstance(self.untrusted_hints, MasterRouteHints):
            raise ContractValidationError("invalid_route_hints")
        if self.region_text != self.untrusted_hints.region_text:
            raise ContractValidationError("region_hint_mismatch")
        _validate_planned_scopes(self)
        _validate_planned_window(self)

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "step_id": self.step_id,
            "invocation": self.invocation.to_dict(),
            "capability": self.capability.value,
            "gates": [item.value for item in self.gates],
            "ready_to_execute": self.ready_to_execute,
            "temporal_scopes": [item.value for item in self.temporal_scopes],
            "depends_on": list(self.depends_on),
            "region_text": self.region_text,
            "needs_persisted_evidence": self.needs_persisted_evidence,
            "target_authorization_granted": False,
            "historical_start_utc": self.historical_start_utc,
            "historical_end_utc": self.historical_end_utc,
            "untrusted_hints": self.untrusted_hints.to_dict(),
            "hint_trust": MasterHintTrust.UNTRUSTED.value,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "MasterRouteStepPlan":
        if not isinstance(data, Mapping):
            raise ContractValidationError("invalid_route_step_plan")
        if data.get("target_authorization_granted") not in (None, False):
            raise ContractValidationError("target_authorization_not_granted")
        allowed = {
            "step_id",
            "invocation",
            "capability",
            "gates",
            "ready_to_execute",
            "temporal_scopes",
            "depends_on",
            "region_text",
            "needs_persisted_evidence",
            "target_authorization_granted",
            "historical_start_utc",
            "historical_end_utc",
            "untrusted_hints",
            "hint_trust",
        }
        if set(data) - allowed:
            raise ContractValidationError("unexpected_route_step_plan_field")
        if "historical_start_utc" not in data or "historical_end_utc" not in data:
            raise ContractValidationError("missing_historical_interval")
        if "untrusted_hints" not in data:
            raise ContractValidationError("invalid_route_hints")
        if data.get("hint_trust") != MasterHintTrust.UNTRUSTED.value:
            raise ContractValidationError("selection_hints_not_trusted")
        if "needs_persisted_evidence" not in data:
            raise ContractValidationError("invalid_needs_persisted_evidence")
        gates = tuple(
            _parse_enum(MasterRouteGate, item, "gates")
            for item in _sequence(_required(data, "gates"), "gates")
        )
        scopes = tuple(
            _parse_enum(TemporalScope, item, "temporal_scopes")
            for item in _sequence(_required(data, "temporal_scopes"), "temporal_scopes")
        )
        return cls(
            step_id=_required(data, "step_id"),
            invocation=MasterInvocationAuthorization.from_dict(_required(data, "invocation")),
            capability=_parse_enum(AgentCapability, _required(data, "capability"), "capability"),
            gates=gates,
            ready_to_execute=_required(data, "ready_to_execute"),
            temporal_scopes=scopes,
            depends_on=tuple(_sequence(_required(data, "depends_on"), "depends_on")),
            region_text=data.get("region_text"),
            needs_persisted_evidence=_strict_bool(
                data["needs_persisted_evidence"],
                "needs_persisted_evidence",
            ),
            target_authorization_granted=False,
            historical_start_utc=data.get("historical_start_utc"),
            historical_end_utc=data.get("historical_end_utc"),
            untrusted_hints=MasterRouteHints.from_dict(_required(data, "untrusted_hints")),
            hint_trust=MasterHintTrust.UNTRUSTED,
        )


@dataclass(frozen=True)
class MasterRoutePlan:
    """Bounded plan. A valid serialized plan is not execution authorization."""

    master_request_id: str
    canonical_user_text: str
    steps: tuple[MasterRouteStepPlan, ...]
    deferred_synthesis: tuple[MasterDeferredSynthesis, ...] = ()
    unsupported_omissions: tuple[str, ...] = ()
    execution_authority: MasterRouteExecutionAuthority = MasterRouteExecutionAuthority.DENIED
    request_coverage: MasterRequestCoverage = MasterRequestCoverage.NOT_ESTABLISHED
    end_to_end_fulfilled: bool = False
    understanding_limitation: str = REQUEST_UNDERSTANDING_LIMITATION
    schema_version: str = MASTER_ROUTE_PLAN_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != MASTER_ROUTE_PLAN_SCHEMA_VERSION:
            raise ContractValidationError("invalid_schema_version")
        _token(self.master_request_id, "master_request_id")
        _prose(
            self.canonical_user_text,
            "canonical_user_text",
            max_length=SPECIALIST_REQUEST_TEXT_MAX_LENGTH,
        )
        if not isinstance(self.steps, tuple) or any(
            not isinstance(item, MasterRouteStepPlan) for item in self.steps
        ):
            raise ContractValidationError("invalid_steps")
        if len(self.steps) > MAX_ROUTE_STEPS:
            raise ContractValidationError("excessive_plan_size")
        if sum(item.invocation.specialist is MasterSpecialist.DECISION for item in self.steps) > MAX_DECISION_STEPS:
            raise ContractValidationError("decision_fanout_not_permitted")
        step_ids = [item.step_id for item in self.steps]
        invocation_ids = [item.invocation.invocation_id for item in self.steps]
        if len(step_ids) != len(set(step_ids)):
            raise ContractValidationError("duplicate_step_id")
        if len(invocation_ids) != len(set(invocation_ids)):
            raise ContractValidationError("duplicate_invocation_id")
        if any(item.invocation.master_request_id != self.master_request_id for item in self.steps):
            raise ContractValidationError("master_request_mismatch")
        _validate_plan_dependencies(self.steps)
        _validate_request_identities(self.steps)
        if not isinstance(self.deferred_synthesis, tuple) or any(
            not isinstance(item, MasterDeferredSynthesis) for item in self.deferred_synthesis
        ):
            raise ContractValidationError("invalid_deferred_synthesis")
        if any(item.executable or item.fulfillment is not MasterSynthesisFulfillment.NOT_YET_FULFILLED for item in self.deferred_synthesis):
            raise ContractValidationError("synthesis_not_fulfilled")
        if not isinstance(self.unsupported_omissions, tuple) or any(
            not isinstance(item, str) or not item.strip() for item in self.unsupported_omissions
        ):
            raise ContractValidationError("invalid_unsupported_omissions")
        _deny_execution(self.execution_authority)
        if self.request_coverage is not MasterRequestCoverage.NOT_ESTABLISHED:
            raise ContractValidationError("request_coverage_not_established")
        if self.end_to_end_fulfilled:
            raise ContractValidationError("end_to_end_fulfillment_not_granted")
        if self.understanding_limitation != REQUEST_UNDERSTANDING_LIMITATION:
            raise ContractValidationError("invalid_understanding_limitation")

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "schema_version": self.schema_version,
            "master_request_id": self.master_request_id,
            "canonical_user_text": self.canonical_user_text,
            "steps": [item.to_dict() for item in self.steps],
            "deferred_synthesis": [item.to_dict() for item in self.deferred_synthesis],
            "unsupported_omissions": list(self.unsupported_omissions),
            "execution_authority": MasterRouteExecutionAuthority.DENIED.value,
            "request_coverage": MasterRequestCoverage.NOT_ESTABLISHED.value,
            "end_to_end_fulfilled": False,
            "understanding_limitation": REQUEST_UNDERSTANDING_LIMITATION,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "MasterRoutePlan":
        if not isinstance(data, Mapping):
            raise ContractValidationError("invalid_master_route_plan")
        if data.get("execution_authority") not in (None, MasterRouteExecutionAuthority.DENIED.value):
            if data.get("execution_authority") == "PRODUCTION":
                pass
            elif "execution_authority" in data:
                raise ContractValidationError("execution_authority_not_granted")
        if data.get("end_to_end_fulfilled") not in (None, False):
            raise ContractValidationError("end_to_end_fulfillment_not_granted")
        if data.get("request_coverage") not in (None, MasterRequestCoverage.NOT_ESTABLISHED.value):
            raise ContractValidationError("request_coverage_not_established")
        return cls(
            master_request_id=_required(data, "master_request_id"),
            canonical_user_text=_required(data, "canonical_user_text"),
            steps=tuple(
                MasterRouteStepPlan.from_dict(item)
                for item in _sequence(_required(data, "steps"), "steps")
            ),
            deferred_synthesis=tuple(
                MasterDeferredSynthesis.from_dict(item)
                for item in _sequence(data.get("deferred_synthesis", ()), "deferred_synthesis")
            ),
            unsupported_omissions=tuple(
                _sequence(data.get("unsupported_omissions", ()), "unsupported_omissions")
            ),
            execution_authority=MasterRouteExecutionAuthority.DENIED,
            request_coverage=MasterRequestCoverage.NOT_ESTABLISHED,
            end_to_end_fulfilled=False,
            understanding_limitation=REQUEST_UNDERSTANDING_LIMITATION,
            schema_version=_required(data, "schema_version"),
        )


@dataclass(frozen=True)
class MasterRouteDecision:
    """Policy result. ``AUTHORIZED_PLAN`` still denies production execution."""

    outcome: MasterRouteOutcome
    reasons: tuple[str, ...]
    plan: MasterRoutePlan | None = None
    execution_authority: MasterRouteExecutionAuthority = MasterRouteExecutionAuthority.DENIED
    request_coverage: MasterRequestCoverage = MasterRequestCoverage.NOT_ESTABLISHED
    understanding_limitation: str = REQUEST_UNDERSTANDING_LIMITATION
    schema_version: str = MASTER_ROUTE_DECISION_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != MASTER_ROUTE_DECISION_SCHEMA_VERSION:
            raise ContractValidationError("invalid_schema_version")
        if not isinstance(self.outcome, MasterRouteOutcome):
            raise ContractValidationError("invalid_outcome")
        if not isinstance(self.reasons, tuple) or any(
            not isinstance(item, str) or not item.strip() for item in self.reasons
        ):
            raise ContractValidationError("invalid_reasons")
        if self.outcome is MasterRouteOutcome.AUTHORIZED_PLAN:
            if not isinstance(self.plan, MasterRoutePlan):
                raise ContractValidationError("authorized_plan_required")
        elif self.plan is not None:
            raise ContractValidationError("policy_outcome_forbids_plan")
        _deny_execution(self.execution_authority)
        if self.request_coverage is not MasterRequestCoverage.NOT_ESTABLISHED:
            raise ContractValidationError("request_coverage_not_established")
        if self.understanding_limitation != REQUEST_UNDERSTANDING_LIMITATION:
            raise ContractValidationError("invalid_understanding_limitation")

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "schema_version": self.schema_version,
            "outcome": self.outcome.value,
            "reasons": list(self.reasons),
            "plan": None if self.plan is None else self.plan.to_dict(),
            "execution_authority": MasterRouteExecutionAuthority.DENIED.value,
            "request_coverage": MasterRequestCoverage.NOT_ESTABLISHED.value,
            "understanding_limitation": REQUEST_UNDERSTANDING_LIMITATION,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "MasterRouteDecision":
        if not isinstance(data, Mapping):
            raise ContractValidationError("invalid_master_route_decision")
        plan_data = data.get("plan")
        plan = None if plan_data is None else MasterRoutePlan.from_dict(plan_data)
        return cls(
            outcome=_parse_enum(MasterRouteOutcome, _required(data, "outcome"), "outcome"),
            reasons=tuple(_sequence(_required(data, "reasons"), "reasons")),
            plan=plan,
            execution_authority=MasterRouteExecutionAuthority.DENIED,
            request_coverage=MasterRequestCoverage.NOT_ESTABLISHED,
            understanding_limitation=REQUEST_UNDERSTANDING_LIMITATION,
            schema_version=_required(data, "schema_version"),
        )


def _decision(
    outcome: MasterRouteOutcome,
    reasons: tuple[str, ...],
    plan: MasterRoutePlan | None = None,
) -> MasterRouteDecision:
    return MasterRouteDecision(outcome=outcome, reasons=reasons, plan=plan)


def _capability_matches(step: MasterProposedStep) -> bool:
    allowed = _PAIR_CAPABILITIES.get((step.specialist, step.purpose))
    return allowed is not None and step.capability in allowed


def _covers(step: MasterProposedStep, capability: AgentCapability) -> bool:
    if not _capability_matches(step):
        return False
    if capability in _EXPLAIN:
        return step.specialist is MasterSpecialist.DECISION and step.purpose is MasterPurpose.ANSWER
    return step.capability is capability


def _window_problem(step: MasterProposedStep) -> tuple[str, str] | None:
    start = step.historical_start_utc
    end = step.historical_end_utc
    historical_specialist = step.specialist is MasterSpecialist.HISTORICAL_ANALYTICS
    wants_history = step.temporal_intent is MasterRouteTemporalIntent.HISTORICAL or historical_specialist
    if historical_specialist and step.temporal_intent is MasterRouteTemporalIntent.CURRENT:
        return ("unsupported", "historical_cannot_answer_current_state")
    if step.specialist is MasterSpecialist.LIVE_OPS and step.temporal_intent is MasterRouteTemporalIntent.HISTORICAL:
        return ("unsupported", "live_ops_cannot_answer_historical")
    if step.specialist is MasterSpecialist.DECISION and step.temporal_intent is MasterRouteTemporalIntent.HISTORICAL:
        return ("unsupported", "decision_cannot_answer_historical_analytics")
    if wants_history:
        if start is None or end is None:
            return ("clarify", "missing_historical_interval")
        parsed_start = _parse_whole_second_utc(start, "historical_start_utc")
        parsed_end = _parse_whole_second_utc(end, "historical_end_utc")
        if parsed_start >= parsed_end:
            return ("clarify", "contradictory_temporal_bounds")
        if parsed_end - parsed_start > MAX_HISTORICAL_WINDOW:
            return ("unsupported", "historical_window_exceeds_seven_days")
        return None
    if start is not None or end is not None:
        return ("clarify", "contradictory_temporal_bounds")
    return None


def _purpose_problem(step: MasterProposedStep) -> tuple[str, str] | None:
    if (step.specialist, step.purpose) not in _ALLOWED_PAIRS:
        return ("unsupported", "unsupported_specialist_purpose")
    if not _capability_matches(step):
        raise ContractValidationError("capability_step_mismatch")
    return _window_problem(step)


def _gates(
    step: MasterProposedStep,
    context: MasterPlanningContext,
    by_id: Mapping[str, MasterProposedStep],
) -> tuple[MasterRouteGate, ...]:
    gates: list[MasterRouteGate] = []
    if step.hints.region_text is not None:
        gates.append(MasterRouteGate.REGION_VALIDATION_PENDING)
    if step.depends_on:
        gates.append(MasterRouteGate.DEPENDENCY_BLOCKED)
    if step.specialist is not MasterSpecialist.DECISION:
        if not gates:
            gates.append(MasterRouteGate.NOT_REQUIRED)
        return tuple(sorted(set(gates), key=lambda item: item.value))
    gates.extend(
        (
            MasterRouteGate.TRUSTED_IDENTITY_VALIDATION_PENDING,
            MasterRouteGate.CURRENTNESS_VALIDATION_PENDING,
        )
    )
    if len(step.hints.population_aircraft_ids) > 1:
        gates.append(MasterRouteGate.TARGET_AMBIGUOUS)
    discovery_ids = {
        item.step_id
        for item in by_id.values()
        if item.purpose is MasterPurpose.DISCOVERY
    }
    if any(dependency in discovery_ids for dependency in step.depends_on):
        gates.append(MasterRouteGate.IDENTITY_DISCOVERY_NEEDED)
    elif step.hints.callsign is not None or (
        step.hints.typed_aircraft_id is None
        and step.hints.recommendation_id is None
        and context.target_candidate_aircraft_id is None
        and context.target_candidate_recommendation_id is None
        and len(step.hints.population_aircraft_ids) == 0
    ):
        gates.append(MasterRouteGate.IDENTITY_DISCOVERY_NEEDED)
    if (
        context.target_candidate_aircraft_id is not None
        or context.target_candidate_recommendation_id is not None
    ):
        gates.append(MasterRouteGate.APPLICATION_TARGET_CANDIDATE_PRESENT)
    return tuple(sorted(set(gates), key=lambda item: item.value))


def _scopes(step: MasterProposedStep, context: MasterPlanningContext) -> tuple[TemporalScope, ...]:
    """Return evidence scopes. An empty tuple means the Decision mode is unresolved."""

    if step.specialist is MasterSpecialist.HISTORICAL_ANALYTICS:
        return (TemporalScope.HISTORICAL,)
    if step.specialist is MasterSpecialist.LIVE_OPS:
        return (TemporalScope.CURRENT,)
    recommendation_evidence = (
        step.hints.recommendation_id is not None
        or context.target_candidate_recommendation_id is not None
    )
    current_requested = step.temporal_intent is MasterRouteTemporalIntent.CURRENT
    if step.needs_persisted_evidence and current_requested:
        return (TemporalScope.CURRENT, TemporalScope.PERSISTED)
    if step.needs_persisted_evidence or recommendation_evidence:
        return (TemporalScope.PERSISTED,)
    if current_requested:
        return (TemporalScope.CURRENT,)
    return ()


def _depth(step_id: str, by_id: Mapping[str, MasterProposedStep], seen: set[str]) -> int:
    if step_id in seen:
        raise ContractValidationError("cyclic_dependency")
    step = by_id.get(step_id)
    if step is None:
        raise ContractValidationError("missing_dependency_id")
    if not step.depends_on:
        return 0
    seen.add(step_id)
    value = 1 + max(_depth(item, by_id, seen) for item in step.depends_on)
    seen.remove(step_id)
    return value


def _contradicts_candidate(step: MasterProposedStep, context: MasterPlanningContext) -> None:
    aircraft = context.target_candidate_aircraft_id
    recommendation = context.target_candidate_recommendation_id
    if step.hints.typed_aircraft_id is not None and aircraft is not None:
        if step.hints.typed_aircraft_id != aircraft:
            raise ContractValidationError("contradictory_targets")
    if step.hints.recommendation_id is not None and recommendation is not None:
        if step.hints.recommendation_id != recommendation:
            raise ContractValidationError("contradictory_targets")
    if aircraft is not None and step.hints.recommendation_id is not None:
        raise ContractValidationError("contradictory_targets")
    if recommendation is not None and step.hints.typed_aircraft_id is not None:
        raise ContractValidationError("contradictory_targets")
    if (
        step.hints.typed_aircraft_id is not None
        and step.hints.population_aircraft_ids
        and step.hints.typed_aircraft_id not in step.hints.population_aircraft_ids
    ):
        raise ContractValidationError("contradictory_targets")


def _build_plan(
    context: MasterPlanningContext,
    steps: tuple[MasterProposedStep, ...],
    deferred: tuple[MasterDeferredSynthesis, ...],
    omissions: tuple[str, ...],
) -> MasterRoutePlan:
    by_id = {item.step_id: item for item in steps}
    _reject_cycles(by_id)
    ordered = tuple(
        sorted(
            steps,
            key=lambda item: (
                _depth(item.step_id, by_id, set()),
                item.specialist.value,
                item.purpose.value,
                item.step_id,
            ),
        )
    )
    planned: list[MasterRouteStepPlan] = []
    for index, step in enumerate(ordered, start=1):
        _contradicts_candidate(step, context)
        scopes = _scopes(step, context)
        gates = _gates(step, context, by_id)
        if not scopes:
            gates = tuple(
                sorted(
                    (*gates, MasterRouteGate.TEMPORAL_SCOPE_UNRESOLVED),
                    key=lambda item: item.value,
                )
            )
        ready = gates == (MasterRouteGate.NOT_REQUIRED,)
        expected_aircraft = None
        expected_recommendation = None
        if step.specialist is MasterSpecialist.DECISION:
            expected_aircraft = context.target_candidate_aircraft_id
            expected_recommendation = context.target_candidate_recommendation_id
        invocation_id = f"{context.master_request_id}-{index}"
        specialist_request_id = f"{context.master_request_id}-specialist-{index}"
        identities = (
            context.master_request_id,
            invocation_id,
            specialist_request_id,
            context.correlation_id,
        )
        if len(set(identities)) != 4:
            raise ContractValidationError("request_identity_not_distinct")
        invocation = MasterInvocationAuthorization(
            master_request_id=context.master_request_id,
            invocation_id=invocation_id,
            specialist=step.specialist,
            purpose=step.purpose,
            requirement=step.requirement,
            specialist_request_id=specialist_request_id,
            correlation_id=context.correlation_id,
            application_request_time_utc=context.application_request_time_utc,
            expected_aircraft_id=expected_aircraft,
            expected_recommendation_id=expected_recommendation,
        )
        planned.append(
            MasterRouteStepPlan(
                step_id=step.step_id,
                invocation=invocation,
                capability=step.capability,
                gates=gates,
                ready_to_execute=ready,
                temporal_scopes=scopes,
                depends_on=tuple(sorted(step.depends_on)),
                region_text=step.hints.region_text,
                needs_persisted_evidence=step.needs_persisted_evidence,
                target_authorization_granted=False,
                historical_start_utc=step.historical_start_utc,
                historical_end_utc=step.historical_end_utc,
                untrusted_hints=step.hints,
                hint_trust=MasterHintTrust.UNTRUSTED,
            )
        )
    return MasterRoutePlan(
        master_request_id=context.master_request_id,
        canonical_user_text=context.canonical_user_text,
        steps=tuple(planned),
        deferred_synthesis=deferred,
        unsupported_omissions=omissions,
    )


def _without_omitted_dependencies(
    steps: list[MasterProposedStep],
    omissions: list[str],
) -> tuple[list[MasterProposedStep], list[str], tuple[str, ...]]:
    """Drop optional dependents of omitted steps. Required dependents fail closed."""

    remaining = list(steps)
    while True:
        known = {item.step_id for item in remaining}
        dangling = []
        for step in remaining:
            missing = tuple(item for item in step.depends_on if item not in known)
            if missing:
                dangling.append((step, missing))
        if not dangling:
            return remaining, omissions, ()
        required = [
            (step, missing)
            for step, missing in dangling
            if step.requirement is MasterRequirement.REQUIRED
        ]
        if required:
            reasons = tuple(
                f"required_dependency_omitted:{step.step_id}:{dependency}"
                for step, missing in required
                for dependency in missing
            )
            return remaining, omissions, reasons
        drop: set[str] = set()
        for step, missing in dangling:
            for dependency in missing:
                omissions.append(
                    f"optional_step_omitted:{step.step_id}:dependency_omitted:{dependency}"
                )
            drop.add(step.step_id)
        remaining = [step for step in remaining if step.step_id not in drop]


def authorize_route(
    proposal: MasterRouteProposal,
    context: MasterPlanningContext,
) -> MasterRouteDecision:
    """Validate one proposal. The result never grants production execution."""

    if not isinstance(proposal, MasterRouteProposal):
        raise TypeError("proposal is required")
    if not isinstance(context, MasterPlanningContext):
        raise TypeError("planning context is required")
    if proposal.user_text != context.canonical_user_text:
        raise ContractValidationError("user_text_mismatch")
    if V1_AUTHORITY.mode.value != "READ_ONLY_ADVISORY":
        raise ContractValidationError("invalid_authority_mode")

    prohibited = tuple(
        item.capability
        for item in proposal.capabilities
        if item.capability in PROHIBITED_V1_CAPABILITIES
        or item.capability not in ALLOWED_V1_CAPABILITIES
    )
    if prohibited:
        return _decision(
            MasterRouteOutcome.REFUSED,
            tuple(f"prohibited_capability:{item.value}" for item in prohibited),
        )

    proposed = {item.capability for item in proposal.capabilities}
    for step in proposal.steps:
        if step.capability not in proposed:
            raise ContractValidationError("capability_not_proposed")
        if step.capability in PROHIBITED_V1_CAPABILITIES:
            return _decision(
                MasterRouteOutcome.REFUSED,
                (f"prohibited_capability:{step.capability.value}",),
            )

    deferred: list[MasterDeferredSynthesis] = []
    omissions: list[str] = []
    for work in sorted(proposal.capabilities, key=lambda item: item.capability.value):
        if work.capability is _VISUALIZATION and work.requirement is MasterRequirement.REQUIRED:
            return _decision(
                MasterRouteOutcome.UNSUPPORTED,
                (f"unsupported_capability:{work.capability.value}",),
            )

    kept: list[MasterProposedStep] = []
    unsupported: list[str] = []
    clarify: list[str] = []
    for step in proposal.steps:
        problem = _purpose_problem(step)
        if problem is None:
            kept.append(step)
            continue
        kind, code = problem
        if step.requirement is MasterRequirement.OPTIONAL:
            omissions.append(f"optional_step_omitted:{step.step_id}:{code}")
            continue
        if kind == "clarify":
            clarify.append(code)
        else:
            unsupported.append(code)
    if unsupported:
        return _decision(MasterRouteOutcome.UNSUPPORTED, tuple(dict.fromkeys(unsupported)))
    if clarify:
        return _decision(
            MasterRouteOutcome.CLARIFICATION_REQUIRED,
            tuple(dict.fromkeys(clarify)),
        )

    kept, omissions, blocked = _without_omitted_dependencies(kept, omissions)
    if blocked:
        return _decision(MasterRouteOutcome.UNSUPPORTED, blocked)

    for work in sorted(proposal.capabilities, key=lambda item: item.capability.value):
        capability = work.capability
        if capability in _SYNTHESIS_CAPABILITIES:
            deferred.append(
                MasterDeferredSynthesis(capability=capability, requirement=work.requirement)
            )
            continue
        if capability is _VISUALIZATION:
            omissions.append(f"unsupported_capability:{capability.value}")
            continue
        if capability in _SPECIALIST_CAPABILITIES and not any(
            _covers(step, capability) for step in kept
        ):
            if work.requirement is MasterRequirement.REQUIRED:
                return _decision(
                    MasterRouteOutcome.CLARIFICATION_REQUIRED,
                    ("required_capability_has_no_step",),
                )
            omissions.append(f"optional_capability_omitted:{capability.value}")
    regions = {item.hints.region_text for item in kept if item.hints.region_text is not None}
    if len(regions) > 1:
        return _decision(
            MasterRouteOutcome.CLARIFICATION_REQUIRED,
            ("ambiguous_geographic_scope",),
        )
    plan = _build_plan(context, tuple(kept), tuple(deferred), tuple(omissions))
    return _decision(MasterRouteOutcome.AUTHORIZED_PLAN, ("plan_bounded",), plan)
