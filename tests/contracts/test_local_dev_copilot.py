"""Offline tests for the loopback Copilot preview.

These tests do not call Anthropic or AWS.
"""

from __future__ import annotations

import json
from http.client import HTTPMessage
from io import BytesIO
from urllib import error

import pytest

from wilvor_ai.contracts import AgentCapability, ContractValidationError
from wilvor_ai.live_ops_model_contracts import (
    LiveOpsModelDecision,
    LiveOpsModelDecisionKind,
    LiveOpsUnsupportedReason,
)
from wilvor_ai.local_dev_anthropic_transport import (
    BoundedAnthropicMessagesTransport,
    LocalDevModelError,
)
from wilvor_ai.local_dev_copilot import (
    PRODUCTION_QUERY_PATH,
    admit_local_request,
    assert_loopback_bind,
    run_local_dev_copilot,
)
from wilvor_ai.local_dev_operator_reads import (
    OperatorDevTable,
    build_operator_tables,
    require_operator_principal,
)
from wilvor_ai.master_contracts import MasterPurpose, MasterRequirement, MasterSpecialist
from wilvor_ai.master_routing import (
    MASTER_ROUTE_PROPOSAL_SCHEMA_VERSION,
    MasterProposedCapability,
    MasterProposedStep,
    MasterRouteProposal,
    MasterRouteTemporalIntent,
)
from wilvor_ai.model_contracts import (
    MODEL_DECISION_SCHEMA_VERSION,
    ModelDecision,
    ModelDecisionKind,
    ModelTurnRequest,
    ProposedToolCall,
)
from wilvor_ai.providers.anthropic_messages import (
    DEFAULT_MODEL_ID,
    AnthropicMessagesModelProvider,
    parse_messages_response,
)
from wilvor_ai.providers.instructions import HISTORICAL_SPECIALIST_INSTRUCTION_REF
from wilvor_ai.specialist_contracts import UnsupportedReason
from wilvor_ai.sprint4_live_ops_authorization import PRODUCTION_LIVE_OPS_AWS_READ_ENABLED
from wilvor_ai.sprint5_copilot_host import SPRINT5_AI_QUERY_ROUTE_ENABLED


NOW = 1_700_000_100
TEXT = "What is the current operational status of aircraft adb65b?"
SECRET = "sk-ant-test-secret"


class _Table:
    def __init__(self, item=None):
        self.item = item
        self.scans = 0

    def get_item(self, **kwargs):
        if self.item is None:
            return {}
        return {"Item": dict(self.item)}

    def query(self, **kwargs):
        raise AssertionError("unexpected query")

    def scan(self, **kwargs):
        self.scans += 1
        raise AssertionError("scan")


class _Planner:
    def __init__(self, proposal):
        self.proposal = proposal

    def propose(self, context):
        assert context.canonical_user_text == TEXT
        return self.proposal


class _CompletePlanner:
    def __init__(self, decision):
        self.decision = decision

    def complete(self, turn):
        return self.decision


class _Specialist:
    def __init__(self, decisions):
        self.decisions = list(decisions)

    def complete(self, turn):
        return self.decisions.pop(0)


def _payload():
    return {
        "schemaVersion": "wilvor.ai.copilot.session_query.v1",
        "liveOpsSchemaVersion": "wilvor.ai.live_ops_public.v1",
        "masterRequestId": "mreq-localdev0001",
        "planningSessionId": "plan-localdev0001",
        "conversationId": "conv-localdev0001",
        "correlationId": "corr-localdev0001",
        "applicationRequestTimeUtc": "2026-11-07T21:00:00Z",
        "userText": TEXT,
        "priorTurns": [],
    }


def _proposal():
    capability = AgentCapability.RETRIEVE_DETERMINISTIC_OPERATIONAL_CONTEXT
    return MasterRouteProposal(
        user_text=TEXT,
        capabilities=(MasterProposedCapability(capability, MasterRequirement.REQUIRED),),
        steps=(
            MasterProposedStep(
                step_id="live-1",
                specialist=MasterSpecialist.LIVE_OPS,
                purpose=MasterPurpose.ANSWER,
                requirement=MasterRequirement.REQUIRED,
                capability=capability,
                temporal_intent=MasterRouteTemporalIntent.CURRENT,
            ),
        ),
    )


