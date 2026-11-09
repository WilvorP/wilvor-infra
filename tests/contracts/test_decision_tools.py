"""DT5 closed Decision Tool catalog, runtime, and adapter."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from wilvor_ai.contracts import (
    AgentAuthorityMode,
    AgentCapability,
    ConfidenceLevel,
    ContractValidationError,
    Evidence,
    FreshnessStatus,
    SourceRecord,
    TemporalScope,
    ToolInputValueType,
    ToolResult,
    ToolResultStatus,
)
from wilvor_ai.decision_context import DecisionContextCall
from wilvor_ai.decision_contracts import (
    OPERATIONAL_ID_MAX_LENGTH,
    DecisionChainGap,
    DecisionEvaluationState,
    DecisionEvidence,
    DecisionEvidenceKind,
    DecisionLimitationCode,
    DecisionRecommendationSet,
    DecisionReportedLinkState,
    DecisionRiskEvidence,
    RiskPresence,
    expected_decision_status,
)
from wilvor_ai.decision_tools import (
    DECISION_TOOLS,
    DecisionToolsAdapter,
    DecisionToolsRuntime,
    decision_tool_schemas,
)
from wilvor_ai.persisted_airport_contracts import PersistedAirportEvidence
from wilvor_ai.persisted_airport_evidence import (
    NOT_CURRENT_LIMITATION,
    PersistedAirportEvidenceCall,
)
from wilvor_ai.tool_schema import FORBIDDEN_TOOL_SCHEMA_FIELD_NAMES, build_tool_schemas
from wilvor_operational.context import _normalize_aircraft_id, normalize_aircraft_id
import wilvor_ai.decision_context as decision_context
import wilvor_ai.decision_tools as decision_tools
import wilvor_ai.persisted_airport_evidence as persisted_airport_evidence


QUERY = "2026-09-30T20:00:00Z"
NOW = 1759276800
CALL_ID = "decision-call-1"
CORRELATION = "corr-dt5"
MODULE = (
    Path(__file__).resolve().parents[2]
    / "functions"
    / "shared"
    / "wilvor_ai"
    / "decision_tools.py"
)
_TOOL_NAMES = (
    "get_current_decision_context",
    "get_current_risk_evidence",
    "get_current_recommendation",
    "get_persisted_airport_candidate_evidence",
)


def _runtime(**overrides) -> DecisionToolsRuntime:
    values = {
        "tables": object(),
        "now_epoch": NOW,
        "query_timestamp_utc": QUERY,
        "correlation_id": CORRELATION,
    }
    values.update(overrides)
    return DecisionToolsRuntime(**values)


def _adapter(**overrides) -> DecisionToolsAdapter:
    return DecisionToolsAdapter(_runtime(**overrides))


def _evidence_item(tool_call_id: str, scope: TemporalScope) -> Evidence:
    return Evidence(
        source="decision-test",
        source_records=(
            SourceRecord(
                record_id="record-1",
                source_version="ver-1",
                event_timestamp_utc=QUERY,
            ),
        ),
        query_timestamp_utc=QUERY,
        freshness_status=FreshnessStatus.FRESH,
        confidence=ConfidenceLevel.UNKNOWN,
        limitations=(),
        tool_call_id=tool_call_id,
        temporal_scope=scope,
    )


def _current_evidence(
    kind: DecisionEvidenceKind,
    *,
    aircraft_id: str | None = "n123ab",
    evaluation_state: DecisionEvaluationState = DecisionEvaluationState.ESTABLISHED,
) -> DecisionEvidence:
    if evaluation_state is DecisionEvaluationState.SOURCE_UNAVAILABLE:
        values = {
            "kind": kind,
            "evaluation_state": evaluation_state,
            "aircraft_in_current_set": False,
            "projection_state": DecisionReportedLinkState.MISSING,
            "chain_gaps": (),
            "limitation_codes": (),
        }
        if kind is not DecisionEvidenceKind.DECISION_CONTEXT:
            values["risk"] = DecisionRiskEvidence(presence=RiskPresence.ABSENT)
        return DecisionEvidence(**values)
    if evaluation_state is DecisionEvaluationState.NOT_ESTABLISHED:
        values = {
            "kind": kind,
            "evaluation_state": evaluation_state,
            "aircraft_in_current_set": False,
            "aircraft_id": aircraft_id,
            "projection_state": DecisionReportedLinkState.MISSING,
            "chain_gaps": (),
            "limitation_codes": (),
        }
        if kind is not DecisionEvidenceKind.DECISION_CONTEXT:
            values["risk"] = DecisionRiskEvidence(presence=RiskPresence.ABSENT)
        return DecisionEvidence(**values)
    if aircraft_id is None:
        return DecisionEvidence(
            kind=kind,
            evaluation_state=DecisionEvaluationState.ESTABLISHED,
            aircraft_in_current_set=False,
            aircraft_id=None,
            projection_state=DecisionReportedLinkState.MISSING,
            chain_gaps=(DecisionChainGap.AIRCRAFT_NOT_IN_CURRENT_SET,),
            limitation_codes=(DecisionLimitationCode.NO_SNAPSHOT_LIMITATION,),
            risk=(
                None
                if kind is DecisionEvidenceKind.DECISION_CONTEXT
                else DecisionRiskEvidence(presence=RiskPresence.ABSENT)
            ),
        )
    gaps = [DecisionChainGap.NO_CURRENT_PROJECTION]
    risk = None
    recommendations = None
    limitation_codes = [DecisionLimitationCode.NO_SNAPSHOT_LIMITATION]
    if kind is not DecisionEvidenceKind.DECISION_CONTEXT:
        risk = DecisionRiskEvidence(presence=RiskPresence.ABSENT)
        gaps.append(DecisionChainGap.RISK_ABSENT)
    if kind is DecisionEvidenceKind.RECOMMENDATION_EVIDENCE:
        recommendations = DecisionRecommendationSet(
            current=(),
            absence_state=DecisionReportedLinkState.ABSENT_FROM_CURRENT_CANDIDATES,
        )
        gaps.append(DecisionChainGap.RECOMMENDATION_ABSENT)
        limitation_codes.append(DecisionLimitationCode.RECOMMENDATION_ABSENCE_LIMITATION)
    return DecisionEvidence(
        kind=kind,
        evaluation_state=DecisionEvaluationState.ESTABLISHED,
        aircraft_in_current_set=True,
        aircraft_id=aircraft_id,
        projection_state=DecisionReportedLinkState.MISSING,
        chain_gaps=tuple(gaps),
        limitation_codes=tuple(limitation_codes),
        risk=risk,
        recommendations=recommendations,
    )


def _current_result(
    tool_name: str,
    kind: DecisionEvidenceKind,
    *,
    tool_call_id: str = CALL_ID,
    correlation_id: str | None = CORRELATION,
    aircraft_id: str | None = "n123ab",
    evaluation_state: DecisionEvaluationState = DecisionEvaluationState.ESTABLISHED,
    temporal_scope: TemporalScope = TemporalScope.CURRENT,
) -> ToolResult:
    evidence = _current_evidence(
        kind,
        aircraft_id=aircraft_id,
        evaluation_state=evaluation_state,
    )
    status = expected_decision_status(evidence)
    limitations: tuple[str, ...] = ()
    if status is ToolResultStatus.PARTIAL:
        limitations = ("The read is partial.",)
    if status in {ToolResultStatus.UNAVAILABLE, ToolResultStatus.UNKNOWN}:
        limitations = ("The read could not be established.",)
    tool_evidence = ()
    if status in {ToolResultStatus.SUCCESS, ToolResultStatus.PARTIAL}:
        tool_evidence = (_evidence_item(tool_call_id, temporal_scope),)
    return ToolResult(
        tool_name=tool_name,
        tool_call_id=tool_call_id,
        status=status,
        temporal_scope=temporal_scope,
        data=evidence.to_dict(),
        evidence=tool_evidence,
        as_of_utc=QUERY,
        limitations=limitations,
        correlation_id=correlation_id,
    )


def _persisted_result(
    recommendation_id: str = "rec-1",
    *,
    tool_name: str = "get_persisted_airport_candidate_evidence",
    tool_call_id: str = CALL_ID,
    correlation_id: str | None = CORRELATION,
    temporal_scope: TemporalScope = TemporalScope.PERSISTED,
) -> ToolResult:
    payload = PersistedAirportEvidence(
        recommendation_id=recommendation_id,
        airport_evaluation_id=None,
        candidates=(),
    )
    return ToolResult(
        tool_name=tool_name,
        tool_call_id=tool_call_id,
        status=ToolResultStatus.SUCCESS,
        temporal_scope=temporal_scope,
        data=payload.to_dict(),
        evidence=(
            Evidence(
                source="get_recommendation_record",
                source_records=(
                    SourceRecord(
                        record_id=recommendation_id,
                        source_version=None,
                        event_timestamp_utc=None,
                    ),
                ),
                query_timestamp_utc=QUERY,
                freshness_status=FreshnessStatus.UNKNOWN,
                confidence=ConfidenceLevel.UNKNOWN,
                limitations=(NOT_CURRENT_LIMITATION,),
                tool_call_id=tool_call_id,
                temporal_scope=temporal_scope,
            ),
        ),
        as_of_utc=None,
        limitations=(),
        correlation_id=correlation_id,
    )


def _patch_operations(monkeypatch, responder):
    called = []

    def install(module, name):
        def fake(*args):
            called.append(name)
            if name != responder[0]:
                raise AssertionError(name)
            return responder[1](*args)

        monkeypatch.setattr(module, name, fake)

    install(decision_context, "get_current_decision_context")
    install(decision_context, "get_current_risk_evidence")
    install(decision_context, "get_current_recommendation")
    install(
        persisted_airport_evidence,
        "get_persisted_airport_candidate_evidence",
    )
    return called


def _block_projection(monkeypatch):
    projected = []

    def fake(result):
        projected.append(result)
        raise AssertionError("projection")

    monkeypatch.setattr(decision_tools, "project_decision_tool_result", fake)
    return projected


def test_catalog_is_exactly_four_tools_in_order():
    assert [item.name for item in DECISION_TOOLS] == list(_TOOL_NAMES)
    schemas = decision_tool_schemas()
    assert [item.name for item in schemas] == list(_TOOL_NAMES)
    assert schemas == build_tool_schemas(DECISION_TOOLS)
    for spec, schema in zip(DECISION_TOOLS, schemas, strict=True):
        assert spec.authority_mode is AgentAuthorityMode.READ_ONLY_ADVISORY
        assert spec.capabilities == (
            AgentCapability.RETRIEVE_DETERMINISTIC_OPERATIONAL_CONTEXT,
        )
        assert len(spec.input_fields) == 1
        field = spec.input_fields[0]
        assert field.required is True
        assert field.value_type is ToolInputValueType.STRING
        assert schema.input_fields == spec.input_fields
    assert [item.input_fields[0].name for item in DECISION_TOOLS[:3]] == [
        "aircraft_id",
        "aircraft_id",
        "aircraft_id",
    ]
    assert DECISION_TOOLS[3].input_fields[0].name == "recommendation_id"
    dumped = ast.dump(ast.parse(MODULE.read_text(encoding="utf-8")))
    assert "inspect" not in dumped
    assert "signature" not in dumped
    assert "globals" not in dumped
    assert "getattr" not in dumped
    assert "importlib" not in dumped


def test_descriptions_do_not_claim_missing_capabilities():
    text = {item.name: item.description for item in DECISION_TOOLS}
    assert "does not calculate risk" in text["get_current_decision_context"]
    assert "Absent risk is not LOW" in text["get_current_risk_evidence"]
    assert "none is selected" in text["get_current_recommendation"]
    assert "EVALUATE_DIVERSION" in text["get_current_recommendation"]
    assert "not a selected diversion" in text["get_persisted_airport_candidate_evidence"]
    assert "scoring completed" in text["get_persisted_airport_candidate_evidence"]


def test_forbidden_runtime_names_stay_out_of_schemas_and_invoke(monkeypatch):
    called = _patch_operations(
        monkeypatch,
        ("get_current_decision_context", lambda *args: None),
    )
    names = {field.name for spec in DECISION_TOOLS for field in spec.input_fields}
    assert names.isdisjoint(FORBIDDEN_TOOL_SCHEMA_FIELD_NAMES)
    for forbidden in (
        "call",
        "tables",
        "now_epoch",
        "query_timestamp_utc",
        "tool_call_id",
        "correlation_id",
    ):
        assert forbidden in FORBIDDEN_TOOL_SCHEMA_FIELD_NAMES
        with pytest.raises(ContractValidationError, match="trusted_argument_forbidden"):
            _adapter().invoke(
                "get_current_decision_context",
                {"aircraft_id": "N123AB", forbidden: "injected"},
                tool_call_id=CALL_ID,
            )
    assert called == []


def test_public_aircraft_normalizer_matches_the_private_helper():
    assert normalize_aircraft_id("N123AB") == "n123ab"
    assert normalize_aircraft_id("n123ab") == "n123ab"
    assert normalize_aircraft_id(" N123AB ") == "n123ab"
    assert _normalize_aircraft_id(" N123AB ") == normalize_aircraft_id(" N123AB ")
    assert _normalize_aircraft_id(None) == normalize_aircraft_id(None) == ""


def test_runtime_accepts_trusted_values_and_rejects_bad_ones():
    runtime = _runtime()
    assert runtime.now_epoch == NOW
    assert runtime.correlation_id == CORRELATION
    assert _runtime(correlation_id=None).correlation_id is None
    for overrides in (
        {"now_epoch": True},
        {"now_epoch": 1.5},
        {"query_timestamp_utc": "2026-09-30T20:00:00"},
        {"query_timestamp_utc": "not-a-time"},
        {"correlation_id": ""},
        {"correlation_id": " corr"},
        {"tables": None},
    ):
        with pytest.raises(ContractValidationError):
            _runtime(**overrides)
    source = MODULE.read_text(encoding="utf-8")
    assert "datetime.now" not in source
    assert "time.time" not in source


@pytest.mark.parametrize(
    ("tool_name", "arguments"),
    [
        ("choose_diversion", {"aircraft_id": "N123AB"}),
        ("get_current_decision_context", ["N123AB"]),
        ("get_current_decision_context", {"aircraft_id": "N123AB", "extra": "x"}),
        ("get_current_decision_context", {}),
        ("get_current_decision_context", {"aircraft_id": 1}),
        ("get_current_decision_context", {"aircraft_id": ""}),
        ("get_current_decision_context", {"aircraft_id": "   "}),
        ("get_current_decision_context", {"aircraft_id": " N123AB"}),
        ("get_current_decision_context", {"aircraft_id": "N123AB "}),
        ("get_current_decision_context", {"aircraft_id": "N12 3AB"}),
        (
            "get_current_decision_context",
            {"aircraft_id": "A" * (OPERATIONAL_ID_MAX_LENGTH + 1)},
        ),
        ("get_persisted_airport_candidate_evidence", {"recommendation_id": ""}),
        ("get_persisted_airport_candidate_evidence", {"recommendation_id": "   "}),
        ("get_persisted_airport_candidate_evidence", {"recommendation_id": " rec-1"}),
        ("get_persisted_airport_candidate_evidence", {"recommendation_id": "rec-1 "}),
    ],
)
def test_invalid_input_does_not_call_an_operation(monkeypatch, tool_name, arguments):
    called = _patch_operations(monkeypatch, ("unused", lambda *args: None))
    with pytest.raises(ContractValidationError):
        _adapter().invoke(tool_name, arguments, tool_call_id=CALL_ID)
    assert called == []


def test_invalid_tool_call_id_does_not_call_an_operation(monkeypatch):
    called = _patch_operations(monkeypatch, ("unused", lambda *args: None))
    with pytest.raises(ContractValidationError, match="invalid_tool_call_id"):
        _adapter().invoke(
            "get_current_decision_context",
            {"aircraft_id": "N123AB"},
            tool_call_id=" call-1",
        )
    assert called == []


@pytest.mark.parametrize(
    ("tool_name", "kind"),
    [
        ("get_current_decision_context", DecisionEvidenceKind.DECISION_CONTEXT),
        ("get_current_risk_evidence", DecisionEvidenceKind.RISK_EVIDENCE),
        ("get_current_recommendation", DecisionEvidenceKind.RECOMMENDATION_EVIDENCE),
    ],
)
def test_current_tools_dispatch_only_their_operation(monkeypatch, tool_name, kind):
    called = _patch_operations(
        monkeypatch,
        (tool_name, lambda call, aircraft_id: _current_result(tool_name, kind)),
    )
    result = _adapter().invoke(
        tool_name,
        {"aircraft_id": "N123AB"},
        tool_call_id=CALL_ID,
    )
    assert called == [tool_name]
    assert result.raw_tool_result.tool_name == tool_name
    assert "tool_call_id" not in result.model_projection.to_dict()
    assert "correlation_id" not in result.model_projection.to_dict()


def test_current_dispatch_uses_decision_context_call(monkeypatch):
    tables = object()
    seen = {}

    def fake(call, aircraft_id):
        seen["call"] = call
        seen["aircraft_id"] = aircraft_id
        return _current_result(
            "get_current_decision_context",
            DecisionEvidenceKind.DECISION_CONTEXT,
        )

    called = _patch_operations(monkeypatch, ("get_current_decision_context", fake))
    DecisionToolsAdapter(_runtime(tables=tables)).invoke(
        "get_current_decision_context",
        {"aircraft_id": "N123AB"},
        tool_call_id=CALL_ID,
    )
    assert called == ["get_current_decision_context"]
    assert isinstance(seen["call"], DecisionContextCall)
    assert seen["call"].tables is tables
    assert seen["call"].now_epoch == NOW
    assert seen["call"].tool_call_id == CALL_ID
    assert seen["call"].correlation_id == CORRELATION
    assert seen["aircraft_id"] == "N123AB"


def test_persisted_dispatch_uses_persisted_call(monkeypatch):
    tables = object()
    seen = {}

    def fake(call, recommendation_id):
        seen["call"] = call
        seen["recommendation_id"] = recommendation_id
        return _persisted_result(recommendation_id)

    called = _patch_operations(
        monkeypatch,
        ("get_persisted_airport_candidate_evidence", fake),
    )
    invocation = DecisionToolsAdapter(_runtime(tables=tables)).invoke(
        "get_persisted_airport_candidate_evidence",
        {"recommendation_id": "rec-1"},
        tool_call_id=CALL_ID,
    )
    assert called == ["get_persisted_airport_candidate_evidence"]
    assert isinstance(seen["call"], PersistedAirportEvidenceCall)
    assert seen["call"].tables is tables
    assert seen["call"].tool_call_id == CALL_ID
    assert seen["call"].query_timestamp_utc == QUERY
    assert seen["call"].correlation_id == CORRELATION
    assert seen["recommendation_id"] == "rec-1"
    assert invocation.model_projection.temporal_scope is TemporalScope.PERSISTED


@pytest.mark.parametrize(
    "factory",
    [
        lambda: "not-a-result",
        lambda: _current_result(
            "get_current_risk_evidence",
            DecisionEvidenceKind.DECISION_CONTEXT,
        ),
        lambda: _current_result(
            "get_current_decision_context",
            DecisionEvidenceKind.DECISION_CONTEXT,
            tool_call_id="other-call",
        ),
        lambda: _current_result(
            "get_current_decision_context",
            DecisionEvidenceKind.DECISION_CONTEXT,
            correlation_id=None,
        ),
        lambda: _current_result(
            "get_current_decision_context",
            DecisionEvidenceKind.DECISION_CONTEXT,
            correlation_id="other-corr",
        ),
        lambda: _current_result(
            "get_current_decision_context",
            DecisionEvidenceKind.DECISION_CONTEXT,
            temporal_scope=TemporalScope.PERSISTED,
        ),
        lambda: _current_result(
            "get_current_decision_context",
            DecisionEvidenceKind.DECISION_CONTEXT,
            aircraft_id="n456cd",
        ),
    ],
)
def test_post_dispatch_mismatch_produces_no_projection(monkeypatch, factory):
    projected = _block_projection(monkeypatch)
    _patch_operations(
        monkeypatch,
        ("get_current_decision_context", lambda *args: factory()),
    )
    with pytest.raises(ContractValidationError):
        _adapter().invoke(
            "get_current_decision_context",
            {"aircraft_id": "N123AB"},
            tool_call_id=CALL_ID,
        )
    assert projected == []


def test_unexpected_correlation_when_runtime_has_none_is_rejected(monkeypatch):
    projected = _block_projection(monkeypatch)
    _patch_operations(
        monkeypatch,
        (
            "get_current_decision_context",
            lambda *args: _current_result(
                "get_current_decision_context",
                DecisionEvidenceKind.DECISION_CONTEXT,
                correlation_id="unexpected",
            ),
        ),
    )
    with pytest.raises(ContractValidationError, match="decision_correlation_id_mismatch"):
        _adapter(correlation_id=None).invoke(
            "get_current_decision_context",
            {"aircraft_id": "N123AB"},
            tool_call_id=CALL_ID,
        )
    assert projected == []


def test_malformed_payload_produces_no_projection(monkeypatch):
    projected = _block_projection(monkeypatch)
    result = _current_result(
        "get_current_decision_context",
        DecisionEvidenceKind.DECISION_CONTEXT,
    )
    broken = ToolResult(
        tool_name=result.tool_name,
        tool_call_id=result.tool_call_id,
        status=result.status,
        temporal_scope=result.temporal_scope,
        data={"kind": "DECISION_CONTEXT"},
        evidence=result.evidence,
        as_of_utc=result.as_of_utc,
        limitations=result.limitations,
        correlation_id=result.correlation_id,
    )
    _patch_operations(
        monkeypatch,
        ("get_current_decision_context", lambda *args: broken),
    )
    with pytest.raises(ContractValidationError):
        _adapter().invoke(
            "get_current_decision_context",
            {"aircraft_id": "N123AB"},
            tool_call_id=CALL_ID,
        )
    assert projected == []


@pytest.mark.parametrize(
    ("tool_name", "returned_kind"),
    [
        ("get_current_decision_context", DecisionEvidenceKind.RISK_EVIDENCE),
        ("get_current_risk_evidence", DecisionEvidenceKind.DECISION_CONTEXT),
        ("get_current_recommendation", DecisionEvidenceKind.RISK_EVIDENCE),
    ],
)
def test_wrong_evidence_kind_is_rejected(monkeypatch, tool_name, returned_kind):
    projected = _block_projection(monkeypatch)
    _patch_operations(
        monkeypatch,
        (tool_name, lambda *args: _current_result(tool_name, returned_kind)),
    )
    with pytest.raises(ContractValidationError, match="decision_evidence_kind_mismatch"):
        _adapter().invoke(tool_name, {"aircraft_id": "N123AB"}, tool_call_id=CALL_ID)
    assert projected == []


def test_case_different_aircraft_id_matches(monkeypatch):
    _patch_operations(
        monkeypatch,
        (
            "get_current_risk_evidence",
            lambda *args: _current_result(
                "get_current_risk_evidence",
                DecisionEvidenceKind.RISK_EVIDENCE,
                aircraft_id="n123ab",
            ),
        ),
    )
    invocation = _adapter().invoke(
        "get_current_risk_evidence",
        {"aircraft_id": "N123AB"},
        tool_call_id=CALL_ID,
    )
    assert invocation.raw_tool_result.data["aircraft_id"] == "n123ab"


def test_different_aircraft_id_is_rejected(monkeypatch):
    projected = _block_projection(monkeypatch)
    _patch_operations(
        monkeypatch,
        (
            "get_current_decision_context",
            lambda *args: _current_result(
                "get_current_decision_context",
                DecisionEvidenceKind.DECISION_CONTEXT,
                aircraft_id="N456CD",
            ),
        ),
    )
    with pytest.raises(ContractValidationError, match="decision_aircraft_identity_mismatch"):
        _adapter().invoke(
            "get_current_decision_context",
            {"aircraft_id": "N123AB"},
            tool_call_id=CALL_ID,
        )
    assert projected == []


def test_source_unavailable_without_aircraft_id_is_accepted(monkeypatch):
    _patch_operations(
        monkeypatch,
        (
            "get_current_recommendation",
            lambda *args: _current_result(
                "get_current_recommendation",
                DecisionEvidenceKind.RECOMMENDATION_EVIDENCE,
                aircraft_id=None,
                evaluation_state=DecisionEvaluationState.SOURCE_UNAVAILABLE,
            ),
        ),
    )
    invocation = _adapter().invoke(
        "get_current_recommendation",
        {"aircraft_id": "N123AB"},
        tool_call_id=CALL_ID,
    )
    assert invocation.raw_tool_result.data["aircraft_id"] is None
    assert invocation.model_projection.status is ToolResultStatus.UNAVAILABLE


def test_not_found_without_aircraft_id_is_rejected(monkeypatch):
    projected = _block_projection(monkeypatch)
    _patch_operations(
        monkeypatch,
        (
            "get_current_decision_context",
            lambda *args: _current_result(
                "get_current_decision_context",
                DecisionEvidenceKind.DECISION_CONTEXT,
                aircraft_id=None,
            ),
        ),
    )
    with pytest.raises(ContractValidationError, match="decision_aircraft_identity_mismatch"):
        _adapter().invoke(
            "get_current_decision_context",
            {"aircraft_id": "N123AB"},
            tool_call_id=CALL_ID,
        )
    assert projected == []


def test_not_established_without_aircraft_id_is_rejected(monkeypatch):
    projected = _block_projection(monkeypatch)
    _patch_operations(
        monkeypatch,
        (
            "get_current_risk_evidence",
            lambda *args: _current_result(
                "get_current_risk_evidence",
                DecisionEvidenceKind.RISK_EVIDENCE,
                aircraft_id=None,
                evaluation_state=DecisionEvaluationState.NOT_ESTABLISHED,
            ),
        ),
    )
    with pytest.raises(ContractValidationError, match="decision_aircraft_identity_mismatch"):
        _adapter().invoke(
            "get_current_risk_evidence",
            {"aircraft_id": "N123AB"},
            tool_call_id=CALL_ID,
        )
    assert projected == []


def test_persisted_recommendation_id_must_match_exactly(monkeypatch):
    projected = _block_projection(monkeypatch)
    _patch_operations(
        monkeypatch,
        (
            "get_persisted_airport_candidate_evidence",
            lambda *args: _persisted_result("rec-2"),
        ),
    )
    with pytest.raises(
        ContractValidationError,
        match="persisted_recommendation_identity_mismatch",
    ):
        _adapter().invoke(
            "get_persisted_airport_candidate_evidence",
            {"recommendation_id": "rec-1"},
            tool_call_id=CALL_ID,
        )
    assert projected == []


def test_projection_copy_does_not_alias_raw_data(monkeypatch):
    _patch_operations(
        monkeypatch,
        (
            "get_current_decision_context",
            lambda *args: _current_result(
                "get_current_decision_context",
                DecisionEvidenceKind.DECISION_CONTEXT,
            ),
        ),
    )
    invocation = _adapter().invoke(
        "get_current_decision_context",
        {"aircraft_id": "N123AB"},
        tool_call_id=CALL_ID,
    )
    invocation.model_projection.data["aircraft_id"] = "mutated"
    invocation.model_projection.to_dict()["data"]["chain_gaps"].append("invented")
    assert invocation.raw_tool_result.data["aircraft_id"] == "n123ab"
    assert "invented" not in invocation.raw_tool_result.data["chain_gaps"]
