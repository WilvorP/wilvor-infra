"""Public Master to Live Ops contract v1.

Structural validity is not operational verification. Tests and mocks may
construct these objects. A LiveOpsRunResult is an authorized specialist
answer only when a trusted specialist invocation returned it. This module
does not verify evidence, dispatch tools, call a provider, or render
factual prose from claims.

The only shared dependency is ``wilvor_ai.contracts``, which is the
Phase 0 contract module already present on the AI copilot line. This
module does not import Live Ops tools, the adapter, LO2 claims, the
verifier, providers, LangGraph, or AWS clients.
"""

from __future__ import annotations

import math
from dataclasses import MISSING, dataclass, fields
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Mapping, Protocol, runtime_checkable

from wilvor_ai.contracts import (
    PROVENANCE_TOKEN_MAX_LENGTH,
    ContractValidationError,
    JsonValue,
    _required,
    _sequence,
    _validate_bounded_text,
)


LIVE_OPS_PUBLIC_SCHEMA_VERSION = "wilvor.ai.live_ops_public.v1"
LIVE_OPS_PUBLIC_TEXT_MAX_LENGTH = 8192
LIVE_OPS_PUBLIC_ANSWER_MAX_LENGTH = 8192
LIVE_OPS_PRIOR_TURN_MAX = 4
LIVE_OPS_PRIOR_TURN_TEXT_MAX_LENGTH = 2048
LIVE_OPS_PRIOR_TURNS_TOTAL_MAX_LENGTH = 8192
LIVE_OPS_SELECTOR_STRING_MAX_LENGTH = 512
LIVE_OPS_HAZARD_IDS_MAX = 25
LIVE_OPS_ENTITY_HINTS_MAX = 4
LIVE_OPS_IDENTITY_SET_MAX = 25
LIVE_OPS_MAX_CLAIMS = 32
LIVE_OPS_MAX_DIAGNOSTICS = 32
LIVE_OPS_MAX_MODEL_TURNS = 4
LIVE_OPS_MAX_TOOL_CALLS = 6
LIVE_OPS_MAX_EVIDENCE_REFERENCES = 64
LIVE_OPS_MAX_LIMITATION_CODES = 64
LIVE_OPS_PUBLIC_SPECIALIST = "LIVE_OPS"
LIVE_OPS_ENTITY_AUTHORITY = "LIVE_OPS_CURRENT_OBSERVATION"

LIVE_OPS_COMPLETENESS_NOTICE = (
    "ALL_VERIFIED means every submitted claim verified. It does not mean "
    "every operational fact was investigated or that the source data is "
    "fresh, complete, or atomic."
)

LIVE_OPS_ANSWER_INVALID_REQUEST = "The live operations request is invalid."
LIVE_OPS_ANSWER_UNSUPPORTED = "That request is outside live operations coverage."
LIVE_OPS_ANSWER_REFUSED = (
    "Live operations declined this request. No operational facts are verified."
)
LIVE_OPS_ANSWER_TOOL_FAILED = (
    "Live operations could not complete the tool execution. "
    "No operational facts are verified."
)
LIVE_OPS_ANSWER_PROVIDER = (
    "Live operations could not complete the model turn. "
    "No operational facts are verified."
)
LIVE_OPS_ANSWER_STOPPED = (
    "Live operations stopped before verification. No operational facts are verified."
)
LIVE_OPS_ANSWER_UNAVAILABLE = (
    "Live operations cannot authorize an answer for this run."
)
LIVE_OPS_ANSWER_CANCELLED = (
    "Live operations was cancelled before an authorized answer."
)
LIVE_OPS_ANSWER_NONE_VERIFIED = (
    "Live operations found no verified operational facts for this request."
)

_TOOL_NAMES = frozenset(
    {
        "search_current_hazards",
        "search_current_impacts",
        "search_current_encounters",
        "get_observed_network_state",
        "get_aircraft_operational_context",
        "get_hazard_operational_context",
        "get_airport_operational_context",
        "find_aircraft_by_callsign",
    }
)
_IMPACT_TOOLS = frozenset(
    {"search_current_impacts", "get_hazard_operational_context"}
)
_ENCOUNTER_TOOLS = frozenset(
    {"search_current_encounters", "get_aircraft_operational_context"}
)
_CONTEXT_TOOLS = frozenset(
    {
        "get_aircraft_operational_context",
        "get_hazard_operational_context",
        "get_airport_operational_context",
    }
)
_OBSERVED_TOOLS = frozenset(
    {
        "get_aircraft_operational_context",
        "search_current_impacts",
        "search_current_encounters",
    }
)
_PROJECTION_TOOLS = frozenset(
    {
        "get_aircraft_operational_context",
        "search_current_impacts",
        "get_hazard_operational_context",
    }
)
_ADVISORY_TOOLS = frozenset(
    {
        "search_current_impacts",
        "search_current_encounters",
        "get_aircraft_operational_context",
        "get_hazard_operational_context",
    }
)
_ZERO_METRICS = frozenset(
    {
        "HAZARD_QUERY_MATCH_COUNT",
        "IMPACT_RECORD_COUNT",
        "ENCOUNTER_RECORD_COUNT",
        "CALLSIGN_MATCH_COUNT",
    }
)
_VISIBLE_LISTS = frozenset(
    {
        "hazards",
        "impacts",
        "encounters",
        "matches",
        "unique_aircraft_ids",
        "callsign_matches",
        "geospatial_unevaluated",
        "operationally_unevaluated",
        "rejected_hazard_candidates",
        "rejected_hazard_selections",
        "current_aircraft_ids",
        "current_hazard_ids",
        "current_encounter_ids",
        "current_risk_ids",
        "current_recommendation_ids",
        "current_alert_ids",
    }
)
_PROSE_KEYS = frozenset(
    {
        "answer",
        "answer_text",
        "advisory_notice",
        "candidate_answer",
        "claim_text",
        "explanation",
        "factual_text",
        "message",
        "projection_trigger_reason",
        "reason",
        "state_reason",
        "statement",
        "summary",
    }
)
_FORBIDDEN_KEYS = frozenset(
    {
        "alternative_actions",
        "altitude_instruction",
        "atc_instruction",
        "best_airport",
        "computed_risk",
        "dispatch_instruction",
        "diversion_airport",
        "landing_instruction",
        "preferred_airport",
        "preferred_airport_assessment_id",
        "preferred_airport_id",
        "preferred_airport_score",
        "primary_action_details",
        "risk_score_calculated",
        "route",
        "safest_airport",
        "score",
        "selected_aircraft_id",
        "selected_airport",
        "selected_recommendation_id",
        "waypoint",
        "winning_recommendation",
        "winning_recommendation_id",
    }
)
_REQUEST_KEYS = frozenset(
    {
        "schema_version",
        "user_text",
        "request_id",
        "conversation_id",
        "prior_turns",
        "selector_hints",
        "entity_hints",
    }
)
_RESULT_KEYS = frozenset(
    {
        "schema_version",
        "request_id",
        "conversation_id",
        "correlation_id",
        "execution_status",
        "verification_outcome",
        "rendering_posture",
        "answer_origin",
        "answer",
        "reference_epoch",
        "reference_utc",
        "completeness_notice",
        "verified_claims",
        "evidence_references",
        "diagnostics",
        "structural_codes",
        "contradiction_subjects",
        "mandatory_limitations",
        "discoveries",
        "verified_entity_refs",
        "failure",
    }
)
_SCOPE_KEYS = frozenset(
    {
        "region",
        "product_type",
        "hazard_type",
        "hazard_ids",
        "aircraft_id",
        "callsign",
        "hazard_id",
    }
)
_SELECTOR_KEYS = _SCOPE_KEYS | frozenset({"airport_id"})
_CURRENTNESS_REQUIRED = {
    "AIRCRAFT_IS_CURRENT": frozenset({"aircraft_id"}),
    "HAZARD_IS_CURRENT": frozenset({"hazard_id"}),
    "HAZARD_IS_LIFECYCLE_ACTIVE": frozenset({"hazard_id"}),
    "PROJECTION_IS_CURRENT": frozenset({"aircraft_id"}),
    "ENCOUNTER_IS_CURRENT": frozenset({"encounter_id", "aircraft_id", "hazard_id"}),
    "AIRPORT_IS_CURRENT": frozenset({"airport_id"}),
    "CURRENT_IMPACTS_EVALUATED": frozenset({"hazard_id"}),
    "HAZARD_SELECTION_NOT_CURRENT": frozenset({"hazard_id", "source_version"}),
}
_CURRENTNESS_OPTIONAL = {
    "HAZARD_IS_CURRENT": frozenset({"source_version"}),
    "PROJECTION_IS_CURRENT": frozenset({"projection_id"}),
}
_POSTURE_BY_OUTCOME = {
    "ALL_VERIFIED": "COMPLETE_VERIFIED_SET",
    "PARTIALLY_VERIFIED": "INCOMPLETE_VERIFIED_SUBSET",
    "NONE_VERIFIED": "NO_VERIFIED_FACTS",
    "NOT_AUTHORIZED": "NOT_RENDERABLE",
}


class LiveOpsRunStatus(str, Enum):
    COMPLETED = "COMPLETED"
    UNSUPPORTED = "UNSUPPORTED"
    REFUSED = "REFUSED"
    INVALID_REQUEST = "INVALID_REQUEST"
    TOOL_FAILED = "TOOL_FAILED"
    PROVIDER_FAILED = "PROVIDER_FAILED"
    PROVIDER_TIMEOUT = "PROVIDER_TIMEOUT"
    RUNTIME_TIMEOUT = "RUNTIME_TIMEOUT"
    BUDGET_EXHAUSTED = "BUDGET_EXHAUSTED"
    CANCELLED = "CANCELLED"
    UNAVAILABLE = "UNAVAILABLE"


class LiveOpsFailureReason(str, Enum):
    UNSUPPORTED = "UNSUPPORTED"
    MODEL_REFUSAL = "MODEL_REFUSAL"
    MALFORMED_PUBLIC_REQUEST = "MALFORMED_PUBLIC_REQUEST"
    TOOL_CALL_VALIDATION_EXHAUSTED = "TOOL_CALL_VALIDATION_EXHAUSTED"
    TOOL_EXECUTION_FAILED = "TOOL_EXECUTION_FAILED"
    TRANSPORT_OR_API = "TRANSPORT_OR_API"
    MALFORMED_OUTPUT = "MALFORMED_OUTPUT"
    CONTEXT_LENGTH = "CONTEXT_LENGTH"
    PROVIDER_TIMEOUT = "PROVIDER_TIMEOUT"
    RUNTIME_TIMEOUT = "RUNTIME_TIMEOUT"
    MODEL_TURNS = "MODEL_TURNS"
    TOOL_CALLS = "TOOL_CALLS"
    CANCELLED = "CANCELLED"
    LEDGER_NOT_AUTHORIZED = "LEDGER_NOT_AUTHORIZED"
    INTERNAL_ERROR = "INTERNAL_ERROR"


class LiveOpsPublicValidationCode(str, Enum):
    UNKNOWN_TOOL = "UNKNOWN_TOOL"
    UNKNOWN_ARGUMENT = "UNKNOWN_ARGUMENT"
    TRUSTED_ARGUMENT_FORBIDDEN = "TRUSTED_ARGUMENT_FORBIDDEN"
    MISSING_REQUIRED_ARGUMENT = "MISSING_REQUIRED_ARGUMENT"
    INVALID_ARGUMENT_TYPE = "INVALID_ARGUMENT_TYPE"
    INVALID_ARGUMENT_VALUE = "INVALID_ARGUMENT_VALUE"
    EMPTY_OR_WHITESPACE = "EMPTY_OR_WHITESPACE"
    STRING_TOO_LONG = "STRING_TOO_LONG"
    ARRAY_TOO_LARGE = "ARRAY_TOO_LARGE"
    EMPTY_HAZARD_IDS = "EMPTY_HAZARD_IDS"
    DUPLICATE_HAZARD_ID = "DUPLICATE_HAZARD_ID"
    MISSING_SELECTOR = "MISSING_SELECTOR"
    UNBOUNDED_HAZARD_SEARCH = "UNBOUNDED_HAZARD_SEARCH"
    NESTED_VALUE_FORBIDDEN = "NESTED_VALUE_FORBIDDEN"
    DUPLICATE_TOOL_CALL = "DUPLICATE_TOOL_CALL"
    BOOLEAN_INTEGER_CONFUSION = "BOOLEAN_INTEGER_CONFUSION"
    EMPTY_BATCH = "EMPTY_BATCH"
    BATCH_TOO_LARGE = "BATCH_TOO_LARGE"
    CANONICAL_KEY_TOO_LARGE = "CANONICAL_KEY_TOO_LARGE"


class LiveOpsPublicUnsupportedReason(str, Enum):
    HISTORICAL_ANALYTICS = "HISTORICAL_ANALYTICS"
    ROUTE_GENERATION_NOT_IMPLEMENTED = "ROUTE_GENERATION_NOT_IMPLEMENTED"
    SELECTED_DIVERSION_NOT_SUPPORTED = "SELECTED_DIVERSION_NOT_SUPPORTED"
    ACTION_REQUEST = "ACTION_REQUEST"
    ATC_OR_CONTROL_INSTRUCTION = "ATC_OR_CONTROL_INSTRUCTION"
    NEW_RISK_OR_RECOMMENDATION = "NEW_RISK_OR_RECOMMENDATION"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
    OUT_OF_CATALOG = "OUT_OF_CATALOG"


class LiveOpsPublicVerificationOutcome(str, Enum):
    ALL_VERIFIED = "ALL_VERIFIED"
    PARTIALLY_VERIFIED = "PARTIALLY_VERIFIED"
    NONE_VERIFIED = "NONE_VERIFIED"
    NOT_AUTHORIZED = "NOT_AUTHORIZED"


class LiveOpsPublicRenderingPosture(str, Enum):
    COMPLETE_VERIFIED_SET = "COMPLETE_VERIFIED_SET"
    INCOMPLETE_VERIFIED_SUBSET = "INCOMPLETE_VERIFIED_SUBSET"
    NO_VERIFIED_FACTS = "NO_VERIFIED_FACTS"
    NOT_RENDERABLE = "NOT_RENDERABLE"


class LiveOpsAnswerOrigin(str, Enum):
    FIXED_SAFE_TEXT = "FIXED_SAFE_TEXT"
    DETERMINISTIC_RENDERER = "DETERMINISTIC_RENDERER"


class LiveOpsPublicClaimKind(str, Enum):
    CONFIRMED_IMPACT = "CONFIRMED_IMPACT"
    CONFIRMED_ENCOUNTER = "CONFIRMED_ENCOUNTER"
    SCOPED_COUNT = "SCOPED_COUNT"
    QUERY_RESULT_ZERO = "QUERY_RESULT_ZERO"
    CONTEXT_ROOT_ABSENT = "CONTEXT_ROOT_ABSENT"
    HAZARD_REGION_INTERSECTION = "HAZARD_REGION_INTERSECTION"
    AIRCRAFT_OBSERVED_STATE = "AIRCRAFT_OBSERVED_STATE"
    AIRCRAFT_PROJECTION_STATE = "AIRCRAFT_PROJECTION_STATE"
    CURRENTNESS = "CURRENTNESS"
    RISK_STATE = "RISK_STATE"
    RECOMMENDATION_SET = "RECOMMENDATION_SET"
    ALERT_RECORD = "ALERT_RECORD"
    CALLSIGN_MATCH_SET = "CALLSIGN_MATCH_SET"
    COVERAGE_LIMITATION = "COVERAGE_LIMITATION"
    MODEL_VISIBLE_TRUNCATION = "MODEL_VISIBLE_TRUNCATION"
    CROSS_TOOL_CONTRADICTION = "CROSS_TOOL_CONTRADICTION"


