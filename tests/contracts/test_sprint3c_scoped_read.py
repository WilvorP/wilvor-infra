"""Sprint 3C Agent C: bounded AWS reads stay allowlisted, offline, and unauthenticated."""

from __future__ import annotations

import ast
import os
import subprocess
import sys
from pathlib import Path

import pytest

from wilvor_ai.contracts import AgentCapability, ContractValidationError
from wilvor_ai.decision_contracts import RiskPresence, StoredRiskLevel
from wilvor_ai.master_authorized_scope import (
    EXECUTION_GRANT_AVAILABLE,
    bind_local_test_host,
    promote_effective_scope_to_execution_grant,
)
from wilvor_ai.master_contracts import MasterPurpose, MasterSpecialist
from wilvor_ai.master_evidence_admission import MasterSourceAuthenticity
from wilvor_ai.master_host_scoped_read_integration import PRODUCTION_HOST_SCOPED_READ_ENABLED
from wilvor_ai.master_read_resource_guard import (
    HISTORICAL_LIST_MAX,
    ReadOperation,
    server_read_argument_names,
    server_read_resources,
    server_read_tools,
)
from wilvor_ai.master_runtime_budget import (
    DEPLOYED_HOST_ENABLED,
    EMPTY_ROWS_CREATE_VERIFIED_ZERO as BUDGET_EMPTY_ZERO,
    PRODUCTION_GRANT_AVAILABLE,
)
from wilvor_ai.master_runtime_read_journal import (
    AWS_CONNECTION_IMPLEMENTED,
    EMPTY_ROWS_CREATE_VERIFIED_ZERO as JOURNAL_EMPTY_ZERO,
)
from wilvor_ai.master_trusted_read_executor import (
    PRODUCTION_GRANT_ISSUER_INTEGRATED,
    PRODUCTION_READ_EXECUTOR_ENABLED,
    ReadBoundaryDenial,
    ReadExecutionOutcome,
    UntrustedReadRequest,
    bind_nonproduction_cancellation,
    issue_production_read_invocation,
    open_production_executor,
    raw_read_observation,
)
from wilvor_ai.sprint3c_scoped_read import (
    ABSENT_RECORD_IS_LOW_RISK,
    ADAPTER_ORIGIN,
    APPROVED_SOURCE_ADAPTER,
    CALLER_ARGUMENTS_WIDEN_AUTHORIZATION,
    CONSUMED_ATTEMPT_IS_PRODUCTION_GRANT,
    GRANT_CONSUMPTION_LIFECYCLE,
    LEDGER_EXECUTION_STATE_STARTED,
    EMPTY_PAGE_IS_VERIFIED_ZERO,
    LIVE_AWS_OPERATION_PERMITTED,
    PRODUCTION_SCOPED_AWS_READ_ENABLED,
    REGION_LABEL_IS_SIGMET_POLYGON,
    SPRINT3C_SCOPED_READ_SCHEMA_VERSION,
    OfflineAwsReadClient,
    ScopedAwsReadRefusal,
    ScopedAwsSourceError,
    Sprint3CScopedReadPort,
    bind_offline_aws_read_client,
    describe_consumed_attempt_read_binding,
    issue_production_scoped_aws_read,
    open_production_scoped_aws_read,
    supported_read_catalog,
)


LIVE = MasterSpecialist.LIVE_OPS
HISTORY = MasterSpecialist.HISTORICAL_ANALYTICS
DECISION = MasterSpecialist.DECISION
RETRIEVE = AgentCapability.RETRIEVE_DETERMINISTIC_OPERATIONAL_CONTEXT
HISTORICAL = AgentCapability.RETRIEVE_HISTORICAL_ANALYTICS
RISK = AgentCapability.EXPLAIN_EXISTING_RISK
ACCEPTED = "2026-10-27T16:00:00Z"
OBSERVED = "2026-10-27T17:30:00Z"
EVALUATED = "2026-10-27T18:00:00Z"
DEADLINE = "2026-10-27T20:00:00Z"
NOW = "2026-10-27T18:30:00Z"
SOURCE_OBSERVED = "2026-10-27T12:00:00Z"
HIST_START = "2026-10-20T18:00:00Z"
HIST_END = "2026-10-27T18:00:00Z"
EIGHT_DAY_START = "2026-10-19T18:00:00Z"

REPO_ROOT = Path(__file__).resolve().parents[2]
SHARED_DIR = REPO_ROOT / "functions" / "shared"
MODULE = SHARED_DIR / "wilvor_ai" / "sprint3c_scoped_read.py"

