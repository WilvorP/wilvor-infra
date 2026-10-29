"""Offline boundary from a local read observation to MA4 admission.

``ReadReceiptCandidate`` and ``raw_read_observation`` are host observations.
This module passes them to the existing provenance and admission functions.
It does not authenticate a source, mint a verified zero, or admit an
operational count, LOW risk, or SIGMET polygon intersection.

A future production host must originate the fields on
``ProductionHostEvidencePort``. That port is inactive. Historical run results
still omit ``HISTORICAL_NATIVE_FIELDS_NOT_ON_RUN_RESULT``, including
``source_version`` and ``query_id``. This module does not add those fields
and does not treat a local adapter as the missing authenticator.
"""

from __future__ import annotations

from dataclasses import dataclass, fields
from datetime import datetime, timedelta, timezone
from enum import Enum

from wilvor_ai.contracts import ContractValidationError, Evidence, ToolResult
from wilvor_ai.master_authenticated_read_receipt import (
    HISTORICAL_NATIVE_FIELDS_NOT_ON_RUN_RESULT,
    VERIFIED_ZERO_ALWAYS_MISSING,
    GeographicLimitation,
    HostIssuedReadReceipt,
    LocalTrustedReadHost,
    PopulationCondition,
    ReadReceiptGap,
    ReadReceiptVerification,
    ReadReceiptVerificationStatus,
    assess_untrusted_read_subject,
    ma4_integration_requirements,
    verify_read_receipt,
)
from wilvor_ai.master_contracts import (
    MasterBindingDefect,
    MasterInvocationAuthorization,
    MasterProductionAuthority,
    MasterSpecialist,
)
from wilvor_ai.master_evidence_admission import (
    MasterAdmissionReason,
    MasterAdmissionStatus,
    MasterEvidenceAdmission,
    MasterEvidenceAdmissionInput,
    MasterSourceAuthenticity,
    admit_master_evidence,
)
from wilvor_ai.master_provenance_trace import (
    ProposedDispatcherExecutionEvent,
    classify_master_provenance,
)
from wilvor_ai.master_read_resource_guard import MasterReadDenialReason
from wilvor_ai.master_routing import MAX_HISTORICAL_WINDOW
from wilvor_ai.master_source_read_trace_boundary import (
    MasterSourceReadTraceAssessment,
    assess_source_read_trace,
)
from wilvor_ai.master_trusted_read_executor import (
    RAW_OBSERVATION_KIND,
    HostAuthorityKind,
    ReadBoundaryDenial,
    ReadExecutionOutcome,
    ReadReceiptCandidate,
    raw_read_observation,
)
from wilvor_ai.specialist_runtime_contracts import HistoricalSpecialistRunResult


MASTER_HOST_EVIDENCE_INTEGRATION_SCHEMA_VERSION = (
    "wilvor.ai.master_host_evidence_integration.v1"
)
PRODUCTION_HOST_EVIDENCE_PORT_ID = "wilvor.production_host_source_authenticator.unavailable"
PRODUCTION_SOURCE_AUTHENTICATOR_INTEGRATED = False
LOCAL_READ_IS_PRODUCTION_EVIDENCE = False
RAW_OBSERVATION_IS_AUTHENTICATED_RECEIPT = False

PRODUCTION_HOST_ORIGIN_FIELDS = (
    "master_request_id",
    "planning_session_id",
    "step_id",
    "invocation_id",
    "specialist_request_id",
    "correlation_id",
    "specialist",
    "purpose",
    "capability",
    "principal_ref",
    "policy_revision",
    "idempotency_key",
    "tool_name",
    "tool_call_id",
    "resource_id",
    "operation",
    "requested_arguments",
    "executed_arguments",
    "region_scope",
    "historical_window",
    "decision_target_scope",
    "accepted_at_utc",
    "deadline_utc",
    "expires_at_utc",
    "observation_clock_utc",
    "evaluation_clock_utc",
    "query_clock_utc",
    "source_version",
    "query_id",
    "grant_consumption_seal",
    "ledger_consumption_seal",
    "registered_production_adapter_seal",
)

_PROVENANCE_SUBJECTS = (ToolResult, Evidence, ProposedDispatcherExecutionEvent)
_CLOCK_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
_IDENTITY_ARGUMENTS = ("aircraft_id", "hazard_id", "encounter_id", "record_id")
_LOW_RISK_LABELS = frozenset({"LOW", "LOW_RISK"})


class HostEvidenceLimitation(str, Enum):
    """Cross-module limits. None of these values is a satisfied proof."""

    PRODUCTION_SOURCE_AUTHENTICATOR_ABSENT = "PRODUCTION_SOURCE_AUTHENTICATOR_ABSENT"
    HISTORICAL_NATIVE_IDENTITY_UNRESOLVED = "HISTORICAL_NATIVE_IDENTITY_UNRESOLVED"
    LOCAL_ADAPTER_NOT_PRODUCTION_PROOF = "LOCAL_ADAPTER_NOT_PRODUCTION_PROOF"
    SERIALIZED_OBSERVATION_NOT_RECEIPT = "SERIALIZED_OBSERVATION_NOT_RECEIPT"
    ARBITRARY_LOCAL_RECEIPT_REJECTED = "ARBITRARY_LOCAL_RECEIPT_REJECTED"
    COPIED_CORRELATOR_NOT_PROOF = "COPIED_CORRELATOR_NOT_PROOF"
    INVOCATION_MISMATCH = "INVOCATION_MISMATCH"
    SPECIALIST_MISMATCH = "SPECIALIST_MISMATCH"
    PURPOSE_MISMATCH = "PURPOSE_MISMATCH"
    CLOCKS_SWAPPED = "CLOCKS_SWAPPED"
    AIRCRAFT_ID_MISMATCH = "AIRCRAFT_ID_MISMATCH"
    CALLSIGN_NOT_AIRCRAFT_IDENTITY = "CALLSIGN_NOT_AIRCRAFT_IDENTITY"
    CALLSIGN_COLLISION = "CALLSIGN_COLLISION"
    HISTORICAL_WINDOW_BOUNDED_NOT_AUTHENTICATED = "HISTORICAL_WINDOW_BOUNDED_NOT_AUTHENTICATED"
    HISTORICAL_WINDOW_EXCEEDS_SEVEN_DAYS = "HISTORICAL_WINDOW_EXCEEDS_SEVEN_DAYS"
    CLAIMED_LOW_RISK_NOT_ESTABLISHED = "CLAIMED_LOW_RISK_NOT_ESTABLISHED"
    ZERO_ROWS_NOT_VERIFIED_ZERO = "ZERO_ROWS_NOT_VERIFIED_ZERO"
    MISSING_RECORD_NOT_ZERO_ROWS = "MISSING_RECORD_NOT_ZERO_ROWS"
    SCOPED_COMPLETE_ZERO_NOT_ESTABLISHED = "SCOPED_COMPLETE_ZERO_NOT_ESTABLISHED"
    REGIONAL_TEXT_NOT_POLYGON_INTERSECTION = "REGIONAL_TEXT_NOT_POLYGON_INTERSECTION"


