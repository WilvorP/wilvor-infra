"""Sprint 4 Live Ops source projection: freshness, pagination, geography."""

from __future__ import annotations

import pytest

from wilvor_ai.contracts import AgentCapability, ContractValidationError, FreshnessStatus
from wilvor_ai.master_contracts import MasterPurpose, MasterSpecialist
from wilvor_ai.master_evidence_admission import MasterSourceAuthenticity
from wilvor_ai.master_read_resource_guard import ReadOperation
from wilvor_ai.sprint3c_scoped_read import (
    GRANT_CONSUMPTION_LIFECYCLE,
    LEDGER_EXECUTION_STATE_STARTED,
    describe_consumed_attempt_read_binding,
)
from wilvor_ai.sprint4_live_ops_authorization import authorize_live_ops_tool
from wilvor_ai.sprint4_live_ops_source import (
    EMPTY_PAGE_NOT_VERIFIED_ZERO,
    GEOGRAPHIC_PROVENANCE_ABSENT,
    LiveOpsCompletenessVerdict,
    LiveOpsFreshnessVerdict,
    LiveOpsGeographicBasis,
    LiveOpsGeographicCoverage,
    REGION_LABEL_NOT_POLYGON,
    build_live_ops_source_report,
    observe_completeness,
    observe_geographic_coverage,
    observe_source_freshness,
    provenance_for_page,
)
from wilvor_operational.linking import Coverage


ACCEPTED = "2026-10-27T16:00:00Z"
DEADLINE = "2026-10-27T20:00:00Z"
NOW = 1_788_661_800


def _auth():
    binding = describe_consumed_attempt_read_binding(
        owner_id="owner-1",
        attempt_token="attempt-1",
        fence_epoch=1,
        grant_nonce="grant-nonce-1",
        idempotency_key="idem-src",
        payload_fingerprint="fingerprint-src",
        ledger_state=LEDGER_EXECUTION_STATE_STARTED,
        grant_consumption_lifecycle=GRANT_CONSUMPTION_LIFECYCLE,
        master_request_id="req-src",
        planning_session_id="plan-src",
        step_id="step-src",
        invocation_id="inv-src",
        specialist=MasterSpecialist.LIVE_OPS,
        purpose=MasterPurpose.ANSWER,
        capability=AgentCapability.RETRIEVE_DETERMINISTIC_OPERATIONAL_CONTEXT,
        principal_ref="principal-1",
        policy_revision="policy-1",
        tool_name="search_current_hazards",
        resource_id="hazards",
        operation=ReadOperation.QUERY,
        region_scope=("CA",),
        accepted_at_utc=ACCEPTED,
        deadline_utc=DEADLINE,
        correlation_id="corr-src",
    )
    return authorize_live_ops_tool(
        binding,
        tool_name="search_current_hazards",
        arguments={"region": "California"},
    )


def test_freshness_uses_source_clock_not_evaluation_time():
    fresh, observed = observe_source_freshness(
        ({"position_time_epoch": NOW - 30, "expires_at_epoch": NOW + 100},),
        now_epoch=NOW,
    )
    stale, _ = observe_source_freshness(
        ({"expires_at_epoch": NOW - 1, "position_time_epoch": NOW - 10},),
        now_epoch=NOW,
    )
    unknown, missing = observe_source_freshness(({"hazard_id": "H1"},), now_epoch=NOW)

    assert fresh is LiveOpsFreshnessVerdict.FRESH
    assert observed is not None
    assert stale is LiveOpsFreshnessVerdict.STALE
    assert unknown is LiveOpsFreshnessVerdict.UNKNOWN
    assert missing is None