FORBIDDEN_SOURCE = (
    "boto3",
    "botocore",
    "datetime.now",
    "os.environ",
    "getenv(",
    ".scan(",
    "scan_all(",
    "execute_statement(",
    "LocalHarnessGrantIssuer",
    "issue_production_execution_grant",
)


def _live_binding(**overrides: object):
    payload: dict[str, object] = {
        "owner_id": "owner-1",
        "attempt_token": "attempt-1",
        "fence_epoch": 1,
        "grant_nonce": "grant-nonce-1",
        "idempotency_key": "idem-live",
        "payload_fingerprint": "fingerprint-1",
        "ledger_state": LEDGER_EXECUTION_STATE_STARTED,
        "grant_consumption_lifecycle": GRANT_CONSUMPTION_LIFECYCLE,
        "master_request_id": "req-live",
        "planning_session_id": "plan-live",
        "step_id": "step-live",
        "invocation_id": "inv-live",
        "specialist": LIVE,
        "purpose": MasterPurpose.ANSWER,
        "capability": RETRIEVE,
        "principal_ref": "principal-1",
        "policy_revision": "policy-1",
        "tool_name": "get_aircraft_operational_context",
        "resource_id": "aircraft",
        "operation": ReadOperation.GET_ITEM,
        "region_scope": ("CA", "NV"),
        "accepted_at_utc": ACCEPTED,
        "deadline_utc": DEADLINE,
        "correlation_id": "corr-live",
        "decision_aircraft_id": None,
    }
    payload.update(overrides)
    return describe_consumed_attempt_read_binding(**payload)


def _historical_binding(**overrides: object):
    payload: dict[str, object] = {
        "owner_id": "owner-hist",
        "attempt_token": "attempt-hist",
        "fence_epoch": 1,
        "grant_nonce": "grant-nonce-hist",
        "idempotency_key": "idem-hist",
        "payload_fingerprint": "fingerprint-hist",
        "ledger_state": LEDGER_EXECUTION_STATE_STARTED,
        "grant_consumption_lifecycle": GRANT_CONSUMPTION_LIFECYCLE,
        "master_request_id": "req-hist",
        "planning_session_id": "plan-hist",
        "step_id": "step-hist",
        "invocation_id": "inv-hist",
        "specialist": HISTORY,
        "purpose": MasterPurpose.ANSWER,
        "capability": HISTORICAL,
        "principal_ref": "principal-1",
        "policy_revision": "policy-1",
        "tool_name": "list_historical_encounters",
        "resource_id": "historical.encounters",
        "operation": ReadOperation.QUERY,
        "region_scope": ("CA", "NV"),
        "accepted_at_utc": ACCEPTED,
        "deadline_utc": DEADLINE,
        "correlation_id": "corr-hist",
        "historical_window": (HIST_START, HIST_END),
    }
    payload.update(overrides)
    return describe_consumed_attempt_read_binding(**payload)


def _decision_binding(**overrides: object):
    payload: dict[str, object] = {
        "owner_id": "owner-dec",
        "attempt_token": "attempt-dec",
        "fence_epoch": 1,
        "grant_nonce": "grant-nonce-dec",
        "idempotency_key": "idem-dec",
        "payload_fingerprint": "fingerprint-dec",
        "ledger_state": LEDGER_EXECUTION_STATE_STARTED,
        "grant_consumption_lifecycle": GRANT_CONSUMPTION_LIFECYCLE,
        "master_request_id": "req-dec",
        "planning_session_id": "plan-dec",
        "step_id": "step-dec",
        "invocation_id": "inv-dec",
        "specialist": DECISION,
        "purpose": MasterPurpose.ANSWER,
        "capability": RISK,
        "principal_ref": "principal-1",
        "policy_revision": "policy-1",
        "tool_name": "get_current_risk_evidence",
        "resource_id": "aircraft",
        "operation": ReadOperation.GET_ITEM,
        "region_scope": ("CA",),
        "accepted_at_utc": ACCEPTED,
        "deadline_utc": DEADLINE,
        "correlation_id": "corr-dec",
        "decision_aircraft_id": "AC1",
    }
    payload.update(overrides)
    return describe_consumed_attempt_read_binding(**payload)


def _live_host(**overrides: object):
    payload: dict[str, object] = {
        "principal_ref": "principal-1",
        "policy_revision": "policy-1",
        "specialist": LIVE,
        "purpose": MasterPurpose.ANSWER,
        "capability": RETRIEVE,
        "population_entity_ids": frozenset({"AC1"}),
        "region_state_codes": frozenset({"CA", "NV"}),
        "observation_utc": OBSERVED,
        "evaluation_utc": EVALUATED,
    }
    payload.update(overrides)
    return bind_local_test_host(**payload)  # type: ignore[arg-type]