@dataclass(frozen=True)
class ExpectedObservationClocks:
    """Clocks the caller says the host observed. A swap is a mismatch."""

    accepted_at_utc: str
    execution_started_at_utc: str | None = None
    execution_finished_at_utc: str | None = None
    source_reported_observed_at_utc: str | None = None

    def __post_init__(self) -> None:
        for name in (
            "accepted_at_utc",
            "execution_started_at_utc",
            "execution_finished_at_utc",
            "source_reported_observed_at_utc",
        ):
            value = getattr(self, name)
            if value is None and name != "accepted_at_utc":
                continue
            _require_clock(value, name)


@dataclass(frozen=True)
class ProductionHostEvidencePort:
    """Inactive authenticator port. Offline success does not open it."""

    port_id: str = PRODUCTION_HOST_EVIDENCE_PORT_ID
    active: bool = False
    authenticator_integrated: bool = False
    origin_fields: tuple[str, ...] = PRODUCTION_HOST_ORIGIN_FIELDS
    historical_native_fields_absent: tuple[str, ...] = ()
    requirements: tuple[object, ...] = ()

    def __post_init__(self) -> None:
        if self.port_id != PRODUCTION_HOST_EVIDENCE_PORT_ID:
            raise ContractValidationError("invalid_production_host_port")
        if self.active is not False or self.authenticator_integrated is not False:
            raise ContractValidationError("production_source_authenticator_not_integrated")
        if self.origin_fields != PRODUCTION_HOST_ORIGIN_FIELDS:
            raise ContractValidationError("invalid_production_host_port")
        if not isinstance(self.historical_native_fields_absent, tuple):
            raise ContractValidationError("invalid_historical_native_fields")
        if any(item.satisfied is not False or item.status.value != "UNRESOLVED" for item in self.requirements):
            raise ContractValidationError("ma4_requirement_not_unresolved")

    def __bool__(self) -> bool:
        return False


@dataclass(frozen=True)
class HostEvidenceIntegration:
    """One admission of a local observation or an untrusted substitute.

    ``observation_status`` is the local recording, when there was one.
    ``receipt_status`` is that recording only when it is bound to the
    authorization. A mismatch stays a local observation and is not evidence
    for the authorization. Production authenticity stays ``NOT_ESTABLISHED``.
    """

    schema_version: str
    observation_status: ReadReceiptVerificationStatus
    receipt_status: ReadReceiptVerificationStatus
    authorization_bound: bool
    admission: MasterEvidenceAdmission
    source_authenticity: MasterSourceAuthenticity
    population_conditions: tuple[PopulationCondition, ...]
    geographic_limitations: tuple[GeographicLimitation, ...]
    gaps: tuple[ReadReceiptGap, ...]
    limitations: tuple[HostEvidenceLimitation, ...]
    binding_defects: tuple[MasterBindingDefect, ...]
    missing_verified_zero_conditions: tuple[str, ...]
    production_host_port: ProductionHostEvidencePort
    serialized_observation: ReadReceiptVerification | None = None
    untrusted_claim_verification: ReadReceiptVerification | None = None
    trace_assessment: MasterSourceReadTraceAssessment | None = None
    candidate_provenance_rejected: bool = False
    observed_rows_returned: int | None = None
    production_authority: MasterProductionAuthority = MasterProductionAuthority.DENIED
    aviation_evidence_admitted: bool = False
    operational_claim_admitted: bool = False
    operational_count_admitted: bool = False
    verified_zero_established: bool = False
    polygon_intersection_established: bool = False
    impacted_aircraft_count_established: bool = False
    low_risk_admitted: bool = False
    decision_risk_level: None = None
    authenticated_source_read_available: bool = False

    def __post_init__(self) -> None:
        if self.schema_version != MASTER_HOST_EVIDENCE_INTEGRATION_SCHEMA_VERSION:
            raise ContractValidationError("invalid_schema_version")
        if not isinstance(self.admission, MasterEvidenceAdmission):
            raise ContractValidationError("invalid_admission")
        if self.source_authenticity is not MasterSourceAuthenticity.NOT_ESTABLISHED:
            raise ContractValidationError("source_authenticity_not_established")
        if self.admission.source_authenticity is not MasterSourceAuthenticity.NOT_ESTABLISHED:
            raise ContractValidationError("source_authenticity_not_established")
        if self.production_authority is not MasterProductionAuthority.DENIED:
            raise ContractValidationError("production_authority_not_issued")
        closed = (
            self.aviation_evidence_admitted,
            self.operational_claim_admitted,
            self.operational_count_admitted,
            self.verified_zero_established,
            self.polygon_intersection_established,
            self.impacted_aircraft_count_established,
            self.low_risk_admitted,
            self.authenticated_source_read_available,
        )
        if any(item is not False for item in closed) or self.decision_risk_level is not None:
            raise ContractValidationError("operational_claim_not_admitted")
        if not isinstance(self.production_host_port, ProductionHostEvidencePort):
            raise ContractValidationError("invalid_production_host_port")
        if PRODUCTION_SOURCE_AUTHENTICATOR_INTEGRATED is not False:
            raise ContractValidationError("production_source_authenticator_not_integrated")
        if LOCAL_READ_IS_PRODUCTION_EVIDENCE is not False:
            raise ContractValidationError("local_observation_not_production_evidence")
        if any(name not in self.missing_verified_zero_conditions for name in VERIFIED_ZERO_ALWAYS_MISSING):
            raise ContractValidationError("verified_zero_not_established")
        if (
            self.receipt_status is ReadReceiptVerificationStatus.LOCAL_OBSERVATION_RECORDED
            and self.authorization_bound is not True
        ):
            raise ContractValidationError("local_observation_not_bound")
        if self.trace_assessment is not None and self.trace_assessment.source_authenticity is not (
            MasterSourceAuthenticity.NOT_ESTABLISHED
        ):
            raise ContractValidationError("source_authenticity_not_established")

    def __bool__(self) -> bool:
        return False


