"""Offline LO3C orchestration through the real LO1, LO2, LO3A, and LO3B path."""

from __future__ import annotations

import json
import threading
from collections.abc import Mapping
from dataclasses import replace

import pytest

from tests.fixtures import live_ops_domain_fixtures as fixtures
from wilvor_ai.contracts import ContractValidationError, ToolResult
from wilvor_ai.live_ops import (
    find_aircraft_by_callsign,
    get_aircraft_operational_context,
    get_observed_network_state,
)
from wilvor_ai.live_ops_answer_renderer import (
    LiveOpsRenderError,
    LiveOpsRenderErrorCode,
)
from wilvor_ai.live_ops_claims import (
    CallsignMatchSetClaim,
    ConfirmedImpactClaim,
    LiveOpsCountMetric,
    LiveOpsCountMode,
    LiveOpsExecutedScope,
    LiveOpsRiskMode,
    QueryResultZeroClaim,
    RiskStateClaim,
    ScopedCountClaim,
)
from wilvor_ai.live_ops_evidence_verifier import LiveOpsTrustedEvidenceLedger
from wilvor_ai.live_ops_model_contracts import (
    LIVE_OPS_INSTRUCTION_REF,
    LiveOpsModelDecision,
    LiveOpsModelDecisionKind,
    LiveOpsProviderFailureKind,
    LiveOpsUnsupportedReason,
)
from wilvor_ai.live_ops_public_contracts import (
    LIVE_OPS_ANSWER_CANCELLED,
    LIVE_OPS_ANSWER_INVALID_REQUEST,
    LIVE_OPS_ANSWER_NONE_VERIFIED,
    LIVE_OPS_ANSWER_PROVIDER,
    LIVE_OPS_ANSWER_REFUSED,
    LIVE_OPS_ANSWER_STOPPED,
    LIVE_OPS_ANSWER_TOOL_FAILED,
    LIVE_OPS_ANSWER_UNAVAILABLE,
    LIVE_OPS_ANSWER_UNSUPPORTED,
    LiveOpsAnswerOrigin,
    LiveOpsDiscoveryStatus,
    LiveOpsEntityHint,
    LiveOpsFailureReason,
    LiveOpsPublicClaimKind,
    LiveOpsPublicRenderingPosture,
    LiveOpsPublicUnsupportedReason,
    LiveOpsPublicValidationCode,
    LiveOpsPublicVerificationOutcome,
    LiveOpsRunRequest,
    LiveOpsRunResult,
    LiveOpsRunStatus,
    LiveOpsSelectorHints,
    LiveOpsSpecialistPort,
    LiveOpsTrustedContext,
    LiveOpsUntrustedTurn,
    entity_hint_from_result,
    live_ops_reference_utc,
    present_live_ops_result,
    validate_live_ops_presentation,
)
from wilvor_ai.live_ops_public_export import (
    LiveOpsExportError,
    LiveOpsExportErrorCode,
)
from wilvor_ai.live_ops_tool_projection import LiveOpsToolProjection
from wilvor_ai.model_contracts import ProposedToolCall
from wilvor_ai import live_ops as live_ops_module
from wilvor_ai import live_ops_specialist as specialist_module
from wilvor_ai.live_ops_specialist import (
    LiveOpsProviderError,
    LiveOpsProviderTimeoutError,
    LiveOpsSpecialist,
    decode_and_run_live_ops,
)

_PROSE = "MODEL PROSE MUST NOT APPEAR"
_SECRET = "RAW-SECRET-MARKER"
_HANDLER_SECRET = "handler-secret"


class FakeProvider:
    """Scripted provider. Exceptions and callables are consumed in order."""

    def __init__(self, steps):
        self.steps = list(steps)
        self.requests = []

    def complete(self, turn):
        self.requests.append(turn)
        if not self.steps:
            raise RuntimeError("script exhausted")
        step = self.steps.pop(0)
        if isinstance(step, BaseException):
            raise step
        if callable(step):
            return step(turn)
        return step


class Clock:
    """Deterministic clock. Armed reads can expire on a chosen count."""

    def __init__(self, *, idle=fixtures.NOW - 1, expired=fixtures.NOW):
        self.idle = idle
        self.expired = expired
        self.armed = False
        self.reads = 0
        self.expire_at = None
        self.calls = 0

    def __call__(self):
        self.calls += 1
        if not self.armed:
            return self.idle
        self.reads += 1
        if self.expire_at is not None and self.reads >= self.expire_at:
            return self.expired
        return self.idle


class Cancel:
    """Deterministic cancellation. Armed reads can trip on a chosen count."""

    def __init__(self, *, value=False):
        self.value = value
        self.armed = False
        self.reads = 0
        self.trip_at = None

    def __call__(self):
        if self.value is True:
            return True
        if not self.armed:
            return False
        self.reads += 1
        if self.trip_at is not None and self.reads >= self.trip_at:
            return True
        return False


def _ids():
    state = {"n": 0}

    def factory():
        state["n"] += 1
        return "tool-" + str(state["n"])

    return factory


def _recording(log, lock=None):
    handlers = {}
    for name in (
        "search_current_hazards",
        "search_current_impacts",
        "search_current_encounters",
        "get_observed_network_state",
        "get_aircraft_operational_context",
        "get_hazard_operational_context",
        "get_airport_operational_context",
        "find_aircraft_by_callsign",
    ):
        function = getattr(live_ops_module, name)

        def make(function, name):
            def handler(call, **kwargs):
                entry = (name, call.correlation_id, call.tool_call_id, dict(kwargs))
                if lock is None:
                    log.append(entry)
                else:
                    with lock:
                        log.append(entry)
                return function(call, **kwargs)

            return handler

        handlers[name] = make(function, name)
    return handlers


def _request(text="Which aircraft are currently observed?", **kwargs):
    payload = {
        "user_text": text,
        "request_id": "req-1",
        "conversation_id": "conv-1",
    }
    payload.update(kwargs)
    return LiveOpsRunRequest(**payload)


def _context(**kwargs):
    payload = {
        "tables": fixtures.california_tables(),
        "now_epoch": fixtures.NOW,
        "correlation_id": fixtures.CORRELATION_ID,
    }
    payload.update(kwargs)
    return LiveOpsTrustedContext(**payload)


def _unique_tables():
    item = fixtures.aircraft("n1", callsign="N123AA")
    return fixtures.tables(
        aircraft=fixtures.FlexibleTable(
            records={"n1": item},
            query_items={"N123AA": [item]},
        )
    )


def _run(steps, *, req=None, handlers=None, log=None, factory=None, **ctx):
    if log is None:
        log = []
    provider = FakeProvider(steps)
    if handlers is None:
        handlers = _recording(log)
    specialist = LiveOpsSpecialist(
        provider=provider,
        tool_call_id_factory=_ids() if factory is None else factory,
        handlers=handlers,
    )
    result = specialist.run(req if req is not None else _request(), _context(**ctx))
    return result, provider, log


def _tools(*calls):
    proposed = tuple(
        item if isinstance(item, ProposedToolCall) else ProposedToolCall(item[0], item[1])
        for item in calls
    )
    return LiveOpsModelDecision(
        kind=LiveOpsModelDecisionKind.TOOL_CALLS,
        tool_calls=proposed,
    )


def _claims(*items):
    return LiveOpsModelDecision(
        kind=LiveOpsModelDecisionKind.FINAL_CLAIMS,
        claims=tuple(items),
    )


