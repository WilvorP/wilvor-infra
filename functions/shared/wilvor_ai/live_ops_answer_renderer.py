"""Deterministic Live Ops answer renderer.

Factual sentences come only from a ``LiveOpsEvidenceExport``. This module
does not verify claims, read raw tool results, call a model, or construct a
``LiveOpsRunResult``.

``LiveOpsRenderedAnswer.answer`` is plain text. Dashboard and Master
surfaces must display it as plain text and must not interpret it as
Markdown, HTML, or another markup language. Identifier escaping below keeps
verified tokens as data; it is not an HTML sanitizer.
"""

from __future__ import annotations

import math
import unicodedata
from dataclasses import dataclass
from enum import Enum

from wilvor_ai.live_ops_public_contracts import (
    LIVE_OPS_PUBLIC_ANSWER_MAX_LENGTH,
    LiveOpsPublicAdvisoryMode,
    LiveOpsPublicClaim,
    LiveOpsPublicClaimKind,
    LiveOpsPublicCountMetric,
    LiveOpsPublicCountMode,
    LiveOpsDiscovery,
    LiveOpsDiscoveryStatus,
    LiveOpsPublicCurrentnessPredicate,
    LiveOpsPublicLimitationCode,
    LiveOpsPublicObservedField,
    LiveOpsPublicOverlapField,
    LiveOpsPublicProjectionField,
    LiveOpsPublicRenderingPosture,
    LiveOpsPublicRiskMode,
    LiveOpsPublicScope,
    LiveOpsPublicVerificationOutcome,
)
from wilvor_ai.live_ops_public_export import (
    LIVE_OPS_EXPORT_SCHEMA_VERSION,
    LiveOpsEvidenceExport,
)


class LiveOpsRenderErrorCode(str, Enum):
    INVALID_EXPORT = "INVALID_EXPORT"
    NOT_RENDERABLE = "NOT_RENDERABLE"
    UNSUPPORTED_CLAIM = "UNSUPPORTED_CLAIM"
    UNSAFE_VALUE = "UNSAFE_VALUE"
    ANSWER_LENGTH_EXCEEDED = "ANSWER_LENGTH_EXCEEDED"
    TEMPLATE_FAILURE = "TEMPLATE_FAILURE"


class LiveOpsRenderError(Exception):
    """Closed render failure. The message is only the error code."""

    def __init__(self, code: LiveOpsRenderErrorCode) -> None:
        if not isinstance(code, LiveOpsRenderErrorCode):
            raise TypeError("invalid_render_error_code")
        self.code = code
        super().__init__(code.value)


@dataclass(frozen=True)
class LiveOpsRenderedSentence:
    """One deterministic sentence and the verified claims that support it.

    An empty ``claim_indexes`` tuple means the sentence comes from run
    metadata: the opening notice, a mandatory limitation, or an incomplete
    discovery. It is not a verified operational claim.
    """

    text: str
    claim_indexes: tuple[int, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.text, str) or not self.text or self.text != self.text.strip():
            raise LiveOpsRenderError(LiveOpsRenderErrorCode.TEMPLATE_FAILURE)
        if not isinstance(self.claim_indexes, tuple) or any(type(item) is not int or item < 0 for item in self.claim_indexes):
            raise LiveOpsRenderError(LiveOpsRenderErrorCode.TEMPLATE_FAILURE)


@dataclass(frozen=True)
class LiveOpsRenderedAnswer:
    """Rendered prose and the sentences that were joined to produce it."""

    answer: str
    sentences: tuple[LiveOpsRenderedSentence, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.sentences, tuple) or any(not isinstance(item, LiveOpsRenderedSentence) for item in self.sentences):
            raise LiveOpsRenderError(LiveOpsRenderErrorCode.TEMPLATE_FAILURE)
        if not self.sentences:
            raise LiveOpsRenderError(LiveOpsRenderErrorCode.TEMPLATE_FAILURE)
        expected = " ".join(item.text for item in self.sentences)
        if self.answer != expected or self.answer != self.answer.strip() or not self.answer:
            raise LiveOpsRenderError(LiveOpsRenderErrorCode.TEMPLATE_FAILURE)
        if len(self.answer) > LIVE_OPS_PUBLIC_ANSWER_MAX_LENGTH:
            raise LiveOpsRenderError(LiveOpsRenderErrorCode.ANSWER_LENGTH_EXCEEDED)


