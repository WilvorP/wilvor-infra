"""Master-to-Historical native request and result bridge.

The bridge preserves one host invocation record and, when a native result
is supplied, an unauthenticated observation of that result. It does not
execute Historical Analytics, call a model provider, construct AWS or
Athena clients, or call ``finalize_historical_specialist_run``.

Clocks stay distinct:

- ``application_request_time_utc`` is when the application accepted the request.
- ``requested_evaluation_time_utc`` is the evaluation instant the plan asked
  for. It is not evidence and it is not copied into ``evaluated_as_of_utc``.
- ``actual_runtime_evaluation_time_utc`` is copied only from
  ``HistoricalSpecialistRunResult.evaluated_as_of_utc`` when a run is
  observed. A missing native value stays missing.
- ``unbound_plan_window_start_utc`` and ``unbound_plan_window_end_utc`` are
  the Master plan window. They are not native tool arguments.

``HistoricalSpecialistRunResult`` remains an intermediate result.
``SpecialistResult`` remains the final artifact type. This module does not
produce either one. A caller-supplied native run or final result is not
authenticated execution and is not admitted evidence.

Sprint 3B.2 native-contract migration, not performed on this branch:

- ``HistoricalSpecialistRunResult`` and ``SpecialistResult`` still omit
  ``master_request_id``, ``invocation_id``, ``specialist_request_id``,
  ``correlation_id``, ``application_request_time_utc``,
  ``requested_evaluation_time_utc``, ``source_version``, and ``query_id``.
  Those identities stay on the host record until a native contract can
  carry them from the specialist runtime.
- ``SpecialistRequest`` has no historical window arguments.
  ``HistoricalSpecialistTrustedContext`` has only ``as_of_utc``.
- ``ToolResult.correlation_id`` may be present and is still not a
  result-level correlation contract. Historical mapping leaves it unset.
- ``source_version`` lives on ``SourceRecord``. Current historical mapping
  writes ``None`` for listed records and omits source records for summaries.
- ``query_id`` lives on nested ``QueryExecutionTrace`` only.
- No native type represents an independently authorized tool interval.
  Supplying ``IndependentHistoricalToolInterval`` only names eligible
  ``start_utc`` and ``end_utc`` arguments. It does not grant execution,
  authenticate a source, or copy the unbound plan window.
"""

from __future__ import annotations

from dataclasses import dataclass, fields
from datetime import datetime, timezone
from enum import Enum
from types import MappingProxyType

from wilvor_ai.contracts import (
    ContractValidationError,
    ToolResult,
    ToolResultStatus,
    _validate_text,
    _validate_utc,
)
from wilvor_ai.master_contracts import (
    MasterBindingDefect,
    MasterProductionAuthority,
    MasterSpecialist,
)
from wilvor_ai.master_evidence_admission import (
    MasterAdmissionReason,
    MasterSourceAuthenticity,
)
from wilvor_ai.master_historical_binding import (
    HISTORICAL_FINALIZATION_ENTRYPOINT,
    MasterHistoricalBinding,
    MasterHistoricalPlanScope,
)
from wilvor_ai.master_historical_execution_readiness import (
    MasterHistoricalExecutionReadiness,
    NativeContractFieldGap,
    assess_master_historical_execution_readiness,
    historical_native_result_correlation_gaps,
)
from wilvor_ai.master_parallel_contracts import MasterProvenanceRequirement
from wilvor_ai.master_routing import (
    MasterRequestCoverage,
    MasterRouteExecutionAuthority,
)
from wilvor_ai.master_specialist_adapters import MasterAdapterExecutionState
from wilvor_ai.model_contracts import ValidationFeedbackCode
from wilvor_ai.specialist_contracts import (
    HistoricalSpecialistTrustedContext,
    SpecialistRequest,
    SpecialistResult,
    VerifiedZeroClaim,
)
from wilvor_ai.specialist_runtime_contracts import HistoricalSpecialistRunResult
from wilvor_ai.tool_schema import FORBIDDEN_TOOL_SCHEMA_FIELD_NAMES


MASTER_HISTORICAL_NATIVE_BRIDGE_SCHEMA_VERSION = (
    "wilvor.ai.master_historical_native_bridge.v1"
)
INTERMEDIATE_RUN_TYPE = "HistoricalSpecialistRunResult"
FINAL_RESULT_TYPE = "SpecialistResult"
EVALUABLE_COMPLETENESS_STATUS = "EVALUABLE"
RESULT_TRUNCATED_MARKER = "RESULT_TRUNCATED"
_WINDOW_ARGUMENT_NAMES = ("start_utc", "end_utc")
_NATIVE_IDENTITY_FIELDS = (
    "master_request_id",
    "invocation_id",
    "specialist_request_id",
    "correlation_id",
    "application_request_time_utc",
    "requested_evaluation_time_utc",
    "source_version",
    "query_id",
)
_UNSUPPORTED_FEEDBACK = frozenset(
    {
        ValidationFeedbackCode.UNKNOWN_TOOL,
        ValidationFeedbackCode.UNKNOWN_ARGUMENT,
        ValidationFeedbackCode.TRUSTED_ARGUMENT_FORBIDDEN,
        ValidationFeedbackCode.INVALID_ARGUMENT_TYPE,
        ValidationFeedbackCode.MISSING_REQUIRED_ARGUMENT,
    }
)
_ZERO_COUNT_FIELDS = (
    "physical_record_count",
    "distinct_encounter_count",
    "distinct_aircraft_count",
    "distinct_hazard_count",
    "distinct_dedup_count",
    "distinct_risk_count",
    "distinct_hazard_version_count",
)

# Mirrors the Historical Analytics catalog. Tests lock this to the catalog
# so a native argument change becomes an explicit Sprint 3B.2 migration.
HISTORICAL_TOOL_ARGUMENT_ALLOWLIST = MappingProxyType(
    {
        "summarize_historical_encounters": frozenset(
            {"start_utc", "end_utc", "aircraft_id", "hazard_id", "hazard_type"}
        ),
        "summarize_historical_risks": frozenset(
            {
                "start_utc",
                "end_utc",
                "aircraft_id",
                "hazard_id",
                "encounter_id",
                "risk_level",
            }
        ),
        "summarize_historical_hazard_versions": frozenset(
            {"start_utc", "end_utc", "hazard_id", "hazard_type", "product_type"}
        ),
        "list_historical_encounters": frozenset(
            {"start_utc", "end_utc", "aircraft_id", "hazard_id", "limit"}
        ),
    }
)