def _historical_host(**overrides: object):
    payload: dict[str, object] = {
        "principal_ref": "principal-1",
        "policy_revision": "policy-1",
        "specialist": HISTORY,
        "purpose": MasterPurpose.ANSWER,
        "capability": HISTORICAL,
        "population_entity_ids": frozenset({"AC1"}),
        "region_state_codes": frozenset({"CA", "NV"}),
        "observation_utc": OBSERVED,
        "evaluation_utc": EVALUATED,
        "historical_interval_id": "hist-interval-1",
        "historical_start_utc": HIST_START,
        "historical_end_utc": HIST_END,
    }
    payload.update(overrides)
    return bind_local_test_host(**payload)  # type: ignore[arg-type]


def _decision_host(**overrides: object):
    payload: dict[str, object] = {
        "principal_ref": "principal-1",
        "policy_revision": "policy-1",
        "specialist": DECISION,
        "purpose": MasterPurpose.ANSWER,
        "capability": RISK,
        "population_entity_ids": frozenset({"AC1"}),
        "region_state_codes": frozenset({"CA"}),
        "observation_utc": OBSERVED,
        "evaluation_utc": EVALUATED,
        "validated_aircraft_ids": frozenset({"AC1"}),
        "row_entity_id": "AC1",
        "row_present": True,
        "risk_presence": RiskPresence.PRESENT,
        "stored_risk_level": StoredRiskLevel.MEDIUM,
    }
    payload.update(overrides)
    return bind_local_test_host(**payload)  # type: ignore[arg-type]


def _request(binding: object, **overrides: object) -> UntrustedReadRequest:
    payload: dict[str, object] = {
        "master_request_id": binding.master_request_id,
        "planning_session_id": binding.planning_session_id,
        "step_id": binding.step_id,
        "invocation_id": binding.invocation_id,
        "specialist": binding.specialist,
        "purpose": binding.purpose,
        "capability": binding.capability,
        "tool_name": binding.tool_name,
        "tool_call_id": "tool-1",
        "resource_id": binding.resource_id,
        "operation": binding.operation,
        "requested_arguments": (("aircraft_id", "AC1"),),
        "correlation_id": binding.correlation_id,
    }
    payload.update(overrides)
    return UntrustedReadRequest(**payload)  # type: ignore[arg-type]


def _port(client: OfflineAwsReadClient, now_utc: str = NOW) -> Sprint3CScopedReadPort:
    return Sprint3CScopedReadPort(client, now_utc=now_utc, remaining_ms=30_000)


def _closed(result: object) -> None:
    assert result.approved_source_adapter is False
    assert result.production_grant_issued is False
    assert result.live_aws_initiated is False
    assert result.source_authenticity_established is False
    assert result.verified_zero is False
    assert result.absent_record_is_low_risk is False
    assert result.sigmet_polygon_intersection_established is False
    assert result.caller_arguments_widened_authorization is False
    assert result.distributed_exactly_once is False
    assert result.inferred_risk_level is None
    assert result.binding.production_grant is False
    assert result.binding.grant_nonce_is_production_grant is False
    with pytest.raises(ContractValidationError, match="scoped_aws_read_is_not_source_authentication"):
        bool(result)
    if result.receipt is not None:
        assert result.receipt.approved_source_adapter is False
        assert result.receipt.verified_zero_minted is False
        assert result.receipt.source_authenticity is MasterSourceAuthenticity.NOT_ESTABLISHED
        assert result.receipt.geographic_coverage == "NOT_ESTABLISHED"
        assert result.receipt.geographic_impact_verified is False
        observation = raw_read_observation(result.receipt)
        assert observation["authentic_source_read"] is False
        assert "next_page_token" not in observation
        assert "tool_result" not in observation