def _network_claim(projection):
    return ScopedCountClaim(
        tool_call_id=projection.tool_call_id,
        tool_name=projection.tool_name,
        metric=LiveOpsCountMetric.NETWORK_AIRCRAFT_COUNT,
        mode=LiveOpsCountMode.EXACT_RETURNED_SET,
        value=projection.data["current_aircraft_count"],
        scope=LiveOpsExecutedScope(),
    )


def _network_from_turn(turn):
    return _claims(_network_claim(turn.tool_results[-1]))


def _callsign_claim(projection):
    matches = projection.data["matches"]
    callsign = projection.data["callsign"]
    if not matches:
        return QueryResultZeroClaim(
            tool_call_id=projection.tool_call_id,
            tool_name=projection.tool_name,
            metric=LiveOpsCountMetric.CALLSIGN_MATCH_COUNT,
            scope=LiveOpsExecutedScope(callsign=callsign),
        )
    return CallsignMatchSetClaim(
        tool_call_id=projection.tool_call_id,
        callsign=callsign,
        aircraft_ids=tuple(sorted(row["aircraft_id"] for row in matches)),
    )


def _impact_claims(projection, *, include_absent=True):
    built = []
    for row in projection.data["impacts"]:
        built.append(
            ConfirmedImpactClaim(
                tool_call_id=projection.tool_call_id,
                tool_name=projection.tool_name,
                aircraft_id=row["aircraft_id"],
                hazard_id=row["hazard_id"],
                source_version=row["source_version"],
                encounter_id=row["encounter_id"],
            )
        )
        context = row.get("encounter_context") or {}
        risk = context.get("risk")
        state = (context.get("risk_link") or {}).get("state")
        if state == "PRESENT" and isinstance(risk, Mapping):
            built.append(
                RiskStateClaim(
                    tool_call_id=projection.tool_call_id,
                    tool_name=projection.tool_name,
                    mode=LiveOpsRiskMode.PRESENT,
                    encounter_id=row["encounter_id"],
                    aircraft_id=row["aircraft_id"],
                    hazard_id=row["hazard_id"],
                    risk_id=risk["risk_id"],
                    risk_level=risk["risk_level"],
                    risk_score=risk["risk_score"],
                )
            )
        elif include_absent and risk is None and state in {
            "MISSING",
            "ABSENT_FROM_CURRENT_CANDIDATES",
            "NOT_CURRENT",
        }:
            built.append(
                RiskStateClaim(
                    tool_call_id=projection.tool_call_id,
                    tool_name=projection.tool_name,
                    mode=LiveOpsRiskMode.ABSENT_FROM_OBSERVED_CANDIDATES,
                    encounter_id=row["encounter_id"],
                    aircraft_id=row["aircraft_id"],
                    hazard_id=row["hazard_id"],
                )
            )
    return tuple(built)


def _placeholder_claim():
    return ConfirmedImpactClaim(
        tool_call_id="unused-1",
        tool_name="search_current_impacts",
        aircraft_id="a",
        hazard_id="H1",
        source_version="v1",
        encounter_id="H1-A",
    )


def _assert_identity(result, *, correlation_id=fixtures.CORRELATION_ID):
    assert result.request_id == "req-1"
    assert result.conversation_id == "conv-1"
    assert result.correlation_id == correlation_id
    assert result.reference_epoch == fixtures.NOW
    assert result.reference_utc == live_ops_reference_utc(fixtures.NOW)


def _assert_closed(result, status, reason, answer):
    _assert_identity(result)
    assert result.execution_status is status
    assert result.failure is not None
    assert result.failure.reason is reason
    assert result.verification_outcome is None
    assert result.rendering_posture is None
    assert result.answer_origin is LiveOpsAnswerOrigin.FIXED_SAFE_TEXT
    assert result.answer == answer
    assert result.verified_claims == ()
    assert result.evidence_references == ()
    assert result.diagnostics == ()
    assert result.discoveries == ()
    assert result.verified_entity_refs == ()
    assert result.structural_codes == ()
    assert result.contradiction_subjects == ()
    assert result.mandatory_limitations == ()
    assert result.failure.failed_tool_name is None
    assert result.failure.validation_code is None


def _assert_no_facts(result):
    assert result.verified_claims == ()
    assert result.verified_entity_refs == ()
    assert result.discoveries == ()


def _assert_model_boundary(provider, *markers):
    assert provider.requests
    for turn in provider.requests:
        assert turn.instruction_ref == LIVE_OPS_INSTRUCTION_REF
        assert all(isinstance(item, LiveOpsToolProjection) for item in turn.tool_results)
        blob = json.dumps(turn.to_dict())
        assert "LiveOpsTrustedEvidenceLedger" not in blob
        for marker in markers:
            assert marker not in blob
        assert _forbidden_object(turn.tool_results) is False
        assert _forbidden_object(turn.prior_turns) is False
        assert _forbidden_object(turn.selector_hint_items) is False
        assert _forbidden_object(turn.entity_hint_items) is False


def _forbidden_object(value, seen=None):
    if seen is None:
        seen = set()
    marker = id(value)
    if marker in seen:
        return False
    seen.add(marker)
    if isinstance(value, (ToolResult, LiveOpsTrustedEvidenceLedger)):
        return True
    if type(value).__name__ in {"OperationalTables", "FlexibleTable"}:
        return True
    if isinstance(value, Mapping):
        return any(_forbidden_object(item, seen) for item in value.values())
    if isinstance(value, (list, tuple)):
        return any(_forbidden_object(item, seen) for item in value)
    return False


def _public_blob(result):
    payload = result.to_dict()
    assert LiveOpsRunResult.from_dict(payload).to_dict() == payload
    return json.dumps(payload)


def _tool_names(log):
    return [item[0] for item in log]


def test_one_tool_verified_completion_on_final_turn():
    result, provider, log = _run(
        [_tools(("get_observed_network_state", {})), _network_from_turn],
        max_model_turns=2,
    )
    _assert_identity(result)
    assert result.execution_status is LiveOpsRunStatus.COMPLETED
    assert result.failure is None
    assert result.verification_outcome is LiveOpsPublicVerificationOutcome.ALL_VERIFIED
    assert result.rendering_posture is LiveOpsPublicRenderingPosture.COMPLETE_VERIFIED_SET
    assert result.answer_origin is LiveOpsAnswerOrigin.DETERMINISTIC_RENDERER
    assert result.answer.startswith("These statements repeat only the verified live operations claims.")
    assert result.verified_claims[0].kind is LiveOpsPublicClaimKind.SCOPED_COUNT
    assert result.verified_claims[0].count_value == 2
    assert result.evidence_references[0].tool_name == "get_observed_network_state"
    assert _tool_names(log) == ["get_observed_network_state"]
    assert len(provider.requests) == 2
    assert len(provider.requests[0].tool_results) == 0
    assert len(provider.requests[1].tool_results) == 1
    _assert_model_boundary(provider)
    assert _PROSE not in result.answer