def test_empty_page_is_not_verified_zero_and_truncation_is_partial():
    assert observe_completeness(rows_returned=0, truncated=False) is (
        LiveOpsCompletenessVerdict.EMPTY_PAGE
    )
    assert observe_completeness(rows_returned=3, truncated=True) is (
        LiveOpsCompletenessVerdict.PARTIAL
    )
    report = build_live_ops_source_report(
        _auth(),
        now_epoch=NOW,
        records=(),
        provenance=(
            provenance_for_page(
                resource_id="hazards",
                operation=ReadOperation.QUERY,
                coverage=Coverage.BOUNDED_QUERY,
                rows_returned=0,
                truncated=False,
            ),
        ),
    )
    assert report.completeness is LiveOpsCompletenessVerdict.EMPTY_PAGE
    assert report.verified_zero is False
    assert EMPTY_PAGE_NOT_VERIFIED_ZERO in report.limitations
    assert report.source_authenticity is MasterSourceAuthenticity.NOT_ESTABLISHED


def test_california_label_is_not_polygon_intersection():
    coverage, basis, limitations = observe_geographic_coverage(
        spatial_statuses=(),
        region_query="California",
        coordinates_queried=False,
        region_resolved=True,
        spatial_supported=True,
    )
    assert coverage is LiveOpsGeographicCoverage.NOT_ESTABLISHED
    assert basis is LiveOpsGeographicBasis.NOT_ESTABLISHED
    assert REGION_LABEL_NOT_POLYGON in limitations
    assert GEOGRAPHIC_PROVENANCE_ABSENT in limitations


def test_intersection_requires_computed_spatial_status():
    coverage, basis, _limitations = observe_geographic_coverage(
        spatial_statuses=("INTERSECTS",),
        region_query="California",
        coordinates_queried=True,
        region_resolved=True,
        spatial_supported=True,
    )
    mixed, mixed_basis, mixed_limitations = observe_geographic_coverage(
        spatial_statuses=("INTERSECTS", "UNEVALUATED"),
        region_query="California",
        coordinates_queried=True,
        region_resolved=True,
        spatial_supported=True,
    )
    unevaluated, unevaluated_basis, _ = observe_geographic_coverage(
        spatial_statuses=("UNEVALUATED",),
        region_query="California",
        coordinates_queried=True,
        region_resolved=True,
        spatial_supported=True,
    )
    assert coverage is LiveOpsGeographicCoverage.INTERSECTION
    assert basis is LiveOpsGeographicBasis.COMPUTED_GEOMETRY
    assert mixed is LiveOpsGeographicCoverage.INTERSECTION
    assert mixed_basis is LiveOpsGeographicBasis.COMPUTED_GEOMETRY
    assert GEOGRAPHIC_PROVENANCE_ABSENT in mixed_limitations
    assert unevaluated is LiveOpsGeographicCoverage.NOT_ESTABLISHED
    assert unevaluated_basis is LiveOpsGeographicBasis.NOT_ESTABLISHED


def test_scan_provenance_is_rejected():
    with pytest.raises(ContractValidationError, match="scan_not_permitted"):
        provenance_for_page(
            resource_id="aircraft",
            operation=ReadOperation.SCAN,
            coverage=Coverage.FULL_SCAN,
            rows_returned=0,
            truncated=False,
        )


def test_report_does_not_claim_live_aws_or_authenticity():
    report = build_live_ops_source_report(
        _auth(),
        now_epoch=NOW,
        records=({"hazard_id": "H1", "source_version": "v1"},),
        provenance=(
            provenance_for_page(
                resource_id="hazards",
                operation=ReadOperation.GET_ITEM,
                coverage=Coverage.EXACT_PK,
                rows_returned=1,
                truncated=False,
                source_version="v1",
            ),
        ),
        spatial_statuses=("INTERSECTS",),
        intersecting_hazard_ids=("H1",),
        region_query="California",
        coordinates_queried=True,
        region_resolved=True,
        spatial_supported=True,
    )
    assert report.source_authenticity is MasterSourceAuthenticity.NOT_ESTABLISHED
    assert report.region_label_is_polygon is False
    assert report.geographic_coverage is LiveOpsGeographicCoverage.INTERSECTION
    assert report.intersecting_hazard_ids == ("H1",)
    assert FreshnessStatus.UNKNOWN.value
    assert all(item.live_aws_initiated is False for item in report.provenance)
