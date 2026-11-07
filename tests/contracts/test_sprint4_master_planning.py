"""Sprint 4 Agent D: request understanding and MA2 planning. Offline only."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from wilvor_ai.contracts import AgentCapability, ContractValidationError
from wilvor_ai.master_contracts import MasterPurpose, MasterRequirement, MasterSpecialist
from wilvor_ai.master_evidence_admission import MasterSourceAuthenticity
from wilvor_ai.master_routing import (
    REQUEST_UNDERSTANDING_LIMITATION,
    MasterHintTrust,
    MasterPlanningContext,
    MasterProposedCapability,
    MasterProposedStep,
    MasterRequestCoverage,
    MasterRouteExecutionAuthority,
    MasterRouteHints,
    MasterRouteOutcome,
    MasterRouteProposal,
    MasterRouteTemporalIntent,
)
from wilvor_ai.model_contracts import ModelDecision, ModelDecisionKind, ProposedToolCall
from wilvor_ai.specialist_contracts import ExactCountClaim, UnsupportedReason
from wilvor_ai.sprint4_master_planning import (
    HINT_TRUST,
    MODEL_PROPOSAL_IS_AUTHORIZATION,
    SPRINT4_PLANNING_EXECUTION_AUTHORITY_AVAILABLE,
    Sprint4PlanningStatus,
    collect_untrusted_hints,
    plan_master_request,
)


TEXT = "Which aircraft are currently affected by hazards, and what recorded risks apply?"
LIVE = AgentCapability.RETRIEVE_DETERMINISTIC_OPERATIONAL_CONTEXT
MODULE = (
    Path(__file__).resolve().parents[2]
    / "functions"
    / "shared"
    / "wilvor_ai"
    / "sprint4_master_planning.py"
)


class ScriptedPlanner:
    def __init__(self, payload):
        self.payload = payload
        self.contexts = []

    def propose(self, context):
        self.contexts.append(context)
        return self.payload


class CompleteOnlyPlanner:
    def __init__(self, decision):
        self.decision = decision

    def complete(self, turn):
        return self.decision


def _context(**overrides: object) -> MasterPlanningContext:
    values: dict[str, object] = {
        "master_request_id": "master-req-sprint4-1",
        "correlation_id": "corr-sprint4-1",
        "application_request_time_utc": "2026-01-15T00:00:01Z",
        "canonical_user_text": TEXT,
    }
    values.update(overrides)
    return MasterPlanningContext(**values)  # type: ignore[arg-type]


def _proposal(*steps: MasterProposedStep, capabilities=None) -> MasterRouteProposal:
    if capabilities is None:
        capabilities = (MasterProposedCapability(LIVE, MasterRequirement.REQUIRED),)
    return MasterRouteProposal(user_text=TEXT, capabilities=capabilities, steps=steps)


def _live_step(**overrides: object) -> MasterProposedStep:
    values: dict[str, object] = {
        "step_id": "live-1",
        "specialist": MasterSpecialist.LIVE_OPS,
        "purpose": MasterPurpose.ANSWER,
        "requirement": MasterRequirement.REQUIRED,
        "capability": LIVE,
        "temporal_intent": MasterRouteTemporalIntent.CURRENT,
    }
    values.update(overrides)
    return MasterProposedStep(**values)  # type: ignore[arg-type]


def test_closed_planning_flags_remain_denied():
    assert SPRINT4_PLANNING_EXECUTION_AUTHORITY_AVAILABLE is False
    assert MODEL_PROPOSAL_IS_AUTHORIZATION is False
    assert HINT_TRUST is MasterHintTrust.UNTRUSTED


def test_injected_proposal_is_authorized_by_ma2_without_execution():
    proposal = _proposal(_live_step())
    result = plan_master_request(_context(), ScriptedPlanner(proposal))
    assert result.status is Sprint4PlanningStatus.AUTHORIZED_PLAN
    assert result.decision is not None
    assert result.decision.outcome is MasterRouteOutcome.AUTHORIZED_PLAN
    assert result.decision.plan is not None
    assert result.execution_authority is MasterRouteExecutionAuthority.DENIED
    assert result.request_coverage is MasterRequestCoverage.NOT_ESTABLISHED
    assert result.hint_trust is MasterHintTrust.UNTRUSTED
    assert result.source_authenticity is MasterSourceAuthenticity.NOT_ESTABLISHED
    assert result.understanding_limitation == REQUEST_UNDERSTANDING_LIMITATION
    assert result.decision.plan.execution_authority is MasterRouteExecutionAuthority.DENIED


def test_model_hints_stay_untrusted_and_are_not_authorization():
    hints = MasterRouteHints(callsign="UAL123", region_text="California")
    proposal = _proposal(_live_step(hints=hints))
    result = plan_master_request(_context(), ScriptedPlanner(proposal.to_dict()))
    copied = collect_untrusted_hints(result.proposal)
    assert copied == (hints,)
    assert result.hint_trust is MasterHintTrust.UNTRUSTED
    assert result.decision is not None
    assert result.decision.plan is not None
    step = result.decision.plan.steps[0]
    assert step.untrusted_hints.callsign == "UAL123"
    assert step.untrusted_hints.region_text == "California"


def test_mapping_proposal_is_accepted_and_text_mismatch_fails():
    proposal = _proposal(_live_step())
    payload = proposal.to_dict()
    result = plan_master_request(_context(), ScriptedPlanner(payload))
    assert result.status is Sprint4PlanningStatus.AUTHORIZED_PLAN
    payload["user_text"] = "Different question"
    with pytest.raises(ContractValidationError, match="user_text_mismatch"):
        plan_master_request(_context(), ScriptedPlanner(payload))


def test_complete_only_planner_cannot_authorize_aws_or_mint_claims():
    aws = ModelDecision(
        kind=ModelDecisionKind.TOOL_CALLS,
        tool_calls=(ProposedToolCall("GetItem", {"TableName": "aircraft"}),),
    )
    with pytest.raises(ContractValidationError, match="model_aws_authorization_rejected"):
        plan_master_request(_context(), CompleteOnlyPlanner(aws))
    claims = ModelDecision(
        kind=ModelDecisionKind.FINAL_CLAIMS,
        claims=(ExactCountClaim(tool_call_id="tool-1", metric_id="count", value=1),),
    )
    with pytest.raises(ContractValidationError, match="master_planner_claims_rejected"):
        plan_master_request(_context(), CompleteOnlyPlanner(claims))
    unsupported = ModelDecision(
        kind=ModelDecisionKind.UNSUPPORTED,
        unsupported_reason=UnsupportedReason.OUT_OF_CATALOG,
    )
    result = plan_master_request(_context(), CompleteOnlyPlanner(unsupported))
    assert result.status is Sprint4PlanningStatus.PLANNER_UNSUPPORTED
    assert result.decision is None


def test_prohibited_capability_is_refused_by_existing_ma2():
    altitude = AgentCapability.ISSUE_ALTITUDE_INSTRUCTIONS
    proposal = MasterRouteProposal(
        user_text=TEXT,
        capabilities=(MasterProposedCapability(altitude, MasterRequirement.REQUIRED),),
        steps=(
            MasterProposedStep(
                step_id="bad-1",
                specialist=MasterSpecialist.LIVE_OPS,
                purpose=MasterPurpose.ANSWER,
                requirement=MasterRequirement.REQUIRED,
                capability=altitude,
                temporal_intent=MasterRouteTemporalIntent.CURRENT,
            ),
        ),
    )
    result = plan_master_request(_context(), ScriptedPlanner(proposal))
    assert result.status is Sprint4PlanningStatus.REFUSED
    assert result.decision is not None
    assert result.decision.outcome is MasterRouteOutcome.REFUSED
    assert result.decision.plan is None


def test_planning_module_does_not_import_langgraph_or_sdks():
    tree = ast.parse(MODULE.read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    assert "langgraph" not in imported
    assert imported.isdisjoint({"boto3", "botocore", "anthropic", "openai"})
    source = MODULE.read_text(encoding="utf-8")
    assert "langgraph" not in source
    assert "invoke_live_ops" not in source