def test_closed_flags_and_production_entrypoints_do_not_call_aws():
    assert PRODUCTION_SCOPED_AWS_READ_ENABLED is False
    assert LIVE_AWS_OPERATION_PERMITTED is False
    assert APPROVED_SOURCE_ADAPTER is False
    assert EMPTY_PAGE_IS_VERIFIED_ZERO is False
    assert ABSENT_RECORD_IS_LOW_RISK is False
    assert REGION_LABEL_IS_SIGMET_POLYGON is False
    assert CALLER_ARGUMENTS_WIDEN_AUTHORIZATION is False
    assert CONSUMED_ATTEMPT_IS_PRODUCTION_GRANT is False
    assert PRODUCTION_READ_EXECUTOR_ENABLED is False
    assert PRODUCTION_GRANT_ISSUER_INTEGRATED is False
    assert PRODUCTION_HOST_SCOPED_READ_ENABLED is False
    assert EXECUTION_GRANT_AVAILABLE is False
    assert PRODUCTION_GRANT_AVAILABLE is False
    assert DEPLOYED_HOST_ENABLED is False
    assert AWS_CONNECTION_IMPLEMENTED is False
    assert BUDGET_EMPTY_ZERO is False
    assert JOURNAL_EMPTY_ZERO is False
    assert SPRINT3C_SCOPED_READ_SCHEMA_VERSION == "wilvor.ai.sprint3c.scoped_read.v1"

    class LiveClient:
        def get_item(self, **_kwargs: object) -> dict[str, object]:
            raise AssertionError("live AWS get_item")

        def query(self, **_kwargs: object) -> dict[str, object]:
            raise AssertionError("live AWS query")

    live = LiveClient()
    with pytest.raises(ContractValidationError, match="production_read_executor_not_enabled"):
        open_production_scoped_aws_read(live)
    with pytest.raises(ContractValidationError, match="production_grant_issuer_not_integrated"):
        issue_production_scoped_aws_read(live)
    with pytest.raises(ContractValidationError, match="production_grant_issuer_not_integrated"):
        issue_production_read_invocation()
    with pytest.raises(ContractValidationError, match="production_read_executor_not_enabled"):
        open_production_executor()
    with pytest.raises(ContractValidationError, match="consumed_attempt_is_not_a_production_grant"):
        describe_consumed_attempt_read_binding(
            owner_id="owner-1",
            attempt_token="attempt-1",
            fence_epoch=1,
            grant_nonce="grant-nonce-1",
            idempotency_key="idem-live",
            payload_fingerprint="fingerprint-1",
            ledger_state="STARTED",
            grant_consumption_lifecycle=GRANT_CONSUMPTION_LIFECYCLE,
            master_request_id="req-live",
            planning_session_id="plan-live",
            step_id="step-live",
            invocation_id="inv-live",
            specialist=LIVE,
            purpose=MasterPurpose.ANSWER,
            capability=RETRIEVE,
            principal_ref="principal-1",
            policy_revision="policy-1",
            tool_name="get_aircraft_operational_context",
            resource_id="aircraft",
            operation=ReadOperation.GET_ITEM,
            region_scope=("CA",),
            accepted_at_utc=ACCEPTED,
            deadline_utc=DEADLINE,
            correlation_id="corr-live",
            production_grant=True,
        )


def test_cp4_get_item_binds_receipt_identity_to_the_consumed_attempt_fence():
    client = bind_offline_aws_read_client(
        get_items={
            ("aircraft", "AC1"): {
                "aircraft_id": "AC1",
                "source_version": "aircraft-v1",
                "observed_at_utc": SOURCE_OBSERVED,
            }
        }
    )
    binding = _live_binding()
    result = _port(client).execute(
        binding,
        _request(binding),
        _live_host(),
        tool_call_id="tool-1",
        action_id="read-1",
        location_hint="California",
        census_region_name="California",
        sigmet_region_string="Oakland FIR",
    )
    assert result.refusal is None
    assert result.adapter_invoked is True
    assert result.aws_operations == ("get_item",)
    assert client.operations == ["get_item"]
    assert client.requests[0]["TableName"] == "aircraft"
    assert client.requests[0]["ConsistentRead"] is True
    assert "ExclusiveStartKey" not in client.requests[0]
    receipt = result.receipt
    assert receipt is not None
    assert receipt.outcome is ReadExecutionOutcome.COMPLETED
    assert receipt.operation is ReadOperation.GET_ITEM
    assert receipt.invocation_id == binding.invocation_id
    assert receipt.master_request_id == binding.master_request_id
    assert receipt.planning_session_id == binding.planning_session_id
    assert receipt.step_id == binding.step_id
    assert receipt.grant_nonce == binding.grant_nonce
    assert receipt.idempotency_key == binding.idempotency_key
    assert receipt.principal_ref == binding.principal_ref
    assert receipt.correlation_id == binding.correlation_id
    assert receipt.record_ids == ("AC1",)
    assert receipt.source_version == "aircraft-v1"
    assert receipt.source_version_is_integrity_proof is False
    assert receipt.source_reported_observed_at_utc == SOURCE_OBSERVED
    assert receipt.source_reported_observed_at_utc != receipt.deadline_utc
    assert receipt.page_empty is False
    assert receipt.verified_zero_minted is False
    assert result.scope is not None
    assert result.scope.region_scope.effective_state_codes == ("CA",)
    assert result.scope.region_scope.sigmet_polygon_intersection_established is False
    assert result.scope.region_scope.geographic_coverage_established is False
    assert receipt.adapter_id == (
        "nonproduction:LIVE_OPS:get_aircraft_operational_context:aircraft:GET_ITEM"
    )
    assert result.journal_entry is not None
    assert result.journal_entry.adapter_identity.startswith(ADAPTER_ORIGIN)
    with pytest.raises(ContractValidationError, match="effective_scope_is_not_execution_grant"):
        promote_effective_scope_to_execution_grant(result.scope)
    _closed(result)