def unresolved_historical_native_fields() -> tuple[str, ...]:
    """Run-result fields this process still cannot read. Absence is not backfilled."""

    present = {item.name for item in fields(HistoricalSpecialistRunResult)}
    return tuple(name for name in HISTORICAL_NATIVE_FIELDS_NOT_ON_RUN_RESULT if name not in present)


def production_host_evidence_port() -> ProductionHostEvidencePort:
    """The future authenticator. Every requirement stays unresolved."""

    return ProductionHostEvidencePort(
        historical_native_fields_absent=unresolved_historical_native_fields(),
        requirements=ma4_integration_requirements(),
    )


def open_production_source_authenticator() -> None:
    """Production source approval is not implemented."""

    raise ContractValidationError("production_source_authenticator_not_integrated")


def integrate_host_read_evidence(
    subject: object,
    authorization: MasterInvocationAuthorization,
    *,
    local_host: LocalTrustedReadHost | None = None,
    expected_clocks: ExpectedObservationClocks | None = None,
    expected_source_version: str | None = None,
    expected_aircraft_id: str | None = None,
    expected_step_id: str | None = None,
    expected_tool_call_id: str | None = None,
    untrusted_claim: object | None = None,
) -> HostEvidenceIntegration:
    """Route one subject through admission and, when it is descriptive, provenance.

    A sealed local candidate is recorded as a local observation and then
    rejected by ``admit_master_evidence`` because it is not a native specialist
    artifact. A ``ToolResult``, ``Evidence`` object, serialized observation,
    or host receipt presented without its issuing host is not that observation.
    """

    if not isinstance(authorization, MasterInvocationAuthorization):
        raise ContractValidationError("invalid_authorization")
    if local_host is not None and type(local_host) is not LocalTrustedReadHost:
        raise ContractValidationError("invalid_read_host")
    if expected_clocks is not None and type(expected_clocks) is not ExpectedObservationClocks:
        raise ContractValidationError("invalid_observation_clocks")
    port = production_host_evidence_port()
    limitations = {
        HostEvidenceLimitation.PRODUCTION_SOURCE_AUTHENTICATOR_ABSENT,
        HostEvidenceLimitation.HISTORICAL_NATIVE_IDENTITY_UNRESOLVED,
    }
    if port.historical_native_fields_absent != unresolved_historical_native_fields():
        raise ContractValidationError("invalid_historical_native_fields")

    serialized: ReadReceiptVerification | None = None
    claim_verification: ReadReceiptVerification | None = None
    trace: MasterSourceReadTraceAssessment | None = None
    provenance_rejected = False
    population: set[PopulationCondition] = set()
    geographic: set[GeographicLimitation] = set()
    gaps: set[ReadReceiptGap] = set()
    defects: set[MasterBindingDefect] = set()
    rows: int | None = None
    observation_status = ReadReceiptVerificationStatus.NOT_A_RECEIPT
    bound = False

    if type(subject) is ReadReceiptCandidate:
        observation = raw_read_observation(subject)
        if observation.get("authentic_source_read") is not False or observation.get("candidate_kind") != (
            RAW_OBSERVATION_KIND
        ):
            raise ContractValidationError("forged_read_receipt")
        serialized = assess_untrusted_read_subject(observation)
        limitations.add(HostEvidenceLimitation.SERIALIZED_OBSERVATION_NOT_RECEIPT)
        limitations.add(HostEvidenceLimitation.LOCAL_ADAPTER_NOT_PRODUCTION_PROOF)
        provenance_rejected = _provenance_rejects(subject)
        _candidate_limits(
            subject,
            authorization,
            expected_clocks=expected_clocks,
            expected_source_version=expected_source_version,
            expected_aircraft_id=expected_aircraft_id,
            expected_step_id=expected_step_id,
            expected_tool_call_id=expected_tool_call_id,
            population=population,
            geographic=geographic,
            gaps=gaps,
            limitations=limitations,
            defects=defects,
        )
        rows = subject.rows_returned
        if subject.outcome is ReadExecutionOutcome.COMPLETED and not subject.denial_reasons:
            observation_status = ReadReceiptVerificationStatus.LOCAL_OBSERVATION_RECORDED
        else:
            observation_status = ReadReceiptVerificationStatus.LOCAL_FAILURE_RECORDED
        bound = observation_status is ReadReceiptVerificationStatus.LOCAL_OBSERVATION_RECORDED and not defects
    elif type(subject) is HostIssuedReadReceipt:
        if local_host is None:
            limitations.add(HostEvidenceLimitation.ARBITRARY_LOCAL_RECEIPT_REJECTED)
            serialized = assess_untrusted_read_subject(subject)
            observation_status = ReadReceiptVerificationStatus.NOT_A_RECEIPT
        else:
            serialized = verify_read_receipt(
                local_host,
                subject,
                expected_master_request_id=authorization.master_request_id,
                expected_step_id=expected_step_id,
            )
            observation_status = serialized.status
            population.update(serialized.population_conditions)
            geographic.update(serialized.geographic_limitations)
            gaps.update(serialized.gaps)
            _host_receipt_limits(
                subject,
                authorization,
                expected_clocks=expected_clocks,
                expected_aircraft_id=expected_aircraft_id,
                expected_step_id=expected_step_id,
                expected_tool_call_id=expected_tool_call_id,
                gaps=gaps,
                limitations=limitations,
                defects=defects,
            )
            limitations.add(HostEvidenceLimitation.LOCAL_ADAPTER_NOT_PRODUCTION_PROOF)
            rows = subject.rows_returned
            bound = (
                observation_status is ReadReceiptVerificationStatus.LOCAL_OBSERVATION_RECORDED
                and not defects
            )
    else:
        if getattr(subject, "approved_source_adapter", False) is True or getattr(
            subject,
            "local_fake_is_aviation_evidence",
            False,
        ) is True:
            limitations.add(HostEvidenceLimitation.LOCAL_ADAPTER_NOT_PRODUCTION_PROOF)
        _untrusted_limits(subject, authorization, limitations, defects, record_defect=True)
        serialized = assess_untrusted_read_subject(subject)
        observation_status = serialized.status
        geographic.update(serialized.geographic_limitations)
        gaps.update(serialized.gaps)
        _claimed_region(subject, geographic, limitations, gaps)
        if geographic & {
            GeographicLimitation.REGIONAL_TEXT_NOT_POLYGON,
            GeographicLimitation.POLYGON_INTERSECTION_NOT_ESTABLISHED,
            GeographicLimitation.IMPACT_COUNT_NOT_ESTABLISHED,
        }:
            limitations.add(HostEvidenceLimitation.REGIONAL_TEXT_NOT_POLYGON_INTERSECTION)
        if _is_provenance_subject(subject):
            trace = assess_source_read_trace(subject, authorization=authorization)

    if untrusted_claim is not None:
        claim_verification = assess_untrusted_read_subject(untrusted_claim)
        geographic.update(claim_verification.geographic_limitations)
        gaps.update(claim_verification.gaps)
        _untrusted_limits(untrusted_claim, authorization, limitations, defects, record_defect=False)
        _claimed_risk(untrusted_claim, limitations)
        _claimed_region(untrusted_claim, geographic, limitations, gaps)
        if _is_provenance_subject(untrusted_claim):
            trace = assess_source_read_trace(untrusted_claim, authorization=authorization)

    receipt_status = observation_status if bound else (
        ReadReceiptVerificationStatus.BINDING_MISMATCH
        if type(subject) in (ReadReceiptCandidate, HostIssuedReadReceipt) and defects
        else observation_status
    )
    if (
        type(subject) in (ReadReceiptCandidate, HostIssuedReadReceipt)
        and not bound
        and observation_status is ReadReceiptVerificationStatus.LOCAL_OBSERVATION_RECORDED
    ):
        receipt_status = ReadReceiptVerificationStatus.BINDING_MISMATCH
    admission = _admit(authorization, subject)
    if _read_shaped(subject) and admission.admission_status is not MasterAdmissionStatus.REJECTED:
        raise ContractValidationError("local_observation_not_production_evidence")
    if _read_shaped(subject) and MasterAdmissionReason.UNSUPPORTED_ARTIFACT not in admission.reason_codes:
        raise ContractValidationError("local_observation_not_production_evidence")
    return HostEvidenceIntegration(
        schema_version=MASTER_HOST_EVIDENCE_INTEGRATION_SCHEMA_VERSION,
        observation_status=observation_status,
        receipt_status=receipt_status,
        authorization_bound=bound,
        admission=admission,
        source_authenticity=MasterSourceAuthenticity.NOT_ESTABLISHED,
        population_conditions=_ordered(PopulationCondition, population),
        geographic_limitations=_ordered(GeographicLimitation, geographic),
        gaps=_ordered(ReadReceiptGap, gaps),
        limitations=_ordered(HostEvidenceLimitation, limitations),
        binding_defects=tuple(sorted(defects, key=lambda item: item.value)),
        missing_verified_zero_conditions=VERIFIED_ZERO_ALWAYS_MISSING,
        production_host_port=port,
        serialized_observation=serialized,
        untrusted_claim_verification=claim_verification,
        trace_assessment=trace,
        candidate_provenance_rejected=provenance_rejected,
        observed_rows_returned=rows,
    )


