"""Sprint 3C Agent D: native specialist orchestration after consumed authority.

This module is the I4 dispatcher. It binds the original Master invocation
identity to one specialist, accepts dispatch only after a consumed grant and
a current fence, and calls the native Live Ops ``run`` with an injected
provider. Historical finalization and Decision request construction stay at
their existing gates. Production dispatch stays disabled.

``LiveOpsSpecialist.run(request, context)``,
``HistoricalAnalyticsSpecialist.run(request, context)``, and
``DecisionSpecialist.run(request, runtime)`` are different types. A shared
envelope is never passed to them. ``ScriptedTestWorker`` success is not
native execution. Native success is not authenticated source evidence.
"""

from __future__ import annotations

import functools
import inspect
import types
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from threading import Lock
from typing import Any, get_type_hints

from wilvor_ai.contracts import (
    PROVENANCE_TOKEN_MAX_LENGTH,
    AgentCapability,
    ContractValidationError,
    _validate_bounded_text,
    _validate_utc,
)
from wilvor_ai.decision_specialist import DecisionSpecialist
from wilvor_ai.decision_specialist_runtime_contracts import DecisionSpecialistRequest
from wilvor_ai.decision_tools import DecisionToolsRuntime
from wilvor_ai.historical_specialist import HistoricalAnalyticsSpecialist
from wilvor_ai.live_ops_public_contracts import (
    LiveOpsRunRequest,
    LiveOpsRunResult,
    LiveOpsTrustedContext,
)
from wilvor_ai.live_ops_specialist import LiveOpsSpecialist
from wilvor_ai.master_authorized_scheduler_adapter import (
    PRODUCTION_DISPATCH_ENABLED,
    BoundedDispatchContext,
    ConsumedHostDispatchGrant,
    ConsumedLedgerReservation,
    HostDispatchGrant,
    LedgerReservation,
    LocalTestHostAuthoritySeam,
    MasterAuthorizedSchedulerAdapter,
    SchedulerInvocationOutcome,
    ScriptedTestWorker,
    ScriptedWorkerObservation,
)
from wilvor_ai.master_contracts import (
    MasterInvocationAuthorization,
    MasterProductionAuthority,
    MasterPurpose,
    MasterSpecialist,
)
from wilvor_ai.master_decision_binding import MasterDecisionBinding
from wilvor_ai.master_decision_execution_readiness import (
    MasterDecisionExecutionReadiness,
)
from wilvor_ai.master_decision_target_resolver import (
    REQUIRED_HOST_PROOFS,
    FakeDecisionReadCatalog,
    HostProofRequirement,
    MasterDecisionTargetResolution,
    resolve_master_decision_target,
)
from wilvor_ai.master_evidence_admission import MasterSourceAuthenticity
from wilvor_ai.master_execution_grants import (
    GrantConsumption,
    GrantRejectionReason,
    LocalHarnessExecutionGrant,
    LocalHarnessGrantIssuer,
    LocalHarnessPrincipal,
    MasterExecutionGrantEnvelope,
    ParsedExecutionGrantClaim,
    parse_execution_grant,
)
from wilvor_ai.master_execution_ledger import (
    MasterExecutionAttemptFenceCheck,
    MasterExecutionLedger,
    MasterExecutionLedgerEnvelope,
    MasterExecutionLedgerRejection,
    MasterExecutionLedgerState,
)
from wilvor_ai.master_historical_binding import (
    HISTORICAL_FINALIZATION_ENTRYPOINT,
    MasterHistoricalBinding,
)
from wilvor_ai.master_historical_execution_readiness import (
    historical_native_result_correlation_gaps,
)
from wilvor_ai.master_historical_native_bridge import (
    MasterHistoricalNativeBridgeEnvelope,
    correlate_master_historical_native_result,
    prepare_master_historical_native_invocation,
)
from wilvor_ai.master_host_scheduler_integration import retained_grant_bindings
from wilvor_ai.master_live_ops_binding import MasterLiveOpsBinding
from wilvor_ai.master_live_ops_execution_readiness import LIVE_OPS_READ_ONLY_TOOL_NAMES
from wilvor_ai.master_live_ops_native_bridge import (
    MasterLiveOpsNativeBridge,
    MasterLiveOpsNativeResult,
    accept_native_live_ops_result,
    prepare_master_live_ops_native_bridge,
    reject_unauthorized_selectors,
)
from wilvor_ai.master_read_resource_guard import server_read_budget
from wilvor_ai.master_routing import MasterRouteExecutionAuthority
from wilvor_ai.master_specialist_runtime_contract import invoke_specialist
from wilvor_ai.specialist_contracts import (
    HistoricalSpecialistTrustedContext,
    SpecialistRequest,
)
from wilvor_ai.specialist_runtime_contracts import HistoricalSpecialistRunResult


SPRINT3C_SPECIALIST_ORCHESTRATION_SCHEMA_VERSION = (
    "wilvor.ai.sprint3c.specialist_orchestration.v1"
)
NATIVE_PRODUCTION_DISPATCH_AVAILABLE = False
NATIVE_OFFLINE_EXECUTION_IS_PRODUCTION_DISPATCH = False
SCRIPTED_WORKER_IS_NATIVE_EXECUTION = False
NATIVE_EXECUTION_IS_SOURCE_EVIDENCE = False

_NETWORK_PROVIDER_MARKERS = (
    "anthropic",
    "boto3",
    "botocore",
    "openai",
    "langgraph",
)
_LIVE_OPS_ALLOWED_TOOLS = frozenset(LIVE_OPS_READ_ONLY_TOOL_NAMES)
_IDENTITY_FIELDS = (
    "master_request_id",
    "planning_session_id",
    "step_id",
    "invocation_id",
    "specialist_request_id",
    "correlation_id",
)


def _fail(code: str) -> None:
    raise ContractValidationError(code)


def _closed(value: object, denied: object, code: str) -> None:
    if value is not denied:
        _fail(code)


