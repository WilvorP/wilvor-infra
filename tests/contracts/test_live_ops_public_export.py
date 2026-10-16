"""LO3A exports LO2 decisions into public v1 DTOs without reverifying them."""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from wilvor_ai.contracts import (
    ConfidenceLevel,
    Evidence,
    FreshnessStatus,
    SourceRecord,
    TemporalScope,
    ToolResult,
    ToolResultStatus,
)
from wilvor_ai.live_ops_claims import (
    AircraftObservedStateClaim,
    AircraftProjectionStateClaim,
    AlertRecordClaim,
    CallsignMatchSetClaim,
    ConfirmedEncounterClaim,
    ConfirmedImpactClaim,
    ContextRootAbsentClaim,
    CoverageLimitationClaim,
    CrossToolContradictionClaim,
    CurrentnessClaim,
    HazardRegionIntersectionClaim,
    LiveOpsAdvisoryMode,
    LiveOpsContradictionSubjectKind,
    LiveOpsCountMetric,
    LiveOpsCountMode,
    LiveOpsCurrentnessPredicate,
    LiveOpsExecutedScope,
    LiveOpsLimitationCode,
    LiveOpsObservedField,
    LiveOpsProjectionField,
    LiveOpsRiskMode,
    ModelVisibleTruncationClaim,
    QueryResultZeroClaim,
    RecommendationSetClaim,
    RiskStateClaim,
    ScopedCountClaim,
)
from wilvor_ai.live_ops_evidence_verifier import (
    LiveOpsVerificationOutcome,
    verify_live_ops_claims,
)
from wilvor_ai.live_ops_public_contracts import (
    LIVE_OPS_ANSWER_NONE_VERIFIED,
    LIVE_OPS_ANSWER_TOOL_FAILED,
    LIVE_OPS_ANSWER_UNAVAILABLE,
    LiveOpsAnswerOrigin,
    LiveOpsFailureReason,
    LiveOpsPublicClaim,
    LiveOpsPublicClaimKind,
    LiveOpsDiscoveryStatus,
    LiveOpsPublicClaimVerdict,
    LiveOpsPublicLimitationCode,
    LiveOpsPublicRenderingPosture,
    LiveOpsPublicVerificationOutcome,
    LiveOpsRunFailure,
    LiveOpsRunResult,
    LiveOpsRunStatus,
    live_ops_reference_utc,
)
from wilvor_ai.live_ops_public_export import (
    LIVE_OPS_EXPORT_SCHEMA_VERSION,
    LiveOpsExportError,
    LiveOpsExportErrorCode,
    export_live_ops_evidence,
)
from wilvor_ai.live_ops_specialist_runtime_contracts import (
    LiveOpsDispatchResult,
    LiveOpsDispatchStatus,
    LiveOpsRuntimeErrorCode,
    LiveOpsToolInvocation,
)
from wilvor_ai.live_ops_tool_projection import project_live_ops_tool_result


REPO_ROOT = Path(__file__).resolve().parents[2]
SNAPSHOT_PATH = REPO_ROOT / "tests" / "fixtures" / "live_ops_public_claim_snapshots.json"
EXPORTER_PATH = REPO_ROOT / "functions" / "shared" / "wilvor_ai" / "live_ops_public_export.py"
PUBLIC_PATH = REPO_ROOT / "functions" / "shared" / "wilvor_ai" / "live_ops_public_contracts.py"

NOW = 1_788_661_800
CORRELATION = "corr-export-1"
TOOL_AS_OF = "2019-05-01T00:00:00Z"
QUERY_TIME = "2021-06-01T00:00:00Z"
SOURCE_OBSERVED = "2020-01-01T00:00:00Z"
NO_SNAPSHOT = (
    "DynamoDB reads are independently observed; this composition is not "
    "a transactional cross-table snapshot."
)
PROBE_ANSWER = "Synthetic renderer probe."


def _evidence(tool_call_id: str) -> Evidence:
    return Evidence(
        source="synthetic-export",
        source_records=(
            SourceRecord(
                record_id="rec-export-1",
                source_version="SV-1",
                event_timestamp_utc=SOURCE_OBSERVED,
            ),
        ),
        query_timestamp_utc=QUERY_TIME,
        freshness_status=FreshnessStatus.UNKNOWN,
        confidence=ConfidenceLevel.LOW,
        limitations=("FRESHNESS_NOT_ESTABLISHED",),
        tool_call_id=tool_call_id,
        temporal_scope=TemporalScope.CURRENT,
    )


def _result(
    tool_name: str,
    tool_call_id: str,
    data: dict,
    *,
    status: ToolResultStatus = ToolResultStatus.SUCCESS,
    limitations: tuple[str, ...] = (NO_SNAPSHOT,),
    evidence: tuple[Evidence, ...] | None = None,
) -> ToolResult:
    if evidence is None:
        evidence = () if status is ToolResultStatus.NOT_FOUND else (_evidence(tool_call_id),)
    payload = dict(data)
    payload.setdefault("as_of_epoch", NOW)
    return ToolResult(
        tool_name=tool_name,
        tool_call_id=tool_call_id,
        status=status,
        temporal_scope=TemporalScope.CURRENT,
        data=payload,
        evidence=evidence,
        as_of_utc=TOOL_AS_OF,
        limitations=limitations,
        correlation_id=CORRELATION,
    )


def _invoke(result: ToolResult, executed: dict) -> LiveOpsToolInvocation:
    return LiveOpsToolInvocation(
        tool_name=result.tool_name,
        tool_call_id=result.tool_call_id,
        requested_arguments=dict(executed),
        executed_arguments=dict(executed),
        canonical_key=result.tool_name + ":" + result.tool_call_id,
        raw_tool_result=result,
        model_projection=project_live_ops_tool_result(result),
    )


def _completed(*invocations: LiveOpsToolInvocation) -> LiveOpsDispatchResult:
    return LiveOpsDispatchResult(
        status=LiveOpsDispatchStatus.COMPLETED,
        invocations=invocations,
        planned_tool_call_ids=tuple(item.tool_call_id for item in invocations),
    )


