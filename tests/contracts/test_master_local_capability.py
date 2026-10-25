"""Offline single-use capability mechanics. Nothing here calls a specialist."""

from __future__ import annotations

import copy
import dataclasses
import inspect
import json
import pickle
import threading
from datetime import datetime, timedelta, timezone

import pytest

from wilvor_ai.contracts import AgentCapability, ContractValidationError
from wilvor_ai.master_contracts import (
    MasterBindingKind,
    MasterInvocationAuthorization,
    MasterProductionAuthority,
    MasterPurpose,
    MasterRequirement,
    MasterResultBinding,
    MasterSpecialist,
)
from wilvor_ai.master_evidence_admission import (
    MasterEvidenceAdmissionInput,
    MasterSourceAuthenticity,
)
from wilvor_ai.master_local_capability import (
    LOCAL_CAPABILITY_SCHEMA_VERSION,
    PROCESS_LOCAL_OBJECT_IDENTITY_IS_DISTRIBUTED_PRODUCTION_AUTHORIZATION,
    PROCESS_LOCAL_RANDOM_TOKEN_IS_DISTRIBUTED_PRODUCTION_AUTHORIZATION,
    LocalCapabilityDescription,
    LocalCapabilityHandle,
    LocalCapabilityRegistry,
    LocalCapabilityScope,
    LocalCapabilityState,
    LocalConsumptionOutcome,
    LocalConsumptionResult,
    LocalRejectionReason,
    LocalSessionRevocation,
    LocalTestClock,
)
from wilvor_ai.master_routing import (
    MasterPlanningContext,
    MasterProposedCapability,
    MasterRouteExecutionAuthority,
    MasterRoutePlan,
    MasterRouteProposal,
)
from wilvor_ai.model_contracts import ModelDecision, ModelDecisionKind


T0 = datetime(2026, 1, 15, 0, 0, 0, tzinfo=timezone.utc)
T1 = datetime(2026, 1, 15, 0, 0, 30, tzinfo=timezone.utc)
T2 = datetime(2026, 1, 15, 0, 1, 0, tzinfo=timezone.utc)
INVOCATION_ID = "inv-local-1"
CONSUMER_ID = "consumer-local-1"
SESSION_ID = "session-local-1"
TEXT = "Which recorded history applies?"


def _scope(**overrides: object) -> LocalCapabilityScope:
    values: dict[str, object] = {
        "specialist": MasterSpecialist.HISTORICAL_ANALYTICS,
        "purpose": MasterPurpose.ANSWER,
        "requirement": MasterRequirement.REQUIRED,
        "agent_capability": AgentCapability.RETRIEVE_HISTORICAL_ANALYTICS,
    }
    values.update(overrides)
    return LocalCapabilityScope(**values)  # type: ignore[arg-type]


def _registry(instant: datetime = T0) -> tuple[LocalCapabilityRegistry, LocalTestClock]:
    clock = LocalTestClock(instant)
    return LocalCapabilityRegistry(clock=clock), clock


def _issue(registry: LocalCapabilityRegistry, **overrides: object) -> LocalCapabilityHandle:
    values: dict[str, object] = {
        "scope": _scope(),
        "invocation_id": INVOCATION_ID,
        "consumer_id": CONSUMER_ID,
        "session_id": SESSION_ID,
        "expires_at": T2,
    }
    values.update(overrides)
    return registry.issue(**values)  # type: ignore[arg-type]


def _consume(
    registry: LocalCapabilityRegistry,
    handle: object,
    **overrides: object,
):
    values: dict[str, object] = {
        "scope": _scope(),
        "invocation_id": INVOCATION_ID,
        "consumer_id": CONSUMER_ID,
    }
    values.update(overrides)
    return registry.consume(handle, **values)  # type: ignore[arg-type]


def _assert_closed(result: object) -> None:
    assert isinstance(result, (LocalConsumptionResult, LocalSessionRevocation))
    assert result.execution_authority is MasterRouteExecutionAuthority.DENIED
    assert result.production_authority is MasterProductionAuthority.DENIED
    assert result.source_authenticity is MasterSourceAuthenticity.NOT_ESTABLISHED
    assert result.execution_permitted is False
    assert result.dispatch_permitted is False
    assert result.specialist_dispatched is False
    assert result.distributed_production_authorization is False
    assert not result
    assert callable(result) is False