_OPENING_ALL = (
    "These statements repeat only the verified live operations claims. "
    "Verification of this set does not mean every relevant operational fact was investigated."
)
_OPENING_PARTIAL = (
    "These statements repeat only the verified subset of the submitted live operations claims. "
    "Unverified submissions are omitted and are not established facts. "
    "Verification of this subset does not mean every relevant operational fact was investigated."
)
_STORED_RELATIONSHIP = (
    "Each stored impact or encounter stated above is a stored record. "
    "None of them establishes physical entry into hazardous weather, severe turbulence, "
    "a current position inside the hazard, a change in operational risk, or a diversion."
)
_STORED_RISK = (
    "Where a stored risk record is stated above, that record is a persisted value, "
    "not a newly calculated risk assessment, and it does not establish that an aircraft "
    "is unsafe or that operational risk increased."
)
_STORED_RECOMMENDATIONS = (
    "Where stored recommendation ids are stated above, none of those records is selected. "
    "They are not a diversion, route, altitude, or ATC instruction."
)
_STORED_ALERTS = (
    "Where a stored alert is stated above, it is a record, not an operational or ATC instruction."
)
_POSTURE = {
    LiveOpsPublicVerificationOutcome.ALL_VERIFIED: LiveOpsPublicRenderingPosture.COMPLETE_VERIFIED_SET,
    LiveOpsPublicVerificationOutcome.PARTIALLY_VERIFIED: LiveOpsPublicRenderingPosture.INCOMPLETE_VERIFIED_SUBSET,
    LiveOpsPublicVerificationOutcome.NONE_VERIFIED: LiveOpsPublicRenderingPosture.NO_VERIFIED_FACTS,
    LiveOpsPublicVerificationOutcome.NOT_AUTHORIZED: LiveOpsPublicRenderingPosture.NOT_RENDERABLE,
}
_RENDERABLE = frozenset(
    {
        LiveOpsPublicVerificationOutcome.ALL_VERIFIED,
        LiveOpsPublicVerificationOutcome.PARTIALLY_VERIFIED,
    }
)
_ENCOUNTER_KINDS = frozenset(
    {
        LiveOpsPublicClaimKind.CONFIRMED_IMPACT,
        LiveOpsPublicClaimKind.CONFIRMED_ENCOUNTER,
        LiveOpsPublicClaimKind.RISK_STATE,
        LiveOpsPublicClaimKind.RECOMMENDATION_SET,
        LiveOpsPublicClaimKind.ALERT_RECORD,
    }
)
_CONTEXT_NOUN = {
    "get_aircraft_operational_context": "aircraft",
    "get_hazard_operational_context": "hazard",
    "get_airport_operational_context": "airport",
}
_METRIC_TEXT = {
    LiveOpsPublicCountMetric.IMPACT_RECORD_COUNT: "impact records",
    LiveOpsPublicCountMetric.ENCOUNTER_RECORD_COUNT: "encounter records",
    LiveOpsPublicCountMetric.UNIQUE_AIRCRAFT_COUNT: "unique aircraft",
    LiveOpsPublicCountMetric.UNIQUE_HAZARD_COUNT: "unique hazards",
    LiveOpsPublicCountMetric.HAZARD_QUERY_MATCH_COUNT: "hazard-query matches",
    LiveOpsPublicCountMetric.CALLSIGN_MATCH_COUNT: "callsign matches",
    LiveOpsPublicCountMetric.NETWORK_AIRCRAFT_COUNT: "observed-network aircraft",
    LiveOpsPublicCountMetric.NETWORK_HAZARD_COUNT: "observed-network hazards",
    LiveOpsPublicCountMetric.NETWORK_ENCOUNTER_COUNT: "observed-network encounters",
    LiveOpsPublicCountMetric.NETWORK_RISK_COUNT: "observed-network risk records",
    LiveOpsPublicCountMetric.NETWORK_RECOMMENDATION_COUNT: "observed-network recommendation records",
    LiveOpsPublicCountMetric.NETWORK_ALERT_COUNT: "observed-network alert records",
    LiveOpsPublicCountMetric.HAZARD_CONTEXT_IMPACT_COUNT: "hazard-context impact records",
    LiveOpsPublicCountMetric.AIRCRAFT_CONTEXT_ENCOUNTER_COUNT: "aircraft-context encounter records",
}
_NETWORK_METRICS = frozenset(
    {
        LiveOpsPublicCountMetric.NETWORK_AIRCRAFT_COUNT,
        LiveOpsPublicCountMetric.NETWORK_HAZARD_COUNT,
        LiveOpsPublicCountMetric.NETWORK_ENCOUNTER_COUNT,
        LiveOpsPublicCountMetric.NETWORK_RISK_COUNT,
        LiveOpsPublicCountMetric.NETWORK_RECOMMENDATION_COUNT,
        LiveOpsPublicCountMetric.NETWORK_ALERT_COUNT,
    }
)
_OBSERVED_UNITS = {
    LiveOpsPublicObservedField.LATITUDE: "degrees",
    LiveOpsPublicObservedField.LONGITUDE: "degrees",
    LiveOpsPublicObservedField.BARO_ALTITUDE_FT: "feet",
    LiveOpsPublicObservedField.GEO_ALTITUDE_FT: "feet",
    LiveOpsPublicObservedField.GROUND_SPEED_KT: "knots",
    LiveOpsPublicObservedField.TRACK_DEG: "degrees",
}
_OVERLAP_LABELS = {
    LiveOpsPublicOverlapField.GEOMETRY_OVERLAP_STATUS: "geometry overlap status",
    LiveOpsPublicOverlapField.TIME_OVERLAP_STATUS: "time overlap status",
    LiveOpsPublicOverlapField.ALTITUDE_OVERLAP_STATUS: "altitude overlap status",
}
_MARKUP_CHARACTERS = set("*_`[]()#<>&|~")
_LIMITATION_TEXT = {
    LiveOpsPublicLimitationCode.FRESHNESS_NOT_ESTABLISHED.value: (
        "Source freshness is not established. A stored currentness value, a stored freshness status, "
        "and the batch reference clock do not substitute for source freshness."
    ),
    LiveOpsPublicLimitationCode.NO_SNAPSHOT_LIMITATION.value: (
        "These records were read independently and are not a single transactional snapshot."
    ),
    LiveOpsPublicLimitationCode.GEOSPATIAL_UNEVALUATED_PRESENT.value: (
        "At least one hazard geometry in the supporting read was not spatially evaluated. "
        "This answer does not describe those unevaluated rows as intersections."
    ),
    LiveOpsPublicLimitationCode.OPERATIONALLY_UNEVALUATED_PRESENT.value: (
        "At least one encounter in the supporting read was not operationally evaluated. "
        "This answer does not describe those unevaluated rows as confirmed impacts or encounters."
    ),
    LiveOpsPublicLimitationCode.REGION_UNRESOLVED.value: (
        "A requested region was not resolved. No region membership is established for that unresolved request."
    ),
    LiveOpsPublicLimitationCode.LINEAGE_MISMATCH_PRESENT.value: (
        "A lineage mismatch is present in the supporting read. Conflicting lineage is not reconciled into one record."
    ),
    LiveOpsPublicLimitationCode.ALERT_EVIDENCE_IDENTITY_UNAVAILABLE.value: (
        "Alert evidence identity is unavailable. No alert identity is established from that read."
    ),
    LiveOpsPublicLimitationCode.QUERY_SELECTION_REQUIRED.value: (
        "Impact search requires a region or hazard ids. A worldwide impact scan was not performed."
    ),
    LiveOpsPublicLimitationCode.ENCOUNTER_SELECTION_REQUIRED.value: (
        "Encounter search requires an aircraft id, callsign, hazard id, or hazard ids. "
        "An unscoped encounter scan was not performed."
    ),
    LiveOpsPublicLimitationCode.CALLSIGN_REQUIRED.value: "Callsign discovery requires a non-empty callsign.",
    LiveOpsPublicLimitationCode.CALLSIGN_CASE_LIMITATION.value: (
        "Callsign matching uses the stored normalized query. It is not case-insensitive global callsign coverage."
    ),
    LiveOpsPublicLimitationCode.EVENTUAL_SCAN_LIMITATION.value: (
        "A recently written record may not yet be visible in this read."
    ),
    LiveOpsPublicLimitationCode.RECOMMENDATION_ABSENCE_LIMITATION.value: (
        "Absence of a recommendation is limited to the observed candidate set."
    ),
    LiveOpsPublicLimitationCode.ALERT_ABSENCE_LIMITATION.value: (
        "Absence of an alert is limited to the observed candidate set."
    ),
    LiveOpsPublicLimitationCode.OBSERVED_INVENTORY_NOT_A_COMPLETE_SNAPSHOT.value: (
        "The observed network inventory is not a complete snapshot."
    ),
    LiveOpsPublicLimitationCode.SPATIAL_EVALUATION_UNSUPPORTED.value: (
        "Region identity may be present while spatial evaluation is unsupported. "
        "An unsupported spatial evaluation is not an intersection."
    ),
    LiveOpsPublicLimitationCode.RECORDED_OVERLAP_NOT_PHYSICAL_ENTRY.value: (
        "A recorded impact, encounter, overlap status, or geometry flag is not physical entry into hazardous weather."
    ),
}


