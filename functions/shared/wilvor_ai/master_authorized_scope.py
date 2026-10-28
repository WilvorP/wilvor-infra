"""Server-owned effective scope for one Master specialist step.

The resolver intersects untrusted request hints with a local test-host
record of entitlement, monitored entities, US state codes, tool bounds,
a historical policy window, and Decision identifiers. The result is a
bounded description or a denial. It is not a production principal, not
an authenticated source read, and not an execution grant.

Trust is split on purpose:

- Descriptive: request hints, including a location name such as
  ``California``. Normalizing that name to ``CA`` is only a hint.
- Evaluated: the intersection computed here.
- Host-issued: a ``LocalTestHostScope`` sealed in this process. A
  mapping, header, boolean, serialized payload, or environment value is
  not a host and cannot seal one.
- Consumed: nothing. No grant is consumed because none is issued.
- Source-authenticated: nothing. Source coverage stays unknown.

A SIGMET regional string and a census state-name lookup are recorded as
those match kinds. Neither is an exact SIGMET polygon intersection.
Absent or invalid permission denies. It does not expand to the 50
states. Approved reads stay ``GET_ITEM`` or ``QUERY``. Historical tool
bounds come only from a host policy window plus verified clocks, never
from an unbound Master plan window, and this module does not build a
``SpecialistRequest``. Callsign, model target, Live Ops discovery, and
persisted recommendation stay different candidates. A missing risk row
has no stored level.

Vocabulary mapping, not a second authority:

- ``BOUNDED_DESCRIBED`` is not ``MasterReadCheckStatus.WITHIN_ALLOWLIST``
  and not ``MasterRouteExecutionAuthority`` success. Execution authority
  stays denied in the routing vocabulary; this module has no success
  member for it.
- A described aircraft id is not
  ``DecisionCandidateRole.CURRENT_INDEPENDENTLY_VALIDATED_AUTHORIZED_TARGET``.
  ``resolve_master_decision_target`` remains the identity checker.
- ``assess_master_execution_policy`` may read this decision as a
  descriptive candidate. ``MasterExecutionScope`` is not bound here.
- ``HOST_APPROVED_POLICY`` means the eligible historical arguments came
  from the sealed policy window. It maps to the native bridge rule that
  tool bounds are independent of the unbound plan window. It does not
  construct ``IndependentHistoricalToolInterval`` or ``SpecialistRequest``.
- ``RiskPresence`` and ``StoredRiskLevel`` are the decision-contract
  vocabulary. ``ABSENT`` is not ``LOW``.

PROPOSED later readers, not imported and not implemented here:

- ``assess_master_execution_policy`` may inspect the envelope. A
  descriptive bound is not execution permission, region authority, or a
  read-resource grant.
- ``resolve_master_decision_target`` may inspect described identifiers
  as candidates. This module does not promote them and does not release
  a target.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum

from wilvor_ai.contracts import (
    PROVENANCE_TOKEN_MAX_LENGTH,
    AgentCapability,
    ContractValidationError,
    _validate_bounded_text,
    _validate_utc,
)
from wilvor_ai.decision_contracts import RiskPresence, StoredRiskLevel
from wilvor_ai.master_contracts import MasterPurpose, MasterSpecialist
from wilvor_ai.master_read_resource_guard import (
    HISTORICAL_LIST_MAX,
    HISTORICAL_LIST_MIN,
    ReadOperation,
    server_read_argument_names,
    server_read_budget,
    server_read_resources,
    server_read_tools,
)
from wilvor_ai.master_routing import (
    MAX_DECISION_STEPS,
    MAX_HISTORICAL_WINDOW,
    MAX_ROUTE_STEPS,
    MAX_UNTRUSTED_POPULATION_HINTS,
)


MASTER_AUTHORIZED_SCOPE_SCHEMA_VERSION = "wilvor.ai.master_authorized_scope.v1"

PRODUCTION_PRINCIPAL_AVAILABLE = False
AUTHENTICATED_SOURCE_READ_AVAILABLE = False
EXECUTION_GRANT_AVAILABLE = False

# Restates live_ops_public_contracts.LIVE_OPS_HAZARD_IDS_MAX. The contract
# test locks this integer to that constant.
HAZARD_IDS_MAX = 25

POLICY_INTEGRATION_HANDOFF = (
    "assess_master_execution_policy may read this decision as a descriptive "
    "candidate. BOUNDED_DESCRIBED is not execution permission, not region "
    "authority, and not a read-resource grant. MasterExecutionScope is not "
    "bound or replaced here."
)
TARGET_RESOLVER_INTEGRATION_HANDOFF = (
    "resolve_master_decision_target remains the identity checker. Callsign, "
    "model target, Live Ops discovery, and persisted recommendation are not "
    "promoted to a current authorized target. A described aircraft id is not "
    "a trusted target. RiskPresence.ABSENT is not StoredRiskLevel.LOW."
)
SPRINT_3B2_SCOPE_INTEGRATION_NOTES = (
    POLICY_INTEGRATION_HANDOFF,
    TARGET_RESOLVER_INTEGRATION_HANDOFF,
    "Eligible historical arguments come from host-approved policy and verified clocks.",
    "The unbound Master plan window is not copied into a SpecialistRequest.",
    "server_read_tools, server_read_argument_names, server_read_resources, and server_read_budget remain authoritative.",
    "TrustedReadPrincipal is not issued here.",
    "A local test-host seal is not a production principal.",
    "An event, header, boolean, or environment value cannot seal a host.",
)

_LIVE = AgentCapability.RETRIEVE_DETERMINISTIC_OPERATIONAL_CONTEXT
_HISTORY = AgentCapability.RETRIEVE_HISTORICAL_ANALYTICS
_RISK = AgentCapability.EXPLAIN_EXISTING_RISK
_RECOMMENDATIONS = AgentCapability.EXPLAIN_EXISTING_RECOMMENDATIONS
_PAIRS = {
    (MasterSpecialist.LIVE_OPS, MasterPurpose.DISCOVERY): frozenset({_LIVE}),
    (MasterSpecialist.LIVE_OPS, MasterPurpose.ANSWER): frozenset({_LIVE}),
    (MasterSpecialist.HISTORICAL_ANALYTICS, MasterPurpose.ANSWER): frozenset({_HISTORY}),
    (MasterSpecialist.DECISION, MasterPurpose.ANSWER): frozenset({_RISK, _RECOMMENDATIONS}),
}
_STATE_NAMES = (
    ("AL", "Alabama"),
    ("AK", "Alaska"),
    ("AZ", "Arizona"),
    ("AR", "Arkansas"),
    ("CA", "California"),
    ("CO", "Colorado"),
    ("CT", "Connecticut"),
    ("DE", "Delaware"),
    ("FL", "Florida"),
    ("GA", "Georgia"),
    ("HI", "Hawaii"),
    ("ID", "Idaho"),
    ("IL", "Illinois"),
    ("IN", "Indiana"),
    ("IA", "Iowa"),
    ("KS", "Kansas"),
    ("KY", "Kentucky"),
    ("LA", "Louisiana"),
    ("ME", "Maine"),
    ("MD", "Maryland"),
    ("MA", "Massachusetts"),
    ("MI", "Michigan"),
    ("MN", "Minnesota"),
    ("MS", "Mississippi"),
    ("MO", "Missouri"),
    ("MT", "Montana"),
    ("NE", "Nebraska"),
    ("NV", "Nevada"),
    ("NH", "New Hampshire"),
    ("NJ", "New Jersey"),
    ("NM", "New Mexico"),
    ("NY", "New York"),
    ("NC", "North Carolina"),
    ("ND", "North Dakota"),
    ("OH", "Ohio"),
    ("OK", "Oklahoma"),
    ("OR", "Oregon"),
    ("PA", "Pennsylvania"),
    ("RI", "Rhode Island"),
    ("SC", "South Carolina"),
    ("SD", "South Dakota"),
    ("TN", "Tennessee"),
    ("TX", "Texas"),
    ("UT", "Utah"),
    ("VT", "Vermont"),
    ("VA", "Virginia"),
    ("WA", "Washington"),
    ("WV", "West Virginia"),
    ("WI", "Wisconsin"),
    ("WY", "Wyoming"),
)
_US_STATE_CODES = frozenset(code for code, _name in _STATE_NAMES)
_NAME_TO_CODE = {name.casefold(): code for code, name in _STATE_NAMES}
_BROAD_REGION_TOKENS = frozenset(
    {"US", "USA", "ALL", "ALL_STATES", "UNITED_STATES", "UNITEDSTATES", "NATIONAL"}
)
_WILDCARD_TOKENS = frozenset(
    {"*", "ALL", "ALL_TABLES", "ALL_AIRCRAFT", "ANY", "UNRESTRICTED", "FLEET", "TABLE", "TABLES"}
)
_UNBOUNDED_TOOL = "get_observed_network_state"
_PERSISTED_TOOL = "get_persisted_airport_candidate_evidence"
_RECOMMENDATION_TOOLS = frozenset({_PERSISTED_TOOL, "get_current_recommendation"})
_TOOL_CAPABILITIES = {
    "search_current_hazards": frozenset({_LIVE}),
    "search_current_impacts": frozenset({_LIVE}),
    "search_current_encounters": frozenset({_LIVE}),
    "get_aircraft_operational_context": frozenset({_LIVE}),
    "get_hazard_operational_context": frozenset({_LIVE}),
    "get_airport_operational_context": frozenset({_LIVE}),
    "find_aircraft_by_callsign": frozenset({_LIVE}),
    "summarize_historical_encounters": frozenset({_HISTORY}),
    "summarize_historical_risks": frozenset({_HISTORY}),
    "summarize_historical_hazard_versions": frozenset({_HISTORY}),
    "list_historical_encounters": frozenset({_HISTORY}),
    "get_current_decision_context": frozenset({_RISK, _RECOMMENDATIONS}),
    "get_current_risk_evidence": frozenset({_RISK}),
    "get_current_recommendation": frozenset({_RECOMMENDATIONS}),
    "get_persisted_airport_candidate_evidence": frozenset({_RECOMMENDATIONS}),
}
_SELECTOR = {
    "search_current_hazards": "region_or_hazard_ids",
    "search_current_impacts": "region_or_hazard_ids",
    "search_current_encounters": "encounter",
    "get_aircraft_operational_context": "aircraft_id",
    "get_hazard_operational_context": "hazard_id",
    "get_airport_operational_context": "airport_id",
    "find_aircraft_by_callsign": "callsign",
    "summarize_historical_encounters": "window",
    "summarize_historical_risks": "window",
    "summarize_historical_hazard_versions": "window",
    "list_historical_encounters": "window_and_selector",
    "get_current_decision_context": "aircraft_id",
    "get_current_risk_evidence": "aircraft_id",
    "get_current_recommendation": "aircraft_id",
    "get_persisted_airport_candidate_evidence": "recommendation_id",
}
_WINDOW_ARGUMENTS = frozenset({"start_utc", "end_utc"})


class _LocalTestHostSeal:
    """Process-local seam. Production payloads are not this object."""


_SEAL = _LocalTestHostSeal()


class ScopeInputOrigin(str, Enum):
    """Where a value came from. Not every member can appear on a decision."""

    DESCRIPTIVE = "DESCRIPTIVE"
    EVALUATED = "EVALUATED"
    HOST_ISSUED = "HOST_ISSUED"
    CONSUMED = "CONSUMED"
    SOURCE_AUTHENTICATED = "SOURCE_AUTHENTICATED"


class EffectiveScopeStatus(str, Enum):
    """Evaluated intersection status. Neither member is an execution grant."""

    BOUNDED_DESCRIBED = "BOUNDED_DESCRIBED"
    DENIED = "DENIED"


class EffectiveScopeDenial(str, Enum):
    """Why the intersection is not a bounded description."""

    PRODUCTION_INPUT_NOT_A_HOST = "PRODUCTION_INPUT_NOT_A_HOST"
    ENTITLEMENT_ABSENT = "ENTITLEMENT_ABSENT"
    ENTITLEMENT_NOT_HOST_ISSUED = "ENTITLEMENT_NOT_HOST_ISSUED"
    ENTITLEMENT_MISMATCH = "ENTITLEMENT_MISMATCH"
    PRINCIPAL_LABEL_NOT_AUTHENTICATION = "PRINCIPAL_LABEL_NOT_AUTHENTICATION"
    CALLER_AUTHORIZED_NOT_AUTHORITY = "CALLER_AUTHORIZED_NOT_AUTHORITY"
    PERMISSION_SCOPE_ABSENT = "PERMISSION_SCOPE_ABSENT"
    PERMISSION_SCOPE_INVALID = "PERMISSION_SCOPE_INVALID"
    BROAD_US_ACCESS_REFUSED = "BROAD_US_ACCESS_REFUSED"
    CROSS_REGION_ESCALATION = "CROSS_REGION_ESCALATION"
    FALSE_GEOGRAPHIC_COVERAGE_CLAIM = "FALSE_GEOGRAPHIC_COVERAGE_CLAIM"
    WILDCARD_RESOURCE = "WILDCARD_RESOURCE"
    SCAN_NOT_PERMITTED = "SCAN_NOT_PERMITTED"
    ALL_AIRCRAFT_SCAN_REFUSED = "ALL_AIRCRAFT_SCAN_REFUSED"
    POPULATION_NOT_APPROVED = "POPULATION_NOT_APPROVED"
    WRONG_ENTITY = "WRONG_ENTITY"
    AMBIGUOUS_CALLSIGN = "AMBIGUOUS_CALLSIGN"
    ABSENT_ROW = "ABSENT_ROW"
    EXPIRED_SOURCE_DATA = "EXPIRED_SOURCE_DATA"
    TOOL_SELECTION_OUT_OF_BOUNDS = "TOOL_SELECTION_OUT_OF_BOUNDS"
    UNSUPPORTED_SPECIALIST_PURPOSE = "UNSUPPORTED_SPECIALIST_PURPOSE"
    UNBOUNDED_HISTORICAL_WINDOW = "UNBOUNDED_HISTORICAL_WINDOW"
    HISTORICAL_WINDOW_NOT_HOST_APPROVED = "HISTORICAL_WINDOW_NOT_HOST_APPROVED"
    UNBOUND_PLAN_WINDOW_NOT_TOOL_ARGUMENTS = "UNBOUND_PLAN_WINDOW_NOT_TOOL_ARGUMENTS"
    CLOCK_NOT_VERIFIED = "CLOCK_NOT_VERIFIED"
    CLOCKS_NOT_SEPARATE = "CLOCKS_NOT_SEPARATE"
    DEADLINE_EXPIRED = "DEADLINE_EXPIRED"
    CALLER_DEADLINE_NOT_AUTHORITY = "CALLER_DEADLINE_NOT_AUTHORITY"
    CALLER_BUDGET_NOT_AUTHORITY = "CALLER_BUDGET_NOT_AUTHORITY"
    STEP_CEILING_EXCEEDED = "STEP_CEILING_EXCEEDED"
    CANDIDATE_PROMOTION_REFUSED = "CANDIDATE_PROMOTION_REFUSED"
    DECISION_AIRCRAFT_NOT_INDEPENDENTLY_VALIDATED = (
        "DECISION_AIRCRAFT_NOT_INDEPENDENTLY_VALIDATED"
    )
    PERSISTED_RECOMMENDATION_NOT_SEPARATELY_PERMITTED = (
        "PERSISTED_RECOMMENDATION_NOT_SEPARATELY_PERMITTED"
    )
    MISSING_RISK_RECORD = "MISSING_RISK_RECORD"
    MISSING_RISK_IS_NOT_LOW = "MISSING_RISK_IS_NOT_LOW"


class GeographicMatchKind(str, Enum):
    """How a location string was noticed. None of these is polygon coverage."""

    LOCATION_NAME_NORMALIZED = "LOCATION_NAME_NORMALIZED"
    REGIONAL_STRING = "REGIONAL_STRING"
    CENSUS_NAME = "CENSUS_NAME"


class SourceCoverage(str, Enum):
    """Retained source-coverage state. Verified coverage is not a member."""

    UNKNOWN = "UNKNOWN"


class CandidateKind(str, Enum):
    """Offered selector kind. Membership does not promote the candidate."""

    CALLSIGN = "CALLSIGN"
    MODEL_TARGET = "MODEL_TARGET"
    LIVE_OPS_DISCOVERY = "LIVE_OPS_DISCOVERY"
    PERSISTED_RECOMMENDATION = "PERSISTED_RECOMMENDATION"
    REQUEST_AIRCRAFT_IDENTIFIER = "REQUEST_AIRCRAFT_IDENTIFIER"
    LOCATION_HINT = "LOCATION_HINT"


class HistoricalArgumentSource(str, Enum):
    """Where eligible historical arguments came from."""

    NONE = "NONE"
    HOST_APPROVED_POLICY = "HOST_APPROVED_POLICY"


_DENIAL_ORDER = {item: index for index, item in enumerate(EffectiveScopeDenial)}
_MATCH_ORDER = {item: index for index, item in enumerate(GeographicMatchKind)}
_CANDIDATE_ORDER = {item: index for index, item in enumerate(CandidateKind)}


def _assert_module_ceilings() -> None:
    if len(_US_STATE_CODES) != 50 or len(_NAME_TO_CODE) != 50:
        raise ContractValidationError("invalid_server_region_vocabulary")
    if MAX_HISTORICAL_WINDOW != timedelta(days=7):
        raise ContractValidationError("invalid_historical_ceiling")
    if MAX_ROUTE_STEPS != 4 or MAX_DECISION_STEPS != 1:
        raise ContractValidationError("invalid_step_ceiling")
    if HAZARD_IDS_MAX != 25:
        raise ContractValidationError("invalid_hazard_id_ceiling")
    if (
        PRODUCTION_PRINCIPAL_AVAILABLE is not False
        or AUTHENTICATED_SOURCE_READ_AVAILABLE is not False
        or EXECUTION_GRANT_AVAILABLE is not False
    ):
        raise ContractValidationError("production_principal_not_available")
    if _UNBOUNDED_TOOL in _SELECTOR:
        raise ContractValidationError("unbounded_population_not_a_bounded_tool")


_assert_module_ceilings()


def _ordered_denials(found: list[EffectiveScopeDenial]) -> tuple[EffectiveScopeDenial, ...]:
    return tuple(sorted(set(found), key=_DENIAL_ORDER.__getitem__))


def _parse_whole_second(value: object) -> datetime | None:
    if not isinstance(value, str) or len(value) != 20 or not value.endswith("Z") or value[10] != "T":
        return None
    if _validate_utc(value, "timestamp"):
        return None
    text = value[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.microsecond != 0:
        return None
    if parsed.strftime("%Y-%m-%dT%H:%M:%SZ") != value:
        return None
    return parsed


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


def _prose(value: object, field_name: str) -> str:
    errors = _validate_bounded_text(value, field_name, max_length=PROVENANCE_TOKEN_MAX_LENGTH)
    if errors:
        raise ContractValidationError(errors)
    if not isinstance(value, str) or value != value.strip():
        raise ContractValidationError(f"invalid_{field_name}")
    return value


def _optional_prose(value: object, field_name: str) -> None:
    if value is not None:
        _prose(value, field_name)


def _flag(value: object, field_name: str) -> bool:
    if type(value) is not bool:
        raise ContractValidationError(f"invalid_{field_name}")
    return value


def _count(value: object, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ContractValidationError(f"invalid_{field_name}")
    return value


def _clock(value: object, field_name: str) -> str:
    parsed = _parse_whole_second(value)
    if parsed is None or not isinstance(value, str):
        raise ContractValidationError(f"invalid_{field_name}")
    return value


def _optional_clock(value: object, field_name: str) -> None:
    if value is not None:
        _clock(value, field_name)


def _id_set(value: object, field_name: str, *, allow_empty: bool) -> frozenset[str]:
    if type(value) is not frozenset:
        raise ContractValidationError(f"invalid_{field_name}")
    if not allow_empty and not value:
        raise ContractValidationError(f"invalid_{field_name}")
    if len(value) > MAX_UNTRUSTED_POPULATION_HINTS:
        raise ContractValidationError("population_bound_exceeded")
    parsed: set[str] = set()
    for item in value:
        token = _token(item, field_name)
        if _wildcard_text(token):
            raise ContractValidationError("wildcard_not_a_population")
        parsed.add(token)
    if len(parsed) != len(value):
        raise ContractValidationError(f"invalid_{field_name}")
    return frozenset(parsed)


def _wildcard_text(value: str) -> bool:
    folded = value.casefold()
    return "*" in value or folded.upper() in _WILDCARD_TOKENS or folded in _WILDCARD_TOKENS


def _window_span(start: datetime, end: datetime) -> timedelta | None:
    if start >= end:
        return None
    return end - start


def _reject_production_flags(
    *,
    production_principal_issued: object,
    authenticated_source_read: object,
    execution_granted: object,
    dispatch_permitted: object,
    specialist_request: object,
) -> None:
    if (
        PRODUCTION_PRINCIPAL_AVAILABLE is not False
        or AUTHENTICATED_SOURCE_READ_AVAILABLE is not False
        or EXECUTION_GRANT_AVAILABLE is not False
    ):
        raise ContractValidationError("production_principal_not_available")
    if production_principal_issued is not False:
        raise ContractValidationError("production_principal_not_issued")
    if authenticated_source_read is not False:
        raise ContractValidationError("authenticated_source_read_not_issued")
    if execution_granted is not False or dispatch_permitted is not False:
        raise ContractValidationError("execution_not_granted")
    if specialist_request is not None:
        raise ContractValidationError("specialist_request_not_built")


@dataclass(frozen=True)
class LocalTestHostScope:
    """Sealed local evaluation input. This is not a production principal.

    Tests obtain one from ``bind_local_test_host``. A gateway-looking
    mapping, an authorization header, a boolean, or an environment value
    cannot become this object.
    """

    principal_ref: str
    policy_revision: str
    specialist: MasterSpecialist
    purpose: MasterPurpose
    capability: AgentCapability
    population_entity_ids: frozenset[str]
    region_state_codes: frozenset[str]
    observation_utc: str
    evaluation_utc: str
    validated_aircraft_ids: frozenset[str] = frozenset()
    permitted_recommendation_ids: frozenset[str] = frozenset()
    historical_interval_id: str | None = None
    historical_start_utc: str | None = None
    historical_end_utc: str | None = None
    row_entity_id: str | None = None
    row_present: bool | None = None
    row_callsign: str | None = None
    callsign_aircraft_ids: frozenset[str] = frozenset()
    risk_presence: RiskPresence | None = None
    stored_risk_level: StoredRiskLevel | None = None
    source_expires_utc: str | None = None
    _seal: object = None

    def __post_init__(self) -> None:
        if self._seal is not _SEAL:
            raise ContractValidationError("host_not_issued")
        _token(self.principal_ref, "principal_ref")
        _token(self.policy_revision, "policy_revision")
        if not isinstance(self.specialist, MasterSpecialist):
            raise ContractValidationError("invalid_specialist")
        if not isinstance(self.purpose, MasterPurpose):
            raise ContractValidationError("invalid_purpose")
        if not isinstance(self.capability, AgentCapability):
            raise ContractValidationError("invalid_capability")
        allowed = _PAIRS.get((self.specialist, self.purpose))
        if allowed is None or self.capability not in allowed:
            raise ContractValidationError("unsupported_specialist_purpose")
        _id_set(self.population_entity_ids, "population_entity_id", allow_empty=False)
        if type(self.region_state_codes) is not frozenset or not self.region_state_codes:
            raise ContractValidationError("invalid_region_constraint")
        if self.region_state_codes == _US_STATE_CODES or any(
            code not in _US_STATE_CODES for code in self.region_state_codes
        ):
            raise ContractValidationError("broad_us_access_refused")
        _clock(self.observation_utc, "observation_utc")
        _clock(self.evaluation_utc, "evaluation_utc")
        _id_set(self.validated_aircraft_ids, "validated_aircraft_id", allow_empty=True)
        _id_set(
            self.permitted_recommendation_ids,
            "permitted_recommendation_id",
            allow_empty=True,
        )
        _id_set(self.callsign_aircraft_ids, "callsign_aircraft_id", allow_empty=True)
        window = (
            self.historical_interval_id,
            self.historical_start_utc,
            self.historical_end_utc,
        )
        if any(item is not None for item in window) and any(item is None for item in window):
            raise ContractValidationError("invalid_historical_policy")
        if self.historical_interval_id is not None:
            _token(self.historical_interval_id, "historical_interval_id")
            start = _parse_whole_second(self.historical_start_utc)
            end = _parse_whole_second(self.historical_end_utc)
            if start is None or end is None or start >= end:
                raise ContractValidationError("invalid_historical_policy")
        _optional_token(self.row_entity_id, "row_entity_id")
        _optional_token(self.row_callsign, "row_callsign")
        _optional_clock(self.source_expires_utc, "source_expires_utc")
        if self.row_present is None:
            if (
                self.row_entity_id is not None
                or self.row_callsign is not None
                or self.risk_presence is not None
                or self.stored_risk_level is not None
            ):
                raise ContractValidationError("invalid_row_observation")
        elif type(self.row_present) is not bool or self.row_entity_id is None:
            raise ContractValidationError("invalid_row_observation")
        if self.risk_presence is None:
            if self.stored_risk_level is not None:
                raise ContractValidationError("invalid_risk_level")
        elif self.risk_presence is RiskPresence.ABSENT:
            if self.stored_risk_level is not None:
                raise ContractValidationError("absent_risk_is_not_low_risk")
            if self.row_present is not True and self.row_present is not False:
                raise ContractValidationError("invalid_row_observation")
        elif self.risk_presence is RiskPresence.PRESENT:
            if not isinstance(self.stored_risk_level, StoredRiskLevel):
                raise ContractValidationError("invalid_risk_level")
            if self.row_present is not True:
                raise ContractValidationError("absent_row_has_no_stored_risk")
        else:
            raise ContractValidationError("invalid_risk_presence")


@dataclass(frozen=True)
class EffectiveScopeRequestHints:
    """Untrusted request material. None of these fields is a host seal."""

    master_request_id: str
    planning_session_id: str
    step_id: str
    invocation_id: str
    correlation_id: str
    idempotency_key: str
    specialist: MasterSpecialist
    purpose: MasterPurpose
    capability: AgentCapability
    tool_name: str
    resource_id: str
    operation: ReadOperation
    accepted_at_utc: str
    deadline_utc: str
    arguments: tuple[tuple[str, object], ...] = ()
    location_hint: str | None = None
    claimed_state_codes: tuple[str, ...] = ()
    sigmet_region_string: str | None = None
    census_region_name: str | None = None
    claims_geographic_coverage: bool = False
    claims_exact_sigmet_polygon: bool = False
    entity_id: str | None = None
    aircraft_id: str | None = None
    callsign: str | None = None
    model_target_id: str | None = None
    live_ops_discovery_id: str | None = None
    recommendation_id: str | None = None
    population_aircraft_ids: tuple[str, ...] = ()
    historical_start_utc: str | None = None
    historical_end_utc: str | None = None
    unbound_plan_window_start_utc: str | None = None
    unbound_plan_window_end_utc: str | None = None
    copy_unbound_plan_window: bool = False
    principal_label: str | None = None
    caller_authorized: bool | None = None
    caller_deadline_utc: str | None = None
    caller_max_tool_calls: int | None = None
    requested_all_aircraft: bool = False
    missing_risk_means_low: bool = False
    requested_route_steps: int = 1
    requested_decision_steps: int = 1

    def __post_init__(self) -> None:
        identities = (
            _token(self.master_request_id, "master_request_id"),
            _token(self.planning_session_id, "planning_session_id"),
            _token(self.step_id, "step_id"),
            _token(self.invocation_id, "invocation_id"),
            _token(self.correlation_id, "correlation_id"),
            _token(self.idempotency_key, "idempotency_key"),
        )
        if len(set(identities)) != len(identities):
            raise ContractValidationError("request_identity_not_distinct")
        if not isinstance(self.specialist, MasterSpecialist):
            raise ContractValidationError("invalid_specialist")
        if not isinstance(self.purpose, MasterPurpose):
            raise ContractValidationError("invalid_purpose")
        if not isinstance(self.capability, AgentCapability):
            raise ContractValidationError("invalid_capability")
        if not isinstance(self.operation, ReadOperation):
            raise ContractValidationError("invalid_operation")
        if (
            not isinstance(self.tool_name, str)
            or not self.tool_name
            or len(self.tool_name) > 64
            or self.tool_name != self.tool_name.strip()
            or any(character.isspace() for character in self.tool_name)
            or not self.tool_name[0].isalpha()
        ):
            raise ContractValidationError("invalid_tool_name")
        _token(self.resource_id, "resource_id")
        _clock(self.accepted_at_utc, "accepted_at_utc")
        _clock(self.deadline_utc, "deadline_utc")
        _validate_arguments(self.arguments)
        _optional_prose(self.location_hint, "location_hint")
        _validate_claimed_codes(self.claimed_state_codes)
        _optional_prose(self.sigmet_region_string, "sigmet_region_string")
        _optional_prose(self.census_region_name, "census_region_name")
        _flag(self.claims_geographic_coverage, "claims_geographic_coverage")
        _flag(self.claims_exact_sigmet_polygon, "claims_exact_sigmet_polygon")
        _optional_token(self.entity_id, "entity_id")
        _optional_token(self.aircraft_id, "aircraft_id")
        _optional_token(self.callsign, "callsign")
        _optional_token(self.model_target_id, "model_target_id")
        _optional_token(self.live_ops_discovery_id, "live_ops_discovery_id")
        _optional_token(self.recommendation_id, "recommendation_id")
        _validate_population_hints(self.population_aircraft_ids)
        _optional_clock(self.historical_start_utc, "historical_start_utc")
        _optional_clock(self.historical_end_utc, "historical_end_utc")
        _optional_clock(self.unbound_plan_window_start_utc, "unbound_plan_window_start_utc")
        _optional_clock(self.unbound_plan_window_end_utc, "unbound_plan_window_end_utc")
        _flag(self.copy_unbound_plan_window, "copy_unbound_plan_window")
        _optional_token(self.principal_label, "principal_label")
        if self.caller_authorized is not None and type(self.caller_authorized) is not bool:
            raise ContractValidationError("invalid_caller_authorized")
        _optional_clock(self.caller_deadline_utc, "caller_deadline_utc")
        if self.caller_max_tool_calls is not None:
            _count(self.caller_max_tool_calls, "caller_max_tool_calls")
        _flag(self.requested_all_aircraft, "requested_all_aircraft")
        _flag(self.missing_risk_means_low, "missing_risk_means_low")
        _count(self.requested_route_steps, "requested_route_steps")
        _count(self.requested_decision_steps, "requested_decision_steps")


def _validate_arguments(value: object) -> None:
    if not isinstance(value, tuple):
        raise ContractValidationError("invalid_arguments")
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, tuple) or len(item) != 2 or not isinstance(item[0], str):
            raise ContractValidationError("invalid_arguments")
        if item[0] in seen or not item[0] or any(character.isspace() for character in item[0]):
            raise ContractValidationError("invalid_arguments")
        seen.add(item[0])


def _validate_claimed_codes(value: object) -> None:
    if not isinstance(value, tuple) or len(value) > MAX_UNTRUSTED_POPULATION_HINTS:
        raise ContractValidationError("invalid_claimed_state_codes")
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, str) or not item or item != item.strip() or any(
            character.isspace() for character in item
        ):
            raise ContractValidationError("invalid_claimed_state_codes")
        if len(item) > PROVENANCE_TOKEN_MAX_LENGTH or item in seen:
            raise ContractValidationError("invalid_claimed_state_codes")
        seen.add(item)


def _validate_population_hints(value: object) -> None:
    if not isinstance(value, tuple) or len(value) > MAX_UNTRUSTED_POPULATION_HINTS:
        raise ContractValidationError("invalid_population_aircraft_ids")
    seen: set[str] = set()
    for item in value:
        parsed = _token(item, "population_aircraft_id")
        if parsed in seen:
            raise ContractValidationError("duplicate_population_aircraft_id")
        seen.add(parsed)


@dataclass(frozen=True)
class EffectiveResourceScope:
    """Keyed read bound. A scan or an all-aircraft set cannot be represented."""

    tool_name: str
    resource_id: str
    requested_operation: ReadOperation
    approved_operation: ReadOperation | None
    argument_names: tuple[str, ...]
    population_entity_ids: tuple[str, ...]
    population_ceiling: tuple[str, ...]
    all_aircraft_scan: bool = False
    keyed_operations_only: bool = True

    def __post_init__(self) -> None:
        if self.all_aircraft_scan is not False:
            raise ContractValidationError("all_aircraft_scan_refused")
        if self.keyed_operations_only is not True:
            raise ContractValidationError("keyed_operations_only")
        if not isinstance(self.requested_operation, ReadOperation):
            raise ContractValidationError("invalid_operation")
        if self.approved_operation is not None and self.approved_operation not in {
            ReadOperation.GET_ITEM,
            ReadOperation.QUERY,
        }:
            raise ContractValidationError("scan_not_permitted")
        if len(self.population_ceiling) > MAX_UNTRUSTED_POPULATION_HINTS:
            raise ContractValidationError("population_bound_exceeded")
        if any(_wildcard_text(item) for item in self.population_entity_ids + self.population_ceiling):
            raise ContractValidationError("wildcard_not_a_population")


@dataclass(frozen=True)
class EffectiveRegionScope:
    """State-code intersection. Name resolution is not hazard coverage."""

    requested_state_codes: tuple[str, ...]
    approved_state_codes: tuple[str, ...]
    effective_state_codes: tuple[str, ...]
    location_hint: str | None
    normalized_location_code: str | None
    geographic_matches: tuple[GeographicMatchKind, ...]
    geographic_coverage_established: bool = False
    sigmet_polygon_intersection_established: bool = False
    normalization_establishes_coverage: bool = False
    source_coverage: SourceCoverage = SourceCoverage.UNKNOWN
    broad_us_default_applied: bool = False

    def __post_init__(self) -> None:
        if self.geographic_coverage_established is not False:
            raise ContractValidationError("geographic_coverage_not_established")
        if self.sigmet_polygon_intersection_established is not False:
            raise ContractValidationError("sigmet_polygon_intersection_not_established")
        if self.normalization_establishes_coverage is not False:
            raise ContractValidationError("geographic_coverage_not_established")
        if self.source_coverage is not SourceCoverage.UNKNOWN:
            raise ContractValidationError("source_coverage_not_established")
        if self.broad_us_default_applied is not False:
            raise ContractValidationError("broad_us_access_refused")
        if any(code not in _US_STATE_CODES for code in self.effective_state_codes):
            raise ContractValidationError("invalid_region_scope")
        if self.approved_state_codes and set(self.approved_state_codes) == _US_STATE_CODES:
            raise ContractValidationError("broad_us_access_refused")
        if not set(self.effective_state_codes) <= set(self.approved_state_codes):
            raise ContractValidationError("cross_region_escalation")


@dataclass(frozen=True)
class EffectiveHistoricalWindow:
    """Eligible bounds, separate from the unbound plan window."""

    requested_start_utc: str | None
    requested_end_utc: str | None
    unbound_plan_start_utc: str | None
    unbound_plan_end_utc: str | None
    eligible_start_utc: str | None
    eligible_end_utc: str | None
    tool_argument_source: HistoricalArgumentSource
    within_seven_days: bool
    copied_into_specialist_request: bool = False
    plan_window_used_as_tool_arguments: bool = False

    def __post_init__(self) -> None:
        if self.copied_into_specialist_request is not False:
            raise ContractValidationError("specialist_request_not_built")
        if self.plan_window_used_as_tool_arguments is not False:
            raise ContractValidationError("unbound_plan_window_not_tool_arguments")
        if not isinstance(self.tool_argument_source, HistoricalArgumentSource):
            raise ContractValidationError("invalid_historical_argument_source")
        if type(self.within_seven_days) is not bool:
            raise ContractValidationError("invalid_historical_window")
        eligible = (self.eligible_start_utc, self.eligible_end_utc)
        if self.tool_argument_source is HistoricalArgumentSource.NONE:
            if any(item is not None for item in eligible) or self.within_seven_days is not False:
                raise ContractValidationError("historical_window_not_host_approved")
            return
        if self.eligible_start_utc is None or self.eligible_end_utc is None:
            raise ContractValidationError("historical_window_not_host_approved")
        start = _parse_whole_second(self.eligible_start_utc)
        end = _parse_whole_second(self.eligible_end_utc)
        if start is None or end is None or start >= end or end - start > MAX_HISTORICAL_WINDOW:
            raise ContractValidationError("unbounded_historical_window")
        if self.within_seven_days is not True:
            raise ContractValidationError("unbounded_historical_window")
        if (
            self.eligible_start_utc == self.unbound_plan_start_utc
            and self.eligible_end_utc == self.unbound_plan_end_utc
            and self.unbound_plan_start_utc is not None
        ):
            raise ContractValidationError("unbound_plan_window_not_tool_arguments")


@dataclass(frozen=True)
class EffectiveDecisionTargetScope:
    """Candidate classification. Promotion flags cannot be true."""

    candidate_kinds: tuple[CandidateKind, ...]
    described_aircraft_id: str | None
    described_recommendation_id: str | None
    aircraft_id_in_validated_intersection: bool
    separate_recommendation_permission_in_intersection: bool
    risk_presence: RiskPresence | None
    stored_risk_level: StoredRiskLevel | None
    promoted_from_callsign: bool = False
    promoted_from_model_target: bool = False
    promoted_from_live_ops_discovery: bool = False
    promoted_from_persisted_recommendation: bool = False
    missing_risk_recorded_as_low: bool = False
    target_authorization_granted: bool = False

    def __post_init__(self) -> None:
        if any(
            flag is not False
            for flag in (
                self.promoted_from_callsign,
                self.promoted_from_model_target,
                self.promoted_from_live_ops_discovery,
                self.promoted_from_persisted_recommendation,
                self.missing_risk_recorded_as_low,
                self.target_authorization_granted,
            )
        ):
            raise ContractValidationError("candidate_promotion_refused")
        if type(self.aircraft_id_in_validated_intersection) is not bool:
            raise ContractValidationError("invalid_decision_target_scope")
        if type(self.separate_recommendation_permission_in_intersection) is not bool:
            raise ContractValidationError("invalid_decision_target_scope")
        if self.risk_presence is RiskPresence.ABSENT and self.stored_risk_level is not None:
            raise ContractValidationError("absent_risk_is_not_low_risk")
        if self.risk_presence is RiskPresence.PRESENT and not isinstance(
            self.stored_risk_level, StoredRiskLevel
        ):
            raise ContractValidationError("invalid_risk_level")
        if self.risk_presence is None and self.stored_risk_level is not None:
            raise ContractValidationError("invalid_risk_level")
        for identifier in (self.described_aircraft_id, self.described_recommendation_id):
            if identifier is not None:
                _token(identifier, "described_identifier")
                if _wildcard_text(identifier):
                    raise ContractValidationError("wildcard_not_a_population")


@dataclass(frozen=True)
class ScopeOriginRecord:
    """Origins actually used. Consumed and source-authenticated stay empty."""

    request_hints: ScopeInputOrigin = ScopeInputOrigin.DESCRIPTIVE
    intersection: ScopeInputOrigin = ScopeInputOrigin.EVALUATED
    host_material: ScopeInputOrigin | None = None
    consumed_grant: None = None
    source_authentication: None = None

    def __post_init__(self) -> None:
        if self.request_hints is not ScopeInputOrigin.DESCRIPTIVE:
            raise ContractValidationError("invalid_scope_origin")
        if self.intersection is not ScopeInputOrigin.EVALUATED:
            raise ContractValidationError("invalid_scope_origin")
        if self.host_material not in (None, ScopeInputOrigin.HOST_ISSUED):
            raise ContractValidationError("invalid_scope_origin")
        if self.consumed_grant is not None:
            raise ContractValidationError("consumed_grant_not_issued")
        if self.source_authentication is not None:
            raise ContractValidationError("source_not_authenticated")


@dataclass(frozen=True)
class MasterAuthorizedScopeDecision:
    """Evaluated scope envelope. Closed flags cannot become a grant.

    Construction recomputes the intersection from ``hints`` and ``host``.
    A replaced grant flag, coverage flag, or widened region fails closed.
    """

    hints: EffectiveScopeRequestHints
    host: object
    status: EffectiveScopeStatus
    denial_reasons: tuple[EffectiveScopeDenial, ...]
    master_request_id: str
    planning_session_id: str
    step_id: str
    invocation_id: str
    specialist: MasterSpecialist
    purpose: MasterPurpose
    capability: AgentCapability
    principal_ref: str | None
    policy_revision: str | None
    resource_scope: EffectiveResourceScope
    region_scope: EffectiveRegionScope
    historical_window: EffectiveHistoricalWindow
    decision_target_scope: EffectiveDecisionTargetScope
    accepted_at_utc: str
    deadline_utc: str
    observation_utc: str | None
    evaluation_utc: str | None
    idempotency_key: str
    max_executions: int
    max_model_turns: int
    max_tool_calls: int
    max_route_steps: int
    max_decision_steps: int
    correlation_id: str
    input_origins: ScopeOriginRecord
    policy_handoff: str = POLICY_INTEGRATION_HANDOFF
    target_resolver_handoff: str = TARGET_RESOLVER_INTEGRATION_HANDOFF
    production_principal_issued: bool = False
    authenticated_source_read: bool = False
    execution_granted: bool = False
    dispatch_permitted: bool = False
    specialist_request: None = None
    schema_version: str = MASTER_AUTHORIZED_SCOPE_SCHEMA_VERSION
    _seal: object = None

    def __post_init__(self) -> None:
        if self._seal is not _SEAL:
            raise ContractValidationError("effective_scope_not_server_issued")
        if self.schema_version != MASTER_AUTHORIZED_SCOPE_SCHEMA_VERSION:
            raise ContractValidationError("invalid_schema_version")
        if self.policy_handoff != POLICY_INTEGRATION_HANDOFF:
            raise ContractValidationError("invalid_policy_handoff")
        if self.target_resolver_handoff != TARGET_RESOLVER_INTEGRATION_HANDOFF:
            raise ContractValidationError("invalid_target_resolver_handoff")
        _reject_production_flags(
            production_principal_issued=self.production_principal_issued,
            authenticated_source_read=self.authenticated_source_read,
            execution_granted=self.execution_granted,
            dispatch_permitted=self.dispatch_permitted,
            specialist_request=self.specialist_request,
        )
        if type(self.hints) is not EffectiveScopeRequestHints:
            raise ContractValidationError("invalid_effective_scope_request")
        if not isinstance(self.status, EffectiveScopeStatus):
            raise ContractValidationError("invalid_effective_scope_status")
        if not isinstance(self.denial_reasons, tuple) or any(
            not isinstance(reason, EffectiveScopeDenial) for reason in self.denial_reasons
        ):
            raise ContractValidationError("invalid_denial_reasons")
        if self.status is EffectiveScopeStatus.BOUNDED_DESCRIBED and self.denial_reasons:
            raise ContractValidationError("execution_not_granted")
        if self.status is EffectiveScopeStatus.DENIED and not self.denial_reasons:
            raise ContractValidationError("invalid_denial_reasons")
        expected = _evaluate(self.hints, self.host)
        for name, value in expected.items():
            if getattr(self, name) != value:
                raise ContractValidationError("effective_scope_not_server_issued")

    def __bool__(self) -> bool:
        raise ContractValidationError("effective_scope_is_not_execution_grant")


def bind_local_test_host(
    *,
    principal_ref: str,
    policy_revision: str,
    specialist: MasterSpecialist,
    purpose: MasterPurpose,
    capability: AgentCapability,
    population_entity_ids: frozenset[str],
    region_state_codes: frozenset[str],
    observation_utc: str,
    evaluation_utc: str,
    validated_aircraft_ids: frozenset[str] = frozenset(),
    permitted_recommendation_ids: frozenset[str] = frozenset(),
    historical_interval_id: str | None = None,
    historical_start_utc: str | None = None,
    historical_end_utc: str | None = None,
    row_entity_id: str | None = None,
    row_present: bool | None = None,
    row_callsign: str | None = None,
    callsign_aircraft_ids: frozenset[str] = frozenset(),
    risk_presence: RiskPresence | None = None,
    stored_risk_level: StoredRiskLevel | None = None,
    source_expires_utc: str | None = None,
) -> LocalTestHostScope:
    """Seal one local test host. Mappings and booleans are rejected.

    The seal lets tests evaluate an intersection. It does not read the
    process environment, does not accept an event body, and does not
    issue a production principal.
    """

    for value in (
        principal_ref,
        policy_revision,
        specialist,
        purpose,
        capability,
        population_entity_ids,
        region_state_codes,
        observation_utc,
        evaluation_utc,
        validated_aircraft_ids,
        permitted_recommendation_ids,
        historical_interval_id,
        historical_start_utc,
        historical_end_utc,
        row_entity_id,
        row_present,
        row_callsign,
        callsign_aircraft_ids,
        risk_presence,
        stored_risk_level,
        source_expires_utc,
    ):
        if isinstance(value, Mapping) or isinstance(value, list):
            raise ContractValidationError("production_input_not_a_host")
    return LocalTestHostScope(
        principal_ref=principal_ref,
        policy_revision=policy_revision,
        specialist=specialist,
        purpose=purpose,
        capability=capability,
        population_entity_ids=population_entity_ids,
        region_state_codes=region_state_codes,
        observation_utc=observation_utc,
        evaluation_utc=evaluation_utc,
        validated_aircraft_ids=validated_aircraft_ids,
        permitted_recommendation_ids=permitted_recommendation_ids,
        historical_interval_id=historical_interval_id,
        historical_start_utc=historical_start_utc,
        historical_end_utc=historical_end_utc,
        row_entity_id=row_entity_id,
        row_present=row_present,
        row_callsign=row_callsign,
        callsign_aircraft_ids=callsign_aircraft_ids,
        risk_presence=risk_presence,
        stored_risk_level=stored_risk_level,
        source_expires_utc=source_expires_utc,
        _seal=_SEAL,
    )


def resolve_master_authorized_scope(
    hints: EffectiveScopeRequestHints,
    host: object = None,
) -> MasterAuthorizedScopeDecision:
    """Intersect hints with a sealed host, or deny when the host is absent.

    ``host`` is not read from the event, the Authorization header, or the
    environment. Only a ``LocalTestHostScope`` is host-issued. The returned
    decision cannot be used as a boolean grant.
    """

    if type(hints) is not EffectiveScopeRequestHints:
        raise ContractValidationError("invalid_effective_scope_request")
    outcome = _evaluate(hints, host)
    return MasterAuthorizedScopeDecision(hints=hints, host=host, _seal=_SEAL, **outcome)


def promote_effective_scope_to_execution_grant(decision: MasterAuthorizedScopeDecision) -> None:
    """Refuse to turn a described scope into an execution grant."""

    if type(decision) is not MasterAuthorizedScopeDecision:
        raise ContractValidationError("invalid_effective_scope")
    if (
        PRODUCTION_PRINCIPAL_AVAILABLE is not False
        or AUTHENTICATED_SOURCE_READ_AVAILABLE is not False
        or EXECUTION_GRANT_AVAILABLE is not False
    ):
        raise ContractValidationError("production_principal_not_available")
    raise ContractValidationError("effective_scope_is_not_execution_grant")


def _normalize_place_name(value: str | None) -> str | None:
    if value is None:
        return None
    folded = " ".join(value.casefold().split())
    if folded in _NAME_TO_CODE:
        return _NAME_TO_CODE[folded]
    compact = folded.replace(".", "")
    if len(compact) == 2 and compact.upper() in _US_STATE_CODES:
        return compact.upper()
    return None


def _requested_entities(hints: EffectiveScopeRequestHints) -> tuple[str, ...]:
    found: list[str] = []
    for item in (hints.entity_id, hints.aircraft_id, *hints.population_aircraft_ids):
        if item is not None and item not in found:
            found.append(item)
    return tuple(found)


def _candidate_kinds(hints: EffectiveScopeRequestHints) -> tuple[CandidateKind, ...]:
    found: list[CandidateKind] = []
    if hints.callsign is not None:
        found.append(CandidateKind.CALLSIGN)
    if hints.model_target_id is not None:
        found.append(CandidateKind.MODEL_TARGET)
    if hints.live_ops_discovery_id is not None:
        found.append(CandidateKind.LIVE_OPS_DISCOVERY)
    if hints.recommendation_id is not None:
        found.append(CandidateKind.PERSISTED_RECOMMENDATION)
    if hints.aircraft_id is not None:
        found.append(CandidateKind.REQUEST_AIRCRAFT_IDENTIFIER)
    if hints.location_hint is not None:
        found.append(CandidateKind.LOCATION_HINT)
    return tuple(sorted(set(found), key=_CANDIDATE_ORDER.__getitem__))


def _selector_missing(mode: str, arguments: dict[str, object]) -> bool:
    if mode == "region_or_hazard_ids":
        return "region" not in arguments and not _nonempty_ids(arguments.get("hazard_ids"))
    if mode == "encounter":
        return not any(name in arguments for name in ("aircraft_id", "callsign", "hazard_id")) and not (
            _nonempty_ids(arguments.get("hazard_ids"))
        )
    if mode == "window":
        return "start_utc" not in arguments or "end_utc" not in arguments
    if mode == "window_and_selector":
        return (
            "start_utc" not in arguments
            or "end_utc" not in arguments
            or ("aircraft_id" not in arguments and "hazard_id" not in arguments)
        )
    return mode not in arguments


def _nonempty_ids(value: object) -> bool:
    return isinstance(value, tuple) and len(value) > 0 and all(isinstance(item, str) for item in value)


def _argument_rejected(name: str, value: object) -> bool:
    if name == "hazard_ids":
        return not (
            isinstance(value, tuple)
            and 0 < len(value) <= HAZARD_IDS_MAX
            and all(_selector_token(item) for item in value)
        )
    if name == "limit":
        return type(value) is not int or not HISTORICAL_LIST_MIN <= value <= HISTORICAL_LIST_MAX
    if name in _WINDOW_ARGUMENTS:
        return _parse_whole_second(value) is None
    if name == "region":
        return not isinstance(value, str) or value not in _US_STATE_CODES
    if name == "risk_level":
        return not isinstance(value, str) or value not in {level.value for level in StoredRiskLevel}
    return not _selector_token(value)


def _selector_token(value: object) -> bool:
    return (
        isinstance(value, str)
        and bool(value)
        and value == value.strip()
        and len(value) <= PROVENANCE_TOKEN_MAX_LENGTH
        and not any(character.isspace() for character in value)
        and not _wildcard_text(value)
    )


def _evaluate(hints: EffectiveScopeRequestHints, host: object) -> dict[str, object]:
    reasons: list[EffectiveScopeDenial] = []
    sealed = type(host) is LocalTestHostScope
    if host is None:
        reasons.extend(
            (
                EffectiveScopeDenial.ENTITLEMENT_ABSENT,
                EffectiveScopeDenial.PERMISSION_SCOPE_ABSENT,
                EffectiveScopeDenial.POPULATION_NOT_APPROVED,
                EffectiveScopeDenial.CLOCK_NOT_VERIFIED,
            )
        )
    elif not sealed:
        reasons.extend(
            (
                EffectiveScopeDenial.PRODUCTION_INPUT_NOT_A_HOST,
                EffectiveScopeDenial.ENTITLEMENT_NOT_HOST_ISSUED,
                EffectiveScopeDenial.PERMISSION_SCOPE_ABSENT,
                EffectiveScopeDenial.POPULATION_NOT_APPROVED,
                EffectiveScopeDenial.CLOCK_NOT_VERIFIED,
            )
        )
    if hints.principal_label is not None:
        reasons.append(EffectiveScopeDenial.PRINCIPAL_LABEL_NOT_AUTHENTICATION)
    if hints.caller_authorized is not None:
        reasons.append(EffectiveScopeDenial.CALLER_AUTHORIZED_NOT_AUTHORITY)
    if hints.caller_deadline_utc is not None:
        reasons.append(EffectiveScopeDenial.CALLER_DEADLINE_NOT_AUTHORITY)
    if hints.caller_max_tool_calls is not None:
        reasons.append(EffectiveScopeDenial.CALLER_BUDGET_NOT_AUTHORITY)
    if hints.requested_route_steps > MAX_ROUTE_STEPS or (
        hints.specialist is MasterSpecialist.DECISION
        and hints.requested_decision_steps > MAX_DECISION_STEPS
    ):
        reasons.append(EffectiveScopeDenial.STEP_CEILING_EXCEEDED)
    allowed = _PAIRS.get((hints.specialist, hints.purpose))
    if allowed is None or hints.capability not in allowed:
        reasons.append(EffectiveScopeDenial.UNSUPPORTED_SPECIALIST_PURPOSE)
    if sealed and (
        host.specialist is not hints.specialist
        or host.purpose is not hints.purpose
        or host.capability is not hints.capability
    ):
        reasons.append(EffectiveScopeDenial.ENTITLEMENT_MISMATCH)

    arguments = dict(hints.arguments)
    _collect_tool_reasons(hints, arguments, reasons)
    region = _collect_region(hints, sealed, host, arguments, reasons)
    population = _collect_population(hints, sealed, host, reasons)
    _collect_candidates(hints, sealed, host, population, reasons)
    historical = _collect_historical(hints, sealed, host, arguments, reasons)
    _collect_clocks(hints, sealed, host, reasons)
    risk = _collect_row(hints, sealed, host, reasons)

    described = not reasons
    budget = server_read_budget(hints.specialist)
    approved_operation = hints.operation if described else None
    if described and hints.operation is ReadOperation.SCAN:
        approved_operation = None
    effective_entities = population[0] if described else ()
    eligible_start = historical[0] if described else None
    eligible_end = historical[1] if described else None
    argument_source = (
        HistoricalArgumentSource.HOST_APPROVED_POLICY
        if described and eligible_start is not None
        else HistoricalArgumentSource.NONE
    )
    within = argument_source is HistoricalArgumentSource.HOST_APPROVED_POLICY
    described_aircraft = population[2] if described else None
    described_recommendation = population[3] if described else None
    return {
        "status": (
            EffectiveScopeStatus.BOUNDED_DESCRIBED if described else EffectiveScopeStatus.DENIED
        ),
        "denial_reasons": _ordered_denials(reasons),
        "master_request_id": hints.master_request_id,
        "planning_session_id": hints.planning_session_id,
        "step_id": hints.step_id,
        "invocation_id": hints.invocation_id,
        "specialist": hints.specialist,
        "purpose": hints.purpose,
        "capability": hints.capability,
        "principal_ref": host.principal_ref if sealed else None,
        "policy_revision": host.policy_revision if sealed else None,
        "resource_scope": EffectiveResourceScope(
            tool_name=hints.tool_name,
            resource_id=hints.resource_id,
            requested_operation=hints.operation,
            approved_operation=approved_operation,
            argument_names=tuple(sorted(arguments)),
            population_entity_ids=effective_entities,
            population_ceiling=population[1],
        ),
        "region_scope": EffectiveRegionScope(
            requested_state_codes=region[0],
            approved_state_codes=region[1],
            effective_state_codes=region[2] if described else (),
            location_hint=hints.location_hint,
            normalized_location_code=region[3],
            geographic_matches=region[4],
        ),
        "historical_window": EffectiveHistoricalWindow(
            requested_start_utc=hints.historical_start_utc,
            requested_end_utc=hints.historical_end_utc,
            unbound_plan_start_utc=hints.unbound_plan_window_start_utc,
            unbound_plan_end_utc=hints.unbound_plan_window_end_utc,
            eligible_start_utc=eligible_start,
            eligible_end_utc=eligible_end,
            tool_argument_source=argument_source,
            within_seven_days=within,
        ),
        "decision_target_scope": EffectiveDecisionTargetScope(
            candidate_kinds=_candidate_kinds(hints),
            described_aircraft_id=described_aircraft,
            described_recommendation_id=described_recommendation,
            aircraft_id_in_validated_intersection=bool(described and described_aircraft is not None),
            separate_recommendation_permission_in_intersection=bool(
                described and described_recommendation is not None
            ),
            risk_presence=risk[0],
            stored_risk_level=risk[1],
        ),
        "accepted_at_utc": hints.accepted_at_utc,
        "deadline_utc": hints.deadline_utc,
        "observation_utc": host.observation_utc if sealed else None,
        "evaluation_utc": host.evaluation_utc if sealed else None,
        "idempotency_key": hints.idempotency_key,
        "max_executions": budget[0],
        "max_model_turns": budget[1],
        "max_tool_calls": budget[2],
        "max_route_steps": MAX_ROUTE_STEPS,
        "max_decision_steps": MAX_DECISION_STEPS,
        "correlation_id": hints.correlation_id,
        "input_origins": ScopeOriginRecord(
            host_material=ScopeInputOrigin.HOST_ISSUED if sealed else None
        ),
        "policy_handoff": POLICY_INTEGRATION_HANDOFF,
        "target_resolver_handoff": TARGET_RESOLVER_INTEGRATION_HANDOFF,
        "production_principal_issued": False,
        "authenticated_source_read": False,
        "execution_granted": False,
        "dispatch_permitted": False,
        "specialist_request": None,
    }


def _collect_tool_reasons(
    hints: EffectiveScopeRequestHints,
    arguments: dict[str, object],
    reasons: list[EffectiveScopeDenial],
) -> None:
    if _wildcard_text(hints.resource_id) or any(_wildcard_text(name) for name in arguments):
        reasons.append(EffectiveScopeDenial.WILDCARD_RESOURCE)
    if hints.operation is ReadOperation.SCAN:
        reasons.append(EffectiveScopeDenial.SCAN_NOT_PERMITTED)
    if hints.requested_all_aircraft or hints.tool_name == _UNBOUNDED_TOOL:
        reasons.append(EffectiveScopeDenial.ALL_AIRCRAFT_SCAN_REFUSED)
    tools = server_read_tools(hints.specialist)
    if hints.tool_name not in tools or hints.tool_name not in _SELECTOR:
        reasons.append(EffectiveScopeDenial.TOOL_SELECTION_OUT_OF_BOUNDS)
        return
    if hints.capability not in _TOOL_CAPABILITIES.get(hints.tool_name, frozenset()):
        reasons.append(EffectiveScopeDenial.TOOL_SELECTION_OUT_OF_BOUNDS)
    allowed_names = server_read_argument_names(hints.specialist, hints.tool_name)
    allowed_resources = server_read_resources(hints.specialist, hints.tool_name)
    if any(name not in allowed_names for name in arguments) or not allowed_resources:
        reasons.append(EffectiveScopeDenial.TOOL_SELECTION_OUT_OF_BOUNDS)
    if (hints.resource_id, hints.operation) not in allowed_resources:
        reasons.append(EffectiveScopeDenial.TOOL_SELECTION_OUT_OF_BOUNDS)
    if _selector_missing(_SELECTOR[hints.tool_name], arguments) or any(
        name in allowed_names and _argument_rejected(name, value) for name, value in arguments.items()
    ):
        reasons.append(EffectiveScopeDenial.TOOL_SELECTION_OUT_OF_BOUNDS)
    if "aircraft_id" in arguments and arguments.get("aircraft_id") != hints.aircraft_id:
        reasons.append(EffectiveScopeDenial.WRONG_ENTITY)
    if "recommendation_id" in arguments and arguments.get("recommendation_id") != hints.recommendation_id:
        reasons.append(EffectiveScopeDenial.CANDIDATE_PROMOTION_REFUSED)
    if "callsign" in arguments and arguments.get("callsign") != hints.callsign:
        reasons.append(EffectiveScopeDenial.CANDIDATE_PROMOTION_REFUSED)


def _collect_region(
    hints: EffectiveScopeRequestHints,
    sealed: bool,
    host: object,
    arguments: dict[str, object],
    reasons: list[EffectiveScopeDenial],
) -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...], str | None, tuple[GeographicMatchKind, ...]]:
    matches: list[GeographicMatchKind] = []
    normalized = _normalize_place_name(hints.location_hint)
    if hints.location_hint is not None and normalized is not None and hints.location_hint.casefold() not in {
        normalized.casefold()
    }:
        matches.append(GeographicMatchKind.LOCATION_NAME_NORMALIZED)
        matches.append(GeographicMatchKind.CENSUS_NAME)
    census_code = _normalize_place_name(hints.census_region_name)
    if hints.census_region_name is not None:
        matches.append(GeographicMatchKind.CENSUS_NAME)
    if hints.sigmet_region_string is not None:
        matches.append(GeographicMatchKind.REGIONAL_STRING)
    if hints.claims_geographic_coverage or hints.claims_exact_sigmet_polygon:
        reasons.append(EffectiveScopeDenial.FALSE_GEOGRAPHIC_COVERAGE_CLAIM)
    approved: set[str] = set(host.region_state_codes) if sealed else set()
    if sealed and (not approved or not approved <= _US_STATE_CODES or approved == _US_STATE_CODES):
        reasons.append(EffectiveScopeDenial.PERMISSION_SCOPE_INVALID)
        approved = set()
    requested: set[str] = set()
    for token in hints.claimed_state_codes:
        upper = token.upper()
        if upper in _BROAD_REGION_TOKENS or upper in _WILDCARD_TOKENS or _wildcard_text(token):
            if upper in _BROAD_REGION_TOKENS:
                reasons.append(EffectiveScopeDenial.BROAD_US_ACCESS_REFUSED)
            else:
                reasons.append(EffectiveScopeDenial.WILDCARD_RESOURCE)
            continue
        if upper in _US_STATE_CODES:
            requested.add(upper)
        else:
            reasons.append(EffectiveScopeDenial.CROSS_REGION_ESCALATION)
    if normalized is not None:
        requested.add(normalized)
    if census_code is not None:
        requested.add(census_code)
    if (
        hints.location_hint is not None
        and normalized is None
        and hints.location_hint.upper() not in _US_STATE_CODES
    ):
        reasons.append(EffectiveScopeDenial.CROSS_REGION_ESCALATION)
    if normalized is not None and hints.claimed_state_codes:
        claimed_states = {token.upper() for token in hints.claimed_state_codes if token.upper() in _US_STATE_CODES}
        if claimed_states and normalized not in claimed_states:
            reasons.append(EffectiveScopeDenial.CROSS_REGION_ESCALATION)
    if census_code is not None and normalized is not None and census_code != normalized:
        reasons.append(EffectiveScopeDenial.CROSS_REGION_ESCALATION)
    region_argument = arguments.get("region")
    if isinstance(region_argument, str) and region_argument in _US_STATE_CODES:
        requested.add(region_argument)
    if requested - approved:
        reasons.append(EffectiveScopeDenial.CROSS_REGION_ESCALATION)
    if isinstance(region_argument, str) and region_argument not in approved:
        reasons.append(EffectiveScopeDenial.CROSS_REGION_ESCALATION)
    if isinstance(region_argument, str) and requested and region_argument not in requested:
        reasons.append(EffectiveScopeDenial.CROSS_REGION_ESCALATION)
    mode = _SELECTOR.get(hints.tool_name)
    if mode == "region_or_hazard_ids" and "region" in arguments and not requested:
        reasons.append(EffectiveScopeDenial.CROSS_REGION_ESCALATION)
    effective = tuple(sorted(requested & approved))
    match_tuple = tuple(sorted(set(matches), key=_MATCH_ORDER.__getitem__))
    return (
        tuple(sorted(requested)),
        tuple(sorted(approved)),
        effective,
        normalized,
        match_tuple,
    )


def _collect_population(
    hints: EffectiveScopeRequestHints,
    sealed: bool,
    host: object,
    reasons: list[EffectiveScopeDenial],
) -> tuple[tuple[str, ...], tuple[str, ...], str | None, str | None]:
    ceiling = tuple(sorted(host.population_entity_ids)) if sealed else ()
    approved = set(ceiling)
    requested = _requested_entities(hints)
    if any(entity not in approved for entity in requested):
        reasons.append(EffectiveScopeDenial.WRONG_ENTITY)
    if any(_wildcard_text(entity) for entity in requested):
        reasons.append(EffectiveScopeDenial.WILDCARD_RESOURCE)
    intersection = tuple(sorted(set(requested) & approved))
    described_aircraft = None
    described_recommendation = None
    if hints.specialist is MasterSpecialist.DECISION:
        if hints.aircraft_id is None or not sealed or hints.aircraft_id not in host.validated_aircraft_ids:
            reasons.append(EffectiveScopeDenial.DECISION_AIRCRAFT_NOT_INDEPENDENTLY_VALIDATED)
        elif hints.aircraft_id in approved:
            described_aircraft = hints.aircraft_id
        recommendation_required = (
            hints.recommendation_id is not None
            or hints.capability is _RECOMMENDATIONS
            or hints.tool_name in _RECOMMENDATION_TOOLS
        )
        if recommendation_required:
            if (
                hints.recommendation_id is None
                or not sealed
                or hints.recommendation_id not in host.permitted_recommendation_ids
            ):
                reasons.append(
                    EffectiveScopeDenial.PERSISTED_RECOMMENDATION_NOT_SEPARATELY_PERMITTED
                )
            else:
                described_recommendation = hints.recommendation_id
    return intersection, ceiling, described_aircraft, described_recommendation


def _collect_candidates(
    hints: EffectiveScopeRequestHints,
    sealed: bool,
    host: object,
    population: tuple[tuple[str, ...], tuple[str, ...], str | None, str | None],
    reasons: list[EffectiveScopeDenial],
) -> None:
    matches = set(host.callsign_aircraft_ids) if sealed else set()
    if hints.callsign is not None:
        if len(matches) != 1:
            reasons.append(EffectiveScopeDenial.AMBIGUOUS_CALLSIGN)
        elif next(iter(matches)) not in set(population[1]):
            reasons.append(EffectiveScopeDenial.WRONG_ENTITY)
        elif hints.aircraft_id is None:
            reasons.append(EffectiveScopeDenial.CANDIDATE_PROMOTION_REFUSED)
        elif hints.aircraft_id not in matches:
            reasons.append(EffectiveScopeDenial.WRONG_ENTITY)
    if hints.model_target_id is not None and hints.model_target_id != hints.aircraft_id:
        reasons.append(EffectiveScopeDenial.CANDIDATE_PROMOTION_REFUSED)
    if hints.live_ops_discovery_id is not None and hints.live_ops_discovery_id != hints.aircraft_id:
        reasons.append(EffectiveScopeDenial.CANDIDATE_PROMOTION_REFUSED)
    if hints.recommendation_id is not None and hints.specialist is not MasterSpecialist.DECISION:
        reasons.append(EffectiveScopeDenial.CANDIDATE_PROMOTION_REFUSED)
    if hints.aircraft_id is None and (
        hints.model_target_id is not None or hints.live_ops_discovery_id is not None
    ):
        reasons.append(EffectiveScopeDenial.CANDIDATE_PROMOTION_REFUSED)


def _collect_historical(
    hints: EffectiveScopeRequestHints,
    sealed: bool,
    host: object,
    arguments: dict[str, object],
    reasons: list[EffectiveScopeDenial],
) -> tuple[str | None, str | None]:
    historical = hints.specialist is MasterSpecialist.HISTORICAL_ANALYTICS
    requested = (hints.historical_start_utc, hints.historical_end_utc)
    unbound = (hints.unbound_plan_window_start_utc, hints.unbound_plan_window_end_utc)
    if hints.copy_unbound_plan_window or (not historical and any(item is not None for item in unbound)):
        reasons.append(EffectiveScopeDenial.UNBOUND_PLAN_WINDOW_NOT_TOOL_ARGUMENTS)
    if not historical:
        if any(item is not None for item in requested) or any(name in arguments for name in _WINDOW_ARGUMENTS):
            reasons.append(EffectiveScopeDenial.UNBOUNDED_HISTORICAL_WINDOW)
        return None, None
    start = _parse_whole_second(hints.historical_start_utc)
    end = _parse_whole_second(hints.historical_end_utc)
    request_ok = (
        start is not None
        and end is not None
        and start < end
        and end - start <= MAX_HISTORICAL_WINDOW
    )
    if not request_ok:
        reasons.append(EffectiveScopeDenial.UNBOUNDED_HISTORICAL_WINDOW)
    if not sealed or host.historical_interval_id is None:
        reasons.append(EffectiveScopeDenial.HISTORICAL_WINDOW_NOT_HOST_APPROVED)
        if any(item is not None for item in unbound):
            reasons.append(EffectiveScopeDenial.UNBOUND_PLAN_WINDOW_NOT_TOOL_ARGUMENTS)
        return None, None
    policy_start = _parse_whole_second(host.historical_start_utc)
    policy_end = _parse_whole_second(host.historical_end_utc)
    identities = {
        hints.master_request_id,
        hints.planning_session_id,
        hints.step_id,
        hints.invocation_id,
        hints.correlation_id,
        hints.idempotency_key,
    }
    policy_ok = (
        policy_start is not None
        and policy_end is not None
        and policy_start < policy_end
        and policy_end - policy_start <= MAX_HISTORICAL_WINDOW
        and host.historical_interval_id not in identities
    )
    eligible = (host.historical_start_utc, host.historical_end_utc)
    if not policy_ok:
        reasons.append(EffectiveScopeDenial.UNBOUNDED_HISTORICAL_WINDOW)
        return None, None
    if not request_ok:
        return None, None
    if policy_start < start or policy_end > end:
        reasons.append(EffectiveScopeDenial.HISTORICAL_WINDOW_NOT_HOST_APPROVED)
        return None, None
    if eligible == unbound and unbound[0] is not None:
        reasons.append(EffectiveScopeDenial.UNBOUND_PLAN_WINDOW_NOT_TOOL_ARGUMENTS)
        return None, None
    if arguments.get("start_utc") != eligible[0] or arguments.get("end_utc") != eligible[1]:
        reasons.append(EffectiveScopeDenial.HISTORICAL_WINDOW_NOT_HOST_APPROVED)
        if (arguments.get("start_utc"), arguments.get("end_utc")) == unbound:
            reasons.append(EffectiveScopeDenial.UNBOUND_PLAN_WINDOW_NOT_TOOL_ARGUMENTS)
        return None, None
    return eligible


def _collect_clocks(
    hints: EffectiveScopeRequestHints,
    sealed: bool,
    host: object,
    reasons: list[EffectiveScopeDenial],
) -> None:
    accepted = _parse_whole_second(hints.accepted_at_utc)
    deadline = _parse_whole_second(hints.deadline_utc)
    if accepted is None or deadline is None or deadline <= accepted:
        reasons.append(EffectiveScopeDenial.DEADLINE_EXPIRED)
        return
    if not sealed:
        return
    observation = _parse_whole_second(host.observation_utc)
    evaluation = _parse_whole_second(host.evaluation_utc)
    if observation is None or evaluation is None:
        reasons.append(EffectiveScopeDenial.CLOCK_NOT_VERIFIED)
        return
    application = {accepted, deadline}
    if observation in application or evaluation in application:
        reasons.append(EffectiveScopeDenial.CLOCKS_NOT_SEPARATE)
    if deadline <= evaluation or deadline <= observation:
        reasons.append(EffectiveScopeDenial.DEADLINE_EXPIRED)
    expires = _parse_whole_second(host.source_expires_utc) if host.source_expires_utc else None
    if expires is not None and evaluation >= expires:
        reasons.append(EffectiveScopeDenial.EXPIRED_SOURCE_DATA)


def _collect_row(
    hints: EffectiveScopeRequestHints,
    sealed: bool,
    host: object,
    reasons: list[EffectiveScopeDenial],
) -> tuple[RiskPresence | None, StoredRiskLevel | None]:
    if hints.missing_risk_means_low:
        reasons.append(EffectiveScopeDenial.MISSING_RISK_IS_NOT_LOW)
    if not sealed or host.row_present is None:
        if hints.specialist is MasterSpecialist.DECISION:
            reasons.append(EffectiveScopeDenial.ABSENT_ROW)
            return RiskPresence.ABSENT, None
        return None, None
    if host.row_entity_id != (hints.aircraft_id or hints.entity_id):
        reasons.append(EffectiveScopeDenial.WRONG_ENTITY)
    if host.row_present is False:
        reasons.append(EffectiveScopeDenial.ABSENT_ROW)
        return RiskPresence.ABSENT, None
    if hints.specialist is MasterSpecialist.DECISION and host.risk_presence is RiskPresence.ABSENT:
        reasons.append(EffectiveScopeDenial.MISSING_RISK_RECORD)
        return RiskPresence.ABSENT, None
    if host.risk_presence is RiskPresence.PRESENT:
        return RiskPresence.PRESENT, host.stored_risk_level
    return host.risk_presence, None


__all__ = [
    "AUTHENTICATED_SOURCE_READ_AVAILABLE",
    "EXECUTION_GRANT_AVAILABLE",
    "HAZARD_IDS_MAX",
    "MASTER_AUTHORIZED_SCOPE_SCHEMA_VERSION",
    "POLICY_INTEGRATION_HANDOFF",
    "PRODUCTION_PRINCIPAL_AVAILABLE",
    "SPRINT_3B2_SCOPE_INTEGRATION_NOTES",
    "TARGET_RESOLVER_INTEGRATION_HANDOFF",
    "CandidateKind",
    "EffectiveDecisionTargetScope",
    "EffectiveHistoricalWindow",
    "EffectiveRegionScope",
    "EffectiveResourceScope",
    "EffectiveScopeDenial",
    "EffectiveScopeRequestHints",
    "EffectiveScopeStatus",
    "GeographicMatchKind",
    "HistoricalArgumentSource",
    "LocalTestHostScope",
    "MasterAuthorizedScopeDecision",
    "ScopeInputOrigin",
    "ScopeOriginRecord",
    "SourceCoverage",
    "bind_local_test_host",
    "promote_effective_scope_to_execution_grant",
    "resolve_master_authorized_scope",
]
