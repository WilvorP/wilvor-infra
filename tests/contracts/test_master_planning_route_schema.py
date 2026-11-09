"""Offline Master route schema and host checks. No model or AWS calls."""

from __future__ import annotations

import json

import pytest

from wilvor_ai.contracts import (
    ALLOWED_V1_CAPABILITIES,
    AgentCapability,
    ContractValidationError,
)
from wilvor_ai.master_contracts import MasterPurpose, MasterRequirement, MasterSpecialist
from wilvor_ai.master_routing import (
    EXECUTABLE_ROUTE_KIND_TABLE,
    MASTER_ROUTE_PROPOSAL_SCHEMA_VERSION,
    MasterPlanningContext,
    MasterProposedCapability,
    MasterProposedStep,
    MasterRouteExecutionAuthority,
    MasterRouteHints,
    MasterRouteOutcome,
    MasterRouteProposal,
    MasterRouteTemporalIntent,
    _SYNTHESIS_CAPABILITIES,
    _VISUALIZATION,
    authorize_route,
)
from wilvor_ai.model_contracts import MODEL_DECISION_SCHEMA_VERSION, ModelDecisionKind, ModelTurnRequest
from wilvor_ai.providers.master_instructions import (
    MASTER_PLANNING_INSTRUCTION_REF,
    resolve_master_instruction,
)
from wilvor_ai.specialist_contracts import UnsupportedReason
from wilvor_ai.providers.errors import ModelProviderMalformedDecisionError
from wilvor_ai.sprint4_master_planning import Sprint4PlanningStatus, plan_master_request
from wilvor_ai.sprint5_master_planning import (
    ANTHROPIC_MAX_OPTIONAL_PARAMETERS,
    ANTHROPIC_MAX_UNION_TYPED_PARAMETERS,
    MODEL_PROPOSAL_IS_AUTHORIZATION,
    build_master_planning_messages_kwargs,
    decode_master_planning_json,
    master_planning_json_schema,
    parse_master_planning_text_blocks,
)
from wilvor_ai.local_dev_copilot import run_local_dev_copilot


TEXT = "Retrieve aircraft aaf617 from Wilvor's live AWS database."
LIVE = AgentCapability.RETRIEVE_DETERMINISTIC_OPERATIONAL_CONTEXT
HISTORY = AgentCapability.RETRIEVE_HISTORICAL_ANALYTICS
RISK = AgentCapability.EXPLAIN_EXISTING_RISK
SUMMARIZE = AgentCapability.SUMMARIZE_OPERATIONAL_STATE
_SCHEMA_KEYWORDS = {
    "anyOf",
    "type",
    "properties",
    "required",
    "additionalProperties",
    "enum",
    "const",
    "items",
}
_UNSUPPORTED_SCHEMA_KEYWORDS = {
    "minimum",
    "maximum",
    "exclusiveMinimum",
    "exclusiveMaximum",
    "multipleOf",
    "minLength",
    "maxLength",
    "pattern",
    "minItems",
    "maxItems",
    "uniqueItems",
    "minProperties",
    "maxProperties",
    "format",
    "oneOf",
}
_REJECTED_STEP_OPTIONALS = 11


class _Propose:
    def __init__(self, proposal: MasterRouteProposal) -> None:
        self.proposal = proposal

    def propose(self, context: MasterPlanningContext) -> MasterRouteProposal:
        assert context.canonical_user_text == self.proposal.user_text
        return self.proposal


def _context(**overrides: object) -> MasterPlanningContext:
    values: dict[str, object] = {
        "master_request_id": "master-req-schema-1",
        "correlation_id": "corr-schema-1",
        "application_request_time_utc": "2026-01-15T00:00:01Z",
        "canonical_user_text": TEXT,
    }
    values.update(overrides)
    return MasterPlanningContext(**values)  # type: ignore[arg-type]


def _proposal(*steps: MasterProposedStep, capabilities: tuple[AgentCapability, ...]) -> MasterRouteProposal:
    return MasterRouteProposal(
        user_text=TEXT,
        capabilities=tuple(
            MasterProposedCapability(item, MasterRequirement.REQUIRED) for item in capabilities
        ),
        steps=steps,
    )