class LiveOpsPublicCountMetric(str, Enum):
    IMPACT_RECORD_COUNT = "IMPACT_RECORD_COUNT"
    ENCOUNTER_RECORD_COUNT = "ENCOUNTER_RECORD_COUNT"
    UNIQUE_AIRCRAFT_COUNT = "UNIQUE_AIRCRAFT_COUNT"
    UNIQUE_HAZARD_COUNT = "UNIQUE_HAZARD_COUNT"
    HAZARD_QUERY_MATCH_COUNT = "HAZARD_QUERY_MATCH_COUNT"
    CALLSIGN_MATCH_COUNT = "CALLSIGN_MATCH_COUNT"
    NETWORK_AIRCRAFT_COUNT = "NETWORK_AIRCRAFT_COUNT"
    NETWORK_HAZARD_COUNT = "NETWORK_HAZARD_COUNT"
    NETWORK_ENCOUNTER_COUNT = "NETWORK_ENCOUNTER_COUNT"
    NETWORK_RISK_COUNT = "NETWORK_RISK_COUNT"
    NETWORK_RECOMMENDATION_COUNT = "NETWORK_RECOMMENDATION_COUNT"
    NETWORK_ALERT_COUNT = "NETWORK_ALERT_COUNT"
    HAZARD_CONTEXT_IMPACT_COUNT = "HAZARD_CONTEXT_IMPACT_COUNT"
    AIRCRAFT_CONTEXT_ENCOUNTER_COUNT = "AIRCRAFT_CONTEXT_ENCOUNTER_COUNT"


class LiveOpsPublicCountMode(str, Enum):
    EXACT_RETURNED_SET = "EXACT_RETURNED_SET"
    CONFIRMED_SUBSET = "CONFIRMED_SUBSET"


class LiveOpsPublicCurrentnessPredicate(str, Enum):
    AIRCRAFT_IS_CURRENT = "AIRCRAFT_IS_CURRENT"
    HAZARD_IS_CURRENT = "HAZARD_IS_CURRENT"
    HAZARD_IS_LIFECYCLE_ACTIVE = "HAZARD_IS_LIFECYCLE_ACTIVE"
    PROJECTION_IS_CURRENT = "PROJECTION_IS_CURRENT"
    ENCOUNTER_IS_CURRENT = "ENCOUNTER_IS_CURRENT"
    AIRPORT_IS_CURRENT = "AIRPORT_IS_CURRENT"
    CURRENT_IMPACTS_EVALUATED = "CURRENT_IMPACTS_EVALUATED"
    HAZARD_SELECTION_NOT_CURRENT = "HAZARD_SELECTION_NOT_CURRENT"


class LiveOpsPublicRiskMode(str, Enum):
    PRESENT = "PRESENT"
    ABSENT_FROM_OBSERVED_CANDIDATES = "ABSENT_FROM_OBSERVED_CANDIDATES"


class LiveOpsPublicAdvisoryMode(str, Enum):
    PRESENT_SET = "PRESENT_SET"
    ABSENT_FROM_OBSERVED_CANDIDATES = "ABSENT_FROM_OBSERVED_CANDIDATES"


class LiveOpsPublicObservedField(str, Enum):
    LATITUDE = "latitude"
    LONGITUDE = "longitude"
    BARO_ALTITUDE_FT = "baro_altitude_ft"
    GEO_ALTITUDE_FT = "geo_altitude_ft"
    GROUND_SPEED_KT = "ground_speed_kt"
    TRACK_DEG = "track_deg"
    ON_GROUND = "on_ground"
    POSITION_TIME_UTC = "position_time_utc"
    HAS_POSITION = "has_position"
    CURRENT_H3_CELL = "current_h3_cell"
    FRESHNESS_STATUS = "freshness_status"


class LiveOpsPublicProjectionField(str, Enum):
    PROJECTION_ID = "projection_id"
    AIRCRAFT_ID = "aircraft_id"
    GENERATED_AT_UTC = "generated_at_utc"
    VALID_UNTIL_UTC = "valid_until_utc"
    PROJECTION_HORIZON_MIN = "projection_horizon_min"
    PROJECTION_STATUS = "projection_status"
    CURRENT_AIRCRAFT_H3_CELL = "current_aircraft_h3_cell"
    CONFIDENCE = "confidence"


class LiveOpsPublicRecordedFlag(str, Enum):
    CORRIDOR_INTERSECTS = "corridor_intersects"
    CENTERLINE_INTERSECTS = "centerline_intersects"
    INSIDE_NOW = "inside_now"
    EXACT_INTERSECTION_CONFIRMED = "exact_intersection_confirmed"


class LiveOpsPublicOverlapField(str, Enum):
    GEOMETRY_OVERLAP_STATUS = "geometry_overlap_status"
    TIME_OVERLAP_STATUS = "time_overlap_status"
    ALTITUDE_OVERLAP_STATUS = "altitude_overlap_status"


class LiveOpsPublicLimitationCode(str, Enum):
    FRESHNESS_NOT_ESTABLISHED = "FRESHNESS_NOT_ESTABLISHED"
    NO_SNAPSHOT_LIMITATION = "NO_SNAPSHOT_LIMITATION"
    GEOSPATIAL_UNEVALUATED_PRESENT = "GEOSPATIAL_UNEVALUATED_PRESENT"
    OPERATIONALLY_UNEVALUATED_PRESENT = "OPERATIONALLY_UNEVALUATED_PRESENT"
    REGION_UNRESOLVED = "REGION_UNRESOLVED"
    LINEAGE_MISMATCH_PRESENT = "LINEAGE_MISMATCH_PRESENT"
    ALERT_EVIDENCE_IDENTITY_UNAVAILABLE = "ALERT_EVIDENCE_IDENTITY_UNAVAILABLE"
    QUERY_SELECTION_REQUIRED = "QUERY_SELECTION_REQUIRED"
    ENCOUNTER_SELECTION_REQUIRED = "ENCOUNTER_SELECTION_REQUIRED"
    CALLSIGN_REQUIRED = "CALLSIGN_REQUIRED"
    CALLSIGN_CASE_LIMITATION = "CALLSIGN_CASE_LIMITATION"
    EVENTUAL_SCAN_LIMITATION = "EVENTUAL_SCAN_LIMITATION"
    RECOMMENDATION_ABSENCE_LIMITATION = "RECOMMENDATION_ABSENCE_LIMITATION"
    ALERT_ABSENCE_LIMITATION = "ALERT_ABSENCE_LIMITATION"
    OBSERVED_INVENTORY_NOT_A_COMPLETE_SNAPSHOT = (
        "OBSERVED_INVENTORY_NOT_A_COMPLETE_SNAPSHOT"
    )
    SPATIAL_EVALUATION_UNSUPPORTED = "SPATIAL_EVALUATION_UNSUPPORTED"
    RECORDED_OVERLAP_NOT_PHYSICAL_ENTRY = "RECORDED_OVERLAP_NOT_PHYSICAL_ENTRY"


class LiveOpsPublicContradictionSubject(str, Enum):
    HAZARD_SOURCE_VERSION = "HAZARD_SOURCE_VERSION"
    AIRCRAFT_CURRENTNESS = "AIRCRAFT_CURRENTNESS"
    ENCOUNTER_LINEAGE = "ENCOUNTER_LINEAGE"


class LiveOpsPublicClaimVerdict(str, Enum):
    VERIFIED = "VERIFIED"
    REJECTED = "REJECTED"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
    CONTRADICTED = "CONTRADICTED"


class LiveOpsDiscoveryStatus(str, Enum):
    UNIQUE_SUPPORTED_MATCH = "UNIQUE_SUPPORTED_MATCH"
    MULTIPLE_SUPPORTED_MATCHES = "MULTIPLE_SUPPORTED_MATCHES"
    NO_SUPPORTED_MATCH = "NO_SUPPORTED_MATCH"
    INCOMPLETE = "INCOMPLETE"


_STATUS_REASONS = {
    LiveOpsRunStatus.UNSUPPORTED: frozenset({LiveOpsFailureReason.UNSUPPORTED}),
    LiveOpsRunStatus.REFUSED: frozenset({LiveOpsFailureReason.MODEL_REFUSAL}),
    LiveOpsRunStatus.INVALID_REQUEST: frozenset(
        {
            LiveOpsFailureReason.MALFORMED_PUBLIC_REQUEST,
            LiveOpsFailureReason.TOOL_CALL_VALIDATION_EXHAUSTED,
        }
    ),
    LiveOpsRunStatus.TOOL_FAILED: frozenset(
        {LiveOpsFailureReason.TOOL_EXECUTION_FAILED}
    ),
    LiveOpsRunStatus.PROVIDER_FAILED: frozenset(
        {
            LiveOpsFailureReason.TRANSPORT_OR_API,
            LiveOpsFailureReason.MALFORMED_OUTPUT,
            LiveOpsFailureReason.CONTEXT_LENGTH,
        }
    ),
    LiveOpsRunStatus.PROVIDER_TIMEOUT: frozenset(
        {LiveOpsFailureReason.PROVIDER_TIMEOUT}
    ),
    LiveOpsRunStatus.RUNTIME_TIMEOUT: frozenset({LiveOpsFailureReason.RUNTIME_TIMEOUT}),
    LiveOpsRunStatus.BUDGET_EXHAUSTED: frozenset(
        {LiveOpsFailureReason.MODEL_TURNS, LiveOpsFailureReason.TOOL_CALLS}
    ),
    LiveOpsRunStatus.CANCELLED: frozenset({LiveOpsFailureReason.CANCELLED}),
    LiveOpsRunStatus.UNAVAILABLE: frozenset(
        {
            LiveOpsFailureReason.LEDGER_NOT_AUTHORIZED,
            LiveOpsFailureReason.INTERNAL_ERROR,
        }
    ),
}
_COMPLETED_OUTCOMES = frozenset(
    {
        LiveOpsPublicVerificationOutcome.ALL_VERIFIED,
        LiveOpsPublicVerificationOutcome.PARTIALLY_VERIFIED,
        LiveOpsPublicVerificationOutcome.NONE_VERIFIED,
    }
)
_DIAGNOSTIC_VERDICTS = frozenset(
    {
        LiveOpsPublicClaimVerdict.REJECTED,
        LiveOpsPublicClaimVerdict.INSUFFICIENT_EVIDENCE,
        LiveOpsPublicClaimVerdict.CONTRADICTED,
    }
)


