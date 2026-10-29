"""Nonproduction binding of a consumed host grant to a ledger start fence.

This module is the MA3 consumer port. It does not issue grants. Production
issuance stays ``issue_production_execution_grant``, which this module does
not unlock. ``LocalHarnessGrantIssuer.consume`` accepts only
``LocalHarnessConsumptionLedger``. ``MasterExecutionLedger.mark_started`` is
a separate call. Those two local records are not one atomic store and not
distributed exactly-once execution.

Permission is the live ``LocalHarnessExecutionGrant`` object plus
``LocalHarnessGrantIssuer.verify`` returning a consumed replay for the
retained invocation, and ``MasterExecutionLedger.mark_started`` replaying an
already started fence. A callable, a serialized claim, a
``GrantConsumption`` value, an ``authorized`` flag, or another invocation's
attempt token is not permission.

``MasterExecutionFanout.complete`` and ``MasterExecutionLedger.complete``
record scheduling. Neither one is native specialist success or MA4 admission.
``ScriptedTestWorker`` is not a native specialist adapter.

After Agent 2 merges, the integration owner must run the checks in
``AFTER_MERGE_TESTS`` against the combined grant and ledger API. This module
must not grow a second production issuer to paper over that gap.
"""

from __future__ import annotations

import functools
import types
from dataclasses import dataclass
from threading import Lock
from typing import Mapping

from wilvor_ai.contracts import ContractValidationError
from wilvor_ai.master_authorized_scheduler_adapter import (
    PRODUCTION_DISPATCH_ENABLED,
    BoundedDispatchContext,
    MasterAuthorizedSchedulerAdapter,
    Ma4SchedulerHandoff,
    ScriptedTestWorker,
    ScriptedWorkerObservation,
)
from wilvor_ai.master_contracts import MasterInvocationAuthorization, MasterSpecialist
from wilvor_ai.master_execution_grants import (
    GrantConsumption,
    GrantRejectionReason,
    GrantVerification,
    LocalHarnessConsumptionLedger,
    LocalHarnessExecutionGrant,
    LocalHarnessGrantIssuer,
    LocalHarnessPrincipal,
    MasterExecutionGrantEnvelope,
    ParsedExecutionGrantClaim,
    evaluate_grant_envelope,
)
from wilvor_ai.master_execution_ledger import (
    MasterExecutionLedger,
    MasterExecutionLedgerEnvelope,
    MasterExecutionLedgerOutcome,
    MasterExecutionLedgerRejection,
    MasterExecutionLedgerResult,
    MasterExecutionLedgerState,
)
from wilvor_ai.master_specialist_runtime_contract import (
    PRODUCTION_HOST,
    invoke_specialist,
    observe_local_scheduler_protocol,
)


AFTER_MERGE_TESTS = (
    "Call LocalHarnessGrantIssuer.consume(live_grant, MasterExecutionLedger, **bindings) "
    "on Agent 2's merged API. Today that call returns "
    "GrantRejectionReason.LEDGER_NOT_INTEGRATED because consume accepts only "
    "LocalHarnessConsumptionLedger. Do not treat a passing local harness "
    "consumption and a separate mark_started replay as one atomic commit.",
    "On that merged API, reserve_once, consume, and mark_started for one "
    "invocation and prove nonce expiry together with fence expiry. Re-run "
    "NonproductionHostSchedulerPort.dispatch_consumed against those objects. "
    "Do not add a second production issuer.",
    "Wire prepare_master_live_ops_native_bridge, "
    "prepare_master_historical_native_invocation, and the Decision runtime "
    "only after a reviewed host exists. ScriptedTestWorker is not those calls.",
    "Keep admit_master_evidence unwired. Ma4SchedulerHandoff.admission_performed "
    "stays false after scheduling complete().",
)

SCHEDULING_COMPLETE_IS_NOT_SPECIALIST_SUCCESS = (
    "Fanout complete() and ledger complete() record scheduling. They are not "
    "native specialist success and they do not admit MA4 evidence."
)


