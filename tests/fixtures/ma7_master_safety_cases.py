"""Offline adversarial cases for the frozen Master safety handoff.

Every object built here is an in-memory contract fixture. Nothing in this
module calls AWS, a model provider, a specialist runner, or LangGraph.
MA5 and MA6 modules are not imported. A passing case is a contract
rejection or a closed nonfactual classification, not an operational fact.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum

from tests.contracts.test_historical_verification import (
    AS_OF as HISTORICAL_AS_OF,
)
from tests.contracts.test_historical_verification import (
    CALL_A,
    _exact,
    _run,
    _succeeded_encounters,
    _truncated_list,
    _verified_zero,
    _window,
)
from tests.fixtures.live_ops_public_examples import (
    SYNTHETIC_CORRELATION_ID,
    SYNTHETIC_REQUEST_ID,
    SYNTHETIC_UTC,
    completed_all_verified,
    evidence,
    none_verified,
    partially_verified,
    sample_request,
    trusted_context,
)
from wilvor_ai.contracts import (
    AgentCapability,
    ConfidenceLevel,
    Evidence,
    FreshnessStatus,
    SourceRecord,
    TemporalScope,
    ToolResult,
    ToolResultStatus,
)
from wilvor_ai.decision_answer_renderer import render_decision_verification
from wilvor_ai.decision_claims import AircraftIdentityClaim, RiskAbsentClaim
from wilvor_ai.decision_contracts import (
    DecisionAdvisoryAuthority,
    DecisionChainGap,
    DecisionEncounterLink,
    DecisionEvaluationState,
    DecisionEvidence,
    DecisionEvidenceKind,
    DecisionRecommendationEvidence,
    DecisionRecommendationSet,
    DecisionReportedLinkState,
    DecisionRiskEvidence,
    DecisionRouteCapability,
    HazardSourceVersionLink,
    RecommendationActionType,
    RiskPresence,
    StoredRiskLevel,
    expected_decision_status,
)
from wilvor_ai.decision_evidence_verifier import (
    CONTEXT_TOOL,
    RISK_TOOL,
    DecisionEvidenceBinding,
    verify_decision_evidence,
)
from wilvor_ai.decision_model_contracts import DecisionEvidenceSnapshot
from wilvor_ai.decision_specialist_runtime_contracts import (
    DecisionDispatchOrigin,
    DecisionSpecialistRunResult,
    DecisionSpecialistRunStatus,
    DecisionTargetMode,
    DecisionToolInvocationAudit,
)
from wilvor_ai.decision_tool_projection import project_decision_tool_result
from wilvor_ai.historical_answer_renderer import finalize_historical_specialist_run
from wilvor_ai.historical_evidence_verifier import LOWER_BOUND_METRIC_ID
from wilvor_ai.live_ops_public_contracts import (
    LiveOpsPublicClaim,
    LiveOpsPublicClaimKind,
    LiveOpsPublicCountMetric,
    LiveOpsPublicCountMode,
    LiveOpsPublicObservedField,
    LiveOpsPublicScope,
    LiveOpsRunResult,
)
from wilvor_ai.master_contracts import (
    MasterInvocationAuthorization,
    MasterPurpose,
    MasterRequirement,
    MasterSpecialist,
)
from wilvor_ai.master_evidence_admission import (
    LiveOpsAdmissionSources,
    MasterEvidenceAdmissionInput,
)
from wilvor_ai.master_evidence_comparability import (
    MasterComparisonCategory,
    MasterComparisonRequest,
)
from wilvor_ai.master_evidence_corroboration import MasterEvidenceCorroborationInput
from wilvor_ai.master_parallel_contracts import (
    MAX_COMPARISON_REQUESTS,
    MasterParallelContractBundle,
)
from wilvor_ai.master_routing import (
    MAX_ROUTE_STEPS,
    MasterPlanningContext,
    MasterProposedCapability,
    MasterProposedStep,
    MasterRoutePlan,
    MasterRouteProposal,
    MasterRouteTemporalIntent,
    authorize_route,
)
from wilvor_ai.model_contracts import ModelDecisionKind
from wilvor_ai.specialist_contracts import (
    LowerBoundCountClaim,
    SpecialistResult,
    SpecialistStatus,
    VerifiedZeroClaim,
    VerifierOutcome,
)
from wilvor_ai.specialist_runtime_contracts import HistoricalSpecialistRunResult


MASTER_REQUEST = "master-req-ma7-safety"
OTHER_REQUEST = "master-req-ma7-other"
CORRELATION = "corr-ma7-safety"
OTHER_CORRELATION = "corr-ma7-other"
APP_TIME = "2026-01-15T00:00:01Z"
OTHER_APP_TIME = "2026-01-15T00:00:02Z"
USER_TEXT = "Which recorded hazards apply to the aircraft currently in view?"
LIVE_A = "inv-ma7-live-a"
LIVE_B = "inv-ma7-live-b"
HIST_A = "inv-ma7-hist-a"
HIST_B = "inv-ma7-hist-b"
DECISION_ID = "inv-ma7-decision"
AIRCRAFT_LABEL = "AC-1"
EARLY_POSITION_TIME = "2026-01-15T00:00:00Z"
LATER_POSITION_TIME = "2026-01-15T01:00:00Z"

LIVE_CAPABILITY = AgentCapability.RETRIEVE_DETERMINISTIC_OPERATIONAL_CONTEXT
HISTORY_CAPABILITY = AgentCapability.RETRIEVE_HISTORICAL_ANALYTICS
RISK_CAPABILITY = AgentCapability.EXPLAIN_EXISTING_RISK


class SafetyClassification(str, Enum):
    """How the frozen contract classifies the case. None of these grant a fact."""

    STRUCTURAL_REJECTION = "STRUCTURAL_REJECTION"
    BINDING_UNRESOLVED = "BINDING_UNRESOLVED"
    SUPPLIED_INCONSISTENT = "SUPPLIED_INCONSISTENT"
    CONSISTENT_NOT_AUTHENTICATED = "CONSISTENT_NOT_AUTHENTICATED"
    NOT_RECHECKABLE = "NOT_RECHECKABLE"
    COMPARISON_NOT_AGREEMENT = "COMPARISON_NOT_AGREEMENT"
    RESPONSE_NONFACTUAL = "RESPONSE_NONFACTUAL"
    EXECUTION_DENIED = "EXECUTION_DENIED"
    SYNTHETIC_NOT_EVIDENCE = "SYNTHETIC_NOT_EVIDENCE"


@dataclass(frozen=True)
class MasterSafetyCase:
    """One adversarial scenario and the contract outcome the baseline can support."""

    scenario_id: str
    invariant: str
    native_input: str
    expected_outcome: str
    classification: SafetyClassification
    danger: str
    expected_reasons: tuple[str, ...] = ()
    builder: str = ""


def _define(
    scenario_id: str,
    invariant: str,
    native_input: str,
    expected_outcome: str,
    classification: SafetyClassification,
    danger: str,
    reasons: tuple[str, ...] = (),
    builder: str = "",
) -> MasterSafetyCase:
    return MasterSafetyCase(
        scenario_id=scenario_id,
        invariant=invariant,
        native_input=native_input,
        expected_outcome=expected_outcome,
        classification=classification,
        danger=danger,
        expected_reasons=reasons,
        builder=builder,
    )


REQUIRED_SCENARIO_IDS: tuple[str, ...] = (
    "evidence-from-another-master-request",
    "duplicate-invocation-ids",
    "unplanned-specialist-result",
    "wrong-specialist-for-invocation",
    "different-correlation-ids",
    "different-application-request-timestamps",
    "multiple-decision-invocations",
    "excessive-route-steps",
    "historical-unavailable-with-passed-verifier",
    "forged-historical-final-answer",
    "historical-lower-bound-is-not-exact",
    "historical-verified-zero-is-not-unknown",
    "missing-historical-original-run",
    "live-ops-public-evidence-without-tool-results",
    "rejected-native-admission",
    "unresolved-native-binding",
    "inconsistent-supplied-evidence",
    "same-aircraft-label-is-not-authenticated-identity",
    "different-observation-timestamps",
    "same-hazard-different-source-versions",
    "missing-sigmet-geometry",
    "california-text-is-not-authenticated-geography",
    "historical-count-is-not-live-ops-subset",
    "missing-risk-is-not-low",
    "partial-count-is-not-network-conclusion",
    "excessive-comparison-requests",
    "duplicate-comparison-requests",
    "offline-handoff-cannot-emit-a-factual-response",
    "fixed-status-text-cannot-carry-an-operational-claim",
    "unavailable-count-is-not-zero",
    "consistent-ma4-result-is-not-authenticated-evidence",
    "unresolved-geography-is-not-a-confirmed-intersection",
    "response-handoff-cannot-create-a-pilot-or-atc-instruction",
    "route-does-not-grant-production-execution",
    "preflight-does-not-permit-dispatch",
    "provenance-requirement-cannot-be-marked-satisfied",
    "empty-plan-is-not-executable",
    "contract-ready-remains-non-executable",
    "synthetic-ma3-result-is-not-authentic-evidence",
)


SAFETY_CASES: tuple[MasterSafetyCase, ...] = (
    _define(
        "evidence-from-another-master-request",
        "Evidence stays bound to the Master request that planned its invocation.",
        "Authorized Live Ops step plus an evidence authorization whose master_request_id is a different request.",
        "master_request_mismatch",
        SafetyClassification.STRUCTURAL_REJECTION,
        "Accepting it would let one request's specialist output answer a different request.",
        builder="another_master_request_inputs",
    ),
    _define(
        "duplicate-invocation-ids",
        "One invocation id appears once in the evidence collection.",
        "Authorized Live Ops and Historical plan with the same evidence input repeated.",
        "duplicate_invocation_id",
        SafetyClassification.STRUCTURAL_REJECTION,
        "Accepting it would let one specialist result be counted twice.",
        builder="live_and_historical_bundle",
    ),
    _define(
        "unplanned-specialist-result",
        "A received artifact cannot create an invocation the route did not plan.",
        "Authorized Live Ops plan with evidence whose invocation_id is absent from the plan.",
        "invocation_not_in_plan",
        SafetyClassification.STRUCTURAL_REJECTION,
        "Accepting it would admit a specialist result the route never requested.",
        builder="unplanned_result_inputs",
    ),
    _define(
        "wrong-specialist-for-invocation",
        "The artifact family must be the specialist named by the planned authorization.",
        "Historical authorization carrying Live Ops public sources.",
        "unsupported_artifact",
        SafetyClassification.STRUCTURAL_REJECTION,
        "Accepting it would treat a Live Ops public result as a Historical analytics result.",
        ("SPECIALIST_ARTIFACT_MISMATCH", "REJECTED"),
        "wrong_specialist_inputs",
    ),
    _define(
        "different-correlation-ids",
        "Planned authorizations keep the planning context correlation id.",
        "Live Ops plan whose authorization correlation_id differs from the planning context.",
        "correlation_mismatch",
        SafetyClassification.STRUCTURAL_REJECTION,
        "Accepting it would join evidence from a different application correlation.",
        builder="correlation_mismatch_inputs",
    ),
    _define(
        "different-application-request-timestamps",
        "Planned authorizations keep the planning context application timestamp.",
        "Live Ops plan whose authorization application_request_time_utc differs from the planning context.",
        "application_time_mismatch",
        SafetyClassification.STRUCTURAL_REJECTION,
        "Accepting it would mix two application clocks into one request.",
        builder="application_time_mismatch_inputs",
    ),
    _define(
        "multiple-decision-invocations",
        "A route contains at most one Decision invocation.",
        "Route proposal and route plan that each contain two Decision steps.",
        "decision_fanout_not_permitted",
        SafetyClassification.STRUCTURAL_REJECTION,
        "Accepting it would let two Decision targets be planned for one Master request.",
        builder="two_decision_plan_steps",
    ),
    _define(
        "excessive-route-steps",
        "A route contains at most four steps.",
        "Route proposal and route plan that each contain five Live Ops steps.",
        "excessive_plan_size",
        SafetyClassification.STRUCTURAL_REJECTION,
        "Accepting it would exceed the bounded specialist fan-out the contract can validate.",
        builder="five_plan_steps",
    ),
    _define(
        "historical-unavailable-with-passed-verifier",
        "A stored PASSED verifier on an UNAVAILABLE Historical result is not a factual read.",
        "SpecialistResult with status UNAVAILABLE, verifier PASSED, no tool results, and no original run.",
        "NOT_RECHECKABLE",
        SafetyClassification.NOT_RECHECKABLE,
        "Accepting it would turn an unavailable specialist status into a verified historical answer.",
        (
            "HISTORICAL_STORED_PASSED_NOT_TRUSTED",
            "HISTORICAL_RUN_ABSENT",
            "INCOMPLETE_SPECIALIST_STATUS",
            "VERIFIED_ZERO_NOT_INFERRED",
        ),
        "unavailable_passed_input",
    ),
    _define(
        "forged-historical-final-answer",
        "A Historical final answer must match the answer rendered from the supplied run.",
        "Finalized forged-count run whose answer text is replaced before corroboration.",
        "INCONSISTENT",
        SafetyClassification.SUPPLIED_INCONSISTENT,
        "Accepting it would publish an answer the supplied tool evidence did not render.",
        ("ANSWER_MISMATCH",),
        "forged_answer_input",
    ),
    _define(
        "historical-lower-bound-is-not-exact",
        "A Historical lower bound and an exact count stay different metrics.",
        "Finalized exact-count run compared with a finalized truncated lower-bound run.",
        "CONTRACT_CHECKABLE_ONLY",
        SafetyClassification.COMPARISON_NOT_AGREEMENT,
        "Accepting it would report a minimum observed count as the exact population.",
        ("EXACT_COUNT_IS_NOT_LOWER_BOUND",),
        "lower_bound_comparison",
    ),
    _define(
        "historical-verified-zero-is-not-unknown",
        "A Historical verified zero is a different outcome from Live Ops NONE_VERIFIED.",
        "Finalized verified-zero Historical result compared with a NONE_VERIFIED Live Ops result.",
        "NOT_ESTABLISHED",
        SafetyClassification.COMPARISON_NOT_AGREEMENT,
        "Accepting it would erase a certified historical zero or invent a current zero from unknown.",
        ("VERIFIED_ZERO_IS_NOT_UNKNOWN",),
        "verified_zero_comparison",
    ),
    _define(
        "missing-historical-original-run",
        "A missing Historical run stays missing.",
        "Finalized exact-count SpecialistResult corroborated with historical_run left as None.",
        "NOT_RECHECKABLE",
        SafetyClassification.NOT_RECHECKABLE,
        "Accepting it would invent an original tool run from the final answer alone.",
        ("HISTORICAL_RUN_ABSENT",),
        "missing_historical_run_input",
    ),
    _define(
        "live-ops-public-evidence-without-tool-results",
        "Live Ops public evidence references are not raw tool results.",
        "LiveOpsAdmissionSources built from the public request, context, and ALL_VERIFIED result.",
        "NOT_RECHECKABLE",
        SafetyClassification.NOT_RECHECKABLE,
        "Accepting it would treat a public reference as the tool payload that produced it.",
        (
            "LIVE_OPS_RAW_EVIDENCE_UNAVAILABLE",
            "LIVE_OPS_NOT_RECHECKABLE",
            "LIVE_OPS_EVIDENCE_REFS_ARE_NOT_TOOL_RESULTS",
        ),
        "live_public_input",
    ),
    _define(
        "rejected-native-admission",
        "A rejected native binding stays rejected.",
        "Live Ops authorization whose specialist request id differs from the public result request id.",
        "REJECTED",
        SafetyClassification.STRUCTURAL_REJECTION,
        "Accepting it would use a public result that belongs to another specialist request.",
        ("REQUEST_ID_MISMATCH", "ADMISSION_REJECTED"),
        "rejected_live_input",
    ),
    _define(
        "unresolved-native-binding",
        "An unresolved native binding is not authenticated evidence.",
        "Finalized Historical result whose authorization carries a correlation the artifact cannot echo.",
        "BINDING_UNRESOLVED",
        SafetyClassification.BINDING_UNRESOLVED,
        "Accepting it would treat a structurally shaped result as bound to this application request.",
        ("NATIVE_CORRELATION_ABSENT",),
        "unresolved_historical_input",
    ),
    _define(
        "inconsistent-supplied-evidence",
        "Supplied Historical claims must match the claims rendered from the original run.",
        "Finalized exact-count run whose verified_claims are removed before corroboration.",
        "INCONSISTENT",
        SafetyClassification.SUPPLIED_INCONSISTENT,
        "Accepting it would hide a claim the supplied run actually rendered, or invent one it did not.",
        ("CLAIM_MISMATCH",),
        "inconsistent_claim_input",
    ),
    _define(
        "same-aircraft-label-is-not-authenticated-identity",
        "A shared aircraft id string is not an authenticated join.",
        "Live Ops impact claim and Decision run that both carry aircraft id AC-1.",
        "NOT_ESTABLISHED",
        SafetyClassification.COMPARISON_NOT_AGREEMENT,
        "Accepting it would conclude two specialist outputs describe the same aircraft.",
        ("SHARED_AIRCRAFT_ID_STRING_NOT_AUTHENTICATED_JOIN",),
        "same_aircraft_comparison",
    ),
    _define(
        "different-observation-timestamps",
        "Different position-time strings are not one observation.",
        "Two Live Ops aircraft-state claims for AC-1 with different POSITION_TIME_UTC values.",
        "NOT_ESTABLISHED",
        SafetyClassification.COMPARISON_NOT_AGREEMENT,
        "Accepting it would merge two observation times into one current position.",
        ("OBSERVATION_TIME_STRINGS_ARE_DIFFERENT",),
        "observation_time_comparison",
    ),
    _define(
        "same-hazard-different-source-versions",
        "A shared hazard id with different source versions is not one hazard version.",
        "Two Live Ops impact claims for HZ-1 with source versions SV-1 and SV-2.",
        "NOT_ESTABLISHED",
        SafetyClassification.COMPARISON_NOT_AGREEMENT,
        "Accepting it would treat two hazard source versions as the same hazard state.",
        ("SOURCE_VERSION_STRINGS_ARE_DIFFERENT",),
        "hazard_version_comparison",
    ),
    _define(
        "missing-sigmet-geometry",
        "A current hazard claim without SIGMET geometry is not a geometric intersection.",
        "Live Ops impact result compared with a finalized Historical exact-count result.",
        "UNSUPPORTED",
        SafetyClassification.COMPARISON_NOT_AGREEMENT,
        "Accepting it would report a SIGMET intersection without geometry.",
        ("SIGMET_GEOMETRY_NOT_PRESENT",),
        "missing_sigmet_comparison",
    ),
    _define(
        "california-text-is-not-authenticated-geography",
        "The user word California is not an operational boundary.",
        "Comparison request whose region_text_hint is California, over two public Live Ops results.",
        "NOT_ESTABLISHED",
        SafetyClassification.COMPARISON_NOT_AGREEMENT,
        "Accepting it would treat the word California as a verified geographic boundary.",
        (
            "CALIFORNIA_TEXT_IS_NOT_AN_OPERATIONAL_BOUNDARY",
            "REGION_TEXT_UNTRUSTED",
        ),
        "california_comparison",
    ),
    _define(
        "historical-count-is-not-live-ops-subset",
        "A Historical exact count is not a Live Ops confirmed subset.",
        "Finalized Historical exact count compared with a Live Ops SCOPED_COUNT in CONFIRMED_SUBSET mode.",
        "NOT_ESTABLISHED",
        SafetyClassification.COMPARISON_NOT_AGREEMENT,
        "Accepting it would report a historical population as the current operational subset.",
        ("EXACT_COUNT_IS_NOT_SUBSET_COUNT",),
        "historical_subset_comparison",
    ),
    _define(
        "missing-risk-is-not-low",
        "Recorded risk absence and a missing cross-invocation risk evaluation are not LOW.",
        "Decision run with a recorded risk-absence claim compared with a Live Ops result that has no risk evaluation.",
        "UNSUPPORTED",
        SafetyClassification.COMPARISON_NOT_AGREEMENT,
        "Accepting it would tell a pilot that missing or absent risk is a LOW risk level.",
        ("ABSENCE_AND_LOW_BELONG_TO_ONE_ARTIFACT",),
        "missing_risk_comparison",
    ),
    _define(
        "partial-count-is-not-network-conclusion",
        "Partial verification and ALL_VERIFIED are not a network-wide count.",
        "PARTIALLY_VERIFIED Live Ops result compared with an ALL_VERIFIED Live Ops result.",
        "NOT_ESTABLISHED",
        SafetyClassification.COMPARISON_NOT_AGREEMENT,
        "Accepting it would turn a partial or returned set into a network-wide conclusion.",
        (
            "PARTIAL_EVIDENCE_IS_NOT_NETWORK_COMPLETE",
            "ALL_VERIFIED_IS_NOT_NETWORK_COMPLETE",
        ),
        "partial_network_comparison",
    ),
    _define(
        "excessive-comparison-requests",
        "Comparison requests stop at the twelve ordered pairs of a four-step route.",
        "Valid two-step bundle carrying thirteen comparison request objects.",
        "excessive_comparison_requests",
        SafetyClassification.STRUCTURAL_REJECTION,
        "Accepting it would silently drop comparison requests past the contract budget.",
        builder="oversized_comparison_requests",
    ),
    _define(
        "duplicate-comparison-requests",
        "The same left invocation, right invocation, and category appears once.",
        "Two identical comparison requests between the planned Live Ops and Historical steps.",
        "duplicate_comparison_request",
        SafetyClassification.STRUCTURAL_REJECTION,
        "Accepting it would let one comparison be recorded as two independent checks.",
        builder="duplicate_comparison_inputs",
    ),
    _define(
        "offline-handoff-cannot-emit-a-factual-response",
        "The frozen response handoff carries a closed status and admits no operational claim.",
        "response_policy_for on an authorized Live Ops bundle, then MasterSafetyHandoff.",
        "NO_OPERATIONAL_CLAIM",
        SafetyClassification.RESPONSE_NONFACTUAL,
        "Accepting it would let the offline handoff answer with an aircraft, hazard, or count.",
        ("operational_claim_not_admitted",),
        "live_bundle",
    ),
    _define(
        "fixed-status-text-cannot-carry-an-operational-claim",
        "Status text is the closed sentence for the policy state.",
        "Response policy whose status_text is replaced with an operational sentence.",
        "invalid_status_text",
        SafetyClassification.RESPONSE_NONFACTUAL,
        "Accepting it would replace the fixed status with an operational claim.",
        builder="live_bundle",
    ),
    _define(
        "unavailable-count-is-not-zero",
        "Unavailable evidence does not become a zero count.",
        "UNAVAILABLE response policy, and an UNAVAILABLE Historical result with no verified-zero claim.",
        "Required evidence is unavailable. No count or zero is inferred.",
        SafetyClassification.RESPONSE_NONFACTUAL,
        "Accepting it would convert missing evidence into a verified count of zero.",
        ("VERIFIED_ZERO_NOT_INFERRED",),
        "unavailable_passed_input",
    ),
    _define(
        "consistent-ma4-result-is-not-authenticated-evidence",
        "Agreement with supplied evidence is not source authentication.",
        "Finalized exact-count run corroborated against that same supplied run.",
        "CONSISTENT_WITH_SUPPLIED_EVIDENCE",
        SafetyClassification.CONSISTENT_NOT_AUTHENTICATED,
        "Accepting it would treat a deterministic recheck of supplied objects as a source read.",
        ("SOURCE_AUTHENTICITY_NOT_ESTABLISHED", "SUPPLIED_EVIDENCE_ONLY"),
        "consistent_historical_input",
    ),
    _define(
        "unresolved-geography-is-not-a-confirmed-intersection",
        "A comparison without geometry is not a confirmed intersection.",
        "Live Ops impact result compared with a Live Ops position-time result under the missing-geometry category.",
        "UNSUPPORTED",
        SafetyClassification.COMPARISON_NOT_AGREEMENT,
        "Accepting it would report that an aircraft and a hazard intersect.",
        ("GEOGRAPHIC_GEOMETRY_ABSENT", "SIGMET_GEOMETRY_NOT_PRESENT"),
        "unresolved_geography_comparison",
    ),
    _define(
        "response-handoff-cannot-create-a-pilot-or-atc-instruction",
        "Advisory specialist output is not a pilot or ATC instruction.",
        "Decision run compared with a Live Ops result under the recommendation-versus-instruction category.",
        "UNSUPPORTED",
        SafetyClassification.RESPONSE_NONFACTUAL,
        "Accepting it would issue a clearance or a pilot instruction from the response handoff.",
        (
            "ADVISORY_OUTPUT_IS_NOT_PILOT_INSTRUCTION",
            "ADVISORY_OUTPUT_IS_NOT_ATC_INSTRUCTION",
        ),
        "pilot_instruction_comparison",
    ),
    _define(
        "route-does-not-grant-production-execution",
        "An authorized route keeps execution authority denied.",
        "authorize_route for one Live Ops step, then execution_preflight_for on that bundle.",
        "DENIED",
        SafetyClassification.EXECUTION_DENIED,
        "Accepting it would dispatch a specialist because the route was structurally authorized.",
        ("BLOCKED_BY_AUTHORIZATION",),
        "authorized_live",
    ),
    _define(
        "preflight-does-not-permit-dispatch",
        "A preflight report cannot set executable to true.",
        "execution_preflight_for on an authorized Live Ops bundle, then a report with executable true.",
        "execution_not_granted",
        SafetyClassification.EXECUTION_DENIED,
        "Accepting it would let a preflight report dispatch production work.",
        builder="live_bundle",
    ),
    _define(
        "provenance-requirement-cannot-be-marked-satisfied",
        "Every provenance requirement stays missing.",
        "Preflight report whose missing_provenance tuple drops one requirement.",
        "provenance_requirement_not_satisfied",
        SafetyClassification.EXECUTION_DENIED,
        "Accepting it would mark source, time, or population provenance satisfied without a proof.",
        builder="live_bundle",
    ),
    _define(
        "empty-plan-is-not-executable",
        "A plan with no steps is not executable.",
        "MasterRoutePlan with an empty step tuple inside a parallel bundle.",
        "NOT_EXECUTABLE",
        SafetyClassification.EXECUTION_DENIED,
        "Accepting it would execute a request that planned no specialist.",
        builder="empty_plan_bundle",
    ),
    _define(
        "contract-ready-remains-non-executable",
        "CONTRACT_READY_FOR_FUTURE_INTEGRATION does not execute and does not satisfy provenance.",
        "Named preflight report in that state, with the full missing-provenance tuple, on a real bundle.",
        "CONTRACT_READY_FOR_FUTURE_INTEGRATION",
        SafetyClassification.EXECUTION_DENIED,
        "Accepting it would treat a future-integration label as permission to dispatch.",
        builder="live_bundle",
    ),
    _define(
        "synthetic-ma3-result-is-not-authentic-evidence",
        "A synthetic MA3 worker result cannot be stored as specialist evidence.",
        "SyntheticWorkerResult placed on the Historical authorization of an authorized plan.",
        "synthetic_result_not_evidence",
        SafetyClassification.SYNTHETIC_NOT_EVIDENCE,
        "Accepting it would turn an MA3 synthetic execution record into authentic specialist evidence.",
        ("REJECTED", "UNSUPPORTED_ARTIFACT"),
        "authorized_historical",
    ),
)


def case_by_id(scenario_id: str) -> MasterSafetyCase:
    found = [item for item in SAFETY_CASES if item.scenario_id == scenario_id]
    if len(found) != 1:
        raise KeyError(scenario_id)
    return found[0]


DECISION_AS_OF = "2026-09-29T00:00:00Z"
OFFLINE_REQUEST_ID = "req-offline-other"


def historical_authorization(**overrides: object) -> MasterInvocationAuthorization:
    values: dict[str, object] = {
        "master_request_id": "master-req-offline-historical",
        "invocation_id": "inv-offline-historical",
        "specialist": MasterSpecialist.HISTORICAL_ANALYTICS,
        "purpose": MasterPurpose.ANSWER,
        "requirement": MasterRequirement.REQUIRED,
        "specialist_request_id": "specialist-req-offline-historical",
        "correlation_id": "corr-offline-historical",
        "application_request_time_utc": "2026-09-13T12:00:01Z",
        "requested_evaluation_time_utc": HISTORICAL_AS_OF,
    }
    values.update(overrides)
    return MasterInvocationAuthorization(**values)  # type: ignore[arg-type]


def decision_authorization(**overrides: object) -> MasterInvocationAuthorization:
    values: dict[str, object] = {
        "master_request_id": "master-req-offline-decision",
        "invocation_id": "inv-offline-decision",
        "specialist": MasterSpecialist.DECISION,
        "purpose": MasterPurpose.ANSWER,
        "requirement": MasterRequirement.REQUIRED,
        "specialist_request_id": "specialist-req-offline-decision",
        "correlation_id": "corr-offline-decision",
        "application_request_time_utc": "2026-09-29T00:00:01Z",
        "requested_evaluation_time_utc": DECISION_AS_OF,
        "expected_aircraft_id": "ac-1",
    }
    values.update(overrides)
    return MasterInvocationAuthorization(**values)  # type: ignore[arg-type]


def live_ops_authorization(**overrides: object) -> MasterInvocationAuthorization:
    values: dict[str, object] = {
        "master_request_id": "master-req-offline-live",
        "invocation_id": "inv-offline-live",
        "specialist": MasterSpecialist.LIVE_OPS,
        "purpose": MasterPurpose.ANSWER,
        "requirement": MasterRequirement.REQUIRED,
        "specialist_request_id": SYNTHETIC_REQUEST_ID,
        "correlation_id": SYNTHETIC_CORRELATION_ID,
        "application_request_time_utc": "2025-10-15T00:00:01Z",
        "requested_evaluation_time_utc": SYNTHETIC_UTC,
    }
    values.update(overrides)
    return MasterInvocationAuthorization(**values)  # type: ignore[arg-type]


def historical_exact_count_run() -> HistoricalSpecialistRunResult:
    return _run(tool_results=(_succeeded_encounters(),), claims=(_exact(), _window()))


def historical_forged_count_run() -> HistoricalSpecialistRunResult:
    return _run(
        tool_results=(_succeeded_encounters(),),
        claims=(_exact(value=99), _window()),
    )


def historical_truncated_lower_bound_run() -> HistoricalSpecialistRunResult:
    return _run(
        tool_results=(_truncated_list(),),
        claims=(
            LowerBoundCountClaim(
                tool_call_id=CALL_A,
                metric_id=LOWER_BOUND_METRIC_ID,
                minimum_value=6,
            ),
        ),
    )


def historical_verified_zero_run() -> HistoricalSpecialistRunResult:
    return _run(tool_results=(_verified_zero(),), claims=(VerifiedZeroClaim(CALL_A),))


def historical_unverified_passed_result() -> SpecialistResult:
    return SpecialistResult(
        status=SpecialistStatus.ANSWERED,
        answer="OFFLINE_TEST_FIXTURE historical answer with no tool evidence.",
        temporal_scope=TemporalScope.HISTORICAL,
        evaluated_as_of_utc=HISTORICAL_AS_OF,
        tool_results=(),
        verified_claims=(VerifiedZeroClaim(CALL_A),),
        limitations=(),
        unsupported_reason=None,
        verifier_outcome=VerifierOutcome.PASSED,
    )


def live_ops_request():
    return sample_request()


def live_ops_context():
    return trusted_context()


def live_ops_all_verified_result():
    return completed_all_verified()


def live_ops_partially_verified_result():
    return partially_verified()


def live_ops_none_verified_result():
    return none_verified()


def live_ops_request_mismatch_result():
    return completed_all_verified(request_id=OFFLINE_REQUEST_ID)


def _decision_packet(tool_call_id: str) -> Evidence:
    return Evidence(
        source="wilvor.decision.verifier",
        source_records=(
            SourceRecord(record_id="ac-1", source_version=None, event_timestamp_utc=None),
        ),
        query_timestamp_utc=DECISION_AS_OF,
        freshness_status=FreshnessStatus.UNKNOWN,
        confidence=ConfidenceLevel.UNKNOWN,
        limitations=("FRESHNESS_NOT_ESTABLISHED",),
        tool_call_id=tool_call_id,
        temporal_scope=TemporalScope.CURRENT,
    )


def _decision_tool(payload: DecisionEvidence, tool_name: str, tool_call_id: str) -> ToolResult:
    status = expected_decision_status(payload)
    limitations: tuple[str, ...] = ()
    if status in {
        ToolResultStatus.PARTIAL,
        ToolResultStatus.UNAVAILABLE,
        ToolResultStatus.UNKNOWN,
        ToolResultStatus.STALE,
    }:
        limitations = ("Stored limitation.",)
    return ToolResult(
        tool_name=tool_name,
        tool_call_id=tool_call_id,
        status=status,
        temporal_scope=TemporalScope.CURRENT,
        data=payload.to_dict(),
        evidence=(_decision_packet(tool_call_id),),
        as_of_utc=DECISION_AS_OF,
        limitations=limitations,
        correlation_id=None,
    )


def _decision_context(aircraft_id: str) -> DecisionEvidence:
    encounter = DecisionEncounterLink(
        encounter_id="enc-1",
        projection_id="proj-1",
        aircraft_state_version="state-1",
        hazard=HazardSourceVersionLink(
            hazard_id="haz-1",
            state=DecisionReportedLinkState.PRESENT,
            persisted_source_version="sv-1",
            current_source_version="sv-1",
        ),
        risk=DecisionRiskEvidence(
            presence=RiskPresence.PRESENT,
            risk_id="risk-1",
            encounter_id="enc-1",
            risk_level=StoredRiskLevel.HIGH,
            risk_score=80,
        ),
        recommendations=DecisionRecommendationSet(
            current=(
                DecisionRecommendationEvidence(
                    recommendation_id="rec-1",
                    primary_action_type=RecommendationActionType.MONITOR,
                    advisory_authority=DecisionAdvisoryAuthority.ADVISORY_ONLY,
                ),
            ),
            absence_state=DecisionReportedLinkState.PRESENT,
        ),
    )
    return DecisionEvidence(
        kind=DecisionEvidenceKind.DECISION_CONTEXT,
        evaluation_state=DecisionEvaluationState.ESTABLISHED,
        aircraft_in_current_set=True,
        aircraft_id=aircraft_id,
        projection_state=DecisionReportedLinkState.PRESENT,
        projection_id="proj-1",
        aircraft_state_version="state-1",
        encounters=(encounter,),
        capability=DecisionRouteCapability(validated_alternative_available=False),
        chain_gaps=(),
        limitation_codes=(),
    )


def decision_offline_consistent_run(
    *,
    aircraft_id: str = "ac-1",
    tool_call_id: str = "call-offline-run",
) -> DecisionSpecialistRunResult:
    payload = _decision_context(aircraft_id)
    raw = _decision_tool(payload, CONTEXT_TOOL, tool_call_id)
    claims = (AircraftIdentityClaim("de-1", TemporalScope.CURRENT, aircraft_id),)
    bindings = (DecisionEvidenceBinding("de-1", raw),)
    verification = verify_decision_evidence(claims, bindings)
    rendered = render_decision_verification(verification)
    return DecisionSpecialistRunResult(
        status=DecisionSpecialistRunStatus.COMPLETED,
        mode=DecisionTargetMode.AIRCRAFT,
        aircraft_id=aircraft_id,
        recommendation_id=None,
        tool_results=(raw,),
        evidence_snapshots=(
            DecisionEvidenceSnapshot("de-1", project_decision_tool_result(raw)),
        ),
        evidence_bindings=bindings,
        invocations=(_decision_audit(raw, "de-1", aircraft_id),),
        proposed_claims=claims,
        verification=verification,
        render=rendered,
        unsupported_reason=None,
        runtime_errors=(),
        collection_partial_reason=None,
        validation_feedback=(),
        provider_turn_count=1,
        executed_tool_call_count=1,
        executed_persisted_call_count=0,
        terminal_kind=ModelDecisionKind.FINAL_CLAIMS,
    )


def decision_absent_risk_case():
    payload = DecisionEvidence(
        kind=DecisionEvidenceKind.RISK_EVIDENCE,
        evaluation_state=DecisionEvaluationState.ESTABLISHED,
        aircraft_in_current_set=True,
        aircraft_id="ac-1",
        projection_state=DecisionReportedLinkState.PRESENT,
        projection_id="proj-1",
        aircraft_state_version="state-1",
        encounters=(),
        risk=DecisionRiskEvidence(presence=RiskPresence.ABSENT, encounter_id="enc-1"),
        capability=None,
        chain_gaps=(DecisionChainGap.RISK_ABSENT,),
        limitation_codes=(),
    )
    raw = _decision_tool(payload, RISK_TOOL, "call-offline-absent")
    return (RiskAbsentClaim("de-1", TemporalScope.CURRENT, "enc-1"),), (
        DecisionEvidenceBinding("de-1", raw),
    ), payload


def planning_context(**overrides: object) -> MasterPlanningContext:
    values: dict[str, object] = {
        "master_request_id": MASTER_REQUEST,
        "correlation_id": CORRELATION,
        "application_request_time_utc": APP_TIME,
        "canonical_user_text": USER_TEXT,
    }
    values.update(overrides)
    return MasterPlanningContext(**values)  # type: ignore[arg-type]


def live_step(step_id: str = "live-1") -> MasterProposedStep:
    return MasterProposedStep(
        step_id=step_id,
        specialist=MasterSpecialist.LIVE_OPS,
        purpose=MasterPurpose.ANSWER,
        requirement=MasterRequirement.REQUIRED,
        capability=LIVE_CAPABILITY,
        temporal_intent=MasterRouteTemporalIntent.CURRENT,
    )


def history_step(step_id: str = "hist-1") -> MasterProposedStep:
    return MasterProposedStep(
        step_id=step_id,
        specialist=MasterSpecialist.HISTORICAL_ANALYTICS,
        purpose=MasterPurpose.ANSWER,
        requirement=MasterRequirement.REQUIRED,
        capability=HISTORY_CAPABILITY,
        temporal_intent=MasterRouteTemporalIntent.HISTORICAL,
        historical_start_utc="2026-01-01T00:00:00Z",
        historical_end_utc="2026-01-02T00:00:00Z",
    )


def decision_step(step_id: str = "dec-1") -> MasterProposedStep:
    return MasterProposedStep(
        step_id=step_id,
        specialist=MasterSpecialist.DECISION,
        purpose=MasterPurpose.ANSWER,
        requirement=MasterRequirement.REQUIRED,
        capability=RISK_CAPABILITY,
        temporal_intent=MasterRouteTemporalIntent.CURRENT,
    )


def authorized(*steps: MasterProposedStep):
    context = planning_context()
    capabilities = tuple(
        dict.fromkeys(
            MasterProposedCapability(step.capability, MasterRequirement.REQUIRED) for step in steps
        )
    )
    decision = authorize_route(
        MasterRouteProposal(user_text=USER_TEXT, capabilities=capabilities, steps=steps),
        context,
    )
    if decision.plan is None:
        raise RuntimeError("MA7 route fixture did not produce a plan")
    return context, decision.plan, decision


def live_sources(result: LiveOpsRunResult | None = None) -> LiveOpsAdmissionSources:
    return LiveOpsAdmissionSources(
        live_ops_request(),
        live_ops_context(),
        live_ops_all_verified_result() if result is None else result,
    )


def public_live_result(*claims: LiveOpsPublicClaim) -> LiveOpsRunResult:
    names: dict[str, str] = {}
    for claim in claims:
        if not isinstance(claim.tool_call_id, str) or not isinstance(claim.tool_name, str):
            raise RuntimeError("MA7 live claim is missing its tool identity")
        names.setdefault(claim.tool_call_id, claim.tool_name)
    references = tuple(
        evidence(tool_call_id, tool_name, "FRESHNESS_NOT_ESTABLISHED")
        for tool_call_id, tool_name in names.items()
    )
    return replace(
        completed_all_verified(),
        verified_claims=claims,
        evidence_references=references,
    )


def _finalized(run: HistoricalSpecialistRunResult) -> SpecialistResult:
    return finalize_historical_specialist_run(run)


def exact_artifact() -> SpecialistResult:
    return _finalized(historical_exact_count_run())


def lower_bound_artifact() -> SpecialistResult:
    return _finalized(historical_truncated_lower_bound_run())


def verified_zero_artifact() -> SpecialistResult:
    return _finalized(historical_verified_zero_run())


def historical_admission(artifact: SpecialistResult, invocation_id: str) -> MasterEvidenceAdmissionInput:
    return MasterEvidenceAdmissionInput(
        historical_authorization(
            master_request_id=MASTER_REQUEST,
            invocation_id=invocation_id,
            specialist_request_id=f"specialist-{invocation_id}",
            correlation_id="corr-ma7-historical",
            application_request_time_utc=APP_TIME,
            requested_evaluation_time_utc=HISTORICAL_AS_OF,
        ),
        artifact,
    )


def live_admission(result: LiveOpsRunResult, invocation_id: str) -> MasterEvidenceAdmissionInput:
    return MasterEvidenceAdmissionInput(
        live_ops_authorization(
            master_request_id=MASTER_REQUEST,
            invocation_id=invocation_id,
            application_request_time_utc=APP_TIME,
        ),
        live_sources(result),
    )


def decision_admission(
    run: DecisionSpecialistRunResult,
    invocation_id: str,
) -> MasterEvidenceAdmissionInput:
    observed = None if run.verification is None else run.verification.current_as_of_utc
    return MasterEvidenceAdmissionInput(
        decision_authorization(
            master_request_id=MASTER_REQUEST,
            invocation_id=invocation_id,
            specialist_request_id=f"specialist-{invocation_id}",
            correlation_id="corr-ma7-decision",
            application_request_time_utc=APP_TIME,
            requested_evaluation_time_utc=observed,
            expected_aircraft_id=run.aircraft_id,
        ),
        run,
    )


def comparison_request(
    left: MasterEvidenceAdmissionInput,
    right: MasterEvidenceAdmissionInput,
    category: MasterComparisonCategory,
    **hints: str,
) -> MasterComparisonRequest:
    return MasterComparisonRequest(
        master_request_id=MASTER_REQUEST,
        left_invocation_id=left.authorization.invocation_id,
        right_invocation_id=right.authorization.invocation_id,
        category=category,
        **hints,
    )


def _decision_audit(raw, ref: str, aircraft_id: str) -> DecisionToolInvocationAudit:
    arguments = {"aircraft_id": aircraft_id}
    return DecisionToolInvocationAudit(
        tool_name=raw.tool_name,
        tool_call_id=raw.tool_call_id,
        evidence_ref=ref,
        requested_arguments=arguments,
        executed_arguments=arguments,
        canonical_key=f"{raw.tool_name}:{raw.tool_call_id}",
        dispatch_origin=DecisionDispatchOrigin.MODEL_REQUEST,
    )


def recorded_absence_run() -> DecisionSpecialistRunResult:
    """Decision run whose supplied evidence records risk absence."""

    claims, bindings, _payload = decision_absent_risk_case()
    verification = verify_decision_evidence(claims, bindings)
    rendered = render_decision_verification(verification)
    status = (
        DecisionSpecialistRunStatus.COMPLETED
        if verification.outcome is VerifierOutcome.PASSED
        else DecisionSpecialistRunStatus.PARTIAL
    )
    return DecisionSpecialistRunResult(
        status=status,
        mode=DecisionTargetMode.AIRCRAFT,
        aircraft_id="ac-1",
        recommendation_id=None,
        tool_results=tuple(item.raw_tool_result for item in bindings),
        evidence_snapshots=tuple(
            DecisionEvidenceSnapshot(
                item.evidence_ref,
                project_decision_tool_result(item.raw_tool_result),
            )
            for item in bindings
        ),
        evidence_bindings=bindings,
        invocations=tuple(
            _decision_audit(item.raw_tool_result, item.evidence_ref, "ac-1") for item in bindings
        ),
        proposed_claims=claims,
        verification=verification,
        render=rendered,
        unsupported_reason=None,
        runtime_errors=(),
        collection_partial_reason=None,
        validation_feedback=(),
        provider_turn_count=1,
        executed_tool_call_count=len(bindings),
        executed_persisted_call_count=0,
        terminal_kind=ModelDecisionKind.FINAL_CLAIMS,
    )


def matching_evidence(plan: MasterRoutePlan) -> tuple[MasterEvidenceCorroborationInput, ...]:
    items: list[MasterEvidenceCorroborationInput] = []
    for step in plan.steps:
        authorization = step.invocation
        if authorization.specialist is MasterSpecialist.HISTORICAL_ANALYTICS:
            run = historical_exact_count_run()
            items.append(
                MasterEvidenceCorroborationInput(
                    MasterEvidenceAdmissionInput(authorization, _finalized(run)),
                    historical_run=run,
                )
            )
        elif authorization.specialist is MasterSpecialist.LIVE_OPS:
            items.append(
                MasterEvidenceCorroborationInput(
                    MasterEvidenceAdmissionInput(authorization, live_sources())
                )
            )
        else:
            items.append(
                MasterEvidenceCorroborationInput(
                    MasterEvidenceAdmissionInput(
                        authorization,
                        decision_offline_consistent_run(),
                    )
                )
            )
    return tuple(items)


def bundle_from(*steps: MasterProposedStep) -> MasterParallelContractBundle:
    context, plan, _decision = authorized(*steps)
    return MasterParallelContractBundle(context, plan, matching_evidence(plan))


def live_bundle() -> MasterParallelContractBundle:
    return bundle_from(live_step())


def live_and_historical_bundle() -> MasterParallelContractBundle:
    return bundle_from(live_step(), history_step())


def authorized_live():
    return authorized(live_step())


def authorized_historical():
    return authorized(history_step())


def another_master_request_inputs():
    context, plan, _decision = authorized(live_step())
    item = matching_evidence(plan)[0]
    foreign = replace(item.admission_input.authorization, master_request_id=OTHER_REQUEST)
    evidence_item = replace(
        item,
        admission_input=replace(item.admission_input, authorization=foreign),
    )
    return context, plan, evidence_item


def unplanned_result_inputs():
    bundle = live_bundle()
    item = bundle.evidence_inputs[0]
    foreign = replace(item.admission_input.authorization, invocation_id="inv-ma7-unplanned")
    evidence_item = replace(
        item,
        admission_input=replace(item.admission_input, authorization=foreign),
    )
    return bundle, evidence_item


def wrong_specialist_inputs():
    context, plan, _decision = authorized(history_step())
    authorization: MasterInvocationAuthorization = plan.steps[0].invocation
    admission = MasterEvidenceAdmissionInput(authorization, live_sources())
    return context, plan, MasterEvidenceCorroborationInput(admission), admission


def _shifted_plan(field_name: str, value: str):
    context, plan, _decision = authorized(live_step())
    step = plan.steps[0]
    authorization = replace(step.invocation, **{field_name: value})
    shifted = replace(plan, steps=(replace(step, invocation=authorization),))
    return context, shifted, matching_evidence(shifted)


def correlation_mismatch_inputs():
    return _shifted_plan("correlation_id", OTHER_CORRELATION)


def application_time_mismatch_inputs():
    return _shifted_plan("application_request_time_utc", OTHER_APP_TIME)


def two_decision_plan_steps():
    context, plan, _decision = authorized(decision_step())
    first = plan.steps[0]
    second_authorization = replace(
        first.invocation,
        invocation_id="inv-ma7-decision-extra",
        specialist_request_id="specialist-ma7-decision-extra",
    )
    second = replace(first, step_id="dec-ma7-2", invocation=second_authorization)
    return context, (first, second)


def five_plan_steps():
    context, plan, _decision = authorized(live_step())
    base = plan.steps[0]
    steps = []
    for index in range(MAX_ROUTE_STEPS + 1):
        authorization = replace(
            base.invocation,
            invocation_id=f"inv-ma7-extra-{index}",
            specialist_request_id=f"specialist-ma7-extra-{index}",
        )
        steps.append(replace(base, step_id=f"step-ma7-{index}", invocation=authorization))
    return context, tuple(steps)


def five_live_steps() -> tuple[MasterProposedStep, ...]:
    return tuple(live_step(f"live-ma7-{index}") for index in range(MAX_ROUTE_STEPS + 1))


def unavailable_passed_input() -> tuple[MasterEvidenceCorroborationInput, SpecialistResult]:
    artifact = replace(
        historical_unverified_passed_result(),
        status=SpecialistStatus.UNAVAILABLE,
        verified_claims=(),
        answer="Required historical evidence is unavailable.",
    )
    item = MasterEvidenceCorroborationInput(historical_admission(artifact, HIST_A))
    return item, artifact


def forged_answer_input() -> MasterEvidenceCorroborationInput:
    run = historical_forged_count_run()
    forged = replace(
        _finalized(run),
        answer="OFFLINE_TEST_FIXTURE forged historical count is 99.",
    )
    return MasterEvidenceCorroborationInput(
        historical_admission(forged, HIST_A),
        historical_run=run,
    )


def missing_historical_run_input() -> MasterEvidenceCorroborationInput:
    return MasterEvidenceCorroborationInput(historical_admission(exact_artifact(), HIST_A))


def consistent_historical_input() -> MasterEvidenceCorroborationInput:
    run = historical_exact_count_run()
    return MasterEvidenceCorroborationInput(
        historical_admission(_finalized(run), HIST_A),
        historical_run=run,
    )


def inconsistent_claim_input() -> tuple[MasterEvidenceCorroborationInput, SpecialistResult]:
    run = historical_exact_count_run()
    finalized = _finalized(run)
    stripped = replace(finalized, verified_claims=())
    return (
        MasterEvidenceCorroborationInput(
            historical_admission(stripped, HIST_A),
            historical_run=run,
        ),
        finalized,
    )


def live_public_input() -> MasterEvidenceCorroborationInput:
    return MasterEvidenceCorroborationInput(live_admission(live_ops_all_verified_result(), LIVE_A))


def rejected_live_input() -> MasterEvidenceAdmissionInput:
    return MasterEvidenceAdmissionInput(
        live_ops_authorization(invocation_id="inv-ma7-rejected-live"),
        LiveOpsAdmissionSources(
            live_ops_request(),
            live_ops_context(),
            live_ops_request_mismatch_result(),
        ),
    )


def unresolved_historical_input() -> MasterEvidenceAdmissionInput:
    return historical_admission(exact_artifact(), HIST_A)


def position_result(observed_at: str) -> LiveOpsRunResult:
    return public_live_result(
        LiveOpsPublicClaim(
            kind=LiveOpsPublicClaimKind.AIRCRAFT_OBSERVED_STATE,
            tool_call_id="call-aircraft-1",
            tool_name="get_aircraft_operational_context",
            aircraft_id=AIRCRAFT_LABEL,
            observed_field=LiveOpsPublicObservedField.POSITION_TIME_UTC,
            observed_value=observed_at,
            retained_not_current=False,
        )
    )


def hazard_version_result(version: str) -> LiveOpsRunResult:
    return public_live_result(
        LiveOpsPublicClaim(
            kind=LiveOpsPublicClaimKind.CONFIRMED_IMPACT,
            tool_call_id="call-impacts-1",
            tool_name="search_current_impacts",
            aircraft_id=AIRCRAFT_LABEL,
            hazard_id="HZ-1",
            source_version=version,
            encounter_id="EN-1",
        )
    )


def subset_result() -> LiveOpsRunResult:
    return public_live_result(
        LiveOpsPublicClaim(
            kind=LiveOpsPublicClaimKind.SCOPED_COUNT,
            tool_call_id="call-impacts-1",
            tool_name="search_current_impacts",
            metric=LiveOpsPublicCountMetric.IMPACT_RECORD_COUNT,
            count_mode=LiveOpsPublicCountMode.CONFIRMED_SUBSET,
            count_value=1,
            scope=LiveOpsPublicScope(aircraft_id=AIRCRAFT_LABEL),
        )
    )


def lower_bound_comparison():
    left = historical_admission(exact_artifact(), HIST_A)
    right = historical_admission(lower_bound_artifact(), HIST_B)
    return (
        comparison_request(left, right, MasterComparisonCategory.EXACT_COUNT_VS_LOWER_BOUND),
        left,
        right,
    )


def verified_zero_comparison():
    left = historical_admission(verified_zero_artifact(), HIST_A)
    right = live_admission(live_ops_none_verified_result(), LIVE_A)
    return (
        comparison_request(
            left,
            right,
            MasterComparisonCategory.HISTORICAL_VERIFIED_ZERO_VS_LIVE_OPS_UNKNOWN,
        ),
        left,
        right,
    )


def same_aircraft_comparison():
    left = live_admission(live_ops_all_verified_result(), LIVE_A)
    right = decision_admission(
        decision_offline_consistent_run(aircraft_id=AIRCRAFT_LABEL),
        DECISION_ID,
    )
    return (
        comparison_request(
            left,
            right,
            MasterComparisonCategory.LIVE_OPS_AIRCRAFT_VS_DECISION_AIRCRAFT,
        ),
        left,
        right,
    )


def observation_time_comparison():
    left = live_admission(position_result(EARLY_POSITION_TIME), LIVE_A)
    right = live_admission(position_result(LATER_POSITION_TIME), LIVE_B)
    return (
        comparison_request(
            left,
            right,
            MasterComparisonCategory.SAME_AIRCRAFT_DIFFERENT_OBSERVATION_TIMES,
        ),
        left,
        right,
    )


def hazard_version_comparison():
    left = live_admission(hazard_version_result("SV-1"), LIVE_A)
    right = live_admission(hazard_version_result("SV-2"), LIVE_B)
    return (
        comparison_request(
            left,
            right,
            MasterComparisonCategory.SAME_HAZARD_DIFFERENT_SOURCE_VERSIONS,
        ),
        left,
        right,
    )


def missing_sigmet_comparison():
    left = live_admission(live_ops_all_verified_result(), LIVE_A)
    right = historical_admission(exact_artifact(), HIST_A)
    return (
        comparison_request(
            left,
            right,
            MasterComparisonCategory.CURRENT_SIGMET_VS_HISTORICAL_HAZARD_VERSIONS,
        ),
        left,
        right,
    )


def california_comparison():
    left = live_admission(live_ops_all_verified_result(), LIVE_A)
    right = live_admission(live_ops_all_verified_result(), LIVE_B)
    return (
        comparison_request(
            left,
            right,
            MasterComparisonCategory.CALIFORNIA_USER_REGION_TEXT,
            region_text_hint="California",
        ),
        left,
        right,
    )


def historical_subset_comparison():
    left = historical_admission(exact_artifact(), HIST_A)
    right = live_admission(subset_result(), LIVE_A)
    return (
        comparison_request(
            left,
            right,
            MasterComparisonCategory.HISTORICAL_EXACT_VS_LIVE_OPS_SUBSET,
        ),
        left,
        right,
    )


def missing_risk_comparison():
    run = recorded_absence_run()
    left = decision_admission(run, DECISION_ID)
    right = live_admission(live_ops_all_verified_result(), LIVE_A)
    corroboration = MasterEvidenceCorroborationInput(decision_admission(run, DECISION_ID))
    return (
        comparison_request(left, right, MasterComparisonCategory.DECISION_LOW_VS_MISSING_RISK),
        left,
        right,
        corroboration,
    )


def partial_network_comparison():
    left = live_admission(live_ops_partially_verified_result(), LIVE_A)
    right = live_admission(live_ops_all_verified_result(), LIVE_B)
    return (
        comparison_request(
            left,
            right,
            MasterComparisonCategory.PARTIAL_SET_VS_NETWORK_CONCLUSION,
        ),
        left,
        right,
    )


def unresolved_geography_comparison():
    left = live_admission(live_ops_all_verified_result(), LIVE_A)
    right = live_admission(position_result(EARLY_POSITION_TIME), LIVE_B)
    return (
        comparison_request(
            left,
            right,
            MasterComparisonCategory.MISSING_GEOMETRY_VS_CLAIMED_INTERSECTION,
        ),
        left,
        right,
    )


def pilot_instruction_comparison():
    left = decision_admission(
        decision_offline_consistent_run(aircraft_id=AIRCRAFT_LABEL),
        DECISION_ID,
    )
    right = live_admission(live_ops_all_verified_result(), LIVE_A)
    return (
        comparison_request(
            left,
            right,
            MasterComparisonCategory.RECOMMENDATION_RECORD_VS_PILOT_INSTRUCTION,
        ),
        left,
        right,
    )


def oversized_comparison_requests():
    bundle = live_and_historical_bundle()
    requests = tuple(
        MasterComparisonRequest(
            master_request_id=bundle.context.master_request_id,
            left_invocation_id=f"inv-ma7-left-{index}",
            right_invocation_id=f"inv-ma7-right-{index}",
            category=MasterComparisonCategory.QUERY_TIME_VS_OBSERVATION_TIME,
        )
        for index in range(MAX_COMPARISON_REQUESTS + 1)
    )
    return bundle, requests


def duplicate_comparison_inputs():
    bundle = live_and_historical_bundle()
    request = MasterComparisonRequest(
        master_request_id=bundle.context.master_request_id,
        left_invocation_id=bundle.plan.steps[0].invocation.invocation_id,
        right_invocation_id=bundle.plan.steps[1].invocation.invocation_id,
        category=MasterComparisonCategory.QUERY_TIME_VS_OBSERVATION_TIME,
    )
    return bundle, (request, request)


def empty_plan_bundle() -> MasterParallelContractBundle:
    context = planning_context()
    plan = MasterRoutePlan(
        master_request_id=context.master_request_id,
        canonical_user_text=context.canonical_user_text,
        steps=(),
    )
    return MasterParallelContractBundle(context, plan, ())