def _token(value: object, field_name: str) -> str:
    errors = _validate_bounded_text(
        value,
        field_name,
        max_length=PROVENANCE_TOKEN_MAX_LENGTH,
    )
    if errors:
        raise ContractValidationError(errors)
    if not isinstance(value, str) or value != value.strip() or any(
        character.isspace() for character in value
    ):
        _fail(f"invalid_{field_name}")
    return value


def _whole_second(value: object, field_name: str) -> datetime:
    if not isinstance(value, str) or len(value) != 20 or value[10] != "T" or not value.endswith("Z"):
        _fail(f"invalid_{field_name}")
    errors = _validate_utc(value, field_name)
    if errors:
        raise ContractValidationError(errors)
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError as exc:
        raise ContractValidationError(f"invalid_{field_name}") from exc


def _whole_second_epoch(value: str) -> int:
    return int(_whole_second(value, "utc").timestamp())


def _parameter_names(method: Any) -> tuple[str, ...]:
    names = tuple(inspect.signature(method).parameters)
    if names and names[0] == "self":
        names = names[1:]
    return names


def _resolved_annotation(method: Any, name: str) -> object:
    hints = get_type_hints(method)
    if name not in hints:
        _fail("native_signature_mismatch")
    return hints[name]


def _reject_model_callable(value: object) -> None:
    if isinstance(
        value,
        (types.FunctionType, types.MethodType, types.BuiltinFunctionType, functools.partial),
    ):
        _fail("model_origin_dispatch_rejected")
    if isinstance(value, (str, bytes, bytearray, bool, int, float, type, Mapping)):
        _fail("model_origin_dispatch_rejected")


def _reject_network_or_scripted_provider(provider: object) -> None:
    _reject_model_callable(provider)
    if type(provider) is ScriptedTestWorker:
        _fail("model_origin_dispatch_rejected")
    if not callable(getattr(provider, "complete", None)):
        _fail("model_origin_dispatch_rejected")
    module = getattr(type(provider), "__module__", "") or ""
    lowered = module.casefold()
    if any(marker in lowered for marker in _NETWORK_PROVIDER_MARKERS):
        _fail("model_origin_dispatch_rejected")


def _reject_untrusted_grant(value: object) -> None:
    _reject_model_callable(value)
    if isinstance(value, (str, bytes, bytearray)):
        _fail("token_not_a_grant")
    if isinstance(value, (bool, int, float)):
        _fail("authorized_flag_rejected")
    if isinstance(value, Mapping) or type(value) is ParsedExecutionGrantClaim:
        _fail("serialized_grant_rejected")
    if type(value) is GrantConsumption:
        _fail("forged_grant_rejected")
    if type(value) is HostDispatchGrant:
        _fail("grant_not_consumed")
    if type(value) is LedgerReservation:
        _fail("reservation_not_consumed")
    if type(value) not in {LocalHarnessExecutionGrant, ConsumedHostDispatchGrant} and (
        getattr(value, "authorized", None) is True
        or getattr(value, "dispatch_permitted", None) is True
    ):
        _fail("authorized_flag_rejected")
    if type(value) not in {LocalHarnessExecutionGrant, ConsumedHostDispatchGrant}:
        _fail("forged_grant_rejected")


def _restrict_live_ops_handlers(handlers: object) -> Mapping[str, Callable[..., object]] | None:
    if handlers is None:
        return None
    if isinstance(handlers, (str, bytes, bytearray)) or not isinstance(handlers, Mapping):
        _fail("model_origin_dispatch_rejected")
    restricted: dict[str, Callable[..., object]] = {}
    for name, handler in handlers.items():
        if name not in _LIVE_OPS_ALLOWED_TOOLS:
            _fail("model_origin_dispatch_rejected")
        if not callable(handler):
            _fail("model_origin_dispatch_rejected")
        restricted[name] = handler
    return restricted


def _build_native_specialist_signatures() -> "NativeSpecialistSignatureCatalog":
    """Read the three native ``run`` signatures from code. Do not guess them."""

    live_ops = _parameter_names(LiveOpsSpecialist.run)
    historical = _parameter_names(HistoricalAnalyticsSpecialist.run)
    decision = _parameter_names(DecisionSpecialist.run)
    if live_ops != ("request", "context"):
        _fail("native_signature_mismatch")
    if historical != ("request", "context"):
        _fail("native_signature_mismatch")
    if decision != ("request", "runtime"):
        _fail("native_signature_mismatch")
    if _resolved_annotation(LiveOpsSpecialist.run, "request") is not LiveOpsRunRequest:
        _fail("native_signature_mismatch")
    if _resolved_annotation(LiveOpsSpecialist.run, "context") is not LiveOpsTrustedContext:
        _fail("native_signature_mismatch")
    if _resolved_annotation(HistoricalAnalyticsSpecialist.run, "request") is not SpecialistRequest:
        _fail("native_signature_mismatch")
    if _resolved_annotation(HistoricalAnalyticsSpecialist.run, "context") is not HistoricalSpecialistTrustedContext:
        _fail("native_signature_mismatch")
    if _resolved_annotation(DecisionSpecialist.run, "request") is not DecisionSpecialistRequest:
        _fail("native_signature_mismatch")
    if _resolved_annotation(DecisionSpecialist.run, "runtime") is not DecisionToolsRuntime:
        _fail("native_signature_mismatch")
    return NativeSpecialistSignatureCatalog(
        live_ops_run_parameters=live_ops,
        live_ops_request_type=LiveOpsRunRequest.__name__,
        live_ops_context_type=LiveOpsTrustedContext.__name__,
        historical_run_parameters=historical,
        historical_request_type=SpecialistRequest.__name__,
        historical_context_type=HistoricalSpecialistTrustedContext.__name__,
        historical_intermediate_result_type=HistoricalSpecialistRunResult.__name__,
        historical_finalization_entrypoint=HISTORICAL_FINALIZATION_ENTRYPOINT,
        decision_run_parameters=decision,
        decision_request_type=DecisionSpecialistRequest.__name__,
        decision_runtime_type=DecisionToolsRuntime.__name__,
        required_decision_proofs=REQUIRED_HOST_PROOFS,
    )