def test_multi_tool_batch_verifies_count_and_callsign():
    def finish(turn):
        network, callsign = turn.tool_results
        return _claims(_network_claim(network), _callsign_claim(callsign))

    result, provider, log = _run(
        [
            _tools(
                ("get_observed_network_state", {}),
                ("find_aircraft_by_callsign", {"callsign": "UAL123"}),
            ),
            finish,
        ],
        max_model_turns=2,
    )
    assert result.execution_status is LiveOpsRunStatus.COMPLETED
    assert result.verification_outcome is LiveOpsPublicVerificationOutcome.ALL_VERIFIED
    assert result.answer_origin is LiveOpsAnswerOrigin.DETERMINISTIC_RENDERER
    kinds = {item.kind for item in result.verified_claims}
    assert LiveOpsPublicClaimKind.SCOPED_COUNT in kinds
    assert LiveOpsPublicClaimKind.CALLSIGN_MATCH_SET in kinds
    assert result.discoveries[0].status is LiveOpsDiscoveryStatus.MULTIPLE_SUPPORTED_MATCHES
    assert result.discoveries[0].aircraft_ids == ("a", "c")
    assert result.verified_entity_refs == ()
    assert _tool_names(log) == ["get_observed_network_state", "find_aircraft_by_callsign"]
    assert len(provider.requests) == 2


def test_multiple_turns_accumulate_projections():
    def finish(turn):
        return _claims(_network_claim(turn.tool_results[0]), *_impact_claims(turn.tool_results[1]))

    result, provider, log = _run(
        [
            _tools(("get_observed_network_state", {})),
            _tools(("search_current_impacts", {"hazard_ids": ["H1"]})),
            finish,
        ],
        max_model_turns=3,
    )
    assert result.verification_outcome is LiveOpsPublicVerificationOutcome.ALL_VERIFIED
    assert [len(item.tool_results) for item in provider.requests] == [0, 1, 2]
    assert _tool_names(log) == ["get_observed_network_state", "search_current_impacts"]
    assert len(result.verified_claims) >= 2
    _assert_model_boundary(provider)


def test_partially_verified_omits_unverified_aircraft():
    def finish(turn):
        projection = turn.tool_results[0]
        row = next(item for item in projection.data["impacts"] if item["encounter_id"] == "H1-A")
        good = ConfirmedImpactClaim(
            tool_call_id=projection.tool_call_id,
            tool_name=projection.tool_name,
            aircraft_id=row["aircraft_id"],
            hazard_id=row["hazard_id"],
            source_version=row["source_version"],
            encounter_id=row["encounter_id"],
        )
        bad = ConfirmedImpactClaim(
            tool_call_id=projection.tool_call_id,
            tool_name=projection.tool_name,
            aircraft_id="missing",
            hazard_id=row["hazard_id"],
            source_version=row["source_version"],
            encounter_id=row["encounter_id"],
        )
        return _claims(good, bad)

    result, provider, log = _run(
        [_tools(("search_current_impacts", {"hazard_ids": ["H1"]})), finish],
        max_model_turns=2,
    )
    assert result.execution_status is LiveOpsRunStatus.COMPLETED
    assert result.verification_outcome is LiveOpsPublicVerificationOutcome.PARTIALLY_VERIFIED
    assert result.rendering_posture is LiveOpsPublicRenderingPosture.INCOMPLETE_VERIFIED_SUBSET
    assert result.answer_origin is LiveOpsAnswerOrigin.DETERMINISTIC_RENDERER
    assert result.answer.startswith("These statements repeat only the verified subset")
    assert [item.aircraft_id for item in result.verified_claims] == ["a"]
    assert result.diagnostics
    assert "missing" not in result.answer
    assert len(log) == 1
    assert len(provider.requests) == 2


def test_none_verified_uses_fixed_text_and_diagnostics():
    def finish(turn):
        projection = turn.tool_results[0]
        return _claims(
            ConfirmedImpactClaim(
                tool_call_id=projection.tool_call_id,
                tool_name=projection.tool_name,
                aircraft_id="missing",
                hazard_id="H1",
                source_version="v1",
                encounter_id="H1-A",
            )
        )

    result, provider, _log = _run(
        [_tools(("search_current_impacts", {"hazard_ids": ["H1"]})), finish],
        max_model_turns=2,
    )
    assert result.execution_status is LiveOpsRunStatus.COMPLETED
    assert result.failure is None
    assert result.verification_outcome is LiveOpsPublicVerificationOutcome.NONE_VERIFIED
    assert result.rendering_posture is LiveOpsPublicRenderingPosture.NO_VERIFIED_FACTS
    assert result.answer_origin is LiveOpsAnswerOrigin.FIXED_SAFE_TEXT
    assert result.answer == LIVE_OPS_ANSWER_NONE_VERIFIED
    assert result.verified_claims == ()
    assert result.diagnostics
    assert result.discoveries == ()
    assert result.verified_entity_refs == ()
    assert len(provider.requests) == 2


def test_unique_callsign_discovery_exports_entity_ref():
    result, provider, log = _run(
        [lambda turn: _claims(_callsign_claim(turn.tool_results[0]))],
        req=_request(selector_hints=LiveOpsSelectorHints(callsign="N123AA")),
        tables=_unique_tables(),
        max_model_turns=1,
    )
    assert result.verification_outcome is LiveOpsPublicVerificationOutcome.ALL_VERIFIED
    assert result.discoveries[0].status is LiveOpsDiscoveryStatus.UNIQUE_SUPPORTED_MATCH
    assert result.discoveries[0].aircraft_ids == ("n1",)
    assert result.discoveries[0].identity_resolved is True
    assert result.discoveries[0].selection_authorized is False
    assert result.verified_entity_refs[0].entity_id == "n1"
    assert result.verified_entity_refs[0].callsign == "N123AA"
    assert _tool_names(log) == ["find_aircraft_by_callsign"]
    assert len(provider.requests) == 1
    assert provider.requests[0].tool_results[0].tool_name == "find_aircraft_by_callsign"


def test_multiple_callsign_matches_do_not_select_an_aircraft():
    result, _provider, log = _run(
        [lambda turn: _claims(_callsign_claim(turn.tool_results[0]))],
        req=_request(selector_hints=LiveOpsSelectorHints(callsign="UAL123")),
        max_model_turns=1,
    )
    discovery = result.discoveries[0]
    assert discovery.status is LiveOpsDiscoveryStatus.MULTIPLE_SUPPORTED_MATCHES
    assert discovery.aircraft_ids == ("a", "c")
    assert discovery.identity_resolved is False
    assert discovery.selection_authorized is False
    assert result.verified_entity_refs == ()
    assert len(log) == 1


def test_callsign_authoritative_zero():
    result, _provider, log = _run(
        [lambda turn: _claims(_callsign_claim(turn.tool_results[0]))],
        req=_request(selector_hints=LiveOpsSelectorHints(callsign="NONE")),
        max_model_turns=1,
    )
    assert result.verification_outcome is LiveOpsPublicVerificationOutcome.ALL_VERIFIED
    assert result.verified_claims[0].kind is LiveOpsPublicClaimKind.QUERY_RESULT_ZERO
    assert result.discoveries[0].status is LiveOpsDiscoveryStatus.NO_SUPPORTED_MATCH
    assert result.discoveries[0].aircraft_ids == ()
    assert result.verified_entity_refs == ()
    assert len(log) == 1