def test_issue_creates_a_capability_only_through_the_registry():
    registry, _clock = _registry()
    handle = _issue(registry)
    assert type(handle) is LocalCapabilityHandle
    assert registry.owns(handle) is True
    assert registry.observed_state(handle) is LocalCapabilityState.ISSUED
    description = handle.description()
    assert description.schema_version == LOCAL_CAPABILITY_SCHEMA_VERSION
    assert description.execution_authority is MasterRouteExecutionAuthority.DENIED
    assert description.production_authority is MasterProductionAuthority.DENIED
    assert description.source_authenticity is MasterSourceAuthenticity.NOT_ESTABLISHED
    assert description.authorizes_execution is False
    assert description.distributed_production_authorization is False
    assert dataclasses.is_dataclass(LocalCapabilityHandle) is False
    assert not hasattr(LocalCapabilityHandle, "__dataclass_fields__")
    assert "records" not in dir(registry)
    rendered = repr(handle)
    assert "DENIED" in rendered
    assert handle._handle_token not in rendered.encode("utf-8")
    assert handle._registry_token not in rendered.encode("utf-8")


def test_registry_ownership_is_bookkeeping_for_the_issuing_registry():
    owner, _owner_clock = _registry()
    other, _other_clock = _registry()
    handle = _issue(owner)
    assert owner.owns(handle) is True
    assert other.owns(handle) is False
    assert owner.owns(copy.copy(handle)) is False
    assert owner.owns(_scope()) is False
    assert owner.owns(INVOCATION_ID) is False
    assert type(owner._lock) is type(threading.Lock())
    assert PROCESS_LOCAL_RANDOM_TOKEN_IS_DISTRIBUTED_PRODUCTION_AUTHORIZATION is False
    assert PROCESS_LOCAL_OBJECT_IDENTITY_IS_DISTRIBUTED_PRODUCTION_AUTHORIZATION is False


def test_exact_scope_equality_is_required_and_does_not_authorize_execution():
    registry, _clock = _registry()
    handle = _issue(registry)
    mismatched = _consume(
        registry,
        handle,
        scope=_scope(requirement=MasterRequirement.OPTIONAL),
    )
    assert mismatched.outcome is LocalConsumptionOutcome.REJECTED
    assert mismatched.reason is LocalRejectionReason.SCOPE_MISMATCH
    assert mismatched.local_state_transition_only is False
    _assert_closed(mismatched)
    assert registry.observed_state(handle) is LocalCapabilityState.ISSUED

    equal_scope = _scope()
    assert equal_scope == _scope()
    assert equal_scope is not _scope()
    transition = _consume(registry, handle, scope=equal_scope)
    assert transition.outcome is LocalConsumptionOutcome.TRANSITION_RECORDED
    assert transition.reason is None
    assert transition.local_state_transition_only is True
    _assert_closed(transition)
    assert registry.observed_state(handle) is LocalCapabilityState.CONSUMED


def test_single_use_consumption_is_atomic_and_replay_is_rejected():
    registry, _clock = _registry()
    handle = _issue(registry)
    first = _consume(registry, handle)
    second = _consume(registry, handle)
    assert first.outcome is LocalConsumptionOutcome.TRANSITION_RECORDED
    assert second.outcome is LocalConsumptionOutcome.REJECTED
    assert second.reason is LocalRejectionReason.REPLAY
    _assert_closed(first)
    _assert_closed(second)
    assert registry.observed_state(handle) is LocalCapabilityState.CONSUMED
    assert registry.owns(handle) is True


