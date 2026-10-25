"""Non-executing readiness assessment for one Master Historical binding.

The assessment reads ``MasterHistoricalBinding``. It does not call
``HistoricalAnalyticsSpecialist``, does not call the historical finalization
entrypoint, does not construct ``HistoricalSpecialistTrustedContext``, and
does not promote a supplied run or result to authenticated evidence.

Clocks stay distinct:

- ``application_request_time_utc`` is when the application accepted the request.
- ``requested_evaluation_time_utc`` is the evaluation instant the plan asked
  for, when one was supplied. It is not evidence.
- ``unbound_plan_window_start_utc`` and ``unbound_plan_window_end_utc`` are
  the Master plan window. They are not native tool arguments.
- ``authenticated_source_evaluation_time_utc`` is the source evaluation time.
  It is not available. The application clock, the requested instant, and the
  local wall clock are not copied into it. ``evaluated_as_of_utc`` on a
  native result is a different field and is not adopted here.

``HistoricalSpecialistRunResult`` remains an intermediate result.
``SpecialistResult`` remains the final artifact type. Naming the finalization
entrypoint does not run it. A supplied run is not finalized by this module.
Failure and incomplete coverage are not a zero.

Native result envelopes do not carry the Master correlation fields. This
module records those missing fields. It does not add them to the native
contracts, and it does not fabricate a result, timestamp, count, query
result, or source version.
"""

from __future__ import annotations

from dataclasses import dataclass, fields