def _specialist_decisions():
    return [
        LiveOpsModelDecision(
            kind=LiveOpsModelDecisionKind.TOOL_CALLS,
            tool_calls=(
                ProposedToolCall(
                    name="get_aircraft_operational_context",
                    arguments={"aircraft_id": "adb65b"},
                ),
            ),
        ),
        LiveOpsModelDecision(
            kind=LiveOpsModelDecisionKind.UNSUPPORTED,
            unsupported_reason=LiveOpsUnsupportedReason.INSUFFICIENT_EVIDENCE,
        ),
    ]


def _tables(item):
    inner = {name: _Table() for name in (
        "aircraft",
        "projections",
        "projection_points",
        "hazards",
        "hazard_coordinates",
        "encounters",
        "risks",
        "airports",
        "metar",
        "taf",
        "taf_periods",
        "airport_assessments",
        "recommendations",
        "alerts",
    )}
    inner["aircraft"] = _Table(item)
    bundle, journal = build_operator_tables(inner)
    return bundle, journal, inner["aircraft"]


def _run(item):
    bundle, journal, aircraft = _tables(item)
    called = {"open": 0}

    def open_tables():
        called["open"] += 1
        return bundle, journal, {
            "evidenceProvenance": "OPERATOR_AUTHENTICATED_DEV",
            "sourceAuthenticity": "NOT_ESTABLISHED",
            "principalLabel": "operator",
        }

    result = run_local_dev_copilot(
        _payload(),
        planner=_Planner(_proposal()),
        specialist_provider=_Specialist(_specialist_decisions()),
        open_tables=open_tables,
        now_epoch=NOW,
    )
    return result, journal, aircraft, called


def _row(**overrides):
    row = {
        "aircraft_id": "adb65b",
        "callsign": "N123",
        "expires_at_epoch": NOW + 500,
        "position_time_epoch": NOW - 30,
        "position_time_utc": "2026-11-07T20:59:00Z",
    }
    row.update(overrides)
    return row


def test_production_route_and_live_aws_flags_stay_closed():
    assert SPRINT5_AI_QUERY_ROUTE_ENABLED is False
    assert PRODUCTION_LIVE_OPS_AWS_READ_ENABLED is False
    with pytest.raises(Exception, match="nonlocal_bind_rejected"):
        assert_loopback_bind("0.0.0.0")


def test_loopback_admission_rejects_nonlocal_peers_and_production_route():
    admit_local_request(
        peer_host="127.0.0.1",
        host_header="localhost:5173",
        path="/local-dev/copilot/query",
        method="POST",
    )
    with pytest.raises(Exception, match="nonlocal_peer_rejected"):
        admit_local_request(
            peer_host="10.1.2.3",
            host_header="localhost:5173",
            path="/local-dev/copilot/query",
            method="POST",
        )
    with pytest.raises(Exception, match="production_ai_query_not_served"):
        admit_local_request(
            peer_host="127.0.0.1",
            host_header="127.0.0.1:8765",
            path=PRODUCTION_QUERY_PATH,
            method="POST",
        )


def test_missing_aircraft_is_qualified_and_not_a_verified_zero():
    result, journal, _aircraft, called = _run(None)

    assert called["open"] == 1
    assert result["claimsAdmitted"] is False
    assert result["sourceAuthenticity"] == "NOT_ESTABLISHED"
    assert result["developerPreview"]["claimsAdmitted"] is False
    assert result["developerPreview"]["evidenceProvenance"] == "OPERATOR_AUTHENTICATED_DEV"
    assert result["developerPreview"]["notProduction"] is True
    assert result["pageEmpty"] is True
    assert result["verifiedZeroMinted"] is False
    assert result["developerPreview"]["missingEvidence"] == ["adb65b"]
    assert "missing" in result["developerPreview"]["answer"]
    assert "verified zero" in result["developerPreview"]["answer"]
    assert journal == [
        {
            "operation": "GetItem",
            "table": "wilvor-dev-aircraft-current-state",
            "keyAttributes": ("aircraft_id",),
            "consistentRead": True,
            "bounded": True,
            "provenance": "OPERATOR_AUTHENTICATED_DEV",
        }
    ]
    assert _aircraft.scans == 0


