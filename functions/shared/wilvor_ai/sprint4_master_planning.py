"""Sprint 4 Agent D: model-backed request understanding and MA2 planning.

An injected planner may propose a ``MasterRouteProposal``. That proposal is
untrusted. ``authorize_route`` remains the only planner-to-plan function.
Execution authority stays ``DENIED``. Request coverage stays
``NOT_ESTABLISHED``. Model-supplied selectors stay untrusted hints.

This module does not import LangGraph, construct AWS clients, or call a
live model API. TOOL_CALLS from a Master planner are rejected, including
every AWS data-plane name.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum

from wilvor_ai.contracts import ContractValidationError
from wilvor_ai.master_evidence_admission import MasterSourceAuthenticity
from wilvor_ai.master_routing import (
    MASTER_ROUTE_PROPOSAL_SCHEMA_VERSION,
    REQUEST_UNDERSTANDING_LIMITATION,
    MAX_DECISION_STEPS,
    MAX_ROUTE_STEPS,
    MasterHintTrust,
    MasterPlanningContext,
    MasterRequestCoverage,
    MasterRouteDecision,
    MasterRouteExecutionAuthority,
    MasterRouteHints,
    MasterRouteOutcome,
    MasterRouteProposal,
    authorize_route,
)
from wilvor_ai.model_contracts import ModelDecision, ModelDecisionKind
from wilvor_ai.providers.master_instructions import MASTER_PLANNING_INSTRUCTION_REF
from wilvor_ai.sprint4_master_providers import (
    SPRINT4_LIVE_MODEL_INVOCATION_AVAILABLE,
    bind_master_planning_provider,
    inspect_untrusted_tool_calls,
    reject_master_planner_tools,
    reject_vendor_sdk_provider,
)


SPRINT4_MASTER_PLANNING_SCHEMA_VERSION = "wilvor.ai.sprint4.master_planning.v1"
SPRINT4_PLANNING_EXECUTION_AUTHORITY_AVAILABLE = False
MODEL_PROPOSAL_IS_AUTHORIZATION = False
HINT_TRUST = MasterHintTrust.UNTRUSTED


class Sprint4PlanningStatus(str, Enum):
    """Planning outcome. AUTHORIZED_PLAN is still not production execution."""

    AUTHORIZED_PLAN = "AUTHORIZED_PLAN"
    CLARIFICATION_REQUIRED = "CLARIFICATION_REQUIRED"
    UNSUPPORTED = "UNSUPPORTED"
    REFUSED = "REFUSED"
    PLANNER_REFUSED = "PLANNER_REFUSED"
    PLANNER_UNSUPPORTED = "PLANNER_UNSUPPORTED"
    INVALID_PROPOSAL = "INVALID_PROPOSAL"


def _fail(code: str) -> None:
    raise ContractValidationError(code)


def _closed(value: object, denied: object, code: str) -> None:
    if value is not denied:
        _fail(code)


def _assert_closed_surface() -> None:
    _closed(SPRINT4_PLANNING_EXECUTION_AUTHORITY_AVAILABLE, False, "execution_authority_not_granted")
    _closed(MODEL_PROPOSAL_IS_AUTHORIZATION, False, "proposal_is_not_authorization")
    _closed(SPRINT4_LIVE_MODEL_INVOCATION_AVAILABLE, False, "live_model_invocation_unavailable")
    if HINT_TRUST is not MasterHintTrust.UNTRUSTED:
        _fail("model_hints_not_trusted")


def _as_proposal(value: object, context: MasterPlanningContext) -> MasterRouteProposal:
    if isinstance(value, MasterRouteProposal):
        proposal = value
    elif isinstance(value, Mapping):
        payload = dict(value)
        payload.setdefault("schema_version", MASTER_ROUTE_PROPOSAL_SCHEMA_VERSION)
        payload.setdefault("user_text", context.canonical_user_text)
        proposal = MasterRouteProposal.from_dict(payload)
    else:
        _fail("invalid_master_route_proposal")
    if proposal.user_text != context.canonical_user_text:
        _fail("user_text_mismatch")
    if len(proposal.steps) > MAX_ROUTE_STEPS:
        _fail("excessive_plan_size")
    if sum(item.specialist.value == "DECISION" for item in proposal.steps) > MAX_DECISION_STEPS:
        _fail("decision_fanout_not_permitted")
    return proposal


_TERMINAL_CODE = re.compile(r"[A-Za-z0-9_]{1,64}")
_TERMINAL_REASON = re.compile(r"(?:UNSUPPORTED|REFUSAL)(?::[A-Za-z0-9_]{1,64})?")


def sanitized_terminal_reason(raw: object) -> str | None:
    """Closed kind and code for a planner terminal decision.

    Free-form refusal prose is dropped. The code is not an authorization.
    """

    decision = raw
    if isinstance(raw, Mapping):
        try:
            decision = ModelDecision.from_dict(raw)
        except ContractValidationError:
            return None
    if not isinstance(decision, ModelDecision):
        return None
    if decision.kind is ModelDecisionKind.UNSUPPORTED and decision.unsupported_reason is not None:
        return "UNSUPPORTED:%s" % decision.unsupported_reason.value
    if decision.kind is ModelDecisionKind.REFUSAL:
        code = decision.refusal_code
        if isinstance(code, str) and _TERMINAL_CODE.fullmatch(code):
            return "REFUSAL:%s" % code
        return "REFUSAL"
    return None


def _reject_model_decision_as_proposal(decision: ModelDecision) -> Sprint4PlanningStatus:
    inspect_untrusted_tool_calls(decision)
    if decision.kind is ModelDecisionKind.TOOL_CALLS:
        reject_master_planner_tools(decision)
    if decision.kind is ModelDecisionKind.FINAL_CLAIMS:
        _fail("master_planner_claims_rejected")
    if decision.kind is ModelDecisionKind.UNSUPPORTED:
        return Sprint4PlanningStatus.PLANNER_UNSUPPORTED
    if decision.kind is ModelDecisionKind.REFUSAL:
        return Sprint4PlanningStatus.PLANNER_REFUSED
    _fail("invalid_master_route_proposal")
    raise AssertionError("unreachable")


def collect_untrusted_hints(proposal: MasterRouteProposal) -> tuple[MasterRouteHints, ...]:
    """Copy model hints. Copying does not trust them."""

    if not isinstance(proposal, MasterRouteProposal):
        _fail("invalid_master_route_proposal")
    return tuple(step.hints for step in proposal.steps)


def _status_for_outcome(outcome: MasterRouteOutcome) -> Sprint4PlanningStatus:
    mapping = {
        MasterRouteOutcome.AUTHORIZED_PLAN: Sprint4PlanningStatus.AUTHORIZED_PLAN,
        MasterRouteOutcome.CLARIFICATION_REQUIRED: Sprint4PlanningStatus.CLARIFICATION_REQUIRED,
        MasterRouteOutcome.UNSUPPORTED: Sprint4PlanningStatus.UNSUPPORTED,
        MasterRouteOutcome.REFUSED: Sprint4PlanningStatus.REFUSED,
    }
    return mapping[outcome]


@dataclass(frozen=True)
class Sprint4MasterPlanningResult:
    """MA2 decision plus the untrusted proposal that produced it."""

    context: MasterPlanningContext
    proposal: MasterRouteProposal | None
    decision: MasterRouteDecision | None
    status: Sprint4PlanningStatus
    untrusted_hints: tuple[MasterRouteHints, ...]
    instruction_ref: str = MASTER_PLANNING_INSTRUCTION_REF
    execution_authority: MasterRouteExecutionAuthority = MasterRouteExecutionAuthority.DENIED
    request_coverage: MasterRequestCoverage = MasterRequestCoverage.NOT_ESTABLISHED
    hint_trust: MasterHintTrust = MasterHintTrust.UNTRUSTED
    source_authenticity: MasterSourceAuthenticity = MasterSourceAuthenticity.NOT_ESTABLISHED
    understanding_limitation: str = REQUEST_UNDERSTANDING_LIMITATION
    schema_version: str = SPRINT4_MASTER_PLANNING_SCHEMA_VERSION
    terminal_reason: str | None = None

    def __post_init__(self) -> None:
        if self.schema_version != SPRINT4_MASTER_PLANNING_SCHEMA_VERSION:
            _fail("invalid_schema_version")
        if not isinstance(self.context, MasterPlanningContext):
            _fail("invalid_planning_context")
        if self.proposal is not None and not isinstance(self.proposal, MasterRouteProposal):
            _fail("invalid_master_route_proposal")
        if self.decision is not None and not isinstance(self.decision, MasterRouteDecision):
            _fail("invalid_route_decision")
        if not isinstance(self.status, Sprint4PlanningStatus):
            _fail("invalid_planning_status")
        _closed(self.execution_authority, MasterRouteExecutionAuthority.DENIED, "execution_authority_not_granted")
        _closed(
            self.request_coverage,
            MasterRequestCoverage.NOT_ESTABLISHED,
            "request_coverage_not_established",
        )
        _closed(self.hint_trust, MasterHintTrust.UNTRUSTED, "model_hints_not_trusted")
        _closed(
            self.source_authenticity,
            MasterSourceAuthenticity.NOT_ESTABLISHED,
            "source_authenticity_not_established",
        )
        if self.understanding_limitation != REQUEST_UNDERSTANDING_LIMITATION:
            _fail("invalid_understanding_limitation")
        if self.terminal_reason is not None and (
            not isinstance(self.terminal_reason, str)
            or _TERMINAL_REASON.fullmatch(self.terminal_reason) is None
        ):
            _fail("invalid_terminal_reason")
        if self.instruction_ref != MASTER_PLANNING_INSTRUCTION_REF:
            _fail("unknown_instruction_ref")
        if self.decision is not None:
            if self.decision.execution_authority is not MasterRouteExecutionAuthority.DENIED:
                _fail("execution_authority_not_granted")
            if self.decision.request_coverage is not MasterRequestCoverage.NOT_ESTABLISHED:
                _fail("request_coverage_not_established")
            if self.status is Sprint4PlanningStatus.AUTHORIZED_PLAN:
                if self.decision.outcome is not MasterRouteOutcome.AUTHORIZED_PLAN:
                    _fail("authorized_plan_required")
                if self.decision.plan is None:
                    _fail("authorized_plan_required")


class Sprint4InjectedRoutePlanner:
    """Host-injected planner. ``propose`` is untrusted. ``complete`` cannot emit tools."""

    def __init__(self, inner: object) -> None:
        _assert_closed_surface()
        reject_vendor_sdk_provider(inner)
        self._inner = inner
        self._bounded = None
        if callable(getattr(inner, "complete", None)):
            self._bounded = bind_master_planning_provider(inner)

    def propose(self, context: MasterPlanningContext) -> object:
        if not isinstance(context, MasterPlanningContext):
            _fail("invalid_planning_context")
        proposer = getattr(self._inner, "propose", None)
        if callable(proposer):
            return proposer(context)
        if self._bounded is None:
            _fail("invalid_master_route_proposal")
        from wilvor_ai.model_contracts import ModelTurnRequest

        decision = self._bounded.complete(
            ModelTurnRequest(
                user_text=context.canonical_user_text,
                instruction_ref=MASTER_PLANNING_INSTRUCTION_REF,
            )
        )
        if isinstance(decision, ModelDecision):
            return decision
        return decision


def plan_master_request(
    context: MasterPlanningContext,
    planner: object,
) -> Sprint4MasterPlanningResult:
    """Turn an untrusted planner output into an MA2 decision.

    A matching proposal text is not request coverage. An authorized plan is
    not an execution grant.
    """

    _assert_closed_surface()
    if not isinstance(context, MasterPlanningContext):
        _fail("invalid_planning_context")
    wrapped = planner if type(planner) is Sprint4InjectedRoutePlanner else Sprint4InjectedRoutePlanner(planner)
    raw = wrapped.propose(context)
    from wilvor_ai.sprint5_master_planning import (
        decode_planner_model_output,
        mismatched_executable_step_tokens,
    )

    try:
        decoded = decode_planner_model_output(raw, context)
    except ContractValidationError as exc:
        if isinstance(raw, ModelDecision) and exc.errors[0] in {
            "master_planner_tools_rejected",
            "master_planner_claims_rejected",
            "model_aws_authorization_rejected",
        }:
            raise
        if exc.errors[0] in {"user_text_mismatch", "excessive_plan_size", "decision_fanout_not_permitted"}:
            raise
        if isinstance(raw, ModelDecision):
            status = _reject_model_decision_as_proposal(raw)
            return Sprint4MasterPlanningResult(
                context=context,
                proposal=None,
                decision=None,
                status=status,
                untrusted_hints=(),
                terminal_reason=sanitized_terminal_reason(raw),
            )
        if exc.errors == ("invalid_master_route_proposal",) or "invalid_" in exc.errors[0]:
            return Sprint4MasterPlanningResult(
                context=context,
                proposal=None,
                decision=None,
                status=Sprint4PlanningStatus.INVALID_PROPOSAL,
                untrusted_hints=(),
            )
        raise
    if isinstance(decoded, Sprint4PlanningStatus):
        return Sprint4MasterPlanningResult(
            context=context,
            proposal=None,
            decision=None,
            status=decoded,
            untrusted_hints=(),
            terminal_reason=sanitized_terminal_reason(raw),
        )
    try:
        proposal = _as_proposal(decoded, context)
    except ContractValidationError as exc:
        if exc.errors == ("invalid_master_route_proposal",) or "invalid_" in exc.errors[0]:
            if exc.errors[0] in {"user_text_mismatch", "excessive_plan_size", "decision_fanout_not_permitted"}:
                raise
            return Sprint4MasterPlanningResult(
                context=context,
                proposal=None,
                decision=None,
                status=Sprint4PlanningStatus.INVALID_PROPOSAL,
                untrusted_hints=(),
            )
        raise
    hints = collect_untrusted_hints(proposal)
    try:
        decision = authorize_route(proposal, context)
    except ContractValidationError as exc:
        if "capability_step_mismatch" in exc.errors:
            details = mismatched_executable_step_tokens(proposal)
            if details:
                raise ContractValidationError((*exc.errors, *details)) from exc
        raise
    return Sprint4MasterPlanningResult(
        context=context,
        proposal=proposal,
        decision=decision,
        status=_status_for_outcome(decision.outcome),
        untrusted_hints=hints,
    )


__all__ = [
    "HINT_TRUST",
    "MODEL_PROPOSAL_IS_AUTHORIZATION",
    "SPRINT4_MASTER_PLANNING_SCHEMA_VERSION",
    "SPRINT4_PLANNING_EXECUTION_AUTHORITY_AVAILABLE",
    "Sprint4InjectedRoutePlanner",
    "Sprint4MasterPlanningResult",
    "Sprint4PlanningStatus",
    "collect_untrusted_hints",
    "plan_master_request",
]
