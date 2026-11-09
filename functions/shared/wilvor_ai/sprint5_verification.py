"""Sprint 5 verification labels. Offline success is not live AWS proof."""

from __future__ import annotations

from enum import Enum

from wilvor_ai.contracts import ContractValidationError
from wilvor_ai.sprint4_copilot_contract_map import SPRINT4_AI_QUERY_ROUTE_ENABLED
from wilvor_ai.sprint4_security_authorization import SPRINT4_IAM_CHANGE_APPLIED
from wilvor_ai.sprint5_copilot_host import SPRINT5_AI_QUERY_ROUTE_ENABLED
from wilvor_ai.sprint5_ledger_plan import SPRINT5_LEDGER_WRITES_ENABLED
from wilvor_ai.sprint5_live_read_plan import SPRINT5_LIVE_READ_EXECUTION_ENABLED
from wilvor_ai.sprint5_model_plan import SPRINT5_LIVE_MODEL_INVOCATION_AVAILABLE
from wilvor_ai.sprint5_security_iam import SPRINT5_IAM_CHANGE_APPLIED


SPRINT5_VERIFICATION_SCHEMA = "wilvor.ai.sprint5.verification.v1"


class Sprint5VerificationStatus(str, Enum):
    OFFLINE_VERIFIED = "OFFLINE_VERIFIED"
    LIVE_AWS_VERIFIED = "LIVE_AWS_VERIFIED"
    BLOCKED = "BLOCKED"
    NOT_IMPLEMENTED = "NOT_IMPLEMENTED"


def _fail(code: str) -> None:
    raise ContractValidationError(code)


def classify_sprint5_operations() -> dict[str, str]:
    """Label each Sprint 5 operation. Live AWS stays BLOCKED until approval."""

    if SPRINT5_IAM_CHANGE_APPLIED is not False or SPRINT4_IAM_CHANGE_APPLIED is not False:
        _fail("sprint5_iam_not_applied")
    if SPRINT5_LEDGER_WRITES_ENABLED is not False:
        _fail("sprint5_ledger_writes_disabled")
    if SPRINT5_LIVE_MODEL_INVOCATION_AVAILABLE is not False:
        _fail("live_model_invocation_unavailable")
    if SPRINT5_AI_QUERY_ROUTE_ENABLED is not False or SPRINT4_AI_QUERY_ROUTE_ENABLED is not False:
        _fail("ai_query_route_must_remain_disabled")
    if SPRINT5_LIVE_READ_EXECUTION_ENABLED is not False:
        _fail("sprint5_live_read_plan_is_not_execution")
    return {
        "schema_version": SPRINT5_VERIFICATION_SCHEMA,
        "master_modeldecision_to_route_proposal": Sprint5VerificationStatus.OFFLINE_VERIFIED.value,
        "master_langgraph_live_ops": Sprint5VerificationStatus.OFFLINE_VERIFIED.value,
        "master_langgraph_historical_athena_dispatch": Sprint5VerificationStatus.OFFLINE_VERIFIED.value,
        "master_langgraph_decision_live_dispatch": Sprint5VerificationStatus.OFFLINE_VERIFIED.value,
        "three_specialist_coordination": Sprint5VerificationStatus.OFFLINE_VERIFIED.value,
        "host_owned_identities": Sprint5VerificationStatus.OFFLINE_VERIFIED.value,
        "decision_bounded_query_no_scan": Sprint5VerificationStatus.OFFLINE_VERIFIED.value,
        "airport_station_id_resolution": Sprint5VerificationStatus.OFFLINE_VERIFIED.value,
        "catalog_tool_name_validation": Sprint5VerificationStatus.OFFLINE_VERIFIED.value,
        "copilot_host_contract_jwt_fail_closed": Sprint5VerificationStatus.OFFLINE_VERIFIED.value,
        "post_ai_query_route": Sprint5VerificationStatus.BLOCKED.value,
        "iam_least_privilege_delta": Sprint5VerificationStatus.BLOCKED.value,
        "ledger_atomic_started_admission": Sprint5VerificationStatus.BLOCKED.value,
        "specialist_live_aws_reads": Sprint5VerificationStatus.BLOCKED.value,
        "bedrock_model_invocation": Sprint5VerificationStatus.BLOCKED.value,
        "source_authenticity": Sprint5VerificationStatus.BLOCKED.value,
        "production_ai_activation": Sprint5VerificationStatus.BLOCKED.value,
        "statewide_aircraft_completeness": Sprint5VerificationStatus.NOT_IMPLEMENTED.value,
        "distributed_exactly_once_native_execution": Sprint5VerificationStatus.NOT_IMPLEMENTED.value,
    }


__all__ = [
    "SPRINT5_VERIFICATION_SCHEMA",
    "Sprint5VerificationStatus",
    "classify_sprint5_operations",
]
