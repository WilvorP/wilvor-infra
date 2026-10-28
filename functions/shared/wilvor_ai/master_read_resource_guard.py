"""Deterministic read-resource policy check for a future trusted dispatcher.

The server allowlist in this module is the only policy source. A caller
allowlist, region label, boolean, principal string, or policy word is not
authority. A tool name that appears in a specialist catalog is not a resource
grant. DynamoDB Scan is not an allowlisted operation for any resource.

A within-allowlist result is a policy check only. The production permit
issuer is not integrated, and this module exposes no dispatch grant.

PROPOSED, not imported and not implemented here:

- Application ingress may later seal ``TrustedReadPrincipal``. This module
  does not authenticate a gateway claim, a matching identifier, or a boolean.
- A production permit issuer may later consider a within-allowlist check.
  ``promote_read_check_to_dispatch_permit`` refuses that promotion.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum

from wilvor_ai.contracts import (
    ALLOWED_V1_CAPABILITIES,
    PROHIBITED_V1_CAPABILITIES,
    PROVENANCE_TOKEN_MAX_LENGTH,
    AgentCapability,
    ContractValidationError,
    _validate_bounded_text,
    _validate_utc,
)
from wilvor_ai.live_ops_public_contracts import (
    LIVE_OPS_HAZARD_IDS_MAX,
    LIVE_OPS_MAX_MODEL_TURNS,
    LIVE_OPS_MAX_TOOL_CALLS,
    LIVE_OPS_SELECTOR_STRING_MAX_LENGTH,
)
from wilvor_ai.master_contracts import MasterPurpose, MasterSpecialist
from wilvor_ai.master_routing import MAX_HISTORICAL_WINDOW


MASTER_READ_RESOURCE_GUARD_SCHEMA_VERSION = "wilvor.ai.master_read_resource_guard.v1"

# The production permit issuer is a later integration. These stay false.
PRODUCTION_PERMIT_ISSUER_INTEGRATED = False
DISPATCH_GRANT_AVAILABLE = False

# Restates historical_specialist.AI_LIST_MAX. Tighter than the storage
# LIST_MAX_LIMIT. A caller cannot raise it.
HISTORICAL_LIST_MAX = 25
HISTORICAL_LIST_MIN = 1

# One specialist invocation per check. Turns and tool calls restate the
# public Live Ops ceilings and the Historical and Decision loop ceilings.
_MAX_EXECUTIONS = 1
_HISTORICAL_MAX_MODEL_TURNS = 3
_HISTORICAL_MAX_TOOL_CALLS = 2
_DECISION_MAX_MODEL_TURNS = 4
_DECISION_MAX_TOOL_CALLS = 4

_LIVE = AgentCapability.RETRIEVE_DETERMINISTIC_OPERATIONAL_CONTEXT
_HISTORY = AgentCapability.RETRIEVE_HISTORICAL_ANALYTICS
_RISK = AgentCapability.EXPLAIN_EXISTING_RISK
_RECOMMENDATIONS = AgentCapability.EXPLAIN_EXISTING_RECOMMENDATIONS

# Restates master_execution_policy_boundary.SPECIALIST_CAPABILITY_PAIRS.
_SPECIALIST_CAPABILITIES = {
    (MasterSpecialist.LIVE_OPS, MasterPurpose.DISCOVERY): frozenset({_LIVE}),
    (MasterSpecialist.LIVE_OPS, MasterPurpose.ANSWER): frozenset({_LIVE}),
    (MasterSpecialist.HISTORICAL_ANALYTICS, MasterPurpose.ANSWER): frozenset({_HISTORY}),
    (MasterSpecialist.DECISION, MasterPurpose.ANSWER): frozenset({_RISK, _RECOMMENDATIONS}),
}

_US_STATE_CODES = frozenset(
    {
        "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA",
        "HI", "ID", "IL", "IN", "IA", "KS", "KY", "LA", "ME", "MD",
        "MA", "MI", "MN", "MS", "MO", "MT", "NE", "NV", "NH", "NJ",
        "NM", "NY", "NC", "ND", "OH", "OK", "OR", "PA", "RI", "SC",
        "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV", "WI", "WY",
    }
)

_AIRCRAFT = "aircraft"
_HAZARDS = "hazards"
_HAZARD_COORDINATES = "hazard_coordinates"
_ENCOUNTERS = "encounters"
_AIRPORTS = "airports"
_METAR = "metar"
_TAF = "taf"
_RECOMMENDATION_ROWS = "recommendations"
_AIRPORT_ASSESSMENTS = "airport_assessments"
_HIST_ENCOUNTERS = "historical.encounters"
_HIST_RISKS = "historical.risks"
_HIST_HAZARDS = "historical.hazard_versions"

_WILDCARDS = frozenset(
    {"*", "all", "all_tables", "any", "unrestricted", "table", "tables"}
)
_ALLOW_DECISIONS = frozenset(
    {
        "ALLOW",
        "ALLOWED",
        "PERMIT",
        "PERMITTED",
        "AUTHORIZED",
        "AUTHORIZE",
        "GRANTED",
        "GRANT",
        "APPROVED",
        "APPROVE",
        "TRUE",
        "YES",
        "ENABLED",
    }
)
_PROHIBITED_TOOLS = frozenset(
    {
        "scan_all",
        "scan_page",
        "scan_aircraft_page",
        "scan_airports_page",
        "scan_aircraft_candidates",
        "scan_projection_index_candidates",
        "scan_hazard_index_candidates",
        "scan_encounter_candidates",
        "scan_risk_candidates",
        "scan_recommendation_candidates",
        "scan_alert_candidates",
        "put_item",
        "delete_item",
        "update_item",
        "batch_write_item",
        "mutate_operational_state",
        "execute_reroute",
        "issue_atc_instructions",
    }
)


class _ServerSeal:
    """Process-local seal. Caller strings and booleans are not this object."""


_SEAL = _ServerSeal()


class ReadOperation(str, Enum):
    """Keyed reads a later dispatcher might request. Scan is never granted."""

    GET_ITEM = "GET_ITEM"
    QUERY = "QUERY"
    SCAN = "SCAN"


class MasterReadCheckStatus(str, Enum):
    """Policy-check status. Permitted and granted are not members."""

    WITHIN_ALLOWLIST = "WITHIN_ALLOWLIST"
    DENIED = "DENIED"


class MasterReadDenialReason(str, Enum):
    """Why a proposed read is outside the server allowlist."""

    PRINCIPAL_NOT_TRUSTED = "PRINCIPAL_NOT_TRUSTED"
    PRINCIPAL_LABEL_NOT_AUTHENTICATION = "PRINCIPAL_LABEL_NOT_AUTHENTICATION"
    CALLER_POLICY_NOT_AUTHORITY = "CALLER_POLICY_NOT_AUTHORITY"
    CALLER_ALLOWLIST_NOT_AUTHORITY = "CALLER_ALLOWLIST_NOT_AUTHORITY"
    PROHIBITED_CAPABILITY = "PROHIBITED_CAPABILITY"
    UNSUPPORTED_CAPABILITY = "UNSUPPORTED_CAPABILITY"
    CAPABILITY_SPECIALIST_MISMATCH = "CAPABILITY_SPECIALIST_MISMATCH"
    UNSUPPORTED_TOOL = "UNSUPPORTED_TOOL"
    PROHIBITED_TOOL = "PROHIBITED_TOOL"
    RECOGNIZED_TOOL_NAME_NOT_A_GRANT = "RECOGNIZED_TOOL_NAME_NOT_A_GRANT"
    CROSS_SPECIALIST_RESOURCE = "CROSS_SPECIALIST_RESOURCE"
    RESOURCE_NOT_ALLOWLISTED = "RESOURCE_NOT_ALLOWLISTED"
    ARBITRARY_TABLE_ARN = "ARBITRARY_TABLE_ARN"
    WILDCARD_RESOURCE = "WILDCARD_RESOURCE"
    SCAN_NOT_PERMITTED = "SCAN_NOT_PERMITTED"
    OPERATION_NOT_ALLOWLISTED = "OPERATION_NOT_ALLOWLISTED"
    ARGUMENT_NOT_ALLOWLISTED = "ARGUMENT_NOT_ALLOWLISTED"
    ARGUMENT_MISSING = "ARGUMENT_MISSING"
    ARGUMENT_INVALID = "ARGUMENT_INVALID"
    REGION_SUBSTITUTION = "REGION_SUBSTITUTION"
    REGION_LABEL_NOT_AUTHORITY = "REGION_LABEL_NOT_AUTHORITY"
    GEOGRAPHIC_SCOPE_NOT_ESTABLISHED = "GEOGRAPHIC_SCOPE_NOT_ESTABLISHED"
    POPULATION_SCOPE_NOT_ESTABLISHED = "POPULATION_SCOPE_NOT_ESTABLISHED"
    HISTORICAL_INTERVAL_ESCALATION = "HISTORICAL_INTERVAL_ESCALATION"
    HISTORICAL_INTERVAL_INVALID = "HISTORICAL_INTERVAL_INVALID"
    DEADLINE_EXPIRED = "DEADLINE_EXPIRED"
    CALLER_DEADLINE_NOT_AUTHORITY = "CALLER_DEADLINE_NOT_AUTHORITY"
    BUDGET_EXHAUSTED = "BUDGET_EXHAUSTED"
    CALLER_BUDGET_NOT_AUTHORITY = "CALLER_BUDGET_NOT_AUTHORITY"
    CANCELLED = "CANCELLED"
    MATCHING_IDENTIFIER_NOT_AUTHORIZATION = "MATCHING_IDENTIFIER_NOT_AUTHORIZATION"


class _SelectorMode(str, Enum):
    NONE = "NONE"
    REGION_OR_HAZARD_IDS = "REGION_OR_HAZARD_IDS"
    ENCOUNTER = "ENCOUNTER"
    AIRCRAFT_OR_HAZARD = "AIRCRAFT_OR_HAZARD"


@dataclass(frozen=True)
class _Budget:
    max_executions: int
    max_model_turns: int
    max_tool_calls: int


_BUDGETS = {
    MasterSpecialist.LIVE_OPS: _Budget(
        _MAX_EXECUTIONS,
        LIVE_OPS_MAX_MODEL_TURNS,
        LIVE_OPS_MAX_TOOL_CALLS,
    ),
    MasterSpecialist.HISTORICAL_ANALYTICS: _Budget(
        _MAX_EXECUTIONS,
        _HISTORICAL_MAX_MODEL_TURNS,
        _HISTORICAL_MAX_TOOL_CALLS,
    ),
    MasterSpecialist.DECISION: _Budget(
        _MAX_EXECUTIONS,
        _DECISION_MAX_MODEL_TURNS,
        _DECISION_MAX_TOOL_CALLS,
    ),
}


@dataclass(frozen=True)
class _ToolRule:
    capabilities: frozenset[AgentCapability]
    arguments: frozenset[str]
    required: frozenset[str]
    resources: frozenset[tuple[str, ReadOperation]]
    selector: _SelectorMode = _SelectorMode.NONE
    historical_interval: bool = False
    list_limit: bool = False
    unbounded_population: bool = False


def _resources(*pairs: tuple[str, ReadOperation]) -> frozenset[tuple[str, ReadOperation]]:
    return frozenset(pairs)


def _rule(
    capabilities: frozenset[AgentCapability],
    arguments: frozenset[str],
    required: frozenset[str],
    resources: frozenset[tuple[str, ReadOperation]],
    *,
    selector: _SelectorMode = _SelectorMode.NONE,
    historical_interval: bool = False,
    list_limit: bool = False,
    unbounded_population: bool = False,
) -> _ToolRule:
    return _ToolRule(
        capabilities=capabilities,
        arguments=arguments,
        required=required,
        resources=resources,
        selector=selector,
        historical_interval=historical_interval,
        list_limit=list_limit,
        unbounded_population=unbounded_population,
    )


_GET = ReadOperation.GET_ITEM
_QUERY = ReadOperation.QUERY
_HAZARD_ARGS = frozenset({"region", "product_type", "hazard_type", "hazard_ids"})
_WINDOW = frozenset({"start_utc", "end_utc"})

# Logical OperationalTables attributes and historical datasets. No ARNs.
# Scan readers in wilvor_operational.readers are intentionally absent.
_RULES: dict[MasterSpecialist, dict[str, _ToolRule]] = {
    MasterSpecialist.LIVE_OPS: {
        "search_current_hazards": _rule(
            frozenset({_LIVE}),
            _HAZARD_ARGS,
            frozenset(),
            _resources(
                (_HAZARDS, _QUERY),
                (_HAZARDS, _GET),
                (_HAZARD_COORDINATES, _QUERY),
            ),
            selector=_SelectorMode.REGION_OR_HAZARD_IDS,
        ),
        "search_current_impacts": _rule(
            frozenset({_LIVE}),
            _HAZARD_ARGS,
            frozenset(),
            _resources(
                (_HAZARDS, _QUERY),
                (_HAZARDS, _GET),
                (_ENCOUNTERS, _QUERY),
                (_AIRCRAFT, _GET),
            ),
            selector=_SelectorMode.REGION_OR_HAZARD_IDS,
        ),
        "search_current_encounters": _rule(
            frozenset({_LIVE}),
            frozenset({"aircraft_id", "callsign", "hazard_id", "hazard_ids"}),
            frozenset(),
            _resources(
                (_ENCOUNTERS, _QUERY),
                (_ENCOUNTERS, _GET),
                (_AIRCRAFT, _QUERY),
                (_AIRCRAFT, _GET),
            ),
            selector=_SelectorMode.ENCOUNTER,
        ),
        "get_observed_network_state": _rule(
            frozenset({_LIVE}),
            frozenset(),
            frozenset(),
            _resources(),
            unbounded_population=True,
        ),
        "get_aircraft_operational_context": _rule(
            frozenset({_LIVE}),
            frozenset({"aircraft_id"}),
            frozenset({"aircraft_id"}),
            _resources((_AIRCRAFT, _GET)),
        ),
        "get_hazard_operational_context": _rule(
            frozenset({_LIVE}),
            frozenset({"hazard_id"}),
            frozenset({"hazard_id"}),
            _resources((_HAZARDS, _GET)),
        ),
        "get_airport_operational_context": _rule(
            frozenset({_LIVE}),
            frozenset({"airport_id"}),
            frozenset({"airport_id"}),
            _resources((_AIRPORTS, _GET), (_METAR, _GET), (_TAF, _GET)),
        ),
        "find_aircraft_by_callsign": _rule(
            frozenset({_LIVE}),
            frozenset({"callsign"}),
            frozenset({"callsign"}),
            _resources((_AIRCRAFT, _QUERY)),
        ),
    },
    MasterSpecialist.HISTORICAL_ANALYTICS: {
        "summarize_historical_encounters": _rule(
            frozenset({_HISTORY}),
            _WINDOW | frozenset({"aircraft_id", "hazard_id", "hazard_type"}),
            _WINDOW,
            _resources((_HIST_ENCOUNTERS, _QUERY)),
            historical_interval=True,
        ),
        "summarize_historical_risks": _rule(
            frozenset({_HISTORY}),
            _WINDOW | frozenset({"aircraft_id", "hazard_id", "encounter_id", "risk_level"}),
            _WINDOW,
            _resources((_HIST_RISKS, _QUERY)),
            historical_interval=True,
        ),
        "summarize_historical_hazard_versions": _rule(
            frozenset({_HISTORY}),
            _WINDOW | frozenset({"hazard_id", "hazard_type", "product_type"}),
            _WINDOW,
            _resources((_HIST_HAZARDS, _QUERY)),
            historical_interval=True,
        ),
        "list_historical_encounters": _rule(
            frozenset({_HISTORY}),
            _WINDOW | frozenset({"aircraft_id", "hazard_id", "limit"}),
            _WINDOW,
            _resources((_HIST_ENCOUNTERS, _QUERY)),
            historical_interval=True,
            list_limit=True,
            selector=_SelectorMode.AIRCRAFT_OR_HAZARD,
        ),
    },
    MasterSpecialist.DECISION: {
        "get_current_decision_context": _rule(
            frozenset({_RISK, _RECOMMENDATIONS}),
            frozenset({"aircraft_id"}),
            frozenset({"aircraft_id"}),
            _resources((_AIRCRAFT, _GET)),
        ),
        "get_current_risk_evidence": _rule(
            frozenset({_RISK}),
            frozenset({"aircraft_id"}),
            frozenset({"aircraft_id"}),
            _resources((_AIRCRAFT, _GET)),
        ),
        "get_current_recommendation": _rule(
            frozenset({_RECOMMENDATIONS}),
            frozenset({"aircraft_id"}),
            frozenset({"aircraft_id"}),
            _resources((_AIRCRAFT, _GET)),
        ),
        "get_persisted_airport_candidate_evidence": _rule(
            frozenset({_RECOMMENDATIONS}),
            frozenset({"recommendation_id"}),
            frozenset({"recommendation_id"}),
            _resources(
                (_RECOMMENDATION_ROWS, _GET),
                (_AIRPORT_ASSESSMENTS, _QUERY),
            ),
        ),
    },
}


def _assert_server_allowlist() -> None:
    if len(_US_STATE_CODES) != 50:
        raise ContractValidationError("invalid_server_allowlist")
    if PRODUCTION_PERMIT_ISSUER_INTEGRATED is not False or DISPATCH_GRANT_AVAILABLE is not False:
        raise ContractValidationError("production_permit_issuer_not_integrated")
    if _MAX_EXECUTIONS != 1:
        raise ContractValidationError("invalid_server_allowlist")
    if MAX_HISTORICAL_WINDOW != timedelta(days=7):
        raise ContractValidationError("invalid_historical_ceiling")
    names: list[str] = []
    for specialist, tools in _RULES.items():
        if specialist not in _BUDGETS:
            raise ContractValidationError("invalid_server_allowlist")
        for name, rule in tools.items():
            names.append(name)
            if not rule.capabilities or not rule.capabilities <= ALLOWED_V1_CAPABILITIES:
                raise ContractValidationError("invalid_server_allowlist")
            if rule.required - rule.arguments:
                raise ContractValidationError("invalid_server_allowlist")
            if any(operation is ReadOperation.SCAN for _resource, operation in rule.resources):
                raise ContractValidationError("scan_not_permitted")
            if rule.unbounded_population and rule.resources:
                raise ContractValidationError("invalid_server_allowlist")
    if len(names) != len(set(names)):
        raise ContractValidationError("invalid_server_allowlist")
    if _PROHIBITED_TOOLS & set(names):
        raise ContractValidationError("invalid_server_allowlist")
    if "ALLOW" not in _ALLOW_DECISIONS or "PERMIT" not in _ALLOW_DECISIONS:
        raise ContractValidationError("invalid_server_allowlist")


_assert_server_allowlist()

_RESOURCES_BY_SPECIALIST = {
    specialist: frozenset(resource for rule in tools.values() for resource, _operation in rule.resources)
    for specialist, tools in _RULES.items()
}
_REASON_ORDER = tuple(MasterReadDenialReason)


@dataclass(frozen=True)
class TrustedReadPrincipal:
    """Ingress principal sealed for a policy check.

    Constructing this from a string, a boolean, or a matching identifier
    fails. Sealing is not ingress authentication and not a dispatch permit.
    """

    principal_id: str
    _seal: object = field(repr=False, compare=False)

    def __post_init__(self) -> None:
        if self._seal is not _SEAL:
            raise ContractValidationError("principal_not_trusted")
        _token(self.principal_id, "principal_id")


@dataclass(frozen=True)
class ServerPopulationScope:
    """Server geographic codes. A region label string is not this object."""

    region_codes: frozenset[str]
    _seal: object = field(repr=False, compare=False)

    def __post_init__(self) -> None:
        if self._seal is not _SEAL:
            raise ContractValidationError("geographic_scope_not_established")
        if (
            not isinstance(self.region_codes, frozenset)
            or not self.region_codes
            or any(code not in _US_STATE_CODES for code in self.region_codes)
        ):
            raise ContractValidationError("invalid_population_scope")


@dataclass(frozen=True)
class ServerCancellation:
    """Server cancellation token. A caller boolean is not this object."""

    cancelled: bool

    def __post_init__(self) -> None:
        if type(self.cancelled) is not bool:
            raise ContractValidationError("invalid_cancellation")


@dataclass(frozen=True)
class ServerReadEvaluation:
    """Server clock, deadline, and consumed budget. Ceilings are not fields."""

    observed_utc: str
    deadline_utc: str
    executions_used: int
    model_turns_used: int
    tool_calls_used: int
    cancellation: ServerCancellation

    def __post_init__(self) -> None:
        _whole_second(self.observed_utc, "observed_utc")
        _whole_second(self.deadline_utc, "deadline_utc")
        _count(self.executions_used, "executions_used")
        _count(self.model_turns_used, "model_turns_used")
        _count(self.tool_calls_used, "tool_calls_used")
        if type(self.cancellation) is not ServerCancellation:
            raise ContractValidationError("invalid_cancellation")


@dataclass(frozen=True)
class MasterReadResourceProposal:
    """Untrusted read proposal. Embedded policy fields are never authority."""

    specialist: MasterSpecialist
    purpose: MasterPurpose
    capability: AgentCapability
    tool_name: str
    resource_id: str
    operation: ReadOperation
    arguments: tuple[tuple[str, object], ...]
    master_request_id: str
    invocation_id: str
    principal_label: str | None = None
    claimed_region: str | None = None
    caller_allowlist: object = None
    caller_policy_decision: str | None = None
    caller_authorized: bool | None = None
    caller_deadline_utc: str | None = None
    caller_max_tool_calls: int | None = None
    historical_start_utc: str | None = None
    historical_end_utc: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.specialist, MasterSpecialist):
            raise ContractValidationError("invalid_specialist")
        if not isinstance(self.purpose, MasterPurpose):
            raise ContractValidationError("invalid_purpose")
        if not isinstance(self.capability, AgentCapability):
            raise ContractValidationError("invalid_capability")
        if not isinstance(self.operation, ReadOperation):
            raise ContractValidationError("invalid_operation")
        _tool_name(self.tool_name)
        _resource_text(self.resource_id)
        _token(self.master_request_id, "master_request_id")
        _token(self.invocation_id, "invocation_id")
        _optional_token(self.principal_label, "principal_label")
        _optional_text(self.claimed_region, "claimed_region")
        _optional_text(self.caller_policy_decision, "caller_policy_decision")
        _optional_text(self.caller_deadline_utc, "caller_deadline_utc")
        _optional_text(self.historical_start_utc, "historical_start_utc")
        _optional_text(self.historical_end_utc, "historical_end_utc")
        if self.caller_authorized is not None and type(self.caller_authorized) is not bool:
            raise ContractValidationError("invalid_caller_authorized")
        if self.caller_max_tool_calls is not None:
            _count(self.caller_max_tool_calls, "caller_max_tool_calls")
        _argument_pairs(self.arguments)


@dataclass(frozen=True)
class MasterReadResourceCheck:
    """Policy-check result. The closed flags cannot be promoted to true."""

    status: MasterReadCheckStatus
    denial_reasons: tuple[MasterReadDenialReason, ...]
    specialist: MasterSpecialist
    capability: AgentCapability
    tool_name: str
    resource_id: str
    operation: ReadOperation
    dispatch_permitted: bool = False
    execution_permitted: bool = False
    production_permit_issued: bool = False

    def __post_init__(self) -> None:
        if PRODUCTION_PERMIT_ISSUER_INTEGRATED is not False or DISPATCH_GRANT_AVAILABLE is not False:
            raise ContractValidationError("production_permit_issuer_not_integrated")
        if self.status not in {MasterReadCheckStatus.WITHIN_ALLOWLIST, MasterReadCheckStatus.DENIED}:
            raise ContractValidationError("execution_not_granted")
        if (
            self.dispatch_permitted is not False
            or self.execution_permitted is not False
            or self.production_permit_issued is not False
        ):
            raise ContractValidationError("policy_check_is_not_dispatch_permit")
        if not isinstance(self.denial_reasons, tuple) or any(
            not isinstance(reason, MasterReadDenialReason) for reason in self.denial_reasons
        ):
            raise ContractValidationError("invalid_denial_reasons")
        if self.status is MasterReadCheckStatus.WITHIN_ALLOWLIST and self.denial_reasons:
            raise ContractValidationError("execution_not_granted")
        if self.status is MasterReadCheckStatus.DENIED and not self.denial_reasons:
            raise ContractValidationError("invalid_denial_reasons")

    def __bool__(self) -> bool:
        raise ContractValidationError("policy_check_is_not_dispatch_permit")


def seal_ingress_principal(principal_id: str) -> TrustedReadPrincipal:
    """Seal a principal id for the checker.

    This does not prove application ingress and does not issue a permit.
    """

    return TrustedReadPrincipal(principal_id=principal_id, _seal=_SEAL)


def seal_population_scope(region_codes: frozenset[str]) -> ServerPopulationScope:
    """Seal server region codes. Labels, wildcards, and caller sets are rejected."""

    return ServerPopulationScope(region_codes=region_codes, _seal=_SEAL)


def server_read_tools(specialist: MasterSpecialist) -> frozenset[str]:
    """Tool names on the server allowlist for one specialist."""

    if not isinstance(specialist, MasterSpecialist):
        raise ContractValidationError("invalid_specialist")
    return frozenset(_RULES[specialist])


def server_read_argument_names(specialist: MasterSpecialist, tool_name: str) -> frozenset[str]:
    """Argument names the server allowlist accepts for one tool."""

    return _require_rule(specialist, tool_name).arguments


def server_read_resources(
    specialist: MasterSpecialist,
    tool_name: str,
) -> frozenset[tuple[str, ReadOperation]]:
    """Keyed resource operations the server allowlist accepts for one tool."""

    return _require_rule(specialist, tool_name).resources


def server_read_budget(specialist: MasterSpecialist) -> tuple[int, int, int]:
    """Server ceilings ``(executions, model turns, tool calls)``."""

    if not isinstance(specialist, MasterSpecialist):
        raise ContractValidationError("invalid_specialist")
    budget = _BUDGETS[specialist]
    return (budget.max_executions, budget.max_model_turns, budget.max_tool_calls)


def check_master_read_resource(
    proposal: MasterReadResourceProposal,
    *,
    evaluation: ServerReadEvaluation,
    principal: object = None,
    population_scope: object = None,
) -> MasterReadResourceCheck:
    """Check one proposed read against the server allowlist.

    ``allowlist`` is not a parameter. Caller policy fields on ``proposal``
    are recorded as denials. The returned check is not a dispatch permit.
    """

    if type(proposal) is not MasterReadResourceProposal:
        raise ContractValidationError("invalid_read_proposal")
    if type(evaluation) is not ServerReadEvaluation:
        raise ContractValidationError("invalid_read_evaluation")
    reasons: list[MasterReadDenialReason] = []
    trusted = type(principal) is TrustedReadPrincipal
    if not trusted:
        reasons.append(MasterReadDenialReason.PRINCIPAL_NOT_TRUSTED)
        if _identifiers_match(proposal):
            reasons.append(MasterReadDenialReason.MATCHING_IDENTIFIER_NOT_AUTHORIZATION)
    if proposal.principal_label is not None:
        reasons.append(MasterReadDenialReason.PRINCIPAL_LABEL_NOT_AUTHENTICATION)
    reasons.extend(_caller_policy_reasons(proposal))
    reasons.extend(_capability_reasons(proposal))
    rule = _RULES.get(proposal.specialist, {}).get(proposal.tool_name)
    recognized = rule is not None
    if proposal.tool_name in _PROHIBITED_TOOLS:
        reasons.append(MasterReadDenialReason.PROHIBITED_TOOL)
    elif rule is None:
        reasons.append(MasterReadDenialReason.UNSUPPORTED_TOOL)
        if _tool_belongs_to_other(proposal.specialist, proposal.tool_name):
            reasons.append(MasterReadDenialReason.CROSS_SPECIALIST_RESOURCE)
    elif proposal.capability not in rule.capabilities:
        reasons.append(MasterReadDenialReason.CAPABILITY_SPECIALIST_MISMATCH)
    if rule is None:
        reasons.extend(_unscoped_resource_reasons(proposal))
    else:
        arguments = dict(proposal.arguments)
        reasons.extend(_argument_reasons(rule, arguments))
        reasons.extend(_resource_reasons(proposal, rule, recognized))
        reasons.extend(_region_reasons(proposal, rule, arguments, population_scope))
        reasons.extend(_interval_reasons(proposal, rule, arguments))
        if rule.unbounded_population:
            reasons.append(MasterReadDenialReason.POPULATION_SCOPE_NOT_ESTABLISHED)
    reasons.extend(_deadline_reasons(proposal, evaluation))
    reasons.extend(_budget_reasons(proposal, evaluation))
    if evaluation.cancellation.cancelled:
        reasons.append(MasterReadDenialReason.CANCELLED)
    return _check(proposal, _unique(reasons))


def promote_read_check_to_dispatch_permit(check: MasterReadResourceCheck) -> None:
    """Refuse to turn a policy check into a production dispatch permit."""

    if type(check) is not MasterReadResourceCheck:
        raise ContractValidationError("invalid_read_check")
    if PRODUCTION_PERMIT_ISSUER_INTEGRATED is not False or DISPATCH_GRANT_AVAILABLE is not False:
        raise ContractValidationError("production_permit_issuer_not_integrated")
    raise ContractValidationError("policy_check_is_not_dispatch_permit")


def _require_rule(specialist: MasterSpecialist, tool_name: str) -> _ToolRule:
    if not isinstance(specialist, MasterSpecialist):
        raise ContractValidationError("invalid_specialist")
    rule = _RULES.get(specialist, {}).get(tool_name)
    if rule is None:
        raise ContractValidationError("unknown_read_tool")
    return rule


def _check(
    proposal: MasterReadResourceProposal,
    reasons: tuple[MasterReadDenialReason, ...],
) -> MasterReadResourceCheck:
    status = (
        MasterReadCheckStatus.DENIED
        if reasons
        else MasterReadCheckStatus.WITHIN_ALLOWLIST
    )
    return MasterReadResourceCheck(
        status=status,
        denial_reasons=reasons,
        specialist=proposal.specialist,
        capability=proposal.capability,
        tool_name=proposal.tool_name,
        resource_id=proposal.resource_id,
        operation=proposal.operation,
    )


def _unique(
    reasons: list[MasterReadDenialReason],
) -> tuple[MasterReadDenialReason, ...]:
    present = set(reasons)
    return tuple(reason for reason in _REASON_ORDER if reason in present)


def _capability_reasons(
    proposal: MasterReadResourceProposal,
) -> tuple[MasterReadDenialReason, ...]:
    reasons: list[MasterReadDenialReason] = []
    capability = proposal.capability
    allowed = _SPECIALIST_CAPABILITIES.get((proposal.specialist, proposal.purpose))
    if capability in PROHIBITED_V1_CAPABILITIES:
        reasons.append(MasterReadDenialReason.PROHIBITED_CAPABILITY)
    elif capability not in ALLOWED_V1_CAPABILITIES or allowed is None or capability not in allowed:
        if capability in ALLOWED_V1_CAPABILITIES and _pair_member(capability):
            reasons.append(MasterReadDenialReason.CAPABILITY_SPECIALIST_MISMATCH)
        else:
            reasons.append(MasterReadDenialReason.UNSUPPORTED_CAPABILITY)
    return tuple(reasons)


def _pair_member(capability: AgentCapability) -> bool:
    return any(capability in allowed for allowed in _SPECIALIST_CAPABILITIES.values())


def _caller_policy_reasons(
    proposal: MasterReadResourceProposal,
) -> tuple[MasterReadDenialReason, ...]:
    reasons: list[MasterReadDenialReason] = []
    if proposal.caller_allowlist is not None:
        reasons.append(MasterReadDenialReason.CALLER_ALLOWLIST_NOT_AUTHORITY)
    decision = proposal.caller_policy_decision
    if proposal.caller_authorized is not None or decision is not None:
        reasons.append(MasterReadDenialReason.CALLER_POLICY_NOT_AUTHORITY)
    return tuple(reasons)


def _owned_by_other(specialist: MasterSpecialist, resource_id: str) -> bool:
    owners = [
        owner
        for owner, resources in _RESOURCES_BY_SPECIALIST.items()
        if resource_id in resources
    ]
    return bool(owners) and specialist not in owners


def _tool_belongs_to_other(specialist: MasterSpecialist, tool_name: str) -> bool:
    return any(
        owner is not specialist and tool_name in tools
        for owner, tools in _RULES.items()
    )


def _wildcard(resource_id: str) -> bool:
    return "*" in resource_id or resource_id.casefold() in _WILDCARDS


def _arn(resource_id: str) -> bool:
    folded = resource_id.casefold()
    return folded.startswith("arn:") or folded.startswith("dynamodb:") or ":table/" in folded


def _unscoped_resource_reasons(
    proposal: MasterReadResourceProposal,
) -> tuple[MasterReadDenialReason, ...]:
    reasons: list[MasterReadDenialReason] = []
    if _wildcard(proposal.resource_id):
        reasons.append(MasterReadDenialReason.WILDCARD_RESOURCE)
    if _arn(proposal.resource_id):
        reasons.append(MasterReadDenialReason.ARBITRARY_TABLE_ARN)
    if proposal.operation is ReadOperation.SCAN:
        reasons.append(MasterReadDenialReason.SCAN_NOT_PERMITTED)
    if _owned_by_other(proposal.specialist, proposal.resource_id):
        reasons.append(MasterReadDenialReason.CROSS_SPECIALIST_RESOURCE)
    return tuple(reasons)


def _resource_reasons(
    proposal: MasterReadResourceProposal,
    rule: _ToolRule,
    recognized: bool,
) -> tuple[MasterReadDenialReason, ...]:
    reasons: list[MasterReadDenialReason] = []
    resource_id = proposal.resource_id
    operation = proposal.operation
    if (resource_id, operation) in rule.resources:
        return ()
    if _wildcard(resource_id):
        reasons.append(MasterReadDenialReason.WILDCARD_RESOURCE)
    if _arn(resource_id):
        reasons.append(MasterReadDenialReason.ARBITRARY_TABLE_ARN)
    if operation is ReadOperation.SCAN:
        reasons.append(MasterReadDenialReason.SCAN_NOT_PERMITTED)
        reasons.append(MasterReadDenialReason.OPERATION_NOT_ALLOWLISTED)
    elif any(resource == resource_id for resource, _operation in rule.resources):
        reasons.append(MasterReadDenialReason.OPERATION_NOT_ALLOWLISTED)
    elif _owned_by_other(proposal.specialist, resource_id):
        reasons.append(MasterReadDenialReason.CROSS_SPECIALIST_RESOURCE)
    elif not _wildcard(resource_id) and not _arn(resource_id):
        reasons.append(MasterReadDenialReason.RESOURCE_NOT_ALLOWLISTED)
    if recognized:
        reasons.append(MasterReadDenialReason.RECOGNIZED_TOOL_NAME_NOT_A_GRANT)
    return tuple(reasons)


def _argument_reasons(
    rule: _ToolRule,
    arguments: dict[str, object],
) -> tuple[MasterReadDenialReason, ...]:
    reasons: list[MasterReadDenialReason] = []
    unknown = [name for name in arguments if name not in rule.arguments]
    if unknown:
        reasons.append(MasterReadDenialReason.ARGUMENT_NOT_ALLOWLISTED)
    missing = [name for name in rule.required if name not in arguments]
    if missing or not _selector_present(rule, arguments):
        reasons.append(MasterReadDenialReason.ARGUMENT_MISSING)
    if _arguments_invalid(rule, arguments):
        reasons.append(MasterReadDenialReason.ARGUMENT_INVALID)
    return tuple(reasons)


def _selector_present(rule: _ToolRule, arguments: dict[str, object]) -> bool:
    if rule.selector is _SelectorMode.NONE:
        return True
    if rule.selector is _SelectorMode.REGION_OR_HAZARD_IDS:
        return "region" in arguments or _nonempty_ids(arguments.get("hazard_ids"))
    if rule.selector is _SelectorMode.ENCOUNTER:
        return any(name in arguments for name in ("aircraft_id", "callsign", "hazard_id")) or _nonempty_ids(
            arguments.get("hazard_ids")
        )
    return "aircraft_id" in arguments or "hazard_id" in arguments


def _nonempty_ids(value: object) -> bool:
    return isinstance(value, tuple) and len(value) > 0


def _arguments_invalid(rule: _ToolRule, arguments: dict[str, object]) -> bool:
    for name, value in arguments.items():
        if name not in rule.arguments:
            continue
        if name == "hazard_ids":
            if not _valid_hazard_ids(value):
                return True
            continue
        if name == "limit":
            if not rule.list_limit or not _valid_limit(value):
                return True
            continue
        if name in {"start_utc", "end_utc"}:
            if _parse_whole_second(value) is None:
                return True
            continue
        if name == "region":
            if not isinstance(value, str) or value != value.strip() or not value:
                return True
            continue
        if _selector(value) is None:
            return True
    return False


def _region_reasons(
    proposal: MasterReadResourceProposal,
    rule: _ToolRule,
    arguments: dict[str, object],
    population_scope: object,
) -> tuple[MasterReadDenialReason, ...]:
    reasons: list[MasterReadDenialReason] = []
    sealed = type(population_scope) is ServerPopulationScope
    claimed = proposal.claimed_region
    region = arguments.get("region") if "region" in arguments else None
    if population_scope is not None and not sealed:
        reasons.append(MasterReadDenialReason.REGION_LABEL_NOT_AUTHORITY)
        reasons.append(MasterReadDenialReason.GEOGRAPHIC_SCOPE_NOT_ESTABLISHED)
    if claimed is not None and (not sealed or claimed not in population_scope.region_codes):
        reasons.append(MasterReadDenialReason.REGION_LABEL_NOT_AUTHORITY)
    if "region" in arguments:
        if not sealed:
            if MasterReadDenialReason.GEOGRAPHIC_SCOPE_NOT_ESTABLISHED not in reasons:
                reasons.append(MasterReadDenialReason.GEOGRAPHIC_SCOPE_NOT_ESTABLISHED)
        elif not isinstance(region, str) or region not in population_scope.region_codes:
            if isinstance(region, str) and region in _US_STATE_CODES:
                reasons.append(MasterReadDenialReason.REGION_SUBSTITUTION)
            else:
                reasons.append(MasterReadDenialReason.REGION_LABEL_NOT_AUTHORITY)
        elif claimed is not None and claimed != region:
            reasons.append(MasterReadDenialReason.REGION_SUBSTITUTION)
    elif claimed is not None and sealed and claimed not in population_scope.region_codes:
        reasons.append(MasterReadDenialReason.REGION_SUBSTITUTION)
    if rule.selector is _SelectorMode.REGION_OR_HAZARD_IDS and "region" not in arguments and not sealed:
        if population_scope is not None:
            reasons.append(MasterReadDenialReason.GEOGRAPHIC_SCOPE_NOT_ESTABLISHED)
    return tuple(dict.fromkeys(reasons))


def _interval_reasons(
    proposal: MasterReadResourceProposal,
    rule: _ToolRule,
    arguments: dict[str, object],
) -> tuple[MasterReadDenialReason, ...]:
    reasons: list[MasterReadDenialReason] = []
    proposal_window = proposal.historical_start_utc is not None or proposal.historical_end_utc is not None
    if not rule.historical_interval:
        if proposal_window or "start_utc" in arguments or "end_utc" in arguments:
            reasons.append(MasterReadDenialReason.HISTORICAL_INTERVAL_ESCALATION)
        return tuple(reasons)
    start = _parse_whole_second(arguments.get("start_utc"))
    end = _parse_whole_second(arguments.get("end_utc"))
    if start is None or end is None or start >= end:
        reasons.append(MasterReadDenialReason.HISTORICAL_INTERVAL_INVALID)
    elif end - start > MAX_HISTORICAL_WINDOW:
        reasons.append(MasterReadDenialReason.HISTORICAL_INTERVAL_ESCALATION)
    proposal_start = _parse_whole_second(proposal.historical_start_utc) if proposal.historical_start_utc else None
    proposal_end = _parse_whole_second(proposal.historical_end_utc) if proposal.historical_end_utc else None
    if proposal_window and (
        proposal_start is None
        or proposal_end is None
        or proposal_start != start
        or proposal_end != end
        or (proposal_start is not None and proposal_end is not None and proposal_end - proposal_start > MAX_HISTORICAL_WINDOW)
    ):
        reasons.append(MasterReadDenialReason.HISTORICAL_INTERVAL_ESCALATION)
    return tuple(dict.fromkeys(reasons))


def _deadline_reasons(
    proposal: MasterReadResourceProposal,
    evaluation: ServerReadEvaluation,
) -> tuple[MasterReadDenialReason, ...]:
    reasons: list[MasterReadDenialReason] = []
    observed = _whole_second(evaluation.observed_utc, "observed_utc")
    deadline = _whole_second(evaluation.deadline_utc, "deadline_utc")
    if deadline <= observed:
        reasons.append(MasterReadDenialReason.DEADLINE_EXPIRED)
    if proposal.caller_deadline_utc is not None:
        reasons.append(MasterReadDenialReason.CALLER_DEADLINE_NOT_AUTHORITY)
    return tuple(reasons)


def _budget_reasons(
    proposal: MasterReadResourceProposal,
    evaluation: ServerReadEvaluation,
) -> tuple[MasterReadDenialReason, ...]:
    reasons: list[MasterReadDenialReason] = []
    budget = _BUDGETS[proposal.specialist]
    if (
        evaluation.executions_used >= budget.max_executions
        or evaluation.model_turns_used >= budget.max_model_turns
        or evaluation.tool_calls_used >= budget.max_tool_calls
    ):
        reasons.append(MasterReadDenialReason.BUDGET_EXHAUSTED)
    if proposal.caller_max_tool_calls is not None:
        reasons.append(MasterReadDenialReason.CALLER_BUDGET_NOT_AUTHORITY)
    return tuple(reasons)


def _identifiers_match(proposal: MasterReadResourceProposal) -> bool:
    label = proposal.principal_label
    return (
        proposal.master_request_id == proposal.invocation_id
        or (label is not None and label in {proposal.master_request_id, proposal.invocation_id, proposal.resource_id})
        or proposal.resource_id in {proposal.master_request_id, proposal.invocation_id}
    )


def _token(value: object, field_name: str) -> str:
    errors = _validate_bounded_text(value, field_name, max_length=PROVENANCE_TOKEN_MAX_LENGTH)
    if errors:
        raise ContractValidationError(errors)
    if not isinstance(value, str) or value != value.strip() or any(character.isspace() for character in value):
        raise ContractValidationError(f"invalid_{field_name}")
    return value


def _optional_token(value: object, field_name: str) -> None:
    if value is not None:
        _token(value, field_name)


def _optional_text(value: object, field_name: str) -> None:
    if value is None:
        return
    errors = _validate_bounded_text(value, field_name, max_length=PROVENANCE_TOKEN_MAX_LENGTH)
    if errors:
        raise ContractValidationError(errors)
    if not isinstance(value, str) or value != value.strip():
        raise ContractValidationError(f"invalid_{field_name}")


def _tool_name(value: object) -> None:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 64
        or value != value.strip()
        or any(character.isspace() for character in value)
        or not value[0].isalpha()
    ):
        raise ContractValidationError("invalid_tool_name")


def _resource_text(value: object) -> None:
    errors = _validate_bounded_text(value, "resource_id", max_length=PROVENANCE_TOKEN_MAX_LENGTH)
    if errors:
        raise ContractValidationError(errors)
    if not isinstance(value, str) or value != value.strip() or any(character.isspace() for character in value):
        raise ContractValidationError("invalid_resource_id")


def _argument_pairs(value: object) -> None:
    if not isinstance(value, tuple):
        raise ContractValidationError("invalid_arguments")
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, tuple) or len(item) != 2 or not isinstance(item[0], str):
            raise ContractValidationError("invalid_arguments")
        if item[0] in seen:
            raise ContractValidationError("duplicate_argument")
        seen.add(item[0])


def _count(value: object, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ContractValidationError(f"invalid_{field_name}")
    return value


def _whole_second(value: object, field_name: str) -> datetime:
    parsed = _parse_whole_second(value)
    if parsed is None:
        raise ContractValidationError(f"invalid_{field_name}")
    return parsed


def _parse_whole_second(value: object) -> datetime | None:
    if not isinstance(value, str) or len(value) != 20 or not value.endswith("Z") or value[10] != "T":
        return None
    errors = _validate_utc(value, "timestamp")
    if errors:
        return None
    text = value[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.microsecond != 0:
        return None
    return parsed


def _selector(value: object) -> str | None:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or len(value) > LIVE_OPS_SELECTOR_STRING_MAX_LENGTH
        or any(character.isspace() for character in value)
        or "*" in value
        or value.casefold().startswith("arn:")
    ):
        return None
    return value


def _valid_hazard_ids(value: object) -> bool:
    if not isinstance(value, tuple) or not value or len(value) > LIVE_OPS_HAZARD_IDS_MAX:
        return False
    return all(_selector(item) is not None for item in value)


def _valid_limit(value: object) -> bool:
    return (
        type(value) is int
        and HISTORICAL_LIST_MIN <= value <= HISTORICAL_LIST_MAX
    )


__all__ = [
    "DISPATCH_GRANT_AVAILABLE",
    "HISTORICAL_LIST_MAX",
    "HISTORICAL_LIST_MIN",
    "MASTER_READ_RESOURCE_GUARD_SCHEMA_VERSION",
    "PRODUCTION_PERMIT_ISSUER_INTEGRATED",
    "MasterReadCheckStatus",
    "MasterReadDenialReason",
    "MasterReadResourceCheck",
    "MasterReadResourceProposal",
    "ReadOperation",
    "ServerCancellation",
    "ServerPopulationScope",
    "ServerReadEvaluation",
    "TrustedReadPrincipal",
    "check_master_read_resource",
    "promote_read_check_to_dispatch_permit",
    "seal_ingress_principal",
    "seal_population_scope",
    "server_read_argument_names",
    "server_read_budget",
    "server_read_resources",
    "server_read_tools",
]