def live_ops_reference_utc(epoch: int) -> str:
    """UTC Z form of a trusted batch epoch. Not a freshness statement."""

    if type(epoch) is not int:
        raise ContractValidationError("invalid_reference_epoch")
    try:
        return datetime.fromtimestamp(epoch, tz=timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
    except (OSError, OverflowError, ValueError) as exc:
        raise ContractValidationError("invalid_reference_epoch") from exc


def _token(value: Any, field_name: str, *, max_length: int = PROVENANCE_TOKEN_MAX_LENGTH) -> str:
    errors = _validate_bounded_text(value, field_name, max_length=max_length)
    if errors:
        raise ContractValidationError(errors)
    if value != value.strip():
        raise ContractValidationError(f"invalid_{field_name}")
    return value


def _optional_token(
    data: Mapping[str, Any],
    field_name: str,
    *,
    max_length: int = PROVENANCE_TOKEN_MAX_LENGTH,
) -> str | None:
    if field_name not in data or data[field_name] is None:
        return None
    return _token(data[field_name], field_name, max_length=max_length)


def _enum(enum_type: type[Enum], value: Any, field_name: str) -> Any:
    if isinstance(value, enum_type):
        return value
    if isinstance(value, str):
        try:
            return enum_type(value)
        except ValueError as exc:
            raise ContractValidationError(f"invalid_{field_name}") from exc
    raise ContractValidationError(f"invalid_{field_name}")


def _strict_int(value: Any, field_name: str, *, minimum: int | None = None, maximum: int | None = None) -> int:
    if type(value) is not int:
        raise ContractValidationError(f"invalid_{field_name}")
    if minimum is not None and value < minimum:
        raise ContractValidationError(f"invalid_{field_name}")
    if maximum is not None and value > maximum:
        raise ContractValidationError(f"invalid_{field_name}")
    return value


def _count(value: Any, field_name: str) -> int:
    return _strict_int(value, field_name, minimum=0)


def _bool(value: Any, field_name: str) -> bool:
    if type(value) is not bool:
        raise ContractValidationError(f"invalid_{field_name}")
    return value


def _scalar(value: Any, field_name: str) -> JsonValue:
    if type(value) is bool:
        return value
    if isinstance(value, str):
        return _token(value, field_name)
    if type(value) is int:
        return value
    if type(value) is float and math.isfinite(value):
        return value
    raise ContractValidationError(f"invalid_{field_name}")


def _as_tuple(value: Any, field_name: str) -> tuple[Any, ...]:
    if isinstance(value, list):
        value = tuple(value)
    if not isinstance(value, tuple):
        raise ContractValidationError(f"invalid_{field_name}")
    return value


def _id_tuple(value: Any, field_name: str, *, allow_empty: bool = True, max_length: int = LIVE_OPS_IDENTITY_SET_MAX) -> tuple[str, ...]:
    items = _as_tuple(value, field_name)
    if len(items) > max_length:
        raise ContractValidationError(f"too_many_{field_name}")
    parsed = tuple(_token(item, field_name) for item in items)
    if len(parsed) != len(set(parsed)):
        raise ContractValidationError(f"duplicate_{field_name}")
    if list(parsed) != sorted(parsed):
        raise ContractValidationError(f"unsorted_{field_name}")
    if not allow_empty and not parsed:
        raise ContractValidationError(f"invalid_{field_name}")
    return parsed


def _code_tuple(value: Any, field_name: str, *, allow_empty: bool = True) -> tuple[str, ...]:
    items = _as_tuple(value, field_name)
    if len(items) > LIVE_OPS_MAX_LIMITATION_CODES:
        raise ContractValidationError(f"too_many_{field_name}")
    parsed = tuple(_token(item, field_name) for item in items)
    if len(parsed) != len(set(parsed)):
        raise ContractValidationError(f"duplicate_{field_name}")
    if not allow_empty and not parsed:
        raise ContractValidationError(f"invalid_{field_name}")
    return parsed


def _reject_claim_tree(value: Any) -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            if not isinstance(key, str):
                raise ContractValidationError("unexpected_claim_field")
            folded = key.casefold()
            if folded in _PROSE_KEYS:
                raise ContractValidationError("unexpected_claim_prose")
            if folded in _FORBIDDEN_KEYS:
                raise ContractValidationError("forbidden_claim_field")
            _reject_claim_tree(item)
        return
    if isinstance(value, list):
        for item in value:
            _reject_claim_tree(item)


def _mapping(data: Any, field_name: str) -> Mapping[str, Any]:
    if not isinstance(data, Mapping):
        raise ContractValidationError(f"invalid_{field_name}")
    return data


def _unknown(data: Mapping[str, Any], allowed: frozenset[str], code: str) -> None:
    if set(data) - allowed:
        raise ContractValidationError(code)


def _schema(data: Mapping[str, Any]) -> None:
    version = _required(data, "schema_version")
    if version != LIVE_OPS_PUBLIC_SCHEMA_VERSION:
        raise ContractValidationError("invalid_schema_version")


def _tool_name(value: Any, allowed: frozenset[str]) -> str:
    name = _token(value, "tool_name")
    if name not in allowed:
        raise ContractValidationError("invalid_tool_name")
    return name


@dataclass(frozen=True)
class LiveOpsUntrustedTurn:
    """Prior conversation text. Not evidence and not a trusted fact."""

    role: str
    text: str

    def __post_init__(self) -> None:
        if self.role not in {"user", "assistant"}:
            raise ContractValidationError("invalid_prior_turn_role")
        _token(self.text, "prior_turn_text", max_length=LIVE_OPS_PRIOR_TURN_TEXT_MAX_LENGTH)

    def to_dict(self) -> dict[str, JsonValue]:
        return {"role": self.role, "text": self.text}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "LiveOpsUntrustedTurn":
        payload = _mapping(data, "prior_turn")
        _unknown(payload, frozenset({"role", "text"}), "unexpected_prior_turn_field")
        return cls(role=_required(payload, "role"), text=_required(payload, "text"))


@dataclass(frozen=True)
class LiveOpsPublicScope:
    region: str | None = None
    product_type: str | None = None
    hazard_type: str | None = None
    hazard_ids: tuple[str, ...] | None = None
    aircraft_id: str | None = None
    callsign: str | None = None
    hazard_id: str | None = None

    def __post_init__(self) -> None:
        for name in ("region", "product_type", "hazard_type", "aircraft_id", "callsign", "hazard_id"):
            value = getattr(self, name)
            if value is not None:
                _token(value, name)
        if isinstance(self.hazard_ids, list):
            object.__setattr__(self, "hazard_ids", tuple(self.hazard_ids))
        if self.hazard_ids is not None:
            parsed = _id_tuple(self.hazard_ids, "hazard_ids", allow_empty=False, max_length=LIVE_OPS_HAZARD_IDS_MAX)
            object.__setattr__(self, "hazard_ids", parsed)

    def to_dict(self) -> dict[str, JsonValue]:
        payload: dict[str, JsonValue] = {}
        for name in ("region", "product_type", "hazard_type", "aircraft_id", "callsign", "hazard_id"):
            value = getattr(self, name)
            if value is not None:
                payload[name] = value
        if self.hazard_ids is not None:
            payload["hazard_ids"] = list(self.hazard_ids)
        return payload

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "LiveOpsPublicScope":
        payload = _mapping(data, "scope")
        _reject_claim_tree(payload)
        _unknown(payload, _SCOPE_KEYS, "unexpected_claim_field")
        hazard_ids = None
        if "hazard_ids" in payload:
            hazard_ids = tuple(_sequence(payload["hazard_ids"], "hazard_ids"))
        return cls(
            region=payload["region"] if "region" in payload else None,
            product_type=payload["product_type"] if "product_type" in payload else None,
            hazard_type=payload["hazard_type"] if "hazard_type" in payload else None,
            hazard_ids=hazard_ids,
            aircraft_id=payload["aircraft_id"] if "aircraft_id" in payload else None,
            callsign=payload["callsign"] if "callsign" in payload else None,
            hazard_id=payload["hazard_id"] if "hazard_id" in payload else None,
        )


@dataclass(frozen=True)
class LiveOpsSelectorHints:
    """Untrusted selector text. These values do not execute a tool."""

    region: str | None = None
    product_type: str | None = None
    hazard_type: str | None = None
    hazard_ids: tuple[str, ...] | None = None
    aircraft_id: str | None = None
    callsign: str | None = None
    airport_id: str | None = None
    hazard_id: str | None = None

    def __post_init__(self) -> None:
        for name in (
            "region",
            "product_type",
            "hazard_type",
            "aircraft_id",
            "callsign",
            "airport_id",
            "hazard_id",
        ):
            value = getattr(self, name)
            if value is not None:
                _token(value, name, max_length=LIVE_OPS_SELECTOR_STRING_MAX_LENGTH)
        if isinstance(self.hazard_ids, list):
            object.__setattr__(self, "hazard_ids", tuple(self.hazard_ids))
        if self.hazard_ids is not None:
            object.__setattr__(
                self,
                "hazard_ids",
                _id_tuple(
                    self.hazard_ids,
                    "hazard_ids",
                    allow_empty=False,
                    max_length=LIVE_OPS_HAZARD_IDS_MAX,
                ),
            )
        if all(
            getattr(self, name) is None
            for name in (
                "region",
                "product_type",
                "hazard_type",
                "hazard_ids",
                "aircraft_id",
                "callsign",
                "airport_id",
                "hazard_id",
            )
        ):
            raise ContractValidationError("invalid_selector_hints")

    def to_dict(self) -> dict[str, JsonValue]:
        payload: dict[str, JsonValue] = {}
        for name in (
            "region",
            "product_type",
            "hazard_type",
            "aircraft_id",
            "callsign",
            "airport_id",
            "hazard_id",
        ):
            value = getattr(self, name)
            if value is not None:
                payload[name] = value
        if self.hazard_ids is not None:
            payload["hazard_ids"] = list(self.hazard_ids)
        return payload

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "LiveOpsSelectorHints":
        payload = _mapping(data, "selector_hints")
        _unknown(payload, _SELECTOR_KEYS, "unexpected_selector_field")
        hazard_ids = None
        if "hazard_ids" in payload:
            hazard_ids = tuple(_sequence(payload["hazard_ids"], "hazard_ids"))
        return cls(
            region=payload.get("region"),
            product_type=payload.get("product_type"),
            hazard_type=payload.get("hazard_type"),
            hazard_ids=hazard_ids,
            aircraft_id=payload.get("aircraft_id"),
            callsign=payload.get("callsign"),
            airport_id=payload.get("airport_id"),
            hazard_id=payload.get("hazard_id"),
        )


@dataclass(frozen=True)
class LiveOpsEntityHint:
    """Untrusted identity hint. It carries no verification authority."""

    entity_kind: str
    entity_id: str
    callsign: str | None = None

    def __post_init__(self) -> None:
        if self.entity_kind != "AIRCRAFT":
            raise ContractValidationError("invalid_entity_kind")
        _token(self.entity_id, "entity_id")
        if self.callsign is not None:
            _token(self.callsign, "callsign")

    def to_dict(self) -> dict[str, JsonValue]:
        payload: dict[str, JsonValue] = {
            "entity_kind": self.entity_kind,
            "entity_id": self.entity_id,
        }
        if self.callsign is not None:
            payload["callsign"] = self.callsign
        return payload

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "LiveOpsEntityHint":
        payload = _mapping(data, "entity_hint")
        _unknown(payload, frozenset({"entity_kind", "entity_id", "callsign"}), "unexpected_entity_hint_field")
        return cls(
            entity_kind=_required(payload, "entity_kind"),
            entity_id=_required(payload, "entity_id"),
            callsign=_optional_token(payload, "callsign"),
        )


@dataclass(frozen=True)
class LiveOpsRunRequest:
    """Untrusted specialist request. Construction does not authorize tools."""

    user_text: str
    schema_version: str = LIVE_OPS_PUBLIC_SCHEMA_VERSION
    request_id: str | None = None
    conversation_id: str | None = None
    prior_turns: tuple[LiveOpsUntrustedTurn, ...] = ()
    selector_hints: LiveOpsSelectorHints | None = None
    entity_hints: tuple[LiveOpsEntityHint, ...] = ()

    def __post_init__(self) -> None:
        if self.schema_version != LIVE_OPS_PUBLIC_SCHEMA_VERSION:
            raise ContractValidationError("invalid_schema_version")
        _token(self.user_text, "user_text", max_length=LIVE_OPS_PUBLIC_TEXT_MAX_LENGTH)
        if self.request_id is not None:
            _token(self.request_id, "request_id")
        if self.conversation_id is not None:
            _token(self.conversation_id, "conversation_id")
        if isinstance(self.prior_turns, list):
            object.__setattr__(self, "prior_turns", tuple(self.prior_turns))
        if not isinstance(self.prior_turns, tuple) or any(
            not isinstance(item, LiveOpsUntrustedTurn) for item in self.prior_turns
        ):
            raise ContractValidationError("invalid_prior_turns")
        if len(self.prior_turns) > LIVE_OPS_PRIOR_TURN_MAX:
            raise ContractValidationError("too_many_prior_turns")
        if sum(len(item.text) for item in self.prior_turns) > LIVE_OPS_PRIOR_TURNS_TOTAL_MAX_LENGTH:
            raise ContractValidationError("prior_turns_too_large")
        if self.selector_hints is not None and not isinstance(self.selector_hints, LiveOpsSelectorHints):
            raise ContractValidationError("invalid_selector_hints")
        if isinstance(self.entity_hints, list):
            object.__setattr__(self, "entity_hints", tuple(self.entity_hints))
        if not isinstance(self.entity_hints, tuple) or any(
            not isinstance(item, LiveOpsEntityHint) for item in self.entity_hints
        ):
            raise ContractValidationError("invalid_entity_hints")
        if len(self.entity_hints) > LIVE_OPS_ENTITY_HINTS_MAX:
            raise ContractValidationError("too_many_entity_hints")

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "schema_version": self.schema_version,
            "user_text": self.user_text,
            "request_id": self.request_id,
            "conversation_id": self.conversation_id,
            "prior_turns": [item.to_dict() for item in self.prior_turns],
            "selector_hints": None if self.selector_hints is None else self.selector_hints.to_dict(),
            "entity_hints": [item.to_dict() for item in self.entity_hints],
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "LiveOpsRunRequest":
        payload = _mapping(data, "live_ops_run_request")
        _unknown(payload, _REQUEST_KEYS, "unexpected_request_field")
        _schema(payload)
        turns = tuple(
            LiveOpsUntrustedTurn.from_dict(item)
            for item in _sequence(payload.get("prior_turns", ()), "prior_turns")
        )
        hints = payload.get("selector_hints")
        entities = tuple(
            LiveOpsEntityHint.from_dict(item)
            for item in _sequence(payload.get("entity_hints", ()), "entity_hints")
        )
        return cls(
            user_text=_required(payload, "user_text"),
            schema_version=payload["schema_version"],
            request_id=_optional_token(payload, "request_id"),
            conversation_id=_optional_token(payload, "conversation_id"),
            prior_turns=turns,
            selector_hints=None if hints is None else LiveOpsSelectorHints.from_dict(hints),
            entity_hints=entities,
        )


@dataclass(frozen=True)
class LiveOpsTrustedContext:
    """Server-built runtime values.

    There is no serializer. Application code constructs this object and
    keeps it out of model-visible graph state. This milestone does not
    poll ``cancelled`` or compare ``clock`` with ``deadline_epoch``.
    """

    tables: Any
    now_epoch: int
    correlation_id: str
    max_model_turns: int = LIVE_OPS_MAX_MODEL_TURNS
    max_tool_calls: int = LIVE_OPS_MAX_TOOL_CALLS
    clock: Callable[[], int] | None = None
    deadline_epoch: int | None = None
    cancelled: Callable[[], bool] | None = None

    def __post_init__(self) -> None:
        if self.tables is None:
            raise ContractValidationError("invalid_tables")
        _strict_int(self.now_epoch, "now_epoch")
        _token(self.correlation_id, "correlation_id")
        _strict_int(
            self.max_model_turns,
            "max_model_turns",
            minimum=1,
            maximum=LIVE_OPS_MAX_MODEL_TURNS,
        )
        _strict_int(
            self.max_tool_calls,
            "max_tool_calls",
            minimum=1,
            maximum=LIVE_OPS_MAX_TOOL_CALLS,
        )
        if self.clock is not None and not callable(self.clock):
            raise ContractValidationError("invalid_clock")
        if self.cancelled is not None and not callable(self.cancelled):
            raise ContractValidationError("invalid_cancelled")
        if self.deadline_epoch is not None:
            _strict_int(self.deadline_epoch, "deadline_epoch")
            if self.clock is None:
                raise ContractValidationError("deadline_requires_clock")
            if self.deadline_epoch < self.now_epoch:
                raise ContractValidationError("deadline_before_reference_clock")


@dataclass(frozen=True)
class LiveOpsPublicFlag:
    name: LiveOpsPublicRecordedFlag
    value: bool

    def __post_init__(self) -> None:
        if not isinstance(self.name, LiveOpsPublicRecordedFlag):
            raise ContractValidationError("invalid_recorded_flags")
        _bool(self.value, "recorded_flags")

    def to_dict(self) -> dict[str, JsonValue]:
        return {"name": self.name.value, "value": self.value}


@dataclass(frozen=True)
class LiveOpsPublicOverlap:
    name: LiveOpsPublicOverlapField
    value: str

    def __post_init__(self) -> None:
        if not isinstance(self.name, LiveOpsPublicOverlapField):
            raise ContractValidationError("invalid_overlap_statuses")
        _token(self.value, "overlap_status")

    def to_dict(self) -> dict[str, JsonValue]:
        return {"name": self.name.value, "value": self.value}


def _flags(value: Any) -> tuple[LiveOpsPublicFlag, ...]:
    items = _as_tuple(value, "recorded_flags")
    if len(items) > len(LiveOpsPublicRecordedFlag):
        raise ContractValidationError("too_many_recorded_flags")
    parsed: list[LiveOpsPublicFlag] = []
    for item in items:
        if not isinstance(item, LiveOpsPublicFlag):
            raise ContractValidationError("invalid_recorded_flags")
        parsed.append(item)
    names = [item.name.value for item in parsed]
    if len(names) != len(set(names)):
        raise ContractValidationError("duplicate_recorded_flags")
    if names != sorted(names):
        raise ContractValidationError("unsorted_recorded_flags")
    return tuple(parsed)


def _overlaps(value: Any) -> tuple[LiveOpsPublicOverlap, ...]:
    items = _as_tuple(value, "overlap_statuses")
    if len(items) > len(LiveOpsPublicOverlapField):
        raise ContractValidationError("too_many_overlap_statuses")
    parsed: list[LiveOpsPublicOverlap] = []
    for item in items:
        if not isinstance(item, LiveOpsPublicOverlap):
            raise ContractValidationError("invalid_overlap_statuses")
        parsed.append(item)
    names = [item.name.value for item in parsed]
    if len(names) != len(set(names)):
        raise ContractValidationError("duplicate_overlap_statuses")
    if names != sorted(names):
        raise ContractValidationError("unsorted_overlap_statuses")
    return tuple(parsed)


def _only(owner: Any, allowed: frozenset[str]) -> None:
    for item in fields(owner):
        if item.name == "kind" or item.name in allowed:
            continue
        value = getattr(owner, item.name)
        default = item.default_factory() if item.default_factory is not MISSING else item.default
        if value != default:
            raise ContractValidationError("inapplicable_claim_field")


@dataclass(frozen=True)
class LiveOpsPublicClaim:
    """Structurally valid v1 claim export.

    A successful constructor call does not mean LO2 verified the claim.
    """

    kind: LiveOpsPublicClaimKind
    tool_call_id: str | None = None
    tool_name: str | None = None
    aircraft_id: str | None = None
    hazard_id: str | None = None
    source_version: str | None = None
    encounter_id: str | None = None
    airport_id: str | None = None
    hazard_source_version: str | None = None
    projection_id: str | None = None
    recorded_flags: tuple[LiveOpsPublicFlag, ...] = ()
    overlap_statuses: tuple[LiveOpsPublicOverlap, ...] = ()
    metric: LiveOpsPublicCountMetric | None = None
    count_mode: LiveOpsPublicCountMode | None = None
    count_value: int | None = None
    scope: LiveOpsPublicScope | None = None
    entity_id: str | None = None
    region_code: str | None = None
    region_name: str | None = None
    region_query: str | None = None
    observed_field: LiveOpsPublicObservedField | None = None
    observed_value: JsonValue | None = None
    retained_not_current: bool | None = None
    projection_field: LiveOpsPublicProjectionField | None = None
    projection_value: JsonValue | None = None
    predicate: LiveOpsPublicCurrentnessPredicate | None = None
    currentness_value: bool | None = None
    risk_mode: LiveOpsPublicRiskMode | None = None
    risk_id: str | None = None
    risk_level: str | None = None
    risk_score: int | None = None
    advisory_mode: LiveOpsPublicAdvisoryMode | None = None
    recommendation_ids: tuple[str, ...] = ()
    alert_id: str | None = None
    alert_state: str | None = None
    alert_type: str | None = None
    callsign: str | None = None
    aircraft_ids: tuple[str, ...] = ()
    limitation_code: LiveOpsPublicLimitationCode | None = None
    list_name: str | None = None
    total_count: int | None = None
    tool_call_id_a: str | None = None
    tool_call_id_b: str | None = None
    subject: LiveOpsPublicContradictionSubject | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.kind, LiveOpsPublicClaimKind):
            raise ContractValidationError("invalid_kind")
        for name in ("recorded_flags", "overlap_statuses", "recommendation_ids", "aircraft_ids"):
            value = getattr(self, name)
            if isinstance(value, list):
                object.__setattr__(self, name, tuple(value))
        kind = self.kind
        if kind is LiveOpsPublicClaimKind.CONFIRMED_IMPACT:
            _only(self, frozenset({"tool_call_id", "tool_name", "aircraft_id", "hazard_id", "source_version", "encounter_id"}))
            _token(self.tool_call_id, "tool_call_id")
            _tool_name(self.tool_name, _IMPACT_TOOLS)
            for name in ("aircraft_id", "hazard_id", "source_version", "encounter_id"):
                _token(getattr(self, name), name)
        elif kind is LiveOpsPublicClaimKind.CONFIRMED_ENCOUNTER:
            _only(self, frozenset({"tool_call_id", "tool_name", "encounter_id", "aircraft_id", "hazard_id", "hazard_source_version", "projection_id", "recorded_flags", "overlap_statuses"}))
            _token(self.tool_call_id, "tool_call_id")
            _tool_name(self.tool_name, _ENCOUNTER_TOOLS)
            for name in ("encounter_id", "aircraft_id", "hazard_id", "hazard_source_version"):
                _token(getattr(self, name), name)
            if self.projection_id is not None:
                _token(self.projection_id, "projection_id")
            object.__setattr__(self, "recorded_flags", _flags(self.recorded_flags))
            object.__setattr__(self, "overlap_statuses", _overlaps(self.overlap_statuses))
        elif kind is LiveOpsPublicClaimKind.SCOPED_COUNT:
            _only(self, frozenset({"tool_call_id", "tool_name", "metric", "count_mode", "count_value", "scope"}))
            _token(self.tool_call_id, "tool_call_id")
            _tool_name(self.tool_name, _TOOL_NAMES)
            if not isinstance(self.metric, LiveOpsPublicCountMetric):
                raise ContractValidationError("invalid_metric")
            if not isinstance(self.count_mode, LiveOpsPublicCountMode):
                raise ContractValidationError("invalid_count_mode")
            _count(self.count_value, "value")
            if not isinstance(self.scope, LiveOpsPublicScope):
                raise ContractValidationError("invalid_scope")
        elif kind is LiveOpsPublicClaimKind.QUERY_RESULT_ZERO:
            _only(self, frozenset({"tool_call_id", "tool_name", "metric", "scope"}))
            _token(self.tool_call_id, "tool_call_id")
            _tool_name(self.tool_name, _TOOL_NAMES)
            if not isinstance(self.metric, LiveOpsPublicCountMetric) or self.metric.value not in _ZERO_METRICS:
                raise ContractValidationError("invalid_zero_metric")
            if not isinstance(self.scope, LiveOpsPublicScope):
                raise ContractValidationError("invalid_scope")
        elif kind is LiveOpsPublicClaimKind.CONTEXT_ROOT_ABSENT:
            _only(self, frozenset({"tool_call_id", "tool_name", "entity_id"}))
            _token(self.tool_call_id, "tool_call_id")
            _tool_name(self.tool_name, _CONTEXT_TOOLS)
            _token(self.entity_id, "entity_id")
        elif kind is LiveOpsPublicClaimKind.HAZARD_REGION_INTERSECTION:
            _only(self, frozenset({"tool_call_id", "tool_name", "hazard_id", "source_version", "region_code", "region_name", "region_query"}))
            _token(self.tool_call_id, "tool_call_id")
            if self.tool_name != "search_current_hazards":
                raise ContractValidationError("invalid_tool_name")
            for name in ("hazard_id", "source_version", "region_code", "region_name", "region_query"):
                _token(getattr(self, name), name)
        elif kind is LiveOpsPublicClaimKind.AIRCRAFT_OBSERVED_STATE:
            _only(self, frozenset({"tool_call_id", "tool_name", "aircraft_id", "observed_field", "observed_value", "retained_not_current"}))
            _token(self.tool_call_id, "tool_call_id")
            _tool_name(self.tool_name, _OBSERVED_TOOLS)
            _token(self.aircraft_id, "aircraft_id")
            if not isinstance(self.observed_field, LiveOpsPublicObservedField):
                raise ContractValidationError("invalid_field")
            _scalar(self.observed_value, "value")
            _bool(self.retained_not_current, "retained_not_current")
        elif kind is LiveOpsPublicClaimKind.AIRCRAFT_PROJECTION_STATE:
            _only(self, frozenset({"tool_call_id", "tool_name", "aircraft_id", "projection_id", "projection_field", "projection_value"}))
            _token(self.tool_call_id, "tool_call_id")
            _tool_name(self.tool_name, _PROJECTION_TOOLS)
            _token(self.aircraft_id, "aircraft_id")
            _token(self.projection_id, "projection_id")
            if not isinstance(self.projection_field, LiveOpsPublicProjectionField):
                raise ContractValidationError("invalid_field")
            _scalar(self.projection_value, "value")
        elif kind is LiveOpsPublicClaimKind.CURRENTNESS:
            _only(self, frozenset({"tool_call_id", "tool_name", "predicate", "currentness_value", "aircraft_id", "hazard_id", "source_version", "encounter_id", "airport_id", "projection_id"}))
            _token(self.tool_call_id, "tool_call_id")
            _tool_name(self.tool_name, _TOOL_NAMES)
            if not isinstance(self.predicate, LiveOpsPublicCurrentnessPredicate):
                raise ContractValidationError("invalid_predicate")
            _bool(self.currentness_value, "value")
            identities = {
                "aircraft_id": self.aircraft_id,
                "hazard_id": self.hazard_id,
                "source_version": self.source_version,
                "encounter_id": self.encounter_id,
                "airport_id": self.airport_id,
                "projection_id": self.projection_id,
            }
            for name, value in identities.items():
                if value is not None:
                    _token(value, name)
            required = _CURRENTNESS_REQUIRED[self.predicate.value]
            optional = _CURRENTNESS_OPTIONAL.get(self.predicate.value, frozenset())
            present = {name for name, value in identities.items() if value is not None}
            if not required <= present or present - required - optional:
                raise ContractValidationError("invalid_currentness_identity")
        elif kind is LiveOpsPublicClaimKind.RISK_STATE:
            _only(self, frozenset({"tool_call_id", "tool_name", "risk_mode", "encounter_id", "aircraft_id", "hazard_id", "risk_id", "risk_level", "risk_score"}))
            _token(self.tool_call_id, "tool_call_id")
            _tool_name(self.tool_name, _ADVISORY_TOOLS)
            if not isinstance(self.risk_mode, LiveOpsPublicRiskMode):
                raise ContractValidationError("invalid_risk_mode")
            for name in ("encounter_id", "aircraft_id", "hazard_id"):
                _token(getattr(self, name), name)
            if self.risk_mode is LiveOpsPublicRiskMode.PRESENT:
                _token(self.risk_id, "risk_id")
                _token(self.risk_level, "risk_level")
                _count(self.risk_score, "risk_score")
            elif any(item is not None for item in (self.risk_id, self.risk_level, self.risk_score)):
                raise ContractValidationError("absent_risk_forbids_risk_fields")
        elif kind is LiveOpsPublicClaimKind.RECOMMENDATION_SET:
            _only(self, frozenset({"tool_call_id", "tool_name", "advisory_mode", "encounter_id", "aircraft_id", "hazard_id", "recommendation_ids"}))
            _token(self.tool_call_id, "tool_call_id")
            _tool_name(self.tool_name, _ADVISORY_TOOLS)
            if not isinstance(self.advisory_mode, LiveOpsPublicAdvisoryMode):
                raise ContractValidationError("invalid_advisory_mode")
            for name in ("encounter_id", "aircraft_id", "hazard_id"):
                _token(getattr(self, name), name)
            ids = _id_tuple(self.recommendation_ids, "recommendation_ids")
            object.__setattr__(self, "recommendation_ids", ids)
            if self.advisory_mode is LiveOpsPublicAdvisoryMode.PRESENT_SET and not ids:
                raise ContractValidationError("present_set_requires_ids")
            if self.advisory_mode is LiveOpsPublicAdvisoryMode.ABSENT_FROM_OBSERVED_CANDIDATES and ids:
                raise ContractValidationError("absent_set_forbids_ids")
        elif kind is LiveOpsPublicClaimKind.ALERT_RECORD:
            _only(self, frozenset({"tool_call_id", "tool_name", "advisory_mode", "encounter_id", "aircraft_id", "hazard_id", "alert_id", "alert_state", "alert_type"}))
            _token(self.tool_call_id, "tool_call_id")
            _tool_name(self.tool_name, _ADVISORY_TOOLS)
            if not isinstance(self.advisory_mode, LiveOpsPublicAdvisoryMode):
                raise ContractValidationError("invalid_advisory_mode")
            for name in ("encounter_id", "aircraft_id", "hazard_id"):
                _token(getattr(self, name), name)
            if self.advisory_mode is LiveOpsPublicAdvisoryMode.PRESENT_SET:
                _token(self.alert_id, "alert_id")
                _token(self.alert_state, "alert_state")
                if self.alert_type is not None:
                    _token(self.alert_type, "alert_type")
            elif any(item is not None for item in (self.alert_id, self.alert_state, self.alert_type)):
                raise ContractValidationError("absent_alert_forbids_alert_fields")
        elif kind is LiveOpsPublicClaimKind.CALLSIGN_MATCH_SET:
            _only(self, frozenset({"tool_call_id", "tool_name", "callsign", "aircraft_ids"}))
            _token(self.tool_call_id, "tool_call_id")
            if self.tool_name != "find_aircraft_by_callsign":
                raise ContractValidationError("invalid_tool_name")
            _token(self.callsign, "callsign")
            object.__setattr__(self, "aircraft_ids", _id_tuple(self.aircraft_ids, "aircraft_ids"))
        elif kind is LiveOpsPublicClaimKind.COVERAGE_LIMITATION:
            _only(self, frozenset({"tool_call_id", "tool_name", "limitation_code"}))
            _token(self.tool_call_id, "tool_call_id")
            _tool_name(self.tool_name, _TOOL_NAMES)
            if not isinstance(self.limitation_code, LiveOpsPublicLimitationCode):
                raise ContractValidationError("invalid_limitation_code")
            if self.limitation_code is LiveOpsPublicLimitationCode.RECORDED_OVERLAP_NOT_PHYSICAL_ENTRY:
                raise ContractValidationError("not_a_source_limitation")
        elif kind is LiveOpsPublicClaimKind.MODEL_VISIBLE_TRUNCATION:
            _only(self, frozenset({"tool_call_id", "tool_name", "list_name", "total_count"}))
            _token(self.tool_call_id, "tool_call_id")
            _tool_name(self.tool_name, _TOOL_NAMES)
            if self.list_name not in _VISIBLE_LISTS:
                raise ContractValidationError("invalid_list_name")
            _count(self.total_count, "total_count")
        elif kind is LiveOpsPublicClaimKind.CROSS_TOOL_CONTRADICTION:
            _only(self, frozenset({"tool_call_id_a", "tool_call_id_b", "subject", "hazard_id", "aircraft_id", "encounter_id"}))
            _token(self.tool_call_id_a, "tool_call_id_a")
            _token(self.tool_call_id_b, "tool_call_id_b")
            if self.tool_call_id_a == self.tool_call_id_b:
                raise ContractValidationError("contradiction_requires_two_calls")
            if not isinstance(self.subject, LiveOpsPublicContradictionSubject):
                raise ContractValidationError("invalid_contradiction_subject")
            identities = {
                "hazard_id": self.hazard_id,
                "aircraft_id": self.aircraft_id,
                "encounter_id": self.encounter_id,
            }
            for name, value in identities.items():
                if value is not None:
                    _token(value, name)
            required = {
                LiveOpsPublicContradictionSubject.HAZARD_SOURCE_VERSION: "hazard_id",
                LiveOpsPublicContradictionSubject.AIRCRAFT_CURRENTNESS: "aircraft_id",
                LiveOpsPublicContradictionSubject.ENCOUNTER_LINEAGE: "encounter_id",
            }[self.subject]
            if identities[required] is None:
                raise ContractValidationError("missing_contradiction_identity")
            if any(name != required and value is not None for name, value in identities.items()):
                raise ContractValidationError("inapplicable_identity_field")
        else:
            raise ContractValidationError("invalid_kind")

    def cited_tool_call_ids(self) -> tuple[str, ...]:
        if self.kind is LiveOpsPublicClaimKind.CROSS_TOOL_CONTRADICTION:
            return (self.tool_call_id_a or "", self.tool_call_id_b or "")
        return (self.tool_call_id or "",)

    def to_dict(self) -> dict[str, JsonValue]:
        kind = self.kind
        if kind is LiveOpsPublicClaimKind.CONFIRMED_IMPACT:
            return {
                "kind": kind.value,
                "tool_call_id": self.tool_call_id,
                "tool_name": self.tool_name,
                "aircraft_id": self.aircraft_id,
                "hazard_id": self.hazard_id,
                "source_version": self.source_version,
                "encounter_id": self.encounter_id,
            }
        if kind is LiveOpsPublicClaimKind.CONFIRMED_ENCOUNTER:
            payload: dict[str, JsonValue] = {
                "kind": kind.value,
                "tool_call_id": self.tool_call_id,
                "tool_name": self.tool_name,
                "encounter_id": self.encounter_id,
                "aircraft_id": self.aircraft_id,
                "hazard_id": self.hazard_id,
                "hazard_source_version": self.hazard_source_version,
                "recorded_flags": [item.to_dict() for item in self.recorded_flags],
                "overlap_statuses": [item.to_dict() for item in self.overlap_statuses],
            }
            if self.projection_id is not None:
                payload["projection_id"] = self.projection_id
            return payload
        if kind is LiveOpsPublicClaimKind.SCOPED_COUNT:
            return {
                "kind": kind.value,
                "tool_call_id": self.tool_call_id,
                "tool_name": self.tool_name,
                "metric": None if self.metric is None else self.metric.value,
                "mode": None if self.count_mode is None else self.count_mode.value,
                "value": self.count_value,
                "scope": None if self.scope is None else self.scope.to_dict(),
            }
        if kind is LiveOpsPublicClaimKind.QUERY_RESULT_ZERO:
            return {
                "kind": kind.value,
                "tool_call_id": self.tool_call_id,
                "tool_name": self.tool_name,
                "metric": None if self.metric is None else self.metric.value,
                "scope": None if self.scope is None else self.scope.to_dict(),
            }
        if kind is LiveOpsPublicClaimKind.CONTEXT_ROOT_ABSENT:
            return {
                "kind": kind.value,
                "tool_call_id": self.tool_call_id,
                "tool_name": self.tool_name,
                "entity_id": self.entity_id,
            }
        if kind is LiveOpsPublicClaimKind.HAZARD_REGION_INTERSECTION:
            return {
                "kind": kind.value,
                "tool_call_id": self.tool_call_id,
                "tool_name": self.tool_name,
                "hazard_id": self.hazard_id,
                "source_version": self.source_version,
                "region_code": self.region_code,
                "region_name": self.region_name,
                "region_query": self.region_query,
            }
        if kind is LiveOpsPublicClaimKind.AIRCRAFT_OBSERVED_STATE:
            return {
                "kind": kind.value,
                "tool_call_id": self.tool_call_id,
                "tool_name": self.tool_name,
                "aircraft_id": self.aircraft_id,
                "field": None if self.observed_field is None else self.observed_field.value,
                "value": self.observed_value,
                "retained_not_current": self.retained_not_current,
            }
        if kind is LiveOpsPublicClaimKind.AIRCRAFT_PROJECTION_STATE:
            return {
                "kind": kind.value,
                "tool_call_id": self.tool_call_id,
                "tool_name": self.tool_name,
                "aircraft_id": self.aircraft_id,
                "projection_id": self.projection_id,
                "field": None if self.projection_field is None else self.projection_field.value,
                "value": self.projection_value,
            }
        if kind is LiveOpsPublicClaimKind.CURRENTNESS:
            payload = {
                "kind": kind.value,
                "tool_call_id": self.tool_call_id,
                "tool_name": self.tool_name,
                "predicate": None if self.predicate is None else self.predicate.value,
                "value": self.currentness_value,
            }
            for name in ("aircraft_id", "hazard_id", "source_version", "encounter_id", "airport_id", "projection_id"):
                value = getattr(self, name)
                if value is not None:
                    payload[name] = value
            return payload
        if kind is LiveOpsPublicClaimKind.RISK_STATE:
            payload = {
                "kind": kind.value,
                "tool_call_id": self.tool_call_id,
                "tool_name": self.tool_name,
                "mode": None if self.risk_mode is None else self.risk_mode.value,
                "encounter_id": self.encounter_id,
                "aircraft_id": self.aircraft_id,
                "hazard_id": self.hazard_id,
            }
            if self.risk_mode is LiveOpsPublicRiskMode.PRESENT:
                payload["risk_id"] = self.risk_id
                payload["risk_level"] = self.risk_level
                payload["risk_score"] = self.risk_score
            return payload
        if kind is LiveOpsPublicClaimKind.RECOMMENDATION_SET:
            return {
                "kind": kind.value,
                "tool_call_id": self.tool_call_id,
                "tool_name": self.tool_name,
                "mode": None if self.advisory_mode is None else self.advisory_mode.value,
                "encounter_id": self.encounter_id,
                "aircraft_id": self.aircraft_id,
                "hazard_id": self.hazard_id,
                "recommendation_ids": list(self.recommendation_ids),
            }
        if kind is LiveOpsPublicClaimKind.ALERT_RECORD:
            payload = {
                "kind": kind.value,
                "tool_call_id": self.tool_call_id,
                "tool_name": self.tool_name,
                "mode": None if self.advisory_mode is None else self.advisory_mode.value,
                "encounter_id": self.encounter_id,
                "aircraft_id": self.aircraft_id,
                "hazard_id": self.hazard_id,
            }
            if self.advisory_mode is LiveOpsPublicAdvisoryMode.PRESENT_SET:
                payload["alert_id"] = self.alert_id
                payload["alert_state"] = self.alert_state
                if self.alert_type is not None:
                    payload["alert_type"] = self.alert_type
            return payload
        if kind is LiveOpsPublicClaimKind.CALLSIGN_MATCH_SET:
            return {
                "kind": kind.value,
                "tool_call_id": self.tool_call_id,
                "tool_name": self.tool_name,
                "callsign": self.callsign,
                "aircraft_ids": list(self.aircraft_ids),
            }
        if kind is LiveOpsPublicClaimKind.COVERAGE_LIMITATION:
            return {
                "kind": kind.value,
                "tool_call_id": self.tool_call_id,
                "tool_name": self.tool_name,
                "code": None if self.limitation_code is None else self.limitation_code.value,
            }
        if kind is LiveOpsPublicClaimKind.MODEL_VISIBLE_TRUNCATION:
            return {
                "kind": kind.value,
                "tool_call_id": self.tool_call_id,
                "tool_name": self.tool_name,
                "list_name": self.list_name,
                "total_count": self.total_count,
            }
        payload = {
            "kind": kind.value,
            "tool_call_id_a": self.tool_call_id_a,
            "tool_call_id_b": self.tool_call_id_b,
            "subject": None if self.subject is None else self.subject.value,
        }
        for name in ("hazard_id", "aircraft_id", "encounter_id"):
            value = getattr(self, name)
            if value is not None:
                payload[name] = value
        return payload

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "LiveOpsPublicClaim":
        payload = _mapping(data, "live_ops_public_claim")
        _reject_claim_tree(payload)
        kind = _enum(LiveOpsPublicClaimKind, _required(payload, "kind"), "kind")
        allowed = _CLAIM_FIELDS[kind]
        _unknown(payload, allowed, "unexpected_claim_field")
        return _claim_from_payload(kind, payload)