def test_stale_aircraft_is_not_described_as_current():
    result, journal, _aircraft, _called = _run(_row(expires_at_epoch=NOW - 10))

    preview = result["developerPreview"]
    assert preview["staleEvidence"] == ["adb65b"]
    assert preview["missingEvidence"] == []
    assert "stale" in preview["answer"]
    assert result["state"] == "INSUFFICIENT_EVIDENCE"
    assert result["assistantText"] == result["statusText"]
    assert result["evidence"][0]["freshnessBand"] == "STALE"
    assert result["evidence"][0]["verifiedZeroMinted"] is False
    assert journal[0]["operation"] == "GetItem"


def test_present_aircraft_quotes_only_returned_fields():
    result, _journal, _aircraft, _called = _run(_row())

    preview = result["developerPreview"]
    answer = preview["answer"]
    assert "N123" in answer
    assert "2026-11-07T20:59:00Z" in answer
    assert "not an admitted operational claim" in answer
    assert result["evidence"][0]["freshnessBand"] == "UNKNOWN"
    assert "Source freshness is not established" in answer
    assert "Latitude" not in answer
    assert result["evidence"][0]["sourceName"] == "wilvor-dev-aircraft-current-state"
    assert "SUPPLIED_EVIDENCE_NOT_AUTHENTICATED" in result["limitationCodes"]
    assert result["claimsAdmitted"] is False
    assert result["productionAiActive"] is False


def test_aircraft_read_and_encounter_query_reach_a_terminal_turn():
    row = _row(
        latitude=47.6,
        longitude=-122.3,
        baro_altitude_ft=12000,
        ground_speed_kt=410,
        track_deg=270,
        on_ground=False,
    )
    bundle, journal, aircraft = _tables(row)

    def empty_query(**kwargs):
        assert kwargs.get("Limit")
        return {"Items": []}

    bundle.encounters._inner.query = empty_query

    def open_tables():
        return bundle, journal, {
            "evidenceProvenance": "OPERATOR_AUTHENTICATED_DEV",
            "sourceAuthenticity": "NOT_ESTABLISHED",
        }

    decisions = [
        LiveOpsModelDecision(
            kind=LiveOpsModelDecisionKind.TOOL_CALLS,
            tool_calls=(
                ProposedToolCall(
                    name="get_aircraft_operational_context",
                    arguments={"aircraft_id": "adb65b"},
                ),
            ),
        ),
        LiveOpsModelDecision(
            kind=LiveOpsModelDecisionKind.TOOL_CALLS,
            tool_calls=(
                ProposedToolCall(
                    name="search_current_encounters",
                    arguments={"aircraft_id": "adb65b"},
                ),
            ),
        ),
        LiveOpsModelDecision(
            kind=LiveOpsModelDecisionKind.UNSUPPORTED,
            unsupported_reason=LiveOpsUnsupportedReason.INSUFFICIENT_EVIDENCE,
        ),
    ]
    result = run_local_dev_copilot(
        _payload(),
        planner=_Planner(_proposal()),
        specialist_provider=_Specialist(decisions),
        open_tables=open_tables,
        now_epoch=NOW,
    )

    preview = result["developerPreview"]
    answer = preview["answer"]
    assert "BUDGET_EXHAUSTED" not in answer
    assert "Live Ops execution status: UNSUPPORTED" in answer
    assert "Latitude 47.6" in answer
    assert "Longitude -122.3" in answer
    assert "Baro altitude ft 12000" in answer
    assert "Ground speed kt 410" in answer
    assert "Heading deg 270" in answer
    assert "On ground false" in answer
    assert "Geo altitude" not in answer
    assert "clear of hazards" in answer
    assert "not admitted operational claims" in answer
    observations = preview["toolResults"][0]["observations"]
    assert [item["field"] for item in observations] == [
        "latitude",
        "longitude",
        "baro_altitude_ft",
        "ground_speed_kt",
        "track_deg",
        "on_ground",
    ]
    assert preview["toolResults"][1]["encounterCandidateCount"] == 0
    assert result["claimsAdmitted"] is False
    assert result["sourceAuthenticity"] == "NOT_ESTABLISHED"
    assert result["productionAiActive"] is False
    assert result["evidence"][0]["freshnessBand"] == "UNKNOWN"
    assert aircraft.scans == 0
    assert any(
        item["operation"] == "Query" and item["table"] == "wilvor-dev-aircraft-hazard-encounter"
        for item in journal
    )
    assert all(item["operation"] != "Scan" for item in journal)