@dataclass(frozen=True)
class NativeSpecialistSignatureCatalog:
    """Exact native entrypoints. A shared envelope is not one of these."""

    live_ops_run_parameters: tuple[str, ...]
    live_ops_request_type: str
    live_ops_context_type: str
    historical_run_parameters: tuple[str, ...]
    historical_request_type: str
    historical_context_type: str
    historical_intermediate_result_type: str
    historical_finalization_entrypoint: str
    decision_run_parameters: tuple[str, ...]
    decision_request_type: str
    decision_runtime_type: str
    required_decision_proofs: tuple[HostProofRequirement, ...]
    schema_version: str = SPRINT3C_SPECIALIST_ORCHESTRATION_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != SPRINT3C_SPECIALIST_ORCHESTRATION_SCHEMA_VERSION:
            _fail("invalid_schema_version")
        if self.live_ops_run_parameters != ("request", "context"):
            _fail("native_signature_mismatch")
        if self.historical_run_parameters != ("request", "context"):
            _fail("native_signature_mismatch")
        if self.decision_run_parameters != ("request", "runtime"):
            _fail("native_signature_mismatch")
        if self.historical_finalization_entrypoint != HISTORICAL_FINALIZATION_ENTRYPOINT:
            _fail("invalid_finalization_entrypoint")
        if self.required_decision_proofs != REQUIRED_HOST_PROOFS:
            _fail("caller_proof_not_host_issued")
        if self.live_ops_request_type == self.historical_request_type:
            _fail("native_signature_mismatch")
        if self.decision_runtime_type == self.live_ops_context_type:
            _fail("native_signature_mismatch")


NATIVE_SPECIALIST_SIGNATURES = _build_native_specialist_signatures()


def inspect_native_specialist_signatures() -> NativeSpecialistSignatureCatalog:
    """Return the inspected catalog. Rebuilds must match the module constant."""

    catalog = _build_native_specialist_signatures()
    if catalog != NATIVE_SPECIALIST_SIGNATURES:
        _fail("native_signature_mismatch")
    return NATIVE_SPECIALIST_SIGNATURES


@dataclass(frozen=True)
class Sprint3CBoundInvocationIdentity:
    """Original Master identity bound to one specialist. Not a grant."""

    master_request_id: str
    planning_session_id: str
    step_id: str
    invocation_id: str
    specialist_request_id: str
    correlation_id: str
    specialist: MasterSpecialist
    purpose: MasterPurpose
    capability: AgentCapability
    accepted_at_utc: str
    deadline_utc: str
    idempotency_key: str
    max_model_turns: int
    max_tool_calls: int
    schema_version: str = SPRINT3C_SPECIALIST_ORCHESTRATION_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != SPRINT3C_SPECIALIST_ORCHESTRATION_SCHEMA_VERSION:
            _fail("invalid_schema_version")
        for name in _IDENTITY_FIELDS:
            _token(getattr(self, name), name)
        _token(self.idempotency_key, "idempotency_key")
        identities = tuple(getattr(self, name) for name in _IDENTITY_FIELDS)
        if len(set(identities)) != 6:
            _fail("request_identity_not_distinct")
        if self.idempotency_key in identities:
            _fail("request_identity_not_distinct")
        if not isinstance(self.specialist, MasterSpecialist):
            _fail("invalid_specialist")
        if not isinstance(self.purpose, MasterPurpose):
            _fail("invalid_purpose")
        if not isinstance(self.capability, AgentCapability):
            _fail("invalid_capability")
        _whole_second(self.accepted_at_utc, "accepted_at_utc")
        _whole_second(self.deadline_utc, "deadline_utc")
        executions, turns, calls = server_read_budget(self.specialist)
        if self.max_model_turns != turns or self.max_tool_calls != calls:
            _fail("server_budget_exceeded")
        if executions < 1:
            _fail("server_budget_exceeded")


@dataclass(frozen=True)
class Sprint3CConsumedDispatchAuthority:
    """Consumed grant plus the current fence. Possession is not production dispatch."""

    grant: object
    owner_id: str
    attempt_token: str
    fence_epoch: int
    grant_nonce: str
    principal: LocalHarnessPrincipal | None = None
    grant_envelope: MasterExecutionGrantEnvelope | None = None
    ledger_envelope: MasterExecutionLedgerEnvelope | None = None
    consumed_reservation: object | None = None
    idempotency_key: str | None = None
    payload_fingerprint: str | None = None
    ledger_state: MasterExecutionLedgerState | None = MasterExecutionLedgerState.STARTED
    distributed_exactly_once: bool = False
    production_dispatch: bool = False

    def __post_init__(self) -> None:
        _reject_untrusted_grant(self.grant)
        _token(self.owner_id, "owner_id")
        _token(self.attempt_token, "attempt_token")
        _token(self.grant_nonce, "grant_nonce")
        if type(self.fence_epoch) is not int or self.fence_epoch < 1:
            _fail("invalid_fence_epoch")
        _closed(self.distributed_exactly_once, False, "execution_authority_not_granted")
        _closed(self.production_dispatch, False, "production_dispatch_disabled")
        if self.principal is not None and type(self.principal) is not LocalHarnessPrincipal:
            _fail("principal_not_sealed")
        if self.grant_envelope is not None and type(self.grant_envelope) is not MasterExecutionGrantEnvelope:
            _fail("invalid_grant_envelope")
        if self.ledger_envelope is not None and type(self.ledger_envelope) is not MasterExecutionLedgerEnvelope:
            _fail("invalid_ledger_envelope")
        if self.consumed_reservation is not None and type(self.consumed_reservation) not in {
            ConsumedLedgerReservation,
            type(None),
        }:
            if type(self.consumed_reservation) is LedgerReservation:
                _fail("reservation_not_consumed")
        if self.idempotency_key is not None:
            _token(self.idempotency_key, "idempotency_key")
        if self.payload_fingerprint is not None:
            _token(self.payload_fingerprint, "payload_fingerprint")
        if (
            self.ledger_state is not None
            and self.ledger_state is not MasterExecutionLedgerState.STARTED
        ):
            _fail("mark_started_fence_required")


