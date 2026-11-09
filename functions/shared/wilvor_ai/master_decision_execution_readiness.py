"""Offline readiness of an independently trusted Decision target.

A Master plan may name one Decision invocation. This module records what
would still have to be true before that invocation had an independently
trusted target. It does not create the target.

These materials stay distinct, and the first five do not authorize a
Decision invocation:

- a user-provided aircraft identifier (``MasterTypedIdObservation``)
- a callsign (``MasterCallsignObservation``)
- a model route hint (``MasterRouteHints``, trust ``UNTRUSTED``)
- a Master comparison candidate (``expected_aircraft_id`` or
  ``expected_recommendation_id``)
- a Live Ops discovery candidate (``LiveOpsDiscovery``, ``LiveOpsEntityHint``,
  or aircraft ids copied from a callsign observation)

An authorized persisted recommendation id is a separate category. An id that
appears on an earlier result, a comparison candidate, or a route hint is not
that category. It is not current, and it does not authorize persisted
recommendation fanout.

A genuinely trusted Decision target is the remaining category. No such
target is available. This module does not construct a ``DecisionSpecialist``
request, an authorization token, or a specialist runtime. ``MAX_DECISION_STEPS``
stays the routing limit. Candidate lists are not turned into more Decision
invocations.

Authenticated identity, currentness, an allowed target population, and
separately authorized persisted recommendation fanout stay unmet. A missing
risk fact is not ``LOW`` risk. Master execution authority stays ``DENIED``,
production authority stays ``DENIED``, source authenticity stays
``NOT_ESTABLISHED``, and request coverage stays ``NOT_ESTABLISHED``.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from wilvor_ai.contracts import (
    PROVENANCE_TOKEN_MAX_LENGTH,
    ContractValidationError,
    TemporalScope,
)
from wilvor_ai.live_ops_public_contracts import LiveOpsDiscovery, LiveOpsEntityHint
from wilvor_ai.master_contracts import (
    MasterCallsignObservation,
    MasterPopulationObservation,
    MasterProductionAuthority,
    MasterSpecialist,
    MasterTypedIdObservation,
)
from wilvor_ai.master_decision_binding import (
    MasterDecisionBinding,
    prepare_master_decision_binding,
)
from wilvor_ai.master_evidence_admission import MasterSourceAuthenticity
from wilvor_ai.master_parallel_contracts import MasterParallelContractBundle
from wilvor_ai.master_routing import (
    MAX_DECISION_STEPS,
    MAX_UNTRUSTED_POPULATION_HINTS,
    MasterHintTrust,
    MasterRequestCoverage,
    MasterRouteExecutionAuthority,
    MasterRouteGate,
)
from wilvor_ai.master_specialist_adapters import MasterSpecialistAdapterDescriptor


MASTER_DECISION_EXECUTION_READINESS_SCHEMA_VERSION = (
    "wilvor.ai.master_decision_execution_readiness.v1"
)

MASTER_DECISION_EXECUTION_READINESS_EXPLANATION = (
    "An independently trusted Decision target is unavailable. "
    "User-provided aircraft identifiers, callsigns, model route hints, "
    "Master comparison candidates, and Live Ops discovery candidates do not "
    "authorize a Decision invocation. Authenticated identity, currentness, "
    "an allowed target population, and a separately authorized persisted "
    "recommendation fanout remain unmet. A persisted recommendation id from "
    "an earlier result is not current and does not authorize fanout. "
    "Absent risk data is not LOW risk."
)

_AIRCRAFT_FIELDS = frozenset(
    {
        "aircraft_id",
        "typed_aircraft_id",
        "population_aircraft_id",
        "entity_id",
    }
)
_TOKEN_FIELDS = _AIRCRAFT_FIELDS | {"callsign", "recommendation_id"}


class DecisionTargetMaterialKind(str, Enum):
    """Where an offered Decision selector came from. Not a trust level."""

    USER_PROVIDED_AIRCRAFT_IDENTIFIER = "USER_PROVIDED_AIRCRAFT_IDENTIFIER"
    CALLSIGN = "CALLSIGN"
    MODEL_ROUTE_HINT = "MODEL_ROUTE_HINT"
    MASTER_COMPARISON_CANDIDATE = "MASTER_COMPARISON_CANDIDATE"
    LIVE_OPS_DISCOVERY_CANDIDATE = "LIVE_OPS_DISCOVERY_CANDIDATE"
    AUTHORIZED_PERSISTED_RECOMMENDATION_ID = "AUTHORIZED_PERSISTED_RECOMMENDATION_ID"
    TRUSTED_DECISION_TARGET = "TRUSTED_DECISION_TARGET"


NON_AUTHORIZING_DECISION_TARGET_KINDS = (
    DecisionTargetMaterialKind.USER_PROVIDED_AIRCRAFT_IDENTIFIER,
    DecisionTargetMaterialKind.CALLSIGN,
    DecisionTargetMaterialKind.MODEL_ROUTE_HINT,
    DecisionTargetMaterialKind.MASTER_COMPARISON_CANDIDATE,
    DecisionTargetMaterialKind.LIVE_OPS_DISCOVERY_CANDIDATE,
)

_UNAVAILABLE_KINDS = (
    DecisionTargetMaterialKind.AUTHORIZED_PERSISTED_RECOMMENDATION_ID,
    DecisionTargetMaterialKind.TRUSTED_DECISION_TARGET,
)

_MATERIAL_SHAPES = frozenset(
    {
        (
            DecisionTargetMaterialKind.USER_PROVIDED_AIRCRAFT_IDENTIFIER,
            "MasterTypedIdObservation",
            "aircraft_id",
        ),
        (
            DecisionTargetMaterialKind.CALLSIGN,
            "MasterCallsignObservation",
            "callsign",
        ),
        (DecisionTargetMaterialKind.MODEL_ROUTE_HINT, "MasterRouteHints", "callsign"),
        (
            DecisionTargetMaterialKind.MODEL_ROUTE_HINT,
            "MasterRouteHints",
            "typed_aircraft_id",
        ),
        (
            DecisionTargetMaterialKind.MODEL_ROUTE_HINT,
            "MasterRouteHints",
            "recommendation_id",
        ),
        (
            DecisionTargetMaterialKind.MODEL_ROUTE_HINT,
            "MasterRouteHints",
            "population_aircraft_id",
        ),
        (DecisionTargetMaterialKind.MODEL_ROUTE_HINT, "MasterRouteHints", "region_text"),
        (
            DecisionTargetMaterialKind.MASTER_COMPARISON_CANDIDATE,
            "MasterInvocationAuthorization",
            "aircraft_id",
        ),
        (
            DecisionTargetMaterialKind.MASTER_COMPARISON_CANDIDATE,
            "MasterInvocationAuthorization",
            "recommendation_id",
        ),
        (
            DecisionTargetMaterialKind.LIVE_OPS_DISCOVERY_CANDIDATE,
            "LiveOpsDiscovery",
            "callsign",
        ),
        (
            DecisionTargetMaterialKind.LIVE_OPS_DISCOVERY_CANDIDATE,
            "LiveOpsDiscovery",
            "aircraft_id",
        ),
        (
            DecisionTargetMaterialKind.LIVE_OPS_DISCOVERY_CANDIDATE,
            "LiveOpsEntityHint",
            "entity_id",
        ),
        (
            DecisionTargetMaterialKind.LIVE_OPS_DISCOVERY_CANDIDATE,
            "LiveOpsEntityHint",
            "callsign",
        ),
        (
            DecisionTargetMaterialKind.LIVE_OPS_DISCOVERY_CANDIDATE,
            "MasterCallsignObservation",
            "aircraft_id",
        ),
        (
            DecisionTargetMaterialKind.AUTHORIZED_PERSISTED_RECOMMENDATION_ID,
            "unavailable",
            "unavailable",
        ),
        (
            DecisionTargetMaterialKind.TRUSTED_DECISION_TARGET,
            "unavailable",
            "unavailable",
        ),
    }
)


class DecisionTargetReadinessRequirement(str, Enum):
    """Proof this module cannot mark satisfied."""

    AUTHENTICATED_IDENTITY = "AUTHENTICATED_IDENTITY"
    CURRENTNESS = "CURRENTNESS"
    ALLOWED_TARGET_POPULATION = "ALLOWED_TARGET_POPULATION"
    SEPARATELY_AUTHORIZED_PERSISTED_RECOMMENDATION_FANOUT = (
        "SEPARATELY_AUTHORIZED_PERSISTED_RECOMMENDATION_FANOUT"
    )


class DecisionTargetReadinessStatus(str, Enum):
    """The only readiness outcome. A trusted target is not available."""

    TRUSTED_TARGET_UNAVAILABLE = "TRUSTED_TARGET_UNAVAILABLE"


def _closed(value: object, denied: object, code: str) -> None:
    if value is not denied:
        raise ContractValidationError(code)


def _token(value: object, field_name: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or any(character.isspace() for character in value)
        or len(value) > PROVENANCE_TOKEN_MAX_LENGTH
    ):
        raise ContractValidationError(f"invalid_{field_name}")
    return value


def _bounded_tuple(value: object, field_name: str) -> tuple[object, ...]:
    if not isinstance(value, tuple):
        raise ContractValidationError(f"invalid_{field_name}")
    if len(value) > MAX_UNTRUSTED_POPULATION_HINTS:
        raise ContractValidationError("population_aircraft_ids_exceed_bound")
    return value


def _reject_promotions(
    *,
    supplied_evidence: object,
    trusted_target: object,
    risk_level: object,
    authenticated_principal: object,
    absent_risk_is_low_risk: object,
    risk_recorded: object,
    persisted_recommendations_current: object,
    persisted_fanout_authorized: object,
    persisted_fanout_invocation_count: object,
    additional_decision_invocations: object,
    max_decision_steps: object,
    capability_authorizes_dispatch: object,
    principal_authenticated: object,
    caller_evidence_satisfies_gate: object,
    user_text_copied_into_request: object,
    model_text_selects_target: object,
    callsign_is_aircraft_identity: object,
    live_ops_discovery_is_trusted_target: object,
    live_ops_entity_hint_is_trusted_target: object,
    comparison_candidates_authorize_target: object,
    decision_invocation_authorized: object,
    trusted_decision_target_available: object,
    allowed_target_population_satisfied: object,
    authenticated_identity_satisfied: object,
    currentness_satisfied: object,
) -> None:
    """Reject caller attempts to promote a candidate into authority."""

    _closed(supplied_evidence, None, "caller_evidence_cannot_satisfy_gate")
    _closed(trusted_target, None, "target_authorization_not_granted")
    _closed(risk_level, None, "absent_risk_is_not_low_risk")
    _closed(authenticated_principal, None, "principal_not_authenticated")
    _closed(absent_risk_is_low_risk, False, "absent_risk_is_not_low_risk")
    _closed(risk_recorded, False, "absent_risk_is_not_low_risk")
    _closed(persisted_recommendations_current, False, "persisted_data_is_not_current")
    _closed(persisted_fanout_authorized, False, "persisted_evidence_not_authorized")
    _closed(persisted_fanout_invocation_count, 0, "persisted_evidence_not_authorized")
    _closed(additional_decision_invocations, 0, "decision_fanout_not_permitted")
    _closed(max_decision_steps, MAX_DECISION_STEPS, "decision_fanout_not_permitted")
    _closed(capability_authorizes_dispatch, False, "capability_consumption_not_dispatch_permission")
    _closed(principal_authenticated, False, "principal_not_authenticated")
    _closed(caller_evidence_satisfies_gate, False, "caller_evidence_cannot_satisfy_gate")
    _closed(user_text_copied_into_request, False, "user_text_not_a_decision_target")
    _closed(model_text_selects_target, False, "model_text_cannot_select_target")
    _closed(callsign_is_aircraft_identity, False, "callsign_not_authenticated_aircraft")
    _closed(live_ops_discovery_is_trusted_target, False, "live_ops_discovery_not_trusted_target")
    _closed(live_ops_entity_hint_is_trusted_target, False, "live_ops_entity_not_trusted_target")
    _closed(comparison_candidates_authorize_target, False, "target_authorization_not_granted")
    _closed(decision_invocation_authorized, False, "target_authorization_not_granted")
    _closed(trusted_decision_target_available, False, "target_authorization_not_granted")
    _closed(allowed_target_population_satisfied, False, "target_authorization_not_granted")
    _closed(authenticated_identity_satisfied, False, "target_authorization_not_granted")
    _closed(currentness_satisfied, False, "target_authorization_not_granted")


@dataclass(frozen=True)
class DecisionTargetMaterial:
    """One offered selector. Construction cannot mark it authorizing."""

    kind: DecisionTargetMaterialKind
    source: str
    field_name: str
    identifier: str | None
    available: bool = True
    authorizes_decision_invocation: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.kind, DecisionTargetMaterialKind):
            raise ContractValidationError("invalid_decision_target_material")
        shape = (self.kind, self.source, self.field_name)
        if shape not in _MATERIAL_SHAPES:
            raise ContractValidationError("invalid_decision_target_material")
        _closed(self.authorizes_decision_invocation, False, "target_authorization_not_granted")
        unavailable = self.kind in _UNAVAILABLE_KINDS
        if unavailable:
            _closed(self.available, False, "target_authorization_not_granted")
            if self.identifier is not None:
                raise ContractValidationError("target_authorization_not_granted")
            return
        _closed(self.available, True, "invalid_decision_target_material")
        if self.field_name == "region_text":
            if (
                not isinstance(self.identifier, str)
                or not self.identifier
                or self.identifier != self.identifier.strip()
                or len(self.identifier) > PROVENANCE_TOKEN_MAX_LENGTH
            ):
                raise ContractValidationError("invalid_region_text")
            return
        if self.field_name not in _TOKEN_FIELDS:
            raise ContractValidationError("invalid_decision_target_material")
        _token(self.identifier, self.field_name)


def _material(
    kind: DecisionTargetMaterialKind,
    source: str,
    field_name: str,
    identifier: str,
) -> DecisionTargetMaterial:
    return DecisionTargetMaterial(
        kind=kind,
        source=source,
        field_name=field_name,
        identifier=identifier,
    )


def _unavailable(kind: DecisionTargetMaterialKind) -> DecisionTargetMaterial:
    return DecisionTargetMaterial(
        kind=kind,
        source="unavailable",
        field_name="unavailable",
        identifier=None,
        available=False,
    )


def _aircraft_ids(
    materials: tuple[DecisionTargetMaterial, ...],
    population_observation_aircraft_ids: tuple[str, ...],
) -> set[str]:
    found = set(population_observation_aircraft_ids)
    for item in materials:
        if item.field_name in _AIRCRAFT_FIELDS and item.identifier is not None:
            found.add(item.identifier)
    return found


def _recommendation_ids(
    materials: tuple[DecisionTargetMaterial, ...],
    earlier_result_recommendation_ids: tuple[str, ...],
) -> set[str]:
    found = set(earlier_result_recommendation_ids)
    for item in materials:
        if (
            item.available
            and item.field_name == "recommendation_id"
            and item.identifier is not None
        ):
            found.add(item.identifier)
    return found


def _identity_ambiguous(
    materials: tuple[DecisionTargetMaterial, ...],
    population_observation_aircraft_ids: tuple[str, ...],
    earlier_result_recommendation_ids: tuple[str, ...],
) -> bool:
    aircraft = _aircraft_ids(materials, population_observation_aircraft_ids)
    recommendations = _recommendation_ids(materials, earlier_result_recommendation_ids)
    if len(aircraft) > 1:
        return True
    return bool(aircraft) and bool(recommendations)


def _persisted_recommendation_ambiguous(
    materials: tuple[DecisionTargetMaterial, ...],
    earlier_result_recommendation_ids: tuple[str, ...],
) -> bool:
    return len(_recommendation_ids(materials, earlier_result_recommendation_ids)) > 1


def _hint_pairs(binding: MasterDecisionBinding) -> set[tuple[str, str]]:
    pairs: set[tuple[str, str]] = set()
    if binding.untrusted_callsign is not None:
        pairs.add(("callsign", binding.untrusted_callsign))
    if binding.untrusted_typed_aircraft_id is not None:
        pairs.add(("typed_aircraft_id", binding.untrusted_typed_aircraft_id))
    if binding.untrusted_recommendation_hint is not None:
        pairs.add(("recommendation_id", binding.untrusted_recommendation_hint))
    for item in binding.untrusted_population_aircraft_ids:
        pairs.add(("population_aircraft_id", item))
    return pairs


def _comparison_pairs(binding: MasterDecisionBinding) -> set[tuple[str, str]]:
    pairs: set[tuple[str, str]] = set()
    if binding.comparison_candidate_aircraft_id is not None:
        pairs.add(("aircraft_id", binding.comparison_candidate_aircraft_id))
    if binding.comparison_candidate_recommendation_id is not None:
        pairs.add(("recommendation_id", binding.comparison_candidate_recommendation_id))
    return pairs


def _require_plan_materials(
    binding: MasterDecisionBinding,
    materials: tuple[DecisionTargetMaterial, ...],
    untrusted_region_text: str | None,
) -> None:
    hint_pairs = {
        (item.field_name, item.identifier)
        for item in materials
        if item.kind is DecisionTargetMaterialKind.MODEL_ROUTE_HINT
        and item.field_name != "region_text"
    }
    if hint_pairs != _hint_pairs(binding):
        raise ContractValidationError("invocation_binding_mismatch")
    comparison_pairs = {
        (item.field_name, item.identifier)
        for item in materials
        if item.kind is DecisionTargetMaterialKind.MASTER_COMPARISON_CANDIDATE
    }
    if comparison_pairs != _comparison_pairs(binding):
        raise ContractValidationError("invocation_binding_mismatch")
    region_ids = tuple(
        item.identifier
        for item in materials
        if item.kind is DecisionTargetMaterialKind.MODEL_ROUTE_HINT
        and item.field_name == "region_text"
    )
    if untrusted_region_text is None:
        if region_ids:
            raise ContractValidationError("invocation_binding_mismatch")
    elif region_ids != (untrusted_region_text,):
        raise ContractValidationError("invocation_binding_mismatch")
    for kind in _UNAVAILABLE_KINDS:
        matches = [item for item in materials if item.kind is kind]
        if len(matches) != 1 or matches[0].available is not False or matches[0].identifier is not None:
            raise ContractValidationError("target_authorization_not_granted")


def _reject_authority(report: "MasterDecisionExecutionReadiness") -> None:
    _closed(
        report.status,
        DecisionTargetReadinessStatus.TRUSTED_TARGET_UNAVAILABLE,
        "target_authorization_not_granted",
    )
    if report.schema_version != MASTER_DECISION_EXECUTION_READINESS_SCHEMA_VERSION:
        raise ContractValidationError("invalid_schema_version")
    if report.explanation != MASTER_DECISION_EXECUTION_READINESS_EXPLANATION:
        raise ContractValidationError("invalid_decision_readiness_explanation")
    if report.unmet_requirements != tuple(DecisionTargetReadinessRequirement):
        raise ContractValidationError("decision_target_requirement_unsatisfied")
    _closed(report.selected_aircraft_id, None, "target_authorization_not_granted")
    _closed(report.selected_recommendation_id, None, "target_authorization_not_granted")
    _closed(report.trusted_decision_target_available, False, "target_authorization_not_granted")
    if report.authorized_persisted_recommendation_ids != ():
        raise ContractValidationError("persisted_evidence_not_authorized")
    _closed(report.earlier_result_recommendations_are_current, False, "persisted_data_is_not_current")
    _closed(
        report.earlier_result_recommendations_authorize_fanout,
        False,
        "persisted_evidence_not_authorized",
    )
    _closed(report.allowed_target_population_satisfied, False, "target_authorization_not_granted")
    _closed(report.authenticated_identity_satisfied, False, "target_authorization_not_granted")
    _closed(report.currentness_satisfied, False, "target_authorization_not_granted")
    _closed(report.persisted_fanout_authorized, False, "persisted_evidence_not_authorized")
    _closed(report.persisted_fanout_invocation_count, 0, "persisted_evidence_not_authorized")
    if report.fanout_invocation_ids != ():
        raise ContractValidationError("decision_fanout_not_permitted")
    _closed(report.recorded_risk_level, None, "absent_risk_is_not_low_risk")
    _closed(report.absent_risk_is_low_risk, False, "absent_risk_is_not_low_risk")
    _closed(report.risk_recorded, False, "absent_risk_is_not_low_risk")
    _closed(report.max_decision_steps, MAX_DECISION_STEPS, "decision_fanout_not_permitted")
    _closed(report.decision_invocation_count, 1, "decision_fanout_not_permitted")
    _closed(report.decision_invocation_authorized, False, "target_authorization_not_granted")
    _closed(report.execution_authority, MasterRouteExecutionAuthority.DENIED, "execution_authority_not_granted")
    _closed(report.production_authority, MasterProductionAuthority.DENIED, "production_authority_not_issued")
    _closed(
        report.source_authenticity,
        MasterSourceAuthenticity.NOT_ESTABLISHED,
        "source_authenticity_not_established",
    )
    _closed(
        report.request_coverage,
        MasterRequestCoverage.NOT_ESTABLISHED,
        "request_coverage_not_established",
    )
    _closed(report.end_to_end_fulfilled, False, "end_to_end_fulfillment_not_granted")
    _closed(report.dispatch_permitted, False, "execution_not_granted")
    _closed(report.executable, False, "execution_not_granted")
    _closed(report.ready_to_execute, False, "decision_step_not_executable")
    _closed(report.target_authorization_granted, False, "target_authorization_not_granted")
    _closed(report.decision_target_trusted, False, "target_authorization_not_granted")
    _closed(report.authorization_token, None, "authorization_token_not_accepted")
    _closed(report.authorization_signature, None, "authorization_signature_not_accepted")
    _closed(report.authorization_certificate, None, "authorization_certificate_not_accepted")
    _closed(report.user_text_copied_into_request, False, "user_text_not_a_decision_target")
    _closed(report.model_text_selects_target, False, "model_text_cannot_select_target")
    _closed(report.callsign_is_aircraft_identity, False, "callsign_not_authenticated_aircraft")
    _closed(report.capability_authorizes_dispatch, False, "capability_consumption_not_dispatch_permission")
    _closed(report.principal_authenticated, False, "principal_not_authenticated")
    _closed(report.caller_evidence_satisfies_gate, False, "caller_evidence_cannot_satisfy_gate")
    _closed(report.comparison_candidates_authorize_target, False, "target_authorization_not_granted")
    _closed(report.live_ops_discovery_is_trusted_target, False, "live_ops_discovery_not_trusted_target")
    _closed(report.live_ops_entity_hint_is_trusted_target, False, "live_ops_entity_not_trusted_target")
    _closed(report.hint_trust, MasterHintTrust.UNTRUSTED, "selection_hints_not_trusted")


@dataclass(frozen=True)
class MasterDecisionExecutionReadiness:
    """One blocked Decision-target assessment. It cannot be executed.

    ``binding`` is the existing blocked planning identity. This object does
    not keep the parallel bundle, evidence inputs, a Decision request, or a
    runtime. ``recorded_risk_level`` has no value: missing risk is not LOW.
    """

    binding: MasterDecisionBinding
    materials: tuple[DecisionTargetMaterial, ...]
    population_observation_aircraft_ids: tuple[str, ...]
    earlier_result_recommendation_ids: tuple[str, ...]
    temporal_scopes: tuple[TemporalScope, ...]
    untrusted_region_text: str | None
    gates: tuple[MasterRouteGate, ...]
    identity_ambiguous: bool
    persisted_recommendation_ambiguous: bool
    decision_invocation_count: int
    needs_persisted_evidence: bool
    unmet_requirements: tuple[DecisionTargetReadinessRequirement, ...] = tuple(
        DecisionTargetReadinessRequirement
    )
    status: DecisionTargetReadinessStatus = (
        DecisionTargetReadinessStatus.TRUSTED_TARGET_UNAVAILABLE
    )
    explanation: str = MASTER_DECISION_EXECUTION_READINESS_EXPLANATION
    schema_version: str = MASTER_DECISION_EXECUTION_READINESS_SCHEMA_VERSION
    selected_aircraft_id: str | None = None
    selected_recommendation_id: str | None = None
    trusted_decision_target_available: bool = False
    authorized_persisted_recommendation_ids: tuple[str, ...] = ()
    earlier_result_recommendations_are_current: bool = False
    earlier_result_recommendations_authorize_fanout: bool = False
    allowed_target_population_satisfied: bool = False
    authenticated_identity_satisfied: bool = False
    currentness_satisfied: bool = False
    persisted_fanout_authorized: bool = False
    persisted_fanout_invocation_count: int = 0
    fanout_invocation_ids: tuple[str, ...] = ()
    recorded_risk_level: None = None
    absent_risk_is_low_risk: bool = False
    risk_recorded: bool = False
    max_decision_steps: int = MAX_DECISION_STEPS
    decision_invocation_authorized: bool = False
    execution_authority: MasterRouteExecutionAuthority = MasterRouteExecutionAuthority.DENIED
    production_authority: MasterProductionAuthority = MasterProductionAuthority.DENIED
    source_authenticity: MasterSourceAuthenticity = MasterSourceAuthenticity.NOT_ESTABLISHED
    request_coverage: MasterRequestCoverage = MasterRequestCoverage.NOT_ESTABLISHED
    end_to_end_fulfilled: bool = False
    dispatch_permitted: bool = False
    executable: bool = False
    ready_to_execute: bool = False
    target_authorization_granted: bool = False
    decision_target_trusted: bool = False
    authorization_token: str | None = None
    authorization_signature: str | None = None
    authorization_certificate: str | None = None
    user_text_copied_into_request: bool = False
    model_text_selects_target: bool = False
    callsign_is_aircraft_identity: bool = False
    capability_authorizes_dispatch: bool = False
    principal_authenticated: bool = False
    caller_evidence_satisfies_gate: bool = False
    comparison_candidates_authorize_target: bool = False
    live_ops_discovery_is_trusted_target: bool = False
    live_ops_entity_hint_is_trusted_target: bool = False
    hint_trust: MasterHintTrust = MasterHintTrust.UNTRUSTED

    def __post_init__(self) -> None:
        if not isinstance(self.binding, MasterDecisionBinding):
            raise ContractValidationError("invalid_decision_binding")
        if not isinstance(self.materials, tuple) or any(
            not isinstance(item, DecisionTargetMaterial) for item in self.materials
        ):
            raise ContractValidationError("invalid_decision_target_material")
        if not isinstance(self.population_observation_aircraft_ids, tuple):
            raise ContractValidationError("invalid_population_aircraft_ids")
        for item in self.population_observation_aircraft_ids:
            _token(item, "population_aircraft_id")
        if len(self.population_observation_aircraft_ids) != len(
            set(self.population_observation_aircraft_ids)
        ):
            raise ContractValidationError("duplicate_population_aircraft_id")
        if len(self.population_observation_aircraft_ids) > MAX_UNTRUSTED_POPULATION_HINTS:
            raise ContractValidationError("population_aircraft_ids_exceed_bound")
        if not isinstance(self.earlier_result_recommendation_ids, tuple):
            raise ContractValidationError("invalid_recommendation_id")
        for item in self.earlier_result_recommendation_ids:
            _token(item, "recommendation_id")
        if len(self.earlier_result_recommendation_ids) != len(
            set(self.earlier_result_recommendation_ids)
        ):
            raise ContractValidationError("duplicate_recommendation_id")
        if len(self.earlier_result_recommendation_ids) > MAX_UNTRUSTED_POPULATION_HINTS:
            raise ContractValidationError("population_aircraft_ids_exceed_bound")
        if not isinstance(self.temporal_scopes, tuple) or any(
            not isinstance(item, TemporalScope) or item is TemporalScope.HYBRID
            for item in self.temporal_scopes
        ):
            raise ContractValidationError("invalid_temporal_scopes")
        if self.untrusted_region_text is not None and (
            not isinstance(self.untrusted_region_text, str)
            or not self.untrusted_region_text
            or self.untrusted_region_text != self.untrusted_region_text.strip()
            or len(self.untrusted_region_text) > PROVENANCE_TOKEN_MAX_LENGTH
        ):
            raise ContractValidationError("invalid_region_text")
        if not isinstance(self.gates, tuple) or any(
            not isinstance(item, MasterRouteGate) for item in self.gates
        ):
            raise ContractValidationError("invalid_gates")
        if self.gates != self.binding.gates:
            raise ContractValidationError("invocation_binding_mismatch")
        if self.needs_persisted_evidence is not self.binding.needs_persisted_evidence:
            raise ContractValidationError("invocation_binding_mismatch")
        if not isinstance(self.identity_ambiguous, bool):
            raise ContractValidationError("invalid_decision_target_material")
        if not isinstance(self.persisted_recommendation_ambiguous, bool):
            raise ContractValidationError("invalid_decision_target_material")
        _reject_authority(self)
        _require_plan_materials(self.binding, self.materials, self.untrusted_region_text)
        if self.identity_ambiguous is not _identity_ambiguous(
            self.materials,
            self.population_observation_aircraft_ids,
            self.earlier_result_recommendation_ids,
        ):
            raise ContractValidationError("invocation_binding_mismatch")
        if self.persisted_recommendation_ambiguous is not _persisted_recommendation_ambiguous(
            self.materials,
            self.earlier_result_recommendation_ids,
        ):
            raise ContractValidationError("invocation_binding_mismatch")
        if self.hint_trust is not self.binding.hint_trust:
            raise ContractValidationError("selection_hints_not_trusted")
        for item in self.materials:
            if item.kind in NON_AUTHORIZING_DECISION_TARGET_KINDS:
                _closed(item.authorizes_decision_invocation, False, "target_authorization_not_granted")


def _population_ids(
    observations: tuple[MasterPopulationObservation, ...],
) -> tuple[str, ...]:
    checked = _bounded_tuple(observations, "population_observations")
    found: list[str] = []
    for item in checked:
        if not isinstance(item, MasterPopulationObservation):
            raise ContractValidationError("invalid_population_aircraft_ids")
        found.extend(item.aircraft_ids)
    if len(found) > MAX_UNTRUSTED_POPULATION_HINTS:
        raise ContractValidationError("population_aircraft_ids_exceed_bound")
    if len(found) != len(set(found)):
        raise ContractValidationError("duplicate_population_aircraft_id")
    return tuple(found)


def _earlier_ids(value: object) -> tuple[str, ...]:
    checked = _bounded_tuple(value, "earlier_result_recommendation_ids")
    found: list[str] = []
    for item in checked:
        found.append(_token(item, "recommendation_id"))
    if len(found) != len(set(found)):
        raise ContractValidationError("duplicate_recommendation_id")
    return tuple(found)


def _typed_materials(
    observations: tuple[MasterTypedIdObservation, ...],
) -> list[DecisionTargetMaterial]:
    materials: list[DecisionTargetMaterial] = []
    for item in _bounded_tuple(observations, "typed_aircraft_observations"):
        if not isinstance(item, MasterTypedIdObservation):
            raise ContractValidationError("invalid_aircraft_id")
        materials.append(
            _material(
                DecisionTargetMaterialKind.USER_PROVIDED_AIRCRAFT_IDENTIFIER,
                "MasterTypedIdObservation",
                "aircraft_id",
                item.aircraft_id,
            )
        )
    return materials


def _callsign_materials(
    observations: tuple[MasterCallsignObservation, ...],
) -> list[DecisionTargetMaterial]:
    checked = _bounded_tuple(observations, "callsign_observations")
    materials: list[DecisionTargetMaterial] = []
    aircraft_count = 0
    for item in checked:
        if not isinstance(item, MasterCallsignObservation):
            raise ContractValidationError("invalid_callsign")
        materials.append(
            _material(
                DecisionTargetMaterialKind.CALLSIGN,
                "MasterCallsignObservation",
                "callsign",
                item.callsign,
            )
        )
        aircraft_count += len(item.aircraft_ids)
        for aircraft_id in item.aircraft_ids:
            materials.append(
                _material(
                    DecisionTargetMaterialKind.LIVE_OPS_DISCOVERY_CANDIDATE,
                    "MasterCallsignObservation",
                    "aircraft_id",
                    aircraft_id,
                )
            )
    if aircraft_count > MAX_UNTRUSTED_POPULATION_HINTS:
        raise ContractValidationError("population_aircraft_ids_exceed_bound")
    return materials


def _hint_materials(
    binding: MasterDecisionBinding,
    region_text: str | None,
) -> list[DecisionTargetMaterial]:
    materials: list[DecisionTargetMaterial] = []
    kind = DecisionTargetMaterialKind.MODEL_ROUTE_HINT
    source = "MasterRouteHints"
    if binding.untrusted_callsign is not None:
        materials.append(_material(kind, source, "callsign", binding.untrusted_callsign))
    if binding.untrusted_typed_aircraft_id is not None:
        materials.append(
            _material(kind, source, "typed_aircraft_id", binding.untrusted_typed_aircraft_id)
        )
    if binding.untrusted_recommendation_hint is not None:
        materials.append(
            _material(kind, source, "recommendation_id", binding.untrusted_recommendation_hint)
        )
    for aircraft_id in binding.untrusted_population_aircraft_ids:
        materials.append(_material(kind, source, "population_aircraft_id", aircraft_id))
    if region_text is not None:
        materials.append(_material(kind, source, "region_text", region_text))
    return materials


def _comparison_materials(binding: MasterDecisionBinding) -> list[DecisionTargetMaterial]:
    materials: list[DecisionTargetMaterial] = []
    kind = DecisionTargetMaterialKind.MASTER_COMPARISON_CANDIDATE
    source = "MasterInvocationAuthorization"
    if binding.comparison_candidate_aircraft_id is not None:
        materials.append(
            _material(kind, source, "aircraft_id", binding.comparison_candidate_aircraft_id)
        )
    if binding.comparison_candidate_recommendation_id is not None:
        materials.append(
            _material(
                kind,
                source,
                "recommendation_id",
                binding.comparison_candidate_recommendation_id,
            )
        )
    return materials


def _discovery_materials(
    discoveries: tuple[LiveOpsDiscovery, ...],
    entity_hints: tuple[LiveOpsEntityHint, ...],
) -> list[DecisionTargetMaterial]:
    materials: list[DecisionTargetMaterial] = []
    kind = DecisionTargetMaterialKind.LIVE_OPS_DISCOVERY_CANDIDATE
    aircraft_count = 0
    for item in _bounded_tuple(discoveries, "discoveries"):
        if not isinstance(item, LiveOpsDiscovery):
            raise ContractValidationError("invalid_live_ops_discovery")
        _closed(item.selection_authorized, False, "selection_not_authorized")
        materials.append(_material(kind, "LiveOpsDiscovery", "callsign", item.callsign))
        aircraft_count += len(item.aircraft_ids)
        for aircraft_id in item.aircraft_ids:
            materials.append(_material(kind, "LiveOpsDiscovery", "aircraft_id", aircraft_id))
    for item in _bounded_tuple(entity_hints, "entity_hints"):
        if not isinstance(item, LiveOpsEntityHint):
            raise ContractValidationError("invalid_entity_hint")
        materials.append(_material(kind, "LiveOpsEntityHint", "entity_id", item.entity_id))
        if item.callsign is not None:
            materials.append(_material(kind, "LiveOpsEntityHint", "callsign", item.callsign))
    if aircraft_count > MAX_UNTRUSTED_POPULATION_HINTS:
        raise ContractValidationError("population_aircraft_ids_exceed_bound")
    return materials


def _collect_materials(
    binding: MasterDecisionBinding,
    *,
    typed_aircraft_observations: tuple[MasterTypedIdObservation, ...],
    callsign_observations: tuple[MasterCallsignObservation, ...],
    discoveries: tuple[LiveOpsDiscovery, ...],
    entity_hints: tuple[LiveOpsEntityHint, ...],
    region_text: str | None,
) -> tuple[DecisionTargetMaterial, ...]:
    materials: list[DecisionTargetMaterial] = []
    materials.extend(_typed_materials(typed_aircraft_observations))
    materials.extend(_callsign_materials(callsign_observations))
    materials.extend(_hint_materials(binding, region_text))
    materials.extend(_comparison_materials(binding))
    materials.extend(_discovery_materials(discoveries, entity_hints))
    materials.append(
        _unavailable(DecisionTargetMaterialKind.AUTHORIZED_PERSISTED_RECOMMENDATION_ID)
    )
    materials.append(_unavailable(DecisionTargetMaterialKind.TRUSTED_DECISION_TARGET))
    return tuple(materials)


def assess_master_decision_execution_readiness(
    bundle: MasterParallelContractBundle,
    descriptor: MasterSpecialistAdapterDescriptor,
    typed_aircraft_observations: tuple[MasterTypedIdObservation, ...] = (),
    callsign_observations: tuple[MasterCallsignObservation, ...] = (),
    discoveries: tuple[LiveOpsDiscovery, ...] = (),
    entity_hints: tuple[LiveOpsEntityHint, ...] = (),
    population_observations: tuple[MasterPopulationObservation, ...] = (),
    earlier_result_recommendation_ids: tuple[str, ...] = (),
    *,
    supplied_evidence: object = None,
    trusted_target: object = None,
    risk_level: object = None,
    authenticated_principal: object = None,
    absent_risk_is_low_risk: bool = False,
    risk_recorded: bool = False,
    persisted_recommendations_current: bool = False,
    persisted_fanout_authorized: bool = False,
    persisted_fanout_invocation_count: int = 0,
    additional_decision_invocations: int = 0,
    max_decision_steps: int = MAX_DECISION_STEPS,
    capability_authorizes_dispatch: bool = False,
    principal_authenticated: bool = False,
    caller_evidence_satisfies_gate: bool = False,
    user_text_copied_into_request: bool = False,
    model_text_selects_target: bool = False,
    callsign_is_aircraft_identity: bool = False,
    live_ops_discovery_is_trusted_target: bool = False,
    live_ops_entity_hint_is_trusted_target: bool = False,
    comparison_candidates_authorize_target: bool = False,
    decision_invocation_authorized: bool = False,
    trusted_decision_target_available: bool = False,
    allowed_target_population_satisfied: bool = False,
    authenticated_identity_satisfied: bool = False,
    currentness_satisfied: bool = False,
    target_authorization_granted: bool = False,
    decision_target_trusted: bool = False,
    trusted_aircraft_id: str | None = None,
    trusted_recommendation_id: str | None = None,
    authorization_token: str | None = None,
    authorization_signature: str | None = None,
    authorization_certificate: str | None = None,
    execution_authority: object = MasterRouteExecutionAuthority.DENIED,
    production_authority: object = MasterProductionAuthority.DENIED,
    source_authenticity: object = MasterSourceAuthenticity.NOT_ESTABLISHED,
    request_coverage: object = MasterRequestCoverage.NOT_ESTABLISHED,
    end_to_end_fulfilled: bool = False,
    dispatch_permitted: bool = False,
    executable: bool = False,
    ready_to_execute: bool = False,
    persisted_evidence_authorized: bool = False,
) -> MasterDecisionExecutionReadiness:
    """Assess one planned Decision step and leave its target unavailable.

    The bundle is used to recover the planned step, then dropped. Supplied
    evidence is not read and cannot satisfy a gate. Matching identifier
    strings are not authenticated identity. ``identity_resolved`` on a
    callsign observation is not an application principal.
    """

    _reject_promotions(
        supplied_evidence=supplied_evidence,
        trusted_target=trusted_target,
        risk_level=risk_level,
        authenticated_principal=authenticated_principal,
        absent_risk_is_low_risk=absent_risk_is_low_risk,
        risk_recorded=risk_recorded,
        persisted_recommendations_current=persisted_recommendations_current,
        persisted_fanout_authorized=persisted_fanout_authorized,
        persisted_fanout_invocation_count=persisted_fanout_invocation_count,
        additional_decision_invocations=additional_decision_invocations,
        max_decision_steps=max_decision_steps,
        capability_authorizes_dispatch=capability_authorizes_dispatch,
        principal_authenticated=principal_authenticated,
        caller_evidence_satisfies_gate=caller_evidence_satisfies_gate,
        user_text_copied_into_request=user_text_copied_into_request,
        model_text_selects_target=model_text_selects_target,
        callsign_is_aircraft_identity=callsign_is_aircraft_identity,
        live_ops_discovery_is_trusted_target=live_ops_discovery_is_trusted_target,
        live_ops_entity_hint_is_trusted_target=live_ops_entity_hint_is_trusted_target,
        comparison_candidates_authorize_target=comparison_candidates_authorize_target,
        decision_invocation_authorized=decision_invocation_authorized,
        trusted_decision_target_available=trusted_decision_target_available,
        allowed_target_population_satisfied=allowed_target_population_satisfied,
        authenticated_identity_satisfied=authenticated_identity_satisfied,
        currentness_satisfied=currentness_satisfied,
    )
    population_ids = _population_ids(population_observations)
    earlier_ids = _earlier_ids(earlier_result_recommendation_ids)
    binding = prepare_master_decision_binding(
        bundle,
        descriptor,
        target_authorization_granted=target_authorization_granted,
        decision_target_trusted=decision_target_trusted,
        trusted_aircraft_id=trusted_aircraft_id,
        trusted_recommendation_id=trusted_recommendation_id,
        authorization_token=authorization_token,
        authorization_signature=authorization_signature,
        authorization_certificate=authorization_certificate,
        execution_authority=execution_authority,
        production_authority=production_authority,
        source_authenticity=source_authenticity,
        request_coverage=request_coverage,
        end_to_end_fulfilled=end_to_end_fulfilled,
        dispatch_permitted=dispatch_permitted,
        executable=executable,
        ready_to_execute=ready_to_execute,
        persisted_evidence_authorized=persisted_evidence_authorized,
        comparison_candidates_authorize_target=comparison_candidates_authorize_target,
        callsign_is_aircraft_identity=callsign_is_aircraft_identity,
        user_text_copied_into_request=user_text_copied_into_request,
        model_text_selects_target=model_text_selects_target,
        live_ops_discovery_is_trusted_target=live_ops_discovery_is_trusted_target,
        live_ops_entity_hint_is_trusted_target=live_ops_entity_hint_is_trusted_target,
        absent_risk_is_low_risk=absent_risk_is_low_risk,
        persisted_data_is_necessarily_current=persisted_recommendations_current,
    )
    decision_steps = [
        step
        for step in bundle.plan.steps
        if step.invocation.specialist is MasterSpecialist.DECISION
    ]
    if len(decision_steps) != 1 or len(decision_steps) > MAX_DECISION_STEPS:
        raise ContractValidationError("decision_fanout_not_permitted")
    step = decision_steps[0]
    if step.invocation.invocation_id != binding.invocation_id:
        raise ContractValidationError("invocation_not_in_plan")
    materials = _collect_materials(
        binding,
        typed_aircraft_observations=typed_aircraft_observations,
        callsign_observations=callsign_observations,
        discoveries=discoveries,
        entity_hints=entity_hints,
        region_text=step.region_text,
    )
    return MasterDecisionExecutionReadiness(
        binding=binding,
        materials=materials,
        population_observation_aircraft_ids=population_ids,
        earlier_result_recommendation_ids=earlier_ids,
        temporal_scopes=step.temporal_scopes,
        untrusted_region_text=step.region_text,
        gates=binding.gates,
        identity_ambiguous=_identity_ambiguous(materials, population_ids, earlier_ids),
        persisted_recommendation_ambiguous=_persisted_recommendation_ambiguous(
            materials,
            earlier_ids,
        ),
        decision_invocation_count=1,
        needs_persisted_evidence=binding.needs_persisted_evidence,
    )
