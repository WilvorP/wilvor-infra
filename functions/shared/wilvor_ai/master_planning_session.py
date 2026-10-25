"""Process-local planning-session record.

The registry keeps the exact planning context and the ``authorize_route``
result for one isolated test process. It generates the session id. A
caller-supplied string is not a principal. Matching that id, a public field,
or a copied invocation id does not establish registry membership.

The session handle is not a production capability. A session capability is
not production authentication.

Object identity of the retained handle and the retained plan is how this
process checks membership. That check is not production authentication,
source authenticity, or execution authority.

``execution_authority`` stays ``DENIED``. Production authority stays
``DENIED``. Source authenticity stays ``NOT_ESTABLISHED``. A planning
object cannot authorize execution. This module does not call specialists,
models, AWS, or a dispatcher. It does not retain an evidence-bearing
parallel bundle.

There is no ``open_authenticated`` entry point, production credential, grant
issuer, or dispatcher.

PROPOSED Sprint 2 boundaries, not implemented here:

- An execution-scope audit may later cite the retained decision and the one
  selected invocation. This module does not import or construct that audit,
  and selection does not grant execution.
- Gate inspection may later read gates on the retained plan. This module
  does not resolve gates or record runtime dependency outcomes.
- A process-local test capability may later cite ``session_id`` as an
  untrusted label. This module does not issue, consume, or revoke a
  capability, and ``session_id`` is not a principal.
- Provenance classification may later record that source authenticity
  remains ``NOT_ESTABLISHED``. This module does not authenticate a source
  read.
"""

from __future__ import annotations

import secrets
import threading
from enum import Enum
from typing import Mapping

from wilvor_ai.contracts import ContractValidationError
from wilvor_ai.master_contracts import (
    MasterInvocationAuthorization,
    MasterProductionAuthority,
)
from wilvor_ai.master_evidence_admission import MasterSourceAuthenticity
from wilvor_ai.master_routing import (
    MasterPlanningContext,
    MasterRequestCoverage,
    MasterRouteDecision,
    MasterRouteExecutionAuthority,
    MasterRouteOutcome,
    MasterRoutePlan,
    MasterRouteProposal,
    authorize_route,
)


MASTER_PLANNING_SESSION_SCHEMA_VERSION = "wilvor.ai.master_planning_session.v1"

_EVIDENCE_BUNDLE_TYPES = frozenset(
    {
        "MasterParallelContractBundle",
        "MasterEvidenceCorroborationInput",
        "MasterComparisonRequest",
        "LiveOpsAdmissionSources",
        "SpecialistResult",
        "HistoricalSpecialistRunResult",
        "DecisionSpecialistRunResult",
    }
)
_EVIDENCE_BUNDLE_ATTRS = ("evidence_inputs", "comparison_requests")


class MasterPlanningSessionState(str, Enum):
    """Lifecycle of one process-local session. None of these grants execution."""

    OPEN = "OPEN"
    CLOSED = "CLOSED"
    INVALIDATED = "INVALIDATED"


class _Membership:
    """Marker for handles minted by one registry. Not a credential."""

    __slots__ = ()


class _Retained:
    """Private retention. Not a serializable certificate."""

    __slots__ = (
        "record",
        "context",
        "proposal",
        "decision",
        "selected_invocation",
        "state",
    )

    def __init__(
        self,
        record: "MasterPlanningSessionRecord",
        context: MasterPlanningContext,
        proposal: MasterRouteProposal,
        decision: MasterRouteDecision,
    ) -> None:
        self.record = record
        self.context: MasterPlanningContext | None = context
        self.proposal: MasterRouteProposal | None = proposal
        self.decision: MasterRouteDecision | None = decision
        self.selected_invocation: MasterInvocationAuthorization | None = None
        self.state = MasterPlanningSessionState.OPEN


def _reject_evidence_bundle(value: object) -> None:
    if type(value).__name__ in _EVIDENCE_BUNDLE_TYPES or any(
        hasattr(value, name) for name in _EVIDENCE_BUNDLE_ATTRS
    ):
        raise ContractValidationError("evidence_bundle_not_retained")


def _assert_denied(decision: MasterRouteDecision) -> None:
    if decision.execution_authority is not MasterRouteExecutionAuthority.DENIED:
        raise ContractValidationError("execution_authority_not_granted")
    if decision.request_coverage is not MasterRequestCoverage.NOT_ESTABLISHED:
        raise ContractValidationError("request_coverage_not_established")
    plan = decision.plan
    if plan is None:
        return
    if plan.execution_authority is not MasterRouteExecutionAuthority.DENIED:
        raise ContractValidationError("execution_authority_not_granted")
    if plan.request_coverage is not MasterRequestCoverage.NOT_ESTABLISHED:
        raise ContractValidationError("request_coverage_not_established")
    if plan.end_to_end_fulfilled:
        raise ContractValidationError("end_to_end_fulfillment_not_granted")