def test_bounded_query_preserves_query_identity_and_does_not_follow_pages():
    client = bind_offline_aws_read_client(
        query_pages={
            "historical.encounters": [
                {
                    "Items": [
                        {
                            "record_id": "enc-1",
                            "aircraft_id": "AC1",
                            "source_version": "enc-v1",
                            "observed_at_utc": SOURCE_OBSERVED,
                        }
                    ],
                    "LastEvaluatedKey": {"record_id": {"S": "secret-page-token"}},
                    "query_id": "source-query-9",
                }
            ]
        }
    )
    binding = _historical_binding()
    request = _request(
        binding,
        requested_arguments=(
            ("aircraft_id", "AC1"),
            ("start_utc", HIST_START),
            ("end_utc", HIST_END),
        ),
    )
    result = _port(client).execute(
        binding,
        request,
        _historical_host(),
        tool_call_id="tool-1",
        action_id="read-hist",
        location_hint="California",
    )
    assert result.refusal is None
    assert result.aws_operations == ("query",)
    assert client.requests[0]["TableName"] == "historical.encounters"
    assert client.requests[0]["Limit"] == HISTORICAL_LIST_MAX
    assert "ExclusiveStartKey" not in client.requests[0]
    assert "scan" not in "".join(client.operations)
    receipt = result.receipt
    assert receipt is not None
    assert receipt.operation is ReadOperation.QUERY
    assert receipt.query_id == "source-query-9"
    assert receipt.has_more is True
    assert receipt.truncated is True
    assert receipt.partial_pagination is True
    assert receipt.next_page_token_present is True
    assert "limit" not in dict(receipt.requested_arguments)
    assert ("limit", HISTORICAL_LIST_MAX) in receipt.executed_arguments
    assert receipt.grant_nonce == binding.grant_nonce
    observation = raw_read_observation(receipt)
    assert "next_page_token" not in observation
    assert "secret-page-token" not in repr(observation)
    _closed(result)


def test_scan_sql_writes_and_arns_never_reach_the_client():
    client = bind_offline_aws_read_client(
        get_items={("aircraft", "AC1"): {"aircraft_id": "AC1"}}
    )
    binding = _live_binding()
    host = _live_host()
    port = _port(client)
    scanned = port.execute(
        binding,
        _request(binding, operation=ReadOperation.SCAN),
        host,
        tool_call_id="tool-1",
        action_id="read-scan",
    )
    sql = _port(client).execute(
        _live_binding(idempotency_key="idem-sql", invocation_id="inv-sql", correlation_id="corr-sql"),
        _request(
            _live_binding(idempotency_key="idem-sql", invocation_id="inv-sql", correlation_id="corr-sql"),
            requested_arguments=(("aircraft_id", "AC1"), ("sql", "SELECT * FROM aircraft")),
        ),
        host,
        tool_call_id="tool-1",
        action_id="read-sql",
    )
    arn = _port(client).execute(
        _live_binding(idempotency_key="idem-arn", invocation_id="inv-arn", correlation_id="corr-arn"),
        _request(
            _live_binding(idempotency_key="idem-arn", invocation_id="inv-arn", correlation_id="corr-arn"),
            resource_id="arn:aws:dynamodb:us-west-1:123:table/aircraft",
        ),
        host,
        tool_call_id="tool-1",
        action_id="read-arn",
    )
    write = _port(client).execute(
        _live_binding(idempotency_key="idem-write", invocation_id="inv-write", correlation_id="corr-write"),
        _request(
            _live_binding(idempotency_key="idem-write", invocation_id="inv-write", correlation_id="corr-write"),
            tool_name="put_item",
        ),
        host,
        tool_call_id="tool-1",
        action_id="read-write",
    )
    assert scanned.refusal is ScopedAwsReadRefusal.SCAN_NOT_PERMITTED
    assert ReadBoundaryDenial.SCAN_NOT_PERMITTED in scanned.receipt.denial_reasons
    assert sql.refusal is ScopedAwsReadRefusal.ARBITRARY_SQL
    assert ReadBoundaryDenial.ARBITRARY_SQL in sql.receipt.denial_reasons
    assert arn.refusal is ScopedAwsReadRefusal.RESOURCE_NOT_ALLOWLISTED
    assert write.refusal is ScopedAwsReadRefusal.WRITE_NOT_PERMITTED
    assert scanned.adapter_invoked is False
    assert sql.adapter_invoked is False
    assert arn.adapter_invoked is False
    assert write.adapter_invoked is False
    assert client.operations == []
    _closed(scanned)
    _closed(sql)