def test_incomplete_discovery_does_not_fabricate_a_match():
    def finish(turn):
        projection = turn.tool_results[0]
        return _claims(
            CallsignMatchSetClaim(
                tool_call_id=projection.tool_call_id,
                callsign=projection.data["callsign"],
                aircraft_ids=("nope",),
            )
        )

    result, _provider, log = _run(
        [finish],
        req=_request(selector_hints=LiveOpsSelectorHints(callsign="N123AA")),
        tables=_unique_tables(),
        max_model_turns=1,
    )
    assert result.verification_outcome is LiveOpsPublicVerificationOutcome.NONE_VERIFIED
    assert result.answer == LIVE_OPS_ANSWER_NONE_VERIFIED
    assert result.verified_claims == ()
    assert result.discoveries[0].status is LiveOpsDiscoveryStatus.INCOMPLETE
    assert result.discoveries[0].aircraft_ids == ()
    assert result.verified_entity_refs == ()
    assert result.diagnostics
    assert len(log) == 1


def test_repeated_callsign_searches_stay_separate():
    def decide(turn):
        if len(turn.tool_results) == 1:
            return _tools(("find_aircraft_by_callsign", {"callsign": "UAL123"}))
        return _claims(*(_callsign_claim(item) for item in turn.tool_results))

    result, provider, log = _run(
        [decide, decide],
        req=_request(selector_hints=LiveOpsSelectorHints(callsign="UAL123")),
        max_model_turns=2,
        max_tool_calls=2,
    )
    assert result.verification_outcome is LiveOpsPublicVerificationOutcome.ALL_VERIFIED
    assert len(result.discoveries) == 2
    assert {item.evidence_tool_call_id for item in result.discoveries} == {"tool-1", "tool-2"}
    assert all(
        item.status is LiveOpsDiscoveryStatus.MULTIPLE_SUPPORTED_MATCHES for item in result.discoveries
    )
    assert result.verified_entity_refs == ()
    assert _tool_names(log) == ["find_aircraft_by_callsign", "find_aircraft_by_callsign"]
    assert len(provider.requests) == 2


def test_stored_impact_and_recorded_risk_keep_their_limits():
    def finish(turn):
        return _claims(*_impact_claims(turn.tool_results[0]))

    result, _provider, log = _run(
        [_tools(("search_current_impacts", {"hazard_ids": ["H1"]})), finish],
        max_model_turns=2,
    )
    assert result.verification_outcome is LiveOpsPublicVerificationOutcome.ALL_VERIFIED
    assert result.answer_origin is LiveOpsAnswerOrigin.DETERMINISTIC_RENDERER
    impacts = [item for item in result.verified_claims if item.kind is LiveOpsPublicClaimKind.CONFIRMED_IMPACT]
    risks = [item for item in result.verified_claims if item.kind is LiveOpsPublicClaimKind.RISK_STATE]
    assert {item.encounter_id for item in impacts} == {"H1-A", "H1-B"}
    present = next(item for item in risks if item.risk_level == "HIGH")
    assert present.risk_id == "risk-1"
    assert present.risk_score == 90
    assert "None of them establishes physical entry" in result.answer
    assert "not a newly calculated risk assessment" in result.answer
    assert 'stored level "HIGH"' in result.answer
    assert "stored score 90" in result.answer
    assert _tool_names(log) == ["search_current_impacts"]


def test_public_result_round_trips():
    result, _provider, _log = _run(
        [_tools(("get_observed_network_state", {})), _network_from_turn],
        max_model_turns=2,
    )
    assert LiveOpsRunResult.from_dict(result.to_dict()) == result
    assert "raw_tool_result" not in result.to_dict()


@pytest.mark.parametrize("reason", list(LiveOpsUnsupportedReason))
def test_unsupported_reasons_publish_no_facts(reason):
    decision = LiveOpsModelDecision(
        kind=LiveOpsModelDecisionKind.UNSUPPORTED,
        unsupported_reason=reason,
    )
    result, provider, log = _run([decision], max_model_turns=1)
    _assert_closed(
        result,
        LiveOpsRunStatus.UNSUPPORTED,
        LiveOpsFailureReason.UNSUPPORTED,
        LIVE_OPS_ANSWER_UNSUPPORTED,
    )
    assert result.failure.unsupported_reason is LiveOpsPublicUnsupportedReason(reason.value)
    assert log == []
    assert len(provider.requests) == 1


def test_model_refusal_is_not_a_provider_failure():
    decision = LiveOpsModelDecision(
        kind=LiveOpsModelDecisionKind.REFUSAL,
        refusal_code="model_refused",
    )
    result, provider, log = _run([decision])
    _assert_closed(
        result,
        LiveOpsRunStatus.REFUSED,
        LiveOpsFailureReason.MODEL_REFUSAL,
        LIVE_OPS_ANSWER_REFUSED,
    )
    assert result.failure.refusal_code == "model_refused"
    assert log == []
    assert len(provider.requests) == 1


def test_unknown_tool_then_valid_retry():
    result, provider, log = _run(
        [
            _tools(("not_a_catalog_tool", {})),
            _tools(("get_observed_network_state", {})),
            _network_from_turn,
        ],
        max_model_turns=3,
    )
    assert result.verification_outcome is LiveOpsPublicVerificationOutcome.ALL_VERIFIED
    assert provider.requests[1].validation_feedback.code.value == "UNKNOWN_TOOL"
    assert _tool_names(log) == ["get_observed_network_state"]
    assert len(provider.requests) == 3


def test_forbidden_argument_then_valid_retry():
    result, provider, log = _run(
        [
            _tools(("get_observed_network_state", {"tables": "secret"})),
            _tools(("get_observed_network_state", {})),
            _network_from_turn,
        ],
        max_model_turns=3,
    )
    assert result.verification_outcome is LiveOpsPublicVerificationOutcome.ALL_VERIFIED
    assert provider.requests[1].validation_feedback.code.value == "TRUSTED_ARGUMENT_FORBIDDEN"
    assert _tool_names(log) == ["get_observed_network_state"]


def test_repeated_invalid_batches_exhaust_without_execution():
    result, provider, log = _run(
        [
            _tools(("not_a_catalog_tool", {})),
            _tools(("not_a_catalog_tool", {})),
        ],
        max_model_turns=2,
    )
    _assert_identity(result)
    assert result.execution_status is LiveOpsRunStatus.INVALID_REQUEST
    assert result.failure.reason is LiveOpsFailureReason.TOOL_CALL_VALIDATION_EXHAUSTED
    assert result.failure.validation_code is LiveOpsPublicValidationCode.UNKNOWN_TOOL
    assert result.failure.failed_tool_name is None
    assert result.answer == LIVE_OPS_ANSWER_INVALID_REQUEST
    assert result.verification_outcome is None
    _assert_no_facts(result)
    assert log == []
    assert len(provider.requests) == 2
    assert provider.requests[1].validation_feedback.code.value == "UNKNOWN_TOOL"


def test_partial_batch_execution_failure_denies_the_run():
    log = []
    handlers = _recording(log)

    def boom(call, **_kwargs):
        log.append(("find_aircraft_by_callsign", call.correlation_id, call.tool_call_id, {}))
        raise RuntimeError(_HANDLER_SECRET)

    handlers["find_aircraft_by_callsign"] = boom
    result, provider, used = _run(
        [
            _tools(
                ("get_observed_network_state", {}),
                ("find_aircraft_by_callsign", {"callsign": "UAL123"}),
            )
        ],
        handlers=handlers,
        log=log,
        max_model_turns=2,
    )
    assert result.execution_status is LiveOpsRunStatus.TOOL_FAILED
    assert result.failure.reason is LiveOpsFailureReason.TOOL_EXECUTION_FAILED
    assert result.verification_outcome is LiveOpsPublicVerificationOutcome.NOT_AUTHORIZED
    assert result.rendering_posture is LiveOpsPublicRenderingPosture.NOT_RENDERABLE
    assert result.answer == LIVE_OPS_ANSWER_TOOL_FAILED
    assert "RUN_EXECUTION_FAILED" in result.structural_codes
    _assert_no_facts(result)
    assert result.evidence_references == ()
    assert _HANDLER_SECRET not in _public_blob(result)
    assert _tool_names(used) == ["get_observed_network_state", "find_aircraft_by_callsign"]
    assert len(provider.requests) == 1