def _ledger(*invocations: LiveOpsToolInvocation, correlation_id: str = CORRELATION, now_epoch: int = NOW):
    from wilvor_ai.live_ops_evidence_verifier import LiveOpsTrustedEvidenceLedger

    return LiveOpsTrustedEvidenceLedger(
        correlation_id=correlation_id,
        now_epoch=now_epoch,
        dispatches=(_completed(*invocations),),
    )


def _verified(claims, *invocations):
    ledger = _ledger(*invocations)
    submitted = tuple(claims)
    result = verify_live_ops_claims(submitted, ledger)
    return result, ledger, submitted


def _require_all(result) -> None:
    assert result.outcome is LiveOpsVerificationOutcome.ALL_VERIFIED, [
        (item.claim_index, item.verdict.value, list(item.codes)) for item in result.assessments
    ]


def _impact_context(*, advisory: bool) -> dict:
    encounter = {
        "encounter_id": "EN-1",
        "aircraft_id": "AC-1",
        "hazard_id": "HZ-1",
        "hazard_source_version": "SV-1",
    }
    context = {"encounter_is_current": True, "encounter": encounter}
    if advisory:
        context.update(
            {
                "risk_link": {"state": "PRESENT"},
                "risk": {
                    "risk_id": "RISK-1",
                    "encounter_id": "EN-1",
                    "risk_level": "HIGH",
                    "risk_score": 2,
                },
                "recommendation_link": {"state": "PRESENT"},
                "recommendations": [{"recommendation_id": "REC-1"}],
                "alert_link": {"state": "PRESENT"},
                "alerts": [
                    {
                        "alert_id": "AL-1",
                        "alert_state": "ACTIVE",
                        "alert_type": "IMPACT",
                    }
                ],
            }
        )
    return context


def _impact_data(*, advisory: bool = False) -> dict:
    return {
        "impact_count": 1,
        "impacts": [
            {
                "encounter_id": "EN-1",
                "aircraft_id": "AC-1",
                "hazard_id": "HZ-1",
                "source_version": "SV-1",
                "aircraft_is_current": True,
                "encounter_context": _impact_context(advisory=advisory),
            }
        ],
    }


def _impact_claim(tool_call_id: str = "call-impacts-1") -> ConfirmedImpactClaim:
    return ConfirmedImpactClaim(
        tool_call_id=tool_call_id,
        tool_name="search_current_impacts",
        aircraft_id="AC-1",
        hazard_id="HZ-1",
        source_version="SV-1",
        encounter_id="EN-1",
    )


def _impact_invocation(tool_call_id: str = "call-impacts-1", *, advisory: bool = False):
    return _invoke(
        _result("search_current_impacts", tool_call_id, _impact_data(advisory=advisory)),
        {"hazard_ids": ["HZ-1"]},
    )


def _callsign_data(callsign: str, aircraft_ids: tuple[str, ...], *, limit: int | None = None) -> dict:
    payload = {
        "callsign": callsign,
        "match_count": len(aircraft_ids),
        "matches": [
            {"aircraft_is_current": True, "aircraft": {"aircraft_id": aircraft_id}}
            for aircraft_id in aircraft_ids
        ],
    }
    if limit is not None:
        payload["retrieval"] = [{"source": "gsi", "limit": limit}]
    return payload


def _callsign_invocation(tool_call_id: str, callsign: str, aircraft_ids: tuple[str, ...], *, limit: int | None = None):
    return _invoke(
        _result("find_aircraft_by_callsign", tool_call_id, _callsign_data(callsign, aircraft_ids, limit=limit)),
        {"callsign": callsign},
    )


def _region() -> dict:
    return {
        "resolved": True,
        "region_type": "US_STATE",
        "spatial_supported": True,
        "geometry_type": "Polygon",
        "code": "US-CA",
        "name": "California",
        "query": "California",
    }


def _hazard_row(hazard_id: str, source_version: str, *, spatial: str = "INTERSECTS") -> dict:
    return {
        "hazard": {"hazard_id": hazard_id, "source_version": source_version},
        "hazard_is_current": True,
        "spatial_status": spatial,
    }


def _golden_keys() -> dict[str, set[str]]:
    document = json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))
    return {kind: set(payload) for kind, payload in document["claims"].items()}


def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    return imported


def test_all_verified_impact_preserves_claim_clock_and_limitations():
    invocation = _impact_invocation()
    result, ledger, submitted = _verified((_impact_claim(),), invocation)
    _require_all(result)
    exported = export_live_ops_evidence(result, ledger, submitted)
    reference_utc = live_ops_reference_utc(NOW)

    assert exported.schema_version == LIVE_OPS_EXPORT_SCHEMA_VERSION
    assert exported.verification_outcome is LiveOpsPublicVerificationOutcome.ALL_VERIFIED
    assert exported.rendering_posture is LiveOpsPublicRenderingPosture.COMPLETE_VERIFIED_SET
    assert exported.diagnostics == ()
    assert exported.structural_codes == ()
    assert exported.discoveries == ()
    assert exported.verified_entity_refs == ()
    assert len(exported.verified_claims) == 1
    claim = exported.verified_claims[0]
    assert claim.to_dict() == result.verified_claims[0].to_dict()
    assert LiveOpsPublicClaim.from_dict(claim.to_dict()) == claim
    assert exported.correlation_id == CORRELATION
    assert exported.reference_epoch == NOW
    assert exported.reference_utc == reference_utc
    assert exported.reference_utc != TOOL_AS_OF
    assert exported.reference_utc != QUERY_TIME
    assert exported.reference_utc != SOURCE_OBSERVED
    assert len(exported.evidence_references) == 1
    reference = exported.evidence_references[0]
    assert reference.tool_call_id == "call-impacts-1"
    assert reference.tool_name == "search_current_impacts"
    assert reference.as_of_epoch == NOW
    assert reference.as_of_utc == reference_utc
    assert reference.as_of_utc != invocation.raw_tool_result.as_of_utc
    for code in (
        LiveOpsPublicLimitationCode.FRESHNESS_NOT_ESTABLISHED.value,
        LiveOpsPublicLimitationCode.NO_SNAPSHOT_LIMITATION.value,
        LiveOpsPublicLimitationCode.RECORDED_OVERLAP_NOT_PHYSICAL_ENTRY.value,
    ):
        assert code in exported.mandatory_limitations
        assert code in reference.limitation_codes
    assert "answer" not in exported.__dataclass_fields__


