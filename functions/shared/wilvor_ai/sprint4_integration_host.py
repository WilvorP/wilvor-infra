"""Sprint 4 six-agent integration host.

Composes the merged Live Ops, Historical Athena, Decision, Master, Security,
and Copilot mapping modules onto existing Sprint 3C/3D contracts. This module
does not rebuild specialists, apply IAM, deploy Lambdas, or enable
``POST /ai/query``.

Offline injected execution is not production dispatch and is not source
authentication. Historical facts use the Athena/S3 query path. DynamoDB Query
is not substituted for historical retrieval.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from wilvor_ai.contracts import ContractValidationError, TemporalScope
from wilvor_ai.historical_specialist import HistoricalAnalyticsSpecialist
from wilvor_ai.live_ops import LiveOpsCall
from wilvor_ai.master_authorized_scheduler_adapter import PRODUCTION_DISPATCH_ENABLED
from wilvor_ai.master_evidence_admission import MasterSourceAuthenticity
from wilvor_ai.master_routing import MasterPlanningContext
from wilvor_ai.sprint3c_scoped_read import ConsumedAttemptReadBinding
from wilvor_ai.sprint3d_auth_grants import route_enablement_permitted
from wilvor_ai.sprint4_copilot_contract_map import (
    SPRINT4_AI_QUERY_ROUTE_ENABLED,
    Sprint4CopilotMappedRequest,
    map_copilot_session_query,
    project_ma5_to_copilot,
)
from wilvor_ai.sprint4_decision_live import (
    PRODUCTION_DECISION_LIVE_AWS_ENABLED,
    Sprint4DecisionLiveCall,
    Sprint4DecisionLiveResult,
    execute_sprint4_decision_live,
)
from wilvor_ai.sprint4_historical_query_runtime import (
    HISTORICAL_QUERY_IAM_ATTACHED,
    PRODUCTION_HISTORICAL_ATHENA_ENABLED,
    Sprint4HistoricalQueryRuntime,
    compose_sprint4_historical_query_runtime,
)
from wilvor_ai.sprint4_historical_tool_port import (
    HostAuthorizedHistoricalWindow,
    Sprint4HistoricalToolOutcome,
    Sprint4HistoricalToolPort,
    bind_sprint4_historical_tool_port,
)
from wilvor_ai.sprint4_live_ops_authorization import PRODUCTION_LIVE_OPS_AWS_READ_ENABLED
from wilvor_ai.sprint4_live_ops_tools import Sprint4LiveOpsToolOutcome, run_authorized_live_ops_tool
from wilvor_ai.sprint4_master_planning import Sprint4MasterPlanningResult, plan_master_request
from wilvor_ai.sprint4_master_providers import SPRINT4_LIVE_MODEL_INVOCATION_AVAILABLE
from wilvor_ai.sprint4_master_runtime import (
    SPRINT4_CLAIMS_ADMITTED,
    SPRINT4_NATIVE_PRODUCTION_DISPATCH_AVAILABLE,
    Sprint4MasterRuntimeResult,
    run_sprint4_master_runtime,
)
from wilvor_ai.sprint4_security_authorization import (
    SPRINT4_IAM_CHANGE_APPLIED,
    SPRINT4_PRODUCTION_AUTHORIZATION_ENABLED,
    reviewed_iam_change_proposal,
)
from wilvor_ai.sprint4_security_ledger import (
    SPRINT4_ATOMIC_ADMISSION_IS_NATIVE_EXECUTION,
    SPRINT4_LEDGER_WRITES_ENABLED,
    SPRINT4_LIVE_AWS_ENABLED,
    Sprint4AdmissionResult,
    Sprint4SecurityLedgerPort,
)


SPRINT4_INTEGRATION_SCHEMA_VERSION = "wilvor.ai.sprint4.integration_host.v1"
SPRINT4_PRODUCTION_INTEGRATION_AVAILABLE = False
SPRINT4_OFFLINE_LEDGER_IS_AWS_VALIDATION = False
SPRINT4_HISTORICAL_DYNAMODB_QUERY_SUBSTITUTED = False
SPRINT4_CLAIMS_ADMITTED_GLOBALLY = False
SPRINT4_SOURCE_AUTHENTICITY_FROM_OFFLINE_DOUBLES = False

SPRINT4_UNRESOLVED_SEAMS = (
    "I1 host-authenticated production principal remains unissued.",
    "I2/I3 production grant issuance and signed-key store remain closed.",
    "Ledger TransactWriteItems IAM is proposed, not applied. Offline FakeDynamoClient is not AWS ledger validation.",
    "Live Ops Query IAM for hazards, hazard_coordinates, encounters, and the aircraft callsign GSI is not attached.",
    "Historical Athena/Glue/S3 query IAM remains unattached. Do not attach dynamodb:Query on historical tables as a substitute.",
    "Decision host proofs SOURCE_CURRENTNESS, ALLOWED_TARGET_POPULATION, and SEPARATE_PERSISTED_RECOMMENDATION_AUTHORIZATION stay unissued.",
    "Decision Scan-named readers in decision_context are out of I5 contract; Query-by-aircraft readers exist but are not remapped here.",
    "airport_id is not resolved to AirportStatus.station_id for METAR/TAF lookup.",
    "Agent D LangGraph still dispatches Historical through Sprint 3D execute_historical_native (DynamoDB Query). Sprint 4 Athena is the authorized fact path on this host.",
    "Agent D LangGraph Decision dispatch still requires FakeDecisionReadCatalog and does not call execute_sprint4_decision_live.",
    "Master planner cannot decode Bedrock/Anthropic ModelDecision into MasterRouteProposal.",
    "Browser Copilot ids are not MasterPlanningSessionRegistry membership and are not JWT planning_session_id.",
    "POST /ai/query stays disabled. Copilot default transport remains LOCAL_DISABLED.",
    "MasterSourceAuthenticity stays NOT_ESTABLISHED. Offline doubles do not establish it.",
)

SPRINT4_PRODUCTION_DEPENDENCIES = (
    "Reviewed RS256 gateway principal and JWT planning_session_id binding.",
    "Production HMAC/RS256 grant issuance with rotating nonce and no-TTL consume marker.",
    "Operator-applied ledger TransactWriteItems on the execution ledger table only.",
    "Operator-applied Live Ops Query allowlist: hazards, hazard_coordinates, encounters, aircraft GetItem, callsign GSI if find_aircraft_by_callsign is authorized.",
    "Operator-applied Athena/Glue/S3 historical query IAM on the dedicated workgroup, not DynamoDB Query.",
    "Decision GetItem/Query allowlist for risks, recommendations, airport_assessments, METAR, and TAF after Scan removal.",
    "Issued Decision host proofs before DecisionSpecialist.run.",
    "Dedicated AI Lambda, enable_ai_query_route still false until a reviewed HTTP client exists.",
    "Bedrock/Anthropic remain injected test adapters until a reviewed live-model gate.",
)


def _fail(code: str) -> None:
    raise ContractValidationError(code)


def _closed() -> None:
    if SPRINT4_PRODUCTION_INTEGRATION_AVAILABLE is not False:
        _fail("sprint4_production_integration_unavailable")
    if SPRINT4_CLAIMS_ADMITTED_GLOBALLY is not False or SPRINT4_CLAIMS_ADMITTED is not False:
        _fail("operational_claim_not_admitted")
    if SPRINT4_SOURCE_AUTHENTICITY_FROM_OFFLINE_DOUBLES is not False:
        _fail("source_authenticity_not_established")
    if SPRINT4_OFFLINE_LEDGER_IS_AWS_VALIDATION is not False:
        _fail("offline_ledger_is_not_aws_validation")
    if SPRINT4_HISTORICAL_DYNAMODB_QUERY_SUBSTITUTED is not False:
        _fail("historical_dynamodb_query_not_substituted")
    if SPRINT4_AI_QUERY_ROUTE_ENABLED is not False or route_enablement_permitted() is not False:
        _fail("ai_query_route_must_remain_disabled")
    if PRODUCTION_DISPATCH_ENABLED is not False:
        _fail("production_dispatch_disabled")
    if SPRINT4_NATIVE_PRODUCTION_DISPATCH_AVAILABLE is not False:
        _fail("production_dispatch_disabled")
    if SPRINT4_LIVE_MODEL_INVOCATION_AVAILABLE is not False:
        _fail("live_model_invocation_unavailable")
    if SPRINT4_LEDGER_WRITES_ENABLED is not False or SPRINT4_LIVE_AWS_ENABLED is not False:
        _fail("sprint4_live_aws_not_executed")
    if SPRINT4_IAM_CHANGE_APPLIED is not False or SPRINT4_PRODUCTION_AUTHORIZATION_ENABLED is not False:
        _fail("sprint4_iam_not_applied")
    if PRODUCTION_LIVE_OPS_AWS_READ_ENABLED is not False:
        _fail("production_live_ops_aws_read_not_enabled")
    if PRODUCTION_HISTORICAL_ATHENA_ENABLED is not False or HISTORICAL_QUERY_IAM_ATTACHED is not False:
        _fail("production_historical_query_not_enabled")
    if PRODUCTION_DECISION_LIVE_AWS_ENABLED is not False:
        _fail("production_decision_live_aws_not_enabled")
    if SPRINT4_ATOMIC_ADMISSION_IS_NATIVE_EXECUTION is not False:
        _fail("atomic_admission_is_not_native_execution")


def assert_sprint4_integration_closed() -> None:
    """Fail closed if any production gate was flipped."""

    _closed()


@dataclass(frozen=True)
class Sprint4SpecialistRuntimeBinding:
    """Which runtime this host uses for each specialist. Not authorization."""

    live_ops: str
    historical: str
    decision: str
    master: str
    security: str
    historical_uses_athena: bool
    historical_uses_dynamodb_query: bool
    decision_host_proofs_issued: bool
    source_authenticity: MasterSourceAuthenticity
    claims_admitted: bool

    def __post_init__(self) -> None:
        _closed()
        if self.historical_uses_athena is not True:
            _fail("historical_athena_runtime_required")
        if self.historical_uses_dynamodb_query is not False:
            _fail("historical_dynamodb_query_not_substituted")
        if self.decision_host_proofs_issued is not False:
            _fail("decision_host_proofs_unissued")
        if self.source_authenticity is not MasterSourceAuthenticity.NOT_ESTABLISHED:
            _fail("source_authenticity_not_established")
        if self.claims_admitted is not False:
            _fail("operational_claim_not_admitted")


@dataclass(frozen=True)
class Sprint4HybridSlice:
    """Current plus historical artifacts kept side by side. Not a fused claim."""

    live_ops: Sprint4LiveOpsToolOutcome | None
    historical: Sprint4HistoricalToolOutcome | None
    decision: Sprint4DecisionLiveResult | None
    temporal_scopes: tuple[TemporalScope, ...]
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


class Sprint4IntegrationHost:
    """Offline composition of the six Sprint 4 agents onto Sprint 3C/3D seams."""

    def __init__(self, ledger_port: Sprint4SecurityLedgerPort | None = None) -> None:
        _closed()
        if ledger_port is not None and type(ledger_port) is not Sprint4SecurityLedgerPort:
            _fail("invalid_sprint4_ledger_port")
        self._ledger = ledger_port

    @property
    def ledger(self) -> Sprint4SecurityLedgerPort | None:
        return self._ledger

    def specialist_runtime_binding(self) -> Sprint4SpecialistRuntimeBinding:
        _closed()
        return Sprint4SpecialistRuntimeBinding(
            live_ops="wilvor_ai.sprint4_live_ops_tools.run_authorized_live_ops_tool",
            historical="wilvor_ai.sprint4_historical_query_runtime + HistoricalAnalyticsSpecialist",
            decision="wilvor_ai.sprint4_decision_live.execute_sprint4_decision_live",
            master="wilvor_ai.sprint4_master_runtime.run_sprint4_master_runtime",
            security="wilvor_ai.sprint4_security_ledger.Sprint4SecurityLedgerPort.admit_started",
            historical_uses_athena=True,
            historical_uses_dynamodb_query=False,
            decision_host_proofs_issued=False,
            source_authenticity=MasterSourceAuthenticity.NOT_ESTABLISHED,
            claims_admitted=False,
        )

    def admit_started(self, claim: object, **kwargs: Any) -> Sprint4AdmissionResult:
        """Consume one signed grant through the Sprint 4 security port."""

        _closed()
        if self._ledger is None:
            _fail("sprint4_ledger_port_required")
        result = self._ledger.admit_started(claim, **kwargs)
        if result.audit is not None and result.audit.verified_aws_provenance is not False:
            _fail("offline_ledger_is_not_aws_validation")
        return result

    def execute_live_ops_tool(
        self,
        binding: ConsumedAttemptReadBinding,
        *,
        tables: object,
        call: LiveOpsCall,
        tool_name: str,
        arguments: Mapping[str, object] | None = None,
    ) -> Sprint4LiveOpsToolOutcome:
        """Authorized Live Ops tool on wrapped injected tables. Not live AWS."""

        _closed()
        outcome = run_authorized_live_ops_tool(
            binding,
            tables=tables,  # type: ignore[arg-type]
            call=call,
            tool_name=tool_name,
            arguments=arguments,
        )
        if outcome.source_report.source_authenticity is not MasterSourceAuthenticity.NOT_ESTABLISHED:
            _fail("source_authenticity_not_established")
        if outcome.verified_zero is not False:
            _fail("empty_page_is_not_verified_zero")
        if outcome.source_report.region_label_is_polygon is not False:
            _fail("region_label_is_not_polygon")
        return outcome

    def compose_historical_athena_runtime(
        self,
        *,
        athena_client: object,
        s3_client: object,
        config: object,
        monotonic_clock: Callable[[], float] | None = None,
        sleeper: Callable[[float], None] | None = None,
    ) -> Sprint4HistoricalQueryRuntime:
        """Compose the Athena/S3 historical runtime. DynamoDB Query is not used."""

        _closed()
        runtime = compose_sprint4_historical_query_runtime(
            athena_client=athena_client,
            s3_client=s3_client,
            config=config,  # type: ignore[arg-type]
            monotonic_clock=monotonic_clock,
            sleeper=sleeper,
        )
        if runtime.live_athena_initiated is not False:
            _fail("live_athena_not_initiated")
        return runtime

    def bind_historical_tool_port(
        self,
        runtime: Sprint4HistoricalQueryRuntime,
    ) -> Sprint4HistoricalToolPort:
        _closed()
        return bind_sprint4_historical_tool_port(runtime)

    def compose_historical_specialist(
        self,
        runtime: Sprint4HistoricalQueryRuntime,
        *,
        provider: object,
        tool_call_id_factory: Callable[[], str] | None = None,
    ) -> HistoricalAnalyticsSpecialist:
        """Inject Athena operations into the existing Historical specialist."""

        _closed()
        if type(runtime) is not Sprint4HistoricalQueryRuntime:
            _fail("invalid_historical_query_runtime")
        return HistoricalAnalyticsSpecialist(
            provider=provider,  # type: ignore[arg-type]
            operations=runtime.operations,
            tool_call_id_factory=tool_call_id_factory,
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
        outcome = port.invoke(
            tool_name=tool_name,
            arguments=arguments,
            tool_call_id=tool_call_id,
            window=window,
        )
        if outcome.production_query is not False or outcome.live_athena_initiated is not False:
            _fail("production_historical_query_not_enabled")
        if (
            outcome.provenance is not None
            and outcome.provenance.temporal_scope is not TemporalScope.HISTORICAL
        ):
            _fail("historical_temporal_scope_required")
        if (
            outcome.provenance is not None
            and outcome.provenance.represented_as_current_observation is not False
        ):
            _fail("historical_facts_are_not_current_observations")
        return outcome

    def execute_decision_live(self, call: Sprint4DecisionLiveCall) -> Sprint4DecisionLiveResult:
        """Advisory Decision join. Missing risk stays missing. Proofs stay unissued."""

        _closed()
        result = execute_sprint4_decision_live(call)
        if result.source_authenticity is not MasterSourceAuthenticity.NOT_ESTABLISHED:
            _fail("source_authenticity_not_established")
        if result.missing.synthesized_low_risk is not False:
            _fail("absent_risk_is_not_low")
        if result.advisory.advisory_only is not True:
            _fail("decision_must_remain_advisory")
        return result

    def plan_master(
        self,
        context: MasterPlanningContext,
        planner: object,
    ) -> Sprint4MasterPlanningResult:
        _closed()
        return plan_master_request(context, planner)

    def run_master(
        self,
        context: MasterPlanningContext,
        planner: object,
        **kwargs: Any,
    ) -> Sprint4MasterRuntimeResult:
        """Existing LangGraph native dispatch. Live model calls stay refused."""

        _closed()
        result = run_sprint4_master_runtime(context, planner, **kwargs)
        if result.synthesis is not None:
            if result.synthesis.claims_admitted is not False:
                _fail("operational_claim_not_admitted")
            if result.synthesis.source_authenticity is not MasterSourceAuthenticity.NOT_ESTABLISHED:
                _fail("source_authenticity_not_established")
        return result

    def map_copilot_query(self, payload: Mapping[str, Any]) -> Sprint4CopilotMappedRequest:
        _closed()
        return map_copilot_session_query(payload)

    def project_master_to_copilot(self, **kwargs: Any) -> object:
        _closed()
        return project_ma5_to_copilot(**kwargs)

    def compose_hybrid_slice(
        self,
        *,
        live_ops: Sprint4LiveOpsToolOutcome | None = None,
        historical: Sprint4HistoricalToolOutcome | None = None,
        decision: Sprint4DecisionLiveResult | None = None,
    ) -> Sprint4HybridSlice:
        """Keep current and historical artifacts distinct. Do not fuse claims."""

        _closed()
        scopes: list[TemporalScope] = []
        if live_ops is not None and live_ops.tool_result is not None:
            scopes.extend(item.temporal_scope for item in live_ops.tool_result.evidence)
        if historical is not None and historical.provenance is not None:
            scopes.append(historical.provenance.temporal_scope)
        if decision is not None:
            for invocation in decision.invocations.values():
                scopes.append(invocation.raw_tool_result.temporal_scope)
        return Sprint4HybridSlice(
            live_ops=live_ops,
            historical=historical,
            decision=decision,
            temporal_scopes=tuple(scopes),
            source_authenticity=MasterSourceAuthenticity.NOT_ESTABLISHED,
            claims_admitted=False,
            production_dispatch=False,
            california_coverage_fabricated=False,
        )


def refuse_closed_production_controls(*_args: object, **_kwargs: object) -> None:
    """Keep every production issuer and live AWS entrypoint closed."""

    _closed()
    raise ContractValidationError("sprint4_production_controls_closed")


def sprint4_iam_reconciliation() -> dict[str, object]:
    """Record F's remaining IAM deltas against A/B/C actual resource needs."""

    _closed()
    proposal = reviewed_iam_change_proposal()
    return {
        "proposal_status": proposal.status,
        "applied": False,
        "historical_dynamodb_query_forbidden": True,
        "live_ops_query_resources_needed": (
            "hazards QUERY",
            "hazard_coordinates QUERY",
            "encounters QUERY",
            "aircraft GET_ITEM",
            "aircraft callsign GSI QUERY if find_aircraft_by_callsign is authorized",
        ),
        "historical_athena_resources_needed": (
            "athena Start/Get/Stop query on dedicated workgroup",
            "glue GetDatabase/GetTable on historical facts",
            "s3 coverage metadata read and workgroup results prefix",
        ),
        "decision_resources_needed": (
            "recommendations GET_ITEM",
            "airport_assessments QUERY",
            "metar GET_ITEM",
            "taf GET_ITEM",
        ),
        "must_not_grant": proposal.must_not_grant,
        "remaining_deltas_from_agent_f": proposal.remaining_deltas,
        "agent_f_historical_dynamodb_query_remainder_rejected": True,
    }