def test_seven_day_window_is_rejected_rather_than_clipped():
    client = bind_offline_aws_read_client(
        query_pages={"historical.encounters": [{"Items": [{"record_id": "enc-1"}]}]}
    )
    binding = _historical_binding()
    result = _port(client).execute(
        binding,
        _request(
            binding,
            requested_arguments=(
                ("aircraft_id", "AC1"),
                ("start_utc", EIGHT_DAY_START),
                ("end_utc", HIST_END),
            ),
        ),
        _historical_host(),
        tool_call_id="tool-1",
        action_id="read-8d",
    )
    assert result.refusal is ScopedAwsReadRefusal.HISTORICAL_INTERVAL_ESCALATION
    assert result.adapter_invoked is False
    assert client.operations == []
    assert result.scope is not None
    assert result.scope.historical_window.eligible_start_utc in {None, HIST_START}
    _closed(result)


def test_server_scope_wins_over_caller_region_and_caller_authorized():
    client = bind_offline_aws_read_client(
        get_items={("aircraft", "AC1"): {"aircraft_id": "AC1"}}
    )
    binding = _live_binding()
    widened = _port(client).execute(
        binding,
        _request(binding, requested_arguments=(("aircraft_id", "AC1"), ("region", "TX"))),
        _live_host(),
        tool_call_id="tool-1",
        action_id="read-tx",
        location_hint="Texas",
    )
    authorized = _port(client).execute(
        _live_binding(idempotency_key="idem-auth", invocation_id="inv-auth", correlation_id="corr-auth"),
        _request(
            _live_binding(idempotency_key="idem-auth", invocation_id="inv-auth", correlation_id="corr-auth"),
            caller_authorized=True,
        ),
        _live_host(),
        tool_call_id="tool-1",
        action_id="read-auth",
    )
    caller_scope = _port(client).execute(
        _live_binding(idempotency_key="idem-scope", invocation_id="inv-scope", correlation_id="corr-scope"),
        _request(
            _live_binding(idempotency_key="idem-scope", invocation_id="inv-scope", correlation_id="corr-scope"),
            caller_scope={"region": "US"},
        ),
        _live_host(),
        tool_call_id="tool-1",
        action_id="read-caller-scope",
    )
    assert widened.adapter_invoked is False
    assert authorized.adapter_invoked is False
    assert caller_scope.adapter_invoked is False
    assert caller_scope.refusal is ScopedAwsReadRefusal.SCOPE_DENIED
    assert client.operations == []
    _closed(widened)
    _closed(authorized)
    _closed(caller_scope)


def test_repeated_idempotency_key_hits_the_adapter_once():
    client = bind_offline_aws_read_client(
        get_items={
            ("aircraft", "AC1"): {
                "aircraft_id": "AC1",
                "source_version": "aircraft-v1",
                "observed_at_utc": SOURCE_OBSERVED,
            }
        }
    )
    binding = _live_binding()
    port = _port(client)
    first = port.execute(
        binding,
        _request(binding),
        _live_host(),
        tool_call_id="tool-1",
        action_id="read-1",
        location_hint="California",
    )
    second = port.execute(
        binding,
        _request(binding),
        _live_host(),
        tool_call_id="tool-2",
        action_id="read-2",
        location_hint="California",
    )
    assert first.adapter_invoked is True
    assert second.adapter_invoked is True
    assert first.receipt is not None and second.receipt is not None
    assert first.receipt.record_ids == second.receipt.record_ids
    assert client.operations == ["get_item"]
    _closed(first)
    _closed(second)