_CLAIM_FIELDS = {
    LiveOpsPublicClaimKind.CONFIRMED_IMPACT: frozenset({"kind", "tool_call_id", "tool_name", "aircraft_id", "hazard_id", "source_version", "encounter_id"}),
    LiveOpsPublicClaimKind.CONFIRMED_ENCOUNTER: frozenset({"kind", "tool_call_id", "tool_name", "encounter_id", "aircraft_id", "hazard_id", "hazard_source_version", "projection_id", "recorded_flags", "overlap_statuses"}),
    LiveOpsPublicClaimKind.SCOPED_COUNT: frozenset({"kind", "tool_call_id", "tool_name", "metric", "mode", "value", "scope"}),
    LiveOpsPublicClaimKind.QUERY_RESULT_ZERO: frozenset({"kind", "tool_call_id", "tool_name", "metric", "scope"}),
    LiveOpsPublicClaimKind.CONTEXT_ROOT_ABSENT: frozenset({"kind", "tool_call_id", "tool_name", "entity_id"}),
    LiveOpsPublicClaimKind.HAZARD_REGION_INTERSECTION: frozenset({"kind", "tool_call_id", "tool_name", "hazard_id", "source_version", "region_code", "region_name", "region_query"}),
    LiveOpsPublicClaimKind.AIRCRAFT_OBSERVED_STATE: frozenset({"kind", "tool_call_id", "tool_name", "aircraft_id", "field", "value", "retained_not_current"}),
    LiveOpsPublicClaimKind.AIRCRAFT_PROJECTION_STATE: frozenset({"kind", "tool_call_id", "tool_name", "aircraft_id", "projection_id", "field", "value"}),
    LiveOpsPublicClaimKind.CURRENTNESS: frozenset({"kind", "tool_call_id", "tool_name", "predicate", "value", "aircraft_id", "hazard_id", "source_version", "encounter_id", "airport_id", "projection_id"}),
    LiveOpsPublicClaimKind.RISK_STATE: frozenset({"kind", "tool_call_id", "tool_name", "mode", "encounter_id", "aircraft_id", "hazard_id", "risk_id", "risk_level", "risk_score"}),
    LiveOpsPublicClaimKind.RECOMMENDATION_SET: frozenset({"kind", "tool_call_id", "tool_name", "mode", "encounter_id", "aircraft_id", "hazard_id", "recommendation_ids"}),
    LiveOpsPublicClaimKind.ALERT_RECORD: frozenset({"kind", "tool_call_id", "tool_name", "mode", "encounter_id", "aircraft_id", "hazard_id", "alert_id", "alert_state", "alert_type"}),
    LiveOpsPublicClaimKind.CALLSIGN_MATCH_SET: frozenset({"kind", "tool_call_id", "tool_name", "callsign", "aircraft_ids"}),
    LiveOpsPublicClaimKind.COVERAGE_LIMITATION: frozenset({"kind", "tool_call_id", "tool_name", "code"}),
    LiveOpsPublicClaimKind.MODEL_VISIBLE_TRUNCATION: frozenset({"kind", "tool_call_id", "tool_name", "list_name", "total_count"}),
    LiveOpsPublicClaimKind.CROSS_TOOL_CONTRADICTION: frozenset({"kind", "tool_call_id_a", "tool_call_id_b", "subject", "hazard_id", "aircraft_id", "encounter_id"}),
}