@dataclass(frozen=True)
class Sprint3CLiveOpsRuntimeInjection:
    """Injected Live Ops runtime. Not a network client and not a production grant."""

    provider: object
    tables: object
    now_epoch: int
    handlers: Mapping[str, Callable[..., object]] | None = None
    clock: Callable[[], int] | None = None
    deadline_epoch: int | None = None
    cancelled: Callable[[], bool] | None = None
    tool_call_id_factory: Callable[[], str] | None = None

    def __post_init__(self) -> None:
        _reject_network_or_scripted_provider(self.provider)
        if self.tables is None:
            _fail("invalid_tables")
        if type(self.now_epoch) is not int:
            _fail("invalid_source_observation_epoch")
        _restrict_live_ops_handlers(self.handlers)
        if self.clock is not None and not callable(self.clock):
            _fail("invalid_clock")
        if self.cancelled is not None and not callable(self.cancelled):
            _fail("invalid_cancelled")
        if self.deadline_epoch is not None:
            if type(self.deadline_epoch) is not int:
                _fail("invalid_deadline_epoch")
            if self.clock is None:
                _fail("deadline_requires_clock")
            if self.deadline_epoch < self.now_epoch:
                _fail("deadline_before_reference_clock")
        if self.tool_call_id_factory is not None and not callable(self.tool_call_id_factory):
            _fail("model_origin_dispatch_rejected")


@dataclass(frozen=True)
class Sprint3CLiveOpsNativeExecution:
    """Native Live Ops result kept beside host identity.

    The native object is the original ``LiveOpsRunResult``. Mapping it does
    not authenticate a source. Offline ``run`` is not production dispatch.
    """

    identity: Sprint3CBoundInvocationIdentity
    bridge: MasterLiveOpsNativeBridge
    native_result: LiveOpsRunResult
    mapped: MasterLiveOpsNativeResult
    live_ops_run_invoked: bool = True
    scripted_worker_used: bool = False
    production_dispatch: bool = False
    distributed_exactly_once: bool = False
    native_execution_is_source_evidence: bool = False
    source_authenticity: MasterSourceAuthenticity = MasterSourceAuthenticity.NOT_ESTABLISHED
    execution_authority: MasterRouteExecutionAuthority = MasterRouteExecutionAuthority.DENIED
    production_authority: MasterProductionAuthority = MasterProductionAuthority.DENIED
    schema_version: str = SPRINT3C_SPECIALIST_ORCHESTRATION_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != SPRINT3C_SPECIALIST_ORCHESTRATION_SCHEMA_VERSION:
            _fail("invalid_schema_version")
        if not isinstance(self.identity, Sprint3CBoundInvocationIdentity):
            _fail("request_identity_mismatch")
        if not isinstance(self.bridge, MasterLiveOpsNativeBridge):
            _fail("invalid_live_ops_bridge")
        if not isinstance(self.native_result, LiveOpsRunResult):
            _fail("invalid_live_ops_result")
        if not isinstance(self.mapped, MasterLiveOpsNativeResult):
            _fail("invalid_live_ops_result")
        if self.mapped.native_result is not self.native_result:
            _fail("verified_claims_not_native")
        if self.mapped.bridge is not self.bridge:
            _fail("invalid_live_ops_bridge")
        if self.identity.specialist is not MasterSpecialist.LIVE_OPS:
            _fail("specialist_mismatch")
        if self.identity.invocation_id != self.bridge.invocation_id:
            _fail("invocation_binding_mismatch")
        if self.identity.specialist_request_id != self.bridge.specialist_request_id:
            _fail("specialist_request_id_mismatch")
        if self.identity.correlation_id != self.bridge.correlation_id:
            _fail("correlation_mismatch")
        if self.identity.master_request_id != self.bridge.master_request_id:
            _fail("master_request_mismatch")
        _closed(self.live_ops_run_invoked, True, "specialist_execution_not_granted")
        _closed(self.scripted_worker_used, False, "specialist_execution_not_granted")
        _closed(self.production_dispatch, False, "production_dispatch_disabled")
        _closed(self.distributed_exactly_once, False, "execution_authority_not_granted")
        _closed(
            self.native_execution_is_source_evidence,
            False,
            "source_authenticity_not_established",
        )
        _closed(
            self.source_authenticity,
            MasterSourceAuthenticity.NOT_ESTABLISHED,
            "source_authenticity_not_established",
        )
        _closed(
            self.execution_authority,
            MasterRouteExecutionAuthority.DENIED,
            "execution_authority_not_granted",
        )
        _closed(
            self.production_authority,
            MasterProductionAuthority.DENIED,
            "production_authority_not_issued",
        )
        _closed(self.mapped.native_execution_is_source_evidence, False, "source_authenticity_not_established")
        _closed(self.mapped.specialist_invoked_by_bridge, False, "execution_not_granted")


@dataclass(frozen=True)
class Sprint3CHistoricalNativePath:
    """Historical correlation stopped before finalization."""

    identity: Sprint3CBoundInvocationIdentity
    envelope: MasterHistoricalNativeBridgeEnvelope
    native_run_invoked: bool = False
    finalization_performed: bool = False
    evidence_issued: bool = False
    production_dispatch: bool = False
    schema_version: str = SPRINT3C_SPECIALIST_ORCHESTRATION_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != SPRINT3C_SPECIALIST_ORCHESTRATION_SCHEMA_VERSION:
            _fail("invalid_schema_version")
        if not isinstance(self.identity, Sprint3CBoundInvocationIdentity):
            _fail("request_identity_mismatch")
        if not isinstance(self.envelope, MasterHistoricalNativeBridgeEnvelope):
            _fail("invalid_historical_binding")
        if self.identity.specialist is not MasterSpecialist.HISTORICAL_ANALYTICS:
            _fail("specialist_mismatch")
        if self.identity.invocation_id != self.envelope.invocation_id:
            _fail("invocation_binding_mismatch")
        if self.identity.specialist_request_id != self.envelope.specialist_request_id:
            _fail("specialist_request_id_mismatch")
        _closed(self.native_run_invoked, False, "execution_not_granted")
        _closed(self.finalization_performed, False, "historical_finalization_not_performed")
        _closed(self.evidence_issued, False, "verified_evidence_not_issued")
        _closed(self.production_dispatch, False, "production_dispatch_disabled")
        _closed(self.envelope.finalization_performed, False, "historical_finalization_not_performed")
        _closed(self.envelope.evidence_issued, False, "verified_evidence_not_issued")