def test_raw_identity_mismatch_stays_inside_the_ledger():
    log = []
    handlers = _recording(log)

    def mismatch(call):
        raw = get_observed_network_state(call)
        data = dict(raw.data)
        data["marker"] = _SECRET
        log.append(("get_observed_network_state", call.correlation_id, call.tool_call_id, {}))
        return replace(raw, correlation_id="other-corr", data=data)

    handlers["get_observed_network_state"] = mismatch
    result, provider, _log = _run(
        [_tools(("get_observed_network_state", {}))],
        handlers=handlers,
        log=log,
        max_model_turns=2,
    )
    assert result.execution_status is LiveOpsRunStatus.TOOL_FAILED
    assert result.failure.reason is LiveOpsFailureReason.TOOL_EXECUTION_FAILED
    assert "RUN_EXECUTION_FAILED" in result.structural_codes
    _assert_no_facts(result)
    assert _SECRET not in _public_blob(result)
    _assert_model_boundary(provider, _SECRET)


def test_projection_failure_does_not_publish_the_raw_payload():
    log = []
    handlers = _recording(log)

    def broken(call, aircraft_id):
        raw = get_aircraft_operational_context(call, aircraft_id)
        data = dict(raw.data)
        data.pop("found", None)
        data["marker"] = _SECRET
        log.append(("get_aircraft_operational_context", call.correlation_id, call.tool_call_id, {}))
        return replace(raw, data=data)

    handlers["get_aircraft_operational_context"] = broken
    result, provider, _log = _run(
        [_tools(("get_aircraft_operational_context", {"aircraft_id": "a"}))],
        handlers=handlers,
        log=log,
        max_model_turns=2,
    )
    assert result.execution_status is LiveOpsRunStatus.TOOL_FAILED
    assert result.verification_outcome is LiveOpsPublicVerificationOutcome.NOT_AUTHORIZED
    assert "RUN_EXECUTION_FAILED" in result.structural_codes
    _assert_no_facts(result)
    assert _SECRET not in _public_blob(result)
    _assert_model_boundary(provider, _SECRET)


def test_non_decision_output_is_malformed():
    result, provider, log = _run([object()])
    _assert_closed(
        result,
        LiveOpsRunStatus.PROVIDER_FAILED,
        LiveOpsFailureReason.MALFORMED_OUTPUT,
        LIVE_OPS_ANSWER_PROVIDER,
    )
    assert log == []
    assert len(provider.requests) == 1


def test_contract_validation_from_provider_is_malformed_output():
    result, _provider, log = _run([ContractValidationError("bad-output-secret")])
    _assert_closed(
        result,
        LiveOpsRunStatus.PROVIDER_FAILED,
        LiveOpsFailureReason.MALFORMED_OUTPUT,
        LIVE_OPS_ANSWER_PROVIDER,
    )
    assert "bad-output-secret" not in _public_blob(result)
    assert log == []


def test_tool_calls_on_the_only_turn_do_not_dispatch():
    result, provider, log = _run(
        [_tools(("get_observed_network_state", {}))],
        max_model_turns=1,
    )
    _assert_closed(
        result,
        LiveOpsRunStatus.BUDGET_EXHAUSTED,
        LiveOpsFailureReason.MODEL_TURNS,
        LIVE_OPS_ANSWER_STOPPED,
    )
    assert log == []
    assert len(provider.requests) == 1


def test_second_tool_after_budget_is_not_executed():
    result, provider, log = _run(
        [
            _tools(("get_observed_network_state", {})),
            _tools(("find_aircraft_by_callsign", {"callsign": "UAL123"})),
        ],
        max_model_turns=3,
        max_tool_calls=1,
    )
    _assert_closed(
        result,
        LiveOpsRunStatus.BUDGET_EXHAUSTED,
        LiveOpsFailureReason.TOOL_CALLS,
        LIVE_OPS_ANSWER_STOPPED,
    )
    assert _tool_names(log) == ["get_observed_network_state"]
    assert len(provider.requests) == 2


def test_two_call_batch_with_one_slot_executes_none():
    result, provider, log = _run(
        [
            _tools(
                ("get_observed_network_state", {}),
                ("find_aircraft_by_callsign", {"callsign": "UAL123"}),
            )
        ],
        max_model_turns=2,
        max_tool_calls=1,
    )
    _assert_closed(
        result,
        LiveOpsRunStatus.BUDGET_EXHAUSTED,
        LiveOpsFailureReason.TOOL_CALLS,
        LIVE_OPS_ANSWER_STOPPED,
    )
    assert log == []
    assert len(provider.requests) == 1


def test_tool_calls_on_the_final_permitted_turn_do_not_dispatch():
    result, provider, log = _run(
        [
            _tools(("get_observed_network_state", {})),
            _tools(("find_aircraft_by_callsign", {"callsign": "UAL123"})),
        ],
        max_model_turns=2,
        max_tool_calls=2,
    )
    _assert_closed(
        result,
        LiveOpsRunStatus.BUDGET_EXHAUSTED,
        LiveOpsFailureReason.MODEL_TURNS,
        LIVE_OPS_ANSWER_STOPPED,
    )
    assert _tool_names(log) == ["get_observed_network_state"]
    assert len(provider.requests) == 2
    assert provider.requests[1].tool_results[0].tool_name == "get_observed_network_state"


def test_cancellation_before_execution():
    clock = Clock()

    def fail_if_called():
        raise AssertionError("clock")

    clock.__call__ = fail_if_called
    result, provider, log = _run(
        [_tools(("get_observed_network_state", {}))],
        clock=clock,
        deadline_epoch=fixtures.NOW + 10,
        cancelled=lambda: True,
    )
    _assert_closed(
        result,
        LiveOpsRunStatus.CANCELLED,
        LiveOpsFailureReason.CANCELLED,
        LIVE_OPS_ANSWER_CANCELLED,
    )
    assert provider.requests == []
    assert log == []


def test_cancellation_between_turns():
    cancel = Cancel()
    log = []
    handlers = _recording(log)
    real = handlers["get_observed_network_state"]

    def arm(call):
        cancel.armed = True
        cancel.trip_at = 2
        return real(call)

    handlers["get_observed_network_state"] = arm
    result, provider, used = _run(
        [_tools(("get_observed_network_state", {}))],
        handlers=handlers,
        log=log,
        cancelled=cancel,
        max_model_turns=3,
    )
    _assert_closed(
        result,
        LiveOpsRunStatus.CANCELLED,
        LiveOpsFailureReason.CANCELLED,
        LIVE_OPS_ANSWER_CANCELLED,
    )
    assert len(used) == 1
    assert len(provider.requests) == 1