def unresolved_production_dependencies() -> tuple[str, ...]:
    _closed()
    return SPRINT4_PRODUCTION_DEPENDENCIES


def unresolved_integration_seams() -> tuple[str, ...]:
    _closed()
    return SPRINT4_UNRESOLVED_SEAMS


__all__ = [
    "SPRINT4_CLAIMS_ADMITTED_GLOBALLY",
    "SPRINT4_HISTORICAL_DYNAMODB_QUERY_SUBSTITUTED",
    "SPRINT4_INTEGRATION_SCHEMA_VERSION",
    "SPRINT4_OFFLINE_LEDGER_IS_AWS_VALIDATION",
    "SPRINT4_PRODUCTION_DEPENDENCIES",
    "SPRINT4_PRODUCTION_INTEGRATION_AVAILABLE",
    "SPRINT4_SOURCE_AUTHENTICITY_FROM_OFFLINE_DOUBLES",
    "SPRINT4_UNRESOLVED_SEAMS",
    "Sprint4HybridSlice",
    "Sprint4IntegrationHost",
    "Sprint4SpecialistRuntimeBinding",
    "assert_sprint4_integration_closed",
    "refuse_closed_production_controls",
    "sprint4_iam_reconciliation",
    "unresolved_integration_seams",
    "unresolved_production_dependencies",
]