def test_model_cannot_switch_the_aircraft_id_or_scan():
    bundle, journal, aircraft = _tables(_row())

    def open_tables():
        return bundle, journal, {"evidenceProvenance": "OPERATOR_AUTHENTICATED_DEV", "sourceAuthenticity": "NOT_ESTABLISHED"}

    decisions = [
        LiveOpsModelDecision(
            kind=LiveOpsModelDecisionKind.TOOL_CALLS,
            tool_calls=(
                ProposedToolCall(
                    name="get_aircraft_operational_context",
                    arguments={"aircraft_id": "other-ac"},
                ),
            ),
        ),
        LiveOpsModelDecision(
            kind=LiveOpsModelDecisionKind.UNSUPPORTED,
            unsupported_reason=LiveOpsUnsupportedReason.INSUFFICIENT_EVIDENCE,
        ),
    ]
    result = run_local_dev_copilot(
        _payload(),
        planner=_Planner(_proposal()),
        specialist_provider=_Specialist(decisions),
        open_tables=open_tables,
        now_epoch=NOW,
    )

    assert journal == []
    assert aircraft.scans == 0
    assert result["developerPreview"]["toolErrors"] == ["model_selector_rejected"]
    with pytest.raises(ContractValidationError, match="scan_not_permitted"):
        OperatorDevTable("wilvor-dev-aircraft-current-state", aircraft, journal).scan()


def test_planner_aws_tool_fails_closed_before_tables_open():
    called = {"open": 0}

    def open_tables():
        called["open"] += 1
        raise AssertionError("tables opened")

    decision = ModelDecision(
        kind=ModelDecisionKind.TOOL_CALLS,
        tool_calls=(ProposedToolCall(name="GetItem", arguments={"TableName": "aircraft"}),),
    )
    result = run_local_dev_copilot(
        _payload(),
        planner=_CompletePlanner(decision),
        specialist_provider=_Specialist([]),
        open_tables=open_tables,
        now_epoch=NOW,
    )

    assert called["open"] == 0
    assert result["developerPreview"]["modelErrors"] == ["model_aws_authorization_rejected"]
    assert result["claimsAdmitted"] is False
    assert result["evidence"] == []


def test_lambda_execution_role_is_not_operator_dev_evidence():
    with pytest.raises(Exception, match="lambda_execution_role_is_not_operator_dev_evidence"):
        require_operator_principal(
            {
                "Account": "396037508959",
                "Arn": "arn:aws:sts::396037508959:assumed-role/wilvor-dev-ai-host-lambda-role/session",
            }
        )
    labeled = require_operator_principal(
        {
            "Account": "396037508959",
            "Arn": "arn:aws:sts::396037508959:assumed-role/AWSReservedSSO_Operator/session",
        }
    )
    assert labeled["evidenceProvenance"] == "OPERATOR_AUTHENTICATED_DEV"
    assert labeled["sourceAuthenticity"] == "NOT_ESTABLISHED"
    assert labeled["principalLabel"] == "AWSReservedSSO_Operator"
    assert "session" not in labeled["principalLabel"]


def test_anthropic_transport_decodes_with_the_existing_parser_and_hides_the_key():
    body = {
        "schema_version": "wilvor.ai.model_decision.v1",
        "kind": "UNSUPPORTED",
        "unsupported_reason": "OUT_OF_CATALOG",
    }
    response = {
        "type": "message",
        "role": "assistant",
        "model": DEFAULT_MODEL_ID,
        "stop_reason": "end_turn",
        "content": [{"type": "text", "text": json.dumps(body)}],
        "usage": {"input_tokens": 3, "output_tokens": 4},
    }
    seen = {}

    def send(payload, headers):
        seen["payload"] = payload
        seen["headers"] = dict(headers)
        encoded = json.dumps(payload)
        assert SECRET not in encoded
        return response

    transport = BoundedAnthropicMessagesTransport(api_key=SECRET, send=send, max_calls=1)
    provider = AnthropicMessagesModelProvider(client=transport)
    decision = provider.complete(
        ModelTurnRequest(
            user_text="status",
            instruction_ref=HISTORICAL_SPECIALIST_INSTRUCTION_REF,
        )
    )
    parsed = parse_messages_response(response, DEFAULT_MODEL_ID)

    assert decision.kind is parsed.kind
    assert decision.unsupported_reason is UnsupportedReason.OUT_OF_CATALOG
    assert seen["headers"]["x-api-key"] == SECRET
    assert transport.calls[0]["usage"] == {"input_tokens": 3, "output_tokens": 4}
    assert SECRET not in repr(transport)
    with pytest.raises(LocalDevModelError, match="anthropic_call_budget_exceeded"):
        transport.messages_create(model=DEFAULT_MODEL_ID, max_tokens=32, messages=[])

    def explode(_payload, _headers):
        raise RuntimeError("failed with " + SECRET)

    leaking = BoundedAnthropicMessagesTransport(api_key=SECRET, send=explode, max_calls=1)
    with pytest.raises(LocalDevModelError, match="anthropic_transport_failed") as caught:
        leaking.messages_create(model=DEFAULT_MODEL_ID, max_tokens=32, messages=[])
    assert SECRET not in str(caught.value)
    assert SECRET not in "".join(caught.value.public_tokens())