def test_cancellation_after_dispatch():
    cancel = Cancel()
    log = []
    handlers = _recording(log)
    real = handlers["get_observed_network_state"]

    def arm(call):
        cancel.armed = True
        cancel.trip_at = 1
        return real(call)

    handlers["get_observed_network_state"] = arm
    result, provider, used = _run(
        [_tools(("get_observed_network_state", {}))],
        handlers=handlers,
        log=log,
        cancelled=cancel,
        max_model_turns=3,
    )
    _assert_closed(
        result,
        LiveOpsRunStatus.CANCELLED,
        LiveOpsFailureReason.CANCELLED,
        LIVE_OPS_ANSWER_CANCELLED,
    )
    assert len(used) == 1
    assert len(provider.requests) == 1


def test_cancellation_before_final_publication(monkeypatch):
    cancel = Cancel()
    rendered = {"called": False}
    real_render = specialist_module.render_live_ops_answer

    def render(export):
        rendered["called"] = True
        return real_render(export)

    def finish(turn):
        cancel.armed = True
        cancel.trip_at = 5
        return _network_from_turn(turn)

    monkeypatch.setattr(specialist_module, "render_live_ops_answer", render)
    result, _provider, log = _run(
        [_tools(("get_observed_network_state", {})), finish],
        cancelled=cancel,
        max_model_turns=2,
    )
    _assert_closed(
        result,
        LiveOpsRunStatus.CANCELLED,
        LiveOpsFailureReason.CANCELLED,
        LIVE_OPS_ANSWER_CANCELLED,
    )
    assert rendered["called"] is True
    assert len(log) == 1
    assert "2" not in result.answer


def test_deadline_already_reached():
    result, provider, log = _run(
        [_tools(("get_observed_network_state", {}))],
        clock=lambda: fixtures.NOW,
        deadline_epoch=fixtures.NOW,
    )
    _assert_closed(
        result,
        LiveOpsRunStatus.RUNTIME_TIMEOUT,
        LiveOpsFailureReason.RUNTIME_TIMEOUT,
        LIVE_OPS_ANSWER_STOPPED,
    )
    assert result.reference_epoch == fixtures.NOW
    assert provider.requests == []
    assert log == []


def test_deadline_after_provider_discards_claims():
    clock = Clock()

    def finish(_turn):
        clock.armed = True
        clock.expire_at = 1
        return _claims(_placeholder_claim())

    result, provider, log = _run(
        [finish],
        clock=clock,
        deadline_epoch=fixtures.NOW,
    )
    _assert_closed(
        result,
        LiveOpsRunStatus.RUNTIME_TIMEOUT,
        LiveOpsFailureReason.RUNTIME_TIMEOUT,
        LIVE_OPS_ANSWER_STOPPED,
    )
    assert log == []
    assert len(provider.requests) == 1


def test_deadline_after_tools():
    clock = Clock()
    log = []
    handlers = _recording(log)
    real = handlers["get_observed_network_state"]

    def arm(call):
        clock.armed = True
        clock.expire_at = 1
        return real(call)

    handlers["get_observed_network_state"] = arm
    result, provider, used = _run(
        [_tools(("get_observed_network_state", {}))],
        handlers=handlers,
        log=log,
        clock=clock,
        deadline_epoch=fixtures.NOW,
        max_model_turns=3,
    )
    _assert_closed(
        result,
        LiveOpsRunStatus.RUNTIME_TIMEOUT,
        LiveOpsFailureReason.RUNTIME_TIMEOUT,
        LIVE_OPS_ANSWER_STOPPED,
    )
    assert len(used) == 1
    assert len(provider.requests) == 1


def test_deadline_before_rendering(monkeypatch):
    clock = Clock()
    rendered = {"called": False}

    def render(_export):
        rendered["called"] = True
        raise AssertionError("render")

    def finish(turn):
        clock.armed = True
        clock.expire_at = 4
        return _network_from_turn(turn)

    monkeypatch.setattr(specialist_module, "render_live_ops_answer", render)
    result, _provider, log = _run(
        [_tools(("get_observed_network_state", {})), finish],
        clock=clock,
        deadline_epoch=fixtures.NOW,
        max_model_turns=2,
    )
    _assert_closed(
        result,
        LiveOpsRunStatus.RUNTIME_TIMEOUT,
        LiveOpsFailureReason.RUNTIME_TIMEOUT,
        LIVE_OPS_ANSWER_STOPPED,
    )
    assert rendered["called"] is False
    assert len(log) == 1


def test_deadline_equality_is_expired():
    result, provider, log = _run(
        [_claims(_placeholder_claim())],
        clock=lambda: fixtures.NOW,
        deadline_epoch=fixtures.NOW,
    )
    assert result.execution_status is LiveOpsRunStatus.RUNTIME_TIMEOUT
    assert result.failure.reason is LiveOpsFailureReason.RUNTIME_TIMEOUT
    assert provider.requests == []
    assert log == []


@pytest.mark.parametrize(
    ("clock_result", "cancelled_result"),
    [(True, False), (fixtures.NOW, 1)],
)
def test_invalid_callback_types_fail_closed(clock_result, cancelled_result):
    result, provider, log = _run(
        [_tools(("get_observed_network_state", {}))],
        clock=lambda: clock_result,
        deadline_epoch=fixtures.NOW + 10,
        cancelled=lambda: cancelled_result,
    )
    _assert_closed(
        result,
        LiveOpsRunStatus.UNAVAILABLE,
        LiveOpsFailureReason.INTERNAL_ERROR,
        LIVE_OPS_ANSWER_UNAVAILABLE,
    )
    assert provider.requests == []
    assert log == []


def test_cancellation_beats_an_invalid_clock():
    def clock():
        raise AssertionError("clock")

    result, provider, _log = _run(
        [_tools(("get_observed_network_state", {}))],
        clock=clock,
        deadline_epoch=fixtures.NOW + 10,
        cancelled=lambda: True,
    )
    assert result.execution_status is LiveOpsRunStatus.CANCELLED
    assert provider.requests == []


@pytest.mark.parametrize(
    "callback",
    ["cancelled", "clock"],
)
def test_callback_exceptions_do_not_escape(callback):
    def explode():
        raise RuntimeError(callback + "-secret")

    kwargs = {"cancelled": explode} if callback == "cancelled" else {
        "clock": explode,
        "deadline_epoch": fixtures.NOW + 10,
    }
    result, provider, log = _run([_tools(("get_observed_network_state", {}))], **kwargs)
    _assert_closed(
        result,
        LiveOpsRunStatus.UNAVAILABLE,
        LiveOpsFailureReason.INTERNAL_ERROR,
        LIVE_OPS_ANSWER_UNAVAILABLE,
    )
    assert callback + "-secret" not in _public_blob(result)
    assert provider.requests == []
    assert log == []


def test_provider_transport_failure_hides_exception_text():
    result, provider, log = _run([RuntimeError("api-secret")])
    _assert_closed(
        result,
        LiveOpsRunStatus.PROVIDER_FAILED,
        LiveOpsFailureReason.TRANSPORT_OR_API,
        LIVE_OPS_ANSWER_PROVIDER,
    )
    assert "api-secret" not in _public_blob(result)
    assert len(provider.requests) == 1
    assert log == []


def test_provider_timeout_is_distinct_from_runtime_timeout():
    result, _provider, log = _run(
        [LiveOpsProviderTimeoutError()],
        clock=lambda: fixtures.NOW - 1,
        deadline_epoch=fixtures.NOW + 10,
    )
    _assert_closed(
        result,
        LiveOpsRunStatus.PROVIDER_TIMEOUT,
        LiveOpsFailureReason.PROVIDER_TIMEOUT,
        LIVE_OPS_ANSWER_PROVIDER,
    )
    assert log == []