def test_partial_verification_exports_only_the_verified_claim():
    invocation = _impact_invocation()
    rejected = ConfirmedImpactClaim(
        tool_call_id="call-impacts-1",
        tool_name="search_current_impacts",
        aircraft_id="AC-REJECTED",
        hazard_id="HZ-1",
        source_version="SV-1",
        encounter_id="EN-1",
    )
    claims = (_impact_claim(), rejected)
    result, ledger, submitted = _verified(claims, invocation)
    assert result.outcome is LiveOpsVerificationOutcome.PARTIALLY_VERIFIED
    exported = export_live_ops_evidence(result, ledger, submitted)

    assert exported.verification_outcome is LiveOpsPublicVerificationOutcome.PARTIALLY_VERIFIED
    assert [item.kind for item in exported.verified_claims] == [LiveOpsPublicClaimKind.CONFIRMED_IMPACT]
    assert exported.verified_claims[0].aircraft_id == "AC-1"
    assert len(exported.diagnostics) == 1
    diagnostic = exported.diagnostics[0]
    assert diagnostic.claim_kind is LiveOpsPublicClaimKind.CONFIRMED_IMPACT
    assert diagnostic.verdict is LiveOpsPublicClaimVerdict.REJECTED
    assert "IDENTITY_NOT_IN_EVIDENCE" in diagnostic.codes
    assert diagnostic.tool_call_ids == ("call-impacts-1",)
    assert "AC-REJECTED" not in json.dumps(diagnostic.to_dict())
    assert all(item.aircraft_id != "AC-REJECTED" for item in exported.verified_claims)


def test_none_verified_exports_no_positive_fact():
    invocation = _impact_invocation()
    rejected = ConfirmedImpactClaim(
        tool_call_id="call-impacts-1",
        tool_name="search_current_impacts",
        aircraft_id="AC-REJECTED",
        hazard_id="HZ-1",
        source_version="SV-1",
        encounter_id="EN-1",
    )
    result, ledger, submitted = _verified((rejected,), invocation)
    assert result.outcome is LiveOpsVerificationOutcome.NONE_VERIFIED
    exported = export_live_ops_evidence(result, ledger, submitted)

    assert exported.verified_claims == ()
    assert exported.verified_entity_refs == ()
    assert exported.evidence_references == ()
    assert exported.diagnostics[0].verdict is LiveOpsPublicClaimVerdict.REJECTED
    assert LiveOpsPublicLimitationCode.FRESHNESS_NOT_ESTABLISHED.value in exported.mandatory_limitations
    assert exported.verification_outcome is LiveOpsPublicVerificationOutcome.NONE_VERIFIED


def test_execution_failed_does_not_leak_earlier_success():
    from wilvor_ai.live_ops_evidence_verifier import LiveOpsTrustedEvidenceLedger

    invocation = _impact_invocation()
    ledger = LiveOpsTrustedEvidenceLedger(
        correlation_id=CORRELATION,
        now_epoch=NOW,
        dispatches=(
            LiveOpsDispatchResult(
                status=LiveOpsDispatchStatus.EXECUTION_FAILED,
                invocations=(invocation,),
                execution_error=LiveOpsRuntimeErrorCode.TOOL_EXECUTION_FAILED,
                failed_tool_name="search_current_impacts",
                failed_tool_call_id="call-failed",
                planned_tool_call_ids=("call-impacts-1", "call-failed"),
            ),
        ),
    )
    submitted = (_impact_claim(),)
    result = verify_live_ops_claims(submitted, ledger)
    assert result.outcome is LiveOpsVerificationOutcome.NOT_AUTHORIZED
    assert "RUN_EXECUTION_FAILED" in result.structural_codes
    exported = export_live_ops_evidence(result, ledger, submitted)

    assert exported.verification_outcome is LiveOpsPublicVerificationOutcome.NOT_AUTHORIZED
    assert exported.rendering_posture is LiveOpsPublicRenderingPosture.NOT_RENDERABLE
    assert exported.verified_claims == ()
    assert exported.diagnostics == ()
    assert exported.evidence_references == ()
    assert exported.discoveries == ()
    assert exported.verified_entity_refs == ()
    assert exported.contradiction_subjects == ()
    assert "RUN_EXECUTION_FAILED" in exported.structural_codes
    assert LiveOpsPublicLimitationCode.FRESHNESS_NOT_ESTABLISHED.value in exported.mandatory_limitations
    rendered = json.dumps(
        {
            "claims": [item.to_dict() for item in exported.verified_claims],
            "discoveries": [item.to_dict() for item in exported.discoveries],
            "refs": [item.to_dict() for item in exported.verified_entity_refs],
        }
    )
    assert "AC-1" not in rendered
    assert "EN-1" not in rendered