def _live() -> MasterProposedStep:
    return MasterProposedStep(
        step_id="live-1",
        specialist=MasterSpecialist.LIVE_OPS,
        purpose=MasterPurpose.ANSWER,
        requirement=MasterRequirement.REQUIRED,
        capability=LIVE,
        temporal_intent=MasterRouteTemporalIntent.CURRENT,
    )


def _history() -> MasterProposedStep:
    return MasterProposedStep(
        step_id="hist-1",
        specialist=MasterSpecialist.HISTORICAL_ANALYTICS,
        purpose=MasterPurpose.ANSWER,
        requirement=MasterRequirement.REQUIRED,
        capability=HISTORY,
        temporal_intent=MasterRouteTemporalIntent.HISTORICAL,
        historical_start_utc="2026-01-01T00:00:00Z",
        historical_end_utc="2026-01-02T00:00:00Z",
    )


def _decision() -> MasterProposedStep:
    return MasterProposedStep(
        step_id="dec-1",
        specialist=MasterSpecialist.DECISION,
        purpose=MasterPurpose.ANSWER,
        requirement=MasterRequirement.REQUIRED,
        capability=RISK,
        temporal_intent=MasterRouteTemporalIntent.CURRENT,
    )


def _type_matches(kind: str, instance: object) -> bool:
    if kind == "string":
        return isinstance(instance, str)
    if kind == "boolean":
        return isinstance(instance, bool)
    if kind == "integer":
        return isinstance(instance, int) and not isinstance(instance, bool)
    if kind == "object":
        return isinstance(instance, dict)
    if kind == "array":
        return isinstance(instance, list)
    raise AssertionError(kind)


def _schema_accepts(schema: dict[str, object], instance: object) -> bool:
    """Accept only the closed JSON Schema subset the Messages grammar uses."""

    if not isinstance(schema, dict):
        raise AssertionError("schema node is not an object")
    unknown = set(schema) - _SCHEMA_KEYWORDS
    if unknown:
        raise AssertionError(sorted(unknown))
    if "anyOf" in schema:
        extras = set(schema) - {"anyOf"}
        if extras:
            raise AssertionError(sorted(extras))
        branches = schema["anyOf"]
        if not isinstance(branches, list) or not branches:
            raise AssertionError("empty anyOf")
        return any(_schema_accepts(branch, instance) for branch in branches)
    if "const" in schema:
        if instance != schema["const"]:
            return False
        if "type" in schema and not _type_matches(str(schema["type"]), instance):
            return False
        return True
    if "enum" in schema:
        allowed = schema["enum"]
        if not isinstance(allowed, list) or instance not in allowed:
            return False
        if "type" in schema and not _type_matches(str(schema["type"]), instance):
            return False
        return True
    kind = schema.get("type")
    if kind == "object":
        if schema.get("additionalProperties") is not False:
            raise AssertionError("object schema is not closed")
        if not isinstance(instance, dict):
            return False
        properties = schema.get("properties", {})
        required = schema.get("required", [])
        if not isinstance(properties, dict) or not isinstance(required, list):
            raise AssertionError("invalid object schema")
        if any(key not in instance for key in required):
            return False
        if any(key not in properties for key in instance):
            return False
        return all(_schema_accepts(properties[key], value) for key, value in instance.items())
    if kind == "array":
        if "items" not in schema or not isinstance(instance, list):
            return False
        return all(_schema_accepts(schema["items"], item) for item in instance)
    if kind in {"string", "boolean", "integer"}:
        return _type_matches(str(kind), instance)
    raise AssertionError(sorted(schema))


def _messages_schema() -> dict[str, object]:
    kwargs = build_master_planning_messages_kwargs(
        ModelTurnRequest(user_text=TEXT, instruction_ref=MASTER_PLANNING_INSTRUCTION_REF),
        "claude-sonnet-4-6",
    )
    output = kwargs["output_config"]["format"]
    assert output["type"] == "json_schema"
    assert "tools" not in kwargs
    schema = output["schema"]
    assert schema == master_planning_json_schema()
    assert "aaf617" not in json.dumps(schema)
    return schema