def _fail(code: LiveOpsRenderErrorCode) -> None:
    raise LiveOpsRenderError(code)


def _quote(value: str) -> str:
    """Render one verified token as quoted plain-text data.

    Backslash, quotes, ASCII controls, Unicode format and line separators,
    and Markdown or HTML metacharacters are escaped. Characters are not removed.
    """

    if not isinstance(value, str):
        _fail(LiveOpsRenderErrorCode.UNSAFE_VALUE)
    parts = ['"']
    for character in value:
        code_point = ord(character)
        if character == "\\":
            parts.append("\\\\")
        elif character == '"':
            parts.append('\\"')
        elif (
            code_point < 32
            or code_point == 127
            or unicodedata.category(character) in {"Cc", "Cf", "Zl", "Zp", "Cs"}
        ):
            if code_point <= 0xFFFF:
                parts.append(f"\\u{code_point:04x}")
            else:
                parts.append(f"\\U{code_point:08x}")
        elif character in _MARKUP_CHARACTERS:
            parts.append("\\" + character)
        else:
            parts.append(character)
    parts.append('"')
    return "".join(parts)


def _identifier(value: object) -> str:
    if not isinstance(value, str) or not value:
        _fail(LiveOpsRenderErrorCode.UNSAFE_VALUE)
    return _quote(value)


def _format_scalar(value: object) -> str:
    if type(value) is bool:
        return "true" if value else "false"
    if type(value) is int:
        return str(value)
    if type(value) is float:
        if not math.isfinite(value):
            _fail(LiveOpsRenderErrorCode.UNSAFE_VALUE)
        return repr(value)
    if isinstance(value, str):
        return _quote(value)
    _fail(LiveOpsRenderErrorCode.UNSAFE_VALUE)
    raise AssertionError


def _bool_word(value: object) -> str:
    if type(value) is not bool:
        _fail(LiveOpsRenderErrorCode.UNSAFE_VALUE)
    return "true" if value else "false"


def _metric(metric: object) -> str:
    if not isinstance(metric, LiveOpsPublicCountMetric):
        _fail(LiveOpsRenderErrorCode.UNSAFE_VALUE)
    return _METRIC_TEXT[metric]


def _scope_phrase(scope: object) -> str:
    if not isinstance(scope, LiveOpsPublicScope):
        _fail(LiveOpsRenderErrorCode.UNSAFE_VALUE)
    parts: list[str] = []
    if scope.region is not None:
        parts.append("region " + _identifier(scope.region))
    if scope.product_type is not None:
        parts.append("product type " + _identifier(scope.product_type))
    if scope.hazard_type is not None:
        parts.append("hazard type " + _identifier(scope.hazard_type))
    if scope.hazard_ids is not None:
        parts.append("hazard ids " + ", ".join(_identifier(item) for item in scope.hazard_ids))
    if scope.aircraft_id is not None:
        parts.append("aircraft " + _identifier(scope.aircraft_id))
    if scope.callsign is not None:
        parts.append("callsign " + _identifier(scope.callsign))
    if scope.hazard_id is not None:
        parts.append("hazard " + _identifier(scope.hazard_id))
    if not parts:
        return "with no additional selectors"
    return "with " + ", ".join(parts)


def _optional_version(claim: LiveOpsPublicClaim) -> str | None:
    if claim.kind is LiveOpsPublicClaimKind.CONFIRMED_IMPACT:
        return claim.source_version
    if claim.kind is LiveOpsPublicClaimKind.CONFIRMED_ENCOUNTER:
        return claim.hazard_source_version
    if claim.kind is LiveOpsPublicClaimKind.HAZARD_REGION_INTERSECTION:
        return claim.source_version
    if claim.kind is LiveOpsPublicClaimKind.CURRENTNESS:
        return claim.source_version
    return None