def test_all_sixteen_kinds_round_trip_through_public_claims():
    impact = _impact_invocation(advisory=True)
    encounter = _invoke(
        _result(
            "search_current_encounters",
            "call-encounters-1",
            {
                "encounter_count": 1,
                "encounters": [
                    {
                        "encounter_id": "EN-ENC",
                        "aircraft_id": "AC-ENC",
                        "hazard_id": "HZ-ENC",
                        "source_version": "SV-ENC",
                        "aircraft_is_current": True,
                        "encounter_context": {
                            "encounter_is_current": True,
                            "encounter": {
                                "encounter_id": "EN-ENC",
                                "aircraft_id": "AC-ENC",
                                "hazard_id": "HZ-ENC",
                                "hazard_source_version": "SV-ENC",
                                "projection_id": "PROJ-ENC",
                                "centerline_intersects": False,
                                "corridor_intersects": True,
                                "altitude_overlap_status": "RECORDED",
                                "geometry_overlap_status": "RECORDED",
                            },
                        },
                    }
                ],
            },
        ),
        {"aircraft_id": "AC-ENC"},
    )
    hazards = _invoke(
        _result(
            "search_current_hazards",
            "call-hazards-1",
            {
                "match_count": 1,
                "hazards": [_hazard_row("HZ-REG", "SV-REG")],
                "region": _region(),
            },
        ),
        {"region": "California"},
    )
    absent = _invoke(
        _result(
            "get_aircraft_operational_context",
            "call-absent",
            {"found": False, "aircraft_id": "AC-MISS"},
            status=ToolResultStatus.NOT_FOUND,
            limitations=(),
            evidence=(),
        ),
        {"aircraft_id": "AC-MISS"},
    )
    observed = _invoke(
        _result(
            "get_aircraft_operational_context",
            "call-aircraft-1",
            {
                "found": True,
                "aircraft_id": "AC-OBS",
                "aircraft_is_current": True,
                "aircraft": {"aircraft_id": "AC-OBS", "latitude": 37.6},
                "projection_is_current": True,
                "projection": {
                    "projection_id": "PROJ-1",
                    "aircraft_id": "AC-OBS",
                    "generated_at_utc": "2025-10-15T00:00:00Z",
                },
                "projection_link": {
                    "state": "PRESENT",
                    "selected_identity": [["projection_id", "PROJ-1"]],
                    "observed_identity": [["projection_id", "PROJ-1"]],
                },
            },
        ),
        {"aircraft_id": "AC-OBS"},
    )
    unique = _callsign_invocation("call-find-1", "UAL123", ("AC-1",))
    zero = _callsign_invocation("call-find-zero", "NONE0", ())
    multiple = _callsign_invocation("call-find-many", "MULTI", ("AC-B", "AC-A"))
    truncated_rows = [_hazard_row(f"HZ-T{index:02d}", "SV-T") for index in range(26)]
    truncated = _invoke(
        _result(
            "search_current_hazards",
            "call-truncation",
            {"match_count": 26, "hazards": truncated_rows},
        ),
        {"region": "Truncation"},
    )
    version_a = _invoke(
        _result(
            "search_current_hazards",
            "call-hazards-a",
            {"match_count": 1, "hazards": [_hazard_row("HZ-X", "SV-A")]},
        ),
        {"hazard_ids": ["HZ-X"]},
    )
    version_b = _invoke(
        _result(
            "search_current_hazards",
            "call-hazards-b",
            {"match_count": 1, "hazards": [_hazard_row("HZ-X", "SV-B")]},
        ),
        {"hazard_ids": ["HZ-X"]},
    )
    claims = (
        _impact_claim(),
        ConfirmedEncounterClaim(
            tool_call_id="call-encounters-1",
            tool_name="search_current_encounters",
            encounter_id="EN-ENC",
            aircraft_id="AC-ENC",
            hazard_id="HZ-ENC",
            hazard_source_version="SV-ENC",
            projection_id="PROJ-ENC",
            recorded_flags=(
                ("centerline_intersects", False),
                ("corridor_intersects", True),
            ),
            overlap_statuses=(
                ("altitude_overlap_status", "RECORDED"),
                ("geometry_overlap_status", "RECORDED"),
            ),
        ),
        ScopedCountClaim(
            tool_call_id="call-impacts-1",
            tool_name="search_current_impacts",
            metric=LiveOpsCountMetric.IMPACT_RECORD_COUNT,
            mode=LiveOpsCountMode.EXACT_RETURNED_SET,
            value=1,
            scope=LiveOpsExecutedScope(hazard_ids=("HZ-1",)),
        ),
        QueryResultZeroClaim(
            tool_call_id="call-find-zero",
            tool_name="find_aircraft_by_callsign",
            metric=LiveOpsCountMetric.CALLSIGN_MATCH_COUNT,
            scope=LiveOpsExecutedScope(callsign="NONE0"),
        ),
        ContextRootAbsentClaim(
            tool_call_id="call-absent",
            tool_name="get_aircraft_operational_context",
            entity_id="AC-MISS",
        ),
        HazardRegionIntersectionClaim(
            tool_call_id="call-hazards-1",
            hazard_id="HZ-REG",
            source_version="SV-REG",
            region_code="US-CA",
            region_name="California",
            region_query="California",
        ),
        AircraftObservedStateClaim(
            tool_call_id="call-aircraft-1",
            tool_name="get_aircraft_operational_context",
            aircraft_id="AC-OBS",
            field=LiveOpsObservedField.LATITUDE,
            value=37.6,
            retained_not_current=False,
        ),
        AircraftProjectionStateClaim(
            tool_call_id="call-aircraft-1",
            tool_name="get_aircraft_operational_context",
            aircraft_id="AC-OBS",
            projection_id="PROJ-1",
            field=LiveOpsProjectionField.GENERATED_AT_UTC,
            value="2025-10-15T00:00:00Z",
        ),
        CurrentnessClaim(
            tool_call_id="call-hazards-1",
            tool_name="search_current_hazards",
            predicate=LiveOpsCurrentnessPredicate.HAZARD_IS_CURRENT,
            value=True,
            hazard_id="HZ-REG",
            source_version="SV-REG",
        ),
        RiskStateClaim(
            tool_call_id="call-impacts-1",
            tool_name="search_current_impacts",
            mode=LiveOpsRiskMode.PRESENT,
            encounter_id="EN-1",
            aircraft_id="AC-1",
            hazard_id="HZ-1",
            risk_id="RISK-1",
            risk_level="HIGH",
            risk_score=2,
        ),
        RecommendationSetClaim(
            tool_call_id="call-impacts-1",
            tool_name="search_current_impacts",
            mode=LiveOpsAdvisoryMode.PRESENT_SET,
            encounter_id="EN-1",
            aircraft_id="AC-1",
            hazard_id="HZ-1",
            recommendation_ids=("REC-1",),
        ),
        AlertRecordClaim(
            tool_call_id="call-impacts-1",
            tool_name="search_current_impacts",
            mode=LiveOpsAdvisoryMode.PRESENT_SET,
            encounter_id="EN-1",
            aircraft_id="AC-1",
            hazard_id="HZ-1",
            alert_id="AL-1",
            alert_state="ACTIVE",
            alert_type="IMPACT",
        ),
        CallsignMatchSetClaim(
            tool_call_id="call-find-1",
            callsign="UAL123",
            aircraft_ids=("AC-1",),
        ),
        CoverageLimitationClaim(
            tool_call_id="call-impacts-1",
            tool_name="search_current_impacts",
            code=LiveOpsLimitationCode.FRESHNESS_NOT_ESTABLISHED,
        ),
        ModelVisibleTruncationClaim(
            tool_call_id="call-truncation",
            tool_name="search_current_hazards",
            list_name="hazards",
            total_count=26,
        ),
        CrossToolContradictionClaim(
            tool_call_id_a="call-hazards-a",
            tool_call_id_b="call-hazards-b",
            subject=LiveOpsContradictionSubjectKind.HAZARD_SOURCE_VERSION,
            hazard_id="HZ-X",
        ),
    )
    result, ledger, submitted = _verified(
        claims,
        impact,
        encounter,
        hazards,
        absent,
        observed,
        unique,
        zero,
        multiple,
        truncated,
        version_a,
        version_b,
    )
    _require_all(result)
    exported = export_live_ops_evidence(result, ledger, submitted)
    golden = _golden_keys()
    assert [item.kind.value for item in exported.verified_claims] == [kind.value for kind in (
        LiveOpsPublicClaimKind.CONFIRMED_IMPACT,
        LiveOpsPublicClaimKind.CONFIRMED_ENCOUNTER,
        LiveOpsPublicClaimKind.SCOPED_COUNT,
        LiveOpsPublicClaimKind.QUERY_RESULT_ZERO,
        LiveOpsPublicClaimKind.CONTEXT_ROOT_ABSENT,
        LiveOpsPublicClaimKind.HAZARD_REGION_INTERSECTION,
        LiveOpsPublicClaimKind.AIRCRAFT_OBSERVED_STATE,
        LiveOpsPublicClaimKind.AIRCRAFT_PROJECTION_STATE,
        LiveOpsPublicClaimKind.CURRENTNESS,
        LiveOpsPublicClaimKind.RISK_STATE,
        LiveOpsPublicClaimKind.RECOMMENDATION_SET,
        LiveOpsPublicClaimKind.ALERT_RECORD,
        LiveOpsPublicClaimKind.CALLSIGN_MATCH_SET,
        LiveOpsPublicClaimKind.COVERAGE_LIMITATION,
        LiveOpsPublicClaimKind.MODEL_VISIBLE_TRUNCATION,
        LiveOpsPublicClaimKind.CROSS_TOOL_CONTRADICTION,
    )]
    assert len(exported.verified_claims) == 16
    for internal, public in zip(result.verified_claims, exported.verified_claims):
        payload = public.to_dict()
        assert payload == internal.to_dict()
        assert LiveOpsPublicClaim.from_dict(payload) == public
        assert set(payload) == golden[public.kind.value]
    assert [
        item.tool_call_id
        for item in exported.evidence_references
        if item.tool_call_id == "call-impacts-1"
    ] == ["call-impacts-1"]
    contradiction = exported.verified_claims[-1]
    cited = set(contradiction.cited_tool_call_ids())
    assert cited == {"call-hazards-a", "call-hazards-b"}
    assert {item.tool_call_id for item in exported.evidence_references} >= cited


