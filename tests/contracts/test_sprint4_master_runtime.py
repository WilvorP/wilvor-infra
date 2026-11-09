"""Sprint 4 Agent D: native multi-specialist runtime composition. Offline only."""

from __future__ import annotations

import ast
import os
import subprocess
import sys
from pathlib import Path

import pytest

from tests.contracts.test_sprint3c_specialist_orchestration import (
    BEFORE,
    LIVE,
    TEXT,
    FakeProvider,
    Harness,
    _live,
    _network_from_turn,
    _runtime,
    _tools,
    _work,
)
from wilvor_ai.contracts import ContractValidationError
from wilvor_ai.master_contracts import MasterRequirement, MasterSpecialist
from wilvor_ai.master_evidence_admission import MasterSourceAuthenticity
from wilvor_ai.master_live_ops_binding import prepare_master_live_ops_binding
from wilvor_ai.master_orchestration import SyntheticWorkerResult, SyntheticOutcome
from wilvor_ai.master_parallel_contracts import MasterResponseLimitation, MasterResponsePolicyState
from wilvor_ai.master_routing import (
    MasterProposedCapability,
    MasterRouteExecutionAuthority,
    MasterRouteOutcome,
    MasterRouteProposal,
)
from wilvor_ai.master_specialist_runtime_contract import invoke_specialist
from wilvor_ai.sprint3c_specialist_orchestration import NATIVE_PRODUCTION_DISPATCH_AVAILABLE
from wilvor_ai.sprint4_master_planning import Sprint4PlanningStatus
from wilvor_ai.sprint4_master_runtime import (
    NATIVE_GRAPH_NODES,
    SPRINT4_CLAIMS_ADMITTED,
    SPRINT4_NATIVE_PRODUCTION_DISPATCH_AVAILABLE,
    Sprint4LiveOpsDispatch,
    Sprint4NativeDispatchHandle,
    Sprint4StepStatus,
    Sprint4TerminalStatus,
    execute_authorized_native_plan,
    refuse_synthetic_as_native_evidence,
    refuse_unconsumed_native_dispatch,
    run_sprint4_master_runtime,
    synthesize_grounded_response,
)


MODULE = (
    Path(__file__).resolve().parents[2]
    / "functions"
    / "shared"
    / "wilvor_ai"
    / "sprint4_master_runtime.py"
)
SHARED_DIR = Path(__file__).resolve().parents[2] / "functions" / "shared"


class ScriptedPlanner:
    def __init__(self, proposal):
        self.proposal = proposal

    def propose(self, context):
        return self.proposal


def _proposal() -> MasterRouteProposal:
    return MasterRouteProposal(
        user_text=TEXT,
        capabilities=(MasterProposedCapability(LIVE, MasterRequirement.REQUIRED),),
        steps=(_live(),),
    )


def _handle(harness: Harness, step, binding, provider) -> Sprint4NativeDispatchHandle:
    return Sprint4NativeDispatchHandle(
        specialist=MasterSpecialist.LIVE_OPS,
        authority=harness.authority(step),
        live_ops=Sprint4LiveOpsDispatch(binding=binding, runtime=_runtime(provider)),
    )


def test_production_and_claim_gates_stay_closed():
    assert SPRINT4_NATIVE_PRODUCTION_DISPATCH_AVAILABLE is False
    assert SPRINT4_CLAIMS_ADMITTED is False
    assert NATIVE_PRODUCTION_DISPATCH_AVAILABLE is False
    assert NATIVE_GRAPH_NODES == (
        "validate_input",
        "select_ready",
        "invoke_native_specialist",
        "finalize_workflow",
    )
    with pytest.raises(ContractValidationError, match="specialist_invocation_not_granted"):
        invoke_specialist()
    with pytest.raises(ContractValidationError, match="consumed_grant_required"):
        refuse_unconsumed_native_dispatch()
    with pytest.raises(ContractValidationError, match="synthetic_result_not_evidence"):
        refuse_synthetic_as_native_evidence(
            SyntheticWorkerResult(
                master_request_id="master-req-1",
                correlation_id="corr-1",
                invocation_id="inv-1",
                specialist_request_id="spec-1",
                specialist=MasterSpecialist.LIVE_OPS,
                outcome=SyntheticOutcome.SYNTHETIC_COMPLETED,
                note="synthetic",
            )
        )