class MasterPlanningSessionRecord:
    """Handle minted by one registry.

    Constructing a handle does not join a registry. ``session_id`` is a
    label generated by the registry. Copying the label or the membership
    marker does not establish membership, and membership is not production
    authentication.
    """

    __slots__ = ("_session_id", "_membership")

    def __init__(self, session_id: str, membership: object) -> None:
        if isinstance(membership, (str, bytes, bytearray)):
            raise ContractValidationError("session_principal_not_accepted")
        if membership is None:
            raise ContractValidationError("invalid_session")
        if not isinstance(session_id, str) or not session_id or session_id != session_id.strip():
            raise ContractValidationError("invalid_session_id")
        self._session_id = session_id
        self._membership = membership

    @property
    def session_id(self) -> str:
        return self._session_id

    @property
    def execution_authority(self) -> MasterRouteExecutionAuthority:
        return MasterRouteExecutionAuthority.DENIED

    @property
    def production_authority(self) -> MasterProductionAuthority:
        return MasterProductionAuthority.DENIED

    @property
    def source_authenticity(self) -> MasterSourceAuthenticity:
        return MasterSourceAuthenticity.NOT_ESTABLISHED

    @property
    def request_coverage(self) -> MasterRequestCoverage:
        return MasterRequestCoverage.NOT_ESTABLISHED

    def __repr__(self) -> str:
        return f"MasterPlanningSessionRecord(session_id={self._session_id!r})"