def test_cancellation_and_deadline_stop_the_read():
    client = bind_offline_aws_read_client(
        get_items={("aircraft", "AC1"): {"aircraft_id": "AC1", "observed_at_utc": SOURCE_OBSERVED}}
    )
    binding = _live_binding()
    cancellation = bind_nonproduction_cancellation()
    cancellation.cancel()
    cancelled = _port(client).execute(
        binding,
        _request(binding),
        _live_host(),
        tool_call_id="tool-1",
        action_id="read-cancel",
        cancellation=cancellation,
        location_hint="California",
    )
    assert cancelled.adapter_invoked is False
    assert cancelled.refusal in {
        ScopedAwsReadRefusal.STOPPED_BEFORE_ADAPTER,
        ScopedAwsReadRefusal.NOT_ENTERED,
        ScopedAwsReadRefusal.GUARD_DENIED,
        ScopedAwsReadRefusal.BUDGET_NOT_RESERVED,
    }
    assert client.operations == []

    holder: dict[str, Sprint3CScopedReadPort] = {}

    def _advance() -> None:
        holder["port"].advance_seconds(60)

    timed_client = bind_offline_aws_read_client(
        get_items={("aircraft", "AC1"): {"aircraft_id": "AC1", "observed_at_utc": SOURCE_OBSERVED}},
        on_call=_advance,
    )
    timed_binding = _live_binding(idempotency_key="idem-time", invocation_id="inv-time", correlation_id="corr-time")
    holder["port"] = _port(timed_client)
    timed = holder["port"].execute(
        timed_binding,
        _request(timed_binding),
        _live_host(),
        tool_call_id="tool-1",
        action_id="read-time",
        location_hint="California",
    )
    assert timed.receipt is not None
    assert timed.receipt.outcome in {ReadExecutionOutcome.TIMEOUT, ReadExecutionOutcome.CANCELLED}
    assert timed.receipt.record_ids == ()
    _closed(cancelled)
    _closed(timed)


def test_empty_page_is_not_verified_zero_and_missing_record_is_not_low():
    client = bind_offline_aws_read_client()
    binding = _live_binding()
    empty = _port(client).execute(
        binding,
        _request(binding),
        _live_host(),
        tool_call_id="tool-1",
        action_id="read-empty",
        location_hint="California",
    )
    assert empty.receipt is not None
    assert empty.receipt.outcome is ReadExecutionOutcome.COMPLETED
    assert empty.receipt.page_empty is True
    assert empty.receipt.record_ids == ()
    assert empty.receipt.verified_zero_minted is False
    assert empty.inferred_risk_level is not StoredRiskLevel.LOW
    assert empty.inferred_risk_level is None
    assert empty.absent_record_is_low_risk is False

    missing_client = bind_offline_aws_read_client()
    missing_binding = _decision_binding()
    missing = _port(missing_client).execute(
        missing_binding,
        _request(missing_binding),
        _decision_host(),
        tool_call_id="tool-1",
        action_id="read-missing",
    )
    assert missing.receipt is not None
    if missing.adapter_invoked:
        assert missing.receipt.page_empty is True
        assert missing.inferred_risk_level is None
    else:
        assert missing.adapter_invoked is False
    assert missing.absent_record_is_low_risk is False
    low_claim = _port(missing_client).execute(
        _decision_binding(idempotency_key="idem-low", invocation_id="inv-low", correlation_id="corr-low"),
        _request(
            _decision_binding(idempotency_key="idem-low", invocation_id="inv-low", correlation_id="corr-low")
        ),
        _decision_host(),
        tool_call_id="tool-1",
        action_id="read-low",
        missing_risk_means_low=True,
    )
    assert low_claim.adapter_invoked is False
    assert low_claim.refusal is ScopedAwsReadRefusal.SCOPE_DENIED
    _closed(empty)
    _closed(missing)
    _closed(low_claim)