def _group_key(claim: LiveOpsPublicClaim, index: int) -> tuple[object, ...]:
    kind = claim.kind
    if kind in _ENCOUNTER_KINDS or (
        kind is LiveOpsPublicClaimKind.CURRENTNESS
        and claim.predicate is LiveOpsPublicCurrentnessPredicate.ENCOUNTER_IS_CURRENT
    ):
        if claim.encounter_id and claim.aircraft_id and claim.hazard_id:
            return ("encounter", claim.encounter_id, claim.aircraft_id, claim.hazard_id)
    elif kind is LiveOpsPublicClaimKind.AIRCRAFT_OBSERVED_STATE or (
        kind is LiveOpsPublicClaimKind.CURRENTNESS
        and claim.predicate is LiveOpsPublicCurrentnessPredicate.AIRCRAFT_IS_CURRENT
    ):
        if claim.aircraft_id:
            return ("observed", claim.aircraft_id)
    elif kind is LiveOpsPublicClaimKind.AIRCRAFT_PROJECTION_STATE and claim.aircraft_id and claim.projection_id:
        return ("projection", claim.aircraft_id, claim.projection_id)
    elif (
        kind is LiveOpsPublicClaimKind.CURRENTNESS
        and claim.predicate is LiveOpsPublicCurrentnessPredicate.PROJECTION_IS_CURRENT
        and claim.aircraft_id
        and claim.projection_id
    ):
        return ("projection", claim.aircraft_id, claim.projection_id)
    elif kind is LiveOpsPublicClaimKind.HAZARD_REGION_INTERSECTION and claim.hazard_id and claim.source_version:
        return ("hazard", claim.hazard_id, claim.source_version)
    elif (
        kind is LiveOpsPublicClaimKind.CURRENTNESS
        and claim.predicate is LiveOpsPublicCurrentnessPredicate.HAZARD_IS_CURRENT
        and claim.hazard_id
        and claim.source_version
    ):
        return ("hazard", claim.hazard_id, claim.source_version)
    return ("solo", index)


def _compatible(existing: tuple[LiveOpsPublicClaim, ...], new: LiveOpsPublicClaim, index: int) -> bool:
    if not existing or _group_key(existing[0], 0)[0] == "solo" or _group_key(new, index)[0] == "solo":
        return False
    if _group_key(existing[0], 0) != _group_key(new, index):
        return False
    versions = [item for item in (_optional_version(claim) for claim in (*existing, new)) if item is not None]
    projections = [claim.projection_id for claim in (*existing, new) if claim.projection_id is not None]
    return len(set(versions)) <= 1 and len(set(projections)) <= 1


def _header(claims: tuple[LiveOpsPublicClaim, ...]) -> str | None:
    if len(claims) < 2:
        return None
    family = _group_key(claims[0], 0)[0]
    if family == "encounter":
        claim = claims[0]
        return (
            f"For encounter {_identifier(claim.encounter_id)}, aircraft {_identifier(claim.aircraft_id)}, "
            f"and hazard {_identifier(claim.hazard_id)}:"
        )
    if family == "observed":
        return f"For aircraft {_identifier(claims[0].aircraft_id)}:"
    if family == "projection":
        claim = claims[0]
        return (
            f"For aircraft {_identifier(claim.aircraft_id)}, stored projection {_identifier(claim.projection_id)}:"
        )
    if family == "hazard":
        claim = claims[0]
        return f"For hazard {_identifier(claim.hazard_id)} at source version {_identifier(claim.source_version)}:"
    return None


def _impact(claim: LiveOpsPublicClaim) -> str:
    return (
        f"Aircraft {_identifier(claim.aircraft_id)} has a stored impact record with hazard "
        f"{_identifier(claim.hazard_id)}, source version {_identifier(claim.source_version)}, "
        f"and encounter {_identifier(claim.encounter_id)}."
    )


def _encounter(claim: LiveOpsPublicClaim) -> str:
    parts = [
        (
            f"Encounter {_identifier(claim.encounter_id)} records aircraft {_identifier(claim.aircraft_id)} "
            f"and hazard {_identifier(claim.hazard_id)} at hazard source version "
            f"{_identifier(claim.hazard_source_version)}."
        )
    ]
    if claim.projection_id is not None:
        parts.append(f"The stored projection id on this encounter is {_identifier(claim.projection_id)}.")
    for flag in claim.recorded_flags:
        parts.append(f"The stored flag {flag.name.value} is {_bool_word(flag.value)}.")
    for overlap in claim.overlap_statuses:
        label = _OVERLAP_LABELS.get(overlap.name)
        if label is None:
            _fail(LiveOpsRenderErrorCode.UNSAFE_VALUE)
        parts.append(f"The stored {label} is {_identifier(overlap.value)}.")
    return " ".join(parts)


def _count(claim: LiveOpsPublicClaim) -> str:
    metric = _metric(claim.metric)
    scope = _scope_phrase(claim.scope)
    if type(claim.count_value) is not int:
        _fail(LiveOpsRenderErrorCode.UNSAFE_VALUE)
    if claim.count_mode is LiveOpsPublicCountMode.EXACT_RETURNED_SET:
        text = (
            f"The executed scope {scope} has an exact returned-set count of {claim.count_value} for {metric}. "
            "This count is only that returned set, not a count outside the executed scope."
        )
    elif claim.count_mode is LiveOpsPublicCountMode.CONFIRMED_SUBSET:
        text = (
            f"The executed scope {scope} has a confirmed-subset count of {claim.count_value} for {metric}. "
            "This is not the exact returned set and it is not a complete population count."
        )
    else:
        _fail(LiveOpsRenderErrorCode.UNSAFE_VALUE)
        raise AssertionError
    if claim.count_value == 0:
        text += " A scoped count of zero is not an authoritative query zero."
    if claim.metric in _NETWORK_METRICS:
        text += " This is the observed network inventory count, not a count of every aircraft or hazard in the operation."
    return text


def _zero(claim: LiveOpsPublicClaim) -> str:
    text = (
        f"The executed scope {_scope_phrase(claim.scope)} has an authoritative query result of zero "
        f"for {_metric(claim.metric)}. This zero is limited to that executed query. "
        "It is not an incomplete result, and it does not prove that matching records are absent outside that scope."
    )
    if claim.metric is LiveOpsPublicCountMetric.CALLSIGN_MATCH_COUNT:
        text += " It does not prove that an aircraft with this callsign does not exist."
    return text