def test_two_concurrent_consumers_record_one_transition():
    for _attempt in range(20):
        registry, _clock = _registry()
        handle = _issue(registry)
        barrier = threading.Barrier(2)
        results: list[LocalConsumptionResult] = []
        errors: list[BaseException] = []
        gate = threading.Lock()

        def _run() -> None:
            try:
                barrier.wait(timeout=10)
                result = _consume(registry, handle)
                with gate:
                    results.append(result)
            except BaseException as exc:  # pragma: no cover - failure path
                errors.append(exc)

        threads = [threading.Thread(target=_run) for _index in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)
            assert thread.is_alive() is False
        assert errors == []
        assert len(results) == 2
        transitions = [
            item for item in results if item.outcome is LocalConsumptionOutcome.TRANSITION_RECORDED
        ]
        replays = [item for item in results if item.reason is LocalRejectionReason.REPLAY]
        assert len(transitions) == 1
        assert len(replays) == 1
        for item in results:
            _assert_closed(item)
        assert registry.observed_state(handle) is LocalCapabilityState.CONSUMED


def test_injected_clock_expiry_does_not_reactivate_when_rewound():
    registry, clock = _registry(T0)
    handle = _issue(registry, expires_at=T1)
    assert registry.observed_state(handle) is LocalCapabilityState.ISSUED
    clock.advance_to(T1)
    expired = _consume(registry, handle)
    assert expired.outcome is LocalConsumptionOutcome.REJECTED
    assert expired.reason is LocalRejectionReason.EXPIRED
    _assert_closed(expired)
    assert registry.observed_state(handle) is LocalCapabilityState.EXPIRED
    clock.advance_to(T0)
    rewound = _consume(registry, handle)
    assert rewound.reason is LocalRejectionReason.NOT_REACTIVATED
    _assert_closed(rewound)
    assert registry.observed_state(handle) is LocalCapabilityState.EXPIRED


def test_issue_rejects_an_already_expired_instant():
    registry, _clock = _registry(T1)
    with pytest.raises(ContractValidationError, match="capability_already_expired"):
        _issue(registry, expires_at=T1)
    with pytest.raises(ContractValidationError, match="capability_already_expired"):
        _issue(registry, expires_at=T0)


def test_session_close_revokes_issued_capabilities():
    registry, _clock = _registry()
    issued = _issue(registry)
    consumed = _issue(registry, invocation_id="inv-local-2")
    transition = _consume(registry, consumed, invocation_id="inv-local-2")
    assert transition.outcome is LocalConsumptionOutcome.TRANSITION_RECORDED
    revocation = registry.close_session(SESSION_ID)
    assert revocation.revoked_count == 1
    assert revocation.session_id == SESSION_ID
    _assert_closed(revocation)
    revoked = _consume(registry, issued)
    assert revoked.reason is LocalRejectionReason.REVOKED
    _assert_closed(revoked)
    again = _consume(registry, issued)
    assert again.reason is LocalRejectionReason.REVOKED
    assert registry.observed_state(issued) is LocalCapabilityState.REVOKED
    assert registry.observed_state(consumed) is LocalCapabilityState.CONSUMED
    replay = _consume(registry, consumed, invocation_id="inv-local-2")
    assert replay.reason is LocalRejectionReason.REPLAY
    with pytest.raises(ContractValidationError, match="session_closed"):
        _issue(registry, invocation_id="inv-local-3")


def test_unknown_capability_is_rejected():
    registry, _clock = _registry()
    unknown = _consume(registry, object())
    assert unknown.reason is LocalRejectionReason.UNKNOWN_CAPABILITY
    _assert_closed(unknown)
    label = _consume(registry, INVOCATION_ID)
    assert label.reason is LocalRejectionReason.UNKNOWN_CAPABILITY
    _assert_closed(label)
    with pytest.raises(ContractValidationError, match="capability_constructor_refused"):
        LocalCapabilityHandle()
    with pytest.raises(ContractValidationError, match="capability_constructor_refused"):
        LocalCapabilityHandle(principal="authenticated-user")  # type: ignore[call-arg]