@dataclass(frozen=True)
class Sprint3CDecisionNativePath:
    """Decision target resolution stopped before native request construction."""

    identity: Sprint3CBoundInvocationIdentity
    resolution: MasterDecisionTargetResolution
    native_run_invoked: bool = False
    decision_request: None = None
    native_request_built: bool = False
    production_dispatch: bool = False
    schema_version: str = SPRINT3C_SPECIALIST_ORCHESTRATION_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != SPRINT3C_SPECIALIST_ORCHESTRATION_SCHEMA_VERSION:
            _fail("invalid_schema_version")
        if not isinstance(self.identity, Sprint3CBoundInvocationIdentity):
            _fail("request_identity_mismatch")
        if not isinstance(self.resolution, MasterDecisionTargetResolution):
            _fail("invalid_decision_target_resolution")
        if self.identity.specialist is not MasterSpecialist.DECISION:
            _fail("specialist_mismatch")
        _closed(self.native_run_invoked, False, "execution_not_granted")
        _closed(self.decision_request, None, "execution_not_granted")
        _closed(self.native_request_built, False, "execution_not_granted")
        _closed(self.production_dispatch, False, "production_dispatch_disabled")
        _closed(self.resolution.decision_request, None, "execution_not_granted")
        _closed(self.resolution.native_request_built, False, "execution_not_granted")
        if self.resolution.host_proofs_satisfied != ():
            _fail("caller_proof_not_host_issued")
        if self.resolution.host_proofs_required != REQUIRED_HOST_PROOFS:
            _fail("caller_proof_not_host_issued")


def refuse_scripted_worker_as_native(observation: object) -> None:
    """A scripted worker note is not ``LiveOpsSpecialist.run``."""

    if type(observation) in {ScriptedTestWorker, ScriptedWorkerObservation, SchedulerInvocationOutcome}:
        _fail("specialist_execution_not_granted")
    _fail("specialist_execution_not_granted")


def refuse_historical_finalization(*_args: object, **_kwargs: object) -> None:
    """Historical finalization stays blocked until correlation gaps are resolved."""

    _fail("historical_finalization_not_performed")


def refuse_decision_native_request(*_args: object, **_kwargs: object) -> None:
    """Decision request construction stays blocked until the three host proofs exist."""

    _fail("execution_not_granted")


def authority_from_durable_attempt(
    grant: object,
    attempt: object,
    *,
    principal: LocalHarnessPrincipal,
    grant_envelope: MasterExecutionGrantEnvelope,
    ledger_envelope: MasterExecutionLedgerEnvelope,
) -> Sprint3CConsumedDispatchAuthority:
    """Bind Agent B's STARTED fence to dispatch. Consumption is not the fence.

    The local grant must be the object Agent B consumed. The ledger state
    stays ``STARTED``. A copied nonce or a ``CONSUMED`` execution label is
    rejected. This object does not permit production dispatch.
    """

    _reject_untrusted_grant(grant)
    attempt_type = type(attempt)
    if (
        attempt_type.__module__ != "wilvor_ai.sprint3c_grant_ledger"
        or attempt_type.__name__ != "DurableConsumedAttempt"
    ):
        _fail("consumed_grant_required")
    if attempt.state is not MasterExecutionLedgerState.STARTED:
        _fail("mark_started_fence_required")
    if (
        attempt.execution_permitted
        or attempt.dispatch_permitted
        or attempt.production_grant
        or attempt.distributed_exactly_once
        or attempt.specialist_executed
    ):
        _fail("execution_authority_not_granted")
    if type(principal) is not LocalHarnessPrincipal:
        _fail("principal_not_sealed")
    if type(grant_envelope) is not MasterExecutionGrantEnvelope:
        _fail("invalid_grant_envelope")
    if type(ledger_envelope) is not MasterExecutionLedgerEnvelope:
        _fail("invalid_ledger_envelope")
    if type(grant) is LocalHarnessExecutionGrant:
        nonce = parse_execution_grant(grant.serialized_claim()).nonce
    elif type(grant) is ParsedExecutionGrantClaim:
        nonce = grant.nonce
    else:
        _fail("invalid_grant_claim")
    if attempt.grant_nonce != nonce or ledger_envelope.principal_ref != principal.reference:
        _fail("forged_grant_rejected")
    if attempt.payload_fingerprint != ledger_envelope.fingerprint:
        _fail("conflicting_ledger_binding")
    pairs = (
        (attempt.master_request_id, grant_envelope.master_request_id, ledger_envelope.master_request_id),
        (attempt.planning_session_id, grant_envelope.planning_session_id, ledger_envelope.planning_session_id),
        (attempt.step_id, grant_envelope.step_id, ledger_envelope.step_id),
        (attempt.invocation_id, grant_envelope.invocation_id, ledger_envelope.invocation_id),
        (attempt.principal_ref, grant_envelope.principal_ref, ledger_envelope.principal_ref),
        (attempt.policy_revision, grant_envelope.policy_revision, ledger_envelope.policy_revision),
        (attempt.correlation_id, grant_envelope.correlation_id, ledger_envelope.correlation_id),
        (attempt.idempotency_key, grant_envelope.idempotency_key, ledger_envelope.idempotency_key),
        (attempt.accepted_at_utc, grant_envelope.accepted_at_utc, ledger_envelope.accepted_at_utc),
        (attempt.deadline_utc, grant_envelope.deadline_utc, ledger_envelope.deadline_utc),
    )
    for left, middle, right in pairs:
        if left != middle or middle != right:
            _fail("request_identity_mismatch")
    if attempt.specialist is not grant_envelope.specialist or attempt.specialist is not ledger_envelope.specialist:
        _fail("cross_specialist_execution_rejected")
    if attempt.purpose is not grant_envelope.purpose or attempt.purpose is not ledger_envelope.purpose:
        _fail("request_identity_mismatch")
    if principal.reference != attempt.principal_ref:
        _fail("forged_grant_rejected")
    return Sprint3CConsumedDispatchAuthority(
        grant=grant,
        owner_id=attempt.owner_id,
        attempt_token=attempt.attempt_token,
        fence_epoch=attempt.fence_epoch,
        grant_nonce=attempt.grant_nonce,
        principal=principal,
        grant_envelope=grant_envelope,
        ledger_envelope=ledger_envelope,
        idempotency_key=attempt.idempotency_key,
        payload_fingerprint=attempt.payload_fingerprint,
        ledger_state=MasterExecutionLedgerState.STARTED,
        distributed_exactly_once=False,
        production_dispatch=False,
    )