def _absent(claim: LiveOpsPublicClaim) -> str:
    noun = _CONTEXT_NOUN.get(claim.tool_name or "")
    if noun is None:
        _fail(LiveOpsRenderErrorCode.UNSAFE_VALUE)
    return (
        f"No operational context root was found for {noun} {_identifier(claim.entity_id)}. "
        "This is a not-found context read. It does not describe encounters, risk, recommendations, or alerts, "
        f"and it does not prove that the {noun} is absent from every other source."
    )


def _intersection(claim: LiveOpsPublicClaim) -> str:
    return (
        f"Hazard {_identifier(claim.hazard_id)} at source version {_identifier(claim.source_version)} "
        f"has a recorded region intersection with {_identifier(claim.region_code)} "
        f"({_identifier(claim.region_name)}), for region query {_identifier(claim.region_query)}. "
        "This places the hazard geometry against that resolved region. "
        "It does not place an aircraft inside the region or inside the hazard, and it does not classify the hazard product."
    )


def _observed_value_text(field: LiveOpsPublicObservedField, value: object) -> str:
    rendered = _format_scalar(value)
    unit = _OBSERVED_UNITS.get(field)
    if unit is not None and type(value) in {int, float}:
        rendered = f"{rendered} {unit}"
    text = f"stored observed {field.value} {rendered}."
    if field is LiveOpsPublicObservedField.FRESHNESS_STATUS:
        text += " This is the stored freshness_status label, not a new freshness determination."
    elif field is LiveOpsPublicObservedField.POSITION_TIME_UTC:
        text += " This is the stored position time, not the batch reference clock."
    return text


def _observed(claim: LiveOpsPublicClaim) -> str:
    if not isinstance(claim.observed_field, LiveOpsPublicObservedField):
        _fail(LiveOpsRenderErrorCode.UNSAFE_VALUE)
    if claim.retained_not_current is True:
        retained = "This observed value is marked as retained and not current."
    elif claim.retained_not_current is False:
        retained = "This observed value is not marked as retained/not-current by this flag."
    else:
        _fail(LiveOpsRenderErrorCode.UNSAFE_VALUE)
        raise AssertionError
    return (
        f"Aircraft {_identifier(claim.aircraft_id)} has {_observed_value_text(claim.observed_field, claim.observed_value)} "
        f"{retained}"
    )


def _projection(claim: LiveOpsPublicClaim) -> str:
    if not isinstance(claim.projection_field, LiveOpsPublicProjectionField):
        _fail(LiveOpsRenderErrorCode.UNSAFE_VALUE)
    rendered = _format_scalar(claim.projection_value)
    if claim.projection_field is LiveOpsPublicProjectionField.PROJECTION_HORIZON_MIN and type(claim.projection_value) in {int, float}:
        rendered = f"{rendered} minutes"
    text = (
        f"Aircraft {_identifier(claim.aircraft_id)} has stored projection {_identifier(claim.projection_id)} "
        f"{claim.projection_field.value} {rendered}. This is a projected value, not an observed aircraft state."
    )
    if claim.projection_field in {
        LiveOpsPublicProjectionField.GENERATED_AT_UTC,
        LiveOpsPublicProjectionField.VALID_UNTIL_UTC,
    }:
        text += " This timestamp is the stored projection field, not the batch reference clock, and it does not establish source freshness."
    return text


def _currentness(claim: LiveOpsPublicClaim) -> str:
    predicate = claim.predicate
    value = _bool_word(claim.currentness_value)
    if predicate is LiveOpsPublicCurrentnessPredicate.AIRCRAFT_IS_CURRENT:
        return f"The recorded aircraft currentness predicate for aircraft {_identifier(claim.aircraft_id)} is {value}."
    if predicate is LiveOpsPublicCurrentnessPredicate.HAZARD_IS_CURRENT:
        version = f" at source version {_identifier(claim.source_version)}" if claim.source_version is not None else ""
        return (
            f"The recorded hazard currentness predicate for hazard {_identifier(claim.hazard_id)}{version} is {value}. "
            "This is not a source-freshness determination."
        )
    if predicate is LiveOpsPublicCurrentnessPredicate.HAZARD_IS_LIFECYCLE_ACTIVE:
        return (
            f"The recorded hazard lifecycle-active predicate for hazard {_identifier(claim.hazard_id)} is {value}. "
            "This is not hazard currentness."
        )
    if predicate is LiveOpsPublicCurrentnessPredicate.PROJECTION_IS_CURRENT:
        projection = f" and projection {_identifier(claim.projection_id)}" if claim.projection_id is not None else ""
        return (
            f"The recorded projection currentness predicate for aircraft {_identifier(claim.aircraft_id)}{projection} "
            f"is {value}. This is not aircraft currentness."
        )
    if predicate is LiveOpsPublicCurrentnessPredicate.ENCOUNTER_IS_CURRENT:
        return (
            f"The recorded encounter currentness predicate for encounter {_identifier(claim.encounter_id)}, "
            f"aircraft {_identifier(claim.aircraft_id)}, and hazard {_identifier(claim.hazard_id)} is {value}."
        )
    if predicate is LiveOpsPublicCurrentnessPredicate.AIRPORT_IS_CURRENT:
        return f"The recorded airport currentness predicate for airport {_identifier(claim.airport_id)} is {value}."
    if predicate is LiveOpsPublicCurrentnessPredicate.CURRENT_IMPACTS_EVALUATED:
        return (
            f"The recorded current-impacts-evaluated predicate for hazard {_identifier(claim.hazard_id)} is {value}. "
            "This is not a count and it is not a source-freshness determination."
        )
    if predicate is LiveOpsPublicCurrentnessPredicate.HAZARD_SELECTION_NOT_CURRENT:
        return (
            f"The recorded predicate HAZARD_SELECTION_NOT_CURRENT for hazard {_identifier(claim.hazard_id)} "
            f"at source version {_identifier(claim.source_version)} is {value}. "
            "A false value is not rewritten as hazard currentness."
        )
    _fail(LiveOpsRenderErrorCode.UNSUPPORTED_CLAIM)
    raise AssertionError


