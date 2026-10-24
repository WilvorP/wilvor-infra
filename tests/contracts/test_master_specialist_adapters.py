"""Offline MA6 adapter-descriptor tests. Descriptors are not run."""

from __future__ import annotations

import ast
import os
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from wilvor_ai.contracts import AgentCapability, ContractValidationError
from wilvor_ai.master_contracts import (
    MasterProductionAuthority,
    MasterPurpose,
    MasterRequirement,
    MasterSpecialist,
)
from wilvor_ai.master_orchestration import (
    SyntheticOutcome,
    SyntheticTerminalStatus,
    SyntheticWorkerResult,
    SyntheticWorkflowResult,
)
from wilvor_ai.master_parallel_contracts import MasterParallelContractBundle
from wilvor_ai.master_routing import (
    MAX_DECISION_STEPS,
    MAX_ROUTE_STEPS,
    MasterPlanningContext,
    MasterProposedCapability,
    MasterProposedStep,
    MasterRouteExecutionAuthority,
    MasterRouteHints,
    MasterRouteOutcome,
    MasterRouteProposal,
    MasterRouteTemporalIntent,
    authorize_route,
)
from wilvor_ai.master_specialist_adapters import (
    MasterAdapterExecutionState,
    MasterSpecialistAdapterDescriptor,
    accept_planned_adapters,
    describe_planned_adapters,
    refuse_adapter_evidence,
)


TEXT = "Which aircraft are currently affected by hazards, and what recorded history applies?"
LIVE = AgentCapability.RETRIEVE_DETERMINISTIC_OPERATIONAL_CONTEXT
HISTORY = AgentCapability.RETRIEVE_HISTORICAL_ANALYTICS
RISK = AgentCapability.EXPLAIN_EXISTING_RISK
REPO_ROOT = Path(__file__).resolve().parents[2]
SHARED_DIR = REPO_ROOT / "functions" / "shared"
MODULES = (
    SHARED_DIR / "wilvor_ai" / "master_execution_preflight.py",
    SHARED_DIR / "wilvor_ai" / "master_specialist_adapters.py",
)
FORBIDDEN_MODULES = {
    "anthropic",
    "boto3",
    "botocore",
    "langgraph",
    "openai",
    "wilvor_ai.decision_specialist",
    "wilvor_ai.historical_specialist",
    "wilvor_ai.live_ops",
    "wilvor_ai.live_ops_adapter",
    "wilvor_ai.master_orchestration",
    "wilvor_operational",
}


def _context(**overrides: object) -> MasterPlanningContext:
    values: dict[str, object] = {
        "master_request_id": "master-req-ma6-1",
        "correlation_id": "corr-ma6-1",
        "application_request_time_utc": "2026-01-15T00:00:01Z",
        "canonical_user_text": TEXT,
    }
    values.update(overrides)
    return MasterPlanningContext(**values)  # type: ignore[arg-type]


def _proposal(*steps: MasterProposedStep) -> MasterRouteProposal:
    capabilities = tuple(
        dict.fromkeys(
            MasterProposedCapability(step.capability, MasterRequirement.REQUIRED) for step in steps
        )
    )
    return MasterRouteProposal(user_text=TEXT, capabilities=capabilities, steps=steps)


def _live(step_id: str = "live-1") -> MasterProposedStep:
    return MasterProposedStep(
        step_id=step_id,
        specialist=MasterSpecialist.LIVE_OPS,
        purpose=MasterPurpose.ANSWER,
        requirement=MasterRequirement.REQUIRED,
        capability=LIVE,
        temporal_intent=MasterRouteTemporalIntent.CURRENT,
    )


def _history(step_id: str = "hist-1") -> MasterProposedStep:
    return MasterProposedStep(
        step_id=step_id,
        specialist=MasterSpecialist.HISTORICAL_ANALYTICS,
        purpose=MasterPurpose.ANSWER,
        requirement=MasterRequirement.REQUIRED,
        capability=HISTORY,
        temporal_intent=MasterRouteTemporalIntent.HISTORICAL,
        historical_start_utc="2026-01-01T00:00:00Z",
        historical_end_utc="2026-01-02T00:00:00Z",
    )