class Sprint3CNativeSpecialistOrchestrator:
    """Dispatch one retained specialist after consumed grant and current fence.

    Live Ops is the only specialist that calls native ``run`` in this slice.
    Historical and Decision stop at correlation and target-proof gates.
    """

    def __init__(
        self,
        adapter: MasterAuthorizedSchedulerAdapter,
        *,
        host_seam: LocalTestHostAuthoritySeam | None = None,
        issuer: LocalHarnessGrantIssuer | None = None,
        execution_ledger: MasterExecutionLedger | None = None,
    ) -> None:
        if PRODUCTION_DISPATCH_ENABLED is not False:
            _fail("production_dispatch_disabled")
        if NATIVE_PRODUCTION_DISPATCH_AVAILABLE is not False:
            _fail("production_dispatch_disabled")
        if type(adapter) is not MasterAuthorizedSchedulerAdapter:
            _fail("invalid_schedule")
        if adapter.production_dispatch_enabled is not False:
            _fail("production_dispatch_disabled")
        if adapter.nonproduction_seal is None:
            _fail("host_grant_authority_missing")
        if host_seam is not None and type(host_seam) is not LocalTestHostAuthoritySeam:
            _fail("host_grant_authority_missing")
        if issuer is not None and type(issuer) is not LocalHarnessGrantIssuer:
            _fail("host_grant_authority_missing")
        if execution_ledger is not None and type(execution_ledger) is not MasterExecutionLedger:
            _fail("ledger_authority_missing")
        if host_seam is None and issuer is None:
            _fail("consumed_grant_required")
        self._adapter = adapter
        self._seam = host_seam
        self._issuer = issuer
        self._ledger = execution_ledger
        self._lock = Lock()
        self._dispatched: set[str] = set()
        self._used_grants: set[int] = set()
        inspect_native_specialist_signatures()

    @property
    def production_dispatch_enabled(self) -> bool:
        return False

    @property
    def native_production_dispatch_available(self) -> bool:
        return False

    @property
    def durable_exactly_once(self) -> bool:
        return False

    def execute_live_ops(
        self,
        invocation: MasterInvocationAuthorization,
        binding: MasterLiveOpsBinding,
        authority: Sprint3CConsumedDispatchAuthority,
        runtime: Sprint3CLiveOpsRuntimeInjection,
        *,
        now_utc: str,
    ) -> Sprint3CLiveOpsNativeExecution:
        """Call ``LiveOpsSpecialist.run(request, context)`` after consumed authority."""

        if not isinstance(runtime, Sprint3CLiveOpsRuntimeInjection):
            _fail("model_origin_dispatch_rejected")
        identity, context = self._admit(
            invocation,
            authority,
            expected=MasterSpecialist.LIVE_OPS,
            now_utc=now_utc,
        )
        if not isinstance(binding, MasterLiveOpsBinding):
            _fail("invalid_live_ops_binding")
        self._require_binding_identity(identity, binding)
        if binding.request.selector_hints is not None or binding.request.entity_hints != ():
            _fail("selection_hints_not_trusted")
        if runtime.now_epoch == _whole_second_epoch(identity.accepted_at_utc):
            _fail("application_time_is_not_source_clock")
        self._fail_closed_before_run(identity, runtime, now_utc=now_utc)
        bridge = prepare_master_live_ops_native_bridge(binding)
        if bridge.request is not binding.request:
            _fail("invocation_binding_mismatch")
        if not isinstance(bridge.request, LiveOpsRunRequest):
            _fail("invalid_live_ops_request")
        reject_unauthorized_selectors(bridge)
        trusted = LiveOpsTrustedContext(
            tables=runtime.tables,
            now_epoch=runtime.now_epoch,
            correlation_id=identity.correlation_id,
            max_model_turns=identity.max_model_turns,
            max_tool_calls=identity.max_tool_calls,
            clock=runtime.clock,
            deadline_epoch=runtime.deadline_epoch,
            cancelled=runtime.cancelled,
        )
        specialist = LiveOpsSpecialist(
            provider=runtime.provider,
            tool_call_id_factory=runtime.tool_call_id_factory,
            handlers=_restrict_live_ops_handlers(runtime.handlers),
        )
        result = specialist.run(bridge.request, trusted)
        if not isinstance(result, LiveOpsRunResult):
            _fail("invalid_live_ops_result")
        mapped = accept_native_live_ops_result(
            bridge,
            result,
            source_observation_epoch=runtime.now_epoch,
        )
        if mapped.native_result is not result:
            _fail("verified_claims_not_native")
        return Sprint3CLiveOpsNativeExecution(
            identity=identity,
            bridge=bridge,
            native_result=result,
            mapped=mapped,
        )

    def correlate_historical(
        self,
        invocation: MasterInvocationAuthorization,
        binding: MasterHistoricalBinding,
        authority: Sprint3CConsumedDispatchAuthority,
        *,
        now_utc: str,
        native_run: HistoricalSpecialistRunResult | None = None,
    ) -> Sprint3CHistoricalNativePath:
        """Correlate Historical without calling ``run`` or the finalizer."""

        identity, _context = self._admit(
            invocation,
            authority,
            expected=MasterSpecialist.HISTORICAL_ANALYTICS,
            now_utc=now_utc,
        )
        if not isinstance(binding, MasterHistoricalBinding):
            _fail("invalid_historical_binding")
        if identity.invocation_id != binding.invocation_id:
            _fail("invocation_binding_mismatch")
        if identity.specialist_request_id != binding.specialist_request_id:
            _fail("specialist_request_id_mismatch")
        if native_run is not None:
            _fail("execution_not_granted")
        gaps = historical_native_result_correlation_gaps()
        if not gaps:
            _fail("invalid_native_result_gap")
        prepare_master_historical_native_invocation(binding)
        envelope = correlate_master_historical_native_result(binding)
        if envelope.finalization_performed is not False:
            _fail("historical_finalization_not_performed")
        return Sprint3CHistoricalNativePath(identity=identity, envelope=envelope)

    def resolve_decision(
        self,
        invocation: MasterInvocationAuthorization,
        readiness: MasterDecisionExecutionReadiness,
        reads: FakeDecisionReadCatalog,
        authority: Sprint3CConsumedDispatchAuthority,
        *,
        now_utc: str,
        binding: MasterDecisionBinding | None = None,
    ) -> Sprint3CDecisionNativePath:
        """Resolve a Decision target without building or running a native request."""

        identity, _context = self._admit(
            invocation,
            authority,
            expected=MasterSpecialist.DECISION,
            now_utc=now_utc,
        )
        if not isinstance(readiness, MasterDecisionExecutionReadiness):
            _fail("invalid_decision_readiness")
        if not isinstance(reads, FakeDecisionReadCatalog):
            _fail("invalid_decision_read")
        if binding is not None:
            if not isinstance(binding, MasterDecisionBinding):
                _fail("invalid_decision_binding")
            if identity.invocation_id != binding.invocation_id:
                _fail("invocation_binding_mismatch")
        resolution = resolve_master_decision_target(readiness, reads)
        if resolution.decision_request is not None:
            _fail("execution_not_granted")
        if resolution.host_proofs_satisfied != ():
            _fail("caller_proof_not_host_issued")
        return Sprint3CDecisionNativePath(identity=identity, resolution=resolution)

    def _admit(
        self,
        invocation: object,
        authority: object,
        *,
        expected: MasterSpecialist,
        now_utc: str,
    ) -> tuple[Sprint3CBoundInvocationIdentity, BoundedDispatchContext]:
        if PRODUCTION_DISPATCH_ENABLED is not False or NATIVE_PRODUCTION_DISPATCH_AVAILABLE is not False:
            _fail("production_dispatch_disabled")
        if not isinstance(authority, Sprint3CConsumedDispatchAuthority):
            _fail("consumed_grant_required")
        _whole_second(now_utc, "now_utc")
        if not isinstance(invocation, MasterInvocationAuthorization):
            _fail("forged_invocation_rejected")
        if invocation.specialist is not expected:
            _fail("specialist_mismatch")
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
                    raise ContractValidationError("duplicate_dispatch") from exc
                raise
            if context.specialist is not expected or invocation.specialist is not expected:
                _fail("cross_specialist_execution_rejected")
            if invocation.invocation_id in self._dispatched:
                _fail("duplicate_dispatch")
            identity = self._bind_identity(invocation, context)
            self._require_consumed_grant(authority, invocation, context)
            self._require_current_fence(authority, context)
            self._used_grants.add(id(authority.grant))
            self._dispatched.add(invocation.invocation_id)
            return identity, context

    def _bind_identity(
        self,
        invocation: MasterInvocationAuthorization,
        context: BoundedDispatchContext,
    ) -> Sprint3CBoundInvocationIdentity:
        if not isinstance(invocation.specialist_request_id, str):
            _fail("specialist_request_id_required")
        if context.invocation_id != invocation.invocation_id:
            _fail("request_identity_mismatch")
        if context.specialist is not invocation.specialist:
            _fail("specialist_mismatch")
        if context.purpose is not invocation.purpose:
            _fail("request_identity_mismatch")
        if context.master_request_id != invocation.master_request_id:
            _fail("master_request_mismatch")
        if context.correlation_id != invocation.correlation_id:
            _fail("correlation_mismatch")
        return Sprint3CBoundInvocationIdentity(
            master_request_id=context.master_request_id,
            planning_session_id=context.planning_session_id,
            step_id=context.step_id,
            invocation_id=context.invocation_id,
            specialist_request_id=invocation.specialist_request_id,
            correlation_id=context.correlation_id,
            specialist=context.specialist,
            purpose=context.purpose,
            capability=context.capability,
            accepted_at_utc=context.accepted_at_utc,
            deadline_utc=context.deadline_utc,
            idempotency_key=context.idempotency_key,
            max_model_turns=context.max_model_turns,
            max_tool_calls=context.max_tool_calls,
        )

    def _require_binding_identity(
        self,
        identity: Sprint3CBoundInvocationIdentity,
        binding: MasterLiveOpsBinding,
    ) -> None:
        if identity.master_request_id != binding.master_request_id:
            _fail("master_request_mismatch")
        if identity.invocation_id != binding.invocation_id:
            _fail("invocation_binding_mismatch")
        if identity.step_id != binding.step_id:
            _fail("invocation_binding_mismatch")
        if identity.specialist_request_id != binding.specialist_request_id:
            _fail("specialist_request_id_mismatch")
        if identity.correlation_id != binding.correlation_id:
            _fail("correlation_mismatch")
        if identity.specialist is not binding.specialist:
            _fail("specialist_mismatch")
        if identity.purpose is not binding.purpose:
            _fail("request_identity_mismatch")
        if identity.capability is not binding.capability:
            _fail("request_identity_mismatch")
        if identity.accepted_at_utc != binding.application_request_time_utc:
            _fail("application_time_mismatch")

    def _fail_closed_before_run(
        self,
        identity: Sprint3CBoundInvocationIdentity,
        runtime: Sprint3CLiveOpsRuntimeInjection,
        *,
        now_utc: str,
    ) -> None:
        if now_utc >= identity.deadline_utc:
            _fail("deadline_expired")
        if runtime.cancelled is not None:
            try:
                cancelled = runtime.cancelled()
            except Exception as exc:
                raise ContractValidationError("schedule_cancelled") from exc
            if cancelled is True:
                _fail("schedule_cancelled")
        if runtime.deadline_epoch is not None:
            observed = runtime.now_epoch if runtime.clock is None else runtime.clock()
            if type(observed) is not int:
                _fail("deadline_expired")
            if observed >= runtime.deadline_epoch:
                _fail("deadline_expired")

    def _require_consumed_grant(
        self,
        authority: Sprint3CConsumedDispatchAuthority,
        invocation: MasterInvocationAuthorization,
        context: BoundedDispatchContext,
    ) -> None:
        grant = authority.grant
        if id(grant) in self._used_grants:
            _fail("duplicate_dispatch")
        if type(grant) is ConsumedHostDispatchGrant:
            if self._seam is None:
                _fail("host_grant_authority_missing")
            self._seam.assert_consumed(grant, invocation, context)
            self._seam.mark_used(grant)
            return
        if type(grant) is not LocalHarnessExecutionGrant:
            _fail("forged_grant_rejected")
        if self._issuer is None:
            _fail("host_grant_authority_missing")
        if type(authority.principal) is not LocalHarnessPrincipal:
            _fail("principal_not_sealed")
        if type(authority.grant_envelope) is not MasterExecutionGrantEnvelope:
            _fail("invalid_grant_envelope")
        if authority.grant_envelope.specialist is not context.specialist:
            _fail("cross_specialist_execution_rejected")
        verification = self._issuer.verify(
            grant,
            **retained_grant_bindings(context, authority.grant_envelope, authority.principal),
        )
        if (
            verification.dispatch_permitted
            or verification.specialist_dispatched
            or verification.execution_permitted
            or verification.establishes_authority
        ):
            _fail("execution_authority_not_granted")
        if (
            verification.reason is GrantRejectionReason.REPLAY
            and verification.consumed is True
            and verification.signature_intact is True
            and verification.local_harness_binding_accepted is False
        ):
            return
        if verification.local_harness_binding_accepted is True and verification.consumed is False:
            _fail("consumed_grant_required")
        if verification.reason is not None:
            raise ContractValidationError(verification.reason.value)
        _fail("consumed_grant_required")

    def _require_current_fence(
        self,
        authority: Sprint3CConsumedDispatchAuthority,
        context: BoundedDispatchContext,
    ) -> MasterExecutionAttemptFenceCheck:
        if self._ledger is None or authority.ledger_envelope is None:
            _fail("mark_started_fence_required")
        envelope = authority.ledger_envelope
        if envelope.invocation_id != context.invocation_id:
            _fail("request_identity_mismatch")
        if envelope.specialist is not context.specialist:
            _fail("cross_specialist_execution_rejected")
        if envelope.master_request_id != context.master_request_id:
            _fail("master_request_mismatch")
        if envelope.planning_session_id != context.planning_session_id:
            _fail("session_mismatch")
        if envelope.step_id != context.step_id:
            _fail("request_identity_mismatch")
        if envelope.correlation_id != context.correlation_id:
            _fail("correlation_mismatch")
        if envelope.max_model_turns > context.max_model_turns or envelope.max_tool_calls > context.max_tool_calls:
            _fail("server_budget_exceeded")
        check = self._ledger.fenced_attempt_matches(
            envelope,
            owner_id=authority.owner_id,
            attempt_token=authority.attempt_token,
            fence_epoch=authority.fence_epoch,
            grant_nonce=authority.grant_nonce,
        )
        if check.authorizes_execution is not False:
            _fail("execution_authority_not_granted")
        if check.matches_current_attempt is not True:
            if check.rejection is MasterExecutionLedgerRejection.FENCED_OUT:
                _fail("stale_fence")
            if check.rejection is MasterExecutionLedgerRejection.OWNER_MISMATCH:
                _fail("owner_mismatch")
            if check.rejection is MasterExecutionLedgerRejection.CONFLICTING_DUPLICATE:
                _fail("conflicting_ledger_binding")
            _fail("ledger_fence_rejected")
        if check.state is not MasterExecutionLedgerState.STARTED:
            _fail("mark_started_fence_required")
        return check