class MasterPlanningSessionRegistry:
    """Registry owned by this process.

    Session ids are minted here. The retained context, proposal, and
    ``authorize_route`` result stay the original objects. A second plan is
    rejected rather than written over the first. Deserialized and
    reconstructed plans are not substitutes.
    """

    def __init__(self) -> None:
        self._membership = _Membership()
        self._lock = threading.Lock()
        self._sessions: dict[int, _Retained] = {}
        self._known_ids: set[str] = set()

    def open_session(
        self,
        proposal: MasterRouteProposal,
        context: MasterPlanningContext,
    ) -> MasterPlanningSessionRecord:
        """Retain one ``authorize_route`` result under a new session id.

        The returned handle is process-local membership only.
        """

        self._reject_inputs(proposal, context)
        decision = authorize_route(proposal, context)
        _assert_denied(decision)
        with self._lock:
            session_id = secrets.token_hex(16)
            while session_id in self._known_ids:
                session_id = secrets.token_hex(16)
            record = MasterPlanningSessionRecord(session_id, self._membership)
            self._sessions[id(record)] = _Retained(record, context, proposal, decision)
            self._known_ids.add(session_id)
            return record

    def retain_route(
        self,
        record: MasterPlanningSessionRecord,
        proposal: MasterRouteProposal,
        context: MasterPlanningContext,
    ) -> None:
        """Reject another plan for a session that already has a result.

        The retained decision is left in place.
        """

        self._reject_inputs(proposal, context)
        with self._lock:
            slot = self._require_usable(record)
            if slot.decision is None:
                raise ContractValidationError("invalid_session_state")
            raise ContractValidationError("retained_plan_not_replaced")

    def state(self, record: MasterPlanningSessionRecord) -> MasterPlanningSessionState:
        with self._lock:
            return self._require_known(record).state

    def context_for(self, record: MasterPlanningSessionRecord) -> MasterPlanningContext:
        with self._lock:
            context = self._require_usable(record).context
            if context is None:
                raise ContractValidationError("invalid_session_state")
            return context

    def proposal_for(self, record: MasterPlanningSessionRecord) -> MasterRouteProposal:
        with self._lock:
            proposal = self._require_usable(record).proposal
            if proposal is None:
                raise ContractValidationError("invalid_session_state")
            return proposal

    def decision_for(self, record: MasterPlanningSessionRecord) -> MasterRouteDecision:
        with self._lock:
            decision = self._require_usable(record).decision
            if decision is None:
                raise ContractValidationError("invalid_session_state")
            return decision

    def plan_for(self, record: MasterPlanningSessionRecord) -> MasterRoutePlan:
        with self._lock:
            decision = self._require_usable(record).decision
            if (
                decision is None
                or decision.outcome is not MasterRouteOutcome.AUTHORIZED_PLAN
                or decision.plan is None
            ):
                raise ContractValidationError("invalid_session_state")
            return decision.plan

    def require_retained_decision(
        self,
        record: MasterPlanningSessionRecord,
        decision: object,
    ) -> MasterRouteDecision:
        """Return the retained decision only when ``decision`` is that object."""

        with self._lock:
            retained = self._require_usable(record).decision
            if retained is None:
                raise ContractValidationError("invalid_session_state")
            self._reject_substitute(
                decision,
                retained,
                mapping_code="deserialized_decision_rejected",
                forged_code="forged_decision_rejected",
                reconstructed_code="reconstructed_decision_rejected",
            )
            return retained

    def require_retained_plan(
        self,
        record: MasterPlanningSessionRecord,
        plan: object,
    ) -> MasterRoutePlan:
        """Return the retained plan only when ``plan`` is that object."""

        with self._lock:
            decision = self._require_usable(record).decision
            retained = None if decision is None else decision.plan
            if (
                decision is None
                or decision.outcome is not MasterRouteOutcome.AUTHORIZED_PLAN
                or retained is None
            ):
                raise ContractValidationError("invalid_session_state")
            self._reject_substitute(
                plan,
                retained,
                mapping_code="deserialized_plan_rejected",
                forged_code="forged_plan_rejected",
                reconstructed_code="reconstructed_plan_rejected",
            )
            return retained

    def associate_invocation(
        self,
        record: MasterPlanningSessionRecord,
        invocation: object,
    ) -> MasterInvocationAuthorization:
        """Select one invocation object that is already on the retained plan.

        A matching invocation id on another object is not membership.
        A second selection does not replace the first. Selection does not
        authorize execution.
        """

        with self._lock:
            slot = self._require_usable(record)
            decision = slot.decision
            plan = None if decision is None else decision.plan
            if (
                decision is None
                or decision.outcome is not MasterRouteOutcome.AUTHORIZED_PLAN
                or plan is None
            ):
                raise ContractValidationError("invalid_session_state")
            if isinstance(invocation, (list, tuple, set)) or slot.selected_invocation is not None:
                raise ContractValidationError("multiple_invocations_rejected")
            if isinstance(invocation, str):
                raise ContractValidationError("copied_invocation_identifier")
            if not isinstance(invocation, MasterInvocationAuthorization):
                raise ContractValidationError("forged_invocation_rejected")
            for step in plan.steps:
                if step.invocation is invocation:
                    slot.selected_invocation = invocation
                    return invocation
            if any(step.invocation.invocation_id == invocation.invocation_id for step in plan.steps):
                raise ContractValidationError("copied_invocation_identifier")
            raise ContractValidationError("wrong_invocation")

    def selected_invocation(
        self,
        record: MasterPlanningSessionRecord,
    ) -> MasterInvocationAuthorization:
        with self._lock:
            slot = self._require_usable(record)
            selected = slot.selected_invocation
            plan = None if slot.decision is None else slot.decision.plan
            if selected is None or plan is None:
                raise ContractValidationError("invalid_session_state")
            if not any(step.invocation is selected for step in plan.steps):
                raise ContractValidationError("wrong_invocation")
            return selected

    def close(self, record: MasterPlanningSessionRecord) -> None:
        with self._lock:
            slot = self._require_known(record)
            if slot.state is not MasterPlanningSessionState.OPEN:
                raise ContractValidationError("invalid_session_state")
            if slot.decision is not None:
                try:
                    _assert_denied(slot.decision)
                except ContractValidationError:
                    self._wipe(slot)
                    raise
            slot.state = MasterPlanningSessionState.CLOSED

    def invalidate(self, record: MasterPlanningSessionRecord) -> None:
        """Drop retained planning objects. The session cannot be reused."""

        with self._lock:
            slot = self._require_known(record)
            if slot.state is MasterPlanningSessionState.INVALIDATED:
                raise ContractValidationError("invalid_session_state")
            self._wipe(slot)

    def _reject_inputs(self, proposal: object, context: object) -> None:
        _reject_evidence_bundle(proposal)
        _reject_evidence_bundle(context)
        if not isinstance(proposal, MasterRouteProposal):
            raise TypeError("proposal is required")
        if not isinstance(context, MasterPlanningContext):
            raise TypeError("planning context is required")

    def _minted(self, record: MasterPlanningSessionRecord) -> _Retained | None:
        slot = self._sessions.get(id(record))
        if slot is not None and slot.record is record:
            return slot
        return None

    def _require_known(self, record: object) -> _Retained:
        if not isinstance(record, MasterPlanningSessionRecord):
            raise ContractValidationError("invalid_session")
        slot = self._minted(record)
        if slot is not None:
            return slot
        if record._membership is self._membership or record.session_id in self._known_ids:
            raise ContractValidationError("copied_session_identifier")
        raise ContractValidationError("foreign_session")

    def _require_usable(self, record: object) -> _Retained:
        slot = self._require_known(record)
        if slot.state is MasterPlanningSessionState.CLOSED:
            raise ContractValidationError("session_closed")
        if slot.state is MasterPlanningSessionState.INVALIDATED:
            raise ContractValidationError("session_invalidated")
        if slot.state is not MasterPlanningSessionState.OPEN or slot.decision is None:
            raise ContractValidationError("invalid_session_state")
        try:
            _assert_denied(slot.decision)
        except ContractValidationError:
            self._wipe(slot)
            raise
        return slot

    def _wipe(self, slot: _Retained) -> None:
        slot.context = None
        slot.proposal = None
        slot.decision = None
        slot.selected_invocation = None
        slot.state = MasterPlanningSessionState.INVALIDATED

    @staticmethod
    def _reject_substitute(
        presented: object,
        retained: object,
        *,
        mapping_code: str,
        forged_code: str,
        reconstructed_code: str,
    ) -> None:
        if isinstance(presented, Mapping):
            raise ContractValidationError(mapping_code)
        if type(presented) is not type(retained):
            raise ContractValidationError(forged_code)
        if presented is not retained:
            raise ContractValidationError(reconstructed_code)