def _decision(step_id: str = "dec-1", **overrides: object) -> MasterProposedStep:
    values: dict[str, object] = {
        "step_id": step_id,
        "specialist": MasterSpecialist.DECISION,
        "purpose": MasterPurpose.ANSWER,
        "requirement": MasterRequirement.REQUIRED,
        "capability": RISK,
        "temporal_intent": MasterRouteTemporalIntent.CURRENT,
    }
    values.update(overrides)
    return MasterProposedStep(**values)  # type: ignore[arg-type]


def _bundle(*steps: MasterProposedStep, **context_overrides: object) -> MasterParallelContractBundle:
    context = _context(**context_overrides)
    decision = authorize_route(_proposal(*steps), context)
    assert decision.outcome is MasterRouteOutcome.AUTHORIZED_PLAN
    assert decision.plan is not None
    assert decision.plan.execution_authority is MasterRouteExecutionAuthority.DENIED
    return MasterParallelContractBundle(context=context, plan=decision.plan, evidence_inputs=())


def _loose(
    specialist: MasterSpecialist,
    invocation_id: str,
    *,
    step_id: str = "step-x",
    master_request_id: str = "master-req-ma6-1",
) -> MasterSpecialistAdapterDescriptor:
    return MasterSpecialistAdapterDescriptor(
        master_request_id=master_request_id,
        step_id=step_id,
        invocation_id=invocation_id,
        specialist=specialist,
        purpose=MasterPurpose.ANSWER,
        requirement=MasterRequirement.REQUIRED,
        specialist_request_id=f"req-{invocation_id}",
    )


def _assert_matches(bundle: MasterParallelContractBundle, specialist: MasterSpecialist) -> None:
    described = describe_planned_adapters(bundle)
    assert len(described.descriptors) == 1
    descriptor = described.descriptors[0]
    step = bundle.plan.steps[0]
    assert descriptor.master_request_id == bundle.context.master_request_id
    assert descriptor.step_id == step.step_id
    assert descriptor.invocation_id == step.invocation.invocation_id
    assert descriptor.specialist is specialist
    assert descriptor.purpose is step.invocation.purpose
    assert descriptor.requirement is step.invocation.requirement
    assert descriptor.specialist_request_id == step.invocation.specialist_request_id
    assert (
        descriptor.execution_state
        is MasterAdapterExecutionState.BLOCKED_BEFORE_EXECUTION_AUTHORIZATION
    )
    assert descriptor.production_authority is MasterProductionAuthority.DENIED
    assert descriptor.execution_authority is MasterRouteExecutionAuthority.DENIED
    assert descriptor.decision_target_trusted is False
    assert descriptor.callsign_authenticated is False
    assert descriptor.timestamp_equivalence_established is False
    assert descriptor.historical_run_fabricated is False
    assert descriptor.live_ops_refs_are_tool_results is False
    assert step.target_authorization_granted is False
    assert callable(descriptor) is False
    assert "dispatch" not in MasterSpecialistAdapterDescriptor.__dataclass_fields__
    assert describe_planned_adapters(bundle) == described


def test_planned_live_ops_adapter_descriptor():
    _assert_matches(_bundle(_live()), MasterSpecialist.LIVE_OPS)


def test_planned_historical_adapter_descriptor():
    _assert_matches(_bundle(_history()), MasterSpecialist.HISTORICAL_ANALYTICS)


def test_planned_decision_adapter_descriptor():
    _assert_matches(_bundle(_decision()), MasterSpecialist.DECISION)


def test_unplanned_invocation_rejected():
    bundle = _bundle(_live())
    planned = describe_planned_adapters(bundle).descriptors[0]
    invented = replace(planned, invocation_id="inv-not-on-the-plan")
    with pytest.raises(ContractValidationError, match="invocation_not_in_plan"):
        accept_planned_adapters(bundle, (invented,))