def test_foreign_registry_rejects_the_handle_without_consuming_it():
    owner, _owner_clock = _registry()
    foreign, _foreign_clock = _registry()
    handle = _issue(owner)
    rejected = _consume(foreign, handle)
    assert rejected.reason is LocalRejectionReason.FOREIGN_REGISTRY
    _assert_closed(rejected)
    assert foreign.owns(handle) is False
    assert foreign.observed_state(handle) is None
    assert owner.observed_state(handle) is LocalCapabilityState.ISSUED
    transition = _consume(owner, handle)
    assert transition.outcome is LocalConsumptionOutcome.TRANSITION_RECORDED
    _assert_closed(transition)


def test_wrong_invocation_or_consumer_is_rejected_without_consumption():
    registry, _clock = _registry()
    handle = _issue(registry)
    wrong_invocation = _consume(registry, handle, invocation_id="inv-other")
    wrong_consumer = _consume(registry, handle, consumer_id="consumer-other")
    assert wrong_invocation.reason is LocalRejectionReason.INVOCATION_MISMATCH
    assert wrong_consumer.reason is LocalRejectionReason.CONSUMER_MISMATCH
    _assert_closed(wrong_invocation)
    _assert_closed(wrong_consumer)
    assert registry.observed_state(handle) is LocalCapabilityState.ISSUED
    transition = _consume(registry, handle)
    assert transition.outcome is LocalConsumptionOutcome.TRANSITION_RECORDED
    _assert_closed(transition)
    assert _consume(registry, handle).reason is LocalRejectionReason.REPLAY


def test_failed_consumption_never_permits_execution():
    registry, _clock = _registry()
    handle = _issue(registry)
    failures = (
        _consume(registry, object()),
        _consume(registry, handle, scope=_scope(purpose=MasterPurpose.DISCOVERY)),
        _consume(registry, handle, invocation_id="inv-other"),
        _consume(registry, handle, consumer_id="consumer-other"),
    )
    for failure in failures:
        assert failure.outcome is LocalConsumptionOutcome.REJECTED
        assert failure.local_state_transition_only is False
        _assert_closed(failure)
        with pytest.raises(ContractValidationError, match="execution_not_granted"):
            LocalConsumptionResult(
                outcome=failure.outcome,
                reason=failure.reason,
                local_state_transition_only=False,
                execution_permitted=True,
            )
    assert registry.observed_state(handle) is LocalCapabilityState.ISSUED


def test_copy_and_serialization_do_not_grant_permission():
    registry, _clock = _registry()
    handle = _issue(registry)
    description = handle.description()
    serialized = json.loads(json.dumps(dataclasses.asdict(description)))
    candidates = (
        (copy.copy(handle), LocalRejectionReason.COPIED_CAPABILITY),
        (copy.deepcopy(handle), LocalRejectionReason.COPIED_CAPABILITY),
        (pickle.loads(pickle.dumps(handle)), LocalRejectionReason.SERIALIZED_CAPABILITY),
        (LocalCapabilityHandle.from_description(description), LocalRejectionReason.SERIALIZED_CAPABILITY),
        (LocalCapabilityHandle.from_description(serialized), LocalRejectionReason.SERIALIZED_CAPABILITY),
        (description, LocalRejectionReason.SERIALIZED_CAPABILITY),
        (serialized, LocalRejectionReason.SERIALIZED_CAPABILITY),
    )
    forged = object.__new__(LocalCapabilityHandle)
    forged._registry_token = handle._registry_token
    forged._handle_token = handle._handle_token
    forged._inert_reason = None
    candidates = (
        *candidates,
        (forged, LocalRejectionReason.COPIED_CAPABILITY),
    )
    for candidate, reason in candidates:
        rejected = _consume(registry, candidate)
        assert rejected.reason is reason
        assert rejected.outcome is LocalConsumptionOutcome.REJECTED
        _assert_closed(rejected)
        assert registry.owns(candidate) is False
    assert registry.observed_state(handle) is LocalCapabilityState.ISSUED
    transition = _consume(registry, handle)
    assert transition.outcome is LocalConsumptionOutcome.TRANSITION_RECORDED
    _assert_closed(transition)
    assert handle._handle_token not in json.dumps(serialized).encode("utf-8")