def _risk(claim: LiveOpsPublicClaim) -> str:
    identity = (
        f"encounter {_identifier(claim.encounter_id)}, aircraft {_identifier(claim.aircraft_id)}, "
        f"and hazard {_identifier(claim.hazard_id)}"
    )
    if claim.risk_mode is LiveOpsPublicRiskMode.PRESENT:
        if type(claim.risk_score) is not int:
            _fail(LiveOpsRenderErrorCode.UNSAFE_VALUE)
        return (
            f"Encounter {_identifier(claim.encounter_id)} for aircraft {_identifier(claim.aircraft_id)} "
            f"and hazard {_identifier(claim.hazard_id)} has stored risk record {_identifier(claim.risk_id)} "
            f"with stored level {_identifier(claim.risk_level)} and stored score {claim.risk_score}."
        )
    if claim.risk_mode is LiveOpsPublicRiskMode.ABSENT_FROM_OBSERVED_CANDIDATES:
        return (
            f"No stored risk record was present in the observed candidates for {identity}. "
            "This is not a LOW risk level."
        )
    _fail(LiveOpsRenderErrorCode.UNSAFE_VALUE)
    raise AssertionError


def _recommendations(claim: LiveOpsPublicClaim) -> str:
    identity = (
        f"encounter {_identifier(claim.encounter_id)}, aircraft {_identifier(claim.aircraft_id)}, "
        f"and hazard {_identifier(claim.hazard_id)}"
    )
    if claim.advisory_mode is LiveOpsPublicAdvisoryMode.PRESENT_SET:
        if not claim.recommendation_ids:
            _fail(LiveOpsRenderErrorCode.UNSAFE_VALUE)
        listed = ", ".join(_identifier(item) for item in claim.recommendation_ids)
        return (
            f"Encounter {_identifier(claim.encounter_id)} for aircraft {_identifier(claim.aircraft_id)} "
            f"and hazard {_identifier(claim.hazard_id)} has stored recommendation ids {listed}."
        )
    if claim.advisory_mode is LiveOpsPublicAdvisoryMode.ABSENT_FROM_OBSERVED_CANDIDATES:
        return f"No stored recommendation was present in the observed candidates for {identity}."
    _fail(LiveOpsRenderErrorCode.UNSAFE_VALUE)
    raise AssertionError


def _alert(claim: LiveOpsPublicClaim) -> str:
    identity = (
        f"encounter {_identifier(claim.encounter_id)}, aircraft {_identifier(claim.aircraft_id)}, "
        f"and hazard {_identifier(claim.hazard_id)}"
    )
    if claim.advisory_mode is LiveOpsPublicAdvisoryMode.PRESENT_SET:
        text = (
            f"Encounter {_identifier(claim.encounter_id)} for aircraft {_identifier(claim.aircraft_id)} "
            f"and hazard {_identifier(claim.hazard_id)} has stored alert {_identifier(claim.alert_id)} "
            f"in state {_identifier(claim.alert_state)}"
        )
        if claim.alert_type is not None:
            text += f" with stored type {_identifier(claim.alert_type)}"
        return text + "."
    if claim.advisory_mode is LiveOpsPublicAdvisoryMode.ABSENT_FROM_OBSERVED_CANDIDATES:
        return f"No stored alert was present in the observed candidates for {identity}."
    _fail(LiveOpsRenderErrorCode.UNSAFE_VALUE)
    raise AssertionError


def _callsign_match_text(callsign: object, aircraft_ids: tuple[str, ...], tool_call_id: str | None) -> str:
    call = f" (tool call {_identifier(tool_call_id)})" if tool_call_id is not None else ""
    if len(aircraft_ids) == 1:
        return (
            f"Callsign search {_identifier(callsign)}{call} returned one current supported aircraft id, "
            f"{_identifier(aircraft_ids[0])}. This is the supported match for this search. "
            "It is not a permanent identity guarantee, and no other aircraft is selected."
        )
    if len(aircraft_ids) >= 2:
        listed = ", ".join(_identifier(item) for item in aircraft_ids)
        return (
            f"Callsign search {_identifier(callsign)}{call} returned multiple current supported aircraft ids: "
            f"{listed}. No aircraft is selected."
        )
    return (
        f"Callsign search {_identifier(callsign)}{call} returned an empty supported aircraft-id set. "
        "No aircraft is selected."
    )


def _callsign_zero_text(callsign: object, tool_call_id: str | None) -> str:
    call = f" (tool call {_identifier(tool_call_id)})" if tool_call_id is not None else ""
    return (
        f"Callsign search {_identifier(callsign)}{call} returned an authoritative query result of zero "
        "current supported matches. This does not prove that an aircraft with this callsign does not exist."
    )


def _incomplete_discovery(discovery: LiveOpsDiscovery) -> str:
    return (
        f"Callsign search {_identifier(discovery.callsign)} (tool call {_identifier(discovery.evidence_tool_call_id)}) "
        "did not produce a verified match set. No aircraft identity is established by this search."
    )


def _truncation(claim: LiveOpsPublicClaim) -> str:
    if type(claim.total_count) is not int:
        _fail(LiveOpsRenderErrorCode.UNSAFE_VALUE)
    return (
        f"The model-visible list {_identifier(claim.list_name)} is truncated. "
        f"The stored total count is {claim.total_count}. "
        "Rows omitted from the model-visible list are not described in this answer."
    )