def _proposal_branch(schema: dict[str, object]) -> dict[str, object]:
    for branch in schema["anyOf"]:
        properties = branch.get("properties", {})
        if "steps" in properties and "capabilities" in properties:
            return branch
    raise AssertionError("proposal branch missing")


def _step_schema(schema: dict[str, object]) -> dict[str, object]:
    items = _proposal_branch(schema)["properties"]["steps"]["items"]
    assert "anyOf" not in items
    assert items["type"] == "object"
    assert items["additionalProperties"] is False
    return items


def _expected_triples() -> set[tuple[str, str, str]]:
    return {
        (specialist.value, purpose.value, capability.value)
        for specialist, purpose, capability in EXECUTABLE_ROUTE_KIND_TABLE.values()
    }


def _walk_schema(node: object) -> list[object]:
    values = [node]
    if isinstance(node, dict):
        for item in node.values():
            values.extend(_walk_schema(item))
    elif isinstance(node, list):
        for item in node:
            values.extend(_walk_schema(item))
    return values


def _count_optional_parameters(schema: object) -> int:
    count = 0
    if not isinstance(schema, dict):
        return 0
    if schema.get("type") == "object":
        properties = schema.get("properties") or {}
        required = set(schema.get("required") or [])
        if isinstance(properties, dict):
            count += sum(1 for name in properties if name not in required)
            for child in properties.values():
                count += _count_optional_parameters(child)
    items = schema.get("items")
    if isinstance(items, dict):
        count += _count_optional_parameters(items)
    for branch in schema.get("anyOf") or ():
        count += _count_optional_parameters(branch)
    return count


def _count_union_typed_parameters(schema: object) -> int:
    count = 0
    if not isinstance(schema, dict):
        return 0
    properties = schema.get("properties")
    if isinstance(properties, dict):
        for child in properties.values():
            if not isinstance(child, dict):
                continue
            if "anyOf" in child or isinstance(child.get("type"), list):
                count += 1
            items = child.get("items")
            if isinstance(items, dict) and "anyOf" in items:
                count += 1
            count += _count_union_typed_parameters(child)
    items = schema.get("items")
    if isinstance(items, dict):
        count += _count_union_typed_parameters(items)
    for branch in schema.get("anyOf") or ():
        count += _count_union_typed_parameters(branch)
    return count


def _route_object(step: dict[str, object], capabilities: list[dict[str, str]]) -> dict[str, object]:
    return {
        "schema_version": MASTER_ROUTE_PROPOSAL_SCHEMA_VERSION,
        "capabilities": capabilities,
        "steps": [step],
    }


def _capability(name: str) -> dict[str, str]:
    return {"capability": name, "requirement": "REQUIRED"}


def _route_token(specialist: str, purpose: str, capability: str) -> str:
    for token, (resolved_specialist, resolved_purpose, resolved_capability) in EXECUTABLE_ROUTE_KIND_TABLE.items():
        if (
            resolved_specialist.value,
            resolved_purpose.value,
            resolved_capability.value,
        ) == (specialist, purpose, capability):
            return token
    raise AssertionError((specialist, purpose, capability))


def _step_object(route_kind: str, **extra: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "step_id": "step-1",
        "route_kind": route_kind,
        "requirement": "REQUIRED",
        "temporal_intent": "CURRENT",
    }
    payload.update(extra)
    return payload


def _model_payload(step: dict[str, object], capabilities: list[dict[str, str]]) -> dict[str, object]:
    return _route_object(step, capabilities)


