"""Deterministic Decision target checks over injected reads.

The seven identities stay separate:

- a user-supplied aircraft identifier
- a callsign observation
- a model-proposed target
- a Master comparison candidate
- a Live Ops discovery candidate
- a persisted recommendation candidate
- a current, independently validated, authorized target

Injected reads are test doubles. They can show that an offered identity is
unique, shared, wrong, stale, or out of scope. They cannot establish source
authenticity, and a caller-built object that looks like a proof cannot
satisfy source currentness, allowed-target population, or separate
persisted-recommendation authorization. Those proofs have to be issued later
by the trusted application execution host. This module has no issuer.

No result is a trusted target, a ``DecisionSpecialistRequest``, or a
dispatch. ``MAX_DECISION_STEPS`` and the persisted-fanout cap stay in force.
A missing risk row is not ``LOW`` risk.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from wilvor_ai.contracts import (
    PROVENANCE_TOKEN_MAX_LENGTH,
    ContractValidationError,
)
from wilvor_ai.decision_contracts import RiskPresence, StoredRiskLevel
from wilvor_ai.master_contracts import MasterProductionAuthority
from wilvor_ai.master_decision_execution_readiness import (
    DecisionTargetMaterial,
    DecisionTargetMaterialKind,
    MasterDecisionExecutionReadiness,
)
from wilvor_ai.master_evidence_admission import MasterSourceAuthenticity
from wilvor_ai.master_routing import (
    MAX_DECISION_STEPS,
    MAX_UNTRUSTED_POPULATION_HINTS,
    MasterRequestCoverage,
    MasterRouteExecutionAuthority,
)


MASTER_DECISION_TARGET_RESOLVER_SCHEMA_VERSION = (
    "wilvor.ai.master_decision_target_resolver.v1"
)

MASTER_DECISION_TARGET_RESOLVER_EXPLANATION = (
    "Injected reads can reject a Decision target or observe one consistent "
    "aircraft id. They are not authenticated source evidence. Source "
    "currentness, an allowed target population, and separate "
    "persisted-recommendation authorization remain unmet until the trusted "
    "application execution host issues them. A caller-created proof is not "
    "that issuance. No trusted target or runnable Decision request is "
    "produced. Absent risk data is not LOW risk."
)

# These match the native Decision specialist limits. This module does not
# import that runtime. Contract tests lock the integers to it.
MAX_PERSISTED_RECOMMENDATION_FANOUT = 3
MAX_DECISION_TOOL_CALLS = 4
DECISION_PERSISTED_TOOL = "get_persisted_airport_candidate_evidence"
DECISION_CURRENT_TOOLS = frozenset(
    {
        "get_current_decision_context",
        "get_current_risk_evidence",
        "get_current_recommendation",
    }
)
DECISION_TOOL_NAMES = DECISION_CURRENT_TOOLS | {DECISION_PERSISTED_TOOL}
HOST_PROOF_ISSUER = "unavailable"
_NATIVE_MODES = frozenset({"AIRCRAFT", "DIRECT_PERSISTED"})
_AIRCRAFT_ID_FIELDS = frozenset(
    {
        "aircraft_id",
        "typed_aircraft_id",
        "population_aircraft_id",
        "entity_id",
    }
)
_SELECTOR_FIELDS = _AIRCRAFT_ID_FIELDS | {"callsign", "recommendation_id", "region_text"}
_LIVE_OPS_SOURCES = frozenset({"LiveOpsDiscovery", "LiveOpsEntityHint"})
_CALLSIGN_AIRCRAFT_SOURCE = "MasterCallsignObservation"


class DecisionCandidateRole(str, Enum):
    """Where an offered selector came from. Not an authorization."""

    USER_SUPPLIED_AIRCRAFT_IDENTIFIER = "USER_SUPPLIED_AIRCRAFT_IDENTIFIER"
    CALLSIGN_OBSERVATION = "CALLSIGN_OBSERVATION"
    MODEL_PROPOSED_TARGET = "MODEL_PROPOSED_TARGET"
    MASTER_COMPARISON_CANDIDATE = "MASTER_COMPARISON_CANDIDATE"
    LIVE_OPS_DISCOVERY_CANDIDATE = "LIVE_OPS_DISCOVERY_CANDIDATE"
    PERSISTED_RECOMMENDATION_CANDIDATE = "PERSISTED_RECOMMENDATION_CANDIDATE"
    CURRENT_INDEPENDENTLY_VALIDATED_AUTHORIZED_TARGET = (
        "CURRENT_INDEPENDENTLY_VALIDATED_AUTHORIZED_TARGET"
    )


class DecisionReadOrigin(str, Enum):
    """The only read origin this boundary accepts."""

    INJECTED_FAKE = "INJECTED_FAKE"


class HostProofRequirement(str, Enum):
    """Proofs the execution host must issue. This module cannot."""

    SOURCE_CURRENTNESS = "SOURCE_CURRENTNESS"
    ALLOWED_TARGET_POPULATION = "ALLOWED_TARGET_POPULATION"
    SEPARATE_PERSISTED_RECOMMENDATION_AUTHORIZATION = (
        "SEPARATE_PERSISTED_RECOMMENDATION_AUTHORIZATION"
    )


REQUIRED_HOST_PROOFS = tuple(HostProofRequirement)


class DecisionResolutionStatus(str, Enum):
    """Identity observation only. Neither status dispatches."""

    UNIQUE_CANDIDATE_NOT_ELIGIBLE = "UNIQUE_CANDIDATE_NOT_ELIGIBLE"
    REJECTED = "REJECTED"


class DecisionResolutionDefect(str, Enum):
    """Why an offered identity cannot be an execution target."""

    NO_UNIQUELY_IDENTIFIED_CANDIDATE = "NO_UNIQUELY_IDENTIFIED_CANDIDATE"
    AMBIGUOUS_CALLSIGN = "AMBIGUOUS_CALLSIGN"
    AMBIGUOUS_IDENTITY = "AMBIGUOUS_IDENTITY"
    AIRCRAFT_ID_MISMATCH = "AIRCRAFT_ID_MISMATCH"
    UNTRUSTED_ROUTE_HINT = "UNTRUSTED_ROUTE_HINT"
    COMPARISON_CANDIDATE_NOT_A_TARGET = "COMPARISON_CANDIDATE_NOT_A_TARGET"
    FOREIGN_LIVE_OPS_DISCOVERY = "FOREIGN_LIVE_OPS_DISCOVERY"
    STALE_AIRCRAFT_STATE = "STALE_AIRCRAFT_STATE"
    OUT_OF_SCOPE_AIRCRAFT = "OUT_OF_SCOPE_AIRCRAFT"
    CONFLICTING_RECOMMENDATION_ID = "CONFLICTING_RECOMMENDATION_ID"
    PERSISTED_RECOMMENDATION_NOT_SEPARATELY_AUTHORIZED = (
        "PERSISTED_RECOMMENDATION_NOT_SEPARATELY_AUTHORIZED"
    )
    MISSING_RISK_RECORD = "MISSING_RISK_RECORD"
    UNSUPPORTED_FANOUT = "UNSUPPORTED_FANOUT"
    EXCESSIVE_FANOUT = "EXCESSIVE_FANOUT"
    NATIVE_REQUEST_MODE_MISMATCH = "NATIVE_REQUEST_MODE_MISMATCH"
    CALLER_CONTROLLED_TARGET = "CALLER_CONTROLLED_TARGET"
    CALLER_PROOF_NOT_HOST_ISSUED = "CALLER_PROOF_NOT_HOST_ISSUED"


class RiskRecordObservation(str, Enum):
    """What an injected read said about risk. Not a verified risk fact."""

    MISSING = "MISSING"
    PRESENT = "PRESENT"
    NOT_CONSULTED = "NOT_CONSULTED"


_DEFECT_ORDER = {item: index for index, item in enumerate(DecisionResolutionDefect)}
_IDENTITY_DEFECTS = frozenset(
    {
        DecisionResolutionDefect.AMBIGUOUS_CALLSIGN,
        DecisionResolutionDefect.AMBIGUOUS_IDENTITY,
        DecisionResolutionDefect.AIRCRAFT_ID_MISMATCH,
        DecisionResolutionDefect.UNTRUSTED_ROUTE_HINT,
        DecisionResolutionDefect.COMPARISON_CANDIDATE_NOT_A_TARGET,
        DecisionResolutionDefect.FOREIGN_LIVE_OPS_DISCOVERY,
        DecisionResolutionDefect.OUT_OF_SCOPE_AIRCRAFT,
        DecisionResolutionDefect.CONFLICTING_RECOMMENDATION_ID,
    }
)


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


def _flag(value: object, field_name: str) -> bool:
    if type(value) is not bool:
        raise ContractValidationError(f"invalid_{field_name}")
    return value


def _ordered_defects(
    found: list[DecisionResolutionDefect],
) -> tuple[DecisionResolutionDefect, ...]:
    if any(not isinstance(item, DecisionResolutionDefect) for item in found):
        raise ContractValidationError("invalid_decision_resolution_defect")
    return tuple(sorted(set(found), key=_DEFECT_ORDER.__getitem__))


@dataclass(frozen=True)
class FakeRiskRecord:
    """Injected risk row. Absence has no level, including no ``LOW`` level."""

    presence: RiskPresence
    level: StoredRiskLevel | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.presence, RiskPresence):
            raise ContractValidationError("invalid_risk_presence")
        if self.presence is RiskPresence.ABSENT:
            if self.level is not None:
                raise ContractValidationError("absent_risk_is_not_low_risk")
            return
        if not isinstance(self.level, StoredRiskLevel):
            raise ContractValidationError("invalid_risk_level")


@dataclass(frozen=True)
class FakeAircraftRead:
    """One injected aircraft row.

    ``state_fresh``, ``population_member``, and ``in_request_scope`` can
    only reject a row. ``True`` is not a host currentness proof, an allowed
    population proof, or authenticated evidence.
    """

    aircraft_id: str
    callsign: str | None
    state_fresh: bool
    population_member: bool
    in_request_scope: bool
    risk: FakeRiskRecord | None = None
    recommendation_ids: tuple[str, ...] = ()
    origin: DecisionReadOrigin = DecisionReadOrigin.INJECTED_FAKE
    authenticated_source: bool = False

    def __post_init__(self) -> None:
        _token(self.aircraft_id, "aircraft_id")
        if self.callsign is not None:
            _token(self.callsign, "callsign")
        _flag(self.state_fresh, "state_fresh")
        _flag(self.population_member, "population_member")
        _flag(self.in_request_scope, "in_request_scope")
        if self.risk is not None and not isinstance(self.risk, FakeRiskRecord):
            raise ContractValidationError("invalid_risk_record")
        if not isinstance(self.recommendation_ids, tuple):
            raise ContractValidationError("invalid_recommendation_id")
        if len(self.recommendation_ids) > MAX_UNTRUSTED_POPULATION_HINTS:
            raise ContractValidationError("population_aircraft_ids_exceed_bound")
        seen: set[str] = set()
        for item in self.recommendation_ids:
            parsed = _token(item, "recommendation_id")
            if parsed in seen:
                raise ContractValidationError("duplicate_recommendation_id")
            seen.add(parsed)
        _closed(self.origin, DecisionReadOrigin.INJECTED_FAKE, "fake_read_not_authenticated_evidence")
        _closed(
            self.authenticated_source,
            False,
            "fake_read_not_authenticated_evidence",
        )


@dataclass(frozen=True)
class FakeDecisionReadCatalog:
    """Injected read set. Membership does not authenticate the rows."""

    aircraft: tuple[FakeAircraftRead, ...] = ()
    origin: DecisionReadOrigin = DecisionReadOrigin.INJECTED_FAKE
    authenticated_source: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.aircraft, tuple) or any(
            not isinstance(item, FakeAircraftRead) for item in self.aircraft
        ):
            raise ContractValidationError("invalid_decision_read")
        if len(self.aircraft) > MAX_UNTRUSTED_POPULATION_HINTS:
            raise ContractValidationError("population_aircraft_ids_exceed_bound")
        seen = [item.aircraft_id for item in self.aircraft]
        if len(seen) != len(set(seen)):
            raise ContractValidationError("duplicate_aircraft_id")
        _closed(self.origin, DecisionReadOrigin.INJECTED_FAKE, "fake_read_not_authenticated_evidence")
        _closed(
            self.authenticated_source,
            False,
            "fake_read_not_authenticated_evidence",
        )


@dataclass(frozen=True)
class ExecutionHostProof:
    """Shape of a future host proof. Constructing it does not issue one."""

    requirement: HostProofRequirement
    subject_id: str
    satisfied: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.requirement, HostProofRequirement):
            raise ContractValidationError("invalid_host_proof")
        _token(self.subject_id, "subject_id")
        _closed(self.satisfied, False, "caller_proof_not_host_issued")


@dataclass(frozen=True)
class ClassifiedDecisionCandidate:
    """One offered selector. Classification cannot authorize it."""

    role: DecisionCandidateRole
    source: str
    field_name: str
    identifier: str
    authorizes_execution: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.role, DecisionCandidateRole):
            raise ContractValidationError("invalid_decision_candidate")
        if (
            self.role
            is DecisionCandidateRole.CURRENT_INDEPENDENTLY_VALIDATED_AUTHORIZED_TARGET
        ):
            raise ContractValidationError("target_authorization_not_granted")
        if (
            not isinstance(self.source, str)
            or not self.source
            or self.source != self.source.strip()
        ):
            raise ContractValidationError("invalid_decision_candidate")
        if self.field_name not in _SELECTOR_FIELDS:
            raise ContractValidationError("invalid_decision_candidate")
        if self.field_name == "region_text":
            if (
                not isinstance(self.identifier, str)
                or not self.identifier
                or self.identifier != self.identifier.strip()
                or len(self.identifier) > PROVENANCE_TOKEN_MAX_LENGTH
            ):
                raise ContractValidationError("invalid_region_text")
        else:
            _token(self.identifier, self.field_name)
        _closed(self.authorizes_execution, False, "target_authorization_not_granted")


@dataclass(frozen=True)
class MasterDecisionTargetResolution:
    """A blocked target check. It cannot be executed or dispatched."""

    readiness: MasterDecisionExecutionReadiness
    candidates: tuple[ClassifiedDecisionCandidate, ...]
    status: DecisionResolutionStatus
    defects: tuple[DecisionResolutionDefect, ...]
    observed_aircraft_id: str | None
    risk_observation: RiskRecordObservation
    observed_risk_level: StoredRiskLevel | None
    injected_risk_present: bool
    schema_version: str = MASTER_DECISION_TARGET_RESOLVER_SCHEMA_VERSION
    explanation: str = MASTER_DECISION_TARGET_RESOLVER_EXPLANATION
    read_origin: DecisionReadOrigin = DecisionReadOrigin.INJECTED_FAKE
    observed_recommendation_ids: tuple[str, ...] = ()
    host_proofs_required: tuple[HostProofRequirement, ...] = REQUIRED_HOST_PROOFS
    host_proofs_satisfied: tuple[HostProofRequirement, ...] = ()
    host_proof_issuer: str = HOST_PROOF_ISSUER
    caller_proof_accepted: bool = False
    current_authorized_target_available: bool = False
    execution_eligible: bool = False
    trusted_target_issued: bool = False
    decision_request: None = None
    native_request_built: bool = False
    dispatch_permitted: bool = False
    executable: bool = False
    ready_to_execute: bool = False
    target_authorization_granted: bool = False
    authenticated_evidence: bool = False
    authenticated_risk_evidence: bool = False
    absent_risk_is_low_risk: bool = False
    source_authenticity: MasterSourceAuthenticity = MasterSourceAuthenticity.NOT_ESTABLISHED
    execution_authority: MasterRouteExecutionAuthority = MasterRouteExecutionAuthority.DENIED
    production_authority: MasterProductionAuthority = MasterProductionAuthority.DENIED
    request_coverage: MasterRequestCoverage = MasterRequestCoverage.NOT_ESTABLISHED
    authorization_token: str | None = None
    max_decision_steps: int = MAX_DECISION_STEPS
    decision_invocation_count: int = 1
    max_persisted_recommendation_fanout: int = MAX_PERSISTED_RECOMMENDATION_FANOUT
    max_decision_tool_calls: int = MAX_DECISION_TOOL_CALLS
    persisted_fanout_authorized: bool = False
    persisted_fanout_count: int = 0

    def __post_init__(self) -> None:
        if not isinstance(self.readiness, MasterDecisionExecutionReadiness):
            raise ContractValidationError("invalid_decision_readiness")
        _closed(self.readiness.dispatch_permitted, False, "execution_not_granted")
        _closed(self.readiness.recorded_risk_level, None, "absent_risk_is_not_low_risk")
        if not isinstance(self.candidates, tuple) or any(
            not isinstance(item, ClassifiedDecisionCandidate) for item in self.candidates
        ):
            raise ContractValidationError("invalid_decision_candidate")
        if any(
            item.role
            is DecisionCandidateRole.CURRENT_INDEPENDENTLY_VALIDATED_AUTHORIZED_TARGET
            for item in self.candidates
        ):
            raise ContractValidationError("target_authorization_not_granted")
        if not isinstance(self.status, DecisionResolutionStatus):
            raise ContractValidationError("invalid_decision_resolution")
        if self.defects != _ordered_defects(list(self.defects)):
            raise ContractValidationError("invalid_decision_resolution_defect")
        if self.status is DecisionResolutionStatus.UNIQUE_CANDIDATE_NOT_ELIGIBLE:
            if self.defects or self.observed_aircraft_id is None:
                raise ContractValidationError("invalid_decision_resolution")
            _token(self.observed_aircraft_id, "aircraft_id")
            _closed(
                self.risk_observation,
                RiskRecordObservation.PRESENT,
                "invalid_risk_record",
            )
        elif self.status is DecisionResolutionStatus.REJECTED:
            _closed(self.observed_aircraft_id, None, "target_authorization_not_granted")
        else:
            raise ContractValidationError("invalid_decision_resolution")
        if not isinstance(self.risk_observation, RiskRecordObservation):
            raise ContractValidationError("invalid_risk_record")
        if self.risk_observation is RiskRecordObservation.PRESENT:
            if not isinstance(self.observed_risk_level, StoredRiskLevel):
                raise ContractValidationError("invalid_risk_level")
            _closed(self.injected_risk_present, True, "invalid_risk_record")
        else:
            _closed(self.observed_risk_level, None, "absent_risk_is_not_low_risk")
            _closed(self.injected_risk_present, False, "invalid_risk_record")
        _closed(self.schema_version, MASTER_DECISION_TARGET_RESOLVER_SCHEMA_VERSION, "invalid_schema_version")
        _closed(self.explanation, MASTER_DECISION_TARGET_RESOLVER_EXPLANATION, "invalid_decision_resolution")
        _closed(self.read_origin, DecisionReadOrigin.INJECTED_FAKE, "fake_read_not_authenticated_evidence")
        if self.observed_recommendation_ids != ():
            raise ContractValidationError("persisted_evidence_not_authorized")
        if self.host_proofs_required != REQUIRED_HOST_PROOFS or self.host_proofs_satisfied != ():
            raise ContractValidationError("caller_proof_not_host_issued")
        _closed(self.host_proof_issuer, HOST_PROOF_ISSUER, "caller_proof_not_host_issued")
        _closed(self.caller_proof_accepted, False, "caller_proof_not_host_issued")
        _closed(self.current_authorized_target_available, False, "target_authorization_not_granted")
        _closed(self.execution_eligible, False, "execution_not_granted")
        _closed(self.trusted_target_issued, False, "target_authorization_not_granted")
        _closed(self.decision_request, None, "execution_not_granted")
        _closed(self.native_request_built, False, "execution_not_granted")
        _closed(self.dispatch_permitted, False, "execution_not_granted")
        _closed(self.executable, False, "execution_not_granted")
        _closed(self.ready_to_execute, False, "decision_step_not_executable")
        _closed(self.target_authorization_granted, False, "target_authorization_not_granted")
        _closed(self.authenticated_evidence, False, "fake_read_not_authenticated_evidence")
        _closed(self.authenticated_risk_evidence, False, "fake_read_not_authenticated_evidence")
        _closed(self.absent_risk_is_low_risk, False, "absent_risk_is_not_low_risk")
        _closed(
            self.source_authenticity,
            MasterSourceAuthenticity.NOT_ESTABLISHED,
            "source_authenticity_not_established",
        )
        _closed(
            self.execution_authority,
            MasterRouteExecutionAuthority.DENIED,
            "execution_authority_not_granted",
        )
        _closed(
            self.production_authority,
            MasterProductionAuthority.DENIED,
            "production_authority_not_issued",
        )
        _closed(
            self.request_coverage,
            MasterRequestCoverage.NOT_ESTABLISHED,
            "request_coverage_not_established",
        )
        _closed(self.authorization_token, None, "authorization_token_not_accepted")
        _closed(self.max_decision_steps, MAX_DECISION_STEPS, "decision_fanout_not_permitted")
        _closed(self.decision_invocation_count, 1, "decision_fanout_not_permitted")
        _closed(
            self.max_persisted_recommendation_fanout,
            MAX_PERSISTED_RECOMMENDATION_FANOUT,
            "decision_fanout_not_permitted",
        )
        _closed(self.max_decision_tool_calls, MAX_DECISION_TOOL_CALLS, "decision_fanout_not_permitted")
        _closed(self.persisted_fanout_authorized, False, "persisted_evidence_not_authorized")
        _closed(self.persisted_fanout_count, 0, "persisted_evidence_not_authorized")


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
    currentness_satisfied: object,
    allowed_target_population_satisfied: object,
    persisted_recommendation_authorized: object,
    execution_eligible: object,
    dispatch_permitted: object,
    executable: object,
    ready_to_execute: object,
    trusted_target_issued: object,
    decision_request: object,
    source_authenticity: object,
    execution_authority: object,
    production_authority: object,
    request_coverage: object,
    authorization_token: object,
    authenticated_evidence: object,
    native_request_built: object,
) -> None:
    """Reject caller attempts to grant eligibility or authenticity."""

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
    _closed(
        capability_authorizes_dispatch,
        False,
        "capability_consumption_not_dispatch_permission",
    )
    _closed(principal_authenticated, False, "principal_not_authenticated")
    _closed(caller_evidence_satisfies_gate, False, "caller_evidence_cannot_satisfy_gate")
    _closed(currentness_satisfied, False, "caller_proof_not_host_issued")
    _closed(allowed_target_population_satisfied, False, "caller_proof_not_host_issued")
    _closed(
        persisted_recommendation_authorized,
        False,
        "persisted_evidence_not_authorized",
    )
    _closed(execution_eligible, False, "execution_not_granted")
    _closed(dispatch_permitted, False, "execution_not_granted")
    _closed(executable, False, "execution_not_granted")
    _closed(ready_to_execute, False, "decision_step_not_executable")
    _closed(trusted_target_issued, False, "target_authorization_not_granted")
    _closed(decision_request, None, "execution_not_granted")
    _closed(
        source_authenticity,
        MasterSourceAuthenticity.NOT_ESTABLISHED,
        "source_authenticity_not_established",
    )
    _closed(
        execution_authority,
        MasterRouteExecutionAuthority.DENIED,
        "execution_authority_not_granted",
    )
    _closed(
        production_authority,
        MasterProductionAuthority.DENIED,
        "production_authority_not_issued",
    )
    _closed(
        request_coverage,
        MasterRequestCoverage.NOT_ESTABLISHED,
        "request_coverage_not_established",
    )
    _closed(authorization_token, None, "authorization_token_not_accepted")
    _closed(authenticated_evidence, False, "fake_read_not_authenticated_evidence")
    _closed(native_request_built, False, "execution_not_granted")


def _role(material: DecisionTargetMaterial) -> DecisionCandidateRole:
    kind = material.kind
    if kind is DecisionTargetMaterialKind.USER_PROVIDED_AIRCRAFT_IDENTIFIER:
        return DecisionCandidateRole.USER_SUPPLIED_AIRCRAFT_IDENTIFIER
    if kind is DecisionTargetMaterialKind.CALLSIGN:
        return DecisionCandidateRole.CALLSIGN_OBSERVATION
    if kind is DecisionTargetMaterialKind.MODEL_ROUTE_HINT:
        return DecisionCandidateRole.MODEL_PROPOSED_TARGET
    if kind is DecisionTargetMaterialKind.MASTER_COMPARISON_CANDIDATE:
        if material.field_name == "recommendation_id":
            return DecisionCandidateRole.PERSISTED_RECOMMENDATION_CANDIDATE
        return DecisionCandidateRole.MASTER_COMPARISON_CANDIDATE
    if kind is DecisionTargetMaterialKind.LIVE_OPS_DISCOVERY_CANDIDATE:
        return DecisionCandidateRole.LIVE_OPS_DISCOVERY_CANDIDATE
    raise ContractValidationError("invalid_decision_target_material")


def _classify(
    readiness: MasterDecisionExecutionReadiness,
) -> tuple[ClassifiedDecisionCandidate, ...]:
    found: list[ClassifiedDecisionCandidate] = []
    for item in readiness.materials:
        if not item.available or item.identifier is None:
            continue
        role = _role(item)
        found.append(
            ClassifiedDecisionCandidate(
                role=role,
                source=item.source,
                field_name=item.field_name,
                identifier=item.identifier,
            )
        )
        if (
            item.field_name == "recommendation_id"
            and role is not DecisionCandidateRole.PERSISTED_RECOMMENDATION_CANDIDATE
        ):
            found.append(
                ClassifiedDecisionCandidate(
                    role=DecisionCandidateRole.PERSISTED_RECOMMENDATION_CANDIDATE,
                    source=item.source,
                    field_name=item.field_name,
                    identifier=item.identifier,
                )
            )
    for recommendation_id in readiness.earlier_result_recommendation_ids:
        found.append(
            ClassifiedDecisionCandidate(
                role=DecisionCandidateRole.PERSISTED_RECOMMENDATION_CANDIDATE,
                source="earlier_result",
                field_name="recommendation_id",
                identifier=recommendation_id,
            )
        )
    return tuple(found)


def _ids(
    candidates: tuple[ClassifiedDecisionCandidate, ...],
    role: DecisionCandidateRole,
    fields: frozenset[str],
    source: str | None = None,
    sources: frozenset[str] | None = None,
) -> set[str]:
    found: set[str] = set()
    for item in candidates:
        if item.role is not role or item.field_name not in fields:
            continue
        if source is not None and item.source != source:
            continue
        if sources is not None and item.source not in sources:
            continue
        found.add(item.identifier)
    return found


def _native_mode_name(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    enumerated = getattr(value, "value", None)
    if isinstance(enumerated, str):
        return enumerated
    return ""


def _optional_selector(value: object, field_name: str) -> str | None:
    if value is None:
        return None
    try:
        return _token(value, field_name)
    except ContractValidationError:
        return ""


def _fanout_ids(value: object) -> tuple[str, ...] | None:
    if value is None:
        return None
    if not isinstance(value, tuple):
        raise ContractValidationError("invalid_fanout_ids")
    if len(value) > MAX_UNTRUSTED_POPULATION_HINTS:
        raise ContractValidationError("population_aircraft_ids_exceed_bound")
    found: list[str] = []
    seen: set[str] = set()
    for item in value:
        parsed = _token(item, "recommendation_id")
        if parsed in seen:
            raise ContractValidationError("duplicate_recommendation_id")
        seen.add(parsed)
        found.append(parsed)
    return tuple(found)


def _tool_names(value: object) -> tuple[str, ...] | None:
    if value is None:
        return None
    if not isinstance(value, tuple):
        raise ContractValidationError("invalid_tool_names")
    if len(value) > MAX_UNTRUSTED_POPULATION_HINTS:
        raise ContractValidationError("population_aircraft_ids_exceed_bound")
    found: list[str] = []
    for item in value:
        found.append(_token(item, "tool_name"))
    return tuple(found)


def _check_native_mode(
    defects: list[DecisionResolutionDefect],
    *,
    native_mode: object,
    native_aircraft_id: object,
    native_recommendation_id: object,
    user_ids: set[str],
    offered_recommendations: set[str],
    proposed_tools: tuple[str, ...] | None,
) -> None:
    mode_name = _native_mode_name(native_mode)
    aircraft_id = _optional_selector(native_aircraft_id, "aircraft_id")
    recommendation_id = _optional_selector(native_recommendation_id, "recommendation_id")
    if mode_name is None and aircraft_id is None and recommendation_id is None:
        return
    mismatched = mode_name not in _NATIVE_MODES
    if mode_name == "AIRCRAFT":
        if aircraft_id in {None, ""} or recommendation_id is not None:
            mismatched = True
        elif user_ids and aircraft_id not in user_ids:
            mismatched = True
        elif not user_ids:
            mismatched = True
    elif mode_name == "DIRECT_PERSISTED":
        if recommendation_id in {None, ""} or aircraft_id is not None:
            mismatched = True
        elif user_ids:
            mismatched = True
        elif offered_recommendations and recommendation_id not in offered_recommendations:
            mismatched = True
        if proposed_tools and any(name in DECISION_CURRENT_TOOLS for name in proposed_tools):
            mismatched = True
    if mismatched:
        defects.append(DecisionResolutionDefect.NATIVE_REQUEST_MODE_MISMATCH)


def resolve_master_decision_target(
    readiness: MasterDecisionExecutionReadiness,
    reads: FakeDecisionReadCatalog,
    *,
    host_proofs: object = None,
    caller_target: object = None,
    dispatch_requested: bool = False,
    native_mode: object = None,
    native_aircraft_id: object = None,
    native_recommendation_id: object = None,
    proposed_fanout_ids: object = None,
    proposed_tool_names: object = None,
    requested_decision_invocations: int = 1,
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
    currentness_satisfied: bool = False,
    allowed_target_population_satisfied: bool = False,
    persisted_recommendation_authorized: bool = False,
    execution_eligible: bool = False,
    dispatch_permitted: bool = False,
    executable: bool = False,
    ready_to_execute: bool = False,
    trusted_target_issued: bool = False,
    decision_request: object = None,
    source_authenticity: object = MasterSourceAuthenticity.NOT_ESTABLISHED,
    execution_authority: object = MasterRouteExecutionAuthority.DENIED,
    production_authority: object = MasterProductionAuthority.DENIED,
    request_coverage: object = MasterRequestCoverage.NOT_ESTABLISHED,
    authorization_token: object = None,
    authenticated_evidence: bool = False,
    native_request_built: bool = False,
) -> MasterDecisionTargetResolution:
    """Check offered Decision identities against injected reads.

    A single consistent aircraft id is recorded as an observation. It is
    not eligible for execution. Caller-supplied proofs, targets, and fake
    rows are not the trusted execution host.
    """

    if not isinstance(readiness, MasterDecisionExecutionReadiness):
        raise ContractValidationError("invalid_decision_readiness")
    if not isinstance(reads, FakeDecisionReadCatalog):
        raise ContractValidationError("invalid_decision_read")
    if type(dispatch_requested) is not bool:
        raise ContractValidationError("invalid_dispatch_requested")
    if type(requested_decision_invocations) is not int:
        raise ContractValidationError("invalid_decision_invocation_count")
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
        currentness_satisfied=currentness_satisfied,
        allowed_target_population_satisfied=allowed_target_population_satisfied,
        persisted_recommendation_authorized=persisted_recommendation_authorized,
        execution_eligible=execution_eligible,
        dispatch_permitted=dispatch_permitted,
        executable=executable,
        ready_to_execute=ready_to_execute,
        trusted_target_issued=trusted_target_issued,
        decision_request=decision_request,
        source_authenticity=source_authenticity,
        execution_authority=execution_authority,
        production_authority=production_authority,
        request_coverage=request_coverage,
        authorization_token=authorization_token,
        authenticated_evidence=authenticated_evidence,
        native_request_built=native_request_built,
    )
    fanout_ids = _fanout_ids(proposed_fanout_ids)
    tool_names = _tool_names(proposed_tool_names)
    candidates = _classify(readiness)
    defects: list[DecisionResolutionDefect] = []
    user_ids = _ids(
        candidates,
        DecisionCandidateRole.USER_SUPPLIED_AIRCRAFT_IDENTIFIER,
        frozenset({"aircraft_id"}),
    )
    callsign_aircraft = _ids(
        candidates,
        DecisionCandidateRole.LIVE_OPS_DISCOVERY_CANDIDATE,
        _AIRCRAFT_ID_FIELDS,
        source=_CALLSIGN_AIRCRAFT_SOURCE,
    )
    discovery_aircraft = _ids(
        candidates,
        DecisionCandidateRole.LIVE_OPS_DISCOVERY_CANDIDATE,
        _AIRCRAFT_ID_FIELDS,
        sources=_LIVE_OPS_SOURCES,
    )
    hint_aircraft = _ids(
        candidates,
        DecisionCandidateRole.MODEL_PROPOSED_TARGET,
        frozenset({"typed_aircraft_id", "population_aircraft_id"}),
    )
    hint_recommendations = _ids(
        candidates,
        DecisionCandidateRole.MODEL_PROPOSED_TARGET,
        frozenset({"recommendation_id"}),
    )
    hint_callsigns = _ids(
        candidates,
        DecisionCandidateRole.MODEL_PROPOSED_TARGET,
        frozenset({"callsign"}),
    )
    comparison_aircraft = _ids(
        candidates,
        DecisionCandidateRole.MASTER_COMPARISON_CANDIDATE,
        frozenset({"aircraft_id"}),
    )
    offered_recommendations = _ids(
        candidates,
        DecisionCandidateRole.PERSISTED_RECOMMENDATION_CANDIDATE,
        frozenset({"recommendation_id"}),
    )
    callsigns: dict[str, set[str]] = {}
    for item in reads.aircraft:
        if item.callsign is not None:
            callsigns.setdefault(item.callsign, set()).add(item.aircraft_id)
    ambiguous_callsign = len(callsign_aircraft) > 1 or any(
        len(aircraft_ids) > 1 for aircraft_ids in callsigns.values()
    )
    if len(discovery_aircraft) > 1:
        ambiguous_callsign = True
    if ambiguous_callsign:
        defects.append(DecisionResolutionDefect.AMBIGUOUS_CALLSIGN)
    if len(discovery_aircraft) == 1 and (
        not user_ids or not discovery_aircraft <= user_ids
    ):
        defects.append(DecisionResolutionDefect.FOREIGN_LIVE_OPS_DISCOVERY)
    if comparison_aircraft and (not user_ids or not comparison_aircraft <= user_ids):
        if user_ids and comparison_aircraft - user_ids:
            defects.append(DecisionResolutionDefect.AIRCRAFT_ID_MISMATCH)
        else:
            defects.append(DecisionResolutionDefect.COMPARISON_CANDIDATE_NOT_A_TARGET)
    hint_disagrees = bool(hint_aircraft or hint_recommendations or hint_callsigns) and (
        not user_ids
        or not hint_aircraft <= user_ids
        or bool(hint_recommendations)
    )
    if hint_disagrees:
        defects.append(DecisionResolutionDefect.UNTRUSTED_ROUTE_HINT)
    if len(offered_recommendations) > 1 or (user_ids and offered_recommendations):
        defects.append(DecisionResolutionDefect.CONFLICTING_RECOMMENDATION_ID)
    matched: FakeAircraftRead | None = None
    if len(user_ids) > 1:
        defects.append(DecisionResolutionDefect.AIRCRAFT_ID_MISMATCH)
    elif len(user_ids) == 1 and not ambiguous_callsign:
        user_id = next(iter(user_ids))
        same = [item for item in reads.aircraft if item.aircraft_id == user_id]
        if len(same) == 1 and len(reads.aircraft) == 1:
            matched = same[0]
        else:
            defects.append(DecisionResolutionDefect.AIRCRAFT_ID_MISMATCH)
    if (
        hint_callsigns
        and matched is not None
        and matched.callsign not in hint_callsigns
        and DecisionResolutionDefect.UNTRUSTED_ROUTE_HINT not in defects
    ):
        defects.append(DecisionResolutionDefect.UNTRUSTED_ROUTE_HINT)
    risk_observation = RiskRecordObservation.NOT_CONSULTED
    observed_level: StoredRiskLevel | None = None
    injected_risk_present = False
    if matched is not None:
        if not matched.state_fresh:
            defects.append(DecisionResolutionDefect.STALE_AIRCRAFT_STATE)
        population_ids = readiness.population_observation_aircraft_ids
        if (
            not matched.in_request_scope
            or not matched.population_member
            or (population_ids and matched.aircraft_id not in population_ids)
        ):
            defects.append(DecisionResolutionDefect.OUT_OF_SCOPE_AIRCRAFT)
        if matched.risk is None or matched.risk.presence is RiskPresence.ABSENT:
            risk_observation = RiskRecordObservation.MISSING
            defects.append(DecisionResolutionDefect.MISSING_RISK_RECORD)
        else:
            risk_observation = RiskRecordObservation.PRESENT
            observed_level = matched.risk.level
            injected_risk_present = True
        if matched.recommendation_ids and offered_recommendations:
            if not offered_recommendations <= set(matched.recommendation_ids):
                defects.append(DecisionResolutionDefect.CONFLICTING_RECOMMENDATION_ID)
    persisted_requested = bool(
        offered_recommendations
        or readiness.needs_persisted_evidence
        or fanout_ids
        or (tool_names and DECISION_PERSISTED_TOOL in tool_names)
        or _native_mode_name(native_mode) == "DIRECT_PERSISTED"
    )
    if fanout_ids is not None and len(fanout_ids) > MAX_PERSISTED_RECOMMENDATION_FANOUT:
        defects.append(DecisionResolutionDefect.EXCESSIVE_FANOUT)
    if tool_names is not None:
        if (
            any(name not in DECISION_TOOL_NAMES for name in tool_names)
            or len(tool_names) != len(set(tool_names))
        ):
            defects.append(DecisionResolutionDefect.UNSUPPORTED_FANOUT)
        if len(tool_names) > MAX_DECISION_TOOL_CALLS:
            defects.append(DecisionResolutionDefect.EXCESSIVE_FANOUT)
    if requested_decision_invocations > MAX_DECISION_STEPS:
        defects.append(DecisionResolutionDefect.EXCESSIVE_FANOUT)
    elif requested_decision_invocations < 1:
        defects.append(DecisionResolutionDefect.UNSUPPORTED_FANOUT)
    _check_native_mode(
        defects,
        native_mode=native_mode,
        native_aircraft_id=native_aircraft_id,
        native_recommendation_id=native_recommendation_id,
        user_ids=user_ids,
        offered_recommendations=offered_recommendations,
        proposed_tools=tool_names,
    )
    if persisted_requested:
        defects.append(
            DecisionResolutionDefect.PERSISTED_RECOMMENDATION_NOT_SEPARATELY_AUTHORIZED
        )
    if caller_target is not None or dispatch_requested:
        defects.append(DecisionResolutionDefect.CALLER_CONTROLLED_TARGET)
    if host_proofs is not None:
        defects.append(DecisionResolutionDefect.CALLER_PROOF_NOT_HOST_ISSUED)
    if readiness.identity_ambiguous and _IDENTITY_DEFECTS.isdisjoint(defects):
        defects.append(DecisionResolutionDefect.AMBIGUOUS_IDENTITY)
    ordered = _ordered_defects(defects)
    if matched is not None and not ordered:
        status = DecisionResolutionStatus.UNIQUE_CANDIDATE_NOT_ELIGIBLE
        observed_aircraft_id: str | None = matched.aircraft_id
    else:
        if not ordered:
            ordered = _ordered_defects(
                [DecisionResolutionDefect.NO_UNIQUELY_IDENTIFIED_CANDIDATE]
            )
        status = DecisionResolutionStatus.REJECTED
        observed_aircraft_id = None
    return MasterDecisionTargetResolution(
        readiness=readiness,
        candidates=candidates,
        status=status,
        defects=ordered,
        observed_aircraft_id=observed_aircraft_id,
        risk_observation=risk_observation,
        observed_risk_level=observed_level,
        injected_risk_present=injected_risk_present,
    )


def release_master_decision_target(
    resolution: MasterDecisionTargetResolution,
    native_request: object = None,
) -> None:
    """Refuse dispatch. A caller-built native request is not run."""

    if not isinstance(resolution, MasterDecisionTargetResolution):
        raise ContractValidationError("invalid_decision_target_resolution")
    if native_request is not None and resolution.decision_request is not None:
        raise ContractValidationError("execution_not_granted")
    raise ContractValidationError("execution_not_granted")