def _pair_flags(value: Any, flag_type: type[Enum], field_name: str, value_parser: Callable[[Any], Any]) -> tuple[Any, ...]:
    rows = _sequence(value, field_name) if value is not None else []
    parsed = []
    for item in rows:
        row = _mapping(item, field_name)
        _unknown(row, frozenset({"name", "value"}), "unexpected_claim_field")
        name = _enum(flag_type, _required(row, "name"), "name")
        parsed.append((name, value_parser(_required(row, "value"))))
    return tuple(parsed)


def _claim_from_payload(kind: LiveOpsPublicClaimKind, payload: Mapping[str, Any]) -> LiveOpsPublicClaim:
    if kind is LiveOpsPublicClaimKind.CONFIRMED_IMPACT:
        return LiveOpsPublicClaim(
            kind=kind,
            tool_call_id=_required(payload, "tool_call_id"),
            tool_name=_required(payload, "tool_name"),
            aircraft_id=_required(payload, "aircraft_id"),
            hazard_id=_required(payload, "hazard_id"),
            source_version=_required(payload, "source_version"),
            encounter_id=_required(payload, "encounter_id"),
        )
    if kind is LiveOpsPublicClaimKind.CONFIRMED_ENCOUNTER:
        flags = []
        for name, value in _pair_flags(payload.get("recorded_flags", ()), LiveOpsPublicRecordedFlag, "recorded_flags", lambda item: _bool(item, "recorded_flags")):
            flags.append(LiveOpsPublicFlag(name=name, value=value))
        overlaps = []
        for name, value in _pair_flags(payload.get("overlap_statuses", ()), LiveOpsPublicOverlapField, "overlap_statuses", lambda item: _token(item, "overlap_status")):
            overlaps.append(LiveOpsPublicOverlap(name=name, value=value))
        return LiveOpsPublicClaim(
            kind=kind,
            tool_call_id=_required(payload, "tool_call_id"),
            tool_name=_required(payload, "tool_name"),
            encounter_id=_required(payload, "encounter_id"),
            aircraft_id=_required(payload, "aircraft_id"),
            hazard_id=_required(payload, "hazard_id"),
            hazard_source_version=_required(payload, "hazard_source_version"),
            projection_id=_optional_token(payload, "projection_id"),
            recorded_flags=tuple(flags),
            overlap_statuses=tuple(overlaps),
        )
    if kind is LiveOpsPublicClaimKind.SCOPED_COUNT:
        return LiveOpsPublicClaim(
            kind=kind,
            tool_call_id=_required(payload, "tool_call_id"),
            tool_name=_required(payload, "tool_name"),
            metric=_enum(LiveOpsPublicCountMetric, _required(payload, "metric"), "metric"),
            count_mode=_enum(LiveOpsPublicCountMode, _required(payload, "mode"), "mode"),
            count_value=_required(payload, "value"),
            scope=LiveOpsPublicScope.from_dict(_required(payload, "scope")),
        )
    if kind is LiveOpsPublicClaimKind.QUERY_RESULT_ZERO:
        return LiveOpsPublicClaim(
            kind=kind,
            tool_call_id=_required(payload, "tool_call_id"),
            tool_name=_required(payload, "tool_name"),
            metric=_enum(LiveOpsPublicCountMetric, _required(payload, "metric"), "metric"),
            scope=LiveOpsPublicScope.from_dict(_required(payload, "scope")),
        )
    if kind is LiveOpsPublicClaimKind.CONTEXT_ROOT_ABSENT:
        return LiveOpsPublicClaim(
            kind=kind,
            tool_call_id=_required(payload, "tool_call_id"),
            tool_name=_required(payload, "tool_name"),
            entity_id=_required(payload, "entity_id"),
        )
    if kind is LiveOpsPublicClaimKind.HAZARD_REGION_INTERSECTION:
        return LiveOpsPublicClaim(
            kind=kind,
            tool_call_id=_required(payload, "tool_call_id"),
            tool_name=_required(payload, "tool_name"),
            hazard_id=_required(payload, "hazard_id"),
            source_version=_required(payload, "source_version"),
            region_code=_required(payload, "region_code"),
            region_name=_required(payload, "region_name"),
            region_query=_required(payload, "region_query"),
        )
    if kind is LiveOpsPublicClaimKind.AIRCRAFT_OBSERVED_STATE:
        return LiveOpsPublicClaim(
            kind=kind,
            tool_call_id=_required(payload, "tool_call_id"),
            tool_name=_required(payload, "tool_name"),
            aircraft_id=_required(payload, "aircraft_id"),
            observed_field=_enum(LiveOpsPublicObservedField, _required(payload, "field"), "field"),
            observed_value=_required(payload, "value"),
            retained_not_current=_required(payload, "retained_not_current"),
        )
    if kind is LiveOpsPublicClaimKind.AIRCRAFT_PROJECTION_STATE:
        return LiveOpsPublicClaim(
            kind=kind,
            tool_call_id=_required(payload, "tool_call_id"),
            tool_name=_required(payload, "tool_name"),
            aircraft_id=_required(payload, "aircraft_id"),
            projection_id=_required(payload, "projection_id"),
            projection_field=_enum(LiveOpsPublicProjectionField, _required(payload, "field"), "field"),
            projection_value=_required(payload, "value"),
        )
    if kind is LiveOpsPublicClaimKind.CURRENTNESS:
        return LiveOpsPublicClaim(
            kind=kind,
            tool_call_id=_required(payload, "tool_call_id"),
            tool_name=_required(payload, "tool_name"),
            predicate=_enum(LiveOpsPublicCurrentnessPredicate, _required(payload, "predicate"), "predicate"),
            currentness_value=_required(payload, "value"),
            aircraft_id=_optional_token(payload, "aircraft_id"),
            hazard_id=_optional_token(payload, "hazard_id"),
            source_version=_optional_token(payload, "source_version"),
            encounter_id=_optional_token(payload, "encounter_id"),
            airport_id=_optional_token(payload, "airport_id"),
            projection_id=_optional_token(payload, "projection_id"),
        )
    if kind is LiveOpsPublicClaimKind.RISK_STATE:
        return LiveOpsPublicClaim(
            kind=kind,
            tool_call_id=_required(payload, "tool_call_id"),
            tool_name=_required(payload, "tool_name"),
            risk_mode=_enum(LiveOpsPublicRiskMode, _required(payload, "mode"), "mode"),
            encounter_id=_required(payload, "encounter_id"),
            aircraft_id=_required(payload, "aircraft_id"),
            hazard_id=_required(payload, "hazard_id"),
            risk_id=_optional_token(payload, "risk_id"),
            risk_level=_optional_token(payload, "risk_level"),
            risk_score=payload["risk_score"] if "risk_score" in payload else None,
        )
    if kind is LiveOpsPublicClaimKind.RECOMMENDATION_SET:
        return LiveOpsPublicClaim(
            kind=kind,
            tool_call_id=_required(payload, "tool_call_id"),
            tool_name=_required(payload, "tool_name"),
            advisory_mode=_enum(LiveOpsPublicAdvisoryMode, _required(payload, "mode"), "mode"),
            encounter_id=_required(payload, "encounter_id"),
            aircraft_id=_required(payload, "aircraft_id"),
            hazard_id=_required(payload, "hazard_id"),
            recommendation_ids=tuple(_sequence(payload.get("recommendation_ids", ()), "recommendation_ids")),
        )
    if kind is LiveOpsPublicClaimKind.ALERT_RECORD:
        return LiveOpsPublicClaim(
            kind=kind,
            tool_call_id=_required(payload, "tool_call_id"),
            tool_name=_required(payload, "tool_name"),
            advisory_mode=_enum(LiveOpsPublicAdvisoryMode, _required(payload, "mode"), "mode"),
            encounter_id=_required(payload, "encounter_id"),
            aircraft_id=_required(payload, "aircraft_id"),
            hazard_id=_required(payload, "hazard_id"),
            alert_id=_optional_token(payload, "alert_id"),
            alert_state=_optional_token(payload, "alert_state"),
            alert_type=_optional_token(payload, "alert_type"),
        )
    if kind is LiveOpsPublicClaimKind.CALLSIGN_MATCH_SET:
        return LiveOpsPublicClaim(
            kind=kind,
            tool_call_id=_required(payload, "tool_call_id"),
            tool_name=_required(payload, "tool_name"),
            callsign=_required(payload, "callsign"),
            aircraft_ids=tuple(_sequence(_required(payload, "aircraft_ids"), "aircraft_ids")),
        )
    if kind is LiveOpsPublicClaimKind.COVERAGE_LIMITATION:
        return LiveOpsPublicClaim(
            kind=kind,
            tool_call_id=_required(payload, "tool_call_id"),
            tool_name=_required(payload, "tool_name"),
            limitation_code=_enum(LiveOpsPublicLimitationCode, _required(payload, "code"), "code"),
        )
    if kind is LiveOpsPublicClaimKind.MODEL_VISIBLE_TRUNCATION:
        return LiveOpsPublicClaim(
            kind=kind,
            tool_call_id=_required(payload, "tool_call_id"),
            tool_name=_required(payload, "tool_name"),
            list_name=_required(payload, "list_name"),
            total_count=_required(payload, "total_count"),
        )
    return LiveOpsPublicClaim(
        kind=kind,
        tool_call_id_a=_required(payload, "tool_call_id_a"),
        tool_call_id_b=_required(payload, "tool_call_id_b"),
        subject=_enum(LiveOpsPublicContradictionSubject, _required(payload, "subject"), "subject"),
        hazard_id=_optional_token(payload, "hazard_id"),
        aircraft_id=_optional_token(payload, "aircraft_id"),
        encounter_id=_optional_token(payload, "encounter_id"),
    )