def _http_error(status: int, body: bytes) -> error.HTTPError:
    headers = HTTPMessage()
    headers["request-id"] = "req_011CgpmNLMK31HS2mELK64C4"
    return error.HTTPError(
        "https://api.anthropic.com/v1/messages",
        status,
        "error",
        headers,
        BytesIO(body),
    )


def test_anthropic_http_error_keeps_status_type_and_request_id_only():
    prompt = "What is the current operational status of aircraft adb65b?"
    body = json.dumps(
        {
            "type": "error",
            "error": {
                "type": "invalid_request_error",
                "code": "invalid_request",
                "message": "rejected %s %s" % (SECRET, prompt),
            },
        }
    ).encode("utf-8")

    def send(_payload, _headers):
        raise _http_error(422, body)

    transport = BoundedAnthropicMessagesTransport(api_key=SECRET, send=send, max_calls=1)
    with pytest.raises(LocalDevModelError) as caught:
        transport.messages_create(model=DEFAULT_MODEL_ID, max_tokens=32, messages=[])

    failure = caught.value
    assert failure.code == "anthropic_request_rejected"
    assert failure.public_tokens() == (
        "anthropic_request_rejected",
        "http_status:422",
        "error_type:invalid_request_error",
        "error_code:invalid_request",
        "request_id:req_011CgpmNLMK31HS2mELK64C4",
        "failure:http",
    )
    rendered = str(failure) + repr(failure) + "".join(failure.public_tokens())
    assert SECRET not in rendered
    assert prompt not in rendered
    assert "authorization" not in rendered.casefold()


def test_anthropic_timeout_and_connection_are_not_generic_transport_failures():
    def time_out(_payload, _headers):
        raise error.URLError(TimeoutError("deadline"))

    transport = BoundedAnthropicMessagesTransport(api_key=SECRET, send=time_out, max_calls=1)
    with pytest.raises(LocalDevModelError) as timed:
        transport.messages_create(model=DEFAULT_MODEL_ID, max_tokens=32, messages=[])
    assert timed.value.code == "anthropic_timeout"
    assert "failure:timeout" in timed.value.public_tokens()
    assert "deadline" not in str(timed.value)

    def disconnect(_payload, _headers):
        raise error.URLError(ConnectionError("reset"))

    transport = BoundedAnthropicMessagesTransport(api_key=SECRET, send=disconnect, max_calls=1)
    with pytest.raises(LocalDevModelError) as disconnected:
        transport.messages_create(model=DEFAULT_MODEL_ID, max_tokens=32, messages=[])
    assert disconnected.value.code == "anthropic_connection_failed"
    assert "failure:connection" in disconnected.value.public_tokens()


def test_planner_failure_surfaces_sanitized_anthropic_diagnostics():
    class _FailingPlanner:
        def complete(self, _turn):
            raise LocalDevModelError(
                "anthropic_timeout",
                {"failure": "timeout"},
            )

    result = run_local_dev_copilot(
        _payload(),
        planner=_FailingPlanner(),
        specialist_provider=_Specialist([]),
        open_tables=lambda: (_ for _ in ()).throw(AssertionError("aws")),
        now_epoch=NOW,
    )

    assert result["developerPreview"]["routeStatus"] == "PLANNER_FAILED"
    assert result["developerPreview"]["modelErrors"] == [
        "anthropic_timeout",
        "failure:timeout",
    ]
    assert result["developerPreview"]["awsOperations"] == []


def _live_answer_model() -> dict[str, object]:
    return {
        "schema_version": MASTER_ROUTE_PROPOSAL_SCHEMA_VERSION,
        "capabilities": [
            {
                "capability": AgentCapability.RETRIEVE_DETERMINISTIC_OPERATIONAL_CONTEXT.value,
                "requirement": "REQUIRED",
            }
        ],
        "steps": [
            {
                "step_id": "live-1",
                "route_kind": "LIVE_OPS_ANSWER",
                "requirement": "REQUIRED",
                "temporal_intent": "CURRENT",
            }
        ],
    }