def test_cross_tool_contradiction_keeps_both_references():
    version_a = _invoke(
        _result(
            "search_current_hazards",
            "call-hazards-a",
            {"match_count": 1, "hazards": [_hazard_row("HZ-X", "SV-A")]},
        ),
        {"hazard_ids": ["HZ-X"]},
    )
    version_b = _invoke(
        _result(
            "search_current_hazards",
            "call-hazards-b",
            {"match_count": 1, "hazards": [_hazard_row("HZ-X", "SV-B")]},
        ),
        {"hazard_ids": ["HZ-X"]},
    )
    claim = CrossToolContradictionClaim(
        tool_call_id_a="call-hazards-a",
        tool_call_id_b="call-hazards-b",
        subject=LiveOpsContradictionSubjectKind.HAZARD_SOURCE_VERSION,
        hazard_id="HZ-X",
    )
    result, ledger, submitted = _verified((claim,), version_a, version_b)
    _require_all(result)
    exported = export_live_ops_evidence(result, ledger, submitted)
    assert [item.tool_call_id for item in exported.evidence_references] == [
        "call-hazards-a",
        "call-hazards-b",
    ]
    assert [item.tool_name for item in exported.evidence_references] == [
        "search_current_hazards",
        "search_current_hazards",
    ]
    assert len(exported.contradiction_subjects) == 1
    subject = exported.contradiction_subjects[0]
    assert subject.identity == (("hazard_id", "HZ-X"),)
    assert subject.tool_call_ids == ("call-hazards-a", "call-hazards-b")