def _contradiction(claim: LiveOpsPublicClaim) -> str:
    calls = f"Tool calls {_identifier(claim.tool_call_id_a)} and {_identifier(claim.tool_call_id_b)}"
    subject = claim.subject
    if subject is None:
        _fail(LiveOpsRenderErrorCode.UNSAFE_VALUE)
    if subject.value == "HAZARD_SOURCE_VERSION":
        return (
            f"{calls} disagree on the source version recorded for hazard {_identifier(claim.hazard_id)}. "
            "Neither version is selected."
        )
    if subject.value == "AIRCRAFT_CURRENTNESS":
        return (
            f"{calls} disagree on the recorded currentness of aircraft {_identifier(claim.aircraft_id)}. "
            "Neither value is selected."
        )
    if subject.value == "ENCOUNTER_LINEAGE":
        return (
            f"{calls} disagree on the recorded lineage of encounter {_identifier(claim.encounter_id)}. "
            "Neither record is selected."
        )
    _fail(LiveOpsRenderErrorCode.UNSUPPORTED_CLAIM)
    raise AssertionError


_RENDERERS = {
    LiveOpsPublicClaimKind.CONFIRMED_IMPACT: _impact,
    LiveOpsPublicClaimKind.CONFIRMED_ENCOUNTER: _encounter,
    LiveOpsPublicClaimKind.SCOPED_COUNT: _count,
    LiveOpsPublicClaimKind.QUERY_RESULT_ZERO: _zero,
    LiveOpsPublicClaimKind.CONTEXT_ROOT_ABSENT: _absent,
    LiveOpsPublicClaimKind.HAZARD_REGION_INTERSECTION: _intersection,
    LiveOpsPublicClaimKind.AIRCRAFT_OBSERVED_STATE: _observed,
    LiveOpsPublicClaimKind.AIRCRAFT_PROJECTION_STATE: _projection,
    LiveOpsPublicClaimKind.CURRENTNESS: _currentness,
    LiveOpsPublicClaimKind.RISK_STATE: _risk,
    LiveOpsPublicClaimKind.RECOMMENDATION_SET: _recommendations,
    LiveOpsPublicClaimKind.ALERT_RECORD: _alert,
    LiveOpsPublicClaimKind.MODEL_VISIBLE_TRUNCATION: _truncation,
    LiveOpsPublicClaimKind.CROSS_TOOL_CONTRADICTION: _contradiction,
}


def _discovery_for(claim: LiveOpsPublicClaim, discoveries: dict[str, LiveOpsDiscovery]) -> LiveOpsDiscovery | None:
    if claim.tool_call_id is None:
        return None
    return discoveries.get(claim.tool_call_id)


def _render_claim(claim: LiveOpsPublicClaim, discoveries: dict[str, LiveOpsDiscovery]) -> str:
    if claim.kind is LiveOpsPublicClaimKind.CALLSIGN_MATCH_SET:
        discovery = _discovery_for(claim, discoveries)
        if discovery is not None:
            if (
                discovery.status not in {
                    LiveOpsDiscoveryStatus.UNIQUE_SUPPORTED_MATCH,
                    LiveOpsDiscoveryStatus.MULTIPLE_SUPPORTED_MATCHES,
                }
                or discovery.callsign != claim.callsign
                or discovery.aircraft_ids != claim.aircraft_ids
            ):
                _fail(LiveOpsRenderErrorCode.INVALID_EXPORT)
            return _callsign_match_text(claim.callsign, claim.aircraft_ids, discovery.evidence_tool_call_id)
        return _callsign_match_text(claim.callsign, claim.aircraft_ids, claim.tool_call_id)
    if (
        claim.kind is LiveOpsPublicClaimKind.QUERY_RESULT_ZERO
        and claim.metric is LiveOpsPublicCountMetric.CALLSIGN_MATCH_COUNT
    ):
        discovery = _discovery_for(claim, discoveries)
        if discovery is not None:
            if (
                discovery.status is not LiveOpsDiscoveryStatus.NO_SUPPORTED_MATCH
                or claim.scope is None
                or discovery.callsign != claim.scope.callsign
            ):
                _fail(LiveOpsRenderErrorCode.INVALID_EXPORT)
            return _callsign_zero_text(discovery.callsign, discovery.evidence_tool_call_id)
        return _zero(claim)
    if claim.kind is LiveOpsPublicClaimKind.COVERAGE_LIMITATION:
        _fail(LiveOpsRenderErrorCode.TEMPLATE_FAILURE)
    renderer = _RENDERERS.get(claim.kind)
    if renderer is None:
        _fail(LiveOpsRenderErrorCode.UNSUPPORTED_CLAIM)
        raise AssertionError
    return renderer(claim)


def _groups(claims: tuple[LiveOpsPublicClaim, ...]) -> tuple[tuple[int, ...], ...]:
    groups: list[tuple[int, ...]] = []
    index = 0
    while index < len(claims):
        members = [index]
        nxt = index + 1
        while nxt < len(claims) and _compatible(
            tuple(claims[item] for item in members),
            claims[nxt],
            nxt,
        ):
            members.append(nxt)
            nxt += 1
        groups.append(tuple(members))
        index = nxt
    return tuple(groups)


def _limitation_sentence(code: str, *, first: bool) -> str:
    if not isinstance(code, str) or not code or code != code.strip():
        _fail(LiveOpsRenderErrorCode.UNSAFE_VALUE)
    text = _LIMITATION_TEXT.get(code)
    if text is None:
        text = (
            f"Limitation {_quote(code)} applies. This renderer does not expand that code "
            "and does not treat it as harmless."
        )
    if first:
        return "Limitations: " + text
    return text


def _coverage_indexes(claims: tuple[LiveOpsPublicClaim, ...]) -> dict[str, tuple[int, ...]]:
    found: dict[str, list[int]] = {}
    for index, claim in enumerate(claims):
        if claim.kind is not LiveOpsPublicClaimKind.COVERAGE_LIMITATION:
            continue
        if not isinstance(claim.limitation_code, LiveOpsPublicLimitationCode):
            _fail(LiveOpsRenderErrorCode.UNSAFE_VALUE)
        found.setdefault(claim.limitation_code.value, []).append(index)
    return {code: tuple(indexes) for code, indexes in found.items()}


