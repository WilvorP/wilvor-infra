"""Read persisted airport-evaluation rows for one stored recommendation.

The only domain input is ``recommendation_id``. Tables, ``tool_call_id``,
and ``query_timestamp_utc`` stay on ``PersistedAirportEvidenceCall``.
This module does not search airports, rescore, rerank, or acquire a clock.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from wilvor_ai.contracts import (
    ConfidenceLevel,
    ContractValidationError,
    Evidence,
    FreshnessStatus,
    SourceRecord,
    TemporalScope,
    ToolResult,
    ToolResultStatus,
    _validate_utc,
)
from wilvor_ai.persisted_airport_contracts import (
    COMPLETE,
    UNAVAILABLE,
    WAITING_FOR_WEATHER,
    PersistedAirportCandidate,
    PersistedAirportEvidence,
    _json_number,
    numbers_equal,
    validate_persisted_airport_tool_result,
)
from wilvor_operational import readers


TOOL_NAME = "get_persisted_airport_candidate_evidence"
EMPTY_ASSESSMENTS = "No candidate airport assessment was available."
NO_COMPLETE_ASSESSMENT = "No candidate airport currently has a COMPLETE assessment."
NOT_CURRENT_LIMITATION = (
    "Persisted airport evaluation evidence is not asserted to be current."
)
MEMBERSHIP_LIMITATION = (
    "Persisted airport rows are returned for this evaluation id. This read "
    "does not prove the original complete candidate set after TTL or a later write."
)
_READ_FAILED = "The persisted airport evaluation read failed."
_QUERY_FAILED = "The persisted airport assessment read failed."
_MISSING_REFERENCED_ROW = (
    "A referenced airport assessment row is not in the stored evaluation."
)
_WAITING_ROWS_ABSENT = (
    "The recommendation recorded a non-empty airport assessment list. "
    "Those rows are not in the stored evaluation."
)
_INCONSISTENT = "Persisted airport evaluation evidence is inconsistent."
_NOT_FOUND = "The recommendation was not found."
_EVALUATION_REFERENCE = "AIRPORT_ASSESSMENT_EVALUATION"
_ASSESSMENT_REFERENCE = "AIRPORT_ASSESSMENT"


@dataclass(frozen=True)
class PersistedAirportEvidenceCall:
    """Runtime-owned read context. ``query_timestamp_utc`` is not currentness."""

    tables: Any
    tool_call_id: str
    query_timestamp_utc: str
    correlation_id: str | None = None

    def __post_init__(self) -> None:
        if (
            not isinstance(self.tool_call_id, str)
            or not self.tool_call_id
            or self.tool_call_id != self.tool_call_id.strip()
        ):
            raise ContractValidationError("invalid_tool_call_id")
        if self.correlation_id is not None and (
            not isinstance(self.correlation_id, str)
            or not self.correlation_id
            or self.correlation_id != self.correlation_id.strip()
        ):
            raise ContractValidationError("invalid_correlation_id")
        errors = _validate_utc(self.query_timestamp_utc, "query_timestamp_utc")
        if errors:
            raise ContractValidationError(errors)


def get_persisted_airport_candidate_evidence(
    call: PersistedAirportEvidenceCall,
    recommendation_id: str,
) -> ToolResult:
    """Return persisted airport rows linked from one recommendation."""

    if not isinstance(call, PersistedAirportEvidenceCall):
        raise TypeError("call must be PersistedAirportEvidenceCall")
    if not isinstance(recommendation_id, str) or not recommendation_id or (
        recommendation_id != recommendation_id.strip()
    ):
        raise ContractValidationError("invalid_recommendation_id")

    try:
        recommendation = readers.get_recommendation_record(
            call.tables.recommendations,
            recommendation_id,
        )
    except Exception:
        return _result(
            call,
            ToolResultStatus.UNAVAILABLE,
            _empty_payload(recommendation_id),
            (),
            (_READ_FAILED,),
            as_of_utc=None,
        )

    if recommendation is None:
        return _result(
            call,
            ToolResultStatus.NOT_FOUND,
            _empty_payload(recommendation_id),
            (),
            (_NOT_FOUND,),
            as_of_utc=None,
        )

    identity = _evaluation_identity(recommendation)
    if identity.conflict:
        return _unknown(call, recommendation, recommendation_id)
    if identity.evaluation_id is None:
        return _result(
            call,
            ToolResultStatus.SUCCESS,
            _empty_payload(recommendation_id),
            (_recommendation_evidence(call, recommendation, recommendation_id),),
            (),
            as_of_utc=None,
        )

    if not _hazard_versions_agree(recommendation) or _recommendation_instant(
        recommendation
    ) is None:
        return _unknown(call, recommendation, recommendation_id)

    try:
        rows = readers.query_airport_assessments_for_evaluation(
            call.tables.airport_assessments,
            identity.evaluation_id,
        )
    except Exception:
        return _result(
            call,
            ToolResultStatus.UNAVAILABLE,
            _empty_payload(recommendation_id, identity.evaluation_id),
            (_recommendation_evidence(call, recommendation, recommendation_id),),
            (_QUERY_FAILED,),
            as_of_utc=None,
        )

    reason = recommendation.get("no_suitable_candidate_reason")
    if reason == EMPTY_ASSESSMENTS and rows:
        return _unknown(call, recommendation, recommendation_id, identity.evaluation_id)
    if not rows:
        return _empty_evaluation_result(
            call,
            recommendation,
            recommendation_id,
            identity.evaluation_id,
            reason,
        )

    built = _accept_rows(recommendation, rows, identity.evaluation_id)
    if built.conflict:
        return _unknown(call, recommendation, recommendation_id, identity.evaluation_id)
    payload = PersistedAirportEvidence(
        recommendation_id=recommendation_id,
        airport_evaluation_id=identity.evaluation_id,
        candidates=built.candidates,
        no_suitable_candidate_reason=reason if isinstance(reason, str) else None,
    )
    evidence = (
        _recommendation_evidence(call, recommendation, recommendation_id),
        _assessment_evidence(call, built.candidates),
    )
    limitations = [MEMBERSHIP_LIMITATION]
    status = ToolResultStatus.SUCCESS
    if built.missing_reference:
        status = ToolResultStatus.PARTIAL
        limitations.append(_MISSING_REFERENCED_ROW)
    return _result(
        call,
        status,
        payload,
        evidence,
        tuple(limitations),
        as_of_utc=built.created_at_utc,
    )


@dataclass(frozen=True)
class _Identity:
    evaluation_id: str | None
    conflict: bool


def _evaluation_identity(recommendation: Any) -> _Identity:
    if not isinstance(recommendation, dict):
        return _Identity(None, True)
    top = _id_copy(recommendation.get("airport_evaluation_id"))
    versions = recommendation.get("source_versions")
    if versions is None:
        source = _id_copy(None)
    elif not isinstance(versions, dict):
        return _Identity(None, True)
    else:
        source = _id_copy(versions.get("airport_evaluation_id"))
    references = _evaluation_reference_ids(recommendation.get("evidence_references"))
    if references is None:
        return _Identity(None, True)
    copies = [top, source, *references]
    if any(item == "malformed" for item in copies):
        return _Identity(None, True)
    present = [item for item in copies if item is not None]
    if not present:
        return _Identity(None, False)
    if top is None or source is None or not references:
        return _Identity(None, True)
    distinct = set(present)
    if len(distinct) != 1:
        return _Identity(None, True)
    return _Identity(present[0], False)


def _id_copy(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value or value != value.strip():
        return "malformed"
    return value


def _evaluation_reference_ids(references: Any) -> list[str | None] | None:
    if references is None:
        return []
    if not isinstance(references, list):
        return None
    found: list[str | None] = []
    for item in references:
        if not isinstance(item, dict):
            return None
        if item.get("type") != _EVALUATION_REFERENCE:
            continue
        found.append(_id_copy(item.get("id")))
    return found


def _hazard_versions_agree(recommendation: dict) -> bool:
    versions = recommendation.get("source_versions")
    if not isinstance(versions, dict):
        return False
    left = recommendation.get("hazard_source_version")
    right = versions.get("hazard_source_version")
    return (
        isinstance(left, str)
        and isinstance(right, str)
        and bool(left)
        and left == right
    )


def _recommendation_instant(recommendation: dict) -> datetime | None:
    parsed = _parse_utc(recommendation.get("created_at_utc"))
    epoch = _epoch(recommendation.get("created_at_epoch"))
    if parsed is None or epoch is None:
        return None
    if int(parsed.timestamp()) != epoch:
        return None
    return parsed


def _empty_evaluation_result(call, recommendation, recommendation_id, evaluation_id, reason):
    evidence = (_recommendation_evidence(call, recommendation, recommendation_id),)
    payload_reason = reason if isinstance(reason, str) else None
    if reason == EMPTY_ASSESSMENTS:
        observed = recommendation.get("created_at_utc")
        return _result(
            call,
            ToolResultStatus.SUCCESS,
            PersistedAirportEvidence(
                recommendation_id=recommendation_id,
                airport_evaluation_id=evaluation_id,
                candidates=(),
                no_suitable_candidate_reason=EMPTY_ASSESSMENTS,
                recommendation_observed_empty_at_utc=(
                    observed if isinstance(observed, str) else None
                ),
            ),
            evidence,
            (),
            as_of_utc=None,
        )
    if reason == NO_COMPLETE_ASSESSMENT:
        return _result(
            call,
            ToolResultStatus.PARTIAL,
            PersistedAirportEvidence(
                recommendation_id=recommendation_id,
                airport_evaluation_id=evaluation_id,
                candidates=(),
                no_suitable_candidate_reason=NO_COMPLETE_ASSESSMENT,
            ),
            evidence,
            (_WAITING_ROWS_ABSENT,),
            as_of_utc=None,
        )
    return _unknown(call, recommendation, recommendation_id, evaluation_id)


@dataclass(frozen=True)
class _Built:
    candidates: tuple[PersistedAirportCandidate, ...]
    created_at_utc: str | None
    missing_reference: bool
    conflict: bool


def _accept_rows(recommendation: dict, rows: list, evaluation_id: str) -> _Built:
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        return _Built((), None, False, True)
    recommendation_at = _recommendation_instant(recommendation)
    if recommendation_at is None:
        return _Built((), None, False, True)
    parsed_rows = []
    for row in rows:
        parsed = _inspect_row(row, recommendation, evaluation_id, recommendation_at)
        if parsed is None:
            return _Built((), None, False, True)
        parsed_rows.append(parsed)
    if _duplicate_identity(parsed_rows):
        return _Built((), None, False, True)
    if not _one_generation(parsed_rows) or not _versions_agree(parsed_rows):
        return _Built((), None, False, True)
    snapshots = _snapshots(recommendation)
    if snapshots is None:
        return _Built((), None, False, True)
    by_id = {item["airport_assessment_id"]: item for item in parsed_rows}
    missing = False
    for assessment_id, fields in snapshots.items():
        row = by_id.get(assessment_id)
        if row is None:
            missing = True
            continue
        if not _snapshot_matches(row, fields):
            return _Built((), None, False, True)
    ordered = _ordered(parsed_rows)
    candidates = tuple(_to_candidate(item) for item in ordered)
    return _Built(candidates, ordered[0]["created_at_utc"], missing, False)


def _duplicate_identity(rows: list[dict]) -> bool:
    airports = [row["airport_id"] for row in rows]
    assessments = [row["airport_assessment_id"] for row in rows]
    ranks = [
        _json_number(row["rank"], "rank")
        for row in rows
        if row.get("assessment_status") == COMPLETE and row.get("rank") is not None
    ]
    return (
        len(airports) != len(set(airports))
        or len(assessments) != len(set(assessments))
        or len(ranks) != len(set(ranks))
    )


_SCORE_FIELDS = (
    "rank",
    "distance_score",
    "weather_score",
    "taf_score",
    "total_airport_score",
)


def _score_shape_ok(row: dict) -> bool:
    """WAITING rows stop before scoring; COMPLETE rows are stored already ranked."""

    stored = {name: row[name] if name in row else None for name in _SCORE_FIELDS}
    status = row.get("assessment_status")
    if status == WAITING_FOR_WEATHER:
        return all(value is None for value in stored.values())
    if status != COMPLETE or any(value is None for value in stored.values()):
        return False
    if not _positive_integer(stored["rank"]):
        return False
    return all(
        _decimal_ok(stored[name]) for name in _SCORE_FIELDS if name != "rank"
    )


def _positive_integer(value: Any) -> bool:
    if not _decimal_ok(value):
        return False
    number = _json_number(value, "rank")
    return type(number) is int and number > 0


def _decimal_ok(value: Any) -> bool:
    try:
        return numbers_equal(value, value)
    except Exception:
        return False


def _inspect_row(row, recommendation, evaluation_id, recommendation_at) -> dict | None:
    if row.get("assessment_status") not in {COMPLETE, WAITING_FOR_WEATHER}:
        return None
    for field_name in (
        "route_safety_status",
        "runway_evidence_status",
        "congestion_evidence_status",
    ):
        if row.get(field_name) != UNAVAILABLE:
            return None
    if (
        row.get("evaluation_id") != evaluation_id
        or row.get("risk_id") != recommendation.get("risk_id")
        or row.get("aircraft_id") != recommendation.get("aircraft_id")
        or row.get("hazard_id") != recommendation.get("hazard_id")
        or row.get("hazard_source_version") != recommendation.get("hazard_source_version")
    ):
        return None
    if row.get("evaluation_version") != row.get("assessment_ruleset_version"):
        return None
    created = _parse_utc(row.get("created_at_utc"))
    epoch = _epoch(row.get("created_at_epoch"))
    expires = _epoch(row.get("expires_at_epoch"))
    if created is None or epoch is None or expires is None:
        return None
    if int(created.timestamp()) != epoch or expires < epoch:
        return None
    if created > recommendation_at:
        return None
    required_text = (
        "airport_id",
        "airport_assessment_id",
        "risk_id",
        "aircraft_state_version",
        "evaluation_version",
        "assessment_ruleset_version",
        "schema_version",
        "candidate_reason",
    )
    if any(not isinstance(row.get(name), str) or not row.get(name) for name in required_text):
        return None
    if not _decimal_ok(row.get("distance_nm")) or not _decimal_ok(row.get("eta_minutes")):
        return None
    if not _score_shape_ok(row):
        return None
    if row.get("weather_risk_level") is not None and not isinstance(row.get("weather_risk_level"), str):
        return None
    periods = row.get("taf_period_ids", [])
    if not isinstance(periods, list) or any(not isinstance(item, str) for item in periods):
        return None
    if not isinstance(row.get("known_limitations"), list) or any(
        not isinstance(item, str) or not item for item in row["known_limitations"]
    ):
        return None
    return row


def _one_generation(rows: list[dict]) -> bool:
    return (
        len({row.get("created_at_utc") for row in rows}) == 1
        and len({_epoch(row.get("created_at_epoch")) for row in rows}) == 1
        and len({_epoch(row.get("expires_at_epoch")) for row in rows}) == 1
    )


def _versions_agree(rows: list[dict]) -> bool:
    return all(
        len({row.get(name) for row in rows}) == 1
        for name in (
            "aircraft_state_version",
            "evaluation_version",
            "assessment_ruleset_version",
            "schema_version",
        )
    )


def _snapshots(recommendation: dict) -> dict[str, dict] | None:
    snapshots: dict[str, dict] = {}

    def note(assessment_id: Any, field_name: str, value: Any) -> bool:
        copied = _id_copy(assessment_id)
        if copied is None or copied == "malformed":
            return False
        current = snapshots.setdefault(copied, {})
        if field_name in current and not _values_equal(current[field_name], value):
            return False
        current[field_name] = value
        return True

    summaries = recommendation.get("candidate_airport_summaries")
    if summaries is not None:
        if not isinstance(summaries, list):
            return None
        for summary in summaries:
            if not isinstance(summary, dict):
                return None
            assessment_id = summary.get("airport_assessment_id")
            for field_name in (
                "airport_id",
                "rank",
                "total_airport_score",
                "distance_nm",
                "eta_minutes",
                "weather_risk_level",
            ):
                if field_name in summary and not note(
                    assessment_id,
                    field_name,
                    summary[field_name],
                ):
                    return None
    references = recommendation.get("evidence_references")
    if isinstance(references, list):
        for item in references:
            if not isinstance(item, dict) or item.get("type") != _ASSESSMENT_REFERENCE:
                continue
            if "airport_id" in item and not note(item.get("id"), "airport_id", item["airport_id"]):
                return None
            if _id_copy(item.get("id")) in {None, "malformed"}:
                return None
            snapshots.setdefault(_id_copy(item.get("id")), {})
    preferred_id = recommendation.get("preferred_airport_assessment_id")
    preferred_airport = recommendation.get("preferred_airport_id")
    preferred_score = recommendation.get("preferred_airport_score")
    preferred_present = [
        recommendation.get(name) is not None
        for name in (
            "preferred_airport_id",
            "preferred_airport_assessment_id",
            "preferred_airport_score",
        )
    ]
    if any(preferred_present) and not all(preferred_present):
        return None
    if all(preferred_present):
        if not note(preferred_id, "airport_id", preferred_airport):
            return None
        if not note(preferred_id, "total_airport_score", preferred_score):
            return None
    alternatives = recommendation.get("alternative_actions")
    if alternatives is not None:
        if not isinstance(alternatives, list):
            return None
        for action in alternatives:
            if not isinstance(action, dict):
                return None
            assessment_id = action.get("airport_assessment_id")
            for source_name, field_name in (
                ("airport_id", "airport_id"),
                ("score", "total_airport_score"),
                ("rank", "rank"),
            ):
                if source_name in action and not note(
                    assessment_id,
                    field_name,
                    action[source_name],
                ):
                    return None
    return snapshots


def _values_equal(left: Any, right: Any) -> bool:
    if _decimal_ok(left) and _decimal_ok(right):
        return numbers_equal(left, right)
    return left == right


def _snapshot_matches(row: dict, fields: dict) -> bool:
    mapping = {
        "airport_id": row.get("airport_id"),
        "rank": row.get("rank"),
        "total_airport_score": row.get("total_airport_score"),
        "distance_nm": row.get("distance_nm"),
        "eta_minutes": row.get("eta_minutes"),
        "weather_risk_level": row.get("weather_risk_level"),
    }
    for field_name, expected in fields.items():
        actual = mapping.get(field_name)
        if _decimal_ok(expected):
            if not _decimal_ok(actual) or not numbers_equal(expected, actual):
                return False
        elif actual != expected:
            return False
    return True


def _ordered(rows: list[dict]) -> list[dict]:
    ranked = [
        row
        for row in rows
        if row.get("assessment_status") == COMPLETE and row.get("rank") is not None
    ]
    ranked_ids = {id(row) for row in ranked}
    rest = [row for row in rows if id(row) not in ranked_ids]
    ranked.sort(key=lambda row: _json_number(row["rank"], "rank"))
    rest.sort(key=lambda row: row["airport_id"])
    return ranked + rest


def _to_candidate(row: dict) -> PersistedAirportCandidate:
    def optional(name: str):
        if name not in row or row[name] is None:
            return None
        if name in {"estimated_arrival_time_utc", "weather_risk_level", "metar_version", "taf_version"}:
            return row[name] if isinstance(row[name], str) else None
        return _json_number(row[name], name)

    periods = row.get("taf_period_ids") or []
    return PersistedAirportCandidate(
        airport_id=row["airport_id"],
        airport_assessment_id=row["airport_assessment_id"],
        risk_id=row["risk_id"],
        assessment_status=row["assessment_status"],
        route_safety_status=UNAVAILABLE,
        runway_evidence_status=UNAVAILABLE,
        congestion_evidence_status=UNAVAILABLE,
        created_at_utc=row["created_at_utc"],
        created_at_epoch=_json_number(row["created_at_epoch"], "created_at_epoch"),
        expires_at_epoch=_json_number(row["expires_at_epoch"], "expires_at_epoch"),
        evaluation_version=row["evaluation_version"],
        assessment_ruleset_version=row["assessment_ruleset_version"],
        schema_version=row["schema_version"],
        distance_nm=_json_number(row["distance_nm"], "distance_nm"),
        eta_minutes=_json_number(row["eta_minutes"], "eta_minutes"),
        candidate_reason=row["candidate_reason"],
        known_limitations=tuple(row["known_limitations"]),
        rank=optional("rank"),
        total_airport_score=optional("total_airport_score"),
        distance_score=optional("distance_score"),
        weather_score=optional("weather_score"),
        taf_score=optional("taf_score"),
        estimated_arrival_time_utc=optional("estimated_arrival_time_utc"),
        weather_risk_level=optional("weather_risk_level"),
        metar_version=optional("metar_version"),
        taf_version=optional("taf_version"),
        taf_period_ids=tuple(item for item in periods if isinstance(item, str)),
    )


def _unknown(call, recommendation, recommendation_id, evaluation_id=None):
    return _result(
        call,
        ToolResultStatus.UNKNOWN,
        _empty_payload(recommendation_id, evaluation_id),
        (_recommendation_evidence(call, recommendation, recommendation_id),),
        (_INCONSISTENT,),
        as_of_utc=None,
    )


def _empty_payload(recommendation_id: str, evaluation_id: str | None = None):
    return PersistedAirportEvidence(
        recommendation_id=recommendation_id or "unknown",
        airport_evaluation_id=evaluation_id,
        candidates=(),
    )


def _recommendation_evidence(call, recommendation, recommendation_id) -> Evidence:
    version = recommendation.get("recommendation_version")
    created = recommendation.get("created_at_utc")
    return _evidence(
        call,
        "get_recommendation_record",
        (
            SourceRecord(
                record_id=recommendation_id,
                source_version=version if isinstance(version, str) and version else None,
                event_timestamp_utc=(
                    created if isinstance(created, str) and _parse_utc(created) else None
                ),
            ),
        ),
    )


def _assessment_evidence(call, candidates: tuple[PersistedAirportCandidate, ...]) -> Evidence:
    return _evidence(
        call,
        "query_airport_assessments_for_evaluation",
        tuple(
            SourceRecord(
                record_id=item.airport_assessment_id,
                source_version=item.schema_version,
                event_timestamp_utc=item.created_at_utc,
            )
            for item in candidates
        ),
    )


def _evidence(call, source: str, records: tuple[SourceRecord, ...]) -> Evidence:
    return Evidence(
        source=source,
        source_records=records,
        query_timestamp_utc=call.query_timestamp_utc,
        freshness_status=FreshnessStatus.UNKNOWN,
        confidence=ConfidenceLevel.UNKNOWN,
        limitations=(NOT_CURRENT_LIMITATION,),
        tool_call_id=call.tool_call_id,
        temporal_scope=TemporalScope.PERSISTED,
    )


def _result(call, status, payload, evidence, limitations, *, as_of_utc):
    result = ToolResult(
        tool_name=TOOL_NAME,
        tool_call_id=call.tool_call_id,
        status=status,
        temporal_scope=TemporalScope.PERSISTED,
        data=payload.to_dict(),
        evidence=evidence,
        as_of_utc=as_of_utc,
        limitations=limitations,
        correlation_id=call.correlation_id,
    )
    validate_persisted_airport_tool_result(result)
    return result


def _parse_utc(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(None):
        return None
    return parsed.astimezone(timezone.utc)


def _epoch(value: Any) -> int | None:
    if isinstance(value, bool) or not _decimal_ok(value):
        return None
    number = _json_number(value, "epoch")
    if type(number) is not int:
        return None
    return number