def test_verified_callsign_zero_stays_distinct_from_incomplete_retrieval():
    zero = _callsign_invocation("call-find-zero", "NONE0", ())
    limited = _callsign_invocation("call-limited", "LIMITED", ("AC-HIDDEN",), limit=1)
    claims = (
        QueryResultZeroClaim(
            tool_call_id="call-find-zero",
            tool_name="find_aircraft_by_callsign",
            metric=LiveOpsCountMetric.CALLSIGN_MATCH_COUNT,
            scope=LiveOpsExecutedScope(callsign="NONE0"),
        ),
        QueryResultZeroClaim(
            tool_call_id="call-limited",
            tool_name="find_aircraft_by_callsign",
            metric=LiveOpsCountMetric.CALLSIGN_MATCH_COUNT,
            scope=LiveOpsExecutedScope(callsign="LIMITED"),
        ),
    )
    result, ledger, submitted = _verified(claims, zero, limited)
    assert result.outcome is LiveOpsVerificationOutcome.PARTIALLY_VERIFIED
    exported = export_live_ops_evidence(result, ledger, submitted)
    by_call = {item.evidence_tool_call_id: item for item in exported.discoveries}
    assert by_call["call-find-zero"].status is LiveOpsDiscoveryStatus.NO_SUPPORTED_MATCH
    assert by_call["call-find-zero"].aircraft_ids == ()
    assert by_call["call-limited"].status is LiveOpsDiscoveryStatus.INCOMPLETE
    assert by_call["call-limited"].aircraft_ids == ()
    assert "AC-HIDDEN" not in json.dumps(by_call["call-limited"].to_dict())
    assert exported.verified_entity_refs == ()
    assert len(exported.verified_claims) == 1
    assert exported.verified_claims[0].kind is LiveOpsPublicClaimKind.QUERY_RESULT_ZERO


def test_unique_callsign_match_authorizes_one_entity_ref():
    invocation = _callsign_invocation("call-find-1", "UAL123", ("AC-1",))
    claim = CallsignMatchSetClaim(tool_call_id="call-find-1", callsign="UAL123", aircraft_ids=("AC-1",))
    result, ledger, submitted = _verified((claim,), invocation)
    _require_all(result)
    exported = export_live_ops_evidence(result, ledger, submitted)
    assert len(exported.discoveries) == 1
    discovery = exported.discoveries[0]
    assert discovery.status is LiveOpsDiscoveryStatus.UNIQUE_SUPPORTED_MATCH
    assert discovery.identity_resolved is True
    assert discovery.selection_authorized is False
    assert discovery.reference_epoch == NOW
    assert len(exported.verified_entity_refs) == 1
    ref = exported.verified_entity_refs[0]
    assert ref.entity_id == "AC-1"
    assert ref.callsign == "UAL123"
    assert ref.evidence_tool_call_id == "call-find-1"
    assert ref.correlation_id == CORRELATION
    assert ref.reference_epoch == NOW
    assert ref.reference_utc == live_ops_reference_utc(NOW)
    assert ref.authority == "LIVE_OPS_CURRENT_OBSERVATION"


def test_multiple_callsign_matches_do_not_select_an_aircraft():
    invocation = _callsign_invocation("call-find-many", "MULTI", ("AC-B", "AC-A"))
    claim = CallsignMatchSetClaim(
        tool_call_id="call-find-many",
        callsign="MULTI",
        aircraft_ids=("AC-A", "AC-B"),
    )
    result, ledger, submitted = _verified((claim,), invocation)
    _require_all(result)
    exported = export_live_ops_evidence(result, ledger, submitted)
    discovery = exported.discoveries[0]
    assert discovery.status is LiveOpsDiscoveryStatus.MULTIPLE_SUPPORTED_MATCHES
    assert discovery.identity_resolved is False
    assert discovery.aircraft_ids == ("AC-A", "AC-B")
    assert discovery.selection_authorized is False
    assert exported.verified_entity_refs == ()


def test_incomplete_callsign_search_hides_raw_aircraft_ids():
    hidden = _callsign_invocation("call-hidden", "PARTIAL", ("AC-HIDDEN",), limit=1)
    impact = _impact_invocation()
    result, ledger, submitted = _verified((_impact_claim(),), impact, hidden)
    _require_all(result)
    exported = export_live_ops_evidence(result, ledger, submitted)
    discovery = next(item for item in exported.discoveries if item.evidence_tool_call_id == "call-hidden")
    assert discovery.status is LiveOpsDiscoveryStatus.INCOMPLETE
    assert discovery.aircraft_ids == ()
    assert discovery.callsign == "PARTIAL"
    assert discovery.selection_authorized is False
    assert exported.verified_entity_refs == ()


def test_repeated_callsign_searches_remain_separate():
    first = _callsign_invocation("call-find-a", "UAL123", ("AC-1",))
    second = _callsign_invocation("call-find-b", "UAL123", ("AC-1",))
    claims = (
        CallsignMatchSetClaim(tool_call_id="call-find-a", callsign="UAL123", aircraft_ids=("AC-1",)),
        CallsignMatchSetClaim(tool_call_id="call-find-b", callsign="UAL123", aircraft_ids=("AC-1",)),
    )
    result, ledger, submitted = _verified(claims, first, second)
    _require_all(result)
    exported = export_live_ops_evidence(result, ledger, submitted)
    assert [item.evidence_tool_call_id for item in exported.discoveries] == [
        "call-find-a",
        "call-find-b",
    ]
    assert {item.callsign for item in exported.discoveries} == {"UAL123"}
    assert [item.evidence_tool_call_id for item in exported.verified_entity_refs] == [
        "call-find-a",
        "call-find-b",
    ]


def test_missing_provenance_and_ledger_identity_fail_closed():
    invocation = _impact_invocation()
    result, ledger, submitted = _verified((_impact_claim(),), invocation)
    _require_all(result)
    other = _ledger(
        _impact_invocation("call-other"),
    )
    with pytest.raises(LiveOpsExportError) as missing:
        export_live_ops_evidence(result, other, submitted)
    assert missing.value.code is LiveOpsExportErrorCode.MISSING_PROVENANCE
    assert str(missing.value) == "MISSING_PROVENANCE"
    assert "call-impacts-1" not in str(missing.value)

    swapped = type(ledger)(
        correlation_id="corr-other",
        now_epoch=NOW,
        dispatches=ledger.dispatches,
    )
    with pytest.raises(LiveOpsExportError) as correlation:
        export_live_ops_evidence(result, swapped, submitted)
    assert correlation.value.code is LiveOpsExportErrorCode.LEDGER_MISMATCH

    invocation.raw_tool_result.data["as_of_epoch"] = NOW + 1
    with pytest.raises(LiveOpsExportError) as clock:
        export_live_ops_evidence(result, ledger, submitted)
    assert clock.value.code is LiveOpsExportErrorCode.CLOCK_MISMATCH
    assert "Invalid Wilvor" not in str(clock.value)


