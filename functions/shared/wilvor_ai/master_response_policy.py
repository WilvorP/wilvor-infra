"""Offline Master response policy. Not an operational answer.

Precedence, first match wins:

1. An empty evidence collection is ``INSUFFICIENT_EVIDENCE``. Absence is not
   zero and it is not a verified unavailable condition. MA4 is not called.
2. Any rejected admission or supplied-evidence contradiction is
   ``INSUFFICIENT_EVIDENCE``. It is not a factual answer.
3. Any ``BINDING_UNRESOLVED`` admission stays unresolved as
   ``INSUFFICIENT_EVIDENCE``. It is not promoted to unavailable or unsupported.
4. A comparison whose evidence input is missing is not fabricated and is not
   sent to MA4.3. The response stays ``INSUFFICIENT_EVIDENCE``.
5. ``UNAVAILABLE`` is used only when every supplied artifact has that native
   status, none of those checks was rejected, inconsistent, or unresolved,
   and none was merely consistent with supplied evidence.
6. ``UNSUPPORTED`` uses the same gate for an explicit native unsupported
   status. A comparability eligibility of ``UNSUPPORTED`` does not qualify.
7. Otherwise the response is ``INSUFFICIENT_EVIDENCE``, including
   ``CONSISTENT_WITH_SUPPLIED_EVIDENCE`` and ``CONTRACT_CHECKABLE_ONLY``.

Those MA4 findings are not source authentication. Claims stay suppressed.
"""

from __future__ import annotations

from wilvor_ai.contracts import ContractValidationError
from wilvor_ai.decision_specialist_runtime_contracts import (
    DecisionSpecialistRunResult,
    DecisionSpecialistRunStatus,
)
from wilvor_ai.live_ops_public_contracts import LiveOpsRunStatus
from wilvor_ai.master_evidence_admission import LiveOpsAdmissionSources, MasterAdmissionStatus
from wilvor_ai.master_evidence_comparability import (
    MasterComparisonAssessment,
    assess_master_evidence_comparability,
)  # eligibility stays inside the assessment; this module does not re-score it.
from wilvor_ai.master_evidence_corroboration import (
    MasterCorroborationStatus,
    MasterEvidenceCorroboration,
    MasterEvidenceCorroborationInput,
    corroborate_master_evidence,
)
from wilvor_ai.master_parallel_contracts import (
    MasterParallelContractBundle,
    MasterResponseLimitation,
    MasterResponsePolicyHandoff,
    MasterResponsePolicyState,
    response_policy_for,
)
from wilvor_ai.specialist_contracts import SpecialistResult, SpecialistStatus


def evaluate_master_response_policy(
    workflow: MasterParallelContractBundle,
) -> MasterResponsePolicyHandoff:
    """Evaluate one bundle with the existing MA4 functions. Claims stay suppressed."""

    if not isinstance(workflow, MasterParallelContractBundle):
        raise ContractValidationError("invalid_parallel_contract")
    results = _corroborate(workflow)
    missing_comparison = False
    assessments: list[MasterComparisonAssessment] = []
    by_invocation = {
        item.admission_input.authorization.invocation_id: item.admission_input
        for item in workflow.evidence_inputs
    }
    for request in workflow.comparison_requests:
        left = by_invocation.get(request.left_invocation_id)
        right = by_invocation.get(request.right_invocation_id)
        if left is None or right is None:
            missing_comparison = True
            continue
        assessments.append(assess_master_evidence_comparability(request, left, right))
    state = _state(workflow.evidence_inputs, results, missing_comparison)
    return response_policy_for(
        workflow,
        state,
        _limitations(workflow, assessments, missing_comparison),
    )


def _corroborate(
    workflow: MasterParallelContractBundle,
) -> tuple[MasterEvidenceCorroboration, ...]:
    if not workflow.evidence_inputs:
        return ()
    return corroborate_master_evidence(workflow.evidence_inputs).results


