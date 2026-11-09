"""Server allowlist checks for a proposed specialist read. Not a dispatch permit."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import timedelta

import pytest

from wilvor_ai.contracts import AgentCapability, ContractValidationError
from wilvor_ai.decision_specialist import MAX_MODEL_TURNS as DECISION_MAX_MODEL_TURNS
from wilvor_ai.decision_specialist import MAX_TOOL_CALLS as DECISION_MAX_TOOL_CALLS
from wilvor_ai.decision_tools import DECISION_TOOLS
from wilvor_ai.historical_analytics import HISTORICAL_ANALYTICS_TOOLS
from wilvor_ai.historical_specialist import AI_LIST_MAX
from wilvor_ai.historical_specialist import MAX_MODEL_TURNS as HISTORICAL_MAX_MODEL_TURNS
from wilvor_ai.historical_specialist import MAX_TOOL_CALLS as HISTORICAL_MAX_TOOL_CALLS
from wilvor_ai.live_ops import LIVE_OPS_TOOLS
from wilvor_ai.live_ops_public_contracts import (
    LIVE_OPS_HAZARD_IDS_MAX,
    LIVE_OPS_MAX_MODEL_TURNS,
    LIVE_OPS_MAX_TOOL_CALLS,
)
from wilvor_ai.master_contracts import MasterPurpose, MasterSpecialist
from wilvor_ai.master_execution_policy_boundary import (
    READ_ONLY_TOOL_NAMES_BY_SPECIALIST,
    SPECIALIST_CAPABILITY_PAIRS,
)
from wilvor_ai.master_read_resource_guard import (
    DISPATCH_GRANT_AVAILABLE,
    HISTORICAL_LIST_MAX,
    MASTER_READ_RESOURCE_GUARD_SCHEMA_VERSION,
    PRODUCTION_PERMIT_ISSUER_INTEGRATED,
    MasterReadCheckStatus,
    MasterReadDenialReason,
    MasterReadResourceProposal,
    ReadOperation,
    ServerCancellation,
    TrustedReadPrincipal,
    check_master_read_resource,
    promote_read_check_to_dispatch_permit,
    seal_ingress_principal,
    seal_population_scope,
    server_read_argument_names,
    server_read_budget,
    server_read_resources,
    server_read_tools,
)
from wilvor_ai.master_routing import MAX_HISTORICAL_WINDOW


LIVE = MasterSpecialist.LIVE_OPS
HISTORY = MasterSpecialist.HISTORICAL_ANALYTICS
DECISION = MasterSpecialist.DECISION
RETRIEVE = AgentCapability.RETRIEVE_DETERMINISTIC_OPERATIONAL_CONTEXT
HISTORICAL = AgentCapability.RETRIEVE_HISTORICAL_ANALYTICS
RISK = AgentCapability.EXPLAIN_EXISTING_RISK
RECOMMENDATIONS = AgentCapability.EXPLAIN_EXISTING_RECOMMENDATIONS
MUTATE = AgentCapability.MUTATE_OPERATIONAL_STATE
Reason = MasterReadDenialReason
OBSERVED = "2026-01-15T00:00:00Z"
DEADLINE = "2026-01-15T00:05:00Z"
START = "2026-01-08T00:00:00Z"
END = "2026-01-15T00:00:00Z"
ESCALATED_END = "2026-01-15T00:00:01Z"


def _evaluation(**overrides: object):
    from wilvor_ai.master_read_resource_guard import ServerReadEvaluation

    values: dict[str, object] = {
        "observed_utc": OBSERVED,
        "deadline_utc": DEADLINE,
        "executions_used": 0,
        "model_turns_used": 0,
        "tool_calls_used": 0,
        "cancellation": ServerCancellation(False),
    }
    values.update(overrides)
    return ServerReadEvaluation(**values)  # type: ignore[arg-type]


def _proposal(**overrides: object) -> MasterReadResourceProposal:
    values: dict[str, object] = {
        "specialist": LIVE,
        "purpose": MasterPurpose.DISCOVERY,
        "capability": RETRIEVE,
        "tool_name": "get_aircraft_operational_context",
        "resource_id": "aircraft",
        "operation": ReadOperation.GET_ITEM,
        "arguments": (("aircraft_id", "N123"),),
        "master_request_id": "master-req-1",
        "invocation_id": "inv-1",
    }
    values.update(overrides)
    return MasterReadResourceProposal(**values)  # type: ignore[arg-type]


def _check(proposal: MasterReadResourceProposal | None = None, **kwargs: object):
    values: dict[str, object] = {
        "evaluation": _evaluation(),
        "principal": seal_ingress_principal("ingress-principal-1"),
        "population_scope": None,
    }
    values.update(kwargs)
    return check_master_read_resource(
        proposal or _proposal(),
        evaluation=values["evaluation"],  # type: ignore[arg-type]
        principal=values["principal"],
        population_scope=values["population_scope"],
    )


def test_schema_and_permit_issuer_stay_closed():
    assert MASTER_READ_RESOURCE_GUARD_SCHEMA_VERSION == "wilvor.ai.master_read_resource_guard.v1"
    assert PRODUCTION_PERMIT_ISSUER_INTEGRATED is False
    assert DISPATCH_GRANT_AVAILABLE is False
    assert list(MasterReadCheckStatus) == [
        MasterReadCheckStatus.WITHIN_ALLOWLIST,
        MasterReadCheckStatus.DENIED,
    ]
    assert MAX_HISTORICAL_WINDOW == timedelta(days=7)


def test_server_allowlist_matches_public_catalogs_and_ceilings():
    catalogs = {
        LIVE: LIVE_OPS_TOOLS,
        HISTORY: HISTORICAL_ANALYTICS_TOOLS,
        DECISION: DECISION_TOOLS,
    }
    for specialist, tools in catalogs.items():
        names = tuple(spec.name for spec in tools)
        assert server_read_tools(specialist) == frozenset(names)
        assert server_read_tools(specialist) == READ_ONLY_TOOL_NAMES_BY_SPECIALIST[specialist]
        for spec in tools:
            assert server_read_argument_names(specialist, spec.name) == frozenset(
                field.name for field in spec.input_fields
            )
            assert all(operation is not ReadOperation.SCAN for _resource, operation in server_read_resources(specialist, spec.name))
    assert SPECIALIST_CAPABILITY_PAIRS[(LIVE, MasterPurpose.DISCOVERY)] == frozenset({RETRIEVE})
    assert SPECIALIST_CAPABILITY_PAIRS[(LIVE, MasterPurpose.ANSWER)] == frozenset({RETRIEVE})
    assert SPECIALIST_CAPABILITY_PAIRS[(HISTORY, MasterPurpose.ANSWER)] == frozenset({HISTORICAL})
    assert SPECIALIST_CAPABILITY_PAIRS[(DECISION, MasterPurpose.ANSWER)] == frozenset(
        {RISK, RECOMMENDATIONS}
    )
    assert server_read_budget(LIVE) == (1, LIVE_OPS_MAX_MODEL_TURNS, LIVE_OPS_MAX_TOOL_CALLS)
    assert server_read_budget(HISTORY) == (1, HISTORICAL_MAX_MODEL_TURNS, HISTORICAL_MAX_TOOL_CALLS)
    assert server_read_budget(DECISION) == (1, DECISION_MAX_MODEL_TURNS, DECISION_MAX_TOOL_CALLS)
    assert HISTORICAL_LIST_MAX == AI_LIST_MAX
    assert LIVE_OPS_HAZARD_IDS_MAX == 25


@pytest.mark.parametrize(
    ("specialist", "purpose", "capability", "tool_name", "resource_id", "operation", "arguments", "scope"),
    [
        (LIVE, MasterPurpose.DISCOVERY, RETRIEVE, "get_aircraft_operational_context", "aircraft", ReadOperation.GET_ITEM, (("aircraft_id", "N123"),), None),
        (LIVE, MasterPurpose.ANSWER, RETRIEVE, "find_aircraft_by_callsign", "aircraft", ReadOperation.QUERY, (("callsign", "UAL123"),), None),
        (LIVE, MasterPurpose.ANSWER, RETRIEVE, "get_hazard_operational_context", "hazards", ReadOperation.GET_ITEM, (("hazard_id", "H1"),), None),
        (LIVE, MasterPurpose.DISCOVERY, RETRIEVE, "get_airport_operational_context", "metar", ReadOperation.GET_ITEM, (("airport_id", "KSFO"),), None),
        (LIVE, MasterPurpose.ANSWER, RETRIEVE, "search_current_hazards", "hazards", ReadOperation.QUERY, (("region", "CA"),), frozenset({"CA"})),
        (LIVE, MasterPurpose.ANSWER, RETRIEVE, "search_current_impacts", "encounters", ReadOperation.QUERY, (("hazard_ids", ("H1", "H2")),), None),
        (LIVE, MasterPurpose.DISCOVERY, RETRIEVE, "search_current_encounters", "aircraft", ReadOperation.QUERY, (("callsign", "UAL123"),), None),
        (HISTORY, MasterPurpose.ANSWER, HISTORICAL, "summarize_historical_encounters", "historical.encounters", ReadOperation.QUERY, (("start_utc", START), ("end_utc", END), ("aircraft_id", "N123")), None),
        (HISTORY, MasterPurpose.ANSWER, HISTORICAL, "summarize_historical_risks", "historical.risks", ReadOperation.QUERY, (("start_utc", START), ("end_utc", END)), None),
        (HISTORY, MasterPurpose.ANSWER, HISTORICAL, "summarize_historical_hazard_versions", "historical.hazard_versions", ReadOperation.QUERY, (("start_utc", START), ("end_utc", END), ("hazard_id", "H1")), None),
        (HISTORY, MasterPurpose.ANSWER, HISTORICAL, "list_historical_encounters", "historical.encounters", ReadOperation.QUERY, (("start_utc", START), ("end_utc", END), ("aircraft_id", "N123"), ("limit", 25)), None),
        (DECISION, MasterPurpose.ANSWER, RISK, "get_current_risk_evidence", "aircraft", ReadOperation.GET_ITEM, (("aircraft_id", "N123"),), None),
        (DECISION, MasterPurpose.ANSWER, RISK, "get_current_decision_context", "aircraft", ReadOperation.GET_ITEM, (("aircraft_id", "N123"),), None),
        (DECISION, MasterPurpose.ANSWER, RECOMMENDATIONS, "get_current_recommendation", "aircraft", ReadOperation.GET_ITEM, (("aircraft_id", "N123"),), None),
        (DECISION, MasterPurpose.ANSWER, RECOMMENDATIONS, "get_persisted_airport_candidate_evidence", "recommendations", ReadOperation.GET_ITEM, (("recommendation_id", "R1"),), None),
        (DECISION, MasterPurpose.ANSWER, RECOMMENDATIONS, "get_persisted_airport_candidate_evidence", "airport_assessments", ReadOperation.QUERY, (("recommendation_id", "R1"),), None),
    ],
)
def test_valid_static_specialist_tool_combinations(
    specialist, purpose, capability, tool_name, resource_id, operation, arguments, scope
):
    population = None if scope is None else seal_population_scope(scope)
    result = _check(
        _proposal(
            specialist=specialist,
            purpose=purpose,
            capability=capability,
            tool_name=tool_name,
            resource_id=resource_id,
            operation=operation,
            arguments=arguments,
        ),
        population_scope=population,
    )
    assert result.status is MasterReadCheckStatus.WITHIN_ALLOWLIST
    assert result.denial_reasons == ()
    assert result.dispatch_permitted is False
    assert result.execution_permitted is False
    assert result.production_permit_issued is False


def test_observed_network_tool_is_recognized_and_not_a_scan_grant():
    result = _check(
        _proposal(
            tool_name="get_observed_network_state",
            resource_id="aircraft",
            operation=ReadOperation.SCAN,
            arguments=(),
        ),
        population_scope=seal_population_scope(frozenset({"CA"})),
    )
    assert result.status is MasterReadCheckStatus.DENIED
    assert Reason.POPULATION_SCOPE_NOT_ESTABLISHED in result.denial_reasons
    assert Reason.SCAN_NOT_PERMITTED in result.denial_reasons
    assert Reason.RECOGNIZED_TOOL_NAME_NOT_A_GRANT in result.denial_reasons
    assert server_read_resources(LIVE, "get_observed_network_state") == frozenset()


def test_unsupported_and_prohibited_tools_are_denied():
    unsupported = _check(_proposal(tool_name="describe_table", resource_id="aircraft"))
    prohibited = _check(
        _proposal(tool_name="scan_risk_candidates", resource_id="risks", operation=ReadOperation.SCAN, arguments=())
    )
    mutate = _check(_proposal(capability=MUTATE))
    assert unsupported.status is MasterReadCheckStatus.DENIED
    assert Reason.UNSUPPORTED_TOOL in unsupported.denial_reasons
    assert Reason.PROHIBITED_TOOL in prohibited.denial_reasons
    assert Reason.SCAN_NOT_PERMITTED in prohibited.denial_reasons
    assert Reason.PROHIBITED_CAPABILITY in mutate.denial_reasons


def test_cross_specialist_resource_and_tool_are_denied():
    foreign_resource = _check(_proposal(resource_id="historical.encounters"))
    foreign_tool = _check(
        _proposal(
            tool_name="summarize_historical_encounters",
            resource_id="historical.encounters",
            operation=ReadOperation.QUERY,
            arguments=(("start_utc", START), ("end_utc", END)),
        )
    )
    decision_on_live = _check(
        _proposal(
            specialist=DECISION,
            purpose=MasterPurpose.ANSWER,
            capability=RISK,
            tool_name="get_current_risk_evidence",
            resource_id="historical.risks",
            operation=ReadOperation.QUERY,
        )
    )
    assert Reason.CROSS_SPECIALIST_RESOURCE in foreign_resource.denial_reasons
    assert Reason.RECOGNIZED_TOOL_NAME_NOT_A_GRANT in foreign_resource.denial_reasons
    assert Reason.UNSUPPORTED_TOOL in foreign_tool.denial_reasons
    assert Reason.CROSS_SPECIALIST_RESOURCE in foreign_tool.denial_reasons
    assert Reason.CROSS_SPECIALIST_RESOURCE in decision_on_live.denial_reasons


def test_capability_tool_mismatch_is_denied():
    result = _check(
        _proposal(
            specialist=DECISION,
            purpose=MasterPurpose.ANSWER,
            capability=RISK,
            tool_name="get_current_recommendation",
        )
    )
    assert result.status is MasterReadCheckStatus.DENIED
    assert Reason.CAPABILITY_SPECIALIST_MISMATCH in result.denial_reasons


def test_arbitrary_table_arn_is_denied():
    result = _check(
        _proposal(resource_id="arn:aws:dynamodb:us-west-1:123456789012:table/ActiveHazards")
    )
    assert result.status is MasterReadCheckStatus.DENIED
    assert Reason.ARBITRARY_TABLE_ARN in result.denial_reasons
    assert Reason.RECOGNIZED_TOOL_NAME_NOT_A_GRANT in result.denial_reasons
    assert result.dispatch_permitted is False


def test_wildcard_access_is_denied():
    star = _check(_proposal(resource_id="*"))
    starred_arn = _check(
        _proposal(resource_id="arn:aws:dynamodb:us-east-1:999999999999:table/*")
    )
    scan = _check(_proposal(operation=ReadOperation.SCAN))
    assert Reason.WILDCARD_RESOURCE in star.denial_reasons
    assert Reason.WILDCARD_RESOURCE in starred_arn.denial_reasons
    assert Reason.ARBITRARY_TABLE_ARN in starred_arn.denial_reasons
    assert Reason.SCAN_NOT_PERMITTED in scan.denial_reasons
    assert star.status is MasterReadCheckStatus.DENIED
    assert scan.dispatch_permitted is False


def test_region_substitution_and_label_are_not_authority():
    scope = seal_population_scope(frozenset({"CA"}))
    substituted = _check(
        _proposal(
            purpose=MasterPurpose.ANSWER,
            tool_name="search_current_hazards",
            resource_id="hazards",
            operation=ReadOperation.QUERY,
            arguments=(("region", "TX"),),
        ),
        population_scope=scope,
    )
    labeled = _check(
        _proposal(
            purpose=MasterPurpose.ANSWER,
            tool_name="search_current_hazards",
            resource_id="hazards",
            operation=ReadOperation.QUERY,
            arguments=(("region", "California"),),
        ),
        population_scope=scope,
    )
    caller_scope = _check(
        _proposal(
            purpose=MasterPurpose.ANSWER,
            tool_name="search_current_hazards",
            resource_id="hazards",
            operation=ReadOperation.QUERY,
            arguments=(("region", "CA"),),
        ),
        population_scope="CA",
    )
    assert substituted.status is MasterReadCheckStatus.DENIED
    assert Reason.REGION_SUBSTITUTION in substituted.denial_reasons
    assert Reason.REGION_LABEL_NOT_AUTHORITY in labeled.denial_reasons
    assert Reason.REGION_LABEL_NOT_AUTHORITY in caller_scope.denial_reasons
    assert Reason.GEOGRAPHIC_SCOPE_NOT_ESTABLISHED in caller_scope.denial_reasons
    with pytest.raises(ContractValidationError):
        seal_population_scope(frozenset({"DC"}))
    with pytest.raises(ContractValidationError):
        seal_population_scope(frozenset({"*"}))


def test_historical_interval_escalation_is_denied():
    within = _check(
        _proposal(
            specialist=HISTORY,
            purpose=MasterPurpose.ANSWER,
            capability=HISTORICAL,
            tool_name="summarize_historical_encounters",
            resource_id="historical.encounters",
            operation=ReadOperation.QUERY,
            arguments=(("start_utc", START), ("end_utc", END)),
        )
    )
    escalated = _check(
        _proposal(
            specialist=HISTORY,
            purpose=MasterPurpose.ANSWER,
            capability=HISTORICAL,
            tool_name="summarize_historical_encounters",
            resource_id="historical.encounters",
            operation=ReadOperation.QUERY,
            arguments=(("start_utc", START), ("end_utc", ESCALATED_END)),
            historical_end_utc=ESCALATED_END,
        )
    )
    attached = _check(_proposal(historical_start_utc=START, historical_end_utc=ESCALATED_END))
    assert within.status is MasterReadCheckStatus.WITHIN_ALLOWLIST
    assert Reason.HISTORICAL_INTERVAL_ESCALATION in escalated.denial_reasons
    assert Reason.HISTORICAL_INTERVAL_ESCALATION in attached.denial_reasons
    assert escalated.status is MasterReadCheckStatus.DENIED


def test_expired_deadline_is_denied():
    result = _check(evaluation=_evaluation(deadline_utc=OBSERVED))
    earlier = _check(evaluation=_evaluation(deadline_utc="2026-01-14T23:59:59Z"))
    assert Reason.DEADLINE_EXPIRED in result.denial_reasons
    assert Reason.DEADLINE_EXPIRED in earlier.denial_reasons
    assert result.status is MasterReadCheckStatus.DENIED
    assert result.dispatch_permitted is False


def test_budget_exhaustion_and_caller_ceiling_are_denied():
    exhausted = _check(evaluation=_evaluation(tool_calls_used=LIVE_OPS_MAX_TOOL_CALLS))
    turns = _check(evaluation=_evaluation(model_turns_used=LIVE_OPS_MAX_MODEL_TURNS))
    executions = _check(evaluation=_evaluation(executions_used=1))
    remaining = _check(evaluation=_evaluation(tool_calls_used=LIVE_OPS_MAX_TOOL_CALLS - 1))
    caller_ceiling = _check(_proposal(caller_max_tool_calls=LIVE_OPS_MAX_TOOL_CALLS))
    assert exhausted.status is MasterReadCheckStatus.DENIED
    assert Reason.BUDGET_EXHAUSTED in exhausted.denial_reasons
    assert Reason.BUDGET_EXHAUSTED in turns.denial_reasons
    assert Reason.BUDGET_EXHAUSTED in executions.denial_reasons
    assert remaining.status is MasterReadCheckStatus.WITHIN_ALLOWLIST
    assert Reason.CALLER_BUDGET_NOT_AUTHORITY in caller_ceiling.denial_reasons


def test_cancellation_is_denied():
    result = _check(evaluation=_evaluation(cancellation=ServerCancellation(True)))
    assert result.status is MasterReadCheckStatus.DENIED
    assert result.denial_reasons == (Reason.CANCELLED,)
    with pytest.raises(ContractValidationError):
        ServerCancellation(cancelled="false")  # type: ignore[arg-type]


def test_matching_ids_without_authorization_are_denied():
    result = _check(
        _proposal(
            master_request_id="same-id",
            invocation_id="same-id",
            principal_label="same-id",
        ),
        principal=None,
    )
    assert result.status is MasterReadCheckStatus.DENIED
    assert Reason.PRINCIPAL_NOT_TRUSTED in result.denial_reasons
    assert Reason.MATCHING_IDENTIFIER_NOT_AUTHORIZATION in result.denial_reasons
    assert Reason.PRINCIPAL_LABEL_NOT_AUTHENTICATION in result.denial_reasons
    assert result.dispatch_permitted is False


def test_structurally_valid_input_without_trusted_principal_is_denied():
    result = _check(principal=None)
    assert result.status is MasterReadCheckStatus.DENIED
    assert result.denial_reasons == (Reason.PRINCIPAL_NOT_TRUSTED,)
    with pytest.raises(ContractValidationError):
        TrustedReadPrincipal(principal_id="ingress-principal-1", _seal=object())
    for forged in ("ingress-principal-1", True, {"trusted": True}):
        denied = _check(principal=forged)
        assert denied.status is MasterReadCheckStatus.DENIED
        assert Reason.PRINCIPAL_NOT_TRUSTED in denied.denial_reasons


def test_caller_policy_fields_cannot_authorize_a_matching_read():
    allowlist = _check(_proposal(caller_allowlist={"aircraft": "*"}))
    decision = _check(_proposal(caller_policy_decision="ALLOW", caller_authorized=True))
    deadline = _check(_proposal(caller_deadline_utc=DEADLINE))
    assert allowlist.status is MasterReadCheckStatus.DENIED
    assert Reason.CALLER_ALLOWLIST_NOT_AUTHORITY in allowlist.denial_reasons
    assert Reason.CALLER_POLICY_NOT_AUTHORITY in decision.denial_reasons
    assert Reason.CALLER_DEADLINE_NOT_AUTHORITY in deadline.denial_reasons
    with pytest.raises(TypeError):
        check_master_read_resource(  # type: ignore[call-arg]
            _proposal(),
            evaluation=_evaluation(),
            principal=seal_ingress_principal("ingress-principal-1"),
            allowlist={"get_aircraft_operational_context": "*"},
        )


def test_policy_check_cannot_become_a_dispatch_permit():
    result = _check()
    assert result.status is MasterReadCheckStatus.WITHIN_ALLOWLIST
    with pytest.raises(ContractValidationError, match="policy_check_is_not_dispatch_permit"):
        promote_read_check_to_dispatch_permit(result)
    with pytest.raises(ContractValidationError, match="policy_check_is_not_dispatch_permit"):
        bool(result)
    with pytest.raises(ContractValidationError, match="policy_check_is_not_dispatch_permit"):
        replace(result, dispatch_permitted=True)
    with pytest.raises(ContractValidationError, match="policy_check_is_not_dispatch_permit"):
        replace(result, execution_permitted=True)
    with pytest.raises(ContractValidationError, match="policy_check_is_not_dispatch_permit"):
        replace(result, production_permit_issued=True)


def test_concurrent_checks_do_not_share_denials():
    proposal = _proposal()
    principal = seal_ingress_principal("ingress-principal-1")

    def _run() -> MasterReadCheckStatus:
        return _check(proposal, principal=principal).status

    with ThreadPoolExecutor(max_workers=8) as pool:
        statuses = tuple(pool.map(lambda _index: _run(), range(8)))
    assert statuses == (MasterReadCheckStatus.WITHIN_ALLOWLIST,) * 8
