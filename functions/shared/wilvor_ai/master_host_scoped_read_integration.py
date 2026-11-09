"""Nonproduction join of scope, budget, and the guarded read executor.

This module is an offline harness. It is not a production host, not a
grant issuer, and not source authentication. ``open_production_scoped_read``
and ``issue_production_scoped_read`` always fail. A successful local adapter
call does not flip those doors.

Call order for one read, also listed in ``SCOPED_READ_ENFORCEMENT_SEQUENCE``:

1. Reject a caller clock, a caller region object, or a read callback
   without reading or calling it.
2. ``resolve_master_authorized_scope`` intersects hints with the sealed
   host. ``BOUNDED_DESCRIBED`` is not a grant. Tool, resource, operation,
   and capability are not rewritten. Effective region codes are the
   intersection only.
3. ``server_read_tools``, ``server_read_argument_names``,
   ``server_read_resources``, and ``server_read_budget`` bound the read.
4. ``open_private_offline_harness`` issues the deadline from the harness
   clock and the injected remaining runtime. Caller clocks and caller
   deadlines are not that authority.
5. ``check_master_read_resource`` runs against that host deadline and the
   effective region. ``WITHIN_ALLOWLIST`` is not a permit.
   ``allowlist_status_is_not_a_permit`` always fails.
6. ``RuntimeBudgetSession.begin_read`` then ``mark_entered``.
7. Cancellation and the host deadline are checked again. Either one stops
   the call before the adapter.
8. ``bind_nonproduction_test_invocation`` copies the effective scope and
   the counters from before this reservation. The nonce is a harness
   label, not a ledger grant nonce.
9. ``MasterTrustedReadExecutor.execute`` calls the sealed registry
   adapter. The request does not carry a callable. The executor checks
   tool, arguments, operation, budget, deadline, and cancellation at that
   boundary and again after the adapter returns.
10. ``finish_started`` closes the reservation. A cancel or a deadline that
    arrives while the adapter is running becomes ``TERMINAL_PARTIAL``.

``RuntimeBudgetSession`` cannot interrupt a blocked adapter call. The
adapter must return within ``downstream_timeout_s`` or observe
``HostCancellation``. See ``BLOCKED_IO_NOT_INTERRUPTIBLE``.

The executor's ``INTERNAL_LEDGER_WRITE`` effect is this process's
idempotency mark. It is not ``LocalHarnessGrantIssuer.consume`` and not
``MasterExecutionLedger``. Two local marks are not distributed atomicity.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum

from wilvor_ai.contracts import ContractValidationError
from wilvor_ai.master_authorized_scope import (
    EffectiveScopeRequestHints,
    EffectiveScopeStatus,
    MasterAuthorizedScopeDecision,
    resolve_master_authorized_scope,
)
from wilvor_ai.master_read_resource_guard import (
    MasterReadCheckStatus,
    MasterReadDenialReason,
    MasterReadResourceCheck,
    MasterReadResourceProposal,
    ReadOperation,
    ServerCancellation,
    ServerReadEvaluation,
    check_master_read_resource,
    seal_ingress_principal,
    seal_population_scope,
    server_read_argument_names,
    server_read_budget,
    server_read_resources,
    server_read_tools,
)
from wilvor_ai.master_runtime_budget import (
    BudgetDecision,
    RequestBudgetEnvelope,
    RuntimeBudgetSession,
    StartedTaskDisposition,
    offline_harness_seal,
    open_private_offline_harness,
)
from wilvor_ai.master_trusted_read_executor import (
    HostCancellation,
    MasterTrustedReadExecutor,
    NonproductionHostClock,
    NonproductionReadRegistry,
    ReadExecutionError,
    ReadExecutionOutcome,
    ReadReceiptCandidate,
    UntrustedReadRequest,
    bind_nonproduction_cancellation,
    bind_nonproduction_host_clock,
    bind_nonproduction_test_invocation,
    raw_read_observation,
)


MASTER_HOST_SCOPED_READ_SCHEMA_VERSION = "wilvor.ai.master_host_scoped_read_integration.v1"

PRODUCTION_HOST_SCOPED_READ_ENABLED = False
WITHIN_ALLOWLIST_IS_A_PERMIT = False
SCOPE_DESCRIPTION_IS_A_GRANT = False
LOCAL_READ_IS_SOURCE_AUTHENTICATION = False
LOCAL_READ_IS_VERIFIED_ZERO = False
LOCAL_READ_IS_FLIGHT_SAFETY = False
BLOCKED_IO_IS_INTERRUPTIBLE = False

SCOPED_READ_ENFORCEMENT_SEQUENCE = (
    "resolve_master_authorized_scope",
    "intersect_effective_scope_without_widening",
    "server_read_tools",
    "server_read_argument_names",
    "server_read_resources",
    "server_read_budget",
    "check_master_read_resource",
    "within_allowlist_is_not_a_permit",
    "open_private_offline_harness",
    "RuntimeBudgetSession.begin_read",
    "RuntimeBudgetSession.mark_entered",
    "recheck_cancellation_and_deadline",
    "bind_nonproduction_test_invocation",
    "MasterTrustedReadExecutor.execute",
    "post_return_recheck",
    "RuntimeBudgetSession.finish_started",
)

MINIMAL_READ_INVOCATION_FIELDS = (
    "authority_kind",
    "master_request_id",
    "planning_session_id",
    "step_id",
    "invocation_id",
    "specialist",
    "purpose",
    "capability",
    "tool_name",
    "tool_call_id",
    "resource_id",
    "operation",
    "principal_ref",
    "policy_revision",
    "region_codes",
    "historical_start_utc",
    "historical_end_utc",
    "decision_aircraft_id",
    "decision_recommendation_id",
    "accepted_at_utc",
    "deadline_utc",
    "idempotency_key",
    "grant_nonce",
    "executions_used",
    "model_turns_used",
    "tool_calls_used",
    "timeout_budget_seconds",
    "correlation_id",
)

MINIMAL_READ_RECEIPT_FIELDS = (
    "authority_kind",
    "outcome",
    "denial_reasons",
    "adapter_invoked",
    "approved_source_adapter",
    "production_grant_issued",
    "specialist",
    "purpose",
    "capability",
    "tool_call_id",
    "resource_id",
    "operation",
    "requested_arguments",
    "executed_arguments",
    "scope_injected_argument_names",
    "effective_region_codes",
    "effective_historical_start_utc",
    "effective_historical_end_utc",
    "effective_decision_aircraft_id",
    "effective_decision_recommendation_id",
    "accepted_at_utc",
    "deadline_utc",
    "execution_started_at_utc",
    "execution_finished_at_utc",
    "source_reported_observed_at_utc",
    "source_clock_authority",
    "row_limit",
    "rows_returned",
    "record_ids",
    "partial_pagination",
    "next_page_token_present",
    "page_empty",
    "source_authenticity",
    "verified_zero_minted",
    "aviation_evidence_admitted",
    "geographic_coverage",
    "geographic_impact_verified",
    "effects",
    "grant_nonce",
    "idempotency_key",
)

BLOCKED_IO_NOT_INTERRUPTIBLE = (
    "RuntimeBudgetSession cannot interrupt a blocked adapter call. "
    "mark_entered requires downstream_timeout_s to be at least 1 and no "
    "greater than the remaining host whole seconds. The sealed adapter must "
    "return within that timeout or observe HostCancellation. After return, "
    "MasterTrustedReadExecutor rechecks cancellation, timeout_budget_seconds, "
    "the host deadline, and the row limit. finish_started then reports "
    "TERMINAL_PARTIAL when the call was cancelled or the deadline expired "
    "while it was running. The call is not killed. A next_page_token is not "
    "retained and is not followed. grant_nonce on the invocation is a "
    "nonproduction label, not a MasterExecutionLedger nonce. The receipt "
    "INTERNAL_LEDGER_WRITE effect is this process's idempotency mark, not "
    "distributed ledger consumption and not source authentication."
)

_NS_PER_SECOND = 1_000_000_000
_BUDGET_GATE_DENIALS = frozenset(
    {
        MasterReadDenialReason.DEADLINE_EXPIRED,
        MasterReadDenialReason.BUDGET_EXHAUSTED,
        MasterReadDenialReason.CANCELLED,
    }
)
_TERMINAL_OUTCOMES = frozenset(
    {
        ReadExecutionOutcome.COMPLETED,
        ReadExecutionOutcome.CANCELLED,
        ReadExecutionOutcome.TIMEOUT,
    }
)


class ScopedReadJoinRefusal(str, Enum):
    """Why the harness did not return a bounded local observation."""

    CALLER_CLOCK_NOT_AUTHORITY = "CALLER_CLOCK_NOT_AUTHORITY"
    CALLER_REGION_NOT_AUTHORITY = "CALLER_REGION_NOT_AUTHORITY"
    CALLER_CALLABLE_REJECTED = "CALLER_CALLABLE_REJECTED"
    SCOPE_DENIED = "SCOPE_DENIED"
    RESOURCE_OR_CAPABILITY_CHANGED = "RESOURCE_OR_CAPABILITY_CHANGED"
    SERVER_ALLOWLIST_DENIED = "SERVER_ALLOWLIST_DENIED"
    GUARD_DENIED = "GUARD_DENIED"
    BUDGET_NOT_RESERVED = "BUDGET_NOT_RESERVED"
    NOT_ENTERED = "NOT_ENTERED"
    STOPPED_BEFORE_ADAPTER = "STOPPED_BEFORE_ADAPTER"
    READ_NOT_COMPLETED = "READ_NOT_COMPLETED"


def _closed() -> None:
    if (
        PRODUCTION_HOST_SCOPED_READ_ENABLED is not False
        or WITHIN_ALLOWLIST_IS_A_PERMIT is not False
        or SCOPE_DESCRIPTION_IS_A_GRANT is not False
        or LOCAL_READ_IS_SOURCE_AUTHENTICATION is not False
        or LOCAL_READ_IS_VERIFIED_ZERO is not False
        or LOCAL_READ_IS_FLIGHT_SAFETY is not False
        or BLOCKED_IO_IS_INTERRUPTIBLE is not False
    ):
        raise ContractValidationError("production_read_executor_not_enabled")


def _parse(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


class _AlignedClock:
    """One harness instant shared by the budget session and the executor clock."""

    def __init__(self, now_utc: str) -> None:
        _closed()
        self._executor_clock = bind_nonproduction_host_clock(now_utc)
        self._mono = 0

    @property
    def executor_clock(self) -> NonproductionHostClock:
        return self._executor_clock

    def now_utc(self) -> str:
        return self._executor_clock.now_utc()

    def monotonic_ns(self) -> int:
        return self._mono

    def advance_seconds(self, seconds: int) -> None:
        if type(seconds) is not int or seconds < 1:
            raise ContractValidationError("invalid_clock_advance")
        current = _parse(self._executor_clock.now_utc())
        stamp = (current + timedelta(seconds=seconds)).strftime("%Y-%m-%dT%H:%M:%SZ")
        self._executor_clock.advance_to(stamp)
        self._mono += seconds * _NS_PER_SECOND


class _Remaining:
    def __init__(self, remaining_ms: int) -> None:
        if type(remaining_ms) is not int or remaining_ms < 1000:
            raise ContractValidationError("invalid_host_runtime")
        self._remaining_ms = remaining_ms

    def remaining_ms(self) -> int:
        return self._remaining_ms


@dataclass(frozen=True)
class ScopedReadJoinResult:
    """One harness outcome. Closed flags cannot become a flight-safety conclusion."""

    refusal: ScopedReadJoinRefusal | None
    scope: MasterAuthorizedScopeDecision | None
    guard_check: MasterReadResourceCheck | None
    budget: BudgetDecision | None
    receipt: ReadReceiptCandidate | None
    observation: dict[str, object] | None
    session: RuntimeBudgetSession | None
    adapter_invoked: bool
    downstream_timeout_seconds: int | None
    schema_version: str = MASTER_HOST_SCOPED_READ_SCHEMA_VERSION
    within_allowlist_used_as_permit: bool = False
    scope_description_is_grant: bool = False
    production_grant_issued: bool = False
    production_authority_enabled: bool = False
    source_authenticity_established: bool = False
    verified_zero: bool = False
    flight_safety_conclusion: bool = False
    geographic_coverage_established: bool = False
    sigmet_polygon_intersection_established: bool = False
    blocked_io_interruptible: bool = False

    def __post_init__(self) -> None:
        _closed()
        if self.schema_version != MASTER_HOST_SCOPED_READ_SCHEMA_VERSION:
            raise ContractValidationError("invalid_schema_version")
        closed_flags = (
            self.within_allowlist_used_as_permit,
            self.scope_description_is_grant,
            self.production_grant_issued,
            self.production_authority_enabled,
            self.source_authenticity_established,
            self.verified_zero,
            self.flight_safety_conclusion,
            self.geographic_coverage_established,
            self.sigmet_polygon_intersection_established,
            self.blocked_io_interruptible,
        )
        if any(flag is not False for flag in closed_flags):
            raise ContractValidationError("scoped_read_is_not_a_flight_safety_conclusion")
        if self.refusal is not None and type(self.refusal) is not ScopedReadJoinRefusal:
            raise ContractValidationError("invalid_scoped_read_refusal")
        if self.scope is not None:
            _reject_scope_promotion(self.scope)
        if self.budget is not None and (
            type(self.budget) is not BudgetDecision or self.budget.production_grant_issued is not False
        ):
            raise ContractValidationError("descriptive_allow_is_not_a_production_grant")
        if self.session is not None and (
            type(self.session) is not RuntimeBudgetSession or self.session.production_grant_issued is not False
        ):
            raise ContractValidationError("budget_session_is_not_a_grant")
        if self.receipt is not None:
            _reject_receipt_promotion(self.receipt)
        if self.observation is not None:
            _reject_observation_promotion(self.observation)
        if self.adapter_invoked is not (self.receipt is not None and self.receipt.adapter_invoked):
            raise ContractValidationError("invalid_scoped_read_result")
        if self.refusal is None:
            if (
                self.receipt is None
                or self.receipt.outcome is not ReadExecutionOutcome.COMPLETED
                or self.budget is None
                or self.budget.disposition is not StartedTaskDisposition.COMPLETED_BOUNDED
                or self.adapter_invoked is not True
            ):
                raise ContractValidationError("scoped_read_not_completed")

    def __bool__(self) -> bool:
        raise ContractValidationError("scoped_read_is_not_a_flight_safety_conclusion")


def _reject_scope_promotion(scope: MasterAuthorizedScopeDecision) -> None:
    if type(scope) is not MasterAuthorizedScopeDecision:
        raise ContractValidationError("invalid_effective_scope")
    if (
        scope.execution_granted is not False
        or scope.authenticated_source_read is not False
        or scope.production_principal_issued is not False
        or scope.dispatch_permitted is not False
        or scope.region_scope.geographic_coverage_established is not False
        or scope.region_scope.sigmet_polygon_intersection_established is not False
        or scope.decision_target_scope.missing_risk_recorded_as_low is not False
        or scope.decision_target_scope.target_authorization_granted is not False
    ):
        raise ContractValidationError("effective_scope_is_not_execution_grant")


def _reject_receipt_promotion(receipt: ReadReceiptCandidate) -> None:
    if type(receipt) is not ReadReceiptCandidate:
        raise ContractValidationError("forged_read_receipt")
    if (
        receipt.source_authenticity.value != "NOT_ESTABLISHED"
        or receipt.production_authority.value != "DENIED"
        or receipt.approved_source_adapter is not False
        or receipt.production_grant_issued is not False
        or receipt.aviation_evidence_admitted is not False
        or receipt.verified_zero_minted is not False
        or receipt.geographic_impact_verified is not False
        or receipt.local_fake_is_aviation_evidence is not False
        or receipt.geographic_coverage != "NOT_ESTABLISHED"
        or receipt.tool_result_authentically_read is not False
    ):
        raise ContractValidationError("read_observation_is_not_source_authentication")


def _reject_observation_promotion(observation: dict[str, object]) -> None:
    if type(observation) is not dict:
        raise ContractValidationError("forged_read_receipt")
    if (
        observation.get("authentic_source_read") is not False
        or observation.get("aviation_evidence_admitted") is not False
        or observation.get("verified_zero_minted") is not False
        or observation.get("source_authenticity") != "NOT_ESTABLISHED"
        or observation.get("geographic_coverage") != "NOT_ESTABLISHED"
        or "next_page_token" in observation
        or "tool_result" in observation
    ):
        raise ContractValidationError("read_observation_is_not_source_authentication")


class PreparedScopedRead:
    """Scope and session prepared for one entry. Not a grant and not a read."""

    def __init__(self) -> None:
        self.ready = False
        self.consumed = False
        self.early: ScopedReadJoinResult | None = None
        self.session: RuntimeBudgetSession | None = None
        self.scope: MasterAuthorizedScopeDecision | None = None
        self.hints: EffectiveScopeRequestHints | None = None
        self.tool_call_id: str | None = None
        self.action_id: str | None = None
        self.policy_check: object = None
        self.window: tuple[str, str] | None = None
        self.argument_names: frozenset[str] = frozenset()

    def __repr__(self) -> str:
        return "PreparedScopedRead(ready={0}, consumed={1})".format(self.ready, self.consumed)

    def __bool__(self) -> bool:
        raise ContractValidationError("prepared_scoped_read_is_not_a_grant")


def allowlist_status_is_not_a_permit(check: object) -> None:
    """Refuse to treat a policy check, including WITHIN_ALLOWLIST, as a permit."""

    _closed()
    if type(check) is not MasterReadResourceCheck:
        raise ContractValidationError("invalid_read_check")
    if check.status not in {MasterReadCheckStatus.WITHIN_ALLOWLIST, MasterReadCheckStatus.DENIED}:
        raise ContractValidationError("within_allowlist_is_not_a_permit")
    if check.execution_permitted is not False or check.dispatch_permitted is not False:
        raise ContractValidationError("within_allowlist_is_not_a_permit")
    raise ContractValidationError("within_allowlist_is_not_a_permit")


def open_production_scoped_read(*_args: object, **_kwargs: object) -> None:
    """Production scoped read is not enabled. There is no success return."""

    _closed()
    raise ContractValidationError("production_read_executor_not_enabled")


def issue_production_scoped_read(*_args: object, **_kwargs: object) -> None:
    """Production issuance is not integrated. There is no success return."""

    _closed()
    raise ContractValidationError("production_grant_issuer_not_integrated")


def _result(
    *,
    refusal: ScopedReadJoinRefusal | None,
    scope: MasterAuthorizedScopeDecision | None = None,
    guard_check: MasterReadResourceCheck | None = None,
    budget: BudgetDecision | None = None,
    receipt: ReadReceiptCandidate | None = None,
    session: RuntimeBudgetSession | None = None,
    downstream_timeout_seconds: int | None = None,
) -> ScopedReadJoinResult:
    observation = None if receipt is None else raw_read_observation(receipt)
    return ScopedReadJoinResult(
        refusal=refusal,
        scope=scope,
        guard_check=guard_check,
        budget=budget,
        receipt=receipt,
        observation=observation,
        session=session,
        adapter_invoked=receipt is not None and receipt.adapter_invoked,
        downstream_timeout_seconds=downstream_timeout_seconds,
    )


class NonproductionScopedReadHarness:
    """Offline host that joins scope, budget, and one sealed local adapter.

    The registry is supplied by the harness owner. A request cannot replace
    it or attach a callable. The clock and remaining runtime are the host
    boundary. Caller clocks, caller regions, and caller deadlines are not.
    """

    def __init__(self, registry: object, *, now_utc: str, remaining_ms: int = 30_000) -> None:
        _closed()
        if type(registry) is not NonproductionReadRegistry:
            raise ContractValidationError("approved_source_adapter_absent")
        self._clock = _AlignedClock(now_utc)
        self._remaining = _Remaining(remaining_ms)
        self._executor = MasterTrustedReadExecutor(registry, self._clock.executor_clock)

    def __bool__(self) -> bool:
        raise ContractValidationError("scoped_read_harness_is_not_a_grant")

    def now_utc(self) -> str:
        return self._clock.now_utc()

    def advance_seconds(self, seconds: int) -> None:
        """Move the shared harness clock. This is not a caller clock."""

        self._clock.advance_seconds(seconds)

    def prepare(
        self,
        hints: EffectiveScopeRequestHints,
        host: object,
        *,
        tool_call_id: str,
        action_id: str,
        session: RuntimeBudgetSession | None = None,
        policy_check: object = None,
        caller_clock: object = None,
        caller_region: object = None,
        read_callback: object = None,
    ) -> PreparedScopedRead:
        """Resolve scope and open a budget session. Do not call the adapter."""

        _closed()
        prepared = PreparedScopedRead()
        prepared.policy_check = policy_check
        prepared.tool_call_id = tool_call_id
        prepared.action_id = action_id
        if caller_clock is not None:
            prepared.early = _result(refusal=ScopedReadJoinRefusal.CALLER_CLOCK_NOT_AUTHORITY)
            return prepared
        if caller_region is not None:
            prepared.early = _result(refusal=ScopedReadJoinRefusal.CALLER_REGION_NOT_AUTHORITY)
            return prepared
        if read_callback is not None or _argument_is_callable(hints):
            prepared.early = _result(refusal=ScopedReadJoinRefusal.CALLER_CALLABLE_REJECTED)
            return prepared
        if type(hints) is not EffectiveScopeRequestHints:
            raise ContractValidationError("invalid_effective_scope_request")
        if session is not None and type(session) is not RuntimeBudgetSession:
            raise ContractValidationError("invalid_budget_session")
        decision = resolve_master_authorized_scope(hints, host)
        if decision.status is not EffectiveScopeStatus.BOUNDED_DESCRIBED:
            prepared.early = _result(refusal=ScopedReadJoinRefusal.SCOPE_DENIED, scope=decision)
            return prepared
        problem = _unchanged_boundary(hints, decision)
        if problem is not None:
            prepared.early = _result(refusal=problem, scope=decision)
            return prepared
        bound = _open_session(self._clock, self._remaining, hints, decision, session)
        prepared.session = bound
        prepared.scope = decision
        prepared.hints = hints
        prepared.window = _window_for(hints, decision)
        prepared.argument_names = server_read_argument_names(hints.specialist, hints.tool_name)
        prepared.ready = True
        return prepared

    def enter(
        self,
        prepared: PreparedScopedRead,
        *,
        cancellation: object = None,
        before_execute: object = None,
    ) -> ScopedReadJoinResult:
        """Reserve budget, enter, call the sealed adapter, then finish."""

        _closed()
        if type(prepared) is not PreparedScopedRead:
            raise ContractValidationError("invalid_prepared_scoped_read")
        if prepared.consumed:
            raise ContractValidationError("scoped_read_already_entered")
        prepared.consumed = True
        if prepared.early is not None:
            return prepared.early
        if not prepared.ready:
            return _result(refusal=ScopedReadJoinRefusal.SCOPE_DENIED, scope=prepared.scope, session=prepared.session)
        if cancellation is not None and type(cancellation) is not HostCancellation:
            return _result(
                refusal=ScopedReadJoinRefusal.STOPPED_BEFORE_ADAPTER,
                scope=prepared.scope,
                session=prepared.session,
            )
        if cancellation is None:
            cancellation = bind_nonproduction_cancellation()
        session = prepared.session
        hints = prepared.hints
        decision = prepared.scope
        if session is None or hints is None or decision is None or prepared.action_id is None:
            raise ContractValidationError("invalid_prepared_scoped_read")
        if not isinstance(cancellation, HostCancellation):
            raise ContractValidationError("cancellation_not_host_issued")
        usage = session.usage(invocation_id=hints.invocation_id)
        guard = _guard(hints, decision, session, usage.specialist_executions, usage.model_turns, usage.tool_calls, cancellation, self._clock)
        budget_gate = bool(guard.denial_reasons) and set(guard.denial_reasons) <= _BUDGET_GATE_DENIALS
        if guard.denial_reasons and not budget_gate:
            return _result(
                refusal=ScopedReadJoinRefusal.GUARD_DENIED,
                scope=decision,
                guard_check=guard,
                session=session,
            )
        reservation = session.begin_read(
            invocation_id=hints.invocation_id,
            action_id=prepared.action_id,
            tool_name=hints.tool_name,
            operation=hints.operation,
            rows=0,
            pagination_pages=1,
            arguments=hints.arguments,
            resource_id=hints.resource_id,
            historical_window=prepared.window,
        )
        if reservation.disposition is not StartedTaskDisposition.RESERVED or guard.denial_reasons:
            refusal = (
                ScopedReadJoinRefusal.GUARD_DENIED
                if guard.denial_reasons
                else ScopedReadJoinRefusal.BUDGET_NOT_RESERVED
            )
            return _result(
                refusal=refusal,
                scope=decision,
                guard_check=guard,
                budget=reservation,
                session=session,
            )
        timeout = _timeout_seconds(self._clock, session)
        entered = session.mark_entered(prepared.action_id, downstream_timeout_s=timeout)
        if entered.disposition is not StartedTaskDisposition.STARTED:
            return _result(
                refusal=ScopedReadJoinRefusal.NOT_ENTERED,
                scope=decision,
                guard_check=guard,
                budget=entered,
                session=session,
                downstream_timeout_seconds=timeout,
            )
        if cancellation.is_cancelled() or _deadline_reached(self._clock, session):
            if cancellation.is_cancelled():
                session.cancel()
            finished = session.finish_started(prepared.action_id, failed=False)
            return _result(
                refusal=ScopedReadJoinRefusal.STOPPED_BEFORE_ADAPTER,
                scope=decision,
                guard_check=guard,
                budget=finished,
                session=session,
                downstream_timeout_seconds=timeout,
            )
        if before_execute is not None and not callable(before_execute):
            session.finish_started(prepared.action_id, failed=True)
            raise ContractValidationError("invalid_before_execute")
        receipt: ReadReceiptCandidate | None = None
        finished_budget: BudgetDecision | None = None
        try:
            if before_execute is not None:
                before_execute()
            invocation = _invocation(
                hints,
                decision,
                prepared,
                usage.specialist_executions,
                usage.model_turns,
                usage.tool_calls,
                session,
                timeout,
            )
            request = _request(hints, prepared.tool_call_id or "")
            try:
                receipt = self._executor.execute(
                    request,
                    host_invocation=invocation,
                    cancellation=cancellation,
                    policy_check=prepared.policy_check,
                )
            except ReadExecutionError as exc:
                receipt = exc.receipt
            if cancellation.is_cancelled():
                session.cancel()
            _record_source(session, hints.invocation_id, receipt)
            failed = receipt.outcome not in _TERMINAL_OUTCOMES
            finished_budget = session.finish_started(prepared.action_id, failed=failed)
        except BaseException:
            if finished_budget is None:
                session.finish_started(prepared.action_id, failed=True)
            raise
        completed = (
            receipt.outcome is ReadExecutionOutcome.COMPLETED
            and finished_budget.disposition is StartedTaskDisposition.COMPLETED_BOUNDED
        )
        return _result(
            refusal=None if completed else ScopedReadJoinRefusal.READ_NOT_COMPLETED,
            scope=decision,
            guard_check=guard,
            budget=finished_budget,
            receipt=receipt,
            session=session,
            downstream_timeout_seconds=timeout,
        )

    def run(
        self,
        hints: EffectiveScopeRequestHints,
        host: object,
        *,
        tool_call_id: str,
        action_id: str,
        session: RuntimeBudgetSession | None = None,
        policy_check: object = None,
        cancellation: object = None,
        caller_clock: object = None,
        caller_region: object = None,
        read_callback: object = None,
        before_execute: object = None,
    ) -> ScopedReadJoinResult:
        """Prepare and enter one read. A callback argument is not the adapter."""

        prepared = self.prepare(
            hints,
            host,
            tool_call_id=tool_call_id,
            action_id=action_id,
            session=session,
            policy_check=policy_check,
            caller_clock=caller_clock,
            caller_region=caller_region,
            read_callback=read_callback,
        )
        return self.enter(prepared, cancellation=cancellation, before_execute=before_execute)


def _argument_is_callable(hints: object) -> bool:
    if type(hints) is not EffectiveScopeRequestHints:
        return False
    return any(callable(value) for _name, value in hints.arguments)


def _unchanged_boundary(
    hints: EffectiveScopeRequestHints,
    decision: MasterAuthorizedScopeDecision,
) -> ScopedReadJoinRefusal | None:
    resource = decision.resource_scope
    if (
        decision.specialist is not hints.specialist
        or decision.purpose is not hints.purpose
        or decision.capability is not hints.capability
        or resource.tool_name != hints.tool_name
        or resource.resource_id != hints.resource_id
        or resource.approved_operation is not hints.operation
    ):
        return ScopedReadJoinRefusal.RESOURCE_OR_CAPABILITY_CHANGED
    effective = decision.region_scope.effective_state_codes
    approved = set(decision.region_scope.approved_state_codes)
    if not set(effective) <= approved:
        return ScopedReadJoinRefusal.RESOURCE_OR_CAPABILITY_CHANGED
    try:
        tools = server_read_tools(hints.specialist)
        names = server_read_argument_names(hints.specialist, hints.tool_name)
        resources = server_read_resources(hints.specialist, hints.tool_name)
        server_read_budget(hints.specialist)
    except ContractValidationError:
        return ScopedReadJoinRefusal.SERVER_ALLOWLIST_DENIED
    if hints.tool_name not in tools or hints.operation not in {ReadOperation.GET_ITEM, ReadOperation.QUERY}:
        return ScopedReadJoinRefusal.SERVER_ALLOWLIST_DENIED
    if any(name not in names for name, _value in hints.arguments):
        return ScopedReadJoinRefusal.SERVER_ALLOWLIST_DENIED
    if (hints.resource_id, hints.operation) not in resources:
        return ScopedReadJoinRefusal.SERVER_ALLOWLIST_DENIED
    if "start_utc" in names:
        window = decision.historical_window
        arguments = dict(hints.arguments)
        if (
            window.eligible_start_utc is None
            or window.eligible_end_utc is None
            or arguments.get("start_utc") != window.eligible_start_utc
            or arguments.get("end_utc") != window.eligible_end_utc
        ):
            return ScopedReadJoinRefusal.SERVER_ALLOWLIST_DENIED
        unbound = (window.unbound_plan_start_utc, window.unbound_plan_end_utc)
        eligible = (window.eligible_start_utc, window.eligible_end_utc)
        if unbound[0] is not None and eligible == unbound:
            return ScopedReadJoinRefusal.SERVER_ALLOWLIST_DENIED
    elif decision.historical_window.eligible_start_utc is not None:
        return ScopedReadJoinRefusal.RESOURCE_OR_CAPABILITY_CHANGED
    return None


def _window_for(
    hints: EffectiveScopeRequestHints,
    decision: MasterAuthorizedScopeDecision,
) -> tuple[str, str] | None:
    names = server_read_argument_names(hints.specialist, hints.tool_name)
    if "start_utc" not in names:
        return None
    start = decision.historical_window.eligible_start_utc
    end = decision.historical_window.eligible_end_utc
    if start is None or end is None:
        raise ContractValidationError("historical_window_not_host_approved")
    return start, end


def _open_session(
    clock: _AlignedClock,
    remaining: _Remaining,
    hints: EffectiveScopeRequestHints,
    decision: MasterAuthorizedScopeDecision,
    session: RuntimeBudgetSession | None,
) -> RuntimeBudgetSession:
    if session is None:
        envelope = RequestBudgetEnvelope(
            master_request_id=hints.master_request_id,
            planning_session_id=hints.planning_session_id,
            step_id=hints.step_id,
            invocation_id=hints.invocation_id,
            specialist=decision.specialist,
            purpose=decision.purpose,
            capability=decision.capability,
            correlation_id=hints.correlation_id,
            idempotency_key=hints.idempotency_key,
            principal_ref=decision.principal_ref,
            policy_revision=decision.policy_revision,
        )
        return open_private_offline_harness(
            offline_harness_seal(),
            clock=clock,
            remaining_runtime=remaining,
            envelope=envelope,
        )
    try:
        session.usage(invocation_id=hints.invocation_id)
    except ContractValidationError:
        session.register_invocation(
            step_id=hints.step_id,
            invocation_id=hints.invocation_id,
            specialist=decision.specialist,
            purpose=decision.purpose,
            capability=decision.capability,
        )
    return session


def _guard(
    hints: EffectiveScopeRequestHints,
    decision: MasterAuthorizedScopeDecision,
    session: RuntimeBudgetSession,
    executions_used: int,
    model_turns_used: int,
    tool_calls_used: int,
    cancellation: HostCancellation,
    clock: _AlignedClock,
) -> MasterReadResourceCheck:
    arguments = dict(hints.arguments)
    start = arguments.get("start_utc")
    end = arguments.get("end_utc")
    proposal = MasterReadResourceProposal(
        specialist=hints.specialist,
        purpose=hints.purpose,
        capability=hints.capability,
        tool_name=hints.tool_name,
        resource_id=hints.resource_id,
        operation=hints.operation,
        arguments=hints.arguments,
        master_request_id=hints.master_request_id,
        invocation_id=hints.invocation_id,
        historical_start_utc=start if isinstance(start, str) else None,
        historical_end_utc=end if isinstance(end, str) else None,
    )
    evaluation = ServerReadEvaluation(
        observed_utc=clock.now_utc(),
        deadline_utc=session.deadline_utc,
        executions_used=executions_used,
        model_turns_used=model_turns_used,
        tool_calls_used=tool_calls_used,
        cancellation=ServerCancellation(cancellation.is_cancelled()),
    )
    codes = decision.region_scope.effective_state_codes
    population = seal_population_scope(frozenset(codes)) if codes else None
    principal = decision.principal_ref
    if principal is None:
        raise ContractValidationError("principal_not_trusted")
    return check_master_read_resource(
        proposal,
        evaluation=evaluation,
        principal=seal_ingress_principal(principal),
        population_scope=population,
    )


def _timeout_seconds(clock: _AlignedClock, session: RuntimeBudgetSession) -> int:
    remaining = int((_parse(session.deadline_utc) - _parse(clock.now_utc())).total_seconds())
    if remaining < 1:
        return 1
    return remaining


def _deadline_reached(clock: _AlignedClock, session: RuntimeBudgetSession) -> bool:
    return _parse(clock.now_utc()) >= _parse(session.deadline_utc)


def _invocation(
    hints: EffectiveScopeRequestHints,
    decision: MasterAuthorizedScopeDecision,
    prepared: PreparedScopedRead,
    executions_used: int,
    model_turns_used: int,
    tool_calls_used: int,
    session: RuntimeBudgetSession,
    timeout: int,
) -> object:
    codes = decision.region_scope.effective_state_codes
    start = None if prepared.window is None else prepared.window[0]
    end = None if prepared.window is None else prepared.window[1]
    principal = decision.principal_ref
    policy = decision.policy_revision
    tool_call_id = prepared.tool_call_id
    action_id = prepared.action_id
    if principal is None or policy is None or tool_call_id is None or action_id is None:
        raise ContractValidationError("invalid_prepared_scoped_read")
    return bind_nonproduction_test_invocation(
        master_request_id=hints.master_request_id,
        planning_session_id=hints.planning_session_id,
        step_id=hints.step_id,
        invocation_id=hints.invocation_id,
        specialist=decision.specialist,
        purpose=decision.purpose,
        capability=decision.capability,
        tool_name=hints.tool_name,
        tool_call_id=tool_call_id,
        resource_id=hints.resource_id,
        operation=hints.operation,
        principal_ref=principal,
        policy_revision=policy,
        accepted_at_utc=session.accepted_at_utc,
        deadline_utc=session.deadline_utc,
        idempotency_key=hints.idempotency_key,
        grant_nonce=f"nonproduction-not-a-ledger-{action_id}",
        executions_used=executions_used,
        model_turns_used=model_turns_used,
        tool_calls_used=tool_calls_used,
        timeout_budget_seconds=timeout,
        region_codes=frozenset(codes) if codes else None,
        historical_start_utc=start,
        historical_end_utc=end,
        decision_aircraft_id=decision.decision_target_scope.described_aircraft_id,
        decision_recommendation_id=decision.decision_target_scope.described_recommendation_id,
        correlation_id=hints.correlation_id,
    )


def _request(hints: EffectiveScopeRequestHints, tool_call_id: str) -> UntrustedReadRequest:
    return UntrustedReadRequest(
        master_request_id=hints.master_request_id,
        planning_session_id=hints.planning_session_id,
        step_id=hints.step_id,
        invocation_id=hints.invocation_id,
        specialist=hints.specialist,
        purpose=hints.purpose,
        capability=hints.capability,
        tool_name=hints.tool_name,
        tool_call_id=tool_call_id,
        resource_id=hints.resource_id,
        operation=hints.operation,
        requested_arguments=hints.arguments,
        correlation_id=hints.correlation_id,
    )


def _record_source(session: RuntimeBudgetSession, invocation_id: str, receipt: ReadReceiptCandidate) -> None:
    if receipt.outcome is not ReadExecutionOutcome.COMPLETED:
        return
    observed = receipt.source_reported_observed_at_utc
    if observed is None:
        return
    deadline = session.deadline_utc
    accepted = session.accepted_at_utc
    session.record_source_observation(
        invocation_id=invocation_id,
        observed_at_utc=observed,
        rows=receipt.rows_returned,
        pagination_pages=1 if receipt.partial_pagination else 0,
    )
    if session.deadline_utc != deadline or session.accepted_at_utc != accepted:
        raise ContractValidationError("source_clock_moved_deadline")


__all__ = [
    "BLOCKED_IO_IS_INTERRUPTIBLE",
    "BLOCKED_IO_NOT_INTERRUPTIBLE",
    "LOCAL_READ_IS_FLIGHT_SAFETY",
    "LOCAL_READ_IS_SOURCE_AUTHENTICATION",
    "LOCAL_READ_IS_VERIFIED_ZERO",
    "MASTER_HOST_SCOPED_READ_SCHEMA_VERSION",
    "MINIMAL_READ_INVOCATION_FIELDS",
    "MINIMAL_READ_RECEIPT_FIELDS",
    "PRODUCTION_HOST_SCOPED_READ_ENABLED",
    "SCOPED_READ_ENFORCEMENT_SEQUENCE",
    "SCOPE_DESCRIPTION_IS_A_GRANT",
    "WITHIN_ALLOWLIST_IS_A_PERMIT",
    "NonproductionScopedReadHarness",
    "PreparedScopedRead",
    "ScopedReadJoinRefusal",
    "ScopedReadJoinResult",
    "allowlist_status_is_not_a_permit",
    "issue_production_scoped_read",
    "open_production_scoped_read",
]