@dataclass(frozen=True)
class OfflineFenceConfirmation:
    """What dispatch actually checked. Closed flags cannot be turned on."""

    invocation_id: str
    attempt_token: str
    owner_id: str
    fence_epoch: int
    ledger_grant_nonce: str
    grant_consumed: bool
    fence_replayed: bool
    scripted_worker_only: bool = True
    distributed_atomicity: bool = False
    fresh_transition_is_permission: bool = False
    ledger_nonce_is_host_grant: bool = False
    grant_resource_scope_is_ledger_scope: bool = False
    region_scope_is_geographic_verification: bool = False
    decision_target_authorized: bool = False
    native_specialist_executed: bool = False
    production_dispatch_enabled: bool = False
    ma4_admission_performed: bool = False

    def __post_init__(self) -> None:
        if self.grant_consumed is not True or self.fence_replayed is not True:
            raise ContractValidationError("consumed_grant_required")
        if self.scripted_worker_only is not True:
            raise ContractValidationError("specialist_execution_not_granted")
        closed = (
            self.distributed_atomicity,
            self.fresh_transition_is_permission,
            self.ledger_nonce_is_host_grant,
            self.grant_resource_scope_is_ledger_scope,
            self.region_scope_is_geographic_verification,
            self.decision_target_authorized,
            self.native_specialist_executed,
            self.production_dispatch_enabled,
            self.ma4_admission_performed,
        )
        if any(flag is not False for flag in closed):
            raise ContractValidationError("execution_authority_not_granted")
        if type(self.fence_epoch) is not int or self.fence_epoch < 1:
            raise ContractValidationError("invalid_fence_epoch")
        if not isinstance(self.ledger_grant_nonce, str) or not self.ledger_grant_nonce:
            raise ContractValidationError("invalid_ledger_result")


def _reject_callable(value: object) -> None:
    if isinstance(value, (types.FunctionType, types.MethodType, types.BuiltinFunctionType, functools.partial)):
        raise ContractValidationError("model_origin_dispatch_rejected")
    if callable(value):
        raise ContractValidationError("model_origin_dispatch_rejected")


def _reject_untrusted_grant(value: object) -> None:
    """Refuse every object that is not the live local-harness grant."""

    _reject_callable(value)
    if isinstance(value, (str, bytes, bytearray)):
        raise ContractValidationError("token_not_a_grant")
    if isinstance(value, (bool, int, float)):
        raise ContractValidationError("authorized_flag_rejected")
    if isinstance(value, Mapping) or type(value) is ParsedExecutionGrantClaim:
        raise ContractValidationError("serialized_grant_rejected")
    if type(value) is GrantConsumption:
        raise ContractValidationError("forged_grant_rejected")
    if type(value) is not LocalHarnessExecutionGrant and (
        getattr(value, "authorized", None) is True or getattr(value, "dispatch_permitted", None) is True
    ):
        raise ContractValidationError("authorized_flag_rejected")
    if type(value) is not LocalHarnessExecutionGrant:
        raise ContractValidationError("forged_grant_rejected")