def test_native_langgraph_invokes_live_ops_and_renders_grounded_ma5():
    harness = Harness(_work(LIVE), (_live(),))
    step = harness.step("live-1")
    binding = prepare_master_live_ops_binding(harness.bundle(), harness.descriptor("live-1"))
    provider = FakeProvider([_tools(("get_observed_network_state", {})), _network_from_turn])
    handle = _handle(harness, step, binding, provider)
    result = run_sprint4_master_runtime(
        harness.planning(),
        ScriptedPlanner(_proposal()),
        orchestrator=harness.orchestrator,
        handles={step.invocation.invocation_id: handle},
        now_utc=BEFORE,
        retained_decision=harness.registry.decision_for(harness.record),
    )
    assert result.planning.status is Sprint4PlanningStatus.AUTHORIZED_PLAN
    assert result.workflow is not None
    assert result.workflow.terminal_status is Sprint4TerminalStatus.NATIVE_COMPLETED
    assert result.workflow.execution_authority is MasterRouteExecutionAuthority.DENIED
    assert result.workflow.evidence_verified is False
    assert len(result.workflow.results) == 1
    native = result.workflow.results[0]
    assert native.specialist is MasterSpecialist.LIVE_OPS
    assert native.live_ops is not None
    assert native.live_ops.live_ops_run_invoked is True
    assert native.source_authenticity is MasterSourceAuthenticity.NOT_ESTABLISHED
    assert native.production_dispatch is False
    assert result.synthesis is not None
    assert result.synthesis.claims_admitted is False
    assert result.synthesis.rendered.claims_admitted is False
    assert result.synthesis.source_authenticity is MasterSourceAuthenticity.NOT_ESTABLISHED
    assert result.synthesis.handoff.state is MasterResponsePolicyState.INSUFFICIENT_EVIDENCE
    limitations = set(result.synthesis.handoff.limitations)
    assert MasterResponseLimitation.SOURCE_AUTHENTICITY_NOT_ESTABLISHED in limitations
    assert MasterResponseLimitation.NO_OPERATIONAL_CLAIM in limitations
    assert MasterResponseLimitation.EXECUTION_DENIED in limitations
    assert "divert" not in result.synthesis.rendered.status_text.casefold()
    assert set(result.workflow.executed_nodes) >= {"validate_input", "select_ready", "invoke_native_specialist"}


def test_unconsumed_grant_does_not_call_live_ops_run():
    harness = Harness(_work(LIVE), (_live(),))
    step = harness.step("live-1")
    binding = prepare_master_live_ops_binding(harness.bundle(), harness.descriptor("live-1"))
    provider = FakeProvider([_tools(("get_observed_network_state", {})), _network_from_turn])
    handle = Sprint4NativeDispatchHandle(
        specialist=MasterSpecialist.LIVE_OPS,
        authority=harness.authority(step, consume=False),
        live_ops=Sprint4LiveOpsDispatch(binding=binding, runtime=_runtime(provider)),
    )
    with pytest.raises(ContractValidationError, match="consumed_grant_required|grant_not_consumed"):
        execute_authorized_native_plan(
            harness.registry.decision_for(harness.record),
            harness.orchestrator,
            {step.invocation.invocation_id: handle},
            now_utc=BEFORE,
        )
    assert provider.requests == []


def test_second_dispatch_of_the_same_invocation_fails():
    harness = Harness(_work(LIVE), (_live(),))
    step = harness.step("live-1")
    binding = prepare_master_live_ops_binding(harness.bundle(), harness.descriptor("live-1"))
    provider = FakeProvider([_tools(("get_observed_network_state", {})), _network_from_turn])
    handle = _handle(harness, step, binding, provider)
    first = execute_authorized_native_plan(
        harness.registry.decision_for(harness.record),
        harness.orchestrator,
        {step.invocation.invocation_id: handle},
        now_utc=BEFORE,
    )
    assert first.terminal_status is Sprint4TerminalStatus.NATIVE_COMPLETED
    second_provider = FakeProvider([_tools(("get_observed_network_state", {})), _network_from_turn])
    with pytest.raises(ContractValidationError, match="duplicate_dispatch|consumed_grant_required"):
        execute_authorized_native_plan(
            harness.registry.decision_for(harness.record),
            harness.orchestrator,
            {
                step.invocation.invocation_id: Sprint4NativeDispatchHandle(
                    specialist=MasterSpecialist.LIVE_OPS,
                    authority=handle.authority,
                    live_ops=Sprint4LiveOpsDispatch(
                        binding=binding,
                        runtime=_runtime(second_provider),
                    ),
                )
            },
            now_utc=BEFORE,
        )