class _ModelText:
    def __init__(self, body: dict[str, object]) -> None:
        self.body = body

    def messages_create(self, **kwargs):
        schema = json.dumps(kwargs["output_config"])
        assert "LIVE_OPS_ANSWER" in schema
        assert "LIVE_OPS__ANSWER__" not in schema
        step = kwargs["output_config"]["format"]["schema"]["anyOf"][0]["properties"]["steps"]["items"]
        assert "anyOf" not in step
        return {
            "type": "message",
            "role": "assistant",
            "model": DEFAULT_MODEL_ID,
            "stop_reason": "end_turn",
            "content": [{"type": "text", "text": json.dumps(self.body)}],
        }


def test_anthropic_live_ops_answer_decodes_authorizes_and_dispatches():
    bundle, journal, _aircraft = _tables(_row())
    opened = {"count": 0}

    def open_tables():
        opened["count"] += 1
        return (
            bundle,
            journal,
            {
                "evidenceProvenance": "OPERATOR_AUTHENTICATED_DEV",
                "sourceAuthenticity": "NOT_ESTABLISHED",
            },
        )

    result = run_local_dev_copilot(
        _payload(),
        planner=AnthropicMessagesModelProvider(client=_ModelText(_live_answer_model())),
        specialist_provider=_Specialist(_specialist_decisions()),
        open_tables=open_tables,
        now_epoch=NOW,
    )

    preview = result["developerPreview"]
    assert preview["routeStatus"] == "AUTHORIZED_PLAN"
    assert preview["routeKinds"] == ["LIVE_OPS_ANSWER"]
    assert preview["terminalDecision"] is None
    assert preview["specialists"] == ["LIVE_OPS"]
    assert opened["count"] == 1
    assert journal[0]["operation"] == "GetItem"
    assert journal[0]["table"] == "wilvor-dev-aircraft-current-state"
    assert result["evidence"]
    assert result["claimsAdmitted"] is False
    assert result["productionAiActive"] is False


def test_unsupported_and_refusal_do_not_dispatch_live_ops():
    cases = (
        (
            {
                "schema_version": MODEL_DECISION_SCHEMA_VERSION,
                "kind": "UNSUPPORTED",
                "unsupported_reason": "OUT_OF_CATALOG",
            },
            "PLANNER_UNSUPPORTED",
            "UNSUPPORTED:OUT_OF_CATALOG",
        ),
        (
            {
                "schema_version": MODEL_DECISION_SCHEMA_VERSION,
                "kind": "REFUSAL",
                "refusal_code": "OUT_OF_SCOPE",
            },
            "PLANNER_REFUSED",
            "REFUSAL:OUT_OF_SCOPE",
        ),
    )
    for body, status, reason in cases:
        opened = {"count": 0}

        def open_tables(opened=opened):
            opened["count"] += 1
            raise AssertionError("aws")

        class _Terminal:
            def complete(self, _turn):
                return body

        result = run_local_dev_copilot(
            _payload(),
            planner=_Terminal(),
            specialist_provider=_Specialist([]),
            open_tables=open_tables,
            now_epoch=NOW,
        )
        preview = result["developerPreview"]
        assert opened["count"] == 0
        assert preview["routeStatus"] == status
        assert preview["terminalDecision"] == reason
        assert preview["routeKinds"] == []
        assert preview["awsOperations"] == []
        assert result["evidence"] == []


def test_invalid_route_kind_is_rejected_before_a_read():
    body = _live_answer_model()
    body["steps"][0]["route_kind"] = "NOT_A_ROUTE"
    opened = {"count": 0}

    def open_tables():
        opened["count"] += 1
        raise AssertionError("aws")

    class _Invalid:
        def complete(self, _turn):
            return body

    result = run_local_dev_copilot(
        _payload(),
        planner=_Invalid(),
        specialist_provider=_Specialist([]),
        open_tables=open_tables,
        now_epoch=NOW,
    )

    assert opened["count"] == 0
    assert result["developerPreview"]["routeStatus"] == "INVALID_PROPOSAL"
    assert result["developerPreview"]["routeKinds"] == []
    assert result["evidence"] == []