def assert_production_dispatch_closed() -> None:
    """Keep production dispatch and the closed runtime entry closed."""

    if PRODUCTION_DISPATCH_ENABLED is not False:
        _fail("production_dispatch_disabled")
    if NATIVE_PRODUCTION_DISPATCH_AVAILABLE is not False:
        _fail("production_dispatch_disabled")
    try:
        invoke_specialist()
    except ContractValidationError as exc:
        if exc.errors != ("specialist_invocation_not_granted",):
            raise
    else:
        _fail("specialist_execution_not_granted")


__all__ = [
    "NATIVE_EXECUTION_IS_SOURCE_EVIDENCE",
    "NATIVE_OFFLINE_EXECUTION_IS_PRODUCTION_DISPATCH",
    "NATIVE_PRODUCTION_DISPATCH_AVAILABLE",
    "NATIVE_SPECIALIST_SIGNATURES",
    "SCRIPTED_WORKER_IS_NATIVE_EXECUTION",
    "SPRINT3C_SPECIALIST_ORCHESTRATION_SCHEMA_VERSION",
    "NativeSpecialistSignatureCatalog",
    "Sprint3CBoundInvocationIdentity",
    "Sprint3CConsumedDispatchAuthority",
    "Sprint3CDecisionNativePath",
    "Sprint3CHistoricalNativePath",
    "Sprint3CLiveOpsNativeExecution",
    "Sprint3CLiveOpsRuntimeInjection",
    "Sprint3CNativeSpecialistOrchestrator",
    "assert_production_dispatch_closed",
    "authority_from_durable_attempt",
    "inspect_native_specialist_signatures",
    "refuse_decision_native_request",
    "refuse_historical_finalization",
    "refuse_scripted_worker_as_native",
]