def _admit(
    authorization: MasterInvocationAuthorization,
    subject: object,
) -> MasterEvidenceAdmission:
    collection = admit_master_evidence((MasterEvidenceAdmissionInput(authorization, subject),))
    admission = collection.admissions[0]
    if admission.source_authenticity is not MasterSourceAuthenticity.NOT_ESTABLISHED:
        raise ContractValidationError("source_authenticity_not_established")
    if admission.evidence_verified is not False:
        raise ContractValidationError("synthetic_evidence_not_verified")
    return admission


def _provenance_rejects(candidate: ReadReceiptCandidate) -> bool:
    try:
        classify_master_provenance(candidate)  # type: ignore[arg-type]
    except ContractValidationError as exc:
        if "unsupported_provenance_subject" in exc.errors:
            return True
        raise
    raise ContractValidationError("read_candidate_cannot_be_provenance_subject")


def _candidate_limits(
    receipt: ReadReceiptCandidate,
    authorization: MasterInvocationAuthorization,
    *,
    expected_clocks: ExpectedObservationClocks | None,
    expected_source_version: str | None,
    expected_aircraft_id: str | None,
    expected_step_id: str | None,
    expected_tool_call_id: str | None,
    population: set[PopulationCondition],
    geographic: set[GeographicLimitation],
    gaps: set[ReadReceiptGap],
    limitations: set[HostEvidenceLimitation],
    defects: set[MasterBindingDefect],
) -> None:
    _identity_limits(
        master_request_id=receipt.master_request_id,
        invocation_id=receipt.invocation_id,
        step_id=receipt.step_id,
        tool_call_id=receipt.tool_call_id,
        specialist=receipt.specialist,
        purpose=receipt.purpose,
        correlation_id=receipt.correlation_id,
        accepted_at_utc=receipt.accepted_at_utc,
        authorization=authorization,
        expected_step_id=expected_step_id,
        expected_tool_call_id=expected_tool_call_id,
        limitations=limitations,
        defects=defects,
    )
    _clock_limits(
        accepted_at_utc=receipt.accepted_at_utc,
        execution_started_at_utc=receipt.execution_started_at_utc,
        execution_finished_at_utc=receipt.execution_finished_at_utc,
        source_reported_observed_at_utc=receipt.source_reported_observed_at_utc,
        expected_clocks=expected_clocks,
        gaps=gaps,
        limitations=limitations,
        defects=defects,
    )
    arguments = dict(receipt.requested_arguments)
    _population_limits(
        receipt,
        arguments,
        expected_source_version=expected_source_version,
        population=population,
        gaps=gaps,
        limitations=limitations,
    )
    _aircraft_limits(
        receipt.effective_decision_aircraft_id,
        arguments,
        authorization,
        expected_aircraft_id,
        receipt.denial_reasons,
        limitations,
        defects,
    )
    _callsign_limits(arguments, receipt.record_ids, limitations)
    _risk_argument(arguments.get("risk_level"), limitations)
    _window_limits(receipt, arguments, limitations)
    _region_limits(receipt.effective_region_codes, receipt.record_ids, geographic, limitations)
    if receipt.authority_kind is not HostAuthorityKind.NONPRODUCTION_TEST_ADAPTER:
        limitations.add(HostEvidenceLimitation.LOCAL_ADAPTER_NOT_PRODUCTION_PROOF)
        defects.add(MasterBindingDefect.REQUEST_ID_MISMATCH)
    if receipt.local_test_adapter_resolved or receipt.approved_source_adapter is False:
        limitations.add(HostEvidenceLimitation.LOCAL_ADAPTER_NOT_PRODUCTION_PROOF)


