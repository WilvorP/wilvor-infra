"""Local read receipts stay unauthenticated through MA4 admission and MA5."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from tests.contracts.test_master_evidence_corroboration import _pack
from tests.fixtures.ma4_specialist_evidence_examples import (
    decision_absent_called_low_case,
    decision_absent_risk_case,
)
from wilvor_ai.contracts import (
    AgentCapability,
    ConfidenceLevel,
    ContractValidationError,
    Evidence,
    FreshnessStatus,
    SourceRecord,
    TemporalScope,
    ToolResult,
    ToolResultStatus,
)
from wilvor_ai.master_authenticated_read_receipt import (
    HISTORICAL_NATIVE_FIELDS_NOT_ON_RUN_RESULT,
    ApprovedExecutorAction,
    DescriptiveReadContext,
    LocalAdapterObservation,
    LocalTrustedReadHost,
    ObservedSourceRow,
    PopulationCondition,
    ReadOperation,
    ReadReceiptVerificationStatus,
)
from wilvor_ai.master_contracts import (
    MasterBindingDefect,
    MasterInvocationAuthorization,
    MasterProductionAuthority,
    MasterPurpose,
    MasterRequirement,
    MasterSpecialist,
)
from wilvor_ai.master_evidence_admission import MasterAdmissionReason, MasterAdmissionStatus, MasterEvidenceAdmissionInput
from wilvor_ai.master_evidence_corroboration import (
    MasterCorroborationReason,
    MasterCorroborationStatus,
    MasterEvidenceCorroborationInput,
    corroborate_master_evidence,
)
from wilvor_ai.master_host_evidence_integration import (
    LOCAL_READ_IS_PRODUCTION_EVIDENCE,
    PRODUCTION_HOST_ORIGIN_FIELDS,
    PRODUCTION_SOURCE_AUTHENTICATOR_INTEGRATED,
    RAW_OBSERVATION_IS_AUTHENTICATED_RECEIPT,
    ExpectedObservationClocks,
    HostEvidenceLimitation,
    integrate_host_read_evidence,
    open_production_source_authenticator,
    unresolved_historical_native_fields,
)
from wilvor_ai.master_parallel_contracts import (
    MasterParallelContractBundle,
    MasterResponseLimitation,
    MasterResponsePolicyState,
)
from wilvor_ai.master_read_resource_guard import ReadOperation as GuardReadOperation
from wilvor_ai.master_response_policy import evaluate_master_response_policy
from wilvor_ai.master_response_renderer import render_master_response
from wilvor_ai.master_routing import (
    MasterPlanningContext,
    MasterProposedCapability,
    MasterProposedStep,
    MasterRouteOutcome,
    MasterRoutePlan,
    MasterRouteProposal,
    MasterRouteTemporalIntent,
    authorize_route,
)
from wilvor_ai.master_trusted_read_executor import (
    RAW_OBSERVATION_KIND,
    MasterTrustedReadExecutor,
    NonproductionAdapterSpec,
    NonproductionReadPage,
    ReadBoundaryDenial,
    ReadExecutionError,
    ReadExecutionOutcome,
    UntrustedReadRequest,
    bind_nonproduction_cancellation,
    bind_nonproduction_host_clock,
    bind_nonproduction_read_registry,
    bind_nonproduction_test_invocation,
    raw_read_observation,
)


HISTORY = MasterSpecialist.HISTORICAL_ANALYTICS
LIVE = MasterSpecialist.LIVE_OPS
DECISION = MasterSpecialist.DECISION
HISTORICAL = AgentCapability.RETRIEVE_HISTORICAL_ANALYTICS
OPERATIONAL = AgentCapability.RETRIEVE_DETERMINISTIC_OPERATIONAL_CONTEXT
RISK = AgentCapability.EXPLAIN_EXISTING_RISK
OBSERVED = "2026-01-15T00:00:00Z"
DEADLINE = "2026-01-15T00:05:00Z"
START = "2026-01-08T00:00:00Z"
END = "2026-01-15T00:00:00Z"
SOURCE_OBSERVED = "2026-01-14T12:00:00Z"
WIDE_START = "2026-01-07T00:00:00Z"
TEXT = "Which recorded hazards apply inside the authorized window?"
SECRET_TOKEN = "secret-page-token"


class Clock:
    def __init__(self, *instants: str) -> None:
        self.instants = list(instants)

    def now_utc(self) -> str:
        if not self.instants:
            raise AssertionError("host clock exhausted")
        return self.instants.pop(0)


def _auth(**overrides: object) -> MasterInvocationAuthorization:
    values: dict[str, object] = {
        "master_request_id": "master-req-1",
        "invocation_id": "inv-1",
        "specialist": HISTORY,
        "purpose": MasterPurpose.ANSWER,
        "requirement": MasterRequirement.REQUIRED,
        "correlation_id": "corr-1",
        "application_request_time_utc": OBSERVED,
    }
    values.update(overrides)
    return MasterInvocationAuthorization(**values)  # type: ignore[arg-type]


def _host(**overrides: object):
    values: dict[str, object] = {
        "master_request_id": "master-req-1",
        "planning_session_id": "plan-1",
        "step_id": "step-1",
        "invocation_id": "inv-1",
        "specialist": HISTORY,
        "purpose": MasterPurpose.ANSWER,
        "capability": HISTORICAL,
        "tool_name": "list_historical_encounters",
        "tool_call_id": "tool-call-1",
        "resource_id": "historical.encounters",
        "operation": GuardReadOperation.QUERY,
        "principal_ref": "principal-1",
        "policy_revision": "policy-rev-1",
        "accepted_at_utc": OBSERVED,
        "deadline_utc": DEADLINE,
        "idempotency_key": "idem-1",
        "grant_nonce": "nonce-1",
        "executions_used": 0,
        "model_turns_used": 0,
        "tool_calls_used": 0,
        "timeout_budget_seconds": 30,
        "historical_start_utc": START,
        "historical_end_utc": END,
        "correlation_id": "corr-1",
    }
    values.update(overrides)
    return bind_nonproduction_test_invocation(**values)  # type: ignore[arg-type]


def _request(host: object, **overrides: object) -> UntrustedReadRequest:
    values: dict[str, object] = {
        "master_request_id": host.master_request_id,
        "planning_session_id": host.planning_session_id,
        "step_id": host.step_id,
        "invocation_id": host.invocation_id,
        "specialist": host.specialist,
        "purpose": host.purpose,
        "capability": host.capability,
        "tool_name": host.tool_name,
        "tool_call_id": host.tool_call_id,
        "resource_id": host.resource_id,
        "operation": host.operation,
        "requested_arguments": (
            ("start_utc", START),
            ("end_utc", END),
            ("aircraft_id", "AC1"),
        ),
        "correlation_id": host.correlation_id,
    }
    values.update(overrides)
    return UntrustedReadRequest(**values)  # type: ignore[arg-type]


def _page(**overrides: object) -> NonproductionReadPage:
    values: dict[str, object] = {
        "record_ids": ("enc-1",),
        "source_name": "historical.encounters",
        "source_version": "version-1",
        "query_id": "query-1",
        "page_index": 0,
        "has_more": False,
        "source_observed_at_utc": SOURCE_OBSERVED,
    }
    values.update(overrides)
    return NonproductionReadPage(**values)  # type: ignore[arg-type]


def _execute(host: object, page: object, **request_overrides: object):
    def read(_arguments: object, _cancellation: object) -> object:
        return page

    registry = bind_nonproduction_read_registry(
        (
            NonproductionAdapterSpec(
                specialist=host.specialist,
                tool_name=host.tool_name,
                capability=host.capability,
                resource_id=host.resource_id,
                operation=host.operation,
                read=read,
            ),
        )
    )
    executor = MasterTrustedReadExecutor(registry, bind_nonproduction_host_clock(OBSERVED))
    try:
        return executor.execute(
            _request(host, **request_overrides),
            host_invocation=host,
            cancellation=bind_nonproduction_cancellation(),
        )
    except ReadExecutionError as exc:
        return exc.receipt


def _closed(result) -> None:
    assert result.source_authenticity.value == "NOT_ESTABLISHED"
    assert result.admission.source_authenticity.value == "NOT_ESTABLISHED"
    assert result.admission.evidence_verified is False
    assert result.production_authority is MasterProductionAuthority.DENIED
    assert result.aviation_evidence_admitted is False
    assert result.operational_claim_admitted is False
    assert result.operational_count_admitted is False
    assert result.verified_zero_established is False
    assert result.polygon_intersection_established is False
    assert result.impacted_aircraft_count_established is False
    assert result.low_risk_admitted is False
    assert result.decision_risk_level is None
    assert result.authenticated_source_read_available is False
    assert result.production_host_port.active is False
    assert result.production_host_port.authenticator_integrated is False
    assert bool(result) is False
    assert "source_version" in result.production_host_port.historical_native_fields_absent
    assert "query_id" in result.production_host_port.historical_native_fields_absent
    assert all(item.status.value == "UNRESOLVED" for item in result.production_host_port.requirements)


def _plan(*steps: MasterProposedStep, **context_overrides: object):
    values: dict[str, object] = {
        "master_request_id": "master-req-ma5-1",
        "correlation_id": "corr-ma5-1",
        "application_request_time_utc": "2026-01-15T00:00:01Z",
        "canonical_user_text": TEXT,
    }
    values.update(context_overrides)
    context = MasterPlanningContext(**values)  # type: ignore[arg-type]
    capabilities = tuple(
        dict.fromkeys(MasterProposedCapability(step.capability, step.requirement) for step in steps)
    )
    decision = authorize_route(
        MasterRouteProposal(user_text=TEXT, capabilities=capabilities, steps=steps),
        context,
    )
    assert decision.outcome is MasterRouteOutcome.AUTHORIZED_PLAN
    assert decision.plan is not None
    return context, decision.plan


def _historical_step() -> MasterProposedStep:
    return MasterProposedStep(
        step_id="hist-1",
        specialist=HISTORY,
        purpose=MasterPurpose.ANSWER,
        requirement=MasterRequirement.REQUIRED,
        capability=HISTORICAL,
        temporal_intent=MasterRouteTemporalIntent.HISTORICAL,
        historical_start_utc=START,
        historical_end_utc=END,
    )


def test_production_authenticator_port_stays_unresolved():
    assert PRODUCTION_SOURCE_AUTHENTICATOR_INTEGRATED is False
    assert LOCAL_READ_IS_PRODUCTION_EVIDENCE is False
    assert RAW_OBSERVATION_IS_AUTHENTICATED_RECEIPT is False
    absent = unresolved_historical_native_fields()
    assert absent == tuple(name for name in HISTORICAL_NATIVE_FIELDS_NOT_ON_RUN_RESULT)
    assert "source_version" in absent
    assert "query_id" in absent
    assert "master_request_id" in absent
    assert "invocation_id" in absent
    for field_name in (
        "source_version",
        "query_id",
        "grant_consumption_seal",
        "ledger_consumption_seal",
        "observation_clock_utc",
        "evaluation_clock_utc",
        "query_clock_utc",
    ):
        assert field_name in PRODUCTION_HOST_ORIGIN_FIELDS
    with pytest.raises(ContractValidationError, match="production_source_authenticator_not_integrated"):
        open_production_source_authenticator()


def test_local_read_is_recorded_and_ma4_does_not_admit_it():
    host = _host()
    receipt = _execute(host, _page())
    assert receipt.outcome is ReadExecutionOutcome.COMPLETED
    result = integrate_host_read_evidence(receipt, _auth())
    assert result.observation_status is ReadReceiptVerificationStatus.LOCAL_OBSERVATION_RECORDED
    assert result.receipt_status is ReadReceiptVerificationStatus.LOCAL_OBSERVATION_RECORDED
    assert result.authorization_bound is True
    assert result.candidate_provenance_rejected is True
    assert result.admission.admission_status is MasterAdmissionStatus.REJECTED
    assert MasterAdmissionReason.UNSUPPORTED_ARTIFACT in result.admission.reason_codes
    assert result.serialized_observation is not None
    assert result.serialized_observation.status is ReadReceiptVerificationStatus.NOT_A_RECEIPT
    assert HostEvidenceLimitation.LOCAL_ADAPTER_NOT_PRODUCTION_PROOF in result.limitations
    assert HostEvidenceLimitation.HISTORICAL_WINDOW_BOUNDED_NOT_AUTHENTICATED in result.limitations
    assert HostEvidenceLimitation.HISTORICAL_WINDOW_EXCEEDS_SEVEN_DAYS not in result.limitations
    assert PopulationCondition.ROWS_PRESENT in result.population_conditions
    assert result.observed_rows_returned == 1
    _closed(result)
    observation = raw_read_observation(receipt)
    assert observation["candidate_kind"] == RAW_OBSERVATION_KIND
    assert observation["authentic_source_read"] is False
    serialized = integrate_host_read_evidence(observation, _auth())
    assert serialized.receipt_status is ReadReceiptVerificationStatus.NOT_A_RECEIPT
    assert serialized.observation_status is not ReadReceiptVerificationStatus.LOCAL_OBSERVATION_RECORDED
    assert HostEvidenceLimitation.SERIALIZED_OBSERVATION_NOT_RECEIPT in serialized.limitations
    assert HostEvidenceLimitation.COPIED_CORRELATOR_NOT_PROOF in serialized.limitations
    assert serialized.admission.admission_status is MasterAdmissionStatus.REJECTED
    _closed(serialized)


def test_missing_and_mismatched_source_version_stay_unresolved():
    missing = integrate_host_read_evidence(
        _execute(_host(idempotency_key="idem-no-version"), _page(source_version=None)),
        _auth(),
    )
    assert PopulationCondition.SOURCE_VERSION_ABSENT in missing.population_conditions
    assert PopulationCondition.SOURCE_STALE not in missing.population_conditions
    assert missing.receipt_status is ReadReceiptVerificationStatus.LOCAL_OBSERVATION_RECORDED
    assert "source_version" in missing.production_host_port.historical_native_fields_absent
    _closed(missing)

    mismatched = integrate_host_read_evidence(
        _execute(_host(idempotency_key="idem-stale-version"), _page(source_version="stale-version")),
        _auth(),
        expected_source_version="current-version",
    )
    assert PopulationCondition.SOURCE_VERSION_MISMATCH in mismatched.population_conditions
    assert mismatched.low_risk_admitted is False
    _closed(mismatched)


def test_absent_query_id_pagination_partial_and_timeout_are_not_evidence():
    absent = integrate_host_read_evidence(
        _execute(_host(idempotency_key="idem-no-query"), _page(query_id=None)),
        _auth(),
    )
    assert PopulationCondition.MISSING_QUERY_ID in absent.population_conditions
    assert "query_id" in absent.production_host_port.historical_native_fields_absent
    assert absent.admission.admission_status is MasterAdmissionStatus.REJECTED
    _closed(absent)

    partial = integrate_host_read_evidence(
        _execute(
            _host(idempotency_key="idem-partial"),
            _page(has_more=True, truncated=True, next_page_token=SECRET_TOKEN),
        ),
        _auth(),
    )
    assert PopulationCondition.PAGINATION_INCOMPLETE in partial.population_conditions
    assert PopulationCondition.PARTIAL_RESULT in partial.population_conditions
    assert partial.operational_count_admitted is False
    assert SECRET_TOKEN not in repr(partial)
    _closed(partial)

    clock = bind_nonproduction_host_clock(OBSERVED)
    host = _host(idempotency_key="idem-timeout", timeout_budget_seconds=1)

    def slow(_arguments: object, _cancellation: object) -> NonproductionReadPage:
        clock.advance_to("2026-01-15T00:00:05Z")
        return _page()

    registry = bind_nonproduction_read_registry(
        (
            NonproductionAdapterSpec(
                specialist=host.specialist,
                tool_name=host.tool_name,
                capability=host.capability,
                resource_id=host.resource_id,
                operation=host.operation,
                read=slow,
            ),
        )
    )
    executor = MasterTrustedReadExecutor(registry, clock)
    with pytest.raises(ReadExecutionError) as caught:
        executor.execute(
            _request(host),
            host_invocation=host,
            cancellation=bind_nonproduction_cancellation(),
        )
    timed = integrate_host_read_evidence(caught.value.receipt, _auth())
    assert caught.value.receipt.outcome is ReadExecutionOutcome.TIMEOUT
    assert timed.observation_status is ReadReceiptVerificationStatus.LOCAL_FAILURE_RECORDED
    assert PopulationCondition.TIMEOUT in timed.population_conditions
    assert timed.verified_zero_established is False
    _closed(timed)


def test_zero_rows_missing_record_and_scoped_empty_stay_distinct():
    zero = integrate_host_read_evidence(
        _execute(_host(idempotency_key="idem-zero"), _page(record_ids=())),
        _auth(),
    )
    assert PopulationCondition.QUERY_RETURNED_ZERO_ROWS in zero.population_conditions
    assert PopulationCondition.SOURCE_OMITTED_RECORD not in zero.population_conditions
    assert PopulationCondition.SCOPED_AUTHORITATIVE_EMPTY_POPULATION not in zero.population_conditions
    assert HostEvidenceLimitation.ZERO_ROWS_NOT_VERIFIED_ZERO in zero.limitations
    assert HostEvidenceLimitation.SCOPED_COMPLETE_ZERO_NOT_ESTABLISHED in zero.limitations
    assert zero.observed_rows_returned == 0
    assert zero.operational_count_admitted is False
    _closed(zero)

    omitted = integrate_host_read_evidence(
        _execute(_host(idempotency_key="idem-omitted"), _page(record_ids=("HZ-OTHER",))),
        _auth(),
    )
    assert PopulationCondition.SOURCE_OMITTED_RECORD in omitted.population_conditions
    assert PopulationCondition.ROWS_PRESENT in omitted.population_conditions
    assert PopulationCondition.QUERY_RETURNED_ZERO_ROWS not in omitted.population_conditions
    assert HostEvidenceLimitation.MISSING_RECORD_NOT_ZERO_ROWS in omitted.limitations
    _closed(omitted)

    host, receipt = _local_receipt(
        rows=(),
        rows_returned=0,
        scoped_authoritative_empty=True,
        response_source_version="hv-9",
    )
    scoped = integrate_host_read_evidence(receipt, _local_auth(), local_host=host)
    assert scoped.observation_status is ReadReceiptVerificationStatus.LOCAL_OBSERVATION_RECORDED
    assert scoped.receipt_status is ReadReceiptVerificationStatus.LOCAL_OBSERVATION_RECORDED
    assert PopulationCondition.SCOPED_AUTHORITATIVE_EMPTY_POPULATION in scoped.population_conditions
    assert PopulationCondition.QUERY_RETURNED_ZERO_ROWS in scoped.population_conditions
    assert HostEvidenceLimitation.SCOPED_COMPLETE_ZERO_NOT_ESTABLISHED in scoped.limitations
    assert scoped.verified_zero_established is False
    assert scoped.admission.admission_status is MasterAdmissionStatus.REJECTED
    _closed(scoped)


def test_stale_local_host_receipt_remains_a_local_observation():
    host, receipt = _local_receipt(
        response_status=ToolResultStatus.STALE,
        freshness=FreshnessStatus.STALE,
        tool_call_id="tool-call-stale",
        idempotency_key="idem-stale",
    )
    result = integrate_host_read_evidence(receipt, _local_auth(), local_host=host)
    assert result.observation_status is ReadReceiptVerificationStatus.LOCAL_OBSERVATION_RECORDED
    assert result.receipt_status is ReadReceiptVerificationStatus.LOCAL_OBSERVATION_RECORDED
    assert PopulationCondition.SOURCE_STALE in result.population_conditions
    assert result.admission.admission_status is MasterAdmissionStatus.REJECTED
    assert MasterAdmissionReason.UNSUPPORTED_ARTIFACT in result.admission.reason_codes
    _closed(result)
    without_host = integrate_host_read_evidence(receipt, _local_auth())
    assert without_host.receipt_status is ReadReceiptVerificationStatus.NOT_A_RECEIPT
    assert HostEvidenceLimitation.ARBITRARY_LOCAL_RECEIPT_REJECTED in without_host.limitations
    _closed(without_host)


def test_wrong_aircraft_callsign_collision_and_seven_day_window():
    decision_host = _host(
        idempotency_key="idem-aircraft",
        specialist=DECISION,
        purpose=MasterPurpose.ANSWER,
        capability=RISK,
        tool_name="get_current_risk_evidence",
        tool_call_id="tool-call-risk",
        resource_id="aircraft",
        operation=GuardReadOperation.GET_ITEM,
        historical_start_utc=None,
        historical_end_utc=None,
        decision_aircraft_id="AC1",
    )
    completed = _execute(
        decision_host,
        _page(record_ids=("AC1",), source_name="aircraft", source_observed_at_utc=None),
        requested_arguments=(("aircraft_id", "AC1"),),
    )
    wrong = integrate_host_read_evidence(
        completed,
        _auth(specialist=DECISION, expected_aircraft_id="AC-OTHER"),
    )
    assert wrong.observation_status is ReadReceiptVerificationStatus.LOCAL_OBSERVATION_RECORDED
    assert wrong.receipt_status is ReadReceiptVerificationStatus.BINDING_MISMATCH
    assert HostEvidenceLimitation.AIRCRAFT_ID_MISMATCH in wrong.limitations
    assert MasterBindingDefect.TARGET_MISMATCH in wrong.binding_defects
    assert wrong.decision_risk_level is None
    _closed(wrong)

    denied_host = _host(
        idempotency_key="idem-aircraft-denied",
        specialist=DECISION,
        capability=RISK,
        tool_name="get_current_risk_evidence",
        tool_call_id="tool-call-risk-denied",
        resource_id="aircraft",
        operation=GuardReadOperation.GET_ITEM,
        historical_start_utc=None,
        historical_end_utc=None,
        decision_aircraft_id="AC1",
    )
    denied = _execute(
        denied_host,
        _page(record_ids=("AC2",), source_name="aircraft"),
        requested_arguments=(("aircraft_id", "AC2"),),
    )
    denied_result = integrate_host_read_evidence(denied, _auth(specialist=DECISION, expected_aircraft_id="AC1"))
    assert ReadBoundaryDenial.DECISION_TARGET_MISMATCH in denied.denial_reasons
    assert denied_result.observation_status is ReadReceiptVerificationStatus.LOCAL_FAILURE_RECORDED
    assert HostEvidenceLimitation.AIRCRAFT_ID_MISMATCH in denied_result.limitations
    _closed(denied_result)

    callsign_host = _host(
        idempotency_key="idem-callsign",
        specialist=LIVE,
        purpose=MasterPurpose.DISCOVERY,
        capability=OPERATIONAL,
        tool_name="find_aircraft_by_callsign",
        tool_call_id="tool-call-callsign",
        resource_id="aircraft",
        operation=GuardReadOperation.QUERY,
        historical_start_utc=None,
        historical_end_utc=None,
    )
    collided = _execute(
        callsign_host,
        _page(record_ids=("AC1", "AC2"), source_name="aircraft", source_observed_at_utc=None),
        requested_arguments=(("callsign", "UAL123"),),
    )
    collision = integrate_host_read_evidence(
        collided,
        _auth(specialist=LIVE, purpose=MasterPurpose.DISCOVERY, expected_aircraft_id=None),
    )
    assert collision.receipt_status is ReadReceiptVerificationStatus.LOCAL_OBSERVATION_RECORDED
    assert HostEvidenceLimitation.CALLSIGN_COLLISION in collision.limitations
    assert HostEvidenceLimitation.CALLSIGN_NOT_AIRCRAFT_IDENTITY in collision.limitations
    assert collision.operational_claim_admitted is False
    _closed(collision)

    wide_host = _host(idempotency_key="idem-wide")
    wide = _execute(
        wide_host,
        _page(),
        requested_arguments=(("start_utc", WIDE_START), ("end_utc", END), ("aircraft_id", "AC1")),
    )
    wide_result = integrate_host_read_evidence(wide, _auth())
    assert wide.outcome is ReadExecutionOutcome.REJECTED
    assert wide.adapter_invoked is False
    assert HostEvidenceLimitation.HISTORICAL_WINDOW_EXCEEDS_SEVEN_DAYS in wide_result.limitations
    assert wide_result.observation_status is ReadReceiptVerificationStatus.LOCAL_FAILURE_RECORDED
    assert wide_result.verified_zero_established is False
    _closed(wide_result)


def test_swapped_clocks_forged_provenance_and_region_text_are_rejected():
    host = _host(idempotency_key="idem-clocks")
    receipt = _execute(host, _page())
    swapped = integrate_host_read_evidence(
        receipt,
        _auth(),
        expected_clocks=ExpectedObservationClocks(
            accepted_at_utc=SOURCE_OBSERVED,
            execution_started_at_utc=OBSERVED,
            execution_finished_at_utc=OBSERVED,
            source_reported_observed_at_utc=OBSERVED,
        ),
    )
    assert swapped.observation_status is ReadReceiptVerificationStatus.LOCAL_OBSERVATION_RECORDED
    assert swapped.receipt_status is ReadReceiptVerificationStatus.BINDING_MISMATCH
    assert HostEvidenceLimitation.CLOCKS_SWAPPED in swapped.limitations
    assert MasterBindingDefect.TIME_MISMATCH in swapped.binding_defects
    _closed(swapped)

    evidence = Evidence(
        source="historical.encounters",
        source_records=(
            SourceRecord(record_id="SIGMET-CA", source_version="forged-version", event_timestamp_utc=SOURCE_OBSERVED),
        ),
        query_timestamp_utc=SOURCE_OBSERVED,
        freshness_status=FreshnessStatus.FRESH,
        confidence=ConfidenceLevel.UNKNOWN,
        limitations=(),
        tool_call_id="tool-call-forged",
        temporal_scope=TemporalScope.HISTORICAL,
    )
    forged = ToolResult(
        tool_name="list_historical_encounters",
        tool_call_id="tool-call-forged",
        status=ToolResultStatus.SUCCESS,
        temporal_scope=TemporalScope.HISTORICAL,
        data={
            "region": "California",
            "polygon_intersection": True,
            "impacted_aircraft_count": 12,
            "risk_level": "LOW",
        },
        evidence=(evidence,),
        as_of_utc=OBSERVED,
        limitations=(),
        correlation_id="corr-1",
    )
    claim = integrate_host_read_evidence(forged, _auth())
    assert claim.receipt_status is ReadReceiptVerificationStatus.NOT_A_RECEIPT
    assert claim.trace_assessment is not None
    assert claim.trace_assessment.matching_source_version_authenticates_read is False
    assert claim.trace_assessment.matching_timestamp_authenticates_read is False
    assert claim.trace_assessment.source_authenticity.value == "NOT_ESTABLISHED"
    assert HostEvidenceLimitation.COPIED_CORRELATOR_NOT_PROOF in claim.limitations
    assert HostEvidenceLimitation.CLAIMED_LOW_RISK_NOT_ESTABLISHED in claim.limitations
    assert HostEvidenceLimitation.REGIONAL_TEXT_NOT_POLYGON_INTERSECTION in claim.limitations
    assert claim.polygon_intersection_established is False
    assert claim.impacted_aircraft_count_established is False
    assert claim.low_risk_admitted is False
    assert claim.decision_risk_level is None
    assert claim.admission.admission_status is MasterAdmissionStatus.REJECTED
    _closed(claim)

    regional_host = _host(idempotency_key="idem-sigmet", region_codes=frozenset({"CA"}))
    regional = integrate_host_read_evidence(
        _execute(regional_host, _page(record_ids=("SIGMET-CA",))),
        _auth(),
        untrusted_claim=forged,
    )
    assert regional.receipt_status is ReadReceiptVerificationStatus.LOCAL_OBSERVATION_RECORDED
    assert HostEvidenceLimitation.REGIONAL_TEXT_NOT_POLYGON_INTERSECTION in regional.limitations
    assert regional.polygon_intersection_established is False
    assert regional.untrusted_claim_verification is not None
    assert regional.untrusted_claim_verification.status is ReadReceiptVerificationStatus.NOT_A_RECEIPT
    assert regional.trace_assessment is not None
    assert regional.trace_assessment.caller_supplied_trace_authenticates_read is False
    _closed(regional)

    adapter_claim = SimpleNamespace(approved_source_adapter=True, local_fake_is_aviation_evidence=True)
    adapter = integrate_host_read_evidence(adapter_claim, _auth())
    assert adapter.receipt_status is ReadReceiptVerificationStatus.NOT_A_RECEIPT
    assert HostEvidenceLimitation.LOCAL_ADAPTER_NOT_PRODUCTION_PROOF in adapter.limitations
    assert adapter.aviation_evidence_admitted is False
    _closed(adapter)


def test_claimed_low_risk_and_ma5_do_not_emit_a_factual_claim():
    risk_host = _host(
        idempotency_key="idem-low",
        tool_name="summarize_historical_risks",
        tool_call_id="tool-call-low",
        resource_id="historical.risks",
    )
    risk_receipt = _execute(
        risk_host,
        _page(source_name="historical.risks", record_ids=("risk-row",)),
        requested_arguments=(
            ("start_utc", START),
            ("end_utc", END),
            ("aircraft_id", "AC1"),
            ("risk_level", "LOW"),
        ),
    )
    recorded = integrate_host_read_evidence(risk_receipt, _auth())
    assert HostEvidenceLimitation.CLAIMED_LOW_RISK_NOT_ESTABLISHED in recorded.limitations
    assert recorded.decision_risk_level is None
    assert recorded.low_risk_admitted is False
    assert recorded.admission.admission_status is MasterAdmissionStatus.REJECTED
    _closed(recorded)

    context, plan = _plan(_historical_step())
    with pytest.raises(ContractValidationError, match="unsupported_artifact"):
        MasterParallelContractBundle(
            context=context,
            plan=plan,
            evidence_inputs=(
                MasterEvidenceCorroborationInput(
                    MasterEvidenceAdmissionInput(plan.steps[0].invocation, risk_receipt)
                ),
            ),
        )
    empty = MasterParallelContractBundle(context=context, plan=plan, evidence_inputs=())
    handoff = evaluate_master_response_policy(empty)
    rendered = render_master_response(handoff)
    assert handoff.state is MasterResponsePolicyState.INSUFFICIENT_EVIDENCE
    assert MasterResponseLimitation.NO_OPERATIONAL_CLAIM in handoff.limitations
    assert rendered.claims_admitted is False
    assert rendered.status_text == "Supplied evidence is not sufficient for an operational claim."
    assert not any(character.isdigit() for character in rendered.status_text)
    assert "LOW" not in rendered.status_text

    decision_context, decision_plan = _plan(
        MasterProposedStep(
            step_id="dec-1",
            specialist=DECISION,
            purpose=MasterPurpose.ANSWER,
            requirement=MasterRequirement.REQUIRED,
            capability=RISK,
            temporal_intent=MasterRouteTemporalIntent.CURRENT,
        ),
        target_candidate_aircraft_id="ac-1",
    )
    claims, bindings, _payload = decision_absent_risk_case()
    absent = _pack(claims, bindings)
    evidence = MasterEvidenceCorroborationInput(
        MasterEvidenceAdmissionInput(decision_plan.steps[0].invocation, absent)
    )
    checked = corroborate_master_evidence((evidence,)).results[0]
    decision_handoff = evaluate_master_response_policy(
        MasterParallelContractBundle(context=decision_context, plan=decision_plan, evidence_inputs=(evidence,))
    )
    decision_render = render_master_response(decision_handoff)
    assert MasterCorroborationReason.RECORDED_RISK_ABSENCE_NOT_LOW in checked.reason_codes
    assert checked.operationally_eligible is False
    assert decision_handoff.state is MasterResponsePolicyState.INSUFFICIENT_EVIDENCE
    assert decision_render.claims_admitted is False
    assert "LOW" not in decision_render.status_text
    assert not any(character.isdigit() for character in decision_render.status_text)

    low_claims, low_bindings, _low_payload = decision_absent_called_low_case()
    rejected = corroborate_master_evidence(
        (
            MasterEvidenceCorroborationInput(
                MasterEvidenceAdmissionInput(decision_plan.steps[0].invocation, _pack(low_claims, low_bindings))
            ),
        )
    ).results[0]
    assert rejected.corroboration_status is MasterCorroborationStatus.NO_FACTUAL_CLAIMS
    assert "CLAIM_VALUE_MISMATCH" in rejected.rejected_claim_codes
    assert rejected.operationally_eligible is False


def _local_auth() -> MasterInvocationAuthorization:
    return MasterInvocationAuthorization(
        master_request_id="master-req-1",
        invocation_id="inv-1",
        specialist=HISTORY,
        purpose=MasterPurpose.ANSWER,
        requirement=MasterRequirement.REQUIRED,
        correlation_id="corr-1",
        application_request_time_utc="2026-10-20T11:00:00Z",
    )


def _local_receipt(**observation: object):
    request = "2026-10-20T11:00:00Z"
    context_values: dict[str, object] = {
        "master_request_id": "master-req-1",
        "planning_session_id": "plan-1",
        "step_id": "step-1",
        "invocation_id": "inv-1",
        "specialist_invocation_id": "spec-inv-1",
        "specialist": HISTORY,
        "purpose": MasterPurpose.ANSWER,
        "capability": HISTORICAL,
        "principal_ref": "principal-ref-1",
        "policy_revision": "policy-rev-1",
        "resource_id": "historical.encounters",
        "operation": ReadOperation.QUERY,
        "accepted_at_utc": request,
        "deadline_utc": "2026-10-20T18:00:00Z",
        "adapter_identity": "local-historical-adapter",
        "tool_name": "summarize_historical_encounters",
        "tool_call_id": observation.pop("tool_call_id", "tool-call-1"),
        "requested_query": (("start_utc", "2026-10-01T00:00:00Z"), ("end_utc", "2026-10-03T00:00:00Z")),
        "historical_window_start_utc": "2026-10-01T00:00:00Z",
        "historical_window_end_utc": "2026-10-03T00:00:00Z",
        "idempotency_key": observation.pop("idempotency_key", "idem-local-1"),
        "correlation_id": "corr-1",
    }
    context = DescriptiveReadContext(**context_values)  # type: ignore[arg-type]
    row = ObservedSourceRow(record_id="rec-1", source_version="hv-9", event_timestamp_utc="2026-10-02T00:00:00Z")
    observed = {
        "executed_query": context.requested_query,
        "response_status": ToolResultStatus.SUCCESS,
        "rows_returned": 1,
        "rows": (row,),
        "page_index": 0,
        "page_count": 1,
        "page_limit": 25,
        "row_limit": 25,
        "query_clock_utc": "2026-10-20T12:00:00Z",
        "evaluation_clock_utc": "2026-10-20T12:00:05Z",
        "freshness": FreshnessStatus.FRESH,
        "query_id": "query-1",
        "response_source_version": "hv-9",
    }
    observed.update(observation)
    if observed.get("rows") == ():
        observed["rows"] = ()
    host = LocalTrustedReadHost(Clock("2026-10-20T12:01:00Z", "2026-10-20T12:01:01Z"))
    registration = host.register_local_adapter(
        "local-historical-adapter",
        lambda _action: LocalAdapterObservation(**observed),  # type: ignore[arg-type]
    )
    action = host.approve_executor_action(registration, context)
    assert isinstance(action, ApprovedExecutorAction)
    return host, host.execute_read(action)