def test_messages_schema_admits_only_closed_route_kinds():
    schema = _messages_schema()
    step = _step_schema(schema)
    properties = step["properties"]
    assert "specialist" not in properties
    assert "purpose" not in properties
    assert "capability" not in properties
    route_kind = properties["route_kind"]
    assert set(route_kind) == {"type", "enum"}
    admitted = set(route_kind["enum"])
    expected = set(EXECUTABLE_ROUTE_KIND_TABLE)
    assert admitted == expected
    assert len(admitted) == 5
    assert admitted == {
        "LIVE_OPS_DISCOVERY",
        "LIVE_OPS_ANSWER",
        "HISTORICAL_ANSWER",
        "DECISION_RISK",
        "DECISION_RECOMMENDATION",
    }
    assert EXECUTABLE_ROUTE_KIND_TABLE["LIVE_OPS_ANSWER"][0] is MasterSpecialist.LIVE_OPS
    assert EXECUTABLE_ROUTE_KIND_TABLE["LIVE_OPS_ANSWER"][1].value == "ANSWER"
    assert EXECUTABLE_ROUTE_KIND_TABLE["LIVE_OPS_ANSWER"][2] is LIVE
    triples = set(EXECUTABLE_ROUTE_KIND_TABLE.values())
    assert {(item[0].value, item[1].value, item[2].value) for item in triples} == _expected_triples()
    specialists = {item[0] for item in triples}
    purposes = {item[1] for item in triples}
    capabilities = {item[2] for item in triples}
    assert len(specialists) * len(purposes) * len(capabilities) > len(admitted)
    rejected_tokens = {
        "LIVE_OPS__ANSWER__SUMMARIZE_OPERATIONAL_STATE",
        "LIVE_OPS__ANSWER__EXPLAIN_EXISTING_RISK",
        "LIVE_OPS__ANSWER__RETRIEVE_HISTORICAL_ANALYTICS",
        "LIVE_OPS__DISCOVERY__RETRIEVE_HISTORICAL_ANALYTICS",
        "HISTORICAL_ANALYTICS__ANSWER__RETRIEVE_DETERMINISTIC_OPERATIONAL_CONTEXT",
        "DECISION__ANSWER__RETRIEVE_DETERMINISTIC_OPERATIONAL_CONTEXT",
        "DECISION__DISCOVERY__EXPLAIN_EXISTING_RISK",
        "HISTORICAL_ANALYTICS__DISCOVERY__RETRIEVE_HISTORICAL_ANALYTICS",
    }
    assert rejected_tokens.isdisjoint(admitted)
    synthesis = {item.value for item in _SYNTHESIS_CAPABILITIES}
    routed_capabilities = {item[2].value for item in triples}
    assert synthesis.isdisjoint(routed_capabilities)
    assert _VISUALIZATION.value not in routed_capabilities

    proposal = _proposal_branch(schema)
    capability_schema = proposal["properties"]["capabilities"]["items"]["properties"]["capability"]
    assert "anyOf" not in capability_schema
    names = set(capability_schema["enum"])
    assert synthesis <= names
    assert _VISUALIZATION.value in names
    assert {item.value for item in capabilities} <= names
    assert names == {item.value for item in ALLOWED_V1_CAPABILITIES}
    assert synthesis.isdisjoint(routed_capabilities)


def test_messages_schema_stays_inside_anthropic_structured_output_limits():
    schema = _messages_schema()
    optional = _count_optional_parameters(schema)
    unions = _count_union_typed_parameters(schema)
    duplicated = len(EXECUTABLE_ROUTE_KIND_TABLE) * _REJECTED_STEP_OPTIONALS + 1
    assert duplicated > ANTHROPIC_MAX_OPTIONAL_PARAMETERS
    assert optional <= ANTHROPIC_MAX_OPTIONAL_PARAMETERS
    assert unions <= ANTHROPIC_MAX_UNION_TYPED_PARAMETERS
    assert optional == 12
    assert unions == 0
    assert ANTHROPIC_MAX_OPTIONAL_PARAMETERS == 24
    assert ANTHROPIC_MAX_UNION_TYPED_PARAMETERS == 16
    for node in _walk_schema(schema):
        if isinstance(node, dict):
            assert _UNSUPPORTED_SCHEMA_KEYWORDS.isdisjoint(node)
            if node.get("type") == "object":
                assert node.get("additionalProperties") is False