def test_missing_required_handle_fails_without_claiming_success():
    harness = Harness(_work(LIVE), (_live(),))
    workflow = execute_authorized_native_plan(
        harness.registry.decision_for(harness.record),
        harness.orchestrator,
        {},
        now_utc=BEFORE,
    )
    assert workflow.terminal_status is Sprint4TerminalStatus.FAILED
    assert workflow.results == ()
    assert workflow.step_statuses[0][1] is Sprint4StepStatus.FAILED


def test_synthetic_worker_result_cannot_enter_grounded_synthesis():
    harness = Harness(_work(LIVE), (_live(),))
    step = harness.step("live-1")
    binding = prepare_master_live_ops_binding(harness.bundle(), harness.descriptor("live-1"))
    provider = FakeProvider([_tools(("get_observed_network_state", {})), _network_from_turn])
    handle = _handle(harness, step, binding, provider)
    workflow = execute_authorized_native_plan(
        harness.registry.decision_for(harness.record),
        harness.orchestrator,
        {step.invocation.invocation_id: handle},
        now_utc=BEFORE,
    )
    synthesis = synthesize_grounded_response(harness.planning(), workflow)
    assert synthesis.claims_admitted is False
    assert synthesis.admissions.source_authenticity is MasterSourceAuthenticity.NOT_ESTABLISHED


def test_planner_refusal_does_not_dispatch_specialists():
    harness = Harness(_work(LIVE), (_live(),))
    from wilvor_ai.model_contracts import ModelDecision, ModelDecisionKind
    from wilvor_ai.specialist_contracts import UnsupportedReason

    class RefusingPlanner:
        def complete(self, turn):
            return ModelDecision(
                kind=ModelDecisionKind.UNSUPPORTED,
                unsupported_reason=UnsupportedReason.OUT_OF_CATALOG,
            )

    result = run_sprint4_master_runtime(
        harness.planning(),
        RefusingPlanner(),
        orchestrator=harness.orchestrator,
        handles={},
        now_utc=BEFORE,
        retained_decision=harness.registry.decision_for(harness.record),
    )
    assert result.planning.status is Sprint4PlanningStatus.PLANNER_UNSUPPORTED
    assert result.workflow is None
    assert result.synthesis is None


def test_runtime_import_graph_keeps_sdks_out_and_reuses_langgraph():
    tree = ast.parse(MODULE.read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    assert "langgraph.graph" in imported
    assert "wilvor_ai.sprint3c_specialist_orchestration" in imported
    assert "wilvor_ai.sprint3d_native_specialists" in imported
    assert "wilvor_ai.master_orchestration" in imported
    assert imported.isdisjoint({"boto3", "botocore", "anthropic", "openai"})
    source = MODULE.read_text(encoding="utf-8")
    assert "invoke_fake_specialist" not in source
    assert "boto3.client" not in source


def test_importing_runtime_does_not_load_vendor_sdks():
    env = os.environ.copy()
    pythonpath = str(SHARED_DIR)
    existing = env.get("PYTHONPATH", "")
    if existing:
        pythonpath = pythonpath + os.pathsep + existing
    env["PYTHONPATH"] = pythonpath
    script = """
import sys
import wilvor_ai.sprint4_master_runtime as runtime
assert 'anthropic' not in sys.modules
assert 'openai' not in sys.modules
assert runtime.SPRINT4_NATIVE_PRODUCTION_DISPATCH_AVAILABLE is False
assert runtime.SPRINT4_CLAIMS_ADMITTED is False
assert runtime.NATIVE_GRAPH_NODES[2] == 'invoke_native_specialist'
"""
    completed = subprocess.run(
        [sys.executable, "-B", "-c", script],
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr + completed.stdout