def test_matching_string_ids_do_not_cross_registries():
    registry_a, _clock_a = _registry()
    registry_b, _clock_b = _registry()
    handle_a = _issue(registry_a)
    handle_b = _issue(registry_b)
    assert handle_a._registry_token != handle_b._registry_token
    foreign = _consume(registry_b, handle_a)
    assert foreign.reason is LocalRejectionReason.FOREIGN_REGISTRY
    _assert_closed(foreign)
    revocation = registry_a.close_session(SESSION_ID)
    assert revocation.revoked_count == 1
    _assert_closed(revocation)
    assert _consume(registry_a, handle_a).reason is LocalRejectionReason.REVOKED
    transition = _consume(registry_b, handle_b)
    assert transition.outcome is LocalConsumptionOutcome.TRANSITION_RECORDED
    _assert_closed(transition)


def test_failed_operation_does_not_reactivate_or_permit_execution():
    registry, clock = _registry()
    handle = _issue(registry)
    premature = registry.record_operation_failure(handle)
    assert premature.reason is LocalRejectionReason.OPERATION_NOT_CONSUMED
    _assert_closed(premature)
    assert registry.observed_state(handle) is LocalCapabilityState.ISSUED
    transition = _consume(registry, handle)
    assert transition.outcome is LocalConsumptionOutcome.TRANSITION_RECORDED
    _assert_closed(transition)
    failed = registry.record_operation_failure(handle)
    assert failed.reason is LocalRejectionReason.OPERATION_FAILED
    _assert_closed(failed)
    assert registry.observed_state(handle) is LocalCapabilityState.OPERATION_FAILED
    clock.advance_to(T0 - timedelta(seconds=1))
    again = _consume(registry, handle)
    repeated = registry.record_operation_failure(handle)
    assert again.reason is LocalRejectionReason.NOT_REACTIVATED
    assert repeated.reason is LocalRejectionReason.NOT_REACTIVATED
    _assert_closed(again)
    _assert_closed(repeated)
    assert registry.observed_state(handle) is LocalCapabilityState.OPERATION_FAILED
    assert "operation" not in inspect.signature(LocalCapabilityRegistry.consume).parameters
    failure_signature = inspect.signature(LocalCapabilityRegistry.record_operation_failure)
    assert list(failure_signature.parameters) == ["self", "handle"]


def test_planning_binding_evidence_and_model_output_do_not_authorize():
    registry, _clock = _registry()
    handle = _issue(registry)
    authorization = MasterInvocationAuthorization(
        master_request_id="master-req-local-1",
        invocation_id=INVOCATION_ID,
        specialist=MasterSpecialist.HISTORICAL_ANALYTICS,
        purpose=MasterPurpose.ANSWER,
        requirement=MasterRequirement.REQUIRED,
    )
    planning_context = MasterPlanningContext(
        master_request_id="master-req-local-1",
        correlation_id="corr-local-1",
        application_request_time_utc="2026-01-15T00:00:01Z",
        canonical_user_text=TEXT,
    )
    proposal = MasterRouteProposal(
        user_text=TEXT,
        capabilities=(
            MasterProposedCapability(
                AgentCapability.RETRIEVE_HISTORICAL_ANALYTICS,
                MasterRequirement.REQUIRED,
            ),
        ),
    )
    plan = MasterRoutePlan(
        master_request_id="master-req-local-1",
        canonical_user_text=TEXT,
        steps=(),
    )
    binding = MasterResultBinding(
        invocation_id=INVOCATION_ID,
        binding_kind=MasterBindingKind.CONTRACT_TEST,
    )
    evidence = MasterEvidenceAdmissionInput(authorization, artifact={"supplied": True})
    model = ModelDecision(kind=ModelDecisionKind.REFUSAL)
    presented = (
        (planning_context, LocalRejectionReason.PLANNING_OBJECT_NOT_AUTHORITY),
        (proposal, LocalRejectionReason.PLANNING_OBJECT_NOT_AUTHORITY),
        (plan, LocalRejectionReason.PLANNING_OBJECT_NOT_AUTHORITY),
        (authorization, LocalRejectionReason.BINDING_NOT_AUTHORITY),
        (binding, LocalRejectionReason.BINDING_NOT_AUTHORITY),
        (evidence, LocalRejectionReason.EVIDENCE_NOT_AUTHORITY),
        (model, LocalRejectionReason.MODEL_OUTPUT_NOT_AUTHORITY),
        (_scope(), LocalRejectionReason.UNKNOWN_CAPABILITY),
    )
    for candidate, reason in presented:
        rejected = _consume(registry, candidate)
        assert rejected.reason is reason
        _assert_closed(rejected)
    assert registry.observed_state(handle) is LocalCapabilityState.ISSUED
    assert binding.production_authority is MasterProductionAuthority.DENIED


