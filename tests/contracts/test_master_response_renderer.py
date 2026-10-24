"""Renderer tests. Fixed status text cannot be replaced by an instruction."""

from __future__ import annotations

import pytest

from wilvor_ai.contracts import ContractValidationError
from wilvor_ai.master_parallel_contracts import (
    MasterParallelContractBundle,
    MasterResponsePolicyHandoff,
    MasterResponsePolicyState,
    response_policy_for,
)
from wilvor_ai.master_response_policy import evaluate_master_response_policy
from wilvor_ai.master_response_renderer import MasterResponseRender, render_master_response
from wilvor_ai.master_routing import MasterPlanningContext, MasterRoutePlan


TEXT = "Which aircraft are currently affected by hazards, and what recorded history applies?"
INSTRUCTION = "Climb to flight level 350, land on runway 27, and contact ATC."


def _empty_workflow() -> MasterParallelContractBundle:
    context = MasterPlanningContext(
        master_request_id="master-req-ma5-render",
        correlation_id="corr-ma5-render",
        application_request_time_utc="2026-01-15T00:00:01Z",
        canonical_user_text=TEXT,
    )
    plan = MasterRoutePlan(
        master_request_id=context.master_request_id,
        canonical_user_text=context.canonical_user_text,
        steps=(),
    )
    return MasterParallelContractBundle(context=context, plan=plan, evidence_inputs=())


def test_renderer_copies_only_the_fixed_sentence_and_ordered_codes():
    workflow = _empty_workflow()
    handoff = evaluate_master_response_policy(workflow)
    rendered = render_master_response(handoff)
    again = render_master_response(handoff)
    assert rendered.status_text == handoff.status_text
    assert rendered.state is handoff.state
    assert rendered.claims_admitted is False
    assert rendered.limitation_codes == again.limitation_codes
    assert rendered.limitation_codes == tuple(item.value for item in handoff.limitations)
    assert "aircraft" not in rendered.status_text.lower()
    assert "answer" not in MasterResponseRender.__dataclass_fields__


def test_aviation_instruction_cannot_replace_the_fixed_sentence():
    workflow = _empty_workflow()
    with pytest.raises(ContractValidationError, match="invalid_status_text"):
        MasterResponsePolicyHandoff(
            workflow=workflow,
            state=MasterResponsePolicyState.INSUFFICIENT_EVIDENCE,
            limitations=response_policy_for(workflow, MasterResponsePolicyState.INSUFFICIENT_EVIDENCE).limitations,
            status_text=INSTRUCTION,
        )
    rendered = render_master_response(
        response_policy_for(workflow, MasterResponsePolicyState.UNAVAILABLE)
    )
    assert rendered.status_text == "Required evidence is unavailable. No count or zero is inferred."
    assert INSTRUCTION not in rendered.status_text
    assert "runway" not in rendered.status_text.lower()
    assert "atc" not in rendered.status_text.lower()
    with pytest.raises(ContractValidationError, match="invalid_response_policy"):
        render_master_response(INSTRUCTION)  # type: ignore[arg-type]
    with pytest.raises(ContractValidationError, match="invalid_status_text"):
        MasterResponseRender(
            state=MasterResponsePolicyState.INSUFFICIENT_EVIDENCE,
            status_text=INSTRUCTION,
            limitation_codes=("NO_OPERATIONAL_CLAIM",),
        )


def test_renderer_rejects_an_admitted_claim_and_reordered_codes():
    with pytest.raises(ContractValidationError, match="operational_claim_not_admitted"):
        MasterResponseRender(
            state=MasterResponsePolicyState.INSUFFICIENT_EVIDENCE,
            status_text="Supplied evidence is not sufficient for an operational claim.",
            limitation_codes=("NO_OPERATIONAL_CLAIM",),
            claims_admitted=True,
        )
    with pytest.raises(ContractValidationError, match="limitation_order_not_deterministic"):
        MasterResponseRender(
            state=MasterResponsePolicyState.INSUFFICIENT_EVIDENCE,
            status_text="Supplied evidence is not sufficient for an operational claim.",
            limitation_codes=("EXECUTION_DENIED", "NO_OPERATIONAL_CLAIM"),
        )