def _host_receipt_limits(
    receipt: HostIssuedReadReceipt,
    authorization: MasterInvocationAuthorization,
    *,
    expected_clocks: ExpectedObservationClocks | None,
    expected_aircraft_id: str | None,
    expected_step_id: str | None,
    expected_tool_call_id: str | None,
    gaps: set[ReadReceiptGap],
    limitations: set[HostEvidenceLimitation],
    defects: set[MasterBindingDefect],
) -> None:
    _identity_limits(
        master_request_id=receipt.master_request_id,
        invocation_id=receipt.invocation_id,
        step_id=receipt.step_id,
        tool_call_id=receipt.tool_call_id,
        specialist=receipt.specialist,
        purpose=receipt.purpose,
        correlation_id=receipt.correlation_id,
        accepted_at_utc=receipt.accepted_at_utc,
        authorization=authorization,
        expected_step_id=expected_step_id,
        expected_tool_call_id=expected_tool_call_id,
        limitations=limitations,
        defects=defects,
    )
    _clock_limits(
        accepted_at_utc=receipt.accepted_at_utc,
        execution_started_at_utc=None,
        execution_finished_at_utc=None,
        source_reported_observed_at_utc=None,
        expected_clocks=expected_clocks,
        gaps=gaps,
        limitations=limitations,
        defects=defects,
    )
    arguments = dict(receipt.requested_query)
    expected = expected_aircraft_id if expected_aircraft_id is not None else authorization.expected_aircraft_id
    actual = receipt.decision_target_scope
    if expected is not None and actual is not None and actual != expected:
        limitations.add(HostEvidenceLimitation.AIRCRAFT_ID_MISMATCH)
        defects.add(MasterBindingDefect.TARGET_MISMATCH)
    requested = arguments.get("aircraft_id")
    if expected is not None and isinstance(requested, str) and requested != expected:
        limitations.add(HostEvidenceLimitation.AIRCRAFT_ID_MISMATCH)
        defects.add(MasterBindingDefect.TARGET_MISMATCH)
    if PopulationCondition.QUERY_RETURNED_ZERO_ROWS in receipt.population_conditions:
        limitations.add(HostEvidenceLimitation.ZERO_ROWS_NOT_VERIFIED_ZERO)
        limitations.add(HostEvidenceLimitation.SCOPED_COMPLETE_ZERO_NOT_ESTABLISHED)
    if PopulationCondition.SOURCE_OMITTED_RECORD in receipt.population_conditions:
        limitations.add(HostEvidenceLimitation.MISSING_RECORD_NOT_ZERO_ROWS)
    if PopulationCondition.SCOPED_AUTHORITATIVE_EMPTY_POPULATION in receipt.population_conditions:
        limitations.add(HostEvidenceLimitation.SCOPED_COMPLETE_ZERO_NOT_ESTABLISHED)
    if receipt.geographic_limitations:
        limitations.add(HostEvidenceLimitation.REGIONAL_TEXT_NOT_POLYGON_INTERSECTION)


