"""Sprint 3C Agent E join: evidence identity, provenance, admission, and MA5 safety.

This module calls the existing I6, I7, and I8 functions. It does not add a
production authenticity state. ``MasterSourceAuthenticity`` stays
``NOT_ESTABLISHED``. Local and nonproduction receipts stay local. An empty
page is not a verified zero, missing risk is not LOW, and California or
other regional text is not SIGMET polygon overlap.

A receipt is accepted for this join only when its adapter identity matches
the host-owned scoped-read adapter recorded on the consumed attempt and the
I3 consumption identity equals the I5 invocation identity. Matching those
ids is not source authentication.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from wilvor_ai.contracts import (
    PROVENANCE_TOKEN_MAX_LENGTH,
    ContractValidationError,
    Evidence,
    FreshnessStatus,
    ToolResult,
    _validate_bounded_text,
)
from wilvor_ai.live_ops_public_contracts import (
    LiveOpsPublicClaimKind,
    LiveOpsPublicVerificationOutcome,
    LiveOpsRunResult,
    LiveOpsTrustedContext,
)
from wilvor_ai.master_authenticated_read_receipt import (
    SOURCE_AUTHENTICATED_READ_AVAILABLE,
    GeographicLimitation,
    HostIssuedReadReceipt,
    LocalTrustedReadHost,
    PopulationCondition,
    ReadReceiptGap,
    ReadReceiptVerificationStatus,
    VERIFIED_ZERO_ALWAYS_MISSING,
)
from wilvor_ai.master_contracts import (
    MasterBindingDefect,
    MasterInvocationAuthorization,
    MasterProductionAuthority,
    MasterPurpose,
    MasterSpecialist,
)
from wilvor_ai.master_decision_target_resolver import RiskRecordObservation
from wilvor_ai.master_evidence_admission import (
    LiveOpsAdmissionSources,
    MasterAdmissionReason,
    MasterAdmissionStatus,
    MasterEvidenceAdmission,
    MasterEvidenceAdmissionCollection,
    MasterEvidenceAdmissionInput,
    MasterSourceAuthenticity,
    MasterTemporalLimitation,
    admit_master_evidence,
)
from wilvor_ai.master_evidence_corroboration import (
    MasterCorroborationReason,
    MasterEvidenceCorroborationCollection,
    MasterEvidenceCorroborationInput,
    corroborate_master_evidence,
)
from wilvor_ai.master_host_evidence_integration import (
    PRODUCTION_SOURCE_AUTHENTICATOR_INTEGRATED,
    ExpectedObservationClocks,
    HostEvidenceIntegration,
    HostEvidenceLimitation,
    integrate_host_read_evidence,
    open_production_source_authenticator,
)
from wilvor_ai.master_parallel_contracts import (
    MasterParallelContractBundle,
    MasterResponseLimitation,
    MasterResponsePolicyHandoff,
)
from wilvor_ai.master_provenance_trace import AUTHENTICATED_SOURCE_READ_AVAILABLE
from wilvor_ai.master_read_resource_guard import ReadOperation
from wilvor_ai.master_response_policy import evaluate_master_response_policy
from wilvor_ai.master_response_renderer import MasterResponseRender, render_master_response
from wilvor_ai.master_specialist_runtime_contract import admit_evidence
from wilvor_ai.master_trusted_read_executor import (
    HostAuthorityKind,
    ReadExecutionOutcome,
    ReadReceiptCandidate,
)
from wilvor_ai.specialist_runtime_contracts import HistoricalSpecialistRunResult


SPRINT3C_EVIDENCE_RESPONSE_SCHEMA_VERSION = "wilvor.ai.sprint3c.evidence_response.v1"
SPRINT3C_SCOPED_READ_ADAPTER_PREFIX = "nonproduction:"
PRODUCTION_MA4_ADMISSION_AVAILABLE = False
AVIATION_EVIDENCE_ADMISSION_ENABLED = False
SAFETY_OR_DIVERSION_INSTRUCTIONS_ENABLED = False
REGIONAL_TEXT_ESTABLISHES_POLYGON_INTERSECTION = False
ABSENT_RISK_IS_LOW_RISK = False
EMPTY_PAGE_CREATES_VERIFIED_ZERO = False
NUMERICAL_IMPACT_COUNTS_IN_MA5 = False

_ALLOWED_READ_OPERATIONS = frozenset({ReadOperation.GET_ITEM, ReadOperation.QUERY})
_CLOCK_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
_LOW_RISK_LABELS = frozenset({"LOW", "LOW_RISK"})
_REGION_TEXT_MARKERS = frozenset(
    {
        "california",
        "region",
        "census",
        "state",
        "sigmet",
        "polygon",
        "impacted_aircraft",
        "aircraft_count",
    }
)
_FORBIDDEN_RESPONSE_FRAGMENTS = (
    "recommend",
    "divert",
    "diversion",
    "evacuate",
    "descend",
    "climb immediately",
    "low risk",
    "verified zero",
    "impacted aircraft",
    "avoid the area",
    "safety instruction",
)
_REQUIRED_RESPONSE_LIMITATIONS = (
    MasterResponseLimitation.SOURCE_AUTHENTICITY_NOT_ESTABLISHED,
    MasterResponseLimitation.PRODUCTION_AUTHORITY_DENIED,
    MasterResponseLimitation.NO_OPERATIONAL_CLAIM,
    MasterResponseLimitation.EXECUTION_DENIED,
)


class Sprint3cJoinStatus(str, Enum):
    """Join outcome. None of these values authenticates a source."""

    STRUCTURAL_BIND_RECORDED = "STRUCTURAL_BIND_RECORDED"
    IDENTITY_MISMATCH = "IDENTITY_MISMATCH"
    LOCAL_RECEIPT_NOT_PRODUCTION = "LOCAL_RECEIPT_NOT_PRODUCTION"
    NATIVE_CONTRACT_ONLY = "NATIVE_CONTRACT_ONLY"
    NATIVE_BINDING_UNRESOLVED = "NATIVE_BINDING_UNRESOLVED"
    NATIVE_REJECTED = "NATIVE_REJECTED"
    INSUFFICIENT_FOR_RESPONSE = "INSUFFICIENT_FOR_RESPONSE"


class Sprint3cJoinDefect(str, Enum):
    """Why the join stays closed. Matching ids are not authenticity."""

    INVOCATION_IDENTITY_MISMATCH = "INVOCATION_IDENTITY_MISMATCH"
    CONSUMED_ATTEMPT_MISMATCH = "CONSUMED_ATTEMPT_MISMATCH"
    ADAPTER_IDENTITY_MISMATCH = "ADAPTER_IDENTITY_MISMATCH"
    LOCAL_ADAPTER_NOT_SCOPED_READ = "LOCAL_ADAPTER_NOT_SCOPED_READ"
    GRANT_SCOPE_MISMATCH = "GRANT_SCOPE_MISMATCH"
    SCAN_NOT_PERMITTED = "SCAN_NOT_PERMITTED"
    GRANT_NONCE_NOT_PRODUCTION_BIND = "GRANT_NONCE_NOT_PRODUCTION_BIND"
    PRINCIPAL_MISMATCH = "PRINCIPAL_MISMATCH"
    CLOCK_ROLE_COLLISION = "CLOCK_ROLE_COLLISION"
    STALE_SOURCE = "STALE_SOURCE"
    MISSING_RECORD = "MISSING_RECORD"
    PARTIAL_OR_TRUNCATED = "PARTIAL_OR_TRUNCATED"
    CONFLICTING_RECORDS = "CONFLICTING_RECORDS"
    POPULATION_INCOMPLETE = "POPULATION_INCOMPLETE"
    AIRCRAFT_IDENTITY_MISMATCH = "AIRCRAFT_IDENTITY_MISMATCH"
    HAZARD_IDENTITY_MISMATCH = "HAZARD_IDENTITY_MISMATCH"
    REGIONAL_TEXT_NOT_POLYGON = "REGIONAL_TEXT_NOT_POLYGON"
    MISSING_RISK_NOT_LOW = "MISSING_RISK_NOT_LOW"
    EMPTY_PAGE_NOT_VERIFIED_ZERO = "EMPTY_PAGE_NOT_VERIFIED_ZERO"
    LIVE_OPS_VERIFIED_NOT_AUTHENTICATED = "LIVE_OPS_VERIFIED_NOT_AUTHENTICATED"
    HISTORICAL_RUN_NOT_FINAL = "HISTORICAL_RUN_NOT_FINAL"
    DECISION_COMPARISON_NOT_TARGET = "DECISION_COMPARISON_NOT_TARGET"
    DECISION_REQUEST_NOT_ESTABLISHED = "DECISION_REQUEST_NOT_ESTABLISHED"
    SOURCE_VERSION_ABSENT = "SOURCE_VERSION_ABSENT"
    QUERY_ID_ABSENT = "QUERY_ID_ABSENT"
    SOURCE_VERSION_MISMATCH = "SOURCE_VERSION_MISMATCH"
    FORGED_OR_UNTRUSTED_RECEIPT = "FORGED_OR_UNTRUSTED_RECEIPT"
    PRODUCTION_AUTHENTICITY_UNAVAILABLE = "PRODUCTION_AUTHENTICITY_UNAVAILABLE"


class Sprint3cRejectedClaim(str, Enum):
    """Operational claims this join will not emit."""

    PRODUCTION_AUTHENTICATED_SOURCE = "PRODUCTION_AUTHENTICATED_SOURCE"
    VERIFIED_ZERO = "VERIFIED_ZERO"
    LOW_RISK_FROM_ABSENCE = "LOW_RISK_FROM_ABSENCE"
    SIGMET_POLYGON_INTERSECTION = "SIGMET_POLYGON_INTERSECTION"
    IMPACTED_AIRCRAFT_COUNT = "IMPACTED_AIRCRAFT_COUNT"
    OPERATIONAL_RECOMMENDATION = "OPERATIONAL_RECOMMENDATION"
    SAFETY_OR_DIVERSION_INSTRUCTION = "SAFETY_OR_DIVERSION_INSTRUCTION"
    AVIATION_EVIDENCE_ADMISSION = "AVIATION_EVIDENCE_ADMISSION"


def _token(value: object, field_name: str, *, optional: bool = False) -> str | None:
    if value is None:
        if optional:
            return None
        raise ContractValidationError(f"invalid_{field_name}")
    errors = _validate_bounded_text(value, field_name, max_length=PROVENANCE_TOKEN_MAX_LENGTH)
    if errors:
        raise ContractValidationError(errors)
    if not isinstance(value, str) or value != value.strip():
        raise ContractValidationError(f"invalid_{field_name}")
    return value


def _clock(value: object, field_name: str, *, optional: bool = False) -> str | None:
    if value is None:
        if optional:
            return None
        raise ContractValidationError(f"invalid_{field_name}")
    if not isinstance(value, str):
        raise ContractValidationError(f"invalid_{field_name}")
    try:
        datetime.strptime(value, _CLOCK_FORMAT).replace(tzinfo=timezone.utc)
    except ValueError as exc:
        raise ContractValidationError(f"invalid_{field_name}") from exc
    return value


def _count(value: object, field_name: str) -> int:
    if type(value) is not int or value < 0:
        raise ContractValidationError(f"invalid_{field_name}")
    return value


def _ordered(enum_type: type[Enum], selected: set[Enum]) -> tuple[Enum, ...]:
    return tuple(item for item in enum_type if item in selected)


def _attr(subject: object, name: str, default: object = None) -> object:
    return getattr(subject, name, default)


def _mapping_value(data: object, name: str) -> object:
    if isinstance(data, dict):
        return data.get(name)
    return None


def _closed_false(value: object, code: str) -> None:
    if value is not False:
        raise ContractValidationError(code)


def _assert_closed_surface() -> None:
    if list(MasterSourceAuthenticity) != [MasterSourceAuthenticity.NOT_ESTABLISHED]:
        raise ContractValidationError("source_authenticity_enum_broadened")
    if PRODUCTION_MA4_ADMISSION_AVAILABLE is not False:
        raise ContractValidationError("production_ma4_admission_not_available")
    if AVIATION_EVIDENCE_ADMISSION_ENABLED is not False:
        raise ContractValidationError("aviation_evidence_not_admitted")
    if PRODUCTION_SOURCE_AUTHENTICATOR_INTEGRATED is not False:
        raise ContractValidationError("production_source_authenticator_not_integrated")
    if SOURCE_AUTHENTICATED_READ_AVAILABLE is not False:
        raise ContractValidationError("source_authenticated_read_unavailable")
    if AUTHENTICATED_SOURCE_READ_AVAILABLE is not False:
        raise ContractValidationError("source_authenticated_read_unavailable")
    if SAFETY_OR_DIVERSION_INSTRUCTIONS_ENABLED is not False:
        raise ContractValidationError("safety_instructions_not_enabled")
    if REGIONAL_TEXT_ESTABLISHES_POLYGON_INTERSECTION is not False:
        raise ContractValidationError("geospatial_coverage_not_established")
    if ABSENT_RISK_IS_LOW_RISK is not False:
        raise ContractValidationError("absent_risk_is_not_low_risk")
    if EMPTY_PAGE_CREATES_VERIFIED_ZERO is not False:
        raise ContractValidationError("verified_zero_not_established")
    if NUMERICAL_IMPACT_COUNTS_IN_MA5 is not False:
        raise ContractValidationError("impact_count_not_established")


@dataclass(frozen=True)
class Sprint3cConsumedAttemptBinding:
    """Descriptive I3 consumption fields. Not a production grant and not a consume."""

    master_request_id: str
    planning_session_id: str
    step_id: str
    invocation_id: str
    principal_ref: str
    policy_revision: str
    owner_id: str
    attempt_token: str
    fence_epoch: int
    grant_nonce: str
    idempotency_key: str
    payload_fingerprint: str
    ledger_state: str
    resource_id: str
    operation: ReadOperation
    adapter_identity: str
    tool_name: str
    tool_call_id: str
    accepted_at_utc: str
    deadline_utc: str
    specialist: MasterSpecialist
    purpose: MasterPurpose
    specialist_request_id: str | None = None
    correlation_id: str | None = None
    ledger_grant_nonce: str | None = None
    region_scope: tuple[str, ...] = ()
    distributed_exactly_once: bool = False
    production_grant: bool = False

    def __post_init__(self) -> None:
        for name in (
            "master_request_id",
            "planning_session_id",
            "step_id",
            "invocation_id",
            "principal_ref",
            "policy_revision",
            "owner_id",
            "attempt_token",
            "grant_nonce",
            "idempotency_key",
            "payload_fingerprint",
            "ledger_state",
            "resource_id",
            "adapter_identity",
            "tool_name",
            "tool_call_id",
        ):
            _token(getattr(self, name), name)
        _token(self.specialist_request_id, "specialist_request_id", optional=True)
        _token(self.correlation_id, "correlation_id", optional=True)
        _token(self.ledger_grant_nonce, "ledger_grant_nonce", optional=True)
        _count(self.fence_epoch, "fence_epoch")
        _clock(self.accepted_at_utc, "accepted_at_utc")
        _clock(self.deadline_utc, "deadline_utc")
        if not isinstance(self.operation, ReadOperation):
            raise ContractValidationError("invalid_operation")
        if self.operation not in _ALLOWED_READ_OPERATIONS:
            raise ContractValidationError("scan_not_permitted")
        if not isinstance(self.specialist, MasterSpecialist):
            raise ContractValidationError("invalid_specialist")
        if not isinstance(self.purpose, MasterPurpose):
            raise ContractValidationError("invalid_purpose")
        if not isinstance(self.region_scope, tuple):
            raise ContractValidationError("invalid_region_scope")
        for item in self.region_scope:
            _token(item, "region_scope")
        _closed_false(self.distributed_exactly_once, "distributed_exactly_once_not_established")
        _closed_false(self.production_grant, "production_grant_issuance_closed")
        if self.master_request_id == self.invocation_id:
            raise ContractValidationError("request_identity_not_distinct")


@dataclass(frozen=True)
class Sprint3cSourceProvenance:
    """Exact source fields copied from the adapter result. Absence stays absence."""

    source_name: str | None
    source_version: str | None
    query_id: str | None
    source_observation_time_utc: str | None
    evaluation_time_utc: str | None
    requested_time_utc: str | None
    application_request_time_utc: str | None
    execution_started_at_utc: str | None
    execution_finished_at_utc: str | None
    freshness: FreshnessStatus | None
    clocks_distinct: bool
    evaluation_copied_from_request: bool = False
    source_version_is_integrity_proof: bool = False
    query_id_authenticated: bool = False
    freshness_authenticated: bool = False

    def __post_init__(self) -> None:
        _token(self.source_name, "source_name", optional=True)
        _token(self.source_version, "source_version", optional=True)
        _token(self.query_id, "query_id", optional=True)
        for name in (
            "source_observation_time_utc",
            "evaluation_time_utc",
            "requested_time_utc",
            "application_request_time_utc",
            "execution_started_at_utc",
            "execution_finished_at_utc",
        ):
            _clock(getattr(self, name), name, optional=True)
        if self.freshness is not None and not isinstance(self.freshness, FreshnessStatus):
            raise ContractValidationError("invalid_freshness_status")
        if type(self.clocks_distinct) is not bool:
            raise ContractValidationError("invalid_clocks_distinct")
        _closed_false(self.evaluation_copied_from_request, "evaluation_copied_from_request")
        _closed_false(self.source_version_is_integrity_proof, "source_version_not_integrity_proof")
        _closed_false(self.query_id_authenticated, "query_id_not_authenticated")
        _closed_false(self.freshness_authenticated, "freshness_not_authenticated")


@dataclass(frozen=True)
class Sprint3cEvidenceResponseInput:
    """One Master invocation, one consumed attempt, and optional C/D artifacts."""

    authorization: MasterInvocationAuthorization
    consumed_attempt: Sprint3cConsumedAttemptBinding
    read_receipt: object | None = None
    expected_clocks: ExpectedObservationClocks | None = None
    expected_source_version: str | None = None
    expected_aircraft_id: str | None = None
    expected_hazard_id: str | None = None
    expected_step_id: str | None = None
    expected_tool_call_id: str | None = None
    native_live_ops: object | None = None
    live_ops_context: LiveOpsTrustedContext | None = None
    historical_envelope: object | None = None
    decision_resolution: object | None = None
    native_admission_artifact: object | None = None
    historical_run: HistoricalSpecialistRunResult | None = None
    workflow: MasterParallelContractBundle | None = None
    untrusted_claim: object | None = None
    local_host: LocalTrustedReadHost | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.authorization, MasterInvocationAuthorization):
            raise ContractValidationError("invalid_authorization")
        if type(self.consumed_attempt) is not Sprint3cConsumedAttemptBinding:
            raise ContractValidationError("invalid_consumed_attempt")
        if self.expected_clocks is not None and type(self.expected_clocks) is not ExpectedObservationClocks:
            raise ContractValidationError("invalid_observation_clocks")
        if self.live_ops_context is not None and not isinstance(self.live_ops_context, LiveOpsTrustedContext):
            raise ContractValidationError("invalid_live_ops_context")
        if self.historical_run is not None and not isinstance(
            self.historical_run,
            HistoricalSpecialistRunResult,
        ):
            raise ContractValidationError("invalid_historical_run")
        if self.workflow is not None and not isinstance(self.workflow, MasterParallelContractBundle):
            raise ContractValidationError("invalid_parallel_contract")
        if self.local_host is not None and type(self.local_host) is not LocalTrustedReadHost:
            raise ContractValidationError("invalid_read_host")
        _token(self.expected_source_version, "expected_source_version", optional=True)
        _token(self.expected_aircraft_id, "expected_aircraft_id", optional=True)
        _token(self.expected_hazard_id, "expected_hazard_id", optional=True)
        _token(self.expected_step_id, "expected_step_id", optional=True)
        _token(self.expected_tool_call_id, "expected_tool_call_id", optional=True)


@dataclass(frozen=True)
class Sprint3cEvidenceResponseJoin:
    """I6–I8 join for one invocation. Claims stay suppressed."""

    schema_version: str
    status: Sprint3cJoinStatus
    authorization_bound: bool
    consumed_attempt_bound: bool
    scoped_read_adapter_accepted: bool
    defects: tuple[Sprint3cJoinDefect, ...]
    rejected_claims: tuple[Sprint3cRejectedClaim, ...]
    native_limitations: tuple[str, ...]
    provenance: Sprint3cSourceProvenance
    host_evidence: HostEvidenceIntegration | None
    admission: MasterEvidenceAdmission | None
    admissions: MasterEvidenceAdmissionCollection | None
    corroboration: MasterEvidenceCorroborationCollection | None
    response_handoff: MasterResponsePolicyHandoff | None
    response_render: MasterResponseRender | None
    source_authenticity: MasterSourceAuthenticity = MasterSourceAuthenticity.NOT_ESTABLISHED
    production_authority: MasterProductionAuthority = MasterProductionAuthority.DENIED
    claims_admitted: bool = False
    aviation_evidence_admitted: bool = False
    verified_zero_established: bool = False
    polygon_intersection_established: bool = False
    impacted_aircraft_count_established: bool = False
    low_risk_admitted: bool = False
    production_ma4_admission: bool = False
    authenticated_source_read_available: bool = False

    def __post_init__(self) -> None:
        if self.schema_version != SPRINT3C_EVIDENCE_RESPONSE_SCHEMA_VERSION:
            raise ContractValidationError("invalid_schema_version")
        if not isinstance(self.status, Sprint3cJoinStatus):
            raise ContractValidationError("invalid_join_status")
        if not isinstance(self.provenance, Sprint3cSourceProvenance):
            raise ContractValidationError("invalid_source_provenance")
        _ordered_check(self.defects, Sprint3cJoinDefect, "defects")
        _ordered_check(self.rejected_claims, Sprint3cRejectedClaim, "rejected_claims")
        if not isinstance(self.native_limitations, tuple) or any(
            not isinstance(item, str) or item != item.strip() or not item for item in self.native_limitations
        ):
            raise ContractValidationError("invalid_native_limitations")
        if self.source_authenticity is not MasterSourceAuthenticity.NOT_ESTABLISHED:
            raise ContractValidationError("source_authenticity_not_established")
        if self.production_authority is not MasterProductionAuthority.DENIED:
            raise ContractValidationError("production_authority_not_issued")
        closed = (
            self.claims_admitted,
            self.aviation_evidence_admitted,
            self.verified_zero_established,
            self.polygon_intersection_established,
            self.impacted_aircraft_count_established,
            self.low_risk_admitted,
            self.production_ma4_admission,
            self.authenticated_source_read_available,
        )
        if any(item is not False for item in closed):
            raise ContractValidationError("operational_claim_not_admitted")
        if Sprint3cJoinDefect.PRODUCTION_AUTHENTICITY_UNAVAILABLE not in self.defects:
            raise ContractValidationError("source_authenticity_not_established")
        if Sprint3cRejectedClaim.PRODUCTION_AUTHENTICATED_SOURCE not in self.rejected_claims:
            raise ContractValidationError("source_authenticity_not_established")
        if self.admission is not None:
            if self.admission.source_authenticity is not MasterSourceAuthenticity.NOT_ESTABLISHED:
                raise ContractValidationError("source_authenticity_not_established")
            if MasterAdmissionReason.SOURCE_AUTHENTICITY_NOT_ESTABLISHED not in self.admission.reason_codes:
                raise ContractValidationError("source_authenticity_not_established")
        if self.response_render is not None and self.response_render.claims_admitted is not False:
            raise ContractValidationError("operational_claim_not_admitted")
        if self.host_evidence is not None:
            if self.host_evidence.source_authenticity is not MasterSourceAuthenticity.NOT_ESTABLISHED:
                raise ContractValidationError("source_authenticity_not_established")
            if self.host_evidence.aviation_evidence_admitted is not False:
                raise ContractValidationError("aviation_evidence_not_admitted")

    def __bool__(self) -> bool:
        return False


def _ordered_check(value: tuple[Any, ...], enum_type: type[Enum], field_name: str) -> None:
    if not isinstance(value, tuple) or any(not isinstance(item, enum_type) for item in value):
        raise ContractValidationError(f"invalid_{field_name}")
    if value != tuple(item for item in enum_type if item in set(value)):
        raise ContractValidationError(f"{field_name}_order_not_deterministic")


def open_sprint3c_production_evidence_admission() -> None:
    """Production MA4 admission is unavailable until authenticity has a reviewed state."""

    _assert_closed_surface()
    raise ContractValidationError("production_ma4_admission_not_available")


def refuse_sprint3c_runtime_evidence_admission(*args: object, **kwargs: object) -> None:
    """Keep the closed runtime ``admit_evidence`` path raising."""

    admit_evidence(*args, **kwargs)


def bind_scoped_read_to_consumed_attempt(
    attempt: object,
    receipt: object,
    *,
    tool_call_id: str,
    tool_name: str,
    resource_id: str,
    operation: ReadOperation,
    specialist_request_id: str,
    correlation_id: str,
    region_scope: tuple[str, ...],
) -> Sprint3cConsumedAttemptBinding:
    """Bind Agent C's receipt to Agent B's STARTED attempt.

    Grant consumption and the ledger execution state stay different facts.
    The receipt adapter id is the scoped-read id, not the journal origin.
    A shared nonce is copied and is still not production binding.
    """

    from wilvor_ai.master_execution_ledger import MasterExecutionLedgerState
    from wilvor_ai.sprint3c_grant_ledger import DurableConsumedAttempt
    from wilvor_ai.sprint3c_scoped_read import GRANT_CONSUMPTION_LIFECYCLE, LEDGER_EXECUTION_STATE_STARTED

    _assert_closed_surface()
    if type(attempt) is not DurableConsumedAttempt:
        raise ContractValidationError("invalid_consumed_attempt")
    if type(receipt) is not ReadReceiptCandidate:
        raise ContractValidationError("forged_read_receipt")
    if attempt.state is not MasterExecutionLedgerState.STARTED:
        raise ContractValidationError("ledger_execution_state_must_stay_started")
    if attempt.state.value != LEDGER_EXECUTION_STATE_STARTED:
        raise ContractValidationError("ledger_execution_state_must_stay_started")
    if GRANT_CONSUMPTION_LIFECYCLE == LEDGER_EXECUTION_STATE_STARTED:
        raise ContractValidationError("ledger_execution_state_must_stay_started")
    if attempt.execution_permitted or attempt.production_grant or attempt.distributed_exactly_once:
        raise ContractValidationError("execution_not_granted")
    adapter_id = receipt.adapter_id
    if not isinstance(adapter_id, str) or not adapter_id.startswith(SPRINT3C_SCOPED_READ_ADAPTER_PREFIX):
        raise ContractValidationError("adapter_identity_mismatch")
    if receipt.authority_kind is not HostAuthorityKind.NONPRODUCTION_TEST_ADAPTER:
        raise ContractValidationError("adapter_identity_mismatch")
    if receipt.approved_source_adapter is not False:
        raise ContractValidationError("read_observation_is_not_source_authentication")
    if receipt.grant_nonce != attempt.grant_nonce:
        raise ContractValidationError("grant_nonce_mismatch")
    if receipt.invocation_id != attempt.invocation_id or receipt.master_request_id != attempt.master_request_id:
        raise ContractValidationError("invocation_identity_mismatch")
    if receipt.planning_session_id != attempt.planning_session_id or receipt.step_id != attempt.step_id:
        raise ContractValidationError("invocation_identity_mismatch")
    if receipt.principal_ref != attempt.principal_ref or receipt.policy_revision != attempt.policy_revision:
        raise ContractValidationError("principal_mismatch")
    if receipt.resource_id != resource_id or receipt.operation is not operation:
        raise ContractValidationError("grant_scope_mismatch")
    if receipt.tool_call_id != tool_call_id:
        raise ContractValidationError("invocation_identity_mismatch")
    if operation is ReadOperation.SCAN or operation not in _ALLOWED_READ_OPERATIONS:
        raise ContractValidationError("scan_not_permitted")
    return Sprint3cConsumedAttemptBinding(
        master_request_id=attempt.master_request_id,
        planning_session_id=attempt.planning_session_id,
        step_id=attempt.step_id,
        invocation_id=attempt.invocation_id,
        principal_ref=attempt.principal_ref,
        policy_revision=attempt.policy_revision,
        owner_id=attempt.owner_id,
        attempt_token=attempt.attempt_token,
        fence_epoch=attempt.fence_epoch,
        grant_nonce=attempt.grant_nonce,
        idempotency_key=attempt.idempotency_key,
        payload_fingerprint=attempt.payload_fingerprint,
        ledger_state=LEDGER_EXECUTION_STATE_STARTED,
        resource_id=resource_id,
        operation=operation,
        adapter_identity=adapter_id,
        tool_name=tool_name,
        tool_call_id=tool_call_id,
        accepted_at_utc=attempt.accepted_at_utc,
        deadline_utc=attempt.deadline_utc,
        specialist=attempt.specialist,
        purpose=attempt.purpose,
        specialist_request_id=specialist_request_id,
        correlation_id=correlation_id,
        ledger_grant_nonce=attempt.grant_nonce,
        region_scope=region_scope,
        distributed_exactly_once=False,
        production_grant=False,
    )


def join_sprint3c_evidence_response(payload: Sprint3cEvidenceResponseInput) -> Sprint3cEvidenceResponseJoin:
    """Bind C's receipt and D's native result to MA4/MA5 without authenticating them."""

    _assert_closed_surface()
    if type(payload) is not Sprint3cEvidenceResponseInput:
        raise ContractValidationError("invalid_evidence_response_input")
    defects: set[Sprint3cJoinDefect] = {Sprint3cJoinDefect.PRODUCTION_AUTHENTICITY_UNAVAILABLE}
    rejected: set[Sprint3cRejectedClaim] = set(Sprint3cRejectedClaim)
    native_limitations: list[str] = []
    authorization = payload.authorization
    attempt = payload.consumed_attempt
    authorization_bound = _authorization_matches_attempt(authorization, attempt, defects)
    consumed_bound = authorization_bound
    adapter_accepted = False
    host_evidence: HostEvidenceIntegration | None = None
    admission: MasterEvidenceAdmission | None = None
    admissions: MasterEvidenceAdmissionCollection | None = None
    corroboration: MasterEvidenceCorroborationCollection | None = None
    provenance = _empty_provenance(authorization, attempt)

    if payload.read_receipt is not None:
        adapter_accepted, consumed_bound = _bind_receipt(
            payload,
            defects,
            authorization_bound=authorization_bound,
        )
        host_evidence = integrate_host_read_evidence(
            payload.read_receipt,
            authorization,
            local_host=payload.local_host,
            expected_clocks=payload.expected_clocks,
            expected_source_version=payload.expected_source_version,
            expected_aircraft_id=_expected_aircraft(payload),
            expected_step_id=payload.expected_step_id or attempt.step_id,
            expected_tool_call_id=payload.expected_tool_call_id or attempt.tool_call_id,
            untrusted_claim=payload.untrusted_claim,
        )
        provenance = _provenance_from_receipt(payload.read_receipt, authorization, attempt, defects)
        _receipt_claim_defects(payload, host_evidence, defects)
        if host_evidence.admission.admission_status is MasterAdmissionStatus.REJECTED:
            if admission is None:
                admission = host_evidence.admission

    native_inputs = _native_admission_inputs(payload, defects, native_limitations)
    if native_inputs:
        admissions = admit_master_evidence(native_inputs)
        admission = admissions.admissions[0]
        corroboration_inputs = tuple(
            MasterEvidenceCorroborationInput(item, historical_run=payload.historical_run)
            for item in native_inputs
        )
        corroboration = corroborate_master_evidence(corroboration_inputs)
        _admission_claim_defects(admission, corroboration, defects)

    handoff, render = _render_response(payload, defects)
    status = _join_status(
        authorization_bound=authorization_bound,
        adapter_accepted=adapter_accepted,
        host_evidence=host_evidence,
        admission=admission,
        render=render,
        defects=defects,
    )
    return Sprint3cEvidenceResponseJoin(
        schema_version=SPRINT3C_EVIDENCE_RESPONSE_SCHEMA_VERSION,
        status=status,
        authorization_bound=authorization_bound,
        consumed_attempt_bound=consumed_bound,
        scoped_read_adapter_accepted=adapter_accepted,
        defects=_ordered(Sprint3cJoinDefect, defects),
        rejected_claims=_ordered(Sprint3cRejectedClaim, rejected),
        native_limitations=tuple(dict.fromkeys(native_limitations)),
        provenance=provenance,
        host_evidence=host_evidence,
        admission=admission,
        admissions=admissions,
        corroboration=corroboration,
        response_handoff=handoff,
        response_render=render,
    )


def _authorization_matches_attempt(
    authorization: MasterInvocationAuthorization,
    attempt: Sprint3cConsumedAttemptBinding,
    defects: set[Sprint3cJoinDefect],
) -> bool:
    bound = True
    if authorization.master_request_id != attempt.master_request_id:
        defects.add(Sprint3cJoinDefect.INVOCATION_IDENTITY_MISMATCH)
        bound = False
    if authorization.invocation_id != attempt.invocation_id:
        defects.add(Sprint3cJoinDefect.INVOCATION_IDENTITY_MISMATCH)
        bound = False
    if authorization.specialist is not attempt.specialist or authorization.purpose is not attempt.purpose:
        defects.add(Sprint3cJoinDefect.INVOCATION_IDENTITY_MISMATCH)
        bound = False
    if (
        authorization.specialist_request_id is not None
        and attempt.specialist_request_id is not None
        and authorization.specialist_request_id != attempt.specialist_request_id
    ):
        defects.add(Sprint3cJoinDefect.INVOCATION_IDENTITY_MISMATCH)
        bound = False
    if (
        authorization.correlation_id is not None
        and attempt.correlation_id is not None
        and authorization.correlation_id != attempt.correlation_id
    ):
        defects.add(Sprint3cJoinDefect.INVOCATION_IDENTITY_MISMATCH)
        bound = False
    if (
        authorization.application_request_time_utc is not None
        and authorization.application_request_time_utc != attempt.accepted_at_utc
    ):
        defects.add(Sprint3cJoinDefect.CLOCK_ROLE_COLLISION)
        bound = False
    return bound


def _bind_receipt(
    payload: Sprint3cEvidenceResponseInput,
    defects: set[Sprint3cJoinDefect],
    *,
    authorization_bound: bool,
) -> tuple[bool, bool]:
    receipt = payload.read_receipt
    attempt = payload.consumed_attempt
    consumed_bound = authorization_bound
    adapter_accepted = False
    if type(receipt) is HostIssuedReadReceipt:
        defects.add(Sprint3cJoinDefect.LOCAL_ADAPTER_NOT_SCOPED_READ)
        if receipt.adapter_identity != attempt.adapter_identity:
            defects.add(Sprint3cJoinDefect.ADAPTER_IDENTITY_MISMATCH)
        return False, False
    if type(receipt) is not ReadReceiptCandidate:
        defects.add(Sprint3cJoinDefect.FORGED_OR_UNTRUSTED_RECEIPT)
        return False, False
    if receipt.authority_kind is not HostAuthorityKind.NONPRODUCTION_TEST_ADAPTER:
        defects.add(Sprint3cJoinDefect.ADAPTER_IDENTITY_MISMATCH)
    if receipt.approved_source_adapter is not False:
        defects.add(Sprint3cJoinDefect.ADAPTER_IDENTITY_MISMATCH)
    adapter_id = receipt.adapter_id
    if not isinstance(adapter_id, str) or not adapter_id.startswith(SPRINT3C_SCOPED_READ_ADAPTER_PREFIX):
        defects.add(Sprint3cJoinDefect.ADAPTER_IDENTITY_MISMATCH)
    elif adapter_id != attempt.adapter_identity:
        defects.add(Sprint3cJoinDefect.ADAPTER_IDENTITY_MISMATCH)
    else:
        adapter_accepted = True
    if receipt.operation is ReadOperation.SCAN or receipt.operation not in _ALLOWED_READ_OPERATIONS:
        defects.add(Sprint3cJoinDefect.SCAN_NOT_PERMITTED)
        adapter_accepted = False
    if receipt.resource_id != attempt.resource_id or receipt.operation is not attempt.operation:
        defects.add(Sprint3cJoinDefect.GRANT_SCOPE_MISMATCH)
        adapter_accepted = False
        consumed_bound = False
    identity_pairs = (
        (receipt.master_request_id, attempt.master_request_id),
        (receipt.planning_session_id, attempt.planning_session_id),
        (receipt.step_id, attempt.step_id),
        (receipt.invocation_id, attempt.invocation_id),
        (receipt.tool_call_id, attempt.tool_call_id),
    )
    if any(left != right for left, right in identity_pairs):
        defects.add(Sprint3cJoinDefect.CONSUMED_ATTEMPT_MISMATCH)
        consumed_bound = False
        adapter_accepted = False
    if receipt.principal_ref != attempt.principal_ref or receipt.policy_revision != attempt.policy_revision:
        defects.add(Sprint3cJoinDefect.PRINCIPAL_MISMATCH)
        consumed_bound = False
    if receipt.grant_nonce != attempt.grant_nonce:
        defects.add(Sprint3cJoinDefect.GRANT_NONCE_NOT_PRODUCTION_BIND)
    if attempt.ledger_grant_nonce is not None and receipt.grant_nonce == attempt.ledger_grant_nonce:
        defects.add(Sprint3cJoinDefect.GRANT_NONCE_NOT_PRODUCTION_BIND)
    if attempt.region_scope and receipt.effective_region_codes:
        extra = set(receipt.effective_region_codes) - set(attempt.region_scope)
        if extra:
            defects.add(Sprint3cJoinDefect.GRANT_SCOPE_MISMATCH)
            consumed_bound = False
    return adapter_accepted, consumed_bound


def _expected_aircraft(payload: Sprint3cEvidenceResponseInput) -> str | None:
    if payload.expected_aircraft_id is not None:
        return payload.expected_aircraft_id
    return payload.authorization.expected_aircraft_id


def _empty_provenance(
    authorization: MasterInvocationAuthorization,
    attempt: Sprint3cConsumedAttemptBinding,
) -> Sprint3cSourceProvenance:
    requested = authorization.requested_evaluation_time_utc
    application = authorization.application_request_time_utc or attempt.accepted_at_utc
    return Sprint3cSourceProvenance(
        source_name=None,
        source_version=None,
        query_id=None,
        source_observation_time_utc=None,
        evaluation_time_utc=None,
        requested_time_utc=requested,
        application_request_time_utc=application,
        execution_started_at_utc=None,
        execution_finished_at_utc=None,
        freshness=None,
        clocks_distinct=_clocks_distinct(application, None, requested, None),
    )


def _provenance_from_receipt(
    receipt: object,
    authorization: MasterInvocationAuthorization,
    attempt: Sprint3cConsumedAttemptBinding,
    defects: set[Sprint3cJoinDefect],
) -> Sprint3cSourceProvenance:
    application = authorization.application_request_time_utc or attempt.accepted_at_utc
    requested = authorization.requested_evaluation_time_utc
    if type(receipt) is ReadReceiptCandidate:
        observation = receipt.source_reported_observed_at_utc
        evaluation = receipt.execution_finished_at_utc
        source_name = receipt.source_name
        source_version = receipt.source_version
        query_id = receipt.query_id
        started = receipt.execution_started_at_utc
        finished = receipt.execution_finished_at_utc
        freshness = None
        if receipt.outcome is ReadExecutionOutcome.COMPLETED and source_version is None:
            defects.add(Sprint3cJoinDefect.SOURCE_VERSION_ABSENT)
        if receipt.outcome is ReadExecutionOutcome.COMPLETED and query_id is None:
            defects.add(Sprint3cJoinDefect.QUERY_ID_ABSENT)
    elif type(receipt) is HostIssuedReadReceipt:
        observation = receipt.observation_clock_utc
        evaluation = receipt.evaluation_clock_utc
        source_name = receipt.resource_id
        source_version = receipt.response_source_version
        query_id = receipt.query_ids[0] if receipt.query_ids else None
        started = None
        finished = None
        freshness = FreshnessStatus.STALE if PopulationCondition.SOURCE_STALE in receipt.population_conditions else None
        if source_version is None:
            defects.add(Sprint3cJoinDefect.SOURCE_VERSION_ABSENT)
        if query_id is None:
            defects.add(Sprint3cJoinDefect.QUERY_ID_ABSENT)
    else:
        return _empty_provenance(authorization, attempt)
    if application and observation and application == observation:
        defects.add(Sprint3cJoinDefect.CLOCK_ROLE_COLLISION)
    if requested and evaluation and requested == evaluation:
        defects.add(Sprint3cJoinDefect.CLOCK_ROLE_COLLISION)
    if started and finished and finished < started:
        defects.add(Sprint3cJoinDefect.CLOCK_ROLE_COLLISION)
    return Sprint3cSourceProvenance(
        source_name=source_name,
        source_version=source_version,
        query_id=query_id,
        source_observation_time_utc=observation,
        evaluation_time_utc=evaluation,
        requested_time_utc=requested,
        application_request_time_utc=application,
        execution_started_at_utc=started,
        execution_finished_at_utc=finished,
        freshness=freshness,
        clocks_distinct=_clocks_distinct(application, observation, requested, evaluation),
    )


def _clocks_distinct(
    application: str | None,
    observation: str | None,
    requested: str | None,
    evaluation: str | None,
) -> bool:
    present = [item for item in (application, observation, requested, evaluation) if item is not None]
    return len(present) == len(set(present))


def _receipt_claim_defects(
    payload: Sprint3cEvidenceResponseInput,
    evidence: HostEvidenceIntegration,
    defects: set[Sprint3cJoinDefect],
) -> None:
    receipt = payload.read_receipt
    if PopulationCondition.QUERY_RETURNED_ZERO_ROWS in evidence.population_conditions:
        defects.add(Sprint3cJoinDefect.EMPTY_PAGE_NOT_VERIFIED_ZERO)
        defects.add(Sprint3cJoinDefect.POPULATION_INCOMPLETE)
    if PopulationCondition.SOURCE_OMITTED_RECORD in evidence.population_conditions:
        defects.add(Sprint3cJoinDefect.MISSING_RECORD)
        defects.add(Sprint3cJoinDefect.POPULATION_INCOMPLETE)
    if PopulationCondition.PARTIAL_RESULT in evidence.population_conditions:
        defects.add(Sprint3cJoinDefect.PARTIAL_OR_TRUNCATED)
        defects.add(Sprint3cJoinDefect.POPULATION_INCOMPLETE)
    if PopulationCondition.PAGINATION_INCOMPLETE in evidence.population_conditions:
        defects.add(Sprint3cJoinDefect.PARTIAL_OR_TRUNCATED)
        defects.add(Sprint3cJoinDefect.POPULATION_INCOMPLETE)
    if PopulationCondition.SOURCE_STALE in evidence.population_conditions:
        defects.add(Sprint3cJoinDefect.STALE_SOURCE)
    if PopulationCondition.SOURCE_VERSION_MISMATCH in evidence.population_conditions:
        defects.add(Sprint3cJoinDefect.SOURCE_VERSION_MISMATCH)
        defects.add(Sprint3cJoinDefect.CONFLICTING_RECORDS)
    if HostEvidenceLimitation.CLOCKS_SWAPPED in evidence.limitations:
        defects.add(Sprint3cJoinDefect.CLOCK_ROLE_COLLISION)
    if HostEvidenceLimitation.AIRCRAFT_ID_MISMATCH in evidence.limitations:
        defects.add(Sprint3cJoinDefect.AIRCRAFT_IDENTITY_MISMATCH)
    if HostEvidenceLimitation.REGIONAL_TEXT_NOT_POLYGON_INTERSECTION in evidence.limitations:
        defects.add(Sprint3cJoinDefect.REGIONAL_TEXT_NOT_POLYGON)
    if HostEvidenceLimitation.CLAIMED_LOW_RISK_NOT_ESTABLISHED in evidence.limitations:
        defects.add(Sprint3cJoinDefect.MISSING_RISK_NOT_LOW)
    if HostEvidenceLimitation.SCOPED_COMPLETE_ZERO_NOT_ESTABLISHED in evidence.limitations:
        defects.add(Sprint3cJoinDefect.POPULATION_INCOMPLETE)
        defects.add(Sprint3cJoinDefect.EMPTY_PAGE_NOT_VERIFIED_ZERO)
    if any(
        item in evidence.geographic_limitations
        for item in (
            GeographicLimitation.REGIONAL_TEXT_NOT_POLYGON,
            GeographicLimitation.POLYGON_INTERSECTION_NOT_ESTABLISHED,
            GeographicLimitation.IMPACT_COUNT_NOT_ESTABLISHED,
        )
    ):
        defects.add(Sprint3cJoinDefect.REGIONAL_TEXT_NOT_POLYGON)
    expected_hazard = payload.expected_hazard_id
    record_ids = ()
    arguments: dict[str, object] = {}
    if type(receipt) is ReadReceiptCandidate:
        record_ids = receipt.record_ids
        arguments = dict(receipt.requested_arguments)
        if receipt.truncated or receipt.partial_pagination or receipt.has_more:
            defects.add(Sprint3cJoinDefect.PARTIAL_OR_TRUNCATED)
        if receipt.page_empty:
            defects.add(Sprint3cJoinDefect.EMPTY_PAGE_NOT_VERIFIED_ZERO)
            defects.add(Sprint3cJoinDefect.POPULATION_INCOMPLETE)
        if payload.expected_source_version is not None and receipt.source_version not in {
            None,
            payload.expected_source_version,
        }:
            defects.add(Sprint3cJoinDefect.SOURCE_VERSION_MISMATCH)
            defects.add(Sprint3cJoinDefect.CONFLICTING_RECORDS)
        if _region_text_present(receipt.effective_region_codes, record_ids, arguments, payload.untrusted_claim):
            defects.add(Sprint3cJoinDefect.REGIONAL_TEXT_NOT_POLYGON)
    elif type(receipt) is HostIssuedReadReceipt:
        record_ids = receipt.record_ids
        arguments = dict(receipt.requested_query)
        if receipt.truncated or receipt.has_more:
            defects.add(Sprint3cJoinDefect.PARTIAL_OR_TRUNCATED)
        if _region_text_present(receipt.region_scope, record_ids, arguments, payload.untrusted_claim):
            defects.add(Sprint3cJoinDefect.REGIONAL_TEXT_NOT_POLYGON)
    if expected_hazard is not None and record_ids and expected_hazard not in record_ids:
        requested_hazard = arguments.get("hazard_id")
        if requested_hazard == expected_hazard or "hazard_id" in arguments:
            defects.add(Sprint3cJoinDefect.HAZARD_IDENTITY_MISMATCH)
            defects.add(Sprint3cJoinDefect.MISSING_RECORD)
    expected_aircraft = _expected_aircraft(payload)
    requested_aircraft = arguments.get("aircraft_id")
    if (
        expected_aircraft is not None
        and isinstance(requested_aircraft, str)
        and requested_aircraft != expected_aircraft
    ):
        defects.add(Sprint3cJoinDefect.AIRCRAFT_IDENTITY_MISMATCH)
    if ReadReceiptGap.CONTRADICTORY_ROW_COUNT in evidence.gaps:
        defects.add(Sprint3cJoinDefect.CONFLICTING_RECORDS)
    if evidence.verified_zero_established is not False:
        raise ContractValidationError("verified_zero_not_established")
    if not evidence.missing_verified_zero_conditions:
        raise ContractValidationError("verified_zero_not_established")
    if any(name not in evidence.missing_verified_zero_conditions for name in VERIFIED_ZERO_ALWAYS_MISSING):
        raise ContractValidationError("verified_zero_not_established")


def _region_text_present(
    region_codes: tuple[str, ...],
    record_ids: tuple[str, ...],
    arguments: dict[str, object],
    untrusted_claim: object,
) -> bool:
    if region_codes:
        return True
    if any("SIGMET" in item.upper() or "CALIFORNIA" in item.upper() for item in record_ids):
        return True
    for value in arguments.values():
        if isinstance(value, str) and any(marker in value.casefold() for marker in _REGION_TEXT_MARKERS):
            return True
    data: object
    if isinstance(untrusted_claim, ToolResult):
        data = untrusted_claim.data
    else:
        data = untrusted_claim
    if isinstance(data, dict):
        keys = {str(key).casefold() for key in data}
        if keys & {
            "region",
            "region_text",
            "state",
            "polygon",
            "polygon_intersection",
            "impacted_aircraft_count",
        }:
            return True
        blob = " ".join(str(item) for item in data.values())
        if any(marker in blob.casefold() for marker in _REGION_TEXT_MARKERS):
            return True
    if isinstance(untrusted_claim, Evidence):
        return any("SIGMET" in item.record_id.upper() for item in untrusted_claim.source_records if item.record_id)
    return False


def _native_admission_inputs(
    payload: Sprint3cEvidenceResponseInput,
    defects: set[Sprint3cJoinDefect],
    native_limitations: list[str],
) -> tuple[MasterEvidenceAdmissionInput, ...]:
    artifact = payload.native_admission_artifact
    if artifact is None:
        artifact = _live_ops_sources(payload, defects, native_limitations)
    elif isinstance(artifact, LiveOpsAdmissionSources):
        _inspect_live_ops_sources(artifact, payload, defects, native_limitations)
    if payload.native_live_ops is not None:
        _inspect_live_ops_native(payload.native_live_ops, payload, defects, native_limitations)
        if artifact is None and payload.live_ops_context is not None:
            artifact = _live_ops_sources(payload, defects, native_limitations)
    if payload.historical_envelope is not None:
        _inspect_historical_envelope(payload.historical_envelope, payload, defects, native_limitations)
        run = _attr(payload.historical_envelope, "native_run")
        if artifact is None and isinstance(run, HistoricalSpecialistRunResult):
            artifact = run
            defects.add(Sprint3cJoinDefect.HISTORICAL_RUN_NOT_FINAL)
    if payload.decision_resolution is not None:
        _inspect_decision_resolution(payload.decision_resolution, payload, defects, native_limitations)
    if artifact is None:
        return ()
    return (MasterEvidenceAdmissionInput(payload.authorization, artifact),)


def _live_ops_sources(
    payload: Sprint3cEvidenceResponseInput,
    defects: set[Sprint3cJoinDefect],
    native_limitations: list[str],
) -> LiveOpsAdmissionSources | None:
    native = payload.native_live_ops
    context = payload.live_ops_context
    if native is None or context is None:
        return None
    request = _attr(_attr(native, "bridge"), "request")
    result = _attr(native, "native_result")
    if not isinstance(result, LiveOpsRunResult):
        return None
    sources = LiveOpsAdmissionSources(request=request, context=context, result=result)
    _inspect_live_ops_sources(sources, payload, defects, native_limitations)
    return sources


def _native_identity_values(native: object, name: str) -> tuple[str, ...]:
    """Read an identity from the native object and from nested host lineage."""

    found: list[str] = []
    direct = _attr(native, name)
    if isinstance(direct, str) and direct:
        found.append(direct)
    for container_name in ("identity", "mapped", "bridge"):
        nested = _attr(_attr(native, container_name), name)
        if isinstance(nested, str) and nested and nested not in found:
            found.append(nested)
    return tuple(found)


def _inspect_live_ops_native(
    native: object,
    payload: Sprint3cEvidenceResponseInput,
    defects: set[Sprint3cJoinDefect],
    native_limitations: list[str],
) -> None:
    authorization = payload.authorization
    attempt = payload.consumed_attempt
    invocation_ids = _native_identity_values(native, "invocation_id")
    if not invocation_ids or any(
        item not in {authorization.invocation_id, attempt.invocation_id} for item in invocation_ids
    ):
        defects.add(Sprint3cJoinDefect.INVOCATION_IDENTITY_MISMATCH)
    if authorization.correlation_id is not None:
        correlations = _native_identity_values(native, "correlation_id")
        if correlations and any(item != authorization.correlation_id for item in correlations):
            defects.add(Sprint3cJoinDefect.INVOCATION_IDENTITY_MISMATCH)
    if authorization.specialist_request_id is not None:
        requests = _native_identity_values(native, "specialist_request_id")
        if requests and any(item != authorization.specialist_request_id for item in requests):
            defects.add(Sprint3cJoinDefect.INVOCATION_IDENTITY_MISMATCH)
    if _attr(native, "source_authenticity") not in {None, MasterSourceAuthenticity.NOT_ESTABLISHED}:
        raise ContractValidationError("source_authenticity_not_established")
    if _attr(native, "geospatial_coverage_established") is True:
        raise ContractValidationError("geospatial_coverage_not_established")
    if _attr(native, "regional_string_match_is_sigmet_coverage") is True:
        raise ContractValidationError("geospatial_coverage_not_established")
    if _attr(native, "aircraft_impact_count") is not None:
        raise ContractValidationError("impact_count_not_established")
    if _attr(native, "verified_zero") is True:
        raise ContractValidationError("verified_zero_not_established")
    matches = _attr(native, "regional_string_matches") or ()
    if matches:
        defects.add(Sprint3cJoinDefect.REGIONAL_TEXT_NOT_POLYGON)
    outcome = _attr(native, "verification_outcome")
    if outcome is LiveOpsPublicVerificationOutcome.ALL_VERIFIED:
        defects.add(Sprint3cJoinDefect.LIVE_OPS_VERIFIED_NOT_AUTHENTICATED)
    result = _attr(native, "native_result")
    if isinstance(result, LiveOpsRunResult):
        native_limitations.extend(result.mandatory_limitations)
        _live_ops_result_claims(result, payload, defects)


def _inspect_live_ops_sources(
    sources: LiveOpsAdmissionSources,
    payload: Sprint3cEvidenceResponseInput,
    defects: set[Sprint3cJoinDefect],
    native_limitations: list[str],
) -> None:
    result = sources.result
    native_limitations.extend(result.mandatory_limitations)
    if result.verification_outcome is LiveOpsPublicVerificationOutcome.ALL_VERIFIED:
        defects.add(Sprint3cJoinDefect.LIVE_OPS_VERIFIED_NOT_AUTHENTICATED)
    authorization = payload.authorization
    if authorization.specialist_request_id is not None and result.request_id != authorization.specialist_request_id:
        defects.add(Sprint3cJoinDefect.INVOCATION_IDENTITY_MISMATCH)
    if authorization.correlation_id is not None and result.correlation_id != authorization.correlation_id:
        defects.add(Sprint3cJoinDefect.INVOCATION_IDENTITY_MISMATCH)
    _live_ops_result_claims(result, payload, defects)


def _live_ops_result_claims(
    result: LiveOpsRunResult,
    payload: Sprint3cEvidenceResponseInput,
    defects: set[Sprint3cJoinDefect],
) -> None:
    expected_aircraft = _expected_aircraft(payload)
    expected_hazard = payload.expected_hazard_id
    for claim in result.verified_claims:
        if claim.kind is LiveOpsPublicClaimKind.HAZARD_REGION_INTERSECTION:
            defects.add(Sprint3cJoinDefect.REGIONAL_TEXT_NOT_POLYGON)
        if claim.kind is LiveOpsPublicClaimKind.QUERY_RESULT_ZERO:
            defects.add(Sprint3cJoinDefect.EMPTY_PAGE_NOT_VERIFIED_ZERO)
            defects.add(Sprint3cJoinDefect.POPULATION_INCOMPLETE)
        if claim.kind is LiveOpsPublicClaimKind.MODEL_VISIBLE_TRUNCATION:
            defects.add(Sprint3cJoinDefect.PARTIAL_OR_TRUNCATED)
            defects.add(Sprint3cJoinDefect.POPULATION_INCOMPLETE)
        if claim.kind is LiveOpsPublicClaimKind.SCOPED_COUNT:
            defects.add(Sprint3cJoinDefect.POPULATION_INCOMPLETE)
        scope = getattr(claim, "scope", None)
        region = _attr(scope, "region") if scope is not None else None
        if isinstance(region, str) and region:
            defects.add(Sprint3cJoinDefect.REGIONAL_TEXT_NOT_POLYGON)
        if expected_aircraft is not None and claim.aircraft_id not in {None, expected_aircraft}:
            defects.add(Sprint3cJoinDefect.AIRCRAFT_IDENTITY_MISMATCH)
        if expected_hazard is not None and claim.hazard_id not in {None, expected_hazard}:
            defects.add(Sprint3cJoinDefect.HAZARD_IDENTITY_MISMATCH)
        risk_level = getattr(claim, "risk_level", None)
        if isinstance(risk_level, str) and risk_level.strip().upper() in _LOW_RISK_LABELS:
            defects.add(Sprint3cJoinDefect.MISSING_RISK_NOT_LOW)


def _inspect_historical_envelope(
    envelope: object,
    payload: Sprint3cEvidenceResponseInput,
    defects: set[Sprint3cJoinDefect],
    native_limitations: list[str],
) -> None:
    if _attr(envelope, "finalization_performed") is not False:
        raise ContractValidationError("historical_run_not_final_artifact")
    if _attr(envelope, "evidence_issued") is not False:
        raise ContractValidationError("verified_evidence_not_issued")
    if _attr(envelope, "run_is_final_artifact") is True:
        raise ContractValidationError("historical_run_not_final_artifact")
    if _attr(envelope, "verified_zero_admitted") is True:
        raise ContractValidationError("verified_zero_not_established")
    if _attr(envelope, "source_authenticity") not in {None, MasterSourceAuthenticity.NOT_ESTABLISHED}:
        raise ContractValidationError("source_authenticity_not_established")
    invocation_id = _attr(envelope, "invocation_id")
    if invocation_id not in {None, payload.authorization.invocation_id, payload.consumed_attempt.invocation_id}:
        defects.add(Sprint3cJoinDefect.INVOCATION_IDENTITY_MISMATCH)
    defects.add(Sprint3cJoinDefect.HISTORICAL_RUN_NOT_FINAL)
    run = _attr(envelope, "native_run")
    final_result = _attr(envelope, "native_final_result")
    if _attr(envelope, "source_version_mismatch") is True:
        defects.add(Sprint3cJoinDefect.SOURCE_VERSION_MISMATCH)
        defects.add(Sprint3cJoinDefect.CONFLICTING_RECORDS)
    if _attr(envelope, "authenticated_source_version") is not None:
        raise ContractValidationError("source_version_not_integrity_proof")
    if _attr(envelope, "authenticated_query_id") is not None:
        raise ContractValidationError("query_id_not_authenticated")
    if final_result is not None:
        limitations = _attr(final_result, "limitations") or ()
        native_limitations.extend(str(item) for item in limitations)
        if "RESULT_TRUNCATED" in limitations:
            defects.add(Sprint3cJoinDefect.PARTIAL_OR_TRUNCATED)
    if isinstance(run, HistoricalSpecialistRunResult) and run.evaluated_as_of_utc is None:
        native_limitations.append(MasterTemporalLimitation.EVALUATION_TIME_ABSENT.value)


def _inspect_decision_resolution(
    resolution: object,
    payload: Sprint3cEvidenceResponseInput,
    defects: set[Sprint3cJoinDefect],
    native_limitations: list[str],
) -> None:
    if _attr(resolution, "decision_request") is not None:
        raise ContractValidationError("decision_target_not_established")
    if _attr(resolution, "native_request_built") is True:
        raise ContractValidationError("decision_target_not_established")
    if _attr(resolution, "absent_risk_is_low_risk") is True:
        raise ContractValidationError("absent_risk_is_not_low_risk")
    if _attr(resolution, "authenticated_evidence") is True:
        raise ContractValidationError("fake_read_not_authenticated_evidence")
    if _attr(resolution, "source_authenticity") not in {None, MasterSourceAuthenticity.NOT_ESTABLISHED}:
        raise ContractValidationError("source_authenticity_not_established")
    defects.add(Sprint3cJoinDefect.DECISION_COMPARISON_NOT_TARGET)
    defects.add(Sprint3cJoinDefect.DECISION_REQUEST_NOT_ESTABLISHED)
    risk_observation = _attr(resolution, "risk_observation")
    if risk_observation in {RiskRecordObservation.MISSING, RiskRecordObservation.NOT_CONSULTED, "MISSING"}:
        defects.add(Sprint3cJoinDefect.MISSING_RISK_NOT_LOW)
        native_limitations.append("RECORDED_RISK_ABSENCE_NOT_LOW")
    observed_risk = _attr(resolution, "observed_risk_level")
    if observed_risk is not None and str(observed_risk).upper() in _LOW_RISK_LABELS:
        defects.add(Sprint3cJoinDefect.MISSING_RISK_NOT_LOW)
    expected = _expected_aircraft(payload)
    observed_aircraft = _attr(resolution, "observed_aircraft_id")
    if expected is not None and observed_aircraft not in {None, expected}:
        defects.add(Sprint3cJoinDefect.AIRCRAFT_IDENTITY_MISMATCH)
    if payload.authorization.expected_aircraft_id is not None:
        defects.add(Sprint3cJoinDefect.DECISION_COMPARISON_NOT_TARGET)


def _admission_claim_defects(
    admission: MasterEvidenceAdmission,
    corroboration: MasterEvidenceCorroborationCollection,
    defects: set[Sprint3cJoinDefect],
) -> None:
    reasons = admission.reason_codes
    if MasterAdmissionReason.LIVE_OPS_ALL_VERIFIED_NOT_SOURCE_AUTHENTICATION in reasons:
        defects.add(Sprint3cJoinDefect.LIVE_OPS_VERIFIED_NOT_AUTHENTICATED)
    if MasterAdmissionReason.HISTORICAL_RUN_NOT_FINAL_ARTIFACT in reasons:
        defects.add(Sprint3cJoinDefect.HISTORICAL_RUN_NOT_FINAL)
    if MasterAdmissionReason.EXPECTED_AIRCRAFT_IS_COMPARISON_CANDIDATE in reasons:
        defects.add(Sprint3cJoinDefect.DECISION_COMPARISON_NOT_TARGET)
    if MasterAdmissionReason.VERIFIED_ZERO_NOT_INFERRED in reasons:
        defects.add(Sprint3cJoinDefect.EMPTY_PAGE_NOT_VERIFIED_ZERO)
    if MasterAdmissionReason.VERIFIED_ZERO_CLAIM_NOT_AUTHENTICATED in reasons:
        defects.add(Sprint3cJoinDefect.EMPTY_PAGE_NOT_VERIFIED_ZERO)
        defects.add(Sprint3cJoinDefect.POPULATION_INCOMPLETE)
    if MasterAdmissionReason.TRUNCATED_EVIDENCE in reasons:
        defects.add(Sprint3cJoinDefect.PARTIAL_OR_TRUNCATED)
        defects.add(Sprint3cJoinDefect.POPULATION_INCOMPLETE)
    if MasterAdmissionReason.GEOGRAPHIC_PROVENANCE_ABSENT in reasons:
        defects.add(Sprint3cJoinDefect.REGIONAL_TEXT_NOT_POLYGON)
    if MasterBindingDefect.TARGET_MISMATCH in admission.binding_defects:
        defects.add(Sprint3cJoinDefect.AIRCRAFT_IDENTITY_MISMATCH)
    for item in corroboration.results:
        if MasterCorroborationReason.RECORDED_RISK_ABSENCE_NOT_LOW in item.reason_codes:
            defects.add(Sprint3cJoinDefect.MISSING_RISK_NOT_LOW)
        if MasterCorroborationReason.HISTORICAL_PARTIAL_EVIDENCE_NOT_COMPLETE in item.reason_codes:
            defects.add(Sprint3cJoinDefect.PARTIAL_OR_TRUNCATED)
            defects.add(Sprint3cJoinDefect.POPULATION_INCOMPLETE)


def _render_response(
    payload: Sprint3cEvidenceResponseInput,
    defects: set[Sprint3cJoinDefect],
) -> tuple[MasterResponsePolicyHandoff | None, MasterResponseRender | None]:
    if payload.workflow is None:
        return None, None
    workflow = payload.workflow
    if workflow.context.master_request_id != payload.authorization.master_request_id:
        defects.add(Sprint3cJoinDefect.INVOCATION_IDENTITY_MISMATCH)
    handoff = evaluate_master_response_policy(workflow)
    render = render_master_response(handoff)
    _assert_safe_render(render)
    if MasterResponseLimitation.SOURCE_AUTHENTICITY_NOT_ESTABLISHED not in handoff.limitations:
        raise ContractValidationError("source_authenticity_not_established")
    missing = [item for item in _REQUIRED_RESPONSE_LIMITATIONS if item not in handoff.limitations]
    if missing:
        raise ContractValidationError("missing_response_limitation")
    return handoff, render


def _assert_safe_render(render: MasterResponseRender) -> None:
    if render.claims_admitted is not False:
        raise ContractValidationError("operational_claim_not_admitted")
    text = render.status_text
    folded = text.casefold()
    if any(item in folded for item in _FORBIDDEN_RESPONSE_FRAGMENTS):
        raise ContractValidationError("safety_instructions_not_enabled")
    if any(character.isdigit() for character in text):
        raise ContractValidationError("impact_count_not_established")
    if "LOW" in text:
        raise ContractValidationError("absent_risk_is_not_low_risk")


def _join_status(
    *,
    authorization_bound: bool,
    adapter_accepted: bool,
    host_evidence: HostEvidenceIntegration | None,
    admission: MasterEvidenceAdmission | None,
    render: MasterResponseRender | None,
    defects: set[Sprint3cJoinDefect],
) -> Sprint3cJoinStatus:
    identity_blockers = {
        Sprint3cJoinDefect.INVOCATION_IDENTITY_MISMATCH,
        Sprint3cJoinDefect.CONSUMED_ATTEMPT_MISMATCH,
        Sprint3cJoinDefect.ADAPTER_IDENTITY_MISMATCH,
        Sprint3cJoinDefect.PRINCIPAL_MISMATCH,
    }
    if defects & identity_blockers or not authorization_bound:
        return Sprint3cJoinStatus.IDENTITY_MISMATCH
    native_admission = (
        admission is not None
        and MasterAdmissionReason.UNSUPPORTED_ARTIFACT not in admission.reason_codes
    )
    if native_admission:
        if admission.admission_status is MasterAdmissionStatus.REJECTED:
            return Sprint3cJoinStatus.NATIVE_REJECTED
        if admission.admission_status is MasterAdmissionStatus.BINDING_UNRESOLVED:
            return Sprint3cJoinStatus.NATIVE_BINDING_UNRESOLVED
        if admission.admission_status is MasterAdmissionStatus.CONTRACT_ONLY:
            return Sprint3cJoinStatus.NATIVE_CONTRACT_ONLY
    if host_evidence is not None and not adapter_accepted:
        return Sprint3cJoinStatus.LOCAL_RECEIPT_NOT_PRODUCTION
    if adapter_accepted and host_evidence is not None:
        return Sprint3cJoinStatus.STRUCTURAL_BIND_RECORDED
    if render is None:
        return Sprint3cJoinStatus.INSUFFICIENT_FOR_RESPONSE
    return Sprint3cJoinStatus.INSUFFICIENT_FOR_RESPONSE


def production_admission_gaps() -> tuple[str, ...]:
    """Exact gaps that still block production MA4 admission."""

    _assert_closed_surface()
    return (
        "MasterSourceAuthenticity has only NOT_ESTABLISHED",
        "PRODUCTION_SOURCE_AUTHENTICATOR_INTEGRATED is false",
        "SOURCE_AUTHENTICATED_READ_AVAILABLE is false",
        "open_production_source_authenticator raises",
        "admit_evidence raises evidence_not_admitted",
        "local and nonproduction receipts remain NOT_ESTABLISHED",
        "approved_source_adapter stays false",
        "no ADMITTED MasterAdmissionStatus",
        "claims_admitted stays false",
        "verified-zero production conditions stay missing",
        "geospatial polygon proof is unresolved",
        "Decision host proofs remain unissued",
        "Historical native correlation fields remain absent",
    )


_assert_closed_surface()


__all__ = [
    "ABSENT_RISK_IS_LOW_RISK",
    "AVIATION_EVIDENCE_ADMISSION_ENABLED",
    "EMPTY_PAGE_CREATES_VERIFIED_ZERO",
    "NUMERICAL_IMPACT_COUNTS_IN_MA5",
    "PRODUCTION_MA4_ADMISSION_AVAILABLE",
    "REGIONAL_TEXT_ESTABLISHES_POLYGON_INTERSECTION",
    "SAFETY_OR_DIVERSION_INSTRUCTIONS_ENABLED",
    "SPRINT3C_EVIDENCE_RESPONSE_SCHEMA_VERSION",
    "SPRINT3C_SCOPED_READ_ADAPTER_PREFIX",
    "Sprint3cConsumedAttemptBinding",
    "Sprint3cEvidenceResponseInput",
    "Sprint3cEvidenceResponseJoin",
    "Sprint3cJoinDefect",
    "Sprint3cJoinStatus",
    "Sprint3cRejectedClaim",
    "Sprint3cSourceProvenance",
    "bind_scoped_read_to_consumed_attempt",
    "join_sprint3c_evidence_response",
    "open_production_source_authenticator",
    "open_sprint3c_production_evidence_admission",
    "production_admission_gaps",
    "refuse_sprint3c_runtime_evidence_admission",
]