def test_submitted_claim_misalignment_is_rejected():
    invocation = _impact_invocation()
    result, ledger, submitted = _verified((_impact_claim(),), invocation)
    _require_all(result)
    with pytest.raises(LiveOpsExportError) as caught:
        export_live_ops_evidence(result, ledger, ())
    assert caught.value.code is LiveOpsExportErrorCode.MALFORMED_VERIFICATION
    public = LiveOpsPublicClaim.from_dict(result.verified_claims[0].to_dict())
    with pytest.raises(LiveOpsExportError) as forged:
        export_live_ops_evidence(result, ledger, (public,))
    assert forged.value.code is LiveOpsExportErrorCode.MALFORMED_VERIFICATION


def test_unexpected_internal_field_is_unmappable(monkeypatch):
    invocation = _impact_invocation()
    result, ledger, submitted = _verified((_impact_claim(),), invocation)
    original = ConfirmedImpactClaim.to_dict

    def extra(self):
        payload = original(self)
        payload["new_internal_field"] = "x"
        return payload

    monkeypatch.setattr(ConfirmedImpactClaim, "to_dict", extra)
    with pytest.raises(LiveOpsExportError) as caught:
        export_live_ops_evidence(result, ledger, submitted)
    assert caught.value.code is LiveOpsExportErrorCode.UNMAPPABLE_CLAIM
    assert str(caught.value) == "UNMAPPABLE_CLAIM"


def test_unknown_limitation_code_is_unrepresentable(monkeypatch):
    invocation = _impact_invocation()
    result, ledger, submitted = _verified((_impact_claim(),), invocation)
    _require_all(result)
    monkeypatch.setattr(
        "wilvor_ai.live_ops_public_export._limitation_codes",
        lambda raw: ("NOT_A_PUBLIC_LIMITATION",),
    )
    with pytest.raises(LiveOpsExportError) as caught:
        export_live_ops_evidence(result, ledger, submitted)
    assert caught.value.code is LiveOpsExportErrorCode.UNREPRESENTABLE_LIMITATION
    assert str(caught.value) == "UNREPRESENTABLE_LIMITATION"
    assert "NOT_A_PUBLIC_LIMITATION" not in str(caught.value)


def test_discovery_limit_is_not_truncated():
    impact = _impact_invocation()
    searches = tuple(
        _callsign_invocation(f"call-cs-{index}", f"CS{index}", ())
        for index in range(7)
    )
    result, ledger, submitted = _verified((_impact_claim(),), impact, *searches)
    _require_all(result)
    with pytest.raises(LiveOpsExportError) as caught:
        export_live_ops_evidence(result, ledger, submitted)
    assert caught.value.code is LiveOpsExportErrorCode.RESULT_LIMIT_EXCEEDED


def test_two_pair_hazard_subject_is_unrepresentable():
    invocation = _invoke(
        _result(
            "search_current_hazards",
            "call-hazards-gap",
            {
                "match_count": 1,
                "hazards": [_hazard_row("HZ-1", "SV-1")],
                "rejected_hazard_selections": [
                    {
                        "hazard_id": "HZ-1",
                        "reason": "NOT_CURRENT",
                        "discovered_source_version": "SV-1",
                    }
                ],
            },
        ),
        {"hazard_ids": ["HZ-1"]},
    )
    claim = CoverageLimitationClaim(
        tool_call_id="call-hazards-gap",
        tool_name="search_current_hazards",
        code=LiveOpsLimitationCode.FRESHNESS_NOT_ESTABLISHED,
    )
    result, ledger, submitted = _verified((claim,), invocation)
    _require_all(result)
    assert any(len(item.identity) == 2 for item in result.contradiction_subjects)
    with pytest.raises(LiveOpsExportError) as caught:
        export_live_ops_evidence(result, ledger, submitted)
    assert caught.value.code is LiveOpsExportErrorCode.UNREPRESENTABLE_SUBJECT
    assert "HZ-1" not in str(caught.value)
    assert "source_version" not in str(caught.value)


def test_encounter_evaluation_subject_is_unrepresentable():
    invocation = _invoke(
        _result(
            "search_current_impacts",
            "call-eval",
            {
                "impact_count": 1,
                "impacts": [
                    {
                        "encounter_id": "EN-EVAL",
                        "aircraft_id": "AC-EVAL",
                        "hazard_id": "HZ-EVAL",
                        "source_version": "SV-EVAL",
                        "encounter_context": {
                            "encounter_is_current": True,
                            "encounter": {
                                "encounter_id": "EN-EVAL",
                                "aircraft_id": "AC-EVAL",
                                "hazard_id": "HZ-EVAL",
                                "hazard_source_version": "SV-EVAL",
                            },
                        },
                    }
                ],
                "operationally_unevaluated": [{"encounter_id": "EN-EVAL"}],
            },
        ),
        {"hazard_ids": ["HZ-EVAL"]},
    )
    claim = CoverageLimitationClaim(
        tool_call_id="call-eval",
        tool_name="search_current_impacts",
        code=LiveOpsLimitationCode.FRESHNESS_NOT_ESTABLISHED,
    )
    result, ledger, submitted = _verified((claim,), invocation)
    _require_all(result)
    assert any(
        len(item.identity) == 2 and item.identity[1][0] == "evaluation"
        for item in result.contradiction_subjects
    )
    with pytest.raises(LiveOpsExportError) as caught:
        export_live_ops_evidence(result, ledger, submitted)
    assert caught.value.code is LiveOpsExportErrorCode.UNREPRESENTABLE_SUBJECT