def _validate_export(export: LiveOpsEvidenceExport) -> None:
    if not isinstance(export, LiveOpsEvidenceExport):
        _fail(LiveOpsRenderErrorCode.INVALID_EXPORT)
    if export.schema_version != LIVE_OPS_EXPORT_SCHEMA_VERSION:
        _fail(LiveOpsRenderErrorCode.INVALID_EXPORT)
    outcome = export.verification_outcome
    if not isinstance(outcome, LiveOpsPublicVerificationOutcome) or export.rendering_posture is not _POSTURE[outcome]:
        _fail(LiveOpsRenderErrorCode.INVALID_EXPORT)
    if outcome not in _RENDERABLE:
        _fail(LiveOpsRenderErrorCode.NOT_RENDERABLE)
    if not export.verified_claims:
        _fail(LiveOpsRenderErrorCode.INVALID_EXPORT)


def render_live_ops_answer(export: LiveOpsEvidenceExport) -> LiveOpsRenderedAnswer:
    """Render verified public claims. Rejected diagnostics are not an input to prose."""

    _validate_export(export)
    claims = export.verified_claims
    discoveries = {item.evidence_tool_call_id: item for item in export.discoveries}
    if len(discoveries) != len(export.discoveries):
        _fail(LiveOpsRenderErrorCode.INVALID_EXPORT)
    used_discoveries: set[str] = set()
    sentences: list[LiveOpsRenderedSentence] = [
        LiveOpsRenderedSentence(
            _OPENING_ALL if export.verification_outcome is LiveOpsPublicVerificationOutcome.ALL_VERIFIED else _OPENING_PARTIAL,
            (),
        )
    ]
    relationship_indexes: list[int] = []
    risk_indexes: list[int] = []
    recommendation_indexes: list[int] = []
    alert_indexes: list[int] = []
    for group in _groups(claims):
        grouped_claims = tuple(claims[index] for index in group)
        header = _header(grouped_claims)
        if header is not None:
            sentences.append(LiveOpsRenderedSentence(header, group))
        for index in group:
            claim = claims[index]
            if claim.kind is LiveOpsPublicClaimKind.COVERAGE_LIMITATION:
                continue
            if claim.kind in {LiveOpsPublicClaimKind.CONFIRMED_IMPACT, LiveOpsPublicClaimKind.CONFIRMED_ENCOUNTER}:
                relationship_indexes.append(index)
            if claim.kind is LiveOpsPublicClaimKind.RISK_STATE and claim.risk_mode is LiveOpsPublicRiskMode.PRESENT:
                risk_indexes.append(index)
            if (
                claim.kind is LiveOpsPublicClaimKind.RECOMMENDATION_SET
                and claim.advisory_mode is LiveOpsPublicAdvisoryMode.PRESENT_SET
            ):
                recommendation_indexes.append(index)
            if claim.kind is LiveOpsPublicClaimKind.ALERT_RECORD and claim.advisory_mode is LiveOpsPublicAdvisoryMode.PRESENT_SET:
                alert_indexes.append(index)
            discovery = _discovery_for(claim, discoveries)
            authorizes_discovery = claim.kind is LiveOpsPublicClaimKind.CALLSIGN_MATCH_SET or (
                claim.kind is LiveOpsPublicClaimKind.QUERY_RESULT_ZERO
                and claim.metric is LiveOpsPublicCountMetric.CALLSIGN_MATCH_COUNT
            )
            if authorizes_discovery and discovery is not None:
                used_discoveries.add(discovery.evidence_tool_call_id)
            sentences.append(LiveOpsRenderedSentence(_render_claim(claim, discoveries), (index,)))
    for discovery in export.discoveries:
        if discovery.evidence_tool_call_id in used_discoveries:
            continue
        if discovery.status is not LiveOpsDiscoveryStatus.INCOMPLETE:
            _fail(LiveOpsRenderErrorCode.INVALID_EXPORT)
        sentences.append(LiveOpsRenderedSentence(_incomplete_discovery(discovery), ()))
    if relationship_indexes:
        sentences.append(LiveOpsRenderedSentence(_STORED_RELATIONSHIP, tuple(relationship_indexes)))
    if risk_indexes:
        sentences.append(LiveOpsRenderedSentence(_STORED_RISK, tuple(risk_indexes)))
    if recommendation_indexes:
        sentences.append(LiveOpsRenderedSentence(_STORED_RECOMMENDATIONS, tuple(recommendation_indexes)))
    if alert_indexes:
        sentences.append(LiveOpsRenderedSentence(_STORED_ALERTS, tuple(alert_indexes)))
    coverage = _coverage_indexes(claims)
    codes: list[str] = []
    for code in export.mandatory_limitations:
        if not isinstance(code, str):
            _fail(LiveOpsRenderErrorCode.UNSAFE_VALUE)
        if code not in codes:
            codes.append(code)
    for code in coverage:
        if code not in codes:
            codes.append(code)
    for position, code in enumerate(codes):
        sentences.append(
            LiveOpsRenderedSentence(
                _limitation_sentence(code, first=position == 0),
                coverage.get(code, ()),
            )
        )
    represented = {index for sentence in sentences for index in sentence.claim_indexes}
    if represented != set(range(len(claims))):
        _fail(LiveOpsRenderErrorCode.TEMPLATE_FAILURE)
    answer = " ".join(sentence.text for sentence in sentences)
    if len(answer) > LIVE_OPS_PUBLIC_ANSWER_MAX_LENGTH:
        _fail(LiveOpsRenderErrorCode.ANSWER_LENGTH_EXCEEDED)
    return LiveOpsRenderedAnswer(answer, tuple(sentences))


__all__ = [
    "LiveOpsRenderError",
    "LiveOpsRenderErrorCode",
    "LiveOpsRenderedAnswer",
    "LiveOpsRenderedSentence",
    "render_live_ops_answer",
]