def issued_grant_bindings(envelope: object, principal: object) -> dict[str, object]:
    """Bindings for ``verify`` and ``consume`` of one evaluated envelope.

    The returned mapping is not a grant and not a dispatch permit.
    """

    if type(envelope) is not MasterExecutionGrantEnvelope:
        raise ContractValidationError("invalid_grant_envelope")
    if type(principal) is not LocalHarnessPrincipal:
        raise ContractValidationError("principal_not_sealed")
    evaluation = evaluate_grant_envelope(envelope)
    if (
        not evaluation.bounds_accepted
        or evaluation.tool_name is None
        or evaluation.resource_scope is None
        or evaluation.argument_names is None
        or evaluation.region_scope is None
        or evaluation.temporal_scopes is None
    ):
        raise ContractValidationError("grant_bounds_rejected")
    return {
        "principal": principal,
        "planning_session_id": envelope.planning_session_id,
        "step_id": envelope.step_id,
        "invocation_id": envelope.invocation_id,
        "specialist": envelope.specialist,
        "purpose": envelope.purpose,
        "capability": envelope.capability,
        "policy_revision": envelope.policy_revision,
        "tool_name": evaluation.tool_name,
        "resource_scope": evaluation.resource_scope,
        "argument_names": evaluation.argument_names,
        "region_scope": evaluation.region_scope,
        "temporal_scopes": evaluation.temporal_scopes,
        "historical_window": evaluation.historical_window,
        "decision_target_scope": evaluation.decision_target_scope,
        "accepted_at_utc": envelope.accepted_at_utc,
        "deadline_utc": envelope.deadline_utc,
        "expires_at_utc": envelope.expires_at_utc,
        "idempotency_key": envelope.idempotency_key,
        "max_model_turns": envelope.max_model_turns,
        "max_tool_calls": envelope.max_tool_calls,
        "correlation_id": envelope.correlation_id,
        "master_request_id": envelope.master_request_id,
    }


def retained_grant_bindings(
    context: BoundedDispatchContext,
    envelope: object,
    principal: object,
) -> dict[str, object]:
    """Overlay the retained step onto evaluated grant material.

    Identity, the historical window, and the decision-target label come from
    the retained plan. A grant signed for a different step fails ``verify``.
    Field equality here is not authority; ``verify`` is the check.
    """

    bindings = issued_grant_bindings(envelope, principal)
    bindings["master_request_id"] = context.master_request_id
    bindings["planning_session_id"] = context.planning_session_id
    bindings["step_id"] = context.step_id
    bindings["invocation_id"] = context.invocation_id
    bindings["specialist"] = context.specialist
    bindings["purpose"] = context.purpose
    bindings["capability"] = context.capability
    bindings["correlation_id"] = context.correlation_id
    bindings["accepted_at_utc"] = context.accepted_at_utc
    bindings["deadline_utc"] = context.deadline_utc
    bindings["historical_window"] = context.historical_window
    bindings["decision_target_scope"] = context.decision_target_scope
    return bindings