from wilvor_ai.contracts import (
    ContractValidationError,
    Evidence,
    QueryExecutionTrace,
    SourceRecord,
    ToolResult,
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
from wilvor_ai.master_parallel_contracts import MasterProvenanceRequirement
from wilvor_ai.master_routing import (
    MasterRequestCoverage,
    MasterRouteExecutionAuthority,
)
from wilvor_ai.master_specialist_adapters import MasterAdapterExecutionState
from wilvor_ai.specialist_contracts import (
    HistoricalSpecialistTrustedContext,
    SpecialistRequest,
    SpecialistResult,
)
from wilvor_ai.specialist_runtime_contracts import HistoricalSpecialistRunResult


MASTER_HISTORICAL_EXECUTION_READINESS_SCHEMA_VERSION = (
    "wilvor.ai.master_historical_execution_readiness.v1"
)
SOURCE_VERSION_OWNER = "SourceRecord"
QUERY_ID_OWNER = "QueryExecutionTrace"
INTERMEDIATE_RUN_TYPE = "HistoricalSpecialistRunResult"
FINAL_RESULT_TYPE = "SpecialistResult"

_CORRELATION_FIELDS = (
    "master_request_id",
    "specialist_request_id",
    "request_id",
    "correlation_id",
    "application_request_time_utc",
    "requested_evaluation_time_utc",
    "unbound_plan_window_start_utc",
    "unbound_plan_window_end_utc",
    "start_utc",
    "end_utc",
    "authenticated_source_evaluation_time_utc",
    "source_version",
    "query_id",
)
_GAP_CONTRACTS = (
    HistoricalSpecialistRunResult,
    SpecialistResult,
    ToolResult,
    Evidence,
    SpecialistRequest,
    HistoricalSpecialistTrustedContext,
)
_NATIVE_TOOL_WINDOW_ARGUMENTS = ("start_utc", "end_utc")
_DEFECT_ORDER = tuple(MasterBindingDefect)


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


def _field_names(cls: type) -> frozenset[str]:
    return frozenset(item.name for item in fields(cls))


def _missing_fields(cls: type) -> tuple[str, ...]:
    present = _field_names(cls)
    return tuple(name for name in _CORRELATION_FIELDS if name not in present)


def _require_nested_owners() -> None:
    if "source_version" not in _field_names(SourceRecord):
        raise ContractValidationError("invalid_native_result_gap")
    if "query_id" not in _field_names(QueryExecutionTrace):
        raise ContractValidationError("invalid_native_result_gap")
    if "source_version" in _field_names(HistoricalSpecialistRunResult):
        raise ContractValidationError("invalid_native_result_gap")
    if "source_version" in _field_names(SpecialistResult):
        raise ContractValidationError("invalid_native_result_gap")
    if "query_id" in _field_names(HistoricalSpecialistRunResult):
        raise ContractValidationError("invalid_native_result_gap")
    if "query_id" in _field_names(SpecialistResult):
        raise ContractValidationError("invalid_native_result_gap")
    if "evaluated_as_of_utc" not in _field_names(HistoricalSpecialistRunResult):
        raise ContractValidationError("invalid_native_result_gap")
    if "evaluated_as_of_utc" not in _field_names(SpecialistResult):
        raise ContractValidationError("invalid_native_result_gap")
    request_fields = _field_names(SpecialistRequest)
    if any(name in request_fields for name in _NATIVE_TOOL_WINDOW_ARGUMENTS):
        raise ContractValidationError("historical_window_not_authorized")


_require_nested_owners()


@dataclass(frozen=True)
class NativeContractFieldGap:
    """Correlation fields absent from one existing native contract.

    This records a migration target. It does not add the fields.
    """

    contract_name: str
    missing_fields: tuple[str, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.contract_name, str) or self.contract_name != self.contract_name.strip():
            raise ContractValidationError("invalid_native_result_gap")
        if not self.contract_name:
            raise ContractValidationError("invalid_native_result_gap")
        if not isinstance(self.missing_fields, tuple) or any(
            not isinstance(item, str) or not item.strip() for item in self.missing_fields
        ):
            raise ContractValidationError("invalid_native_result_gap")


def historical_native_result_correlation_gaps() -> tuple[NativeContractFieldGap, ...]:
    """Return the correlation fields missing from each inspected native contract."""

    return tuple(
        NativeContractFieldGap(cls.__name__, _missing_fields(cls)) for cls in _GAP_CONTRACTS
    )


def _correlation_reasons(
    *,
    run_observed: bool,
    result_observed: bool,
) -> tuple[MasterAdmissionReason, ...]:
    reasons = {
        MasterAdmissionReason.SOURCE_AUTHENTICITY_NOT_ESTABLISHED,
        MasterAdmissionReason.NATIVE_CORRELATION_ABSENT,
        MasterAdmissionReason.NATIVE_SPECIALIST_REQUEST_ID_ABSENT,
        MasterAdmissionReason.NATIVE_EVALUATION_TIME_ABSENT,
    }
    if run_observed:
        reasons.add(MasterAdmissionReason.HISTORICAL_RUN_NOT_FINAL_ARTIFACT)
        reasons.add(MasterAdmissionReason.HISTORICAL_RUN_CANNOT_REPRODUCE_VERIFICATION)
    elif result_observed:
        reasons.add(MasterAdmissionReason.HISTORICAL_RUN_CANNOT_REPRODUCE_VERIFICATION)
    else:
        reasons.add(MasterAdmissionReason.MISSING_NATIVE_ARTIFACT)
    return tuple(sorted(reasons, key=lambda item: item.value))


def _tool_correlation_ids(
    run: HistoricalSpecialistRunResult | None,
    result: SpecialistResult | None,
) -> tuple[str, ...]:
    found: list[str] = []
    for artifact in (run, result):
        if artifact is None:
            continue
        for tool_result in artifact.tool_results:
            if tool_result.correlation_id is not None:
                found.append(tool_result.correlation_id)
    return tuple(found)


def _binding_defects(
    binding: MasterHistoricalBinding,
    *,
    observed_request: SpecialistRequest | None,
    observed_run: HistoricalSpecialistRunResult | None,
    observed_result: SpecialistResult | None,
) -> tuple[MasterBindingDefect, ...]:
    defects: set[MasterBindingDefect] = set()
    if (
        observed_request is not None
        and observed_request.request_id != binding.specialist_request_id
    ):
        defects.add(MasterBindingDefect.REQUEST_ID_MISMATCH)
    requested = binding.requested_evaluation_time_utc
    observed_times: list[str] = []
    if observed_run is not None and observed_run.evaluated_as_of_utc is not None:
        observed_times.append(observed_run.evaluated_as_of_utc)
    if observed_result is not None and observed_result.evaluated_as_of_utc is not None:
        observed_times.append(observed_result.evaluated_as_of_utc)
    if requested is not None and any(item != requested for item in observed_times):
        defects.add(MasterBindingDefect.TIME_MISMATCH)
    if any(item != binding.correlation_id for item in _tool_correlation_ids(observed_run, observed_result)):
        defects.add(MasterBindingDefect.CORRELATION_MISMATCH)
    return tuple(item for item in _DEFECT_ORDER if item in defects)


@dataclass(frozen=True)
class MasterHistoricalExecutionReadiness:
    """Compatibility report for one bound Historical invocation. Not an execution.

    Supplied runs and results are inspected and then dropped. Retaining them
    would present caller-supplied objects as the assessment's evidence.
    """

    binding: MasterHistoricalBinding
    master_request_id: str
    invocation_id: str
    step_id: str
    specialist_request_id: str
    correlation_id: str
    application_request_time_utc: str
    requested_evaluation_time_utc: str | None
    unbound_plan_window_start_utc: str
    unbound_plan_window_end_utc: str
    correlation_gaps: tuple[NativeContractFieldGap, ...]
    correlation_reasons: tuple[MasterAdmissionReason, ...]
    authenticated_source_evaluation_time_utc: str | None = None
    plan_window_scope: MasterHistoricalPlanScope = MasterHistoricalPlanScope.UNBOUND
    historical_window_bound_to_native_tool_arguments: bool = False
    specialist_request_accepts_window_arguments: bool = False
    structured_request_arguments: None = None
    trusted_context: HistoricalSpecialistTrustedContext | None = None
    historical_run: HistoricalSpecialistRunResult | None = None
    specialist_result: SpecialistResult | None = None
    observed_intermediate_run: bool = False
    observed_final_result: bool = False
    intermediate_run_type: str = INTERMEDIATE_RUN_TYPE
    final_result_type: str = FINAL_RESULT_TYPE
    run_is_final_artifact: bool = False
    finalization_performed: bool = False
    finalization_complete: bool = False
    finalization_entrypoint: str = HISTORICAL_FINALIZATION_ENTRYPOINT
    binding_defects: tuple[MasterBindingDefect, ...] = ()
    source_version_owner: str = SOURCE_VERSION_OWNER
    query_id_owner: str = QUERY_ID_OWNER
    evaluated_as_of_utc_is_authenticated_source_time: bool = False
    application_clock_accepted_as_source_time: bool = False
    wall_clock_accepted_as_source_time: bool = False
    execution_state: MasterAdapterExecutionState = (
        MasterAdapterExecutionState.BLOCKED_BEFORE_EXECUTION_AUTHORIZATION
    )
    execution_authority: MasterRouteExecutionAuthority = MasterRouteExecutionAuthority.DENIED
    source_authenticity: MasterSourceAuthenticity = MasterSourceAuthenticity.NOT_ESTABLISHED
    production_authority: MasterProductionAuthority = MasterProductionAuthority.DENIED
    request_coverage: MasterRequestCoverage = MasterRequestCoverage.NOT_ESTABLISHED
    end_to_end_fulfilled: bool = False
    dispatch_permitted: bool = False
    stops_before_execution_authorization: bool = True
    evidence_issued: bool = False
    native_correlation_resolved: bool = False
    native_specialist_request_id_resolved: bool = False
    tool_result_correlation_satisfies_result_contract: bool = False
    failure_interpreted_as_zero: bool = False
    incomplete_coverage_interpreted_as_zero: bool = False
    inferred_count: int | None = None
    executed_tool_call_count: int | None = None
    source_version: str | None = None
    query_id: str | None = None
    query_result: object = None
    missing_provenance: tuple[MasterProvenanceRequirement, ...] = tuple(MasterProvenanceRequirement)
    schema_version: str = MASTER_HISTORICAL_EXECUTION_READINESS_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != MASTER_HISTORICAL_EXECUTION_READINESS_SCHEMA_VERSION:
            raise ContractValidationError("invalid_schema_version")
        if not isinstance(self.binding, MasterHistoricalBinding):
            raise ContractValidationError("invalid_historical_planning_facts")
        authorization = self.binding.authorization
        if authorization.specialist is not MasterSpecialist.HISTORICAL_ANALYTICS:
            raise ContractValidationError("specialist_mismatch")
        if self.binding.descriptor.specialist is not MasterSpecialist.HISTORICAL_ANALYTICS:
            raise ContractValidationError("specialist_mismatch")
        if self.master_request_id != authorization.master_request_id:
            raise ContractValidationError("master_request_mismatch")
        if self.master_request_id != self.binding.descriptor.master_request_id:
            raise ContractValidationError("master_request_mismatch")
        if self.invocation_id != self.binding.invocation_id:
            raise ContractValidationError("invocation_binding_mismatch")
        if self.step_id != self.binding.step_id:
            raise ContractValidationError("invocation_binding_mismatch")
        if self.specialist_request_id != self.binding.specialist_request_id:
            raise ContractValidationError("specialist_request_id_mismatch")
        if self.specialist_request_id != self.binding.specialist_request.request_id:
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
        if self.binding.specialist_request.text != self.binding.canonical_user_text:
            raise ContractValidationError("canonical_user_text_mismatch")
        _none(self.authenticated_source_evaluation_time_utc, "evaluated_as_of_not_evidence")
        _none(self.binding.evaluated_as_of_utc, "evaluated_as_of_not_evidence")
        if self.authenticated_source_evaluation_time_utc == self.application_request_time_utc:
            raise ContractValidationError("evaluated_as_of_not_evidence")
        if (
            self.requested_evaluation_time_utc is not None
            and self.authenticated_source_evaluation_time_utc == self.requested_evaluation_time_utc
        ):
            raise ContractValidationError("evaluated_as_of_not_evidence")
        _closed(
            self.plan_window_scope,
            MasterHistoricalPlanScope.UNBOUND,
            "historical_window_not_authorized",
        )
        _false(
            self.historical_window_bound_to_native_tool_arguments,
            "historical_window_not_authorized",
        )
        _false(
            self.specialist_request_accepts_window_arguments,
            "historical_window_not_authorized",
        )
        _false(self.binding.historical_window_bound_to_request, "historical_window_not_authorized")
        _none(self.structured_request_arguments, "historical_window_not_authorized")
        _none(self.binding.structured_request_arguments, "historical_window_not_authorized")
        _none(self.trusted_context, "trusted_context_not_authorized")
        _none(self.binding.trusted_context, "trusted_context_not_authorized")
        _none(self.historical_run, "historical_run_not_fabricated")
        _none(self.specialist_result, "verified_evidence_not_issued")
        if not isinstance(self.observed_intermediate_run, bool) or not isinstance(
            self.observed_final_result,
            bool,
        ):
            raise ContractValidationError("invalid_native_result_gap")
        if self.intermediate_run_type != INTERMEDIATE_RUN_TYPE:
            raise ContractValidationError("historical_run_not_final_artifact")
        if self.final_result_type != FINAL_RESULT_TYPE:
            raise ContractValidationError("historical_run_not_final_artifact")
        if self.intermediate_run_type == self.final_result_type:
            raise ContractValidationError("historical_run_not_final_artifact")
        _false(self.run_is_final_artifact, "historical_run_not_final_artifact")
        _false(self.finalization_performed, "historical_finalization_not_performed")
        _false(self.finalization_complete, "historical_finalization_not_performed")
        _false(self.binding.finalization_performed, "historical_finalization_not_performed")
        if self.finalization_entrypoint != HISTORICAL_FINALIZATION_ENTRYPOINT:
            raise ContractValidationError("invalid_finalization_entrypoint")
        if self.correlation_gaps != historical_native_result_correlation_gaps():
            raise ContractValidationError("invalid_native_result_gap")
        if self.correlation_reasons != _correlation_reasons(
            run_observed=self.observed_intermediate_run,
            result_observed=self.observed_final_result,
        ):
            raise ContractValidationError("native_specialist_request_id_not_resolved")
        if not isinstance(self.binding_defects, tuple) or any(
            not isinstance(item, MasterBindingDefect) for item in self.binding_defects
        ):
            raise ContractValidationError("invalid_native_result_gap")
        ordered = tuple(item for item in _DEFECT_ORDER if item in self.binding_defects)
        if self.binding_defects != ordered or len(self.binding_defects) != len(set(self.binding_defects)):
            raise ContractValidationError("invalid_native_result_gap")
        if self.source_version_owner != SOURCE_VERSION_OWNER:
            raise ContractValidationError("verified_evidence_not_issued")
        if self.query_id_owner != QUERY_ID_OWNER:
            raise ContractValidationError("verified_evidence_not_issued")
        _false(
            self.evaluated_as_of_utc_is_authenticated_source_time,
            "evaluated_as_of_not_evidence",
        )
        _false(self.application_clock_accepted_as_source_time, "evaluated_as_of_not_evidence")
        _false(self.wall_clock_accepted_as_source_time, "evaluated_as_of_not_evidence")
        _closed(
            self.execution_state,
            MasterAdapterExecutionState.BLOCKED_BEFORE_EXECUTION_AUTHORIZATION,
            "execution_not_granted",
        )
        _closed(
            self.binding.execution_state,
            MasterAdapterExecutionState.BLOCKED_BEFORE_EXECUTION_AUTHORIZATION,
            "execution_not_granted",
        )
        _closed(
            self.execution_authority,
            MasterRouteExecutionAuthority.DENIED,
            "execution_authority_not_granted",
        )
        _closed(
            self.binding.execution_authority,
            MasterRouteExecutionAuthority.DENIED,
            "execution_authority_not_granted",
        )
        _closed(
            self.source_authenticity,
            MasterSourceAuthenticity.NOT_ESTABLISHED,
            "source_authenticity_not_established",
        )
        _closed(
            self.binding.source_authenticity,
            MasterSourceAuthenticity.NOT_ESTABLISHED,
            "source_authenticity_not_established",
        )
        _closed(
            self.production_authority,
            MasterProductionAuthority.DENIED,
            "production_authority_not_issued",
        )
        _closed(
            self.binding.production_authority,
            MasterProductionAuthority.DENIED,
            "production_authority_not_issued",
        )
        _closed(
            self.request_coverage,
            MasterRequestCoverage.NOT_ESTABLISHED,
            "request_coverage_not_established",
        )
        _closed(
            self.binding.request_coverage,
            MasterRequestCoverage.NOT_ESTABLISHED,
            "request_coverage_not_established",
        )
        _false(self.end_to_end_fulfilled, "end_to_end_fulfillment_not_granted")
        _false(self.binding.end_to_end_fulfilled, "end_to_end_fulfillment_not_granted")
        _false(self.dispatch_permitted, "execution_not_granted")
        _false(self.binding.dispatch_permitted, "execution_not_granted")
        _true(self.stops_before_execution_authorization, "execution_not_granted")
        _true(self.binding.stops_before_execution_authorization, "execution_not_granted")
        _false(self.evidence_issued, "verified_evidence_not_issued")
        _false(self.binding.evidence_issued, "verified_evidence_not_issued")
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
        _none(self.inferred_count, "failure_not_verified_zero")
        _none(self.executed_tool_call_count, "historical_window_not_evaluated")
        _none(self.source_version, "verified_evidence_not_issued")
        _none(self.query_id, "verified_evidence_not_issued")
        _none(self.query_result, "verified_evidence_not_issued")
        if self.missing_provenance != tuple(MasterProvenanceRequirement):
            raise ContractValidationError("provenance_requirement_not_satisfied")
        _require_nested_owners()


def _require_binding(binding: object) -> MasterHistoricalBinding:
    if not isinstance(binding, MasterHistoricalBinding):
        raise ContractValidationError("invalid_historical_planning_facts")
    return binding


def _require_observed_request(
    binding: MasterHistoricalBinding,
    observed_request: object,
) -> SpecialistRequest | None:
    if observed_request is None:
        return None
    if not isinstance(observed_request, SpecialistRequest):
        raise ContractValidationError("invalid_specialist_request")
    if observed_request.text != binding.canonical_user_text:
        raise ContractValidationError("canonical_user_text_mismatch")
    return observed_request


def _require_observed_run(observed_run: object) -> HistoricalSpecialistRunResult | None:
    if observed_run is None:
        return None
    if isinstance(observed_run, SpecialistResult):
        raise ContractValidationError("historical_run_not_final_artifact")
    if not isinstance(observed_run, HistoricalSpecialistRunResult):
        raise ContractValidationError("invalid_historical_run")
    return observed_run


def _require_observed_result(observed_result: object) -> SpecialistResult | None:
    if observed_result is None:
        return None
    if isinstance(observed_result, HistoricalSpecialistRunResult):
        raise ContractValidationError("historical_run_not_final_artifact")
    if not isinstance(observed_result, SpecialistResult):
        raise ContractValidationError("invalid_specialist_result")
    return observed_result


def assess_master_historical_execution_readiness(
    binding: MasterHistoricalBinding,
    *,
    observed_run: HistoricalSpecialistRunResult | None = None,
    observed_result: SpecialistResult | None = None,
    observed_request: SpecialistRequest | None = None,
    proposed_source_evaluation_time_utc: str | None = None,
) -> MasterHistoricalExecutionReadiness:
    """Assess one binding without executing it or finalizing a run.

    ``proposed_source_evaluation_time_utc`` is rejected for every supplied
    value, including the application clock, the requested evaluation instant,
    and a local wall-clock reading. No source evaluation time is created.

    A matching caller-supplied request id, tool correlation id, or
    ``evaluated_as_of_utc`` does not resolve native correlation and does not
    authenticate a source time. The native result contracts still omit the
    Master identity fields.
    """

    checked = _require_binding(binding)
    if proposed_source_evaluation_time_utc is not None:
        raise ContractValidationError("evaluated_as_of_not_evidence")
    run = _require_observed_run(observed_run)
    result = _require_observed_result(observed_result)
    request = _require_observed_request(checked, observed_request)
    return MasterHistoricalExecutionReadiness(
        binding=checked,
        master_request_id=checked.authorization.master_request_id,
        invocation_id=checked.invocation_id,
        step_id=checked.step_id,
        specialist_request_id=checked.specialist_request_id,
        correlation_id=checked.correlation_id,
        application_request_time_utc=checked.application_request_time_utc,
        requested_evaluation_time_utc=checked.requested_evaluation_time_utc,
        unbound_plan_window_start_utc=checked.unbound_plan_window_start_utc,
        unbound_plan_window_end_utc=checked.unbound_plan_window_end_utc,
        correlation_gaps=historical_native_result_correlation_gaps(),
        correlation_reasons=_correlation_reasons(
            run_observed=run is not None,
            result_observed=result is not None,
        ),
        binding_defects=_binding_defects(
            checked,
            observed_request=request,
            observed_run=run,
            observed_result=result,
        ),
        observed_intermediate_run=run is not None,
        observed_final_result=result is not None,
    )


@dataclass(frozen=True)
class MasterHistoricalExecutionReadinessCollection:
    """One Master request's Historical readiness reports. Windows stay separate."""

    master_request_id: str
    correlation_id: str
    application_request_time_utc: str
    assessments: tuple[MasterHistoricalExecutionReadiness, ...]
    schema_version: str = MASTER_HISTORICAL_EXECUTION_READINESS_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != MASTER_HISTORICAL_EXECUTION_READINESS_SCHEMA_VERSION:
            raise ContractValidationError("invalid_schema_version")
        if not isinstance(self.assessments, tuple) or not self.assessments:
            raise ContractValidationError("invocation_not_in_plan")
        if any(not isinstance(item, MasterHistoricalExecutionReadiness) for item in self.assessments):
            raise ContractValidationError("invalid_historical_planning_facts")
        for item in self.assessments:
            if item.master_request_id != self.master_request_id:
                raise ContractValidationError("master_request_mismatch")
            if item.correlation_id != self.correlation_id:
                raise ContractValidationError("correlation_mismatch")
            if item.application_request_time_utc != self.application_request_time_utc:
                raise ContractValidationError("application_time_mismatch")
            _false(
                item.historical_window_bound_to_native_tool_arguments,
                "historical_window_not_authorized",
            )
            _none(item.authenticated_source_evaluation_time_utc, "evaluated_as_of_not_evidence")
            _false(item.dispatch_permitted, "execution_not_granted")
            _closed(
                item.source_authenticity,
                MasterSourceAuthenticity.NOT_ESTABLISHED,
                "source_authenticity_not_established",
            )
        if len({item.invocation_id for item in self.assessments}) != len(self.assessments):
            raise ContractValidationError("duplicate_invocation_id")
        if len({item.specialist_request_id for item in self.assessments}) != len(self.assessments):
            raise ContractValidationError("specialist_request_id_mismatch")
        if len({item.step_id for item in self.assessments}) != len(self.assessments):
            raise ContractValidationError("invocation_binding_mismatch")


def assess_master_historical_execution_readiness_collection(
    bindings: tuple[MasterHistoricalBinding, ...],
) -> MasterHistoricalExecutionReadinessCollection:
    """Assess each Historical binding on its own window. This does not execute."""

    if not isinstance(bindings, tuple) or not bindings:
        raise ContractValidationError("invocation_not_in_plan")
    if any(not isinstance(item, MasterHistoricalBinding) for item in bindings):
        raise ContractValidationError("invalid_historical_planning_facts")
    assessments = tuple(assess_master_historical_execution_readiness(item) for item in bindings)
    first = assessments[0]
    return MasterHistoricalExecutionReadinessCollection(
        master_request_id=first.master_request_id,
        correlation_id=first.correlation_id,
        application_request_time_utc=first.application_request_time_utc,
        assessments=assessments,
    )


__all__ = [
    "FINAL_RESULT_TYPE",
    "INTERMEDIATE_RUN_TYPE",
    "MASTER_HISTORICAL_EXECUTION_READINESS_SCHEMA_VERSION",
    "QUERY_ID_OWNER",
    "SOURCE_VERSION_OWNER",
    "MasterHistoricalExecutionReadiness",
    "MasterHistoricalExecutionReadinessCollection",
    "NativeContractFieldGap",
    "assess_master_historical_execution_readiness",
    "assess_master_historical_execution_readiness_collection",
    "historical_native_result_correlation_gaps",
]