def test_wrong_master_request_rejected():
    bundle = _bundle(_live())
    planned = describe_planned_adapters(bundle).descriptors[0]
    foreign = replace(planned, master_request_id="other-master-request")
    with pytest.raises(ContractValidationError, match="master_request_mismatch"):
        accept_planned_adapters(bundle, (foreign,))


def test_wrong_specialist_type_rejected():
    bundle = _bundle(_live())
    planned = describe_planned_adapters(bundle).descriptors[0]
    swapped = replace(planned, specialist=MasterSpecialist.HISTORICAL_ANALYTICS)
    with pytest.raises(ContractValidationError, match="specialist_mismatch"):
        accept_planned_adapters(bundle, (swapped,))


def test_wrong_specialist_request_id_rejected():
    bundle = _bundle(_history())
    planned = describe_planned_adapters(bundle).descriptors[0]
    renamed = replace(planned, specialist_request_id="other-specialist-request")
    with pytest.raises(ContractValidationError, match="specialist_request_id_mismatch"):
        accept_planned_adapters(bundle, (renamed,))


def test_duplicate_invocation_id_rejected():
    bundle = _bundle(_decision())
    planned = describe_planned_adapters(bundle).descriptors[0]
    with pytest.raises(ContractValidationError, match="duplicate_invocation_id"):
        accept_planned_adapters(bundle, (planned, planned))


def test_more_than_four_planned_steps_rejected():
    assert MAX_ROUTE_STEPS == 4
    with pytest.raises(ContractValidationError, match="excessive_plan_size"):
        _proposal(*tuple(_live(f"live-{index}") for index in range(MAX_ROUTE_STEPS + 1)))
    bundle = _bundle(_live())
    oversized = tuple(
        _loose(MasterSpecialist.LIVE_OPS, f"inv-extra-{index}", step_id=f"step-{index}")
        for index in range(MAX_ROUTE_STEPS + 1)
    )
    with pytest.raises(ContractValidationError, match="excessive_plan_size"):
        accept_planned_adapters(bundle, oversized)


def test_more_than_one_decision_step_rejected():
    assert MAX_DECISION_STEPS == 1
    with pytest.raises(ContractValidationError, match="decision_fanout_not_permitted"):
        _proposal(_decision("dec-1"), _decision("dec-2"))
    bundle = _bundle(_live())
    pair = (
        _loose(MasterSpecialist.DECISION, "inv-dec-a", step_id="dec-a"),
        _loose(MasterSpecialist.DECISION, "inv-dec-b", step_id="dec-b"),
    )
    with pytest.raises(ContractValidationError, match="decision_fanout_not_permitted"):
        accept_planned_adapters(bundle, pair)


def test_adapter_cannot_turn_synthetic_results_into_production_evidence():
    bundle = _bundle(_history())
    descriptor = describe_planned_adapters(bundle).descriptors[0]
    step = bundle.plan.steps[0]
    worker = SyntheticWorkerResult(
        master_request_id=bundle.context.master_request_id,
        correlation_id=bundle.context.correlation_id,
        invocation_id=step.invocation.invocation_id,
        specialist_request_id=step.invocation.specialist_request_id or "specialist-synthetic",
        specialist=step.invocation.specialist,
        outcome=SyntheticOutcome.NO_RESULT,
        note="synthetic-not-evidence",
    )
    workflow = SyntheticWorkflowResult(
        terminal_status=SyntheticTerminalStatus.NOT_RUN,
        reasons=(),
        decision=None,
        step_statuses=(),
        results=(),
        limitations=(),
        dispatch_count=0,
        dispatch_batches=(),
        executed_nodes=(),
    )
    for artifact in (worker, workflow):
        with pytest.raises(ContractValidationError, match="synthetic_result_not_evidence"):
            refuse_adapter_evidence(descriptor, artifact)
    with pytest.raises(ContractValidationError, match="adapter_evidence_not_issued"):
        refuse_adapter_evidence(descriptor, object())
    assert descriptor.historical_run_fabricated is False
    with pytest.raises(ContractValidationError, match="historical_run_not_fabricated"):
        replace(descriptor, historical_run_fabricated=True)
    with pytest.raises(ContractValidationError, match="live_ops_refs_not_tool_results"):
        replace(descriptor, live_ops_refs_are_tool_results=True)
    with pytest.raises(ContractValidationError, match="timestamp_equivalence_not_established"):
        replace(descriptor, timestamp_equivalence_established=True)