def test_messages_schema_rejects_invalid_step_and_accepts_legal_routes():
    schema = _messages_schema()
    live = _route_object(
        _step_object(_route_token("LIVE_OPS", "ANSWER", LIVE.value)),
        [_capability(LIVE.value)],
    )
    historical = _route_object(
        _step_object(
            _route_token("HISTORICAL_ANALYTICS", "ANSWER", HISTORY.value),
            temporal_intent="HISTORICAL",
            historical_start_utc="2026-01-01T00:00:00Z",
            historical_end_utc="2026-01-02T00:00:00Z",
        ),
        [_capability(HISTORY.value)],
    )
    decision = _route_object(
        _step_object(_route_token("DECISION", "ANSWER", RISK.value)),
        [_capability(RISK.value)],
    )
    synthesis_beside_live = _route_object(
        _step_object(_route_token("LIVE_OPS", "ANSWER", LIVE.value)),
        [_capability(LIVE.value), _capability(SUMMARIZE.value)],
    )
    assert _schema_accepts(schema, live)
    assert _schema_accepts(schema, historical)
    assert _schema_accepts(schema, decision)
    assert _schema_accepts(schema, synthesis_beside_live)
    assert _schema_accepts(
        schema,
        {
            "schema_version": MODEL_DECISION_SCHEMA_VERSION,
            "kind": ModelDecisionKind.UNSUPPORTED.value,
            "unsupported_reason": UnsupportedReason.OUT_OF_CATALOG.value,
        },
    )
    assert not _schema_accepts(
        schema,
        _route_object(
            _step_object("LIVE_OPS__ANSWER__SUMMARIZE_OPERATIONAL_STATE"),
            [_capability(SUMMARIZE.value)],
        ),
    )
    assert not _schema_accepts(
        schema,
        _route_object(
            _step_object("DECISION__ANSWER__RETRIEVE_DETERMINISTIC_OPERATIONAL_CONTEXT"),
            [_capability(LIVE.value)],
        ),
    )
    assert not _schema_accepts(
        schema,
        _route_object(
            _step_object("HISTORICAL_ANALYTICS__ANSWER__RETRIEVE_DETERMINISTIC_OPERATIONAL_CONTEXT"),
            [_capability(LIVE.value)],
        ),
    )
    assert not _schema_accepts(
        schema,
        _route_object(
            _step_object("LIVE_OPS__DISCOVERY__RETRIEVE_HISTORICAL_ANALYTICS"),
            [_capability(HISTORY.value)],
        ),
    )
    free_form = _route_object(
        {
            "step_id": "step-1",
            "specialist": "LIVE_OPS",
            "purpose": "ANSWER",
            "requirement": "REQUIRED",
            "capability": LIVE.value,
        },
        [_capability(LIVE.value)],
    )
    assert not _schema_accepts(schema, free_form)


def test_planning_instruction_states_exact_pair_and_synthesis_split():
    text = resolve_master_instruction(MASTER_PLANNING_INSTRUCTION_REF)
    for specialist, purpose, capability in sorted(_expected_triples()):
        assert "specialist %s, purpose %s, capability %s" % (specialist, purpose, capability) in text
    for capability in _SYNTHESIS_CAPABILITIES:
        assert capability.value in text
    assert "Synthesis capabilities are not executable specialist steps" in text
    assert "Do not put them" in text
    assert "in route_kind" in text
    assert "Do not emit specialist, purpose, or capability on a step" in text
    for token in EXECUTABLE_ROUTE_KIND_TABLE:
        assert token in text
    assert _VISUALIZATION.value in text
    assert "is not an executable specialist step" in text
    assert "Do not call AWS APIs" in text
    assert "Do not authorize tools" in text
    assert "route_kind LIVE_OPS_ANSWER" in text
    assert "Do not return UNSUPPORTED or REFUSAL for that question" in text
    assert "A route proposal is not an" in text
    system = build_master_planning_messages_kwargs(
        ModelTurnRequest(user_text=TEXT, instruction_ref=MASTER_PLANNING_INSTRUCTION_REF),
        "claude-sonnet-4-6",
    )["system"]
    assert system == text