SPRINT_3B2_NATIVE_CONTRACT_MIGRATION_NOTES = (
    "HistoricalSpecialistRunResult does not carry host correlation fields.",
    "SpecialistResult does not carry host correlation fields.",
    "SpecialistRequest does not accept historical window arguments.",
    "HistoricalSpecialistTrustedContext.as_of_utc is not source authentication.",
    "ToolResult.correlation_id does not satisfy a result-level correlation contract.",
    "source_version remains nested on SourceRecord and is not authenticated here.",
    "query_id remains nested on QueryExecutionTrace and is not a run field.",
    "evaluated_as_of_utc is not backfilled from requested_evaluation_time_utc.",
    "The unbound Master plan window is not a native tool argument.",
    "IndependentHistoricalToolInterval does not grant execution.",
    "finalize_historical_specialist_run remains the finalization entrypoint and is not called.",
)


class HistoricalToolArgumentSource(str, Enum):
    """Where eligible tool-window arguments came from. Never the plan window."""

    NONE = "NONE"
    INDEPENDENT_INTERVAL = "INDEPENDENT_INTERVAL"


class NativeObservationOrigin(str, Enum):
    """How a native result reached the bridge. Neither value authenticates it."""

    ABSENT = "ABSENT"
    CALLER_SUPPLIED = "CALLER_SUPPLIED"


class HistoricalRowObservation(str, Enum):
    """Structural reading of tool results. Not an admitted aviation conclusion."""

    NOT_OBSERVED = "NOT_OBSERVED"
    NO_ROWS = "NO_ROWS"
    VERIFIED_ZERO_SHAPE = "VERIFIED_ZERO_SHAPE"
    ROWS_PRESENT = "ROWS_PRESENT"


class HistoricalQueryObservation(str, Enum):
    """Partial and truncated queries stay distinct from a verified zero."""

    NOT_OBSERVED = "NOT_OBSERVED"
    PARTIAL_OR_TRUNCATED = "PARTIAL_OR_TRUNCATED"
    OBSERVED_NOT_PARTIAL = "OBSERVED_NOT_PARTIAL"


def _field_names(cls: type) -> frozenset[str]:
    return frozenset(item.name for item in fields(cls))


def _require_native_contract_gaps() -> None:
    """Fail closed if a native result starts carrying host correlation fields.

    Sprint 3B.2 must change this check when those contracts grow. This branch
    does not add the fields.
    """

    gaps = {
        item.contract_name: item.missing_fields
        for item in historical_native_result_correlation_gaps()
    }
    for contract_name, native_type in (
        (INTERMEDIATE_RUN_TYPE, HistoricalSpecialistRunResult),
        (FINAL_RESULT_TYPE, SpecialistResult),
    ):
        missing = gaps.get(contract_name)
        if missing is None:
            raise ContractValidationError("invalid_native_result_gap")
        present = _field_names(native_type)
        for name in _NATIVE_IDENTITY_FIELDS:
            if name in present:
                raise ContractValidationError("invalid_native_result_gap")
        for name in (
            "master_request_id",
            "specialist_request_id",
            "correlation_id",
            "application_request_time_utc",
            "requested_evaluation_time_utc",
            "source_version",
            "query_id",
        ):
            if name not in missing:
                raise ContractValidationError("invalid_native_result_gap")
    request_fields = _field_names(SpecialistRequest)
    if any(name in request_fields for name in _WINDOW_ARGUMENT_NAMES):
        raise ContractValidationError("historical_window_not_authorized")
    context_fields = _field_names(HistoricalSpecialistTrustedContext)
    if context_fields != frozenset({"as_of_utc"}):
        raise ContractValidationError("trusted_context_not_authorized")


_require_native_contract_gaps()


def _false(value: object, code: str) -> None:
    if value is not False:
        raise ContractValidationError(code)


def _true(value: object, code: str) -> None:
    if value is not True:
        raise ContractValidationError(code)


def _none(value: object, code: str) -> None:
    if value is not None:
        raise ContractValidationError(code)


def _closed(value: object, denied: object, code: str) -> None:
    if value is not denied:
        raise ContractValidationError(code)


