"""Sprint 5 isolated nonproduction Bedrock test plan. Not invocation.

The existing reviewed provider adapter stays injected. Real model API calls
require explicit approval. The LLM cannot mint AWS authority, invent source
records, or override deterministic aviation risk calculations.
"""

from __future__ import annotations

from dataclasses import dataclass

from wilvor_ai.contracts import ContractValidationError
from wilvor_ai.sprint4_master_providers import (
    SPRINT4_LIVE_MODEL_INVOCATION_AVAILABLE,
    refuse_live_model_invocation,
)
from wilvor_ai.sprint4_security_authorization import (
    REVIEWED_DEV_ACCOUNT_ID,
    REVIEWED_DEV_REGION,
)


SPRINT5_MODEL_PLAN_SCHEMA = "wilvor.ai.sprint5.model_plan.v1"
SPRINT5_LIVE_MODEL_INVOCATION_AVAILABLE = False
MODEL_CREATES_AWS_AUTHORITY = False
MODEL_INVENTS_SOURCE_RECORDS = False
MODEL_OVERRIDES_DETERMINISTIC_RISK = False


def _fail(code: str) -> None:
    raise ContractValidationError(code)


def _closed() -> None:
    if SPRINT5_LIVE_MODEL_INVOCATION_AVAILABLE is not False:
        _fail("live_model_invocation_unavailable")
    if SPRINT4_LIVE_MODEL_INVOCATION_AVAILABLE is not False:
        _fail("live_model_invocation_unavailable")
    if MODEL_CREATES_AWS_AUTHORITY is not False:
        _fail("model_aws_authorization_rejected")
    if MODEL_INVENTS_SOURCE_RECORDS is not False:
        _fail("source_authenticity_not_established")
    if MODEL_OVERRIDES_DETERMINISTIC_RISK is not False:
        _fail("deterministic_risk_not_overridable")


@dataclass(frozen=True)
class Sprint5ModelCheck:
    check_id: str
    purpose: str
    adapter: str
    live_invocation_permitted: bool = False

    def __post_init__(self) -> None:
        if self.live_invocation_permitted is not False:
            _fail("live_model_invocation_unavailable")


@dataclass(frozen=True)
class Sprint5ModelTestPlan:
    schema_version: str
    account_id: str
    region: str
    adapter: str
    checks: tuple[Sprint5ModelCheck, ...]
    execution_enabled: bool
    model_creates_aws_authority: bool
    model_invents_source_records: bool
    model_overrides_deterministic_risk: bool

    def __post_init__(self) -> None:
        _closed()
        if self.schema_version != SPRINT5_MODEL_PLAN_SCHEMA:
            _fail("invalid_schema_version")
        if self.execution_enabled is not False:
            _fail("live_model_invocation_unavailable")
        if self.model_creates_aws_authority is not False:
            _fail("model_aws_authorization_rejected")
        if self.model_invents_source_records is not False:
            _fail("source_authenticity_not_established")
        if self.model_overrides_deterministic_risk is not False:
            _fail("deterministic_risk_not_overridable")


def execute_sprint5_model_plan(*_args: object, **_kwargs: object) -> None:
    _closed()
    refuse_live_model_invocation()
    raise ContractValidationError("live_model_invocation_unavailable")


def sprint5_model_test_plan() -> Sprint5ModelTestPlan:
    _closed()
    adapter = "wilvor_ai.providers.bedrock_converse.BedrockConverseModelProvider"
    checks = (
        Sprint5ModelCheck("M1", "User intent classification", adapter),
        Sprint5ModelCheck("M2", "ModelDecision to MasterRouteProposal", adapter),
        Sprint5ModelCheck("M3", "One-specialist requests", adapter),
        Sprint5ModelCheck("M4", "Multi-specialist requests", adapter),
        Sprint5ModelCheck("M5", "Tool-call parameter validation", adapter),
        Sprint5ModelCheck("M6", "Authorization before tool execution", adapter),
        Sprint5ModelCheck("M7", "Runtime limits and model budgets", adapter),
        Sprint5ModelCheck("M8", "Cross-specialist evidence comparison", adapter),
        Sprint5ModelCheck("M9", "Grounded final answers", adapter),
        Sprint5ModelCheck("M10", "Refusal on missing or unsupported evidence", adapter),
    )
    return Sprint5ModelTestPlan(
        schema_version=SPRINT5_MODEL_PLAN_SCHEMA,
        account_id=REVIEWED_DEV_ACCOUNT_ID,
        region=REVIEWED_DEV_REGION,
        adapter=adapter,
        checks=checks,
        execution_enabled=False,
        model_creates_aws_authority=False,
        model_invents_source_records=False,
        model_overrides_deterministic_risk=False,
    )


__all__ = [
    "MODEL_CREATES_AWS_AUTHORITY",
    "MODEL_INVENTS_SOURCE_RECORDS",
    "MODEL_OVERRIDES_DETERMINISTIC_RISK",
    "SPRINT5_LIVE_MODEL_INVOCATION_AVAILABLE",
    "SPRINT5_MODEL_PLAN_SCHEMA",
    "Sprint5ModelCheck",
    "Sprint5ModelTestPlan",
    "execute_sprint5_model_plan",
    "sprint5_model_test_plan",
]