def test_stale_row_and_source_error_do_not_fabricate_completeness():
    stale_client = bind_offline_aws_read_client(
        get_items={
            ("aircraft", "AC1"): {
                "aircraft_id": "AC1",
                "source_version": "stale-v",
                "observed_at_utc": SOURCE_OBSERVED,
                "expires_at_utc": "2026-10-27T18:00:00Z",
            }
        },
        stale_keys=frozenset({("aircraft", "AC1")}),
    )
    binding = _live_binding()
    stale = _port(stale_client).execute(
        binding,
        _request(binding),
        _live_host(),
        tool_call_id="tool-1",
        action_id="read-stale",
        location_hint="California",
    )
    assert stale.receipt is not None
    assert stale.receipt.record_ids == ("AC1",)
    assert stale.receipt.truncated is True
    assert stale.receipt.source_version == "stale-v"

    error_client = bind_offline_aws_read_client(
        get_items={("aircraft", "AC1"): {"aircraft_id": "AC1"}},
        errors={"get_item": ScopedAwsSourceError("source")},
    )
    error_binding = _live_binding(idempotency_key="idem-err", invocation_id="inv-err", correlation_id="corr-err")
    failed = _port(error_client).execute(
        error_binding,
        _request(error_binding),
        _live_host(),
        tool_call_id="tool-1",
        action_id="read-err",
        location_hint="California",
    )
    assert failed.receipt is not None
    assert failed.receipt.outcome is ReadExecutionOutcome.EXCEPTION
    assert failed.receipt.record_ids == ()
    assert failed.receipt.page_empty is False
    _closed(stale)
    _closed(failed)


def test_live_client_is_not_invoked_and_scan_catalog_is_absent():
    class PretendBoto:
        def get_item(self, **_kwargs: object) -> dict[str, object]:
            raise AssertionError("live get_item")

        def query(self, **_kwargs: object) -> dict[str, object]:
            raise AssertionError("live query")

        def scan(self, **_kwargs: object) -> dict[str, object]:
            raise AssertionError("live scan")

    pretend = PretendBoto()
    binding = _live_binding()
    result = Sprint3CScopedReadPort(pretend, now_utc=NOW).execute(
        binding,
        _request(binding),
        _live_host(),
        tool_call_id="tool-1",
        action_id="read-live",
    )
    assert result.refusal is ScopedAwsReadRefusal.LIVE_AWS_NOT_INITIATED
    assert result.adapter_invoked is False
    assert result.live_aws_initiated is False
    catalog = supported_read_catalog()
    for specialist, pairs in catalog.items():
        assert pairs
        for tool_name, resource_op in pairs:
            assert "SCAN" not in resource_op
            assert tool_name in server_read_tools(MasterSpecialist(specialist))
            resource_id, operation = resource_op.split(":")
            assert (resource_id, ReadOperation(operation)) in server_read_resources(
                MasterSpecialist(specialist), tool_name
            )
            assert "sql" not in server_read_argument_names(MasterSpecialist(specialist), tool_name)
    with pytest.raises(ContractValidationError, match="consumed_attempt_is_not_a_production_grant"):
        bool(binding)
    _closed(result)


def test_source_version_absence_stays_absence():
    client = bind_offline_aws_read_client(
        get_items={
            ("aircraft", "AC1"): {
                "aircraft_id": "AC1",
                "observed_at_utc": SOURCE_OBSERVED,
            }
        }
    )
    binding = _live_binding()
    result = _port(client).execute(
        binding,
        _request(binding),
        _live_host(),
        tool_call_id="tool-1",
        action_id="read-no-version",
        location_hint="California",
    )
    assert result.receipt is not None
    assert result.receipt.source_version is None
    assert result.receipt.record_ids == ("AC1",)
    _closed(result)


def test_module_import_graph_stays_offline():
    tree = ast.parse(MODULE.read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    forbidden = {
        "boto3",
        "botocore",
        "anthropic",
        "openai",
        "terraform",
        "wilvor_ai.master_host_scoped_read_integration",
        "wilvor_ai.master_execution_grants",
        "wilvor_ai.master_execution_ledger",
        "wilvor_ai.live_ops_specialist",
        "wilvor_ai.historical_specialist",
        "wilvor_ai.decision_specialist",
        "wilvor_operational",
    }
    assert forbidden.isdisjoint(imported)
    source = MODULE.read_text(encoding="utf-8")
    for banned in FORBIDDEN_SOURCE:
        assert banned not in source
    assert "PRODUCTION_SCOPED_AWS_READ_ENABLED = False" in source
    assert "LIVE_AWS_OPERATION_PERMITTED = False" in source
    env = os.environ.copy()
    pythonpath = str(SHARED_DIR)
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = pythonpath if not existing else pythonpath + os.pathsep + existing
    env["PYTHONHASHSEED"] = "0"
    script = (
        "import sys\n"
        "forbidden = {'boto3', 'botocore', 'anthropic'}\n"
        "assert forbidden.isdisjoint(sys.modules)\n"
        "import wilvor_ai.sprint3c_scoped_read as mod\n"
        "assert forbidden.isdisjoint(sys.modules)\n"
        "assert mod.PRODUCTION_SCOPED_AWS_READ_ENABLED is False\n"
        "assert mod.LIVE_AWS_OPERATION_PERMITTED is False\n"
    )
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