def _utc_text(value: object, code: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ContractValidationError(code)
    if _validate_utc(value, "utc"):
        raise ContractValidationError(code)
    return value


def _instant(value: str) -> datetime:
    text = value[:-1] + "+00:00" if value.endswith("Z") else value
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(None):
        raise ContractValidationError("invalid_independent_historical_interval")
    return parsed


def _require_text(value: object, code: str) -> str:
    if _validate_text(value, "text"):
        raise ContractValidationError(code)
    if not isinstance(value, str) or value != value.strip():
        raise ContractValidationError(code)
    return value


def _tool_results(
    run: HistoricalSpecialistRunResult | None,
    result: SpecialistResult | None,
) -> tuple[ToolResult, ...]:
    found: list[ToolResult] = []
    for artifact in (run, result):
        if artifact is not None:
            found.extend(artifact.tool_results)
    return tuple(found)


def _primary_evidence(result: ToolResult):
    if not result.evidence:
        return None
    return result.evidence[0]


def _payload(result: ToolResult) -> dict[str, object] | None:
    data = result.data
    if not isinstance(data, dict):
        return None
    payload = data.get("result")
    if not isinstance(payload, dict):
        return None
    return payload


def _is_partial(result: ToolResult) -> bool:
    if result.status is ToolResultStatus.PARTIAL:
        return True
    if RESULT_TRUNCATED_MARKER in result.limitations:
        return True
    data = result.data
    return isinstance(data, dict) and data.get("status") == RESULT_TRUNCATED_MARKER


def _verified_zero_shape(result: ToolResult) -> bool:
    if _is_partial(result) or result.status is not ToolResultStatus.NOT_FOUND:
        return False
    evidence = _primary_evidence(result)
    if evidence is None or evidence.completeness is None or evidence.match_cardinality is None:
        return False
    cardinality = evidence.match_cardinality
    return (
        evidence.completeness.status == EVALUABLE_COMPLETENESS_STATUS
        and cardinality.exact_count == 0
        and cardinality.is_exact is True
        and cardinality.minimum_count is None
    )


def _has_positive_count(result: ToolResult) -> bool:
    evidence = _primary_evidence(result)
    cardinality = None if evidence is None else evidence.match_cardinality
    if cardinality is not None:
        if isinstance(cardinality.exact_count, int) and cardinality.exact_count > 0:
            return True
        if isinstance(cardinality.minimum_count, int) and cardinality.minimum_count > 0:
            return True
    payload = _payload(result)
    if payload is None:
        return False
    records = payload.get("records")
    return isinstance(records, list) and bool(records)


def _no_rows(result: ToolResult) -> bool:
    if _verified_zero_shape(result) or _is_partial(result) or _has_positive_count(result):
        return False
    evidence = _primary_evidence(result)
    cardinality = None if evidence is None else evidence.match_cardinality
    if cardinality is not None and cardinality.exact_count == 0:
        return True
    payload = _payload(result)
    if payload is not None:
        records = payload.get("records")
        if isinstance(records, list) and not records:
            return True
        if any(payload.get(name) == 0 for name in _ZERO_COUNT_FIELDS):
            return True
    if evidence is not None and evidence.query_executions:
        return all(item.rows_returned == 0 for item in evidence.query_executions)
    return False


def _row_observation(results: tuple[ToolResult, ...]) -> HistoricalRowObservation:
    if not results:
        return HistoricalRowObservation.NOT_OBSERVED
    if any(_verified_zero_shape(item) for item in results) and not any(
        _is_partial(item) for item in results
    ):
        return HistoricalRowObservation.VERIFIED_ZERO_SHAPE
    if any(_has_positive_count(item) or _is_partial(item) for item in results):
        return HistoricalRowObservation.ROWS_PRESENT
    if any(_no_rows(item) for item in results):
        return HistoricalRowObservation.NO_ROWS
    return HistoricalRowObservation.NOT_OBSERVED


def _query_observation(results: tuple[ToolResult, ...]) -> HistoricalQueryObservation:
    if not results:
        return HistoricalQueryObservation.NOT_OBSERVED
    if any(_is_partial(item) for item in results):
        return HistoricalQueryObservation.PARTIAL_OR_TRUNCATED
    return HistoricalQueryObservation.OBSERVED_NOT_PARTIAL


def _unsupported_argument_names(
    run: HistoricalSpecialistRunResult | None,
) -> tuple[str, ...]:
    if run is None:
        return ()
    names: list[str] = []
    for feedback in run.validation_feedback:
        if feedback.code not in _UNSUPPORTED_FEEDBACK:
            continue
        if feedback.argument_name is not None:
            names.append(feedback.argument_name)
        elif feedback.code is ValidationFeedbackCode.UNKNOWN_TOOL and feedback.tool_name:
            names.append(feedback.tool_name)
    for invocation in run.executed_invocations:
        allowed = HISTORICAL_TOOL_ARGUMENT_ALLOWLIST.get(invocation.tool_name)
        if allowed is None:
            names.append(invocation.tool_name)
        for arguments in (invocation.requested_arguments, invocation.executed_arguments):
            for key in arguments:
                if allowed is None or key not in allowed or key.casefold() in FORBIDDEN_TOOL_SCHEMA_FIELD_NAMES:
                    names.append(key)
    return tuple(dict.fromkeys(names))


def _executed_windows(
    run: HistoricalSpecialistRunResult | None,
) -> tuple[tuple[object, object], ...]:
    if run is None:
        return ()
    found: list[tuple[object, object]] = []
    for invocation in run.executed_invocations:
        arguments = invocation.executed_arguments
        if "start_utc" in arguments or "end_utc" in arguments:
            found.append((arguments.get("start_utc"), arguments.get("end_utc")))
    return tuple(found)


def _source_versions(results: tuple[ToolResult, ...]) -> tuple[str | None, ...]:
    found: list[str | None] = []
    for result in results:
        for evidence in result.evidence:
            for record in evidence.source_records:
                found.append(record.source_version)
    return tuple(found)


def _source_version_mismatch(
    versions: tuple[str | None, ...],
    expected: str | None,
) -> bool:
    concrete = {item for item in versions if item is not None}
    if len(concrete) > 1:
        return True
    if concrete and any(item is None for item in versions):
        return True
    if expected is None:
        return False
    if not versions:
        return True
    return any(item != expected for item in versions)


def _query_ids(results: tuple[ToolResult, ...]) -> tuple[str, ...]:
    found: list[str] = []
    for result in results:
        for evidence in result.evidence:
            for trace in evidence.query_executions:
                found.append(trace.query_id)
    return tuple(dict.fromkeys(found))


def _verified_zero_claim_proposed(run: HistoricalSpecialistRunResult | None) -> bool:
    if run is None:
        return False
    return any(isinstance(item, VerifiedZeroClaim) for item in run.proposed_claims)


def _eligible_tool_arguments(
    interval: IndependentHistoricalToolInterval | None,
) -> tuple[tuple[str, str], ...] | None:
    if interval is None:
        return None
    return (("end_utc", interval.end_utc), ("start_utc", interval.start_utc))


@dataclass(frozen=True)
class IndependentHistoricalToolInterval:
    """Bounded interval supplied apart from the Master plan window.

    Eligibility for tool arguments is not an execution grant, not source
    authentication, and not evidence that the interval was read.
    """

    interval_id: str
    start_utc: str
    end_utc: str

    def __post_init__(self) -> None:
        interval_id = _require_text(self.interval_id, "invalid_independent_historical_interval")
        start = _utc_text(self.start_utc, "invalid_independent_historical_interval")
        end = _utc_text(self.end_utc, "invalid_independent_historical_interval")
        if _instant(start) >= _instant(end):
            raise ContractValidationError("invalid_independent_historical_interval")
        if interval_id in {start, end}:
            raise ContractValidationError("request_identity_not_distinct")
        object.__setattr__(self, "interval_id", interval_id)
        object.__setattr__(self, "start_utc", start)
        object.__setattr__(self, "end_utc", end)


@dataclass(frozen=True)
class HistoricalHostInvocationRecord:
    """Host-side invocation identity. This is not a native specialist result."""

    binding: MasterHistoricalBinding
    master_request_id: str
    invocation_id: str
    specialist_request_id: str
    correlation_id: str
    application_request_time_utc: str
    requested_evaluation_time_utc: str | None
    unbound_plan_window_start_utc: str
    unbound_plan_window_end_utc: str
    specialist_request: SpecialistRequest
    trusted_context: HistoricalSpecialistTrustedContext | None = None
    independent_tool_interval: IndependentHistoricalToolInterval | None = None
    tool_argument_source: HistoricalToolArgumentSource = HistoricalToolArgumentSource.NONE
    eligible_tool_arguments: tuple[tuple[str, str], ...] | None = None
    plan_window_scope: MasterHistoricalPlanScope = MasterHistoricalPlanScope.UNBOUND
    structured_request_arguments: None = None
    specialist_request_text_is_canonical: bool = True
    trusted_context_authenticates_execution: bool = False
    schema_version: str = MASTER_HISTORICAL_NATIVE_BRIDGE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != MASTER_HISTORICAL_NATIVE_BRIDGE_SCHEMA_VERSION:
            raise ContractValidationError("invalid_schema_version")
        if not isinstance(self.binding, MasterHistoricalBinding):
            raise ContractValidationError("invalid_historical_binding")
        authorization = self.binding.authorization
        if authorization.specialist is not MasterSpecialist.HISTORICAL_ANALYTICS:
            raise ContractValidationError("specialist_mismatch")
        if self.master_request_id != authorization.master_request_id:
            raise ContractValidationError("master_request_mismatch")
        if self.invocation_id != self.binding.invocation_id:
            raise ContractValidationError("invocation_binding_mismatch")
        if self.specialist_request_id != self.binding.specialist_request_id:
            raise ContractValidationError("specialist_request_id_mismatch")
        if self.correlation_id != self.binding.correlation_id:
            raise ContractValidationError("correlation_mismatch")
        if self.correlation_id == self.specialist_request_id:
            raise ContractValidationError("request_id_not_correlation_proof")
        if self.application_request_time_utc != self.binding.application_request_time_utc:
            raise ContractValidationError("application_time_mismatch")
        if self.requested_evaluation_time_utc != self.binding.requested_evaluation_time_utc:
            raise ContractValidationError("requested_evaluation_time_mismatch")
        if (
            self.unbound_plan_window_start_utc != self.binding.unbound_plan_window_start_utc
            or self.unbound_plan_window_end_utc != self.binding.unbound_plan_window_end_utc
        ):
            raise ContractValidationError("historical_window_not_authorized")
        identities = (
            self.master_request_id,
            self.invocation_id,
            self.specialist_request_id,
            self.correlation_id,
        )
        if len(set(identities)) != 4:
            raise ContractValidationError("request_identity_not_distinct")
        if not isinstance(self.specialist_request, SpecialistRequest):
            raise ContractValidationError("invalid_specialist_request")
        if self.specialist_request is not self.binding.specialist_request:
            raise ContractValidationError("specialist_request_id_mismatch")
        if self.specialist_request.text != self.binding.canonical_user_text:
            raise ContractValidationError("canonical_user_text_mismatch")
        _true(self.specialist_request_text_is_canonical, "canonical_user_text_mismatch")
        _closed(self.plan_window_scope, MasterHistoricalPlanScope.UNBOUND, "historical_window_not_authorized")
        _none(self.structured_request_arguments, "historical_window_not_authorized")
        _none(self.binding.structured_request_arguments, "historical_window_not_authorized")
        _none(self.binding.trusted_context, "trusted_context_not_authorized")
        _false(self.trusted_context_authenticates_execution, "execution_not_granted")
        if self.trusted_context is not None:
            if not isinstance(self.trusted_context, HistoricalSpecialistTrustedContext):
                raise ContractValidationError("invalid_trusted_context")
            if self.trusted_context.as_of_utc == self.application_request_time_utc:
                raise ContractValidationError("application_clock_not_evaluation_time")
        if self.independent_tool_interval is None:
            _closed(
                self.tool_argument_source,
                HistoricalToolArgumentSource.NONE,
                "historical_window_not_authorized",
            )
            _none(self.eligible_tool_arguments, "historical_window_not_authorized")
        else:
            if not isinstance(self.independent_tool_interval, IndependentHistoricalToolInterval):
                raise ContractValidationError("invalid_independent_historical_interval")
            if self.independent_tool_interval.interval_id in identities:
                raise ContractValidationError("request_identity_not_distinct")
            _closed(
                self.tool_argument_source,
                HistoricalToolArgumentSource.INDEPENDENT_INTERVAL,
                "historical_window_not_authorized",
            )
            expected = _eligible_tool_arguments(self.independent_tool_interval)
            if self.eligible_tool_arguments != expected:
                raise ContractValidationError("historical_window_not_authorized")
            arguments = dict(self.eligible_tool_arguments or ())
            if set(arguments) != {"start_utc", "end_utc"}:
                raise ContractValidationError("historical_window_not_authorized")
            if (
                arguments["start_utc"] != self.independent_tool_interval.start_utc
                or arguments["end_utc"] != self.independent_tool_interval.end_utc
            ):
                raise ContractValidationError("historical_window_not_authorized")


@dataclass(frozen=True)
class MasterHistoricalNativeObservation:
    """Caller-supplied native artifacts for one binding. Not an execution grant."""

    binding: MasterHistoricalBinding
    native_run: HistoricalSpecialistRunResult | None = None
    native_final_result: SpecialistResult | None = None
    observed_request: SpecialistRequest | None = None
    trusted_context: HistoricalSpecialistTrustedContext | None = None
    independent_tool_interval: IndependentHistoricalToolInterval | None = None
    expected_source_version: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.binding, MasterHistoricalBinding):
            raise ContractValidationError("invalid_historical_binding")
        if self.native_run is not None and not isinstance(
            self.native_run,
            HistoricalSpecialistRunResult,
        ):
            raise ContractValidationError("historical_run_not_final_artifact")
        if self.native_final_result is not None and not isinstance(
            self.native_final_result,
            SpecialistResult,
        ):
            raise ContractValidationError("historical_run_not_final_artifact")
        if self.expected_source_version is not None:
            _require_text(self.expected_source_version, "invalid_expected_source_version")