@dataclass(frozen=True)
class LiveOpsEvidenceReference:
    tool_call_id: str
    tool_name: str
    as_of_epoch: int
    as_of_utc: str
    limitation_codes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _token(self.tool_call_id, "tool_call_id")
        _tool_name(self.tool_name, _TOOL_NAMES)
        _strict_int(self.as_of_epoch, "as_of_epoch")
        if self.as_of_utc != live_ops_reference_utc(self.as_of_epoch):
            raise ContractValidationError("invalid_as_of_utc")
        if isinstance(self.limitation_codes, list):
            object.__setattr__(self, "limitation_codes", tuple(self.limitation_codes))
        object.__setattr__(self, "limitation_codes", _code_tuple(self.limitation_codes, "limitation_codes"))

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "tool_call_id": self.tool_call_id,
            "tool_name": self.tool_name,
            "as_of_epoch": self.as_of_epoch,
            "as_of_utc": self.as_of_utc,
            "limitation_codes": list(self.limitation_codes),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "LiveOpsEvidenceReference":
        payload = _mapping(data, "evidence_reference")
        _unknown(
            payload,
            frozenset({"tool_call_id", "tool_name", "as_of_epoch", "as_of_utc", "limitation_codes"}),
            "unexpected_evidence_field",
        )
        return cls(
            tool_call_id=_required(payload, "tool_call_id"),
            tool_name=_required(payload, "tool_name"),
            as_of_epoch=_required(payload, "as_of_epoch"),
            as_of_utc=_required(payload, "as_of_utc"),
            limitation_codes=tuple(_sequence(payload.get("limitation_codes", ()), "limitation_codes")),
        )


@dataclass(frozen=True)
class LiveOpsClaimDiagnostic:
    """Non-verified submission summary. It is not a fact."""

    claim_kind: LiveOpsPublicClaimKind
    verdict: LiveOpsPublicClaimVerdict
    codes: tuple[str, ...]
    tool_call_ids: tuple[str, ...] = ()
    limitation_codes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.claim_kind, LiveOpsPublicClaimKind):
            raise ContractValidationError("invalid_claim_kind")
        if self.verdict not in _DIAGNOSTIC_VERDICTS:
            raise ContractValidationError("invalid_verdict")
        if isinstance(self.codes, list):
            object.__setattr__(self, "codes", tuple(self.codes))
        if isinstance(self.tool_call_ids, list):
            object.__setattr__(self, "tool_call_ids", tuple(self.tool_call_ids))
        if isinstance(self.limitation_codes, list):
            object.__setattr__(self, "limitation_codes", tuple(self.limitation_codes))
        object.__setattr__(self, "codes", _code_tuple(self.codes, "codes", allow_empty=False))
        object.__setattr__(self, "tool_call_ids", _id_tuple(self.tool_call_ids, "tool_call_ids"))
        object.__setattr__(self, "limitation_codes", _code_tuple(self.limitation_codes, "limitation_codes"))

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "claim_kind": self.claim_kind.value,
            "verdict": self.verdict.value,
            "codes": list(self.codes),
            "tool_call_ids": list(self.tool_call_ids),
            "limitation_codes": list(self.limitation_codes),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "LiveOpsClaimDiagnostic":
        payload = _mapping(data, "diagnostic")
        _unknown(
            payload,
            frozenset({"claim_kind", "verdict", "codes", "tool_call_ids", "limitation_codes"}),
            "unexpected_diagnostic_field",
        )
        return cls(
            claim_kind=_enum(LiveOpsPublicClaimKind, _required(payload, "claim_kind"), "claim_kind"),
            verdict=_enum(LiveOpsPublicClaimVerdict, _required(payload, "verdict"), "verdict"),
            codes=tuple(_sequence(_required(payload, "codes"), "codes")),
            tool_call_ids=tuple(_sequence(payload.get("tool_call_ids", ()), "tool_call_ids")),
            limitation_codes=tuple(_sequence(payload.get("limitation_codes", ()), "limitation_codes")),
        )


@dataclass(frozen=True)
class LiveOpsContradictionRecord:
    subject: LiveOpsPublicContradictionSubject
    identity: tuple[tuple[str, str], ...]
    tool_call_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.subject, LiveOpsPublicContradictionSubject):
            raise ContractValidationError("invalid_contradiction_subject")
        if isinstance(self.identity, list):
            object.__setattr__(self, "identity", tuple(tuple(item) if isinstance(item, list) else item for item in self.identity))
        if isinstance(self.tool_call_ids, list):
            object.__setattr__(self, "tool_call_ids", tuple(self.tool_call_ids))
        if not isinstance(self.identity, tuple) or len(self.identity) != 1:
            raise ContractValidationError("invalid_contradiction_identity")
        pair = self.identity[0]
        if not isinstance(pair, tuple) or len(pair) != 2:
            raise ContractValidationError("invalid_contradiction_identity")
        key, value = pair
        _token(key, "identity_key")
        _token(value, "identity_value")
        expected = {
            LiveOpsPublicContradictionSubject.HAZARD_SOURCE_VERSION: "hazard_id",
            LiveOpsPublicContradictionSubject.AIRCRAFT_CURRENTNESS: "aircraft_id",
            LiveOpsPublicContradictionSubject.ENCOUNTER_LINEAGE: "encounter_id",
        }[self.subject]
        if key != expected:
            raise ContractValidationError("invalid_contradiction_identity")
        object.__setattr__(self, "tool_call_ids", _id_tuple(self.tool_call_ids, "tool_call_ids", allow_empty=False))

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "subject": self.subject.value,
            "identity": [list(item) for item in self.identity],
            "tool_call_ids": list(self.tool_call_ids),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "LiveOpsContradictionRecord":
        payload = _mapping(data, "contradiction_subject")
        _unknown(payload, frozenset({"subject", "identity", "tool_call_ids"}), "unexpected_contradiction_field")
        identity = tuple(
            tuple(item) if isinstance(item, list) else item
            for item in _sequence(_required(payload, "identity"), "identity")
        )
        return cls(
            subject=_enum(LiveOpsPublicContradictionSubject, _required(payload, "subject"), "subject"),
            identity=identity,
            tool_call_ids=tuple(_sequence(_required(payload, "tool_call_ids"), "tool_call_ids")),
        )


@dataclass(frozen=True)
class LiveOpsDiscovery:
    """One executed callsign search. Identities are not merged across entries.

    ``reference_epoch`` is the batch observation clock for that search.
    It is not an expiry and it is not a source-freshness certificate.
    """

    callsign: str
    status: LiveOpsDiscoveryStatus
    aircraft_ids: tuple[str, ...]
    evidence_tool_call_id: str
    reference_epoch: int
    identity_resolved: bool
    selection_authorized: bool = False

    def __post_init__(self) -> None:
        _token(self.callsign, "callsign")
        if not isinstance(self.status, LiveOpsDiscoveryStatus):
            raise ContractValidationError("invalid_discovery_status")
        if isinstance(self.aircraft_ids, list):
            object.__setattr__(self, "aircraft_ids", tuple(self.aircraft_ids))
        object.__setattr__(self, "aircraft_ids", _id_tuple(self.aircraft_ids, "aircraft_ids"))
        _token(self.evidence_tool_call_id, "evidence_tool_call_id")
        _strict_int(self.reference_epoch, "reference_epoch")
        _bool(self.identity_resolved, "identity_resolved")
        _bool(self.selection_authorized, "selection_authorized")
        if self.selection_authorized:
            raise ContractValidationError("selection_not_authorized")
        count = len(self.aircraft_ids)
        if self.status is LiveOpsDiscoveryStatus.UNIQUE_SUPPORTED_MATCH:
            if count != 1 or not self.identity_resolved:
                raise ContractValidationError("invalid_unique_discovery")
        elif self.status is LiveOpsDiscoveryStatus.MULTIPLE_SUPPORTED_MATCHES:
            if count < 2 or self.identity_resolved:
                raise ContractValidationError("invalid_multiple_discovery")
        elif count or self.identity_resolved:
            raise ContractValidationError("invalid_unresolved_discovery")

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "callsign": self.callsign,
            "status": self.status.value,
            "aircraft_ids": list(self.aircraft_ids),
            "evidence_tool_call_id": self.evidence_tool_call_id,
            "reference_epoch": self.reference_epoch,
            "identity_resolved": self.identity_resolved,
            "selection_authorized": False,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "LiveOpsDiscovery":
        payload = _mapping(data, "discovery")
        _unknown(
            payload,
            frozenset(
                {
                    "callsign",
                    "status",
                    "aircraft_ids",
                    "evidence_tool_call_id",
                    "reference_epoch",
                    "identity_resolved",
                    "selection_authorized",
                }
            ),
            "unexpected_discovery_field",
        )
        return cls(
            callsign=_required(payload, "callsign"),
            status=_enum(LiveOpsDiscoveryStatus, _required(payload, "status"), "status"),
            aircraft_ids=tuple(_sequence(_required(payload, "aircraft_ids"), "aircraft_ids")),
            evidence_tool_call_id=_required(payload, "evidence_tool_call_id"),
            reference_epoch=_required(payload, "reference_epoch"),
            identity_resolved=_required(payload, "identity_resolved"),
            selection_authorized=_required(payload, "selection_authorized"),
        )


@dataclass(frozen=True)
class LiveOpsEntityRef:
    """Output identity for one unique callsign match.

    ``reference_epoch`` records the observation batch clock. It does not
    expire the identity and it does not certify source freshness.
    """

    entity_kind: str
    entity_id: str
    callsign: str
    authority: str
    source_specialist: str
    public_schema_version: str
    correlation_id: str
    reference_epoch: int
    reference_utc: str
    evidence_tool_call_id: str

    def __post_init__(self) -> None:
        if self.entity_kind != "AIRCRAFT":
            raise ContractValidationError("invalid_entity_kind")
        _token(self.entity_id, "entity_id")
        _token(self.callsign, "callsign")
        if self.authority != LIVE_OPS_ENTITY_AUTHORITY:
            raise ContractValidationError("invalid_entity_authority")
        if self.source_specialist != LIVE_OPS_PUBLIC_SPECIALIST:
            raise ContractValidationError("invalid_source_specialist")
        if self.public_schema_version != LIVE_OPS_PUBLIC_SCHEMA_VERSION:
            raise ContractValidationError("invalid_schema_version")
        _token(self.correlation_id, "correlation_id")
        _strict_int(self.reference_epoch, "reference_epoch")
        if self.reference_utc != live_ops_reference_utc(self.reference_epoch):
            raise ContractValidationError("invalid_reference_utc")
        _token(self.evidence_tool_call_id, "evidence_tool_call_id")

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "entity_kind": self.entity_kind,
            "entity_id": self.entity_id,
            "callsign": self.callsign,
            "authority": self.authority,
            "source_specialist": self.source_specialist,
            "public_schema_version": self.public_schema_version,
            "correlation_id": self.correlation_id,
            "reference_epoch": self.reference_epoch,
            "reference_utc": self.reference_utc,
            "evidence_tool_call_id": self.evidence_tool_call_id,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "LiveOpsEntityRef":
        payload = _mapping(data, "entity_ref")
        _unknown(
            payload,
            frozenset(
                {
                    "entity_kind",
                    "entity_id",
                    "callsign",
                    "authority",
                    "source_specialist",
                    "public_schema_version",
                    "correlation_id",
                    "reference_epoch",
                    "reference_utc",
                    "evidence_tool_call_id",
                }
            ),
            "unexpected_entity_ref_field",
        )
        return cls(
            entity_kind=_required(payload, "entity_kind"),
            entity_id=_required(payload, "entity_id"),
            callsign=_required(payload, "callsign"),
            authority=_required(payload, "authority"),
            source_specialist=_required(payload, "source_specialist"),
            public_schema_version=_required(payload, "public_schema_version"),
            correlation_id=_required(payload, "correlation_id"),
            reference_epoch=_required(payload, "reference_epoch"),
            reference_utc=_required(payload, "reference_utc"),
            evidence_tool_call_id=_required(payload, "evidence_tool_call_id"),
        )