def _population_limits(
    receipt: ReadReceiptCandidate,
    arguments: dict[str, object],
    *,
    expected_source_version: str | None,
    population: set[PopulationCondition],
    gaps: set[ReadReceiptGap],
    limitations: set[HostEvidenceLimitation],
) -> None:
    completed = receipt.outcome is ReadExecutionOutcome.COMPLETED
    historical = receipt.specialist is MasterSpecialist.HISTORICAL_ANALYTICS
    if completed and receipt.source_version is None:
        population.add(PopulationCondition.SOURCE_VERSION_ABSENT)
        gaps.add(ReadReceiptGap.MISSING_SOURCE_VERSION)
        if historical:
            gaps.add(ReadReceiptGap.HISTORICAL_NATIVE_FIELD_ABSENT)
    elif receipt.source_version is not None:
        gaps.add(ReadReceiptGap.SOURCE_VERSION_NOT_INTEGRITY_PROOF)
        if expected_source_version is not None and receipt.source_version != expected_source_version:
            population.add(PopulationCondition.SOURCE_VERSION_MISMATCH)
            gaps.add(ReadReceiptGap.SOURCE_VERSION_MISMATCH)
    if completed and receipt.query_id is None:
        population.add(PopulationCondition.MISSING_QUERY_ID)
        gaps.add(ReadReceiptGap.MISSING_QUERY_ID)
        if historical:
            gaps.add(ReadReceiptGap.HISTORICAL_NATIVE_FIELD_ABSENT)
    elif receipt.query_id is not None:
        gaps.add(ReadReceiptGap.QUERY_ID_NOT_AUTHENTICATED)
    if receipt.partial_pagination or receipt.has_more or receipt.next_page_token_present:
        population.add(PopulationCondition.PAGINATION_INCOMPLETE)
        gaps.add(ReadReceiptGap.PAGINATION_INCOMPLETE)
    if receipt.truncated or receipt.partial_pagination:
        population.add(PopulationCondition.PARTIAL_RESULT)
        gaps.add(ReadReceiptGap.PARTIAL_RESULT)
    if receipt.outcome is ReadExecutionOutcome.TIMEOUT:
        population.add(PopulationCondition.TIMEOUT)
        gaps.add(ReadReceiptGap.TIMEOUT)
    if receipt.outcome is ReadExecutionOutcome.CANCELLED:
        population.add(PopulationCondition.CANCELLED)
        gaps.add(ReadReceiptGap.CANCELLED)
    requested_ids = [
        value
        for name in _IDENTITY_ARGUMENTS
        if isinstance((value := arguments.get(name)), str)
    ]
    omitted = (
        completed
        and receipt.rows_returned > 0
        and bool(requested_ids)
        and any(item not in receipt.record_ids for item in requested_ids)
    )
    if completed and receipt.page_empty and receipt.rows_returned == 0:
        population.add(PopulationCondition.QUERY_RETURNED_ZERO_ROWS)
        gaps.add(ReadReceiptGap.QUERY_RETURNED_ZERO_ROWS)
        gaps.add(ReadReceiptGap.SCOPED_EMPTY_NOT_VERIFIED_ZERO)
        limitations.add(HostEvidenceLimitation.ZERO_ROWS_NOT_VERIFIED_ZERO)
        limitations.add(HostEvidenceLimitation.SCOPED_COMPLETE_ZERO_NOT_ESTABLISHED)
    elif omitted:
        population.add(PopulationCondition.SOURCE_OMITTED_RECORD)
        gaps.add(ReadReceiptGap.SOURCE_OMITTED_RECORD)
        limitations.add(HostEvidenceLimitation.MISSING_RECORD_NOT_ZERO_ROWS)
        if receipt.rows_returned > 0:
            population.add(PopulationCondition.ROWS_PRESENT)
    elif completed and receipt.rows_returned > 0:
        population.add(PopulationCondition.ROWS_PRESENT)
    gaps.add(ReadReceiptGap.SOURCE_AUTHENTICITY_NOT_ESTABLISHED)
    gaps.add(ReadReceiptGap.LOCAL_OBSERVATION_NOT_PRODUCTION_AUTHENTICITY)
    gaps.add(ReadReceiptGap.NOT_AVIATION_EVIDENCE)


def _identity_limits(
    *,
    master_request_id: str | None,
    invocation_id: str | None,
    step_id: str | None,
    tool_call_id: str | None,
    specialist: object,
    purpose: object,
    correlation_id: str | None,
    accepted_at_utc: str | None,
    authorization: MasterInvocationAuthorization,
    expected_step_id: str | None,
    expected_tool_call_id: str | None,
    limitations: set[HostEvidenceLimitation],
    defects: set[MasterBindingDefect],
) -> None:
    if master_request_id != authorization.master_request_id or invocation_id != authorization.invocation_id:
        limitations.add(HostEvidenceLimitation.INVOCATION_MISMATCH)
        defects.add(MasterBindingDefect.REQUEST_ID_MISMATCH)
    if specialist is not authorization.specialist:
        limitations.add(HostEvidenceLimitation.SPECIALIST_MISMATCH)
        defects.add(MasterBindingDefect.REQUEST_ID_MISMATCH)
    if purpose is not authorization.purpose:
        limitations.add(HostEvidenceLimitation.PURPOSE_MISMATCH)
        defects.add(MasterBindingDefect.REQUEST_ID_MISMATCH)
    if expected_step_id is not None and step_id != expected_step_id:
        limitations.add(HostEvidenceLimitation.INVOCATION_MISMATCH)
        defects.add(MasterBindingDefect.REQUEST_ID_MISMATCH)
    if expected_tool_call_id is not None and tool_call_id != expected_tool_call_id:
        limitations.add(HostEvidenceLimitation.INVOCATION_MISMATCH)
        defects.add(MasterBindingDefect.REQUEST_ID_MISMATCH)
    if authorization.correlation_id is not None and correlation_id != authorization.correlation_id:
        limitations.add(HostEvidenceLimitation.COPIED_CORRELATOR_NOT_PROOF)
        defects.add(MasterBindingDefect.CORRELATION_MISMATCH)
    if (
        authorization.application_request_time_utc is not None
        and accepted_at_utc != authorization.application_request_time_utc
    ):
        limitations.add(HostEvidenceLimitation.CLOCKS_SWAPPED)
        defects.add(MasterBindingDefect.TIME_MISMATCH)