def test_context_length_uses_the_provider_failure_status():
    result, _provider, _log = _run(
        [LiveOpsProviderError(LiveOpsProviderFailureKind.CONTEXT_LENGTH)]
    )
    _assert_closed(
        result,
        LiveOpsRunStatus.PROVIDER_FAILED,
        LiveOpsFailureReason.CONTEXT_LENGTH,
        LIVE_OPS_ANSWER_PROVIDER,
    )


def test_cancellation_beats_a_provider_failure():
    cancel = Cancel(value=False)

    def fail(_turn):
        cancel.value = True
        raise LiveOpsProviderError(LiveOpsProviderFailureKind.TRANSPORT_OR_API)

    result, provider, _log = _run([fail], cancelled=cancel)
    _assert_closed(
        result,
        LiveOpsRunStatus.CANCELLED,
        LiveOpsFailureReason.CANCELLED,
        LIVE_OPS_ANSWER_CANCELLED,
    )
    assert len(provider.requests) == 1


def test_deadline_beats_a_provider_failure():
    clock = Clock()

    def fail(_turn):
        clock.armed = True
        clock.expire_at = 1
        raise RuntimeError("api-secret")

    result, _provider, _log = _run(
        [fail],
        clock=clock,
        deadline_epoch=fixtures.NOW,
    )
    _assert_closed(
        result,
        LiveOpsRunStatus.RUNTIME_TIMEOUT,
        LiveOpsFailureReason.RUNTIME_TIMEOUT,
        LIVE_OPS_ANSWER_STOPPED,
    )
    assert "api-secret" not in result.answer


def test_export_failure_is_internal_and_not_none_verified(monkeypatch):
    def fail(*_args, **_kwargs):
        raise LiveOpsExportError(LiveOpsExportErrorCode.UNREPRESENTABLE_SUBJECT)

    monkeypatch.setattr(specialist_module, "export_live_ops_evidence", fail)
    result, _provider, log = _run(
        [_tools(("get_observed_network_state", {})), _network_from_turn],
        max_model_turns=2,
    )
    _assert_closed(
        result,
        LiveOpsRunStatus.UNAVAILABLE,
        LiveOpsFailureReason.INTERNAL_ERROR,
        LIVE_OPS_ANSWER_UNAVAILABLE,
    )
    assert result.verification_outcome is not LiveOpsPublicVerificationOutcome.NONE_VERIFIED
    assert "UNREPRESENTABLE_SUBJECT" not in _public_blob(result)
    assert len(log) == 1


def test_renderer_failure_is_internal_and_not_none_verified(monkeypatch):
    def fail(_export):
        raise LiveOpsRenderError(LiveOpsRenderErrorCode.TEMPLATE_FAILURE)

    monkeypatch.setattr(specialist_module, "render_live_ops_answer", fail)
    result, _provider, _log = _run(
        [_tools(("get_observed_network_state", {})), _network_from_turn],
        max_model_turns=2,
    )
    _assert_closed(
        result,
        LiveOpsRunStatus.UNAVAILABLE,
        LiveOpsFailureReason.INTERNAL_ERROR,
        LIVE_OPS_ANSWER_UNAVAILABLE,
    )
    assert "TEMPLATE_FAILURE" not in _public_blob(result)


def test_public_result_construction_failure_drops_verified_claims(monkeypatch):
    real = specialist_module.LiveOpsRunResult

    def guarded(*args, **kwargs):
        if kwargs.get("execution_status") is LiveOpsRunStatus.COMPLETED:
            raise ContractValidationError("assembly-secret")
        return real(*args, **kwargs)

    monkeypatch.setattr(specialist_module, "LiveOpsRunResult", guarded)
    result, _provider, log = _run(
        [_tools(("get_observed_network_state", {})), _network_from_turn],
        max_model_turns=2,
    )
    _assert_closed(
        result,
        LiveOpsRunStatus.UNAVAILABLE,
        LiveOpsFailureReason.INTERNAL_ERROR,
        LIVE_OPS_ANSWER_UNAVAILABLE,
    )
    assert "assembly-secret" not in _public_blob(result)
    assert len(log) == 1


def test_duplicate_tool_call_id_stops_before_the_second_handler():
    log = []
    handlers = _recording(log)
    result, _provider, used = _run(
        [
            _tools(("get_observed_network_state", {})),
            _tools(("find_aircraft_by_callsign", {"callsign": "UAL123"})),
        ],
        handlers=handlers,
        log=log,
        factory=lambda: "same-id",
        max_model_turns=3,
        max_tool_calls=2,
    )
    assert result.execution_status is LiveOpsRunStatus.TOOL_FAILED
    assert result.failure.reason is LiveOpsFailureReason.TOOL_EXECUTION_FAILED
    assert "RUN_EXECUTION_FAILED" in result.structural_codes
    _assert_no_facts(result)
    assert _tool_names(used) == ["get_observed_network_state"]


def test_raw_results_do_not_cross_the_model_or_public_boundary():
    result, provider, _log = _run(
        [_tools(("get_observed_network_state", {})), _network_from_turn],
        max_model_turns=2,
    )
    _assert_model_boundary(provider, _SECRET)
    blob = _public_blob(result)
    assert "raw_tool_result" not in blob
    assert "source_records" not in blob
    assert result.verified_claims


def test_wrong_run_input_types_raise():
    specialist = LiveOpsSpecialist(provider=FakeProvider([]))
    context = _context()
    request = _request()
    with pytest.raises(TypeError):
        specialist.run({}, context)
    with pytest.raises(TypeError):
        specialist.run(request, {})
    with pytest.raises(TypeError):
        specialist.run(request, None)
    assert specialist._provider.requests == []


def test_invalid_serialized_request_does_not_echo_ids():
    context = _context()
    specialist = LiveOpsSpecialist(provider=FakeProvider([]))
    rejected = decode_and_run_live_ops(
        specialist,
        {"request_id": "attacker-id", "conversation_id": "attacker-conv"},
        context,
    )
    assert rejected.execution_status is LiveOpsRunStatus.INVALID_REQUEST
    assert rejected.failure.reason is LiveOpsFailureReason.MALFORMED_PUBLIC_REQUEST
    assert rejected.request_id is None
    assert rejected.conversation_id is None
    assert rejected.answer == LIVE_OPS_ANSWER_INVALID_REQUEST
    oversized = decode_and_run_live_ops(
        specialist,
        {
            "schema_version": "live-ops-public-v1",
            "user_text": "status",
            "request_id": "x" * 257,
        },
        context,
    )
    assert oversized.request_id is None
    valid = _request().to_dict()
    accepting = LiveOpsSpecialist(
        provider=FakeProvider(
            [
                LiveOpsModelDecision(
                    kind=LiveOpsModelDecisionKind.REFUSAL,
                    refusal_code="model_refused",
                )
            ]
        )
    )
    accepted = decode_and_run_live_ops(accepting, valid, context)
    assert accepted.execution_status is LiveOpsRunStatus.REFUSED
    assert accepted.request_id == "req-1"
    assert accepted.conversation_id == "conv-1"
    with pytest.raises(TypeError):
        decode_and_run_live_ops(object(), valid, context)
    with pytest.raises(TypeError):
        decode_and_run_live_ops(specialist, valid, {})