def entity_hint_from_result(result: "LiveOpsRunResult", index: int) -> LiveOpsEntityHint:
    """Copy a result ref down to an untrusted hint. Authority is not copied."""

    if not isinstance(result, LiveOpsRunResult) or type(index) is not int:
        raise ContractValidationError("invalid_entity_hint")
    if index < 0 or index >= len(result.verified_entity_refs):
        raise ContractValidationError("invalid_entity_ref_index")
    ref = result.verified_entity_refs[index]
    return LiveOpsEntityHint(entity_kind="AIRCRAFT", entity_id=ref.entity_id, callsign=ref.callsign)


@dataclass(frozen=True)
class LiveOpsRunFailure:
    """Detail for a non-completed run. It does not repeat execution_status."""

    reason: LiveOpsFailureReason
    failed_tool_name: str | None = None
    validation_code: LiveOpsPublicValidationCode | None = None
    refusal_code: str | None = None
    unsupported_reason: LiveOpsPublicUnsupportedReason | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.reason, LiveOpsFailureReason):
            raise ContractValidationError("invalid_failure_reason")
        if self.failed_tool_name is not None:
            _tool_name(self.failed_tool_name, _TOOL_NAMES)
        if self.validation_code is not None and not isinstance(self.validation_code, LiveOpsPublicValidationCode):
            raise ContractValidationError("invalid_validation_code")
        if self.refusal_code is not None:
            _token(self.refusal_code, "refusal_code")
        if self.unsupported_reason is not None and not isinstance(
            self.unsupported_reason, LiveOpsPublicUnsupportedReason
        ):
            raise ContractValidationError("invalid_unsupported_reason")
        reason = self.reason
        if reason is LiveOpsFailureReason.UNSUPPORTED:
            if self.unsupported_reason is None:
                raise ContractValidationError("missing_unsupported_reason")
            _clear(self, ("failed_tool_name", "validation_code", "refusal_code"))
        elif reason is LiveOpsFailureReason.MODEL_REFUSAL:
            _clear(self, ("failed_tool_name", "validation_code", "unsupported_reason"))
        elif reason is LiveOpsFailureReason.TOOL_CALL_VALIDATION_EXHAUSTED:
            if self.validation_code is None:
                raise ContractValidationError("missing_validation_code")
            _clear(self, ("failed_tool_name", "refusal_code", "unsupported_reason"))
        elif reason is LiveOpsFailureReason.TOOL_EXECUTION_FAILED:
            _clear(self, ("validation_code", "refusal_code", "unsupported_reason"))
        elif reason is LiveOpsFailureReason.MALFORMED_PUBLIC_REQUEST:
            _clear(self, ("failed_tool_name", "validation_code", "refusal_code", "unsupported_reason"))
        else:
            _clear(self, ("failed_tool_name", "validation_code", "refusal_code", "unsupported_reason"))

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "reason": self.reason.value,
            "failed_tool_name": self.failed_tool_name,
            "validation_code": None if self.validation_code is None else self.validation_code.value,
            "refusal_code": self.refusal_code,
            "unsupported_reason": None if self.unsupported_reason is None else self.unsupported_reason.value,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "LiveOpsRunFailure":
        payload = _mapping(data, "failure")
        _unknown(
            payload,
            frozenset({"reason", "failed_tool_name", "validation_code", "refusal_code", "unsupported_reason"}),
            "unexpected_failure_field",
        )
        validation = payload.get("validation_code")
        unsupported = payload.get("unsupported_reason")
        return cls(
            reason=_enum(LiveOpsFailureReason, _required(payload, "reason"), "reason"),
            failed_tool_name=_optional_token(payload, "failed_tool_name"),
            validation_code=None if validation is None else _enum(LiveOpsPublicValidationCode, validation, "validation_code"),
            refusal_code=_optional_token(payload, "refusal_code"),
            unsupported_reason=None if unsupported is None else _enum(LiveOpsPublicUnsupportedReason, unsupported, "unsupported_reason"),
        )


def _clear(owner: Any, names: tuple[str, ...]) -> None:
    for name in names:
        if getattr(owner, name) is not None:
            raise ContractValidationError("inapplicable_failure_field")


def _fixed_answer(status: LiveOpsRunStatus, outcome: LiveOpsPublicVerificationOutcome | None) -> str | None:
    if status is LiveOpsRunStatus.COMPLETED and outcome is LiveOpsPublicVerificationOutcome.NONE_VERIFIED:
        return LIVE_OPS_ANSWER_NONE_VERIFIED
    if status is LiveOpsRunStatus.COMPLETED:
        return None
    return {
        LiveOpsRunStatus.INVALID_REQUEST: LIVE_OPS_ANSWER_INVALID_REQUEST,
        LiveOpsRunStatus.UNSUPPORTED: LIVE_OPS_ANSWER_UNSUPPORTED,
        LiveOpsRunStatus.REFUSED: LIVE_OPS_ANSWER_REFUSED,
        LiveOpsRunStatus.TOOL_FAILED: LIVE_OPS_ANSWER_TOOL_FAILED,
        LiveOpsRunStatus.PROVIDER_FAILED: LIVE_OPS_ANSWER_PROVIDER,
        LiveOpsRunStatus.PROVIDER_TIMEOUT: LIVE_OPS_ANSWER_PROVIDER,
        LiveOpsRunStatus.RUNTIME_TIMEOUT: LIVE_OPS_ANSWER_STOPPED,
        LiveOpsRunStatus.BUDGET_EXHAUSTED: LIVE_OPS_ANSWER_STOPPED,
        LiveOpsRunStatus.UNAVAILABLE: LIVE_OPS_ANSWER_UNAVAILABLE,
        LiveOpsRunStatus.CANCELLED: LIVE_OPS_ANSWER_CANCELLED,
    }[status]


@dataclass(frozen=True)
class LiveOpsRunResult:
    """Public specialist result.

    Constructing a valid instance does not prove that LO2 verified the
    claims. Deserializing model-authored JSON does not create authority.
    """

    correlation_id: str
    execution_status: LiveOpsRunStatus
    verification_outcome: LiveOpsPublicVerificationOutcome | None
    rendering_posture: LiveOpsPublicRenderingPosture | None
    answer_origin: LiveOpsAnswerOrigin
    answer: str
    reference_epoch: int
    reference_utc: str
    verified_claims: tuple[LiveOpsPublicClaim, ...] = ()
    evidence_references: tuple[LiveOpsEvidenceReference, ...] = ()
    diagnostics: tuple[LiveOpsClaimDiagnostic, ...] = ()
    structural_codes: tuple[str, ...] = ()
    contradiction_subjects: tuple[LiveOpsContradictionRecord, ...] = ()
    mandatory_limitations: tuple[str, ...] = ()
    discoveries: tuple[LiveOpsDiscovery, ...] = ()
    verified_entity_refs: tuple[LiveOpsEntityRef, ...] = ()
    failure: LiveOpsRunFailure | None = None
    request_id: str | None = None
    conversation_id: str | None = None
    completeness_notice: str = LIVE_OPS_COMPLETENESS_NOTICE
    schema_version: str = LIVE_OPS_PUBLIC_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != LIVE_OPS_PUBLIC_SCHEMA_VERSION:
            raise ContractValidationError("invalid_schema_version")
        if self.completeness_notice != LIVE_OPS_COMPLETENESS_NOTICE:
            raise ContractValidationError("invalid_completeness_notice")
        _token(self.correlation_id, "correlation_id")
        if self.request_id is not None:
            _token(self.request_id, "request_id")
        if self.conversation_id is not None:
            _token(self.conversation_id, "conversation_id")
        if not isinstance(self.execution_status, LiveOpsRunStatus):
            raise ContractValidationError("invalid_execution_status")
        if self.verification_outcome is not None and not isinstance(
            self.verification_outcome, LiveOpsPublicVerificationOutcome
        ):
            raise ContractValidationError("invalid_verification_outcome")
        if self.rendering_posture is not None and not isinstance(
            self.rendering_posture, LiveOpsPublicRenderingPosture
        ):
            raise ContractValidationError("invalid_rendering_posture")
        if not isinstance(self.answer_origin, LiveOpsAnswerOrigin):
            raise ContractValidationError("invalid_answer_origin")
        _strict_int(self.reference_epoch, "reference_epoch")
        if self.reference_utc != live_ops_reference_utc(self.reference_epoch):
            raise ContractValidationError("invalid_reference_utc")
        _freeze_result_collections(self)
        _validate_result_relations(self)

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "schema_version": self.schema_version,
            "request_id": self.request_id,
            "conversation_id": self.conversation_id,
            "correlation_id": self.correlation_id,
            "execution_status": self.execution_status.value,
            "verification_outcome": None if self.verification_outcome is None else self.verification_outcome.value,
            "rendering_posture": None if self.rendering_posture is None else self.rendering_posture.value,
            "answer_origin": self.answer_origin.value,
            "answer": self.answer,
            "reference_epoch": self.reference_epoch,
            "reference_utc": self.reference_utc,
            "completeness_notice": self.completeness_notice,
            "verified_claims": [item.to_dict() for item in self.verified_claims],
            "evidence_references": [item.to_dict() for item in self.evidence_references],
            "diagnostics": [item.to_dict() for item in self.diagnostics],
            "structural_codes": list(self.structural_codes),
            "contradiction_subjects": [item.to_dict() for item in self.contradiction_subjects],
            "mandatory_limitations": list(self.mandatory_limitations),
            "discoveries": [item.to_dict() for item in self.discoveries],
            "verified_entity_refs": [item.to_dict() for item in self.verified_entity_refs],
            "failure": None if self.failure is None else self.failure.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "LiveOpsRunResult":
        payload = _mapping(data, "live_ops_run_result")
        _unknown(payload, _RESULT_KEYS, "unexpected_result_field")
        _schema(payload)
        outcome = payload["verification_outcome"] if "verification_outcome" in payload else _missing("verification_outcome")
        posture = payload["rendering_posture"] if "rendering_posture" in payload else _missing("rendering_posture")
        failure = payload["failure"] if "failure" in payload else _missing("failure")
        return cls(
            schema_version=payload["schema_version"],
            request_id=_optional_token(payload, "request_id"),
            conversation_id=_optional_token(payload, "conversation_id"),
            correlation_id=_required(payload, "correlation_id"),
            execution_status=_enum(LiveOpsRunStatus, _required(payload, "execution_status"), "execution_status"),
            verification_outcome=None if outcome is None else _enum(LiveOpsPublicVerificationOutcome, outcome, "verification_outcome"),
            rendering_posture=None if posture is None else _enum(LiveOpsPublicRenderingPosture, posture, "rendering_posture"),
            answer_origin=_enum(LiveOpsAnswerOrigin, _required(payload, "answer_origin"), "answer_origin"),
            answer=_required(payload, "answer"),
            reference_epoch=_required(payload, "reference_epoch"),
            reference_utc=_required(payload, "reference_utc"),
            completeness_notice=_required(payload, "completeness_notice"),
            verified_claims=tuple(LiveOpsPublicClaim.from_dict(item) for item in _sequence(_required(payload, "verified_claims"), "verified_claims")),
            evidence_references=tuple(LiveOpsEvidenceReference.from_dict(item) for item in _sequence(_required(payload, "evidence_references"), "evidence_references")),
            diagnostics=tuple(LiveOpsClaimDiagnostic.from_dict(item) for item in _sequence(_required(payload, "diagnostics"), "diagnostics")),
            structural_codes=tuple(_sequence(_required(payload, "structural_codes"), "structural_codes")),
            contradiction_subjects=tuple(LiveOpsContradictionRecord.from_dict(item) for item in _sequence(_required(payload, "contradiction_subjects"), "contradiction_subjects")),
            mandatory_limitations=tuple(_sequence(_required(payload, "mandatory_limitations"), "mandatory_limitations")),
            discoveries=tuple(LiveOpsDiscovery.from_dict(item) for item in _sequence(_required(payload, "discoveries"), "discoveries")),
            verified_entity_refs=tuple(LiveOpsEntityRef.from_dict(item) for item in _sequence(_required(payload, "verified_entity_refs"), "verified_entity_refs")),
            failure=None if failure is None else LiveOpsRunFailure.from_dict(failure),
        )

    @classmethod
    def rejected_payload(
        cls,
        context: LiveOpsTrustedContext,
        *,
        request_id: str | None = None,
        conversation_id: str | None = None,
    ) -> "LiveOpsRunResult":
        """Map a parse failure to a typed result. The context must already be valid."""

        if not isinstance(context, LiveOpsTrustedContext):
            raise TypeError("context must be a LiveOpsTrustedContext")
        return cls(
            request_id=request_id,
            conversation_id=conversation_id,
            correlation_id=context.correlation_id,
            execution_status=LiveOpsRunStatus.INVALID_REQUEST,
            verification_outcome=None,
            rendering_posture=None,
            answer_origin=LiveOpsAnswerOrigin.FIXED_SAFE_TEXT,
            answer=LIVE_OPS_ANSWER_INVALID_REQUEST,
            reference_epoch=context.now_epoch,
            reference_utc=live_ops_reference_utc(context.now_epoch),
            failure=LiveOpsRunFailure(reason=LiveOpsFailureReason.MALFORMED_PUBLIC_REQUEST),
        )


def _missing(field_name: str) -> Any:
    raise ContractValidationError(f"missing_{field_name}")


def _freeze_result_collections(result: LiveOpsRunResult) -> None:
    pairs = (
        ("verified_claims", LiveOpsPublicClaim),
        ("evidence_references", LiveOpsEvidenceReference),
        ("diagnostics", LiveOpsClaimDiagnostic),
        ("contradiction_subjects", LiveOpsContradictionRecord),
        ("discoveries", LiveOpsDiscovery),
        ("verified_entity_refs", LiveOpsEntityRef),
    )
    for name, item_type in pairs:
        value = getattr(result, name)
        if isinstance(value, list):
            value = tuple(value)
            object.__setattr__(result, name, value)
        if not isinstance(value, tuple) or any(not isinstance(item, item_type) for item in value):
            raise ContractValidationError(f"invalid_{name}")
    for name in ("structural_codes", "mandatory_limitations"):
        value = getattr(result, name)
        if isinstance(value, list):
            object.__setattr__(result, name, tuple(value))
    object.__setattr__(result, "structural_codes", _code_tuple(result.structural_codes, "structural_codes"))
    object.__setattr__(
        result,
        "mandatory_limitations",
        _code_tuple(result.mandatory_limitations, "mandatory_limitations"),
    )
    if len(result.verified_claims) > LIVE_OPS_MAX_CLAIMS:
        raise ContractValidationError("too_many_verified_claims")
    if len(result.diagnostics) > LIVE_OPS_MAX_DIAGNOSTICS:
        raise ContractValidationError("too_many_diagnostics")
    if len(result.evidence_references) > LIVE_OPS_MAX_EVIDENCE_REFERENCES:
        raise ContractValidationError("too_many_evidence_references")
    if len(result.discoveries) > LIVE_OPS_MAX_TOOL_CALLS:
        raise ContractValidationError("too_many_discoveries")