def _authorized_model_step(token: str, specialist: MasterSpecialist) -> dict[str, object]:
    if specialist is MasterSpecialist.HISTORICAL_ANALYTICS:
        return _step_object(
            token,
            temporal_intent="HISTORICAL",
            historical_start_utc="2026-01-01T00:00:00Z",
            historical_end_utc="2026-01-02T00:00:00Z",
        )
    return _step_object(token)


def test_every_route_kind_decodes_to_its_triple_and_host_authorizes_it():
    schema = _messages_schema()
    assert MODEL_PROPOSAL_IS_AUTHORIZATION is False
    for token, (specialist, purpose, capability) in EXECUTABLE_ROUTE_KIND_TABLE.items():
        payload = _model_payload(
            _authorized_model_step(token, specialist),
            [_capability(capability.value)],
        )
        assert _schema_accepts(schema, payload)
        decoded = decode_master_planning_json(payload)
        step = decoded["steps"][0]
        assert step["specialist"] == specialist.value
        assert step["purpose"] == purpose.value
        assert step["capability"] == capability.value
        assert "route_kind" not in step
        assert "execution_authority" not in decoded
        proposal = MasterRouteProposal.from_dict({**decoded, "user_text": TEXT})
        decision = authorize_route(proposal, _context())
        assert decision.outcome is MasterRouteOutcome.AUTHORIZED_PLAN
        assert decision.execution_authority is MasterRouteExecutionAuthority.DENIED
        assert decision.plan is not None
        planned = decision.plan.steps[0]
        assert planned.invocation.specialist is specialist
        assert planned.invocation.purpose is purpose
        assert planned.capability is capability


def test_invalid_route_combinations_fail_closed_before_authorization():
    schema = _messages_schema()
    invalid_tokens = (
        "LIVE_OPS__ANSWER__SUMMARIZE_OPERATIONAL_STATE",
        "LIVE_OPS__ANSWER__EXPLAIN_EXISTING_RISK",
        "DECISION__ANSWER__RETRIEVE_DETERMINISTIC_OPERATIONAL_CONTEXT",
        "HISTORICAL_ANALYTICS__DISCOVERY__RETRIEVE_HISTORICAL_ANALYTICS",
        "NOT_A_ROUTE",
    )
    for token in invalid_tokens:
        payload = _model_payload(_step_object(token), [_capability(LIVE.value)])
        assert not _schema_accepts(schema, payload)
        with pytest.raises(ContractValidationError) as rejected:
            decode_master_planning_json(payload)
        assert rejected.value.errors == ("invalid_route_kind",)

    mixed = _model_payload(
        {
            "step_id": "step-1",
            "route_kind": _route_token("LIVE_OPS", "ANSWER", LIVE.value),
            "specialist": "DECISION",
            "purpose": "ANSWER",
            "capability": RISK.value,
            "requirement": "REQUIRED",
        },
        [_capability(LIVE.value)],
    )
    assert not _schema_accepts(schema, mixed)
    with pytest.raises(ContractValidationError) as conflict:
        decode_master_planning_json(mixed)
    assert conflict.value.errors == ("invalid_route_kind_conflict",)


def test_deferred_synthesis_is_not_a_route_kind():
    schema = _messages_schema()
    token = _route_token("LIVE_OPS", "ANSWER", LIVE.value)
    payload = _model_payload(
        _step_object(token),
        [_capability(LIVE.value), _capability(SUMMARIZE.value)],
    )
    assert _schema_accepts(schema, payload)
    assert not _schema_accepts(
        schema,
        _model_payload(
            _step_object("LIVE_OPS__ANSWER__SUMMARIZE_OPERATIONAL_STATE"),
            [_capability(LIVE.value), _capability(SUMMARIZE.value)],
        ),
    )
    decoded = decode_master_planning_json(payload)
    proposal = MasterRouteProposal.from_dict({**decoded, "user_text": TEXT})
    decision = authorize_route(proposal, _context())
    assert decision.outcome is MasterRouteOutcome.AUTHORIZED_PLAN
    assert decision.execution_authority is MasterRouteExecutionAuthority.DENIED
    assert [item.capability for item in decision.plan.steps] == [LIVE]
    deferred = decision.plan.deferred_synthesis
    assert len(deferred) == 1
    assert deferred[0].capability is SUMMARIZE
    assert deferred[0].executable is False
    assert deferred[0].state.value == "NON_EXECUTABLE"