def _clock_limits(
    *,
    accepted_at_utc: str | None,
    execution_started_at_utc: str | None,
    execution_finished_at_utc: str | None,
    source_reported_observed_at_utc: str | None,
    expected_clocks: ExpectedObservationClocks | None,
    gaps: set[ReadReceiptGap],
    limitations: set[HostEvidenceLimitation],
    defects: set[MasterBindingDefect],
) -> None:
    started = _parsed_clock(execution_started_at_utc)
    finished = _parsed_clock(execution_finished_at_utc)
    if started is not None and finished is not None and finished < started:
        gaps.add(ReadReceiptGap.WRONG_CLOCK)
        limitations.add(HostEvidenceLimitation.CLOCKS_SWAPPED)
        defects.add(MasterBindingDefect.TIME_MISMATCH)
    if expected_clocks is None:
        return
    actual = {
        "accepted_at_utc": accepted_at_utc,
        "execution_started_at_utc": execution_started_at_utc,
        "execution_finished_at_utc": execution_finished_at_utc,
        "source_reported_observed_at_utc": source_reported_observed_at_utc,
    }
    for name, expected in (
        ("accepted_at_utc", expected_clocks.accepted_at_utc),
        ("execution_started_at_utc", expected_clocks.execution_started_at_utc),
        ("execution_finished_at_utc", expected_clocks.execution_finished_at_utc),
        ("source_reported_observed_at_utc", expected_clocks.source_reported_observed_at_utc),
    ):
        if expected is not None and actual[name] != expected:
            gaps.add(ReadReceiptGap.WRONG_CLOCK)
            limitations.add(HostEvidenceLimitation.CLOCKS_SWAPPED)
            defects.add(MasterBindingDefect.TIME_MISMATCH)


def _aircraft_limits(
    effective_aircraft_id: str | None,
    arguments: dict[str, object],
    authorization: MasterInvocationAuthorization,
    expected_aircraft_id: str | None,
    denials: tuple[ReadBoundaryDenial, ...],
    limitations: set[HostEvidenceLimitation],
    defects: set[MasterBindingDefect],
) -> None:
    expected = expected_aircraft_id if expected_aircraft_id is not None else authorization.expected_aircraft_id
    requested = arguments.get("aircraft_id")
    mismatched = ReadBoundaryDenial.DECISION_TARGET_MISMATCH in denials
    if expected is not None and effective_aircraft_id is not None and effective_aircraft_id != expected:
        mismatched = True
    if expected is not None and isinstance(requested, str) and requested != expected:
        mismatched = True
    if mismatched:
        limitations.add(HostEvidenceLimitation.AIRCRAFT_ID_MISMATCH)
        defects.add(MasterBindingDefect.TARGET_MISMATCH)


def _callsign_limits(
    arguments: dict[str, object],
    record_ids: tuple[str, ...],
    limitations: set[HostEvidenceLimitation],
) -> None:
    callsign = arguments.get("callsign")
    if not isinstance(callsign, str):
        return
    limitations.add(HostEvidenceLimitation.CALLSIGN_NOT_AIRCRAFT_IDENTITY)
    if len(record_ids) > 1 or callsign in record_ids:
        limitations.add(HostEvidenceLimitation.CALLSIGN_COLLISION)


def _window_limits(
    receipt: ReadReceiptCandidate,
    arguments: dict[str, object],
    limitations: set[HostEvidenceLimitation],
) -> None:
    spans = (
        _span_exceeds(arguments.get("start_utc"), arguments.get("end_utc")),
        _span_exceeds(receipt.effective_historical_start_utc, receipt.effective_historical_end_utc),
    )
    denied = (
        ReadBoundaryDenial.HISTORICAL_WINDOW_OUTSIDE_HOST in receipt.denial_reasons
        or MasterReadDenialReason.HISTORICAL_INTERVAL_ESCALATION in receipt.guard_denial_reasons
    )
    if denied or any(item is True for item in spans):
        limitations.add(HostEvidenceLimitation.HISTORICAL_WINDOW_EXCEEDS_SEVEN_DAYS)
        return
    if any(item is False for item in spans) and receipt.outcome is ReadExecutionOutcome.COMPLETED:
        limitations.add(HostEvidenceLimitation.HISTORICAL_WINDOW_BOUNDED_NOT_AUTHENTICATED)


def _region_limits(
    region_codes: tuple[str, ...],
    record_ids: tuple[str, ...],
    geographic: set[GeographicLimitation],
    limitations: set[HostEvidenceLimitation],
) -> None:
    regional = bool(region_codes) or any("SIGMET" in record_id.upper() for record_id in record_ids)
    if not regional:
        return
    geographic.add(GeographicLimitation.REGIONAL_TEXT_NOT_POLYGON)
    geographic.add(GeographicLimitation.POLYGON_INTERSECTION_NOT_ESTABLISHED)
    geographic.add(GeographicLimitation.STATE_GROUPING_NOT_POLYGON)
    limitations.add(HostEvidenceLimitation.REGIONAL_TEXT_NOT_POLYGON_INTERSECTION)


