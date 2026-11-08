"""Sprint 5 composition host. Offline success is not production authority.

Coordinates the actual Sprint 4 Live Ops authorized-tool runtime, Historical
Athena/S3 runtime, and Decision persisted runtime. Production flags stay
closed. POST /ai/query stays disabled.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from wilvor_ai.contracts import ContractValidationError, TemporalScope
from wilvor_ai.live_ops import LiveOpsCall
from wilvor_ai.master_evidence_admission import MasterSourceAuthenticity
from wilvor_ai.master_routing import MasterPlanningContext
from wilvor_ai.sprint3c_scoped_read import ConsumedAttemptReadBinding
from wilvor_ai.sprint4_decision_live import Sprint4DecisionLiveCall, Sprint4DecisionLiveResult
from wilvor_ai.sprint4_historical_tool_port import (
    HostAuthorizedHistoricalWindow,
    Sprint4HistoricalToolOutcome,
    Sprint4HistoricalToolPort,
)
from wilvor_ai.sprint4_integration_host import (
    SPRINT4_AI_QUERY_ROUTE_ENABLED,
    Sprint4HybridSlice,
    Sprint4IntegrationHost,
    assert_sprint4_integration_closed,
)
from wilvor_ai.sprint4_live_ops_tools import Sprint4LiveOpsToolOutcome
from wilvor_ai.sprint4_master_planning import Sprint4MasterPlanningResult, plan_master_request
from wilvor_ai.sprint5_copilot_host import SPRINT5_AI_QUERY_ROUTE_ENABLED
from wilvor_ai.sprint5_decision_reads import bounded_decision_context_builder
from wilvor_ai.sprint5_identity import mint_host_owned_identities
from wilvor_ai.sprint5_ledger_plan import SPRINT5_LEDGER_WRITES_ENABLED
from wilvor_ai.sprint5_master_planning import decode_planner_model_output
from wilvor_ai.sprint5_model_plan import SPRINT5_LIVE_MODEL_INVOCATION_AVAILABLE
from wilvor_ai.sprint5_security_iam import SPRINT5_IAM_CHANGE_APPLIED


SPRINT5_INTEGRATION_SCHEMA = "wilvor.ai.sprint5.integration_host.v1"
SPRINT5_PRODUCTION_INTEGRATION_AVAILABLE = False
SPRINT5_SOURCE_AUTHENTICITY_FROM_OFFLINE_DOUBLES = False
SPRINT5_CLAIMS_ADMITTED = False
SPRINT5_HISTORICAL_DYNAMODB_QUERY_SUBSTITUTED = False

SPRINT5_CLOSED_SEAMS = (
    "Master planner decodes route-proposal mappings or UNSUPPORTED/REFUSAL ModelDecision.",
    "LangGraph Historical may set use_athena to skip DynamoDB Query fact retrieval.",
    "LangGraph Decision may dispatch execute_sprint4_decision_live without FakeDecisionReadCatalog.",
    "Decision aircraft context uses bounded Query-by-aircraft; Scan is rejected on wrapped tables.",
    "airport_id resolves to AirportStatus.station_id; no airport_id METAR/TAF fallback.",
    "Host mints six pairwise-distinct identities. Browser Copilot ids stay untrusted labels.",
    "Specialist tool names must match server_read_tools catalogs. Display aliases are rejected.",
)

SPRINT5_REMAINING_BLOCKERS = (
    "IAM delta proposed, not applied. Current role has GetItem on aircraft only.",
    "Ledger TransactWriteItems not live-AWS validated in Sprint 5.",
    "Historical Athena/Glue/S3 query policy remains unattached.",
    "Bedrock invoke remains denied. Live model API is not authorized.",
    "POST /ai/query stays disabled. Copilot default transport remains LOCAL_DISABLED.",
    "MasterSourceAuthenticity stays NOT_ESTABLISHED.",
    "Decision host proofs SOURCE_CURRENTNESS, ALLOWED_TARGET_POPULATION, and SEPARATE_PERSISTED_RECOMMENDATION_AUTHORIZATION stay unissued.",
    "Existing aircraft geographic collection is limited; statewide completeness is not established.",
)


def _fail(code: str) -> None:
    raise ContractValidationError(code)


def _closed() -> None:
    if SPRINT5_PRODUCTION_INTEGRATION_AVAILABLE is not False:
        _fail("sprint5_production_integration_unavailable")
    if SPRINT5_CLAIMS_ADMITTED is not False:
        _fail("operational_claim_not_admitted")
    if SPRINT5_SOURCE_AUTHENTICITY_FROM_OFFLINE_DOUBLES is not False:
        _fail("source_authenticity_not_established")
    if SPRINT5_HISTORICAL_DYNAMODB_QUERY_SUBSTITUTED is not False:
        _fail("historical_dynamodb_query_not_substituted")
    if SPRINT5_AI_QUERY_ROUTE_ENABLED is not False or SPRINT4_AI_QUERY_ROUTE_ENABLED is not False:
        _fail("ai_query_route_must_remain_disabled")
    if SPRINT5_IAM_CHANGE_APPLIED is not False:
        _fail("sprint5_iam_not_applied")
    if SPRINT5_LEDGER_WRITES_ENABLED is not False:
        _fail("sprint5_ledger_writes_disabled")
    if SPRINT5_LIVE_MODEL_INVOCATION_AVAILABLE is not False:
        _fail("live_model_invocation_unavailable")
    assert_sprint4_integration_closed()


@dataclass(frozen=True)
class Sprint5CoordinatedSlice:
    """Three Sprint 4 specialist artifacts kept side by side. Not a fused claim."""

    identities: object
    planning: Sprint4MasterPlanningResult | None
    live_ops: Sprint4LiveOpsToolOutcome | None
    historical: Sprint4HistoricalToolOutcome | None
    decision: Sprint4DecisionLiveResult | None
    hybrid: Sprint4HybridSlice
    source_authenticity: MasterSourceAuthenticity
    claims_admitted: bool
    production_dispatch: bool
    california_coverage_fabricated: bool = False

    def __post_init__(self) -> None:
        _closed()
        if self.source_authenticity is not MasterSourceAuthenticity.NOT_ESTABLISHED:
            _fail("source_authenticity_not_established")
        if self.claims_admitted is not False:
            _fail("operational_claim_not_admitted")
        if self.production_dispatch is not False:
            _fail("production_dispatch_disabled")
        if self.california_coverage_fabricated is not False:
            _fail("california_coverage_not_fabricated")


class Sprint5IntegrationHost:
    """Compose Sprint 4 specialist runtimes behind Sprint 5 identity and reads."""

    def __init__(self) -> None:
        _closed()
        self._inner = Sprint4IntegrationHost()

    @property
    def inner(self) -> Sprint4IntegrationHost:
        return self._inner

    def plan_master(self, context: MasterPlanningContext, planner: object) -> Sprint4MasterPlanningResult:
        _closed()
        return plan_master_request(context, planner)

    def decode_planner_output(self, raw: object, context: MasterPlanningContext) -> object:
        _closed()
        return decode_planner_model_output(raw, context)

    def execute_live_ops_tool(
        self,
        binding: ConsumedAttemptReadBinding,
        *,
        tables: object,
        call: LiveOpsCall,
        tool_name: str,
        arguments: Mapping[str, object] | None = None,
    ) -> Sprint4LiveOpsToolOutcome:
        _closed()
        return self._inner.execute_live_ops_tool(
            binding,
            tables=tables,
            call=call,
            tool_name=tool_name,
            arguments=arguments,
        )

    def execute_historical_athena_tool(
        self,
        port: Sprint4HistoricalToolPort,
        *,
        tool_name: str,
        arguments: Mapping[str, Any],
        tool_call_id: str,
        window: HostAuthorizedHistoricalWindow,
    ) -> Sprint4HistoricalToolOutcome:
        _closed()
        return self._inner.execute_historical_athena_tool(
            port,
            tool_name=tool_name,
            arguments=arguments,
            tool_call_id=tool_call_id,
            window=window,
        )

    def execute_decision_live(self, call: Sprint4DecisionLiveCall) -> Sprint4DecisionLiveResult:
        _closed()
        if call.context_builder is None:
            call = Sprint4DecisionLiveCall(
                tables=call.tables,
                now_epoch=call.now_epoch,
                query_timestamp_utc=call.query_timestamp_utc,
                aircraft_id=call.aircraft_id,
                recommendation_id=call.recommendation_id,
                tool_call_id_prefix=call.tool_call_id_prefix,
                correlation_id=call.correlation_id,
                consumed_attempt=call.consumed_attempt,
                context_builder=bounded_decision_context_builder(),
            )
        return self._inner.execute_decision_live(call)

    def coordinate(
        self,
        *,
        context: MasterPlanningContext | None = None,
        planner: object | None = None,
        live_ops: Sprint4LiveOpsToolOutcome | None = None,
        historical: Sprint4HistoricalToolOutcome | None = None,
        decision: Sprint4DecisionLiveResult | None = None,
    ) -> Sprint5CoordinatedSlice:
        """Keep current, historical, and decision artifacts distinct."""

        _closed()
        identities = mint_host_owned_identities()
        planning = None
        if context is not None and planner is not None:
            planning = self.plan_master(context, planner)
        hybrid = self._inner.compose_hybrid_slice(
            live_ops=live_ops,
            historical=historical,
            decision=decision,
        )
        scopes = hybrid.temporal_scopes
        if live_ops is not None and TemporalScope.HISTORICAL in scopes and decision is not None:
            if any(scope is TemporalScope.CURRENT for scope in scopes) is False:
                pass
        return Sprint5CoordinatedSlice(
            identities=identities,
            planning=planning,
            live_ops=live_ops,
            historical=historical,
            decision=decision,
            hybrid=hybrid,
            source_authenticity=MasterSourceAuthenticity.NOT_ESTABLISHED,
            claims_admitted=False,
            production_dispatch=False,
            california_coverage_fabricated=False,
        )


def remaining_sprint5_blockers() -> tuple[str, ...]:
    _closed()
    return SPRINT5_REMAINING_BLOCKERS


def closed_sprint5_seams() -> tuple[str, ...]:
    _closed()
    return SPRINT5_CLOSED_SEAMS


__all__ = [
    "SPRINT5_CLAIMS_ADMITTED",
    "SPRINT5_CLOSED_SEAMS",
    "SPRINT5_HISTORICAL_DYNAMODB_QUERY_SUBSTITUTED",
    "SPRINT5_INTEGRATION_SCHEMA",
    "SPRINT5_PRODUCTION_INTEGRATION_AVAILABLE",
    "SPRINT5_REMAINING_BLOCKERS",
    "SPRINT5_SOURCE_AUTHENTICITY_FROM_OFFLINE_DOUBLES",
    "Sprint5CoordinatedSlice",
    "Sprint5IntegrationHost",
    "closed_sprint5_seams",
    "remaining_sprint5_blockers",
]