def test_host_validation_stays_authoritative_after_route_kind_decode():
    token = _route_token("HISTORICAL_ANALYTICS", "ANSWER", HISTORY.value)
    decoded = decode_master_planning_json(
        _model_payload(
            _step_object(token, temporal_intent="CURRENT"),
            [_capability(HISTORY.value)],
        )
    )
    assert decoded["steps"][0]["capability"] == HISTORY.value
    proposal = MasterRouteProposal.from_dict({**decoded, "user_text": TEXT})
    decision = authorize_route(proposal, _context())
    assert decision.outcome is MasterRouteOutcome.UNSUPPORTED
    assert "historical_cannot_answer_current_state" in decision.reasons
    assert decision.execution_authority is MasterRouteExecutionAuthority.DENIED
    assert decision.plan is None


def test_malformed_model_output_fails_closed():
    with pytest.raises(ModelProviderMalformedDecisionError, match="malformed_terminal_json"):
        parse_master_planning_text_blocks([{"text": "{"}])
    free_form = {
        "schema_version": MASTER_ROUTE_PROPOSAL_SCHEMA_VERSION,
        "capabilities": [_capability(SUMMARIZE.value)],
        "steps": [
            {
                "step_id": "step-1",
                "specialist": "LIVE_OPS",
                "purpose": "ANSWER",
                "requirement": "REQUIRED",
                "capability": SUMMARIZE.value,
            }
        ],
    }
    with pytest.raises(ContractValidationError) as decoded:
        decode_master_planning_json(free_form)
    assert decoded.value.errors == ("invalid_route_kind",)
    with pytest.raises(ModelProviderMalformedDecisionError, match="invalid_master_route_proposal"):
        parse_master_planning_text_blocks([{"text": json.dumps(free_form)}])

    class _UnknownRoute:
        def propose(self, context: MasterPlanningContext) -> dict[str, object]:
            assert context.canonical_user_text == TEXT
            return _model_payload(_step_object("NOT_A_ROUTE"), [_capability(LIVE.value)])

    result = plan_master_request(_context(), _UnknownRoute())
    assert result.status is Sprint4PlanningStatus.INVALID_PROPOSAL
    assert result.proposal is None
    assert result.decision is None


def test_valid_live_ops_answer_route_is_accepted():
    decision = authorize_route(_proposal(_live(), capabilities=(LIVE,)), _context())
    assert decision.outcome is MasterRouteOutcome.AUTHORIZED_PLAN
    assert decision.execution_authority is MasterRouteExecutionAuthority.DENIED
    assert decision.plan is not None
    assert [(item.invocation.specialist, item.capability) for item in decision.plan.steps] == [
        (MasterSpecialist.LIVE_OPS, LIVE)
    ]
    assert decision.plan.deferred_synthesis == ()


def test_valid_historical_route_is_accepted():
    decision = authorize_route(_proposal(_history(), capabilities=(HISTORY,)), _context())
    assert decision.outcome is MasterRouteOutcome.AUTHORIZED_PLAN
    assert decision.execution_authority is MasterRouteExecutionAuthority.DENIED
    step = decision.plan.steps[0]
    assert step.invocation.specialist is MasterSpecialist.HISTORICAL_ANALYTICS
    assert step.capability is HISTORY
    assert step.historical_start_utc == "2026-01-01T00:00:00Z"


def test_valid_decision_route_is_accepted():
    decision = authorize_route(_proposal(_decision(), capabilities=(RISK,)), _context())
    assert decision.outcome is MasterRouteOutcome.AUTHORIZED_PLAN
    assert decision.execution_authority is MasterRouteExecutionAuthority.DENIED
    step = decision.plan.steps[0]
    assert step.invocation.specialist is MasterSpecialist.DECISION
    assert step.invocation.purpose is MasterPurpose.ANSWER
    assert step.capability is RISK