@dataclass(frozen=True)
class MasterHistoricalNativeBridgeEnvelope:
    """Host invocation record correlated with an unauthenticated native result.

    The native run is retained only as an observation. Retaining it does not
    admit evidence, resolve correlation fields the native type does not have,
    or authenticate caller-supplied execution.
    """

    host: HistoricalHostInvocationRecord
    readiness: MasterHistoricalExecutionReadiness
    master_request_id: str
    invocation_id: str
    specialist_request_id: str
    correlation_id: str
    application_request_time_utc: str
    requested_evaluation_time_utc: str | None
    actual_runtime_evaluation_time_utc: str | None
    unbound_plan_window_start_utc: str
    unbound_plan_window_end_utc: str
    native_run: HistoricalSpecialistRunResult | None
    native_final_result: SpecialistResult | None
    native_observation_origin: NativeObservationOrigin
    row_observation: HistoricalRowObservation
    query_observation: HistoricalQueryObservation
    unsupported_argument_names: tuple[str, ...]
    observed_source_versions: tuple[str | None, ...]
    observed_query_ids: tuple[str, ...]
    binding_defects: tuple[MasterBindingDefect, ...]
    correlation_gaps: tuple[NativeContractFieldGap, ...]
    correlation_reasons: tuple[MasterAdmissionReason, ...]
    migration_gaps: tuple[NativeContractFieldGap, ...]
    independent_tool_interval: IndependentHistoricalToolInterval | None = None
    tool_argument_source: HistoricalToolArgumentSource = HistoricalToolArgumentSource.NONE
    eligible_tool_arguments: tuple[tuple[str, str], ...] | None = None
    expected_source_version: str | None = None
    source_version_mismatch: bool = False
    unbound_plan_window_observed_in_executed_arguments: bool = False
    executed_window_matches_independent_interval: bool = False
    verified_zero_claim_proposed: bool = False
    authenticated_execution: bool = False
    caller_supplied_native_result_is_authenticated_execution: bool = False
    evidence_issued: bool = False
    finalization_performed: bool = False
    dispatch_permitted: bool = False
    native_correlation_resolved: bool = False
    native_specialist_request_id_resolved: bool = False
    tool_result_correlation_satisfies_result_contract: bool = False
    requested_time_is_runtime_evaluation_time: bool = False
    application_clock_is_runtime_evaluation_time: bool = False
    evaluated_as_of_is_authenticated_source_time: bool = False
    failure_interpreted_as_zero: bool = False
    incomplete_coverage_interpreted_as_zero: bool = False
    verified_zero_admitted: bool = False
    run_is_final_artifact: bool = False
    source_version_authenticated: bool = False
    authenticated_source_version: str | None = None
    authenticated_query_id: str | None = None
    bridge_copied_unbound_plan_window: bool = False
    execution_state: MasterAdapterExecutionState = (
        MasterAdapterExecutionState.BLOCKED_BEFORE_EXECUTION_AUTHORIZATION
    )
    execution_authority: MasterRouteExecutionAuthority = MasterRouteExecutionAuthority.DENIED
    source_authenticity: MasterSourceAuthenticity = MasterSourceAuthenticity.NOT_ESTABLISHED
    production_authority: MasterProductionAuthority = MasterProductionAuthority.DENIED
    request_coverage: MasterRequestCoverage = MasterRequestCoverage.NOT_ESTABLISHED
    end_to_end_fulfilled: bool = False
    stops_before_execution_authorization: bool = True
    intermediate_run_type: str = INTERMEDIATE_RUN_TYPE
    final_result_type: str = FINAL_RESULT_TYPE
    finalization_entrypoint: str = HISTORICAL_FINALIZATION_ENTRYPOINT
    missing_provenance: tuple[MasterProvenanceRequirement, ...] = tuple(MasterProvenanceRequirement)
    schema_version: str = MASTER_HISTORICAL_NATIVE_BRIDGE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != MASTER_HISTORICAL_NATIVE_BRIDGE_SCHEMA_VERSION:
            raise ContractValidationError("invalid_schema_version")
        if not isinstance(self.host, HistoricalHostInvocationRecord):
            raise ContractValidationError("invalid_historical_binding")
        if not isinstance(self.readiness, MasterHistoricalExecutionReadiness):
            raise ContractValidationError("invalid_historical_binding")
        if self.readiness.binding is not self.host.binding:
            raise ContractValidationError("invocation_binding_mismatch")
        host = self.host
        if self.master_request_id != host.master_request_id:
            raise ContractValidationError("master_request_mismatch")
        if self.invocation_id != host.invocation_id:
            raise ContractValidationError("invocation_binding_mismatch")
        if self.specialist_request_id != host.specialist_request_id:
            raise ContractValidationError("specialist_request_id_mismatch")
        if self.correlation_id != host.correlation_id:
            raise ContractValidationError("correlation_mismatch")
        if self.application_request_time_utc != host.application_request_time_utc:
            raise ContractValidationError("application_time_mismatch")
        if self.requested_evaluation_time_utc != host.requested_evaluation_time_utc:
            raise ContractValidationError("requested_evaluation_time_mismatch")
        if (
            self.unbound_plan_window_start_utc != host.unbound_plan_window_start_utc
            or self.unbound_plan_window_end_utc != host.unbound_plan_window_end_utc
        ):
            raise ContractValidationError("historical_window_not_authorized")
        if self.readiness.master_request_id != self.master_request_id:
            raise ContractValidationError("master_request_mismatch")
        if self.readiness.invocation_id != self.invocation_id:
            raise ContractValidationError("invocation_binding_mismatch")
        if self.readiness.correlation_id != self.correlation_id:
            raise ContractValidationError("correlation_mismatch")
        if self.native_run is not None and not isinstance(self.native_run, HistoricalSpecialistRunResult):
            raise ContractValidationError("historical_run_not_final_artifact")
        if isinstance(self.native_run, SpecialistResult):
            raise ContractValidationError("historical_run_not_final_artifact")
        if self.native_final_result is not None and not isinstance(
            self.native_final_result,
            SpecialistResult,
        ):
            raise ContractValidationError("historical_run_not_final_artifact")
        if isinstance(self.native_final_result, HistoricalSpecialistRunResult):
            raise ContractValidationError("historical_run_not_final_artifact")
        if self.native_run is None and self.native_final_result is None:
            _closed(self.native_observation_origin, NativeObservationOrigin.ABSENT, "invalid_native_result_gap")
        else:
            _closed(
                self.native_observation_origin,
                NativeObservationOrigin.CALLER_SUPPLIED,
                "execution_not_granted",
            )
        runtime_evaluation_time = None if self.native_run is None else self.native_run.evaluated_as_of_utc
        if self.actual_runtime_evaluation_time_utc != runtime_evaluation_time:
            raise ContractValidationError("evaluated_as_of_not_evidence")
        if (
            self.native_run is None
            and self.actual_runtime_evaluation_time_utc == self.requested_evaluation_time_utc
            and self.requested_evaluation_time_utc is not None
        ):
            raise ContractValidationError("evaluated_as_of_not_evidence")
        if self.actual_runtime_evaluation_time_utc == self.application_request_time_utc:
            raise ContractValidationError("application_clock_not_evaluation_time")
        _false(self.requested_time_is_runtime_evaluation_time, "evaluated_as_of_not_evidence")
        _false(self.application_clock_is_runtime_evaluation_time, "evaluated_as_of_not_evidence")
        _false(
            self.evaluated_as_of_is_authenticated_source_time,
            "evaluated_as_of_not_evidence",
        )
        if self.independent_tool_interval is not host.independent_tool_interval:
            raise ContractValidationError("historical_window_not_authorized")
        if self.tool_argument_source is not host.tool_argument_source:
            raise ContractValidationError("historical_window_not_authorized")
        if self.eligible_tool_arguments != host.eligible_tool_arguments:
            raise ContractValidationError("historical_window_not_authorized")
        _false(self.bridge_copied_unbound_plan_window, "historical_window_not_authorized")
        if self.tool_argument_source is HistoricalToolArgumentSource.NONE:
            _none(self.eligible_tool_arguments, "historical_window_not_authorized")
            _false(
                self.executed_window_matches_independent_interval,
                "historical_window_not_authorized",
            )
        results = _tool_results(self.native_run, self.native_final_result)
        if self.row_observation is not _row_observation(results):
            raise ContractValidationError("failure_not_verified_zero")
        if self.query_observation is not _query_observation(results):
            raise ContractValidationError("incomplete_coverage_not_verified_zero")
        if self.unsupported_argument_names != _unsupported_argument_names(self.native_run):
            raise ContractValidationError("invalid_native_result_gap")
        if self.observed_source_versions != _source_versions(results):
            raise ContractValidationError("verified_evidence_not_issued")
        if self.source_version_mismatch is not _source_version_mismatch(
            self.observed_source_versions,
            self.expected_source_version,
        ):
            raise ContractValidationError("verified_evidence_not_issued")
        if self.observed_query_ids != _query_ids(results):
            raise ContractValidationError("verified_evidence_not_issued")
        windows = _executed_windows(self.native_run)
        plan_window = (self.unbound_plan_window_start_utc, self.unbound_plan_window_end_utc)
        plan_observed = bool(windows) and any(item == plan_window for item in windows)
        if (
            self.tool_argument_source is HistoricalToolArgumentSource.NONE
            and self.unbound_plan_window_observed_in_executed_arguments is not plan_observed
        ):
            raise ContractValidationError("historical_window_not_authorized")
        if self.tool_argument_source is not HistoricalToolArgumentSource.NONE:
            _false(
                self.unbound_plan_window_observed_in_executed_arguments,
                "historical_window_not_authorized",
            )
        if self.independent_tool_interval is not None:
            authorized = (
                self.independent_tool_interval.start_utc,
                self.independent_tool_interval.end_utc,
            )
            matched = bool(windows) and all(item == authorized for item in windows)
            if self.executed_window_matches_independent_interval is not matched:
                raise ContractValidationError("historical_window_not_authorized")
        if self.verified_zero_claim_proposed is not _verified_zero_claim_proposed(self.native_run):
            raise ContractValidationError("failure_not_verified_zero")
        _false(self.verified_zero_admitted, "failure_not_verified_zero")
        _false(self.authenticated_execution, "execution_not_granted")
        _false(
            self.caller_supplied_native_result_is_authenticated_execution,
            "execution_not_granted",
        )
        _false(self.evidence_issued, "verified_evidence_not_issued")
        _false(self.finalization_performed, "historical_finalization_not_performed")
        _false(self.readiness.finalization_performed, "historical_finalization_not_performed")
        _false(self.dispatch_permitted, "execution_not_granted")
        _false(self.native_correlation_resolved, "request_id_not_correlation_proof")
        _false(
            self.native_specialist_request_id_resolved,
            "native_specialist_request_id_not_resolved",
        )
        _false(
            self.tool_result_correlation_satisfies_result_contract,
            "request_id_not_correlation_proof",
        )
        _false(self.failure_interpreted_as_zero, "failure_not_verified_zero")
        _false(
            self.incomplete_coverage_interpreted_as_zero,
            "incomplete_coverage_not_verified_zero",
        )
        _false(self.run_is_final_artifact, "historical_run_not_final_artifact")
        _false(self.source_version_authenticated, "verified_evidence_not_issued")
        _none(self.authenticated_source_version, "verified_evidence_not_issued")
        _none(self.authenticated_query_id, "verified_evidence_not_issued")
        _closed(
            self.execution_state,
            MasterAdapterExecutionState.BLOCKED_BEFORE_EXECUTION_AUTHORIZATION,
            "execution_not_granted",
        )
        _closed(
            self.execution_authority,
            MasterRouteExecutionAuthority.DENIED,
            "execution_authority_not_granted",
        )
        _closed(
            self.source_authenticity,
            MasterSourceAuthenticity.NOT_ESTABLISHED,
            "source_authenticity_not_established",
        )
        _closed(
            self.production_authority,
            MasterProductionAuthority.DENIED,
            "production_authority_not_issued",
        )
        _closed(
            self.request_coverage,
            MasterRequestCoverage.NOT_ESTABLISHED,
            "request_coverage_not_established",
        )
        _false(self.end_to_end_fulfilled, "end_to_end_fulfillment_not_granted")
        _true(self.stops_before_execution_authorization, "execution_not_granted")
        if self.intermediate_run_type != INTERMEDIATE_RUN_TYPE:
            raise ContractValidationError("historical_run_not_final_artifact")
        if self.final_result_type != FINAL_RESULT_TYPE:
            raise ContractValidationError("historical_run_not_final_artifact")
        if self.intermediate_run_type == self.final_result_type:
            raise ContractValidationError("historical_run_not_final_artifact")
        if self.finalization_entrypoint != HISTORICAL_FINALIZATION_ENTRYPOINT:
            raise ContractValidationError("invalid_finalization_entrypoint")
        if self.missing_provenance != tuple(MasterProvenanceRequirement):
            raise ContractValidationError("provenance_requirement_not_satisfied")
        if self.binding_defects != self.readiness.binding_defects:
            raise ContractValidationError("invalid_native_result_gap")
        if self.correlation_gaps != historical_native_result_correlation_gaps():
            raise ContractValidationError("invalid_native_result_gap")
        if self.migration_gaps != self.correlation_gaps:
            raise ContractValidationError("invalid_native_result_gap")
        if self.correlation_reasons != self.readiness.correlation_reasons:
            raise ContractValidationError("native_specialist_request_id_not_resolved")
        if self.readiness.observed_intermediate_run is not (self.native_run is not None):
            raise ContractValidationError("historical_run_not_final_artifact")
        if self.readiness.observed_final_result is not (self.native_final_result is not None):
            raise ContractValidationError("historical_run_not_final_artifact")
        _none(self.readiness.historical_run, "historical_run_not_fabricated")
        _none(self.readiness.specialist_result, "verified_evidence_not_issued")
        _false(self.readiness.evidence_issued, "verified_evidence_not_issued")
        _false(self.readiness.dispatch_permitted, "execution_not_granted")
        _require_native_contract_gaps()


