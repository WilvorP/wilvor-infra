"""Sprint 3D Agent D: authenticated source evidence and factual claims."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from wilvor_ai.contracts import AgentCapability, ContractValidationError, FreshnessStatus
from wilvor_ai.master_authenticated_read_receipt import SOURCE_AUTHENTICATED_READ_AVAILABLE
from wilvor_ai.master_contracts import (
    MasterProductionAuthority,
    MasterPurpose,
    MasterRequirement,
    MasterSpecialist,
)
from wilvor_ai.master_evidence_admission import MasterAdmissionStatus, MasterSourceAuthenticity
from wilvor_ai.master_execution_ledger import MasterExecutionLedgerState
from wilvor_ai.master_host_evidence_integration import PRODUCTION_SOURCE_AUTHENTICATOR_INTEGRATED
from wilvor_ai.master_parallel_contracts import MasterResponseLimitation
from wilvor_ai.master_read_resource_guard import ReadOperation
from wilvor_ai.sprint3c_evidence_response import (
    PRODUCTION_MA4_ADMISSION_AVAILABLE,
    Sprint3cConsumedAttemptBinding,
    Sprint3cEvidenceResponseInput,
    Sprint3cJoinDefect,
    join_sprint3c_evidence_response,
)
from wilvor_ai.sprint3c_grant_ledger import DurableConsumedAttempt
from wilvor_ai.sprint3d_source_evidence import (
    ABSENT_RISK_IS_LOW_RISK,
    EMPTY_PAGE_CREATES_VERIFIED_ZERO,
    FACTUAL_RECORD_ADMISSION_PREREQUISITES,
    GLOBAL_EVIDENCE_ADMISSION_ENABLED,
    SOURCE_LABEL_ESTABLISHES_GEOGRAPHY,
    TRUSTED_AWS_RECEIPT_FIELDS,
    ClockVerdict,
    CompletenessVerdict,
    CorrectnessVerdict,
    EvidenceVerdict,
    FreshnessVerdict,
    GeographicBasis,
    GeographicCoverage,
    ProofObligation,
    RejectedClaim,
    SourceAuthenticity,
    SourceEvidenceHost,
    SourceReadInvocation,
    SupportedFactualClaim,
    TrustedAwsSourceReceipt,
    evaluate_source_evidence,
    shared_contract_blockers,
    trusted_receipt_interface,
    valid_admission_prerequisites,
    AwsSourcePage,
    ObservedSourceRecord,
)
from wilvor_ai.master_contracts import MasterInvocationAuthorization


ACCEPTED = "2026-10-20T12:00:00Z"
QUERY_CLOCK = "2026-10-20T12:00:01Z"
OBSERVATION = "2026-10-20T12:00:02Z"
EVALUATION = "2026-10-20T12:00:03Z"
DEADLINE = "2026-10-20T18:00:00Z"
SQUARE = ((0.0, 0.0), (2.0, 0.0), (2.0, 2.0), (0.0, 2.0), (0.0, 0.0))
OPERATIONAL = AgentCapability.RETRIEVE_DETERMINISTIC_OPERATIONAL_CONTEXT


class Clock:
    def __init__(self, *instants: str) -> None:
        self.instants = list(instants)

    def now_utc(self) -> str:
        if not self.instants:
            raise AssertionError("evidence clock exhausted")
        return self.instants.pop(0)


def _grant(**overrides: object) -> DurableConsumedAttempt:
    values: dict[str, object] = {
        "owner_id": "worker-a",
        "attempt_token": "attempt-token-1",
        "fence_epoch": 1,
        "grant_nonce": "grant-nonce-1",
        "idempotency_key": "idem-1",
        "payload_fingerprint": "fingerprint-1",
        "state": MasterExecutionLedgerState.STARTED,
        "master_request_id": "request-1",
        "planning_session_id": "session-1",
        "step_id": "step-1",
        "invocation_id": "invocation-1",
        "principal_ref": "principal-1",
        "policy_revision": "policy-rev-1",
        "specialist": MasterSpecialist.LIVE_OPS,
        "purpose": MasterPurpose.ANSWER,
        "capability": OPERATIONAL,
        "resource_scope": "aircraft.state",
        "region_scope": "CA",
        "deadline_utc": DEADLINE,
        "correlation_id": "correlation-1",
        "lease_expires_at_utc": DEADLINE,
        "accepted_at_utc": ACCEPTED,
        "ledger_observed_at_utc": "2026-10-20T12:00:04Z",
    }
    values.update(overrides)
    return DurableConsumedAttempt(**values)  # type: ignore[arg-type]


def _invocation(**overrides: object) -> SourceReadInvocation:
    values: dict[str, object] = {
        "master_request_id": "request-1",
        "planning_session_id": "session-1",
        "step_id": "step-1",
        "invocation_id": "invocation-1",
        "tool_call_id": "tool-call-1",
        "principal_ref": "principal-1",
        "policy_revision": "policy-rev-1",
        "resource_id": "aircraft.state",
        "operation": ReadOperation.QUERY,
        "correlation_id": "correlation-1",
        "requested_query_id": "query-1",
        "expected_source_version": "sv-1",
        "expected_aircraft_id": "AC1",
        "expected_hazard_id": "HZ1",
    }
    values.update(overrides)
    return SourceReadInvocation(**values)  # type: ignore[arg-type]


def _record(**overrides: object) -> ObservedSourceRecord:
    values: dict[str, object] = {
        "record_id": "rec-ac-1",
        "source_version": "sv-1",
        "aircraft_id": "AC1",
        "longitude": 1.0,
        "latitude": 1.0,
    }
    values.update(overrides)
    return ObservedSourceRecord(**values)  # type: ignore[arg-type]


def _hazard(**overrides: object) -> ObservedSourceRecord:
    values: dict[str, object] = {
        "record_id": "rec-hz-1",
        "source_version": "sv-1",
        "hazard_id": "HZ1",
        "ring": SQUARE,
    }
    values.update(overrides)
    return ObservedSourceRecord(**values)  # type: ignore[arg-type]


def _page(**overrides: object) -> AwsSourcePage:
    values: dict[str, object] = {
        "source_name": "aircraft-state",
        "source_version": "sv-1",
        "query_id": "query-1",
        "records": (_record(), _hazard()),
        "evaluation_clock_utc": EVALUATION,
        "query_clock_utc": QUERY_CLOCK,
        "freshness": FreshnessStatus.FRESH,
        "page_index": 0,
        "page_count": 1,
        "truncated": False,
        "has_more": False,
    }
    values.update(overrides)
    return AwsSourcePage(**values)  # type: ignore[arg-type]


def _run(
    page: AwsSourcePage | None = None,
    *,
    grant: DurableConsumedAttempt | None = None,
    invocation: SourceReadInvocation | None = None,
    calls: list[str] | None = None,
    source_name: str = "aircraft-state",
):
    host = SourceEvidenceHost(Clock(OBSERVATION))
    produced = page if page is not None else _page()

    def execute(_invocation: SourceReadInvocation) -> AwsSourcePage:
        if calls is not None:
            calls.append("executed")
        return produced

    approval = host.approve_aws_adapter("aws-aircraft-state", source_name, execute)
    receipt = host.execute(approval, grant if grant is not None else _grant(), invocation if invocation is not None else _invocation())
    return host, receipt, evaluate_source_evidence(host, receipt)


def test_closed_constants_and_shared_authenticity_stay_closed():
    assert GLOBAL_EVIDENCE_ADMISSION_ENABLED is False
    assert EMPTY_PAGE_CREATES_VERIFIED_ZERO is False
    assert ABSENT_RISK_IS_LOW_RISK is False
    assert SOURCE_LABEL_ESTABLISHES_GEOGRAPHY is False
    assert PRODUCTION_MA4_ADMISSION_AVAILABLE is False
    assert SOURCE_AUTHENTICATED_READ_AVAILABLE is False
    assert PRODUCTION_SOURCE_AUTHENTICATOR_INTEGRATED is False
    assert list(MasterSourceAuthenticity) == [MasterSourceAuthenticity.NOT_ESTABLISHED]
    assert list(MasterAdmissionStatus) == [
        MasterAdmissionStatus.REJECTED,
        MasterAdmissionStatus.CONTRACT_ONLY,
        MasterAdmissionStatus.BINDING_UNRESOLVED,
    ]
    blockers = shared_contract_blockers()
    assert "MasterSourceAuthenticity has only NOT_ESTABLISHED" in blockers
    assert "an empty page is not a verified zero" in blockers
    assert "missing risk is not LOW" in blockers
    assert trusted_receipt_interface() == TRUSTED_AWS_RECEIPT_FIELDS


def test_positive_observation_admits_record_and_computed_intersection():
    host, receipt, assessment = _run()
    assert receipt.adapter_executed is True
    assert receipt.consumed_grant_bound is True
    assert receipt.invocation_bound is True
    assert receipt.grant_region_text == "CA"
    assert receipt.verified_zero_established is False
    assert receipt.polygon_intersection_established is False
    assert receipt.source_authenticity is MasterSourceAuthenticity.NOT_ESTABLISHED
    assert assessment.verdict is EvidenceVerdict.ADMITTED
    assert assessment.authenticity is SourceAuthenticity.ESTABLISHED
    assert assessment.freshness is FreshnessVerdict.FRESH
    assert assessment.correctness is CorrectnessVerdict.CONSISTENT
    assert assessment.clocks is ClockVerdict.DISTINCT
    assert assessment.completeness is CompletenessVerdict.COMPLETE
    assert assessment.geographic_coverage is GeographicCoverage.INTERSECTION
    assert assessment.geographic_basis is GeographicBasis.COMPUTED_GEOMETRY
    assert assessment.admitted_claims == (
        SupportedFactualClaim.OBSERVED_SOURCE_RECORD,
        SupportedFactualClaim.GEOMETRY_INTERSECTION,
    )
    assert assessment.factual_claims_admitted is True
    assert assessment.operational_claim_admitted is False
    assert assessment.verified_zero_established is False
    assert assessment.low_risk_admitted is False
    assert assessment.impact_count_admitted is False
    assert assessment.regional_text_used_as_geography is False
    assert assessment.source_label_used_as_geography is False
    assert RejectedClaim.LOW_RISK in assessment.rejected_claims
    assert "grant_region_text_ignored" in assessment.ignored_non_proofs
    assert "source_label_ignored" in assessment.ignored_non_proofs
    assert set(FACTUAL_RECORD_ADMISSION_PREREQUISITES).issubset(assessment.met_obligations)
    assert MasterResponseLimitation.NO_OPERATIONAL_CLAIM.value in assessment.ma5_limitations
    assert MasterResponseLimitation.EXECUTION_DENIED.value in assessment.ma5_limitations
    assert MasterResponseLimitation.PRODUCTION_AUTHORITY_DENIED.value in assessment.ma5_limitations
    assert MasterResponseLimitation.SOURCE_AUTHENTICITY_NOT_ESTABLISHED.value not in assessment.ma5_limitations
    assert "Observed source records rec-ac-1, rec-hz-1 from aircraft-state." in assessment.status_text
    assert "Observed geometry intersection for aircraft AC1 and hazard HZ1." in assessment.status_text
    assert "california" not in assessment.status_text.casefold()
    assert "low risk" not in assessment.status_text.casefold()
    assert assessment.production_authority is MasterProductionAuthority.DENIED
    assert bool(assessment) is False
    assert bool(receipt) is False
    replayed = evaluate_source_evidence(host, receipt)
    assert replayed.verdict is EvidenceVerdict.REPLAYED
    assert replayed.admitted_claims == ()


def test_geometry_non_intersection_is_not_an_impact():
    page = _page(records=(_record(longitude=5.0, latitude=5.0), _hazard()))
    _host, _receipt, assessment = _run(page)
    assert assessment.geographic_coverage is GeographicCoverage.NON_INTERSECTION
    assert assessment.geographic_basis is GeographicBasis.COMPUTED_GEOMETRY
    assert SupportedFactualClaim.GEOMETRY_NON_INTERSECTION in assessment.admitted_claims
    assert SupportedFactualClaim.GEOMETRY_INTERSECTION not in assessment.admitted_claims
    assert "non-intersection" in assessment.status_text
    assert "impacted aircraft" not in assessment.status_text.casefold()


def test_forged_subjects_and_cross_host_receipts_are_rejected():
    host, receipt, _assessment = _run()
    other = SourceEvidenceHost(Clock(OBSERVATION))
    for subject in (
        {"approved_source_adapter": True, "region": "California", "impacted_aircraft_count": 4},
        SimpleNamespace(approved_source_adapter=True, source_name="SIGMET", claimed_polygon_intersection=True),
        None,
        "California",
    ):
        forged = evaluate_source_evidence(host, subject)
        assert forged.verdict is EvidenceVerdict.FORGED
        assert forged.authenticity is SourceAuthenticity.NOT_ESTABLISHED
        assert forged.admitted_claims == ()
        assert forged.verified_zero_established is False
        assert RejectedClaim.FORGED_EVIDENCE in forged.rejected_claims
    foreign = evaluate_source_evidence(other, receipt)
    assert foreign.verdict is EvidenceVerdict.FORGED
    with pytest.raises(ContractValidationError, match="receipt_not_host_issued"):
        TrustedAwsSourceReceipt(
            schema_version="wilvor.ai.sprint3d.source_evidence.v1",
            receipt_id="forged",
            adapter_id="aws-aircraft-state",
            source_name="aircraft-state",
            reported_source_name="aircraft-state",
            source_identity_match=True,
            source_version="sv-1",
            query_id="query-1",
            records=(),
            master_request_id="request-1",
            planning_session_id="session-1",
            step_id="step-1",
            invocation_id="invocation-1",
            tool_call_id="tool-call-1",
            principal_ref="principal-1",
            policy_revision="policy-rev-1",
            grant_nonce="grant-nonce-1",
            attempt_token="attempt-token-1",
            resource_id="aircraft.state",
            echoed_resource_id=None,
            scope_match=True,
            operation=ReadOperation.QUERY,
            grant_region_text="California",
            accepted_at_utc=ACCEPTED,
            observation_clock_utc=OBSERVATION,
            evaluation_clock_utc=EVALUATION,
            query_clock_utc=QUERY_CLOCK,
            freshness=FreshnessStatus.FRESH,
            page_index=0,
            page_count=1,
            truncated=False,
            has_more=False,
            rows_returned=0,
            contradictory_row_count=False,
            adapter_executed=True,
            consumed_grant_bound=True,
            invocation_bound=True,
            expected_source_version="sv-1",
            expected_aircraft_id="AC1",
            expected_hazard_id="HZ1",
            requested_record_id=None,
            requested_query_id="query-1",
            requested_advice=None,
            untrusted_region_text="California",
            untrusted_claimed_intersection=True,
            untrusted_claimed_impact_count=4,
            untrusted_claimed_risk="LOW",
            untrusted_claimed_verified_zero=True,
            _seal=object(),
        )
    with pytest.raises(ContractValidationError, match="receipt_not_serializable"):
        receipt.__reduce__()


def test_unapproved_adapter_mismatched_invocation_and_replayed_grant_do_not_run():
    host = SourceEvidenceHost(Clock(OBSERVATION, OBSERVATION))
    calls: list[str] = []

    def execute(_invocation: SourceReadInvocation) -> AwsSourcePage:
        calls.append("executed")
        return _page()

    approval = host.approve_aws_adapter("aws-aircraft-state", "aircraft-state", execute)
    with pytest.raises(ContractValidationError, match="consumed_grant_required"):
        host.execute(approval, {"grant_nonce": "grant-nonce-1", "consumed": True}, _invocation())
    assert calls == []
    with pytest.raises(ContractValidationError, match="invocation_mismatch"):
        host.execute(approval, _grant(), _invocation(invocation_id="invocation-2", tool_call_id="tool-call-2"))
    assert calls == []
    with pytest.raises(ContractValidationError, match="scan_not_permitted"):
        _invocation(operation=ReadOperation.SCAN)
    with pytest.raises(ContractValidationError, match="adapter_not_approved"):
        host.execute(SimpleNamespace(adapter_id="aws-aircraft-state"), _grant(), _invocation())
    receipt = host.execute(approval, _grant(), _invocation())
    assert calls == ["executed"]
    second = host.approve_aws_adapter("aws-aircraft-state-2", "aircraft-state", execute)
    with pytest.raises(ContractValidationError, match="grant_replayed"):
        host.execute(second, _grant(), _invocation())
    assert calls == ["executed"]
    assert receipt.invocation_id == "invocation-1"


def test_stale_partial_and_conflicting_observations_stay_unadmitted():
    _host, receipt, stale = _run(_page(freshness=FreshnessStatus.STALE))
    assert stale.authenticity is SourceAuthenticity.ESTABLISHED
    assert stale.freshness is FreshnessVerdict.STALE
    assert stale.geographic_coverage is GeographicCoverage.INTERSECTION
    assert stale.admitted_claims == ()
    assert stale.verdict is EvidenceVerdict.WITHHELD
    assert RejectedClaim.STALE_OBSERVATION in stale.rejected_claims
    assert "intersection" not in stale.status_text
    assert receipt.freshness is FreshnessStatus.STALE

    _host, _receipt, partial = _run(_page(truncated=True, has_more=True, page_count=2))
    assert partial.authenticity is SourceAuthenticity.ESTABLISHED
    assert partial.completeness is CompletenessVerdict.PARTIAL
    assert partial.freshness is FreshnessVerdict.FRESH
    assert partial.admitted_claims == ()
    assert RejectedClaim.PARTIAL_OBSERVATION in partial.rejected_claims

    conflict_page = _page(
        records=(
            _record(),
            _record(record_id="rec-ac-1", source_version="sv-2", longitude=5.0, latitude=5.0),
        )
    )
    _host, _receipt, conflict = _run(conflict_page)
    assert conflict.authenticity is SourceAuthenticity.ESTABLISHED
    assert conflict.correctness is CorrectnessVerdict.CONFLICTING
    assert conflict.geographic_coverage is GeographicCoverage.NOT_ESTABLISHED
    assert conflict.admitted_claims == ()
    assert RejectedClaim.CONFLICTING_OBSERVATION in conflict.rejected_claims


def test_empty_page_is_not_a_verified_zero_and_missing_risk_is_not_low():
    empty = _page(
        records=(),
        claimed_verified_zero=True,
        claimed_risk_level="LOW",
    )
    _host, _receipt, assessment = _run(
        empty,
        invocation=_invocation(expected_aircraft_id=None, expected_hazard_id=None),
    )
    assert assessment.authenticity is SourceAuthenticity.ESTABLISHED
    assert assessment.completeness is CompletenessVerdict.EMPTY_PAGE
    assert assessment.verified_zero_established is False
    assert assessment.low_risk_admitted is False
    assert assessment.admitted_claims == ()
    assert RejectedClaim.VERIFIED_ZERO in assessment.rejected_claims
    assert RejectedClaim.EMPTY_PAGE_ZERO in assessment.rejected_claims
    assert RejectedClaim.LOW_RISK in assessment.rejected_claims
    assert "verified zero" not in assessment.status_text.casefold()
    assert "low risk" not in assessment.status_text.casefold()

    advised = _page(claimed_impacted_aircraft_count=4, claimed_risk_level="LOW")
    _host, _receipt, advice = _run(
        advised,
        invocation=_invocation(requested_advice="divert immediately"),
    )
    assert advice.factual_claims_admitted is True
    assert advice.operational_claim_admitted is False
    assert advice.impact_count_admitted is False
    assert advice.low_risk_admitted is False
    assert RejectedClaim.OPERATIONAL_ADVICE in advice.rejected_claims
    assert RejectedClaim.IMPACT_COUNT in advice.rejected_claims
    assert "divert" not in advice.status_text.casefold()
    assert "4" not in advice.status_text
    assert "low risk" not in advice.status_text.casefold()


def test_california_text_and_source_label_are_not_geographic_proof():
    page = _page(
        source_name="sigmet-bulletin",
        records=(_record(aircraft_id=None, longitude=None, latitude=None),),
        region_text="California",
        claimed_polygon_intersection=True,
        claimed_impacted_aircraft_count=9,
    )
    _host, receipt, assessment = _run(
        page,
        source_name="sigmet-bulletin",
        invocation=_invocation(expected_aircraft_id="AC1", expected_hazard_id="HZ1"),
    )
    assert receipt.untrusted_region_text == "California"
    assert receipt.untrusted_claimed_intersection is True
    assert assessment.geographic_coverage is GeographicCoverage.NOT_ESTABLISHED
    assert assessment.geographic_basis is GeographicBasis.NOT_ESTABLISHED
    assert assessment.regional_text_used_as_geography is False
    assert assessment.source_label_used_as_geography is False
    assert SupportedFactualClaim.GEOMETRY_INTERSECTION not in assessment.admitted_claims
    assert SupportedFactualClaim.OBSERVED_SOURCE_RECORD in assessment.admitted_claims
    assert RejectedClaim.REGIONAL_TEXT_GEOGRAPHY in assessment.rejected_claims
    assert RejectedClaim.SOURCE_LABEL_GEOGRAPHY in assessment.rejected_claims
    assert RejectedClaim.IMPACT_COUNT in assessment.rejected_claims
    assert "california" not in assessment.status_text.casefold()
    assert "intersection" not in assessment.status_text.casefold()
    assert "9" not in assessment.status_text


def test_version_absence_mismatch_and_clock_collision_do_not_admit():
    absent = _page(source_version=None, records=(_record(source_version=None), _hazard(source_version=None)))
    _host, _receipt, missing = _run(
        absent,
        invocation=_invocation(expected_source_version=None, expected_aircraft_id=None, expected_hazard_id=None),
    )
    assert missing.authenticity is SourceAuthenticity.ESTABLISHED
    assert ProofObligation.SOURCE_VERSION_PRESENT in missing.unmet_obligations
    assert missing.admitted_claims == ()

    mismatched = _page(source_version="sv-2", records=(_record(source_version="sv-2"), _hazard(source_version="sv-2")))
    _host, _receipt, conflict = _run(mismatched)
    assert conflict.correctness is CorrectnessVerdict.CONFLICTING
    assert conflict.authenticity is SourceAuthenticity.ESTABLISHED
    assert conflict.admitted_claims == ()

    collided = _page(evaluation_clock_utc=ACCEPTED)
    _host, _receipt, clocks = _run(
        collided,
        invocation=_invocation(expected_aircraft_id=None, expected_hazard_id=None),
    )
    assert clocks.freshness is FreshnessVerdict.FRESH
    assert clocks.clocks is ClockVerdict.COLLISION
    assert clocks.authenticity is SourceAuthenticity.ESTABLISHED
    assert clocks.admitted_claims == ()
    assert ProofObligation.CLOCKS_DISTINCT in clocks.unmet_obligations
    assert ProofObligation.FRESHNESS_CURRENT in clocks.met_obligations


def test_sprint3c_join_does_not_promote_a_trusted_receipt():
    _host, receipt, assessment = _run()
    assert assessment.factual_claims_admitted is True
    attempt = Sprint3cConsumedAttemptBinding(
        master_request_id="request-1",
        planning_session_id="session-1",
        step_id="step-1",
        invocation_id="invocation-1",
        principal_ref="principal-1",
        policy_revision="policy-rev-1",
        owner_id="worker-a",
        attempt_token="attempt-token-1",
        fence_epoch=1,
        grant_nonce="grant-nonce-1",
        idempotency_key="idem-1",
        payload_fingerprint="fingerprint-1",
        ledger_state="STARTED",
        resource_id="aircraft.state",
        operation=ReadOperation.QUERY,
        adapter_identity="nonproduction:LIVE_OPS:get_aircraft_operational_context:aircraft.state:QUERY",
        tool_name="get_aircraft_operational_context",
        tool_call_id="tool-call-1",
        accepted_at_utc=ACCEPTED,
        deadline_utc=DEADLINE,
        specialist=MasterSpecialist.LIVE_OPS,
        purpose=MasterPurpose.ANSWER,
        specialist_request_id="spec-req-1",
        correlation_id="correlation-1",
        region_scope=("CA",),
    )
    authorization = MasterInvocationAuthorization(
        master_request_id="request-1",
        invocation_id="invocation-1",
        specialist=MasterSpecialist.LIVE_OPS,
        purpose=MasterPurpose.ANSWER,
        requirement=MasterRequirement.REQUIRED,
        specialist_request_id="spec-req-1",
        correlation_id="correlation-1",
        application_request_time_utc=ACCEPTED,
    )
    joined = join_sprint3c_evidence_response(
        Sprint3cEvidenceResponseInput(
            authorization=authorization,
            consumed_attempt=attempt,
            read_receipt=receipt,
        )
    )
    assert Sprint3cJoinDefect.TRUSTED_AWS_RECEIPT_NOT_3C_ADMISSION in joined.defects
    assert Sprint3cJoinDefect.PRODUCTION_AUTHENTICITY_UNAVAILABLE in joined.defects
    assert joined.production_ma4_admission is False
    assert joined.claims_admitted is False
    assert joined.authenticated_source_read_available is False
    assert joined.polygon_intersection_established is False
    assert joined.scoped_read_adapter_accepted is False
    assert bool(joined) is False


def test_admission_prerequisites_are_the_record_obligations():
    assert valid_admission_prerequisites() == tuple(item.value for item in FACTUAL_RECORD_ADMISSION_PREREQUISITES)
    assert "APPROVED_EXECUTED_ADAPTER" in valid_admission_prerequisites()
    assert "CONSUMED_GRANT_BOUND" in valid_admission_prerequisites()
    assert "INVOCATION_BOUND" in valid_admission_prerequisites()
    assert "QUERY_OR_RECORD_IDENTITY" in valid_admission_prerequisites()
    assert "FRESHNESS_CURRENT" in valid_admission_prerequisites()
    assert "GEOMETRY_INTERSECTION" not in valid_admission_prerequisites()