class NonproductionHostSchedulerPort:
    """Bind one consumed local grant and one started ledger fence, then a scripted worker.

    Production dispatch stays disabled. The registered worker must be a
    ``ScriptedTestWorker``. Native specialist entrypoints are not called.
    """

    def __init__(
        self,
        adapter: MasterAuthorizedSchedulerAdapter,
        issuer: object,
        consumption_ledger: object,
        execution_ledger: object,
    ) -> None:
        if PRODUCTION_DISPATCH_ENABLED is not False or PRODUCTION_HOST is not None:
            raise ContractValidationError("production_dispatch_disabled")
        if type(adapter) is not MasterAuthorizedSchedulerAdapter:
            raise ContractValidationError("invalid_schedule")
        if adapter.nonproduction_seal is None or adapter.production_dispatch_enabled is not False:
            raise ContractValidationError("production_dispatch_disabled")
        if type(issuer) is not LocalHarnessGrantIssuer:
            raise ContractValidationError("host_grant_authority_missing")
        if issuer.production_grants_issued != 0:
            raise ContractValidationError("production_grant_issuance_closed")
        if type(consumption_ledger) is not LocalHarnessConsumptionLedger:
            raise ContractValidationError("ledger_not_integrated")
        if type(execution_ledger) is not MasterExecutionLedger:
            raise ContractValidationError("ledger_authority_missing")
        if consumption_ledger.distributed is not False or consumption_ledger.durable is not False:
            raise ContractValidationError("ledger_not_integrated")
        if execution_ledger.production_cross_instance_authority is not False:
            raise ContractValidationError("execution_authority_not_granted")
        self._adapter = adapter
        self._issuer = issuer
        self._consumption_ledger = consumption_ledger
        self._execution_ledger = execution_ledger
        self._workers: dict[MasterSpecialist, ScriptedTestWorker] = {}
        self._lock = Lock()
        self.last_verification: GrantVerification | None = None
        self.last_fence: MasterExecutionLedgerResult | None = None
        self.last_confirmation: OfflineFenceConfirmation | None = None

    @property
    def production_dispatch_enabled(self) -> bool:
        return False

    @property
    def distributed_atomicity(self) -> bool:
        return False

    @property
    def durable_exactly_once(self) -> bool:
        return False

    def register(self, specialist: MasterSpecialist, worker: object) -> None:
        """Register one offline scripted worker. A callable is not a worker."""

        _reject_callable(worker)
        if isinstance(worker, (str, bytes, bytearray, bool, int, float, Mapping)):
            raise ContractValidationError("model_origin_dispatch_rejected")
        if type(worker) is not ScriptedTestWorker:
            raise ContractValidationError("model_origin_dispatch_rejected")
        if not isinstance(specialist, MasterSpecialist):
            raise ContractValidationError("invalid_specialist")
        if specialist in self._workers:
            raise ContractValidationError("worker_already_registered")
        self._workers[specialist] = worker

    def demonstrate_execution_ledger_is_not_a_consumption_ledger(
        self,
        grant: object,
        principal: object,
        grant_envelope: object,
    ) -> GrantConsumption:
        """Call ``consume`` with the execution ledger and return the rejection.

        This does not dispatch. A successful local consumption on
        ``LocalHarnessConsumptionLedger`` is a different object.
        """

        _reject_untrusted_grant(grant)
        bindings = issued_grant_bindings(grant_envelope, principal)
        consumption = self._issuer.consume(grant, self._execution_ledger, **bindings)
        if consumption.dispatch_permitted or consumption.local_harness_consumption_recorded:
            raise ContractValidationError("ledger_not_integrated")
        if consumption.reason is not GrantRejectionReason.LEDGER_NOT_INTEGRATED:
            reason = consumption.reason.value if consumption.reason is not None else "ledger_not_integrated"
            raise ContractValidationError(reason)
        return consumption

    def dispatch_consumed(
        self,
        invocation: MasterInvocationAuthorization,
        *,
        grant: object,
        principal: object,
        grant_envelope: object,
        ledger_envelope: object,
        owner_id: str,
        attempt_token: str,
        fence_epoch: int,
        now_utc: str,
    ) -> SchedulerInvocationOutcome:
        """Dispatch one retained invocation through the registered scripted worker.

        The grant must already be consumed. The ledger fence must already be
        ``STARTED``. A fresh ``mark_started`` from this call is failed closed
        and is not retried into a permit.
        """

        _reject_untrusted_grant(grant)
        if type(principal) is not LocalHarnessPrincipal:
            raise ContractValidationError("principal_not_sealed")
        if type(grant_envelope) is not MasterExecutionGrantEnvelope:
            raise ContractValidationError("invalid_grant_envelope")
        if type(ledger_envelope) is not MasterExecutionLedgerEnvelope:
            raise ContractValidationError("invalid_ledger_envelope")
        if not isinstance(invocation, MasterInvocationAuthorization):
            raise ContractValidationError("forged_invocation_rejected")
        seal = self._adapter.nonproduction_seal
        with self._lock:
            try:
                context = self._adapter.require_offline_dispatchable(
                    invocation,
                    now_utc=now_utc,
                    seal=seal,
                )
            except ContractValidationError as exc:
                if exc.errors == ("duplicate_dispatch",):
                    raise ContractValidationError("duplicate_claim") from exc
                raise
            verification = self._require_consumed_grant(context, grant, principal, grant_envelope)
            self._require_ledger_identity(context, ledger_envelope)
            worker = self._workers.get(invocation.specialist)
            if worker is None:
                raise ContractValidationError("test_worker_not_registered")
            fence = self._require_started_fence(
                ledger_envelope,
                owner_id=owner_id,
                attempt_token=attempt_token,
                fence_epoch=fence_epoch,
                now_utc=now_utc,
            )
            observation = worker.observe(context, invocation)
            if type(observation) is not ScriptedWorkerObservation:
                raise ContractValidationError("worker_observation_rejected")
            outcome = self._adapter.record_offline_scripted_attempt(
                invocation,
                observation,
                now_utc=now_utc,
                seal=seal,
            )
            if outcome.native_specialist_executed or outcome.ma4_admission_performed:
                raise ContractValidationError("specialist_execution_not_granted")
            if outcome.scheduling_complete_is_specialist_success:
                raise ContractValidationError("scheduling_is_not_execution")
            nonce = fence.grant_nonce
            if not isinstance(nonce, str):
                raise ContractValidationError("invalid_ledger_result")
            self.last_verification = verification
            self.last_fence = fence
            self.last_confirmation = OfflineFenceConfirmation(
                invocation_id=context.invocation_id,
                attempt_token=attempt_token,
                owner_id=owner_id,
                fence_epoch=fence_epoch,
                ledger_grant_nonce=nonce,
                grant_consumed=True,
                fence_replayed=True,
            )
            return outcome

    def note_ledger_complete(
        self,
        ledger_envelope: object,
        *,
        owner_id: str,
        attempt_token: str,
        fence_epoch: int,
        now_utc: str,
    ) -> MasterExecutionLedgerResult:
        """Record ledger completion. The result does not execute a specialist."""

        if type(ledger_envelope) is not MasterExecutionLedgerEnvelope:
            raise ContractValidationError("invalid_ledger_envelope")
        result = self._execution_ledger.complete(
            ledger_envelope,
            owner_id=owner_id,
            attempt_token=attempt_token,
            fence_epoch=fence_epoch,
            now_utc=now_utc,
        )
        if (
            result.specialist_executed
            or result.specialist_execution_permitted
            or result.dispatch_permitted
            or result.grant_nonce_is_production_grant
        ):
            raise ContractValidationError("execution_success_not_established")
        return result

    def handoff(self, now_utc: str) -> Ma4SchedulerHandoff:
        """Build the MA4 descriptor. Admission is not performed."""

        handoff = self._adapter.handoff_to_ma4(now_utc)
        note = observe_local_scheduler_protocol(handoff)
        if (
            note.native_specialist_executed
            or note.evidence_admitted
            or note.aviation_evidence
            or note.production_host_active
            or handoff.admission_performed
            or handoff.evidence_verified
            or handoff.aviation_evidence
            or handoff.specialist_runners_invoked
        ):
            raise ContractValidationError("ma4_admission_not_performed")
        try:
            invoke_specialist(handoff)
        except ContractValidationError as exc:
            if exc.errors != ("specialist_invocation_not_granted",):
                raise
        else:
            raise ContractValidationError("specialist_execution_not_granted")
        return handoff

    def _require_consumed_grant(
        self,
        context: BoundedDispatchContext,
        grant: object,
        principal: object,
        envelope: object,
    ) -> GrantVerification:
        verification = self._issuer.verify(
            grant,
            **retained_grant_bindings(context, envelope, principal),
        )
        if (
            verification.dispatch_permitted
            or verification.specialist_dispatched
            or verification.execution_permitted
            or verification.establishes_authority
        ):
            raise ContractValidationError("execution_authority_not_granted")
        if (
            verification.reason is GrantRejectionReason.REPLAY
            and verification.consumed is True
            and verification.signature_intact is True
            and verification.local_harness_binding_accepted is False
        ):
            return verification
        if verification.local_harness_binding_accepted is True and verification.consumed is False:
            raise ContractValidationError("consumed_grant_required")
        if verification.reason is not None:
            raise ContractValidationError(verification.reason.value)
        raise ContractValidationError("consumed_grant_required")

    def _require_ledger_identity(
        self,
        context: BoundedDispatchContext,
        envelope: MasterExecutionLedgerEnvelope,
    ) -> None:
        if envelope.master_request_id != context.master_request_id:
            raise ContractValidationError("master_request_mismatch")
        if envelope.planning_session_id != context.planning_session_id:
            raise ContractValidationError("session_mismatch")
        if envelope.step_id != context.step_id or envelope.invocation_id != context.invocation_id:
            raise ContractValidationError("request_identity_mismatch")
        if envelope.specialist is not context.specialist or envelope.purpose is not context.purpose:
            raise ContractValidationError("request_identity_mismatch")
        if envelope.capability is not context.capability:
            raise ContractValidationError("request_identity_mismatch")
        if envelope.correlation_id != context.correlation_id:
            raise ContractValidationError("correlation_mismatch")
        if envelope.accepted_at_utc != context.accepted_at_utc:
            raise ContractValidationError("application_time_mismatch")
        if envelope.deadline_utc != context.deadline_utc:
            raise ContractValidationError("deadline_mismatch")
        if envelope.max_model_turns > context.max_model_turns or envelope.max_tool_calls > context.max_tool_calls:
            raise ContractValidationError("server_budget_exceeded")
        window = context.historical_window
        if context.specialist is MasterSpecialist.HISTORICAL_ANALYTICS:
            if window is None:
                raise ContractValidationError("missing_historical_interval")
            if envelope.historical_start_utc != window[0] or envelope.historical_end_utc != window[1]:
                raise ContractValidationError("historical_window_mismatch")
        elif envelope.historical_start_utc is not None or envelope.historical_end_utc is not None:
            raise ContractValidationError("contradictory_temporal_bounds")
        if context.specialist is MasterSpecialist.DECISION:
            if envelope.decision_target_scope != context.decision_target_scope:
                raise ContractValidationError("decision_target_mismatch")
        elif envelope.decision_target_scope in self._decision_target_labels():
            raise ContractValidationError("decision_target_not_applicable")

    def _decision_target_labels(self) -> set[str]:
        labels: set[str] = set()
        for step in self._adapter.fanout.plan.steps:
            aircraft = step.invocation.expected_aircraft_id
            recommendation = step.invocation.expected_recommendation_id
            if isinstance(aircraft, str):
                labels.add(aircraft)
            if isinstance(recommendation, str):
                labels.add(recommendation)
        return labels

    def _require_started_fence(
        self,
        envelope: MasterExecutionLedgerEnvelope,
        *,
        owner_id: str,
        attempt_token: str,
        fence_epoch: int,
        now_utc: str,
    ) -> MasterExecutionLedgerResult:
        result = self._execution_ledger.mark_started(
            envelope,
            owner_id=owner_id,
            attempt_token=attempt_token,
            fence_epoch=fence_epoch,
            now_utc=now_utc,
        )
        if (
            result.dispatch_permitted
            or result.specialist_executed
            or result.specialist_execution_permitted
            or result.grant_nonce_is_production_grant
        ):
            raise ContractValidationError("execution_authority_not_granted")
        if result.outcome is MasterExecutionLedgerOutcome.CLAIMED:
            self._execution_ledger.fail(
                envelope,
                owner_id=owner_id,
                attempt_token=attempt_token,
                fence_epoch=fence_epoch,
                now_utc=now_utc,
                reason="fence_not_started",
            )
            raise ContractValidationError("mark_started_fence_required")
        if (
            result.outcome is MasterExecutionLedgerOutcome.REPLAYED
            and result.state is MasterExecutionLedgerState.STARTED
            and result.fresh_transition is False
            and result.replayed is True
            and result.attempt_token == attempt_token
            and result.fence_epoch == fence_epoch
            and result.owner_id == owner_id
        ):
            return result
        if result.rejection is MasterExecutionLedgerRejection.FENCED_OUT:
            raise ContractValidationError("stale_fence")
        if result.rejection is MasterExecutionLedgerRejection.OWNER_MISMATCH:
            raise ContractValidationError("owner_mismatch")
        if result.rejection is MasterExecutionLedgerRejection.CONFLICTING_DUPLICATE:
            raise ContractValidationError("conflicting_ledger_binding")
        if result.rejection is MasterExecutionLedgerRejection.DEADLINE_EXPIRED:
            raise ContractValidationError("deadline_expired")
        raise ContractValidationError("ledger_fence_rejected")
