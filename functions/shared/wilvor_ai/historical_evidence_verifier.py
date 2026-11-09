"""Deterministic historical claim verifier (Phase 3A.3).

Proposed claims are untrusted assertions. Full ToolResult and first-class
Evidence are the only verification authority. This module does not render
answers, call a model, or import AWS/provider SDKs.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Mapping

from wilvor_ai.contracts import (
    ContractValidationError,
    Evidence,
    JsonValue,
    TemporalScope,
    ToolResult,
    ToolResultStatus,
    _parse_enum,
    _required,
    _sequence,
    _validate_string_tuple,
    _validate_utc,
)
from wilvor_ai.historical_analytics_mapping import (
    MAPPING_INTEGRITY_FAILED,
    RESULT_TRUNCATED_LIMITATION,
)
from wilvor_ai.specialist_contracts import (
    ExactCountClaim,
    HistoricalWindowClaim,
    LimitationClaim,
    LowerBoundCountClaim,
    RecordIdentityClaim,
    SpecialistClaim,
    SpecialistStatus,
    UnavailableClaim,
    VerifiedZeroClaim,
    VerifierOutcome,
    _is_specialist_claim,
    specialist_claim_from_dict,
)
from wilvor_ai.specialist_runtime_contracts import (
    HistoricalSpecialistRunResult,
    SpecialistRunStatus,
)
from wilvor_historical.coverage_contracts import Evaluability
from wilvor_historical.query_contracts import HAZARD_VERSION_WINDOW_LIMITATION


HISTORICAL_VERIFICATION_SCHEMA_VERSION = "wilvor.ai.historical_verification.v1"
CLAIM_VERIFICATION_FAILED = "CLAIM_VERIFICATION_FAILED"
CLAIM_SET_NOT_ACTIONABLE = "CLAIM_SET_NOT_ACTIONABLE"
RUN_STATE_MALFORMED = "RUN_STATE_MALFORMED"

LIST_HISTORICAL_ENCOUNTERS = "list_historical_encounters"
SUMMARIZE_ENCOUNTERS = "summarize_historical_encounters"
SUMMARIZE_RISKS = "summarize_historical_risks"
SUMMARIZE_HAZARD_VERSIONS = "summarize_historical_hazard_versions"
LOWER_BOUND_METRIC_ID = "minimum_count"
EVALUABLE_STATUS = Evaluability.EVALUABLE.value

ENCOUNTER_EXACT_METRICS = frozenset(
    {
        "physical_record_count",
        "distinct_encounter_count",
        "distinct_aircraft_count",
        "distinct_hazard_count",
        "distinct_dedup_count",
    }
)
RISK_EXACT_METRICS = frozenset(
    {
        "physical_record_count",
        "distinct_risk_count",
        "distinct_encounter_count",
        "distinct_aircraft_count",
    }
)
HAZARD_VERSION_EXACT_METRICS = frozenset(
    {
        "physical_record_count",
        "distinct_hazard_count",
        "distinct_hazard_version_count",
    }
)
EXACT_METRICS_BY_OPERATION = {
    SUMMARIZE_ENCOUNTERS: ENCOUNTER_EXACT_METRICS,
    SUMMARIZE_RISKS: RISK_EXACT_METRICS,
    SUMMARIZE_HAZARD_VERSIONS: HAZARD_VERSION_EXACT_METRICS,
    LIST_HISTORICAL_ENCOUNTERS: frozenset(),
}
ALL_EXACT_METRICS = frozenset().union(*EXACT_METRICS_BY_OPERATION.values())
ALL_KNOWN_METRICS = ALL_EXACT_METRICS | {LOWER_BOUND_METRIC_ID}

FACTUAL_CLAIM_TYPES = (
    ExactCountClaim,
    LowerBoundCountClaim,
    VerifiedZeroClaim,
    RecordIdentityClaim,
    UnavailableClaim,
)

MANDATORY_LIMITATION_CODES = (
    RESULT_TRUNCATED_LIMITATION,
    HAZARD_VERSION_WINDOW_LIMITATION,
    MAPPING_INTEGRITY_FAILED,
    CLAIM_VERIFICATION_FAILED,
    CLAIM_SET_NOT_ACTIONABLE,
    RUN_STATE_MALFORMED,
)


class ClaimRejectionCode(str):
    UNKNOWN_TOOL_CALL_ID = "UNKNOWN_TOOL_CALL_ID"
    DUPLICATE_TOOL_CALL_ID = "DUPLICATE_TOOL_CALL_ID"
    DUPLICATE_CLAIM = "DUPLICATE_CLAIM"
    CONTRADICTORY_CLAIMS = "CONTRADICTORY_CLAIMS"
    UNKNOWN_METRIC = "UNKNOWN_METRIC"
    METRIC_OPERATION_MISMATCH = "METRIC_OPERATION_MISMATCH"
    EXACT_COUNT_STATUS_UNSUPPORTED = "EXACT_COUNT_STATUS_UNSUPPORTED"
    EXACT_COUNT_VALUE_MISMATCH = "EXACT_COUNT_VALUE_MISMATCH"
    LOWER_BOUND_STATUS_UNSUPPORTED = "LOWER_BOUND_STATUS_UNSUPPORTED"
    LOWER_BOUND_VALUE_MISMATCH = "LOWER_BOUND_VALUE_MISMATCH"
    LOWER_BOUND_CARDINALITY_INVALID = "LOWER_BOUND_CARDINALITY_INVALID"
    VERIFIED_ZERO_PROOF_FAILED = "VERIFIED_ZERO_PROOF_FAILED"
    WINDOW_MISMATCH = "WINDOW_MISMATCH"
    RECORD_IDENTITY_UNSUPPORTED_OPERATION = "RECORD_IDENTITY_UNSUPPORTED_OPERATION"
    RECORD_IDENTITY_NOT_FOUND = "RECORD_IDENTITY_NOT_FOUND"
    LIMITATION_CODE_ABSENT = "LIMITATION_CODE_ABSENT"
    UNAVAILABLE_STATUS_UNSUPPORTED = "UNAVAILABLE_STATUS_UNSUPPORTED"
    UNAVAILABLE_CODE_MISMATCH = "UNAVAILABLE_CODE_MISMATCH"
    NON_HISTORICAL_TOOL = "NON_HISTORICAL_TOOL"
    COMPLETENESS_NOT_EVALUABLE = "COMPLETENESS_NOT_EVALUABLE"
    CLAIM_SET_NOT_ACTIONABLE = CLAIM_SET_NOT_ACTIONABLE
    EMPTY_CLAIM_SET = "EMPTY_CLAIM_SET"
    RUN_STATE_MALFORMED = RUN_STATE_MALFORMED


@dataclass(frozen=True)
class HistoricalVerificationResult:
    """Deterministic verification artifact. Not a factual SpecialistResult."""

    outcome: VerifierOutcome
    verified_claims: tuple[SpecialistClaim, ...]
    rejected_claim_codes: tuple[str, ...]
    mandatory_limitations: tuple[str, ...]
    derived_specialist_status: SpecialistStatus | None
    evaluated_as_of_utc: str | None
    used_tool_call_ids: tuple[str, ...]
    schema_version: str = HISTORICAL_VERIFICATION_SCHEMA_VERSION

    def __post_init__(self) -> None:
        errors: list[str] = []
        if self.schema_version != HISTORICAL_VERIFICATION_SCHEMA_VERSION:
            errors.append("invalid_schema_version")
        if not isinstance(self.outcome, VerifierOutcome):
            errors.append("invalid_outcome")
        if not isinstance(self.verified_claims, tuple) or any(
            not _is_specialist_claim(item) for item in self.verified_claims
        ):
            errors.append("invalid_verified_claims")
        errors.extend(
            _validate_string_tuple(self.rejected_claim_codes, "rejected_claim_codes")
        )
        errors.extend(
            _validate_string_tuple(self.mandatory_limitations, "mandatory_limitations")
        )
        errors.extend(
            _validate_string_tuple(self.used_tool_call_ids, "used_tool_call_ids")
        )
        errors.extend(
            _validate_utc(
                self.evaluated_as_of_utc,
                "evaluated_as_of_utc",
                optional=True,
            )
        )
        if (
            self.derived_specialist_status is not None
            and not isinstance(self.derived_specialist_status, SpecialistStatus)
        ):
            errors.append("invalid_derived_specialist_status")
        if self.outcome is VerifierOutcome.PASSED:
            if not self.verified_claims:
                errors.append("passed_requires_verified_claims")
            if self.derived_specialist_status is None:
                errors.append("passed_requires_derived_status")
            if self.rejected_claim_codes:
                errors.append("passed_forbids_rejections")
        if self.outcome is VerifierOutcome.FAILED and self.verified_claims:
            errors.append("failed_forbids_verified_claims")
        if errors:
            raise ContractValidationError(errors)

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "schema_version": self.schema_version,
            "outcome": self.outcome.value,
            "verified_claims": [item.to_dict() for item in self.verified_claims],
            "rejected_claim_codes": list(self.rejected_claim_codes),
            "mandatory_limitations": list(self.mandatory_limitations),
            "derived_specialist_status": (
                None
                if self.derived_specialist_status is None
                else self.derived_specialist_status.value
            ),
            "evaluated_as_of_utc": self.evaluated_as_of_utc,
            "used_tool_call_ids": list(self.used_tool_call_ids),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "HistoricalVerificationResult":
        if not isinstance(data, Mapping):
            raise ContractValidationError("invalid_historical_verification_result")
        derived = data["derived_specialist_status"]
        return cls(
            outcome=_parse_enum(VerifierOutcome, _required(data, "outcome"), "outcome"),
            verified_claims=tuple(
                specialist_claim_from_dict(item)
                for item in _sequence(
                    _required(data, "verified_claims"),
                    "verified_claims",
                )
            ),
            rejected_claim_codes=tuple(
                _sequence(_required(data, "rejected_claim_codes"), "rejected_claim_codes")
            ),
            mandatory_limitations=tuple(
                _sequence(
                    _required(data, "mandatory_limitations"),
                    "mandatory_limitations",
                )
            ),
            derived_specialist_status=(
                None
                if derived is None
                else _parse_enum(SpecialistStatus, derived, "derived_specialist_status")
            ),
            evaluated_as_of_utc=_required(data, "evaluated_as_of_utc"),
            used_tool_call_ids=tuple(
                _sequence(_required(data, "used_tool_call_ids"), "used_tool_call_ids")
            ),
            schema_version=_required(data, "schema_version"),
        )


def verify_historical_specialist_run(
    run_result: HistoricalSpecialistRunResult,
) -> HistoricalVerificationResult:
    """Verify untrusted proposed claims against full ToolResults."""

    if not isinstance(run_result, HistoricalSpecialistRunResult):
        raise TypeError("run_result must be a HistoricalSpecialistRunResult")
    if run_result.status is not SpecialistRunStatus.PROPOSED_CLAIMS:
        return HistoricalVerificationResult(
            outcome=VerifierOutcome.NOT_RUN,
            verified_claims=(),
            rejected_claim_codes=(),
            mandatory_limitations=(),
            derived_specialist_status=None,
            evaluated_as_of_utc=run_result.evaluated_as_of_utc,
            used_tool_call_ids=(),
        )
    if not run_result.proposed_claims:
        return _failed((ClaimRejectionCode.EMPTY_CLAIM_SET,), run_result)

    indexed = _index_tool_results(run_result.tool_results)
    if indexed is None:
        return _failed((ClaimRejectionCode.DUPLICATE_TOOL_CALL_ID,), run_result)

    codes: list[str] = []
    if not any(isinstance(item, FACTUAL_CLAIM_TYPES) for item in run_result.proposed_claims):
        codes.append(ClaimRejectionCode.CLAIM_SET_NOT_ACTIONABLE)

    seen_keys: set[str] = set()
    for claim in run_result.proposed_claims:
        key = _claim_key(claim)
        if key in seen_keys:
            codes.append(ClaimRejectionCode.DUPLICATE_CLAIM)
            break
        seen_keys.add(key)

    codes.extend(_contradiction_codes(run_result.proposed_claims, indexed))

    if not codes:
        for claim in run_result.proposed_claims:
            rejected = _verify_one(claim, indexed)
            if rejected is not None:
                codes.append(rejected)
                break

    if codes:
        return _failed(tuple(dict.fromkeys(codes)), run_result)

    used_ids = tuple(
        dict.fromkeys(claim.tool_call_id for claim in run_result.proposed_claims)
    )
    used_tools = tuple(indexed[item] for item in used_ids)
    as_of_error = _used_as_of_error(used_tools, run_result.evaluated_as_of_utc)
    if as_of_error is not None:
        return _failed((as_of_error,), run_result)
    if any(item.temporal_scope is not TemporalScope.HISTORICAL for item in used_tools):
        return _failed((ClaimRejectionCode.NON_HISTORICAL_TOOL,), run_result)

    status = _derived_status(used_tools, run_result.proposed_claims)
    limitations = _mandatory_limitations(used_tools, extra=())
    return HistoricalVerificationResult(
        outcome=VerifierOutcome.PASSED,
        verified_claims=run_result.proposed_claims,
        rejected_claim_codes=(),
        mandatory_limitations=limitations,
        derived_specialist_status=status,
        evaluated_as_of_utc=run_result.evaluated_as_of_utc,
        used_tool_call_ids=used_ids,
    )


def _failed(
    codes: tuple[str, ...],
    run_result: HistoricalSpecialistRunResult,
) -> HistoricalVerificationResult:
    extra = (
        CLAIM_SET_NOT_ACTIONABLE
        if CLAIM_SET_NOT_ACTIONABLE in codes
        else CLAIM_VERIFICATION_FAILED
    )
    return HistoricalVerificationResult(
        outcome=VerifierOutcome.FAILED,
        verified_claims=(),
        rejected_claim_codes=codes,
        mandatory_limitations=_ordered_limitations((extra,)),
        derived_specialist_status=SpecialistStatus.UNAVAILABLE,
        evaluated_as_of_utc=run_result.evaluated_as_of_utc,
        used_tool_call_ids=(),
    )


def _index_tool_results(
    results: tuple[ToolResult, ...],
) -> dict[str, ToolResult] | None:
    indexed: dict[str, ToolResult] = {}
    for item in results:
        if item.tool_call_id in indexed:
            return None
        indexed[item.tool_call_id] = item
    return indexed


def _claim_key(claim: SpecialistClaim) -> str:
    return json.dumps(claim.to_dict(), sort_keys=True, separators=(",", ":"))


def _contradiction_codes(
    claims: tuple[SpecialistClaim, ...],
    indexed: dict[str, ToolResult],
) -> list[str]:
    by_tool: dict[str, list[SpecialistClaim]] = {}
    for claim in claims:
        by_tool.setdefault(claim.tool_call_id, []).append(claim)
    codes: list[str] = []
    for tool_claims in by_tool.values():
        zeros = [item for item in tool_claims if isinstance(item, VerifiedZeroClaim)]
        exacts = [item for item in tool_claims if isinstance(item, ExactCountClaim)]
        lowers = [item for item in tool_claims if isinstance(item, LowerBoundCountClaim)]
        records = [item for item in tool_claims if isinstance(item, RecordIdentityClaim)]
        if zeros and any(item.value > 0 for item in exacts):
            codes.append(ClaimRejectionCode.CONTRADICTORY_CLAIMS)
        if zeros and records:
            codes.append(ClaimRejectionCode.CONTRADICTORY_CLAIMS)
        if exacts and lowers:
            codes.append(ClaimRejectionCode.CONTRADICTORY_CLAIMS)
    return list(dict.fromkeys(codes))


def _verify_one(claim: SpecialistClaim, indexed: dict[str, ToolResult]) -> str | None:
    result = indexed.get(claim.tool_call_id)
    if result is None:
        return ClaimRejectionCode.UNKNOWN_TOOL_CALL_ID
    if result.temporal_scope is not TemporalScope.HISTORICAL:
        return ClaimRejectionCode.NON_HISTORICAL_TOOL
    if isinstance(claim, ExactCountClaim):
        return _verify_exact_count(claim, result)
    if isinstance(claim, LowerBoundCountClaim):
        return _verify_lower_bound(claim, result)
    if isinstance(claim, VerifiedZeroClaim):
        return _verify_zero(result)
    if isinstance(claim, HistoricalWindowClaim):
        return _verify_window(claim, result)
    if isinstance(claim, RecordIdentityClaim):
        return _verify_record_identity(claim, result)
    if isinstance(claim, LimitationClaim):
        return _verify_limitation(claim, result)
    if isinstance(claim, UnavailableClaim):
        return _verify_unavailable(claim, result)
    return ClaimRejectionCode.RUN_STATE_MALFORMED


def _certified_historical_evaluable(result: ToolResult) -> str | None:
    """Fact-bearing claims require HISTORICAL + EVALUABLE completeness."""

    if result.temporal_scope is not TemporalScope.HISTORICAL:
        return ClaimRejectionCode.NON_HISTORICAL_TOOL
    evidence = _primary_evidence(result)
    completeness = None if evidence is None else evidence.completeness
    if completeness is None or completeness.status != EVALUABLE_STATUS:
        return ClaimRejectionCode.COMPLETENESS_NOT_EVALUABLE
    return None


def _verify_exact_count(claim: ExactCountClaim, result: ToolResult) -> str | None:
    if result.status is not ToolResultStatus.SUCCESS:
        return ClaimRejectionCode.EXACT_COUNT_STATUS_UNSUPPORTED
    certified = _certified_historical_evaluable(result)
    if certified is not None:
        return certified
    if claim.metric_id not in ALL_KNOWN_METRICS:
        return ClaimRejectionCode.UNKNOWN_METRIC
    allowed = EXACT_METRICS_BY_OPERATION.get(result.tool_name, frozenset())
    if claim.metric_id not in allowed:
        return ClaimRejectionCode.METRIC_OPERATION_MISMATCH
    payload = _result_payload(result)
    if not isinstance(payload, dict) or claim.metric_id not in payload:
        return ClaimRejectionCode.METRIC_OPERATION_MISMATCH
    actual = payload[claim.metric_id]
    if not isinstance(actual, int) or isinstance(actual, bool) or actual != claim.value:
        return ClaimRejectionCode.EXACT_COUNT_VALUE_MISMATCH
    return None


def _verify_lower_bound(claim: LowerBoundCountClaim, result: ToolResult) -> str | None:
    if result.status is not ToolResultStatus.PARTIAL:
        return ClaimRejectionCode.LOWER_BOUND_STATUS_UNSUPPORTED
    certified = _certified_historical_evaluable(result)
    if certified is not None:
        return certified
    if claim.metric_id not in ALL_KNOWN_METRICS:
        return ClaimRejectionCode.UNKNOWN_METRIC
    if (
        result.tool_name != LIST_HISTORICAL_ENCOUNTERS
        or claim.metric_id != LOWER_BOUND_METRIC_ID
    ):
        return ClaimRejectionCode.METRIC_OPERATION_MISMATCH
    cardinality = _primary_cardinality(result)
    if (
        cardinality is None
        or cardinality.is_exact is not False
        or cardinality.exact_count is not None
        or cardinality.minimum_count is None
    ):
        return ClaimRejectionCode.LOWER_BOUND_CARDINALITY_INVALID
    if claim.minimum_value != cardinality.minimum_count:
        return ClaimRejectionCode.LOWER_BOUND_VALUE_MISMATCH
    return None


def _verify_zero(result: ToolResult) -> str | None:
    evidence = _primary_evidence(result)
    completeness = None if evidence is None else evidence.completeness
    cardinality = None if evidence is None else evidence.match_cardinality
    if (
        result.status is not ToolResultStatus.NOT_FOUND
        or evidence is None
        or completeness is None
        or completeness.status != EVALUABLE_STATUS
        or cardinality is None
        or cardinality.exact_count != 0
        or cardinality.is_exact is not True
        or cardinality.minimum_count is not None
    ):
        return ClaimRejectionCode.VERIFIED_ZERO_PROOF_FAILED
    return None


def _verify_window(claim: HistoricalWindowClaim, result: ToolResult) -> str | None:
    scope = _requested_scope(result)
    if not isinstance(scope, dict):
        return ClaimRejectionCode.WINDOW_MISMATCH
    if (
        scope.get("start_utc") != claim.start_utc
        or scope.get("end_utc") != claim.end_utc
    ):
        return ClaimRejectionCode.WINDOW_MISMATCH
    return None


def _verify_record_identity(claim: RecordIdentityClaim, result: ToolResult) -> str | None:
    if result.tool_name != LIST_HISTORICAL_ENCOUNTERS:
        return ClaimRejectionCode.RECORD_IDENTITY_UNSUPPORTED_OPERATION
    if result.status not in {ToolResultStatus.SUCCESS, ToolResultStatus.PARTIAL}:
        return ClaimRejectionCode.RECORD_IDENTITY_UNSUPPORTED_OPERATION
    certified = _certified_historical_evaluable(result)
    if certified is not None:
        return certified
    payload = _result_payload(result)
    records = payload.get("records") if isinstance(payload, dict) else None
    if not isinstance(records, list):
        return ClaimRejectionCode.RECORD_IDENTITY_NOT_FOUND
    data_ids = {
        item.get("record_id")
        for item in records
        if isinstance(item, dict)
    }
    evidence = _primary_evidence(result)
    source_ids = (
        {item.record_id for item in evidence.source_records}
        if evidence is not None
        else set()
    )
    if claim.record_id not in data_ids or claim.record_id not in source_ids:
        return ClaimRejectionCode.RECORD_IDENTITY_NOT_FOUND
    return None


def _verify_limitation(claim: LimitationClaim, result: ToolResult) -> str | None:
    present = _limitation_codes(result)
    if claim.limitation_code not in present:
        return ClaimRejectionCode.LIMITATION_CODE_ABSENT
    return None


def _verify_unavailable(claim: UnavailableClaim, result: ToolResult) -> str | None:
    if result.status is not ToolResultStatus.UNAVAILABLE:
        return ClaimRejectionCode.UNAVAILABLE_STATUS_UNSUPPORTED
    codes = _unavailable_codes(result)
    if claim.error_or_coverage_code not in codes:
        return ClaimRejectionCode.UNAVAILABLE_CODE_MISMATCH
    return None


def _result_payload(result: ToolResult) -> Any:
    data = result.data
    if not isinstance(data, dict):
        return None
    return data.get("result")


def _requested_scope(result: ToolResult) -> Any:
    data = result.data
    if not isinstance(data, dict):
        return None
    return data.get("requested_scope")


def _primary_evidence(result: ToolResult) -> Evidence | None:
    if not result.evidence:
        return None
    return result.evidence[0]


def _primary_cardinality(result: ToolResult):
    evidence = _primary_evidence(result)
    if evidence is None:
        return None
    return evidence.match_cardinality


def _limitation_codes(result: ToolResult) -> set[str]:
    codes = set(result.limitations)
    for evidence in result.evidence:
        codes.update(evidence.limitations)
    return codes


def _unavailable_codes(result: ToolResult) -> set[str]:
    codes = _limitation_codes(result)
    evidence = _primary_evidence(result)
    if evidence is not None:
        if evidence.error_code is not None:
            codes.add(evidence.error_code)
        if evidence.completeness is not None and evidence.completeness.reason:
            codes.add(evidence.completeness.reason)
    data = result.data if isinstance(result.data, dict) else {}
    error = data.get("error")
    if isinstance(error, dict) and isinstance(error.get("code"), str):
        codes.add(error["code"])
    return codes


def _used_as_of_error(
    used_tools: tuple[ToolResult, ...],
    evaluated_as_of_utc: str | None,
) -> str | None:
    populated = tuple(item.as_of_utc for item in used_tools if item.as_of_utc is not None)
    if not populated:
        return None
    unique = set(populated)
    if len(unique) != 1:
        return ClaimRejectionCode.RUN_STATE_MALFORMED
    if evaluated_as_of_utc != next(iter(unique)):
        return ClaimRejectionCode.RUN_STATE_MALFORMED
    return None


def _derived_status(
    used_tools: tuple[ToolResult, ...],
    claims: tuple[SpecialistClaim, ...],
) -> SpecialistStatus:
    if any(
        item.status in {ToolResultStatus.UNAVAILABLE, ToolResultStatus.UNKNOWN}
        for item in used_tools
    ) or any(isinstance(item, UnavailableClaim) for item in claims):
        return SpecialistStatus.UNAVAILABLE
    if any(
        item.status is ToolResultStatus.PARTIAL
        or RESULT_TRUNCATED_LIMITATION in item.limitations
        for item in used_tools
    ):
        return SpecialistStatus.PARTIAL
    return SpecialistStatus.ANSWERED


def _mandatory_limitations(
    used_tools: tuple[ToolResult, ...],
    *,
    extra: tuple[str, ...],
) -> tuple[str, ...]:
    collected: list[str] = []
    for item in used_tools:
        for code in item.limitations:
            if code in {
                RESULT_TRUNCATED_LIMITATION,
                HAZARD_VERSION_WINDOW_LIMITATION,
                MAPPING_INTEGRITY_FAILED,
            }:
                collected.append(code)
        if item.status is ToolResultStatus.PARTIAL:
            collected.append(RESULT_TRUNCATED_LIMITATION)
    collected.extend(extra)
    return _ordered_limitations(tuple(collected))


def _ordered_limitations(codes: tuple[str, ...]) -> tuple[str, ...]:
    unique = tuple(dict.fromkeys(codes))
    ranked = [item for item in MANDATORY_LIMITATION_CODES if item in unique]
    rest = [item for item in unique if item not in MANDATORY_LIMITATION_CODES]
    return tuple(ranked + rest)


__all__ = [
    "ALL_EXACT_METRICS",
    "ALL_KNOWN_METRICS",
    "CLAIM_SET_NOT_ACTIONABLE",
    "CLAIM_VERIFICATION_FAILED",
    "ClaimRejectionCode",
    "EXACT_METRICS_BY_OPERATION",
    "HISTORICAL_VERIFICATION_SCHEMA_VERSION",
    "HistoricalVerificationResult",
    "LOWER_BOUND_METRIC_ID",
    "verify_historical_specialist_run",
]