@dataclass(frozen=True)
class MasterHistoricalNativeBridgeCollection:
    """Distinct Historical invocations correlated without crossing windows."""

    master_request_id: str
    correlation_id: str
    application_request_time_utc: str
    envelopes: tuple[MasterHistoricalNativeBridgeEnvelope, ...]
    schema_version: str = MASTER_HISTORICAL_NATIVE_BRIDGE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != MASTER_HISTORICAL_NATIVE_BRIDGE_SCHEMA_VERSION:
            raise ContractValidationError("invalid_schema_version")
        if not isinstance(self.envelopes, tuple) or not self.envelopes:
            raise ContractValidationError("invocation_not_in_plan")
        if any(not isinstance(item, MasterHistoricalNativeBridgeEnvelope) for item in self.envelopes):
            raise ContractValidationError("invalid_historical_binding")
        for item in self.envelopes:
            if item.master_request_id != self.master_request_id:
                raise ContractValidationError("master_request_mismatch")
            if item.correlation_id != self.correlation_id:
                raise ContractValidationError("correlation_mismatch")
            if item.application_request_time_utc != self.application_request_time_utc:
                raise ContractValidationError("application_time_mismatch")
            _false(item.authenticated_execution, "execution_not_granted")
            _false(item.bridge_copied_unbound_plan_window, "historical_window_not_authorized")
            _none(item.authenticated_source_version, "verified_evidence_not_issued")
        if len({item.invocation_id for item in self.envelopes}) != len(self.envelopes):
            raise ContractValidationError("duplicate_invocation_id")
        if len({item.specialist_request_id for item in self.envelopes}) != len(self.envelopes):
            raise ContractValidationError("specialist_request_id_mismatch")
        if len({item.host.binding.step_id for item in self.envelopes}) != len(self.envelopes):
            raise ContractValidationError("invocation_binding_mismatch")
        runs = tuple(item.native_run for item in self.envelopes if item.native_run is not None)
        if len({id(item) for item in runs}) != len(runs):
            raise ContractValidationError("native_run_reused")