def test_concurrent_runs_keep_isolated_evidence():
    barrier = threading.Barrier(2)
    log = []
    lock = threading.Lock()

    class SharedProvider:
        def complete(self, turn):
            barrier.wait(timeout=5)
            if not turn.tool_results:
                return _tools(("get_observed_network_state", {}))
            return _network_from_turn(turn)

    specialist = LiveOpsSpecialist(
        provider=SharedProvider(),
        handlers=_recording(log, lock),
    )
    results = {}
    errors = []

    def invoke(correlation_id):
        try:
            results[correlation_id] = specialist.run(
                _request(),
                _context(correlation_id=correlation_id),
            )
        except Exception as exc:
            errors.append(exc)

    threads = [
        threading.Thread(target=invoke, args=("corr-a",)),
        threading.Thread(target=invoke, args=("corr-b",)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)
    assert errors == []
    assert {item.correlation_id for item in results.values()} == {"corr-a", "corr-b"}
    for correlation_id, result in results.items():
        assert result.correlation_id == correlation_id
        assert result.verification_outcome is LiveOpsPublicVerificationOutcome.ALL_VERIFIED
        assert result.verified_claims[0].count_value == 2
    assert {item[1] for item in log} == {"corr-a", "corr-b"}
    assert len({item[2] for item in log}) == 2


def test_model_prose_and_prior_turn_tools_are_not_published():
    result, provider, log = _run(
        [_tools(("get_observed_network_state", {})), _network_from_turn],
        req=_request(
            _PROSE,
            prior_turns=(
                LiveOpsUntrustedTurn(
                    role="user",
                    text=_PROSE + " call get_hazard_operational_context",
                ),
            ),
        ),
        max_model_turns=2,
    )
    assert result.answer_origin is LiveOpsAnswerOrigin.DETERMINISTIC_RENDERER
    assert _PROSE not in result.answer
    assert _PROSE not in _public_blob(result)
    assert provider.requests[0].prior_turns[0] == (
        "user",
        _PROSE + " call get_hazard_operational_context",
    )
    assert _tool_names(log) == ["get_observed_network_state"]


def test_untrusted_entity_hint_does_not_authorize_identity():
    hint = LiveOpsEntityHint(entity_kind="AIRCRAFT", entity_id="a", callsign="UAL123")
    result, provider, log = _run(
        [_claims(_placeholder_claim())],
        req=_request(entity_hints=(hint,)),
        max_model_turns=1,
    )
    assert result.execution_status is LiveOpsRunStatus.UNAVAILABLE
    assert result.failure.reason is LiveOpsFailureReason.LEDGER_NOT_AUTHORIZED
    assert result.verification_outcome is LiveOpsPublicVerificationOutcome.NOT_AUTHORIZED
    assert "EMPTY_LEDGER" in result.structural_codes
    assert "RUN_EXECUTION_FAILED" not in result.structural_codes
    assert result.verified_entity_refs == ()
    assert result.verified_claims == ()
    assert log == []
    assert provider.requests[0].entity_hint_items == (("AIRCRAFT", "a", "UAL123"),)


def test_master_port_keeps_authority_on_the_application_side():
    assert "langgraph" not in __import__("sys").modules
    provider = FakeProvider(
        [lambda turn: _claims(_callsign_claim(turn.tool_results[0]))]
    )
    specialist = LiveOpsSpecialist(provider=provider, tool_call_id_factory=_ids())
    assert isinstance(specialist, LiveOpsSpecialistPort)
    request = _request(selector_hints=LiveOpsSelectorHints(callsign="N123AA"))
    context = _context(tables=_unique_tables(), max_model_turns=1)
    result = specialist.run(request, context)
    section = present_live_ops_result(result)
    validate_live_ops_presentation(result, section)
    assert result.verified_entity_refs[0].entity_id == "n1"
    hint = entity_hint_from_result(result, 0)
    assert not hasattr(hint, "authority")
    follow = LiveOpsSpecialist(
        provider=FakeProvider([_claims(_placeholder_claim())]),
        tool_call_id_factory=_ids(),
    )
    denied = follow.run(
        _request(entity_hints=(hint,)),
        _context(max_model_turns=1),
    )
    assert denied.failure.reason is LiveOpsFailureReason.LEDGER_NOT_AUTHORIZED
    assert denied.verified_entity_refs == ()
    multiple, _provider, _log = _run(
        [lambda turn: _claims(_callsign_claim(turn.tool_results[0]))],
        req=_request(selector_hints=LiveOpsSelectorHints(callsign="UAL123")),
        max_model_turns=1,
    )
    assert multiple.verified_entity_refs == ()
    assert "tables" not in result.to_dict()
    assert "raw_tool_result" not in _public_blob(result)
    _assert_model_boundary(provider)


def test_california_sigmet_investigation_uses_the_real_path():
    def decide(turn):
        if not turn.tool_results:
            return _tools(
                (
                    "search_current_hazards",
                    {"region": "California", "product_type": "SIGMET"},
                )
            )
        latest = turn.tool_results[-1]
        if latest.tool_name == "search_current_hazards":
            selected = []
            for row in latest.data["hazards"]:
                hazard = row.get("hazard") or {}
                if (
                    hazard.get("product_type") == "SIGMET"
                    and row.get("spatial_status") == "INTERSECTS"
                ):
                    selected.append(hazard["hazard_id"])
            return _tools(
                ("search_current_impacts", {"hazard_ids": sorted(set(selected))})
            )
        impact = next(item for item in turn.tool_results if item.tool_name == "search_current_impacts")
        return _claims(*_impact_claims(impact))

    result, provider, log = _run(
        [decide, decide, decide],
        req=_request(
            "Which aircraft are impacted by California SIGMETs, and what are their recorded risk levels?"
        ),
        max_model_turns=3,
        max_tool_calls=2,
    )
    assert result.execution_status is LiveOpsRunStatus.COMPLETED
    assert result.verification_outcome is LiveOpsPublicVerificationOutcome.ALL_VERIFIED
    assert result.rendering_posture is LiveOpsPublicRenderingPosture.COMPLETE_VERIFIED_SET
    assert result.answer_origin is LiveOpsAnswerOrigin.DETERMINISTIC_RENDERER
    assert result.answer != result.to_dict()["completeness_notice"]
    impacts = [item for item in result.verified_claims if item.kind is LiveOpsPublicClaimKind.CONFIRMED_IMPACT]
    present = [
        item
        for item in result.verified_claims
        if item.kind is LiveOpsPublicClaimKind.RISK_STATE and item.risk_level == "HIGH"
    ]
    assert {item.encounter_id for item in impacts} >= {"H1-A", "H1-B", "H2-A"}
    assert present[0].risk_id == "risk-1"
    assert present[0].risk_score == 90
    assert log[0][0] == "search_current_hazards"
    assert log[0][3]["product_type"] == "SIGMET"
    assert log[0][3]["region"] == "California"
    assert log[1][0] == "search_current_impacts"
    assert "None of them establishes physical entry" in result.answer
    assert "not a newly calculated risk assessment" in result.answer
    assert 'stored level "HIGH"' in result.answer
    assert "SIGMET" not in result.answer
    assert "comprehensive" not in result.answer.lower()
    assert len(provider.requests) == 3
    assert len(log) == 2
    _assert_model_boundary(provider)
    _assert_identity(result)