def _state(
    inputs: tuple[MasterEvidenceCorroborationInput, ...],
    results: tuple[MasterEvidenceCorroboration, ...],
    missing_comparison: bool,
) -> MasterResponsePolicyState:
    if not results:
        return MasterResponsePolicyState.INSUFFICIENT_EVIDENCE
    if any(_blocks_specific_state(item) for item in results):
        return MasterResponsePolicyState.INSUFFICIENT_EVIDENCE
    if missing_comparison:
        return MasterResponsePolicyState.INSUFFICIENT_EVIDENCE
    dispositions = tuple(_disposition(item.admission_input.artifact) for item in inputs)
    if _uniform_native(dispositions, results, "unavailable"):
        return MasterResponsePolicyState.UNAVAILABLE
    if _uniform_native(dispositions, results, "unsupported"):
        return MasterResponsePolicyState.UNSUPPORTED
    return MasterResponsePolicyState.INSUFFICIENT_EVIDENCE


def _blocks_specific_state(result: MasterEvidenceCorroboration) -> bool:
    if result.corroboration_status in {
        MasterCorroborationStatus.ADMISSION_REJECTED,
        MasterCorroborationStatus.INCONSISTENT,
    }:
        return True
    if result.admission_status is MasterAdmissionStatus.BINDING_UNRESOLVED:
        return True
    return result.admission_status is MasterAdmissionStatus.REJECTED


def _uniform_native(
    dispositions: tuple[str | None, ...],
    results: tuple[MasterEvidenceCorroboration, ...],
    expected: str,
) -> bool:
    if not dispositions or any(item != expected for item in dispositions):
        return False
    return all(
        item.corroboration_status
        is not MasterCorroborationStatus.CONSISTENT_WITH_SUPPLIED_EVIDENCE
        for item in results
    )


def _disposition(artifact: object) -> str | None:
    if isinstance(artifact, SpecialistResult):
        if artifact.status is SpecialistStatus.UNSUPPORTED:
            return "unsupported"
        if artifact.status is SpecialistStatus.UNAVAILABLE:
            return "unavailable"
        return None
    if isinstance(artifact, DecisionSpecialistRunResult):
        if artifact.status is DecisionSpecialistRunStatus.UNSUPPORTED:
            return "unsupported"
        if artifact.status is DecisionSpecialistRunStatus.UNAVAILABLE:
            return "unavailable"
        return None
    if isinstance(artifact, LiveOpsAdmissionSources):
        status = artifact.result.execution_status
        if status is LiveOpsRunStatus.UNSUPPORTED:
            return "unsupported"
        if status is LiveOpsRunStatus.UNAVAILABLE:
            return "unavailable"
    return None


def _limitations(
    workflow: MasterParallelContractBundle,
    assessments: list[MasterComparisonAssessment],
    missing_comparison: bool,
) -> tuple[MasterResponseLimitation, ...]:
    selected = {
        MasterResponseLimitation.SOURCE_AUTHENTICITY_NOT_ESTABLISHED,
        MasterResponseLimitation.PRODUCTION_AUTHORITY_DENIED,
        MasterResponseLimitation.REQUEST_COVERAGE_NOT_ESTABLISHED,
        MasterResponseLimitation.NO_OPERATIONAL_CLAIM,
        MasterResponseLimitation.EXECUTION_DENIED,
    }
    if workflow.evidence_inputs:
        selected.add(MasterResponseLimitation.SUPPLIED_EVIDENCE_NOT_AUTHENTICATED)
    if any(item.historical_run is None and _is_historical(item) for item in workflow.evidence_inputs):
        selected.add(MasterResponseLimitation.HISTORICAL_RUN_NOT_FABRICATED)
    if any(_is_live_ops(item) for item in workflow.evidence_inputs):
        selected.add(MasterResponseLimitation.LIVE_OPS_REFS_NOT_TOOL_RESULTS)
    if workflow.comparison_requests or assessments or missing_comparison:
        selected.add(MasterResponseLimitation.COMPARABILITY_NOT_OPERATIONAL_AGREEMENT)
    return tuple(item for item in MasterResponseLimitation if item in selected)


def _is_historical(item: MasterEvidenceCorroborationInput) -> bool:
    return isinstance(item.admission_input.artifact, SpecialistResult)


def _is_live_ops(item: MasterEvidenceCorroborationInput) -> bool:
    return isinstance(item.admission_input.artifact, LiveOpsAdmissionSources)