def test_decision_target_remains_untrusted():
    bundle = _bundle(
        _decision(hints=MasterRouteHints(callsign="UAL123")),
        target_candidate_aircraft_id="N12345",
    )
    step = bundle.plan.steps[0]
    descriptor = describe_planned_adapters(bundle).descriptors[0]
    assert step.target_authorization_granted is False
    assert step.invocation.expected_aircraft_id == "N12345"
    assert descriptor.decision_target_trusted is False
    assert descriptor.callsign_authenticated is False
    assert "callsign" not in MasterSpecialistAdapterDescriptor.__dataclass_fields__
    assert "expected_aircraft_id" not in MasterSpecialistAdapterDescriptor.__dataclass_fields__
    with pytest.raises(ContractValidationError, match="target_authorization_not_granted"):
        replace(descriptor, decision_target_trusted=True)
    with pytest.raises(ContractValidationError, match="callsign_not_authenticated_aircraft"):
        replace(descriptor, callsign_authenticated=True)
    with pytest.raises(ContractValidationError, match="production_authority_not_issued"):
        replace(descriptor, production_authority=MasterProductionAuthority.PRODUCTION)
    with pytest.raises(ContractValidationError, match="execution_not_granted"):
        replace(descriptor, execution_state="GRANTED")  # type: ignore[arg-type]


def test_importing_ma6_foundations_does_not_load_runtimes():
    for path in MODULES:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imported = {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        }
        imported.update(
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module
        )
        assert FORBIDDEN_MODULES.isdisjoint(imported)
        source = path.read_text(encoding="utf-8")
        assert "langgraph" not in source
        assert "boto3" not in source
        assert "AthenaExecutor" not in source
    env = os.environ.copy()
    pythonpath = str(SHARED_DIR)
    existing = env.get("PYTHONPATH", "")
    if existing:
        pythonpath = pythonpath + os.pathsep + existing
    env["PYTHONPATH"] = pythonpath
    absent = sorted(FORBIDDEN_MODULES)
    script = f"""
import sys
import wilvor_ai.master_execution_preflight as preflight
import wilvor_ai.master_specialist_adapters as adapters
loaded = [name for name in {absent!r} if name in sys.modules]
assert loaded == [], loaded
assert preflight.MasterExecutionPreflightEvaluation.__dataclass_fields__['dispatch_permitted'].default is False
assert adapters.MasterAdapterExecutionState.BLOCKED_BEFORE_EXECUTION_AUTHORIZATION.value == 'BLOCKED_BEFORE_EXECUTION_AUTHORIZATION'
"""
    completed = subprocess.run(
        [sys.executable, "-B", "-c", script],
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr + completed.stdout


def test_repeated_adapter_descriptions_are_deterministic():
    bundle = _bundle(_live(), _history(), _decision())
    first = describe_planned_adapters(bundle)
    second = describe_planned_adapters(bundle)
    assert first == second
    assert [item.specialist for item in first.descriptors] == [
        step.invocation.specialist for step in bundle.plan.steps
    ]
    assert {item.specialist for item in first.descriptors} == {
        MasterSpecialist.LIVE_OPS,
        MasterSpecialist.HISTORICAL_ANALYTICS,
        MasterSpecialist.DECISION,
    }
    assert all(
        item.execution_state is MasterAdapterExecutionState.BLOCKED_BEFORE_EXECUTION_AUTHORIZATION
        for item in first.descriptors
    )
    assert accept_planned_adapters(bundle, first.descriptors) == first