def _read_shaped(subject: object) -> bool:
    return type(subject) in (ReadReceiptCandidate, HostIssuedReadReceipt) or isinstance(
        subject,
        (dict, str, bytes, ToolResult, Evidence, ProposedDispatcherExecutionEvent),
    )


def _untrusted_limits(
    subject: object,
    authorization: MasterInvocationAuthorization,
    limitations: set[HostEvidenceLimitation],
    defects: set[MasterBindingDefect],
    *,
    record_defect: bool,
) -> None:
    if isinstance(subject, (ToolResult, Evidence, ProposedDispatcherExecutionEvent, dict, str, bytes)):
        limitations.add(HostEvidenceLimitation.SERIALIZED_OBSERVATION_NOT_RECEIPT)
    if isinstance(subject, HostIssuedReadReceipt):
        limitations.add(HostEvidenceLimitation.ARBITRARY_LOCAL_RECEIPT_REJECTED)
    correlation = _correlation_of(subject)
    if authorization.correlation_id is not None and correlation == authorization.correlation_id:
        limitations.add(HostEvidenceLimitation.COPIED_CORRELATOR_NOT_PROOF)
        if record_defect:
            defects.add(MasterBindingDefect.CORRELATION_MISMATCH)
    _claimed_risk(subject, limitations)
    if isinstance(subject, dict) and subject.get("candidate_kind") == RAW_OBSERVATION_KIND:
        limitations.add(HostEvidenceLimitation.SERIALIZED_OBSERVATION_NOT_RECEIPT)
        if record_defect and subject.get("authentic_source_read") is True:
            defects.add(MasterBindingDefect.REQUEST_ID_MISMATCH)


def _claimed_risk(subject: object, limitations: set[HostEvidenceLimitation]) -> None:
    if isinstance(subject, ToolResult):
        _risk_argument(_mapping_value(subject.data, "risk_level"), limitations)
        _risk_argument(_mapping_value(subject.data, "risk"), limitations)
    elif isinstance(subject, dict):
        _risk_argument(subject.get("risk_level"), limitations)
        _risk_argument(subject.get("risk"), limitations)


def _claimed_region(
    subject: object,
    geographic: set[GeographicLimitation],
    limitations: set[HostEvidenceLimitation],
    gaps: set[ReadReceiptGap],
) -> None:
    data = subject.data if isinstance(subject, ToolResult) else subject if isinstance(subject, dict) else None
    if not isinstance(data, dict):
        return
    keys = {str(key).casefold() for key in data}
    if keys & {"region", "region_text", "state", "polygon", "polygon_intersection", "impacted_aircraft_count"}:
        geographic.add(GeographicLimitation.REGIONAL_TEXT_NOT_POLYGON)
        geographic.add(GeographicLimitation.POLYGON_INTERSECTION_NOT_ESTABLISHED)
        geographic.add(GeographicLimitation.IMPACT_COUNT_NOT_ESTABLISHED)
        limitations.add(HostEvidenceLimitation.REGIONAL_TEXT_NOT_POLYGON_INTERSECTION)
        gaps.add(ReadReceiptGap.FORGED_REGIONAL_IMPACT)


def _risk_argument(value: object, limitations: set[HostEvidenceLimitation]) -> None:
    if isinstance(value, str) and value.strip().upper() in _LOW_RISK_LABELS:
        limitations.add(HostEvidenceLimitation.CLAIMED_LOW_RISK_NOT_ESTABLISHED)


def _correlation_of(subject: object) -> object:
    if isinstance(subject, ToolResult):
        return subject.correlation_id
    if isinstance(subject, ProposedDispatcherExecutionEvent):
        return subject.correlation_id
    if isinstance(subject, dict):
        return subject.get("correlation_id")
    return None


def _mapping_value(data: object, name: str) -> object:
    if isinstance(data, dict):
        return data.get(name)
    return None


def _is_provenance_subject(subject: object) -> bool:
    return isinstance(subject, _PROVENANCE_SUBJECTS)


def _span_exceeds(start: object, end: object) -> bool | None:
    parsed_start = _parsed_clock(start)
    parsed_end = _parsed_clock(end)
    if parsed_start is None or parsed_end is None:
        return None
    return parsed_end - parsed_start > MAX_HISTORICAL_WINDOW


def _parsed_clock(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.strptime(value, _CLOCK_FORMAT).replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _require_clock(value: object, field_name: str) -> None:
    if _parsed_clock(value) is None:
        raise ContractValidationError(f"invalid_{field_name}")


def _ordered(enum_type: type[Enum], selected: set[Enum]) -> tuple[Enum, ...]:
    return tuple(item for item in enum_type if item in selected)


def _assert_window_ceiling() -> None:
    if MAX_HISTORICAL_WINDOW != timedelta(days=7):
        raise ContractValidationError("historical_window_ceiling_changed")
    if PRODUCTION_SOURCE_AUTHENTICATOR_INTEGRATED is not False:
        raise ContractValidationError("production_source_authenticator_not_integrated")


_assert_window_ceiling()


__all__ = [
    "LOCAL_READ_IS_PRODUCTION_EVIDENCE",
    "MASTER_HOST_EVIDENCE_INTEGRATION_SCHEMA_VERSION",
    "PRODUCTION_HOST_EVIDENCE_PORT_ID",
    "PRODUCTION_HOST_ORIGIN_FIELDS",
    "PRODUCTION_SOURCE_AUTHENTICATOR_INTEGRATED",
    "RAW_OBSERVATION_IS_AUTHENTICATED_RECEIPT",
    "ExpectedObservationClocks",
    "HostEvidenceIntegration",
    "HostEvidenceLimitation",
    "ProductionHostEvidencePort",
    "integrate_host_read_evidence",
    "open_production_source_authenticator",
    "production_host_evidence_port",
    "unresolved_historical_native_fields",
]