def _validate_result_relations(result: LiveOpsRunResult) -> None:
    status = result.execution_status
    outcome = result.verification_outcome
    expected_posture = None if outcome is None else LiveOpsPublicRenderingPosture(_POSTURE_BY_OUTCOME[outcome.value])
    if result.rendering_posture is not expected_posture:
        raise ContractValidationError("rendering_posture_mismatch")
    if status is LiveOpsRunStatus.COMPLETED:
        if result.failure is not None:
            raise ContractValidationError("completed_forbids_failure")
        if outcome not in _COMPLETED_OUTCOMES:
            raise ContractValidationError("completed_requires_verification_outcome")
    else:
        if result.failure is None:
            raise ContractValidationError("failure_required")
        if result.failure.reason not in _STATUS_REASONS[status]:
            raise ContractValidationError("failure_reason_mismatch")
        _validate_failure_outcome(result)
    expected = _fixed_answer(status, outcome)
    if expected is None:
        if result.answer_origin is not LiveOpsAnswerOrigin.DETERMINISTIC_RENDERER:
            raise ContractValidationError("invalid_answer_origin")
        _token(result.answer, "answer", max_length=LIVE_OPS_PUBLIC_ANSWER_MAX_LENGTH)
    else:
        if result.answer_origin is not LiveOpsAnswerOrigin.FIXED_SAFE_TEXT or result.answer != expected:
            raise ContractValidationError("invalid_fixed_answer")
    if outcome in (
        LiveOpsPublicVerificationOutcome.ALL_VERIFIED,
        LiveOpsPublicVerificationOutcome.PARTIALLY_VERIFIED,
    ):
        if not result.verified_claims:
            raise ContractValidationError("verified_outcome_requires_claims")
    elif result.verified_claims:
        raise ContractValidationError("unverified_run_forbids_claims")
    if outcome is LiveOpsPublicVerificationOutcome.NOT_AUTHORIZED and not result.structural_codes:
        raise ContractValidationError("not_authorized_requires_structural_codes")
    if outcome in _COMPLETED_OUTCOMES and result.structural_codes:
        raise ContractValidationError("completed_verification_forbids_structural_codes")
    if outcome not in (
        LiveOpsPublicVerificationOutcome.PARTIALLY_VERIFIED,
        LiveOpsPublicVerificationOutcome.NONE_VERIFIED,
    ) and result.diagnostics:
        raise ContractValidationError("diagnostics_require_partial_or_none")
    if outcome not in _COMPLETED_OUTCOMES and result.contradiction_subjects:
        raise ContractValidationError("contradictions_require_completed_verification")
    if status is not LiveOpsRunStatus.COMPLETED and (result.discoveries or result.verified_entity_refs):
        raise ContractValidationError("discovery_requires_completed_run")
    _validate_evidence(result)
    _validate_discoveries(result)


def _validate_failure_outcome(result: LiveOpsRunResult) -> None:
    outcome = result.verification_outcome
    reason = result.failure.reason if result.failure is not None else None
    ledger_denied = reason is LiveOpsFailureReason.LEDGER_NOT_AUTHORIZED
    tool_denied = (
        result.execution_status is LiveOpsRunStatus.TOOL_FAILED
        and outcome is LiveOpsPublicVerificationOutcome.NOT_AUTHORIZED
    )
    if ledger_denied or tool_denied:
        if outcome is not LiveOpsPublicVerificationOutcome.NOT_AUTHORIZED:
            raise ContractValidationError("ledger_denial_requires_not_authorized")
        if tool_denied and "RUN_EXECUTION_FAILED" not in result.structural_codes:
            raise ContractValidationError("tool_failure_requires_run_execution_failed")
        if ledger_denied and "RUN_EXECUTION_FAILED" in result.structural_codes:
            raise ContractValidationError("tool_failure_must_use_tool_failed_status")
        return
    if outcome is not None or result.structural_codes:
        raise ContractValidationError("pre_verification_failure_forbids_outcome")


def _validate_evidence(result: LiveOpsRunResult) -> None:
    ids = [item.tool_call_id for item in result.evidence_references]
    if len(ids) != len(set(ids)):
        raise ContractValidationError("duplicate_evidence_reference")
    by_id = {item.tool_call_id: item for item in result.evidence_references}
    cited: set[str] = set()
    for claim in result.verified_claims:
        for tool_call_id in claim.cited_tool_call_ids():
            cited.add(tool_call_id)
            ref = by_id.get(tool_call_id)
            if ref is None:
                raise ContractValidationError("missing_evidence_reference")
            if claim.tool_name is not None and ref.tool_name != claim.tool_name:
                raise ContractValidationError("evidence_tool_name_mismatch")
            if ref.as_of_epoch != result.reference_epoch or ref.as_of_utc != result.reference_utc:
                raise ContractValidationError("evidence_clock_mismatch")
    for ref in result.evidence_references:
        if ref.as_of_epoch != result.reference_epoch or ref.as_of_utc != result.reference_utc:
            raise ContractValidationError("evidence_clock_mismatch")
    discovery_ids = {item.evidence_tool_call_id for item in result.discoveries}
    if set(ids) != cited | discovery_ids:
        raise ContractValidationError("evidence_reference_set_mismatch")


def _validate_discoveries(result: LiveOpsRunResult) -> None:
    seen: set[str] = set()
    unique_by_call: dict[str, LiveOpsDiscovery] = {}
    for item in result.discoveries:
        if item.evidence_tool_call_id in seen:
            raise ContractValidationError("duplicate_discovery_tool_call")
        seen.add(item.evidence_tool_call_id)
        if item.reference_epoch != result.reference_epoch:
            raise ContractValidationError("discovery_clock_mismatch")
        ref = next(
            (entry for entry in result.evidence_references if entry.tool_call_id == item.evidence_tool_call_id),
            None,
        )
        if ref is None or ref.tool_name != "find_aircraft_by_callsign":
            raise ContractValidationError("discovery_evidence_mismatch")
        matches = [
            claim
            for claim in result.verified_claims
            if claim.kind is LiveOpsPublicClaimKind.CALLSIGN_MATCH_SET
            and claim.tool_call_id == item.evidence_tool_call_id
        ]
        zeros = [
            claim
            for claim in result.verified_claims
            if claim.kind is LiveOpsPublicClaimKind.QUERY_RESULT_ZERO
            and claim.tool_call_id == item.evidence_tool_call_id
            and claim.metric is LiveOpsPublicCountMetric.CALLSIGN_MATCH_COUNT
        ]
        if item.status is LiveOpsDiscoveryStatus.UNIQUE_SUPPORTED_MATCH:
            unique_by_call[item.evidence_tool_call_id] = item
            if len(matches) != 1 or matches[0].callsign != item.callsign or matches[0].aircraft_ids != item.aircraft_ids:
                raise ContractValidationError("unique_discovery_claim_mismatch")
        elif item.status is LiveOpsDiscoveryStatus.MULTIPLE_SUPPORTED_MATCHES:
            if len(matches) != 1 or matches[0].callsign != item.callsign or matches[0].aircraft_ids != item.aircraft_ids:
                raise ContractValidationError("multiple_discovery_claim_mismatch")
        elif item.status is LiveOpsDiscoveryStatus.NO_SUPPORTED_MATCH:
            if matches or len(zeros) != 1 or zeros[0].scope is None or zeros[0].scope.callsign != item.callsign:
                raise ContractValidationError("zero_discovery_claim_mismatch")
        elif matches or zeros:
            raise ContractValidationError("incomplete_discovery_has_verified_claim")
    ref_keys = []
    for ref in result.verified_entity_refs:
        if ref.correlation_id != result.correlation_id or ref.reference_epoch != result.reference_epoch:
            raise ContractValidationError("entity_ref_result_mismatch")
        if ref.reference_utc != result.reference_utc:
            raise ContractValidationError("entity_ref_result_mismatch")
        discovery = unique_by_call.get(ref.evidence_tool_call_id)
        if discovery is None or discovery.callsign != ref.callsign or discovery.aircraft_ids != (ref.entity_id,):
            raise ContractValidationError("entity_ref_discovery_mismatch")
        ref_keys.append(ref.evidence_tool_call_id)
    if len(ref_keys) != len(set(ref_keys)):
        raise ContractValidationError("duplicate_entity_ref")


@dataclass(frozen=True)
class LiveOpsPresentedSection:
    """Live Ops section copied from one trusted result.

    This is not a Master-wide response envelope.
    """

    correlation_id: str
    execution_status: LiveOpsRunStatus
    verification_outcome: LiveOpsPublicVerificationOutcome | None
    factual_text: str
    mandatory_limitations: tuple[str, ...]
    completeness_notice: str
    verified_claims: tuple[LiveOpsPublicClaim, ...]
    specialist: str = LIVE_OPS_PUBLIC_SPECIALIST
    schema_version: str = LIVE_OPS_PUBLIC_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.specialist != LIVE_OPS_PUBLIC_SPECIALIST:
            raise ContractValidationError("invalid_specialist")
        if self.schema_version != LIVE_OPS_PUBLIC_SCHEMA_VERSION:
            raise ContractValidationError("invalid_schema_version")
        _token(self.correlation_id, "correlation_id")
        if not isinstance(self.execution_status, LiveOpsRunStatus):
            raise ContractValidationError("invalid_execution_status")
        if self.verification_outcome is not None and not isinstance(
            self.verification_outcome, LiveOpsPublicVerificationOutcome
        ):
            raise ContractValidationError("invalid_verification_outcome")
        if not isinstance(self.factual_text, str) or not self.factual_text.strip():
            raise ContractValidationError("invalid_factual_text")
        if isinstance(self.mandatory_limitations, list):
            object.__setattr__(self, "mandatory_limitations", tuple(self.mandatory_limitations))
        object.__setattr__(
            self,
            "mandatory_limitations",
            _code_tuple(self.mandatory_limitations, "mandatory_limitations"),
        )
        if self.completeness_notice != LIVE_OPS_COMPLETENESS_NOTICE:
            raise ContractValidationError("invalid_completeness_notice")
        if isinstance(self.verified_claims, list):
            object.__setattr__(self, "verified_claims", tuple(self.verified_claims))
        if not isinstance(self.verified_claims, tuple) or any(
            not isinstance(item, LiveOpsPublicClaim) for item in self.verified_claims
        ):
            raise ContractValidationError("invalid_verified_claims")


def present_live_ops_result(result: LiveOpsRunResult) -> LiveOpsPresentedSection:
    """Copy factual text, limitations, and claims from a trusted result."""

    if not isinstance(result, LiveOpsRunResult):
        raise TypeError("result must be a LiveOpsRunResult")
    return LiveOpsPresentedSection(
        correlation_id=result.correlation_id,
        execution_status=result.execution_status,
        verification_outcome=result.verification_outcome,
        factual_text=result.answer,
        mandatory_limitations=result.mandatory_limitations,
        completeness_notice=result.completeness_notice,
        verified_claims=result.verified_claims,
    )


def validate_live_ops_presentation(
    result: LiveOpsRunResult,
    section: LiveOpsPresentedSection,
) -> None:
    """Require the section to match the trusted result exactly.

    This comparison does not establish the truth of any narrative that is
    not the specialist answer. Master-wide synthesis contracts are not
    defined here.
    """

    if not isinstance(result, LiveOpsRunResult) or not isinstance(section, LiveOpsPresentedSection):
        raise TypeError("presentation validation requires the trusted result and section")
    if section.factual_text != result.answer:
        raise ContractValidationError("factual_text_mismatch")
    if section.mandatory_limitations != result.mandatory_limitations:
        raise ContractValidationError("mandatory_limitations_mismatch")
    if section.verified_claims != result.verified_claims:
        raise ContractValidationError("verified_claims_mismatch")
    if section.completeness_notice != result.completeness_notice:
        raise ContractValidationError("completeness_notice_mismatch")
    if section.correlation_id != result.correlation_id:
        raise ContractValidationError("correlation_mismatch")
    if section.execution_status is not result.execution_status:
        raise ContractValidationError("execution_status_mismatch")
    if section.verification_outcome is not result.verification_outcome:
        raise ContractValidationError("verification_outcome_mismatch")


@runtime_checkable
class LiveOpsSpecialistPort(Protocol):
    def run(
        self,
        request: LiveOpsRunRequest,
        context: LiveOpsTrustedContext,
    ) -> LiveOpsRunResult: ...


__all__ = [
    "LIVE_OPS_ANSWER_CANCELLED",
    "LIVE_OPS_ANSWER_INVALID_REQUEST",
    "LIVE_OPS_ANSWER_NONE_VERIFIED",
    "LIVE_OPS_ANSWER_PROVIDER",
    "LIVE_OPS_ANSWER_REFUSED",
    "LIVE_OPS_ANSWER_STOPPED",
    "LIVE_OPS_ANSWER_TOOL_FAILED",
    "LIVE_OPS_ANSWER_UNAVAILABLE",
    "LIVE_OPS_ANSWER_UNSUPPORTED",
    "LIVE_OPS_COMPLETENESS_NOTICE",
    "LIVE_OPS_ENTITY_AUTHORITY",
    "LIVE_OPS_PUBLIC_SCHEMA_VERSION",
    "LiveOpsAnswerOrigin",
    "LiveOpsClaimDiagnostic",
    "LiveOpsContradictionRecord",
    "LiveOpsDiscovery",
    "LiveOpsDiscoveryStatus",
    "LiveOpsEntityHint",
    "LiveOpsEntityRef",
    "LiveOpsEvidenceReference",
    "LiveOpsFailureReason",
    "LiveOpsPresentedSection",
    "LiveOpsPublicAdvisoryMode",
    "LiveOpsPublicClaim",
    "LiveOpsPublicClaimKind",
    "LiveOpsPublicClaimVerdict",
    "LiveOpsPublicContradictionSubject",
    "LiveOpsPublicCountMetric",
    "LiveOpsPublicCountMode",
    "LiveOpsPublicCurrentnessPredicate",
    "LiveOpsPublicFlag",
    "LiveOpsPublicLimitationCode",
    "LiveOpsPublicObservedField",
    "LiveOpsPublicOverlap",
    "LiveOpsPublicOverlapField",
    "LiveOpsPublicProjectionField",
    "LiveOpsPublicRecordedFlag",
    "LiveOpsPublicRenderingPosture",
    "LiveOpsPublicRiskMode",
    "LiveOpsPublicScope",
    "LiveOpsPublicUnsupportedReason",
    "LiveOpsPublicValidationCode",
    "LiveOpsPublicVerificationOutcome",
    "LiveOpsRunFailure",
    "LiveOpsRunRequest",
    "LiveOpsRunResult",
    "LiveOpsRunStatus",
    "LiveOpsSelectorHints",
    "LiveOpsSpecialistPort",
    "LiveOpsTrustedContext",
    "LiveOpsUntrustedTurn",
    "entity_hint_from_result",
    "live_ops_reference_utc",
    "present_live_ops_result",
    "validate_live_ops_presentation",
]