def test_exported_bundle_satisfies_public_run_result_invariants():
    invocation = _impact_invocation()
    result, ledger, submitted = _verified((_impact_claim(),), invocation)
    exported = export_live_ops_evidence(result, ledger, submitted)
    completed = LiveOpsRunResult(
        correlation_id=exported.correlation_id,
        execution_status=LiveOpsRunStatus.COMPLETED,
        verification_outcome=exported.verification_outcome,
        rendering_posture=exported.rendering_posture,
        answer_origin=LiveOpsAnswerOrigin.DETERMINISTIC_RENDERER,
        answer=PROBE_ANSWER,
        reference_epoch=exported.reference_epoch,
        reference_utc=exported.reference_utc,
        verified_claims=exported.verified_claims,
        evidence_references=exported.evidence_references,
        diagnostics=exported.diagnostics,
        structural_codes=exported.structural_codes,
        contradiction_subjects=exported.contradiction_subjects,
        mandatory_limitations=exported.mandatory_limitations,
        discoveries=exported.discoveries,
        verified_entity_refs=exported.verified_entity_refs,
    )
    assert completed.answer == PROBE_ANSWER
    assert completed.verification_outcome is LiveOpsPublicVerificationOutcome.ALL_VERIFIED

    rejected = ConfirmedImpactClaim(
        tool_call_id="call-impacts-1",
        tool_name="search_current_impacts",
        aircraft_id="AC-REJECTED",
        hazard_id="HZ-1",
        source_version="SV-1",
        encounter_id="EN-1",
    )
    none_result, none_ledger, none_submitted = _verified((rejected,), invocation)
    none_export = export_live_ops_evidence(none_result, none_ledger, none_submitted)
    none_run = LiveOpsRunResult(
        correlation_id=none_export.correlation_id,
        execution_status=LiveOpsRunStatus.COMPLETED,
        verification_outcome=none_export.verification_outcome,
        rendering_posture=none_export.rendering_posture,
        answer_origin=LiveOpsAnswerOrigin.FIXED_SAFE_TEXT,
        answer=LIVE_OPS_ANSWER_NONE_VERIFIED,
        reference_epoch=none_export.reference_epoch,
        reference_utc=none_export.reference_utc,
        verified_claims=none_export.verified_claims,
        evidence_references=none_export.evidence_references,
        diagnostics=none_export.diagnostics,
        structural_codes=none_export.structural_codes,
        contradiction_subjects=none_export.contradiction_subjects,
        mandatory_limitations=none_export.mandatory_limitations,
        discoveries=none_export.discoveries,
        verified_entity_refs=none_export.verified_entity_refs,
    )
    assert none_run.answer == LIVE_OPS_ANSWER_NONE_VERIFIED

    from wilvor_ai.live_ops_evidence_verifier import LiveOpsTrustedEvidenceLedger

    failed_ledger = LiveOpsTrustedEvidenceLedger(
        correlation_id=CORRELATION,
        now_epoch=NOW,
        dispatches=(
            LiveOpsDispatchResult(
                status=LiveOpsDispatchStatus.EXECUTION_FAILED,
                invocations=(invocation,),
                execution_error=LiveOpsRuntimeErrorCode.TOOL_EXECUTION_FAILED,
                failed_tool_name="search_current_impacts",
                failed_tool_call_id="call-failed",
                planned_tool_call_ids=("call-impacts-1", "call-failed"),
            ),
        ),
    )
    failed_result = verify_live_ops_claims((_impact_claim(),), failed_ledger)
    failed_export = export_live_ops_evidence(failed_result, failed_ledger, (_impact_claim(),))
    failed_run = LiveOpsRunResult(
        correlation_id=failed_export.correlation_id,
        execution_status=LiveOpsRunStatus.TOOL_FAILED,
        verification_outcome=failed_export.verification_outcome,
        rendering_posture=failed_export.rendering_posture,
        answer_origin=LiveOpsAnswerOrigin.FIXED_SAFE_TEXT,
        answer=LIVE_OPS_ANSWER_TOOL_FAILED,
        reference_epoch=failed_export.reference_epoch,
        reference_utc=failed_export.reference_utc,
        structural_codes=failed_export.structural_codes,
        mandatory_limitations=failed_export.mandatory_limitations,
        failure=LiveOpsRunFailure(reason=LiveOpsFailureReason.TOOL_EXECUTION_FAILED),
    )
    assert failed_run.answer == LIVE_OPS_ANSWER_TOOL_FAILED
    empty_result = verify_live_ops_claims((), _ledger(invocation))
    empty_export = export_live_ops_evidence(empty_result, _ledger(invocation), ())
    unavailable = LiveOpsRunResult(
        correlation_id=empty_export.correlation_id,
        execution_status=LiveOpsRunStatus.UNAVAILABLE,
        verification_outcome=empty_export.verification_outcome,
        rendering_posture=empty_export.rendering_posture,
        answer_origin=LiveOpsAnswerOrigin.FIXED_SAFE_TEXT,
        answer=LIVE_OPS_ANSWER_UNAVAILABLE,
        reference_epoch=empty_export.reference_epoch,
        reference_utc=empty_export.reference_utc,
        structural_codes=empty_export.structural_codes,
        mandatory_limitations=empty_export.mandatory_limitations,
        failure=LiveOpsRunFailure(reason=LiveOpsFailureReason.LEDGER_NOT_AUTHORIZED),
    )
    assert unavailable.verification_outcome is LiveOpsPublicVerificationOutcome.NOT_AUTHORIZED
    assert "RUN_EXECUTION_FAILED" not in unavailable.structural_codes


def test_exporter_imports_stay_inside_specialist_contracts():
    imported = _imported_modules(EXPORTER_PATH)
    forbidden = {
        "anthropic",
        "boto3",
        "botocore",
        "langgraph",
        "openai",
        "wilvor_ai.live_ops",
        "wilvor_ai.live_ops_adapter",
        "wilvor_ai.live_ops_mapping",
        "wilvor_operational",
    }
    assert forbidden.isdisjoint(imported)
    public = _imported_modules(PUBLIC_PATH)
    assert "wilvor_ai.live_ops_public_export" not in public
    source = EXPORTER_PATH.read_text(encoding="utf-8")
    assert "time.time" not in source
    assert "LiveOpsRunResult(" not in source