def sprint_3b2_historical_native_contract_migration() -> tuple[NativeContractFieldGap, ...]:
    """Return native correlation fields this branch does not add."""

    return historical_native_result_correlation_gaps()


def prepare_master_historical_native_invocation(
    binding: MasterHistoricalBinding,
    *,
    trusted_context: HistoricalSpecialistTrustedContext | None = None,
    independent_tool_interval: IndependentHistoricalToolInterval | None = None,
) -> HistoricalHostInvocationRecord:
    """Copy one binding into a host invocation record without executing it.

    The specialist request is the binding's request object. The plan window
    is not appended to that text and is not placed in tool arguments. An
    independent interval, when supplied, is the only source of eligible
    ``start_utc`` and ``end_utc`` arguments. A trusted context is stored
    only when the caller supplies one, and the application clock is rejected
    as that instant.
    """

    if not isinstance(binding, MasterHistoricalBinding):
        raise ContractValidationError("invalid_historical_binding")
    if independent_tool_interval is not None and not isinstance(
        independent_tool_interval,
        IndependentHistoricalToolInterval,
    ):
        raise ContractValidationError("invalid_independent_historical_interval")
    source = (
        HistoricalToolArgumentSource.NONE
        if independent_tool_interval is None
        else HistoricalToolArgumentSource.INDEPENDENT_INTERVAL
    )
    return HistoricalHostInvocationRecord(
        binding=binding,
        master_request_id=binding.authorization.master_request_id,
        invocation_id=binding.invocation_id,
        specialist_request_id=binding.specialist_request_id,
        correlation_id=binding.correlation_id,
        application_request_time_utc=binding.application_request_time_utc,
        requested_evaluation_time_utc=binding.requested_evaluation_time_utc,
        unbound_plan_window_start_utc=binding.unbound_plan_window_start_utc,
        unbound_plan_window_end_utc=binding.unbound_plan_window_end_utc,
        specialist_request=binding.specialist_request,
        trusted_context=trusted_context,
        independent_tool_interval=independent_tool_interval,
        tool_argument_source=source,
        eligible_tool_arguments=_eligible_tool_arguments(independent_tool_interval),
    )