def test_public_surface_has_no_principal_constructor_or_dispatcher():
    registry_parameters = inspect.signature(LocalCapabilityRegistry.__init__).parameters
    issue_parameters = inspect.signature(LocalCapabilityRegistry.issue).parameters
    consume_parameters = inspect.signature(LocalCapabilityRegistry.consume).parameters
    assert list(registry_parameters) == ["self", "clock"]
    assert list(issue_parameters) == [
        "self",
        "scope",
        "invocation_id",
        "consumer_id",
        "session_id",
        "expires_at",
    ]
    assert list(consume_parameters) == ["self", "handle", "scope", "invocation_id", "consumer_id"]
    for parameter_name in ("principal", "authenticated", "credential", "aws"):
        assert parameter_name not in registry_parameters
        assert parameter_name not in issue_parameters
        assert parameter_name not in consume_parameters
    names = {name.lower() for name in dir(LocalCapabilityRegistry)} | {
        name.lower() for name in dir(LocalCapabilityHandle)
    }
    assert not any(piece in name for name in names for piece in ("dispatch", "invoke", "execute"))
    clock = LocalTestClock(T0)
    registry = LocalCapabilityRegistry(clock=clock)
    assert callable(registry) is False
    assert callable(_issue(registry)) is False
    with pytest.raises(ContractValidationError, match="invalid_clock"):
        LocalCapabilityRegistry(clock=lambda: T0)  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        LocalCapabilityRegistry(clock=clock, principal="authenticated-user")  # type: ignore[call-arg]


def test_prohibited_agent_capability_cannot_be_issued():
    with pytest.raises(ContractValidationError, match="prohibited_capability"):
        _scope(agent_capability=AgentCapability.EXECUTE_REROUTES)
    with pytest.raises(ContractValidationError, match="prohibited_capability"):
        _scope(agent_capability=AgentCapability.MUTATE_AWS_INFRASTRUCTURE)
    with pytest.raises(ContractValidationError, match="execution_authority_not_granted"):
        LocalConsumptionResult(
            outcome=LocalConsumptionOutcome.TRANSITION_RECORDED,
            reason=None,
            local_state_transition_only=True,
            execution_authority="GRANTED",  # type: ignore[arg-type]
        )
    with pytest.raises(ContractValidationError, match="production_authority_not_issued"):
        LocalSessionRevocation(
            session_id=SESSION_ID,
            revoked_count=0,
            production_authority=MasterProductionAuthority.PRODUCTION,
        )
    with pytest.raises(ContractValidationError, match="source_authenticity_not_established"):
        LocalCapabilityDescription(
            schema_version=LOCAL_CAPABILITY_SCHEMA_VERSION,
            kind="OPAQUE_PROCESS_LOCAL_CAPABILITY",
            source_authenticity="AUTHENTICATED",  # type: ignore[arg-type]
        )


def test_observation_does_not_consume_or_authorize():
    registry, _clock = _registry()
    handle = _issue(registry)
    assert registry.observed_state(handle) is LocalCapabilityState.ISSUED
    assert registry.observed_state(copy.copy(handle)) is None
    transition = _consume(registry, handle)
    assert transition.local_state_transition_only is True
    _assert_closed(transition)
    passed_result = _consume(registry, transition)
    assert passed_result.reason is LocalRejectionReason.UNKNOWN_CAPABILITY
    _assert_closed(passed_result)