def test_invalid_capability_step_pair_is_rejected_by_the_host():
    proposal = MasterRouteProposal(
        user_text=TEXT,
        capabilities=(MasterProposedCapability(SUMMARIZE, MasterRequirement.REQUIRED),),
        steps=(
            MasterProposedStep(
                step_id="step-aaf617",
                specialist=MasterSpecialist.LIVE_OPS,
                purpose=MasterPurpose.ANSWER,
                requirement=MasterRequirement.REQUIRED,
                capability=SUMMARIZE,
                temporal_intent=MasterRouteTemporalIntent.CURRENT,
                hints=MasterRouteHints(typed_aircraft_id="aaf617"),
            ),
        ),
    )
    with pytest.raises(ContractValidationError) as rejected:
        authorize_route(proposal, _context())
    assert rejected.value.errors == ("capability_step_mismatch",)
    assert "aaf617" not in str(rejected.value)

    with pytest.raises(ContractValidationError) as planned:
        plan_master_request(_context(), _Propose(proposal))
    assert planned.value.errors == (
        "capability_step_mismatch",
        "specialist:LIVE_OPS",
        "purpose:ANSWER",
        "capability:SUMMARIZE_OPERATIONAL_STATE",
    )
    assert "aaf617" not in str(planned.value)
    assert TEXT not in str(planned.value)


def test_synthesis_capability_is_not_an_executable_specialist_step():
    decision = authorize_route(
        _proposal(_live(), capabilities=(LIVE, SUMMARIZE)),
        _context(),
    )
    assert decision.outcome is MasterRouteOutcome.AUTHORIZED_PLAN
    assert [item.capability for item in decision.plan.steps] == [LIVE]
    assert [item.invocation.specialist for item in decision.plan.steps] == [MasterSpecialist.LIVE_OPS]
    deferred = decision.plan.deferred_synthesis
    assert len(deferred) == 1
    assert deferred[0].capability is SUMMARIZE
    assert deferred[0].executable is False
    assert deferred[0].state.value == "NON_EXECUTABLE"


def test_local_preview_reports_routing_tokens_without_aircraft_data():
    proposal = MasterRouteProposal(
        user_text=TEXT,
        capabilities=(MasterProposedCapability(SUMMARIZE, MasterRequirement.REQUIRED),),
        steps=(
            MasterProposedStep(
                step_id="step-aaf617",
                specialist=MasterSpecialist.LIVE_OPS,
                purpose=MasterPurpose.ANSWER,
                requirement=MasterRequirement.REQUIRED,
                capability=SUMMARIZE,
                temporal_intent=MasterRouteTemporalIntent.CURRENT,
                hints=MasterRouteHints(typed_aircraft_id="aaf617"),
            ),
        ),
    )

    def open_tables():
        raise AssertionError("aws")

    result = run_local_dev_copilot(
        {
            "schemaVersion": "wilvor.ai.copilot.session_query.v1",
            "liveOpsSchemaVersion": "wilvor.ai.live_ops_public.v1",
            "masterRequestId": "mreq-schema0001",
            "planningSessionId": "plan-schema0001",
            "conversationId": "conv-schema0001",
            "correlationId": "corr-schema0001",
            "applicationRequestTimeUtc": "2026-11-07T21:00:00Z",
            "userText": TEXT,
            "priorTurns": [],
        },
        planner=_Propose(proposal),
        specialist_provider=object(),
        open_tables=open_tables,
        now_epoch=1_700_000_100,
    )
    assert result["developerPreview"]["routeStatus"] == "PLANNER_REJECTED"
    assert result["developerPreview"]["modelErrors"] == [
        "capability_step_mismatch",
        "specialist:LIVE_OPS",
        "purpose:ANSWER",
        "capability:SUMMARIZE_OPERATIONAL_STATE",
    ]
    rendered = json.dumps(result)
    assert "aaf617" not in rendered
    assert TEXT not in rendered
    assert "sk-" not in rendered