def correlate_master_historical_native_result(
    binding: MasterHistoricalBinding,
    *,
    native_run: HistoricalSpecialistRunResult | None = None,
    native_final_result: SpecialistResult | None = None,
    observed_request: SpecialistRequest | None = None,
    trusted_context: HistoricalSpecialistTrustedContext | None = None,
    independent_tool_interval: IndependentHistoricalToolInterval | None = None,
    expected_source_version: str | None = None,
) -> MasterHistoricalNativeBridgeEnvelope:
    """Correlate a host binding with a native result without authenticating it.

    A supplied run is caller-supplied from this function's perspective,
    including a run produced by a test double. Matching request ids, tool
    correlation ids, or timestamps do not resolve the native correlation
    fields that the current result contracts omit, and they do not admit
    evidence. This function does not call the specialist or the finalizer.
    """

    if isinstance(native_run, SpecialistResult) or (
        native_run is not None and not isinstance(native_run, HistoricalSpecialistRunResult)
    ):
        raise ContractValidationError("historical_run_not_final_artifact")
    if isinstance(native_final_result, HistoricalSpecialistRunResult) or (
        native_final_result is not None and not isinstance(native_final_result, SpecialistResult)
    ):
        raise ContractValidationError("historical_run_not_final_artifact")
    if expected_source_version is not None:
        _require_text(expected_source_version, "invalid_expected_source_version")
    host = prepare_master_historical_native_invocation(
        binding,
        trusted_context=trusted_context,
        independent_tool_interval=independent_tool_interval,
    )
    readiness = assess_master_historical_execution_readiness(
        binding,
        observed_run=native_run,
        observed_result=native_final_result,
        observed_request=observed_request,
    )
    results = _tool_results(native_run, native_final_result)
    versions = _source_versions(results)
    windows = _executed_windows(native_run)
    plan_window = (host.unbound_plan_window_start_utc, host.unbound_plan_window_end_utc)
    interval_matched = False
    plan_observed = False
    if host.tool_argument_source is HistoricalToolArgumentSource.NONE:
        plan_observed = bool(windows) and any(item == plan_window for item in windows)
    elif independent_tool_interval is not None:
        authorized = (independent_tool_interval.start_utc, independent_tool_interval.end_utc)
        interval_matched = bool(windows) and all(item == authorized for item in windows)
    origin = (
        NativeObservationOrigin.ABSENT
        if native_run is None and native_final_result is None
        else NativeObservationOrigin.CALLER_SUPPLIED
    )
    return MasterHistoricalNativeBridgeEnvelope(
        host=host,
        readiness=readiness,
        master_request_id=host.master_request_id,
        invocation_id=host.invocation_id,
        specialist_request_id=host.specialist_request_id,
        correlation_id=host.correlation_id,
        application_request_time_utc=host.application_request_time_utc,
        requested_evaluation_time_utc=host.requested_evaluation_time_utc,
        actual_runtime_evaluation_time_utc=(
            None if native_run is None else native_run.evaluated_as_of_utc
        ),
        unbound_plan_window_start_utc=host.unbound_plan_window_start_utc,
        unbound_plan_window_end_utc=host.unbound_plan_window_end_utc,
        native_run=native_run,
        native_final_result=native_final_result,
        native_observation_origin=origin,
        row_observation=_row_observation(results),
        query_observation=_query_observation(results),
        unsupported_argument_names=_unsupported_argument_names(native_run),
        observed_source_versions=versions,
        observed_query_ids=_query_ids(results),
        binding_defects=readiness.binding_defects,
        correlation_gaps=historical_native_result_correlation_gaps(),
        correlation_reasons=readiness.correlation_reasons,
        migration_gaps=sprint_3b2_historical_native_contract_migration(),
        independent_tool_interval=independent_tool_interval,
        tool_argument_source=host.tool_argument_source,
        eligible_tool_arguments=host.eligible_tool_arguments,
        expected_source_version=expected_source_version,
        source_version_mismatch=_source_version_mismatch(versions, expected_source_version),
        unbound_plan_window_observed_in_executed_arguments=plan_observed,
        executed_window_matches_independent_interval=interval_matched,
        verified_zero_claim_proposed=_verified_zero_claim_proposed(native_run),
    )


def correlate_master_historical_native_collection(
    observations: tuple[MasterHistoricalNativeObservation, ...],
) -> MasterHistoricalNativeBridgeCollection:
    """Correlate each Historical observation on its own invocation identity."""

    if not isinstance(observations, tuple) or not observations:
        raise ContractValidationError("invocation_not_in_plan")
    if any(not isinstance(item, MasterHistoricalNativeObservation) for item in observations):
        raise ContractValidationError("invalid_historical_binding")
    envelopes = tuple(
        correlate_master_historical_native_result(
            item.binding,
            native_run=item.native_run,
            native_final_result=item.native_final_result,
            observed_request=item.observed_request,
            trusted_context=item.trusted_context,
            independent_tool_interval=item.independent_tool_interval,
            expected_source_version=item.expected_source_version,
        )
        for item in observations
    )
    first = envelopes[0]
    return MasterHistoricalNativeBridgeCollection(
        master_request_id=first.master_request_id,
        correlation_id=first.correlation_id,
        application_request_time_utc=first.application_request_time_utc,
        envelopes=envelopes,
    )


__all__ = [
    "EVALUABLE_COMPLETENESS_STATUS",
    "FINAL_RESULT_TYPE",
    "HISTORICAL_FINALIZATION_ENTRYPOINT",
    "HISTORICAL_TOOL_ARGUMENT_ALLOWLIST",
    "INTERMEDIATE_RUN_TYPE",
    "MASTER_HISTORICAL_NATIVE_BRIDGE_SCHEMA_VERSION",
    "RESULT_TRUNCATED_MARKER",
    "SPRINT_3B2_NATIVE_CONTRACT_MIGRATION_NOTES",
    "HistoricalHostInvocationRecord",
    "HistoricalQueryObservation",
    "HistoricalRowObservation",
    "HistoricalToolArgumentSource",
    "IndependentHistoricalToolInterval",
    "MasterHistoricalNativeBridgeCollection",
    "MasterHistoricalNativeBridgeEnvelope",
    "MasterHistoricalNativeObservation",
    "NativeObservationOrigin",
    "correlate_master_historical_native_collection",
    "correlate_master_historical_native_result",
    "prepare_master_historical_native_invocation",
    "sprint_3b2_historical_native_contract_migration",
]
