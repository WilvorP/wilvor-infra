"""Offline assessment of a future trusted source-read trace. Not a dispatcher.

A future trusted dispatcher would have to record the obligations below. This
module does not implement that dispatcher, and it does not observe one.
Caller-supplied metadata can be proposed. It cannot be an event observed
inside a trusted execution runtime. ``observed_trusted_runtime_events`` is
empty on every assessment. There is no parameter that fills it.

The seven ``MasterProvenanceRequirement`` results come only from
``classify_master_provenance``. This module does not add a satisfied status,
does not authenticate a read, and does not treat a matching timestamp, source
version, or request id as proof. Authority stays denied.

Existing interfaces this assessment does not rewrite:

- ``ProposedDispatcherExecutionEvent`` has no error, timeout, or cancellation
  field. ``ToolResultStatus`` and ``SpecialistRunStatus`` have no timeout or
  cancelled member. ``MasterDisposition`` already names ``PROVIDER_TIMEOUT``,
  ``RUNTIME_TIMEOUT``, ``CANCELLED``, and the failure dispositions. A
  disposition passed here is still caller-supplied. ``Evidence.error_code``
  is not parsed, so text such as ``timeout`` does not become a timeout.
- ``ExecutedToolInvocation`` records the tool name, tool-call id, requested
  arguments, and executed arguments. It has no adapter identity. A
  caller-built instance is not a trusted-runtime observation. Its arguments
  are ``dict[str, JsonValue]``. The proposed event stores
  ``tuple[tuple[str, str], ...]``. Matching strings are not proof that a
  tool ran. Non-string executed values are not rewritten into that pair.
- ``MasterSpecialistAdapterDescriptor`` stays blocked before execution
  authorization and has no implementation identity. The only adapter label
  on a proposed trace is ``adapter_identity``, a caller-supplied string.
- ``SourceCompleteness`` and ``ProposedPopulationScope`` have no geographic
  completeness field. ``CLAIMED_COMPLETE`` does not establish geographic
  coverage. ``MasterExecutionPrerequisite.POPULATION_AND_GEOGRAPHIC_SCOPE``
  remains the existing name for that missing proof. This module does not
  import execution scope and cannot mark the prerequisite satisfied.
- ``LocalConsumptionResult`` has no invocation id, permit reference, adapter,
  tool, arguments, or source response. ``TRANSITION_RECORDED`` is local
  bookkeeping only. The registry emits no provenance. This module does not
  import it, does not call ``consume``, and does not accept a consumption
  result as a trace. Consumption is the existing ``permit_consumption_ref``.
  That reference stays ``PERMIT_REFERENCE_NOT_CONSUMPTION`` and does not
  permit dispatch.
- Missing source records, a caller-reported zero, ``NOT_FOUND``, and an
  exact cardinality of zero are separate untrusted indications. None of
  them is authenticated complete-population absence, and none constructs a
  ``VerifiedZeroClaim``. ``ToolResult.data`` is not read as a count.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from wilvor_ai.contracts import (
    ContractValidationError,
    Evidence,
    SourceCompleteness,
    ToolResult,
    ToolResultStatus,
)
from wilvor_ai.master_contracts import (
    MasterDisposition,
    MasterInvocationAuthorization,
    MasterProductionAuthority,
)
from wilvor_ai.master_evidence_admission import MasterSourceAuthenticity
from wilvor_ai.master_parallel_contracts import MasterProvenanceRequirement
from wilvor_ai.master_provenance_trace import (
    AUTHENTICATED_SOURCE_READ_AVAILABLE,
    MasterProvenanceClassification,
    ProposedDispatcherExecutionEvent,
    ProposedPopulationScope,
    ProposedRetrievalLimit,
    ProvenanceAuditLimitation,
    ProvenanceRequirementStatus,
    classify_master_provenance,
)
from wilvor_ai.master_routing import (
    MasterRequestCoverage,
    MasterRouteExecutionAuthority,
)
from wilvor_ai.specialist_contracts import VerifiedZeroClaim
from wilvor_ai.specialist_runtime_contracts import ExecutedToolInvocation


MASTER_SOURCE_READ_TRACE_BOUNDARY_SCHEMA_VERSION = (
    "wilvor.ai.master_source_read_trace_boundary.v1"
)

TRUSTED_DISPATCHER_IMPLEMENTED = False
TRUSTED_RUNTIME_OBSERVATION_AVAILABLE = False
CALLER_SUPPLIED_TRACE_AUTHENTICATES_READ = False
MATCHING_TIMESTAMP_AUTHENTICATES_READ = False
MATCHING_SOURCE_VERSION_AUTHENTICATES_READ = False
MATCHING_REQUEST_ID_AUTHENTICATES_READ = False
CAPABILITY_CONSUMPTION_IS_DISPATCH_PERMISSION = False
EMPTY_ROWS_CREATE_VERIFIED_ZERO = False
COMPLETE_POPULATION_ABSENCE_AVAILABLE = False

# Same partial vocabulary the provenance audit already uses. Not a new status.
_PARTIAL_COMPLETENESS = frozenset({"PARTIAL", "INCOMPLETE", "LIMITED", "TRUNCATED"})

_ERROR_DISPOSITIONS = frozenset(
    {
        MasterDisposition.PROVIDER_FAILED,
        MasterDisposition.TOOL_FAILED,
        MasterDisposition.VERIFICATION_FAILED,
        MasterDisposition.INVALID_REQUEST,
        MasterDisposition.UNAVAILABLE,
        MasterDisposition.UNSUPPORTED,
        MasterDisposition.REFUSED,
        MasterDisposition.BUDGET_EXHAUSTED,
    }
)
_TIMEOUT_DISPOSITIONS = frozenset(
    {
        MasterDisposition.PROVIDER_TIMEOUT,
        MasterDisposition.RUNTIME_TIMEOUT,
    }
)
_CANCELLATION_DISPOSITIONS = frozenset({MasterDisposition.CANCELLED})
_FAILURE_DISPOSITIONS = _ERROR_DISPOSITIONS | _TIMEOUT_DISPOSITIONS | _CANCELLATION_DISPOSITIONS
_FAILURE_TOOL_STATUSES = frozenset(
    {
        ToolResultStatus.UNAVAILABLE,
        ToolResultStatus.UNKNOWN,
        ToolResultStatus.NOT_FOUND,
        ToolResultStatus.STALE,
    }
)
_ERROR_TOOL_STATUSES = frozenset(
    {
        ToolResultStatus.UNAVAILABLE,
        ToolResultStatus.UNKNOWN,
    }
)
_IDENTITY_LIMITATIONS = frozenset(
    {
        ProvenanceAuditLimitation.FOREIGN_REQUEST,
        ProvenanceAuditLimitation.FOREIGN_INVOCATION,
        ProvenanceAuditLimitation.CORRELATION_MISMATCH,
        ProvenanceAuditLimitation.SOURCE_VERSIONS_DISAGREE,
        ProvenanceAuditLimitation.INCONSISTENT_TIMESTAMPS,
    }
)


class SourceReadTraceObligation(str, Enum):
    """A record a future trusted dispatcher must write.

    Naming an obligation does not record it and does not authenticate a read.
    """

    AUTHORIZED_INVOCATION = "AUTHORIZED_INVOCATION"
    SPECIALIST_IDENTITY = "SPECIALIST_IDENTITY"
    CAPABILITY_CONSUMPTION = "CAPABILITY_CONSUMPTION"
    SELECTED_ADAPTER = "SELECTED_ADAPTER"
    EXECUTED_TOOL = "EXECUTED_TOOL"
    TOOL_ARGUMENTS = "TOOL_ARGUMENTS"
    SOURCE_RESPONSE_IDENTITY = "SOURCE_RESPONSE_IDENTITY"
    OBSERVATION_CLOCK = "OBSERVATION_CLOCK"
    EVALUATION_CLOCK = "EVALUATION_CLOCK"
    SOURCE_VERSION = "SOURCE_VERSION"
    ENTITY_BINDING = "ENTITY_BINDING"
    POPULATION_COMPLETENESS = "POPULATION_COMPLETENESS"
    GEOGRAPHIC_COMPLETENESS = "GEOGRAPHIC_COMPLETENESS"
    PAGINATION = "PAGINATION"
    ROW_LIMIT = "ROW_LIMIT"
    TRUNCATION = "TRUNCATION"
    ERROR_OUTCOME = "ERROR_OUTCOME"
    TIMEOUT_OUTCOME = "TIMEOUT_OUTCOME"
    CANCELLATION_OUTCOME = "CANCELLATION_OUTCOME"


class SourceReadTracePresence(str, Enum):
    """Whether caller-supplied metadata names an obligation.

    An observed trusted-runtime event is not a member. Proposed metadata
    stays unobserved.
    """

    NOT_PROPOSED = "NOT_PROPOSED"
    PROPOSED_UNOBSERVED = "PROPOSED_UNOBSERVED"


_OBLIGATION_REQUIREMENTS = (
    (
        SourceReadTraceObligation.AUTHORIZED_INVOCATION,
        MasterProvenanceRequirement.AUTHORIZED_EXECUTION_CONTEXT,
    ),
    (
        SourceReadTraceObligation.SPECIALIST_IDENTITY,
        MasterProvenanceRequirement.AUTHORIZED_EXECUTION_CONTEXT,
    ),
    (
        SourceReadTraceObligation.CAPABILITY_CONSUMPTION,
        MasterProvenanceRequirement.AUTHORIZED_EXECUTION_CONTEXT,
    ),
    (
        SourceReadTraceObligation.SELECTED_ADAPTER,
        MasterProvenanceRequirement.TRUSTED_SOURCE_ADAPTER,
    ),
    (
        SourceReadTraceObligation.EXECUTED_TOOL,
        MasterProvenanceRequirement.SOURCE_READ_PROVENANCE,
    ),
    (
        SourceReadTraceObligation.TOOL_ARGUMENTS,
        MasterProvenanceRequirement.SOURCE_READ_PROVENANCE,
    ),
    (
        SourceReadTraceObligation.SOURCE_RESPONSE_IDENTITY,
        MasterProvenanceRequirement.SOURCE_READ_PROVENANCE,
    ),
    (
        SourceReadTraceObligation.OBSERVATION_CLOCK,
        MasterProvenanceRequirement.OBSERVATION_EVALUATION_TIME,
    ),
    (
        SourceReadTraceObligation.EVALUATION_CLOCK,
        MasterProvenanceRequirement.OBSERVATION_EVALUATION_TIME,
    ),
    (
        SourceReadTraceObligation.SOURCE_VERSION,
        MasterProvenanceRequirement.SOURCE_VERSION_INTEGRITY,
    ),
    (
        SourceReadTraceObligation.ENTITY_BINDING,
        MasterProvenanceRequirement.REQUEST_ENTITY_BINDING,
    ),
    (
        SourceReadTraceObligation.POPULATION_COMPLETENESS,
        MasterProvenanceRequirement.POPULATION_SCOPE_COMPLETENESS,
    ),
    (
        SourceReadTraceObligation.GEOGRAPHIC_COMPLETENESS,
        MasterProvenanceRequirement.POPULATION_SCOPE_COMPLETENESS,
    ),
    (
        SourceReadTraceObligation.PAGINATION,
        MasterProvenanceRequirement.POPULATION_SCOPE_COMPLETENESS,
    ),
    (
        SourceReadTraceObligation.ROW_LIMIT,
        MasterProvenanceRequirement.POPULATION_SCOPE_COMPLETENESS,
    ),
    (
        SourceReadTraceObligation.TRUNCATION,
        MasterProvenanceRequirement.POPULATION_SCOPE_COMPLETENESS,
    ),
    (
        SourceReadTraceObligation.ERROR_OUTCOME,
        MasterProvenanceRequirement.SOURCE_READ_PROVENANCE,
    ),
    (
        SourceReadTraceObligation.TIMEOUT_OUTCOME,
        MasterProvenanceRequirement.SOURCE_READ_PROVENANCE,
    ),
    (
        SourceReadTraceObligation.CANCELLATION_OUTCOME,
        MasterProvenanceRequirement.SOURCE_READ_PROVENANCE,
    ),
)


def _closed(value: object, denied: object, code: str) -> None:
    if value is not denied:
        raise ContractValidationError(code)


def _deny_authority(
    *,
    source_authenticity: object,
    production_authority: object,
    execution_authority: object,
    request_coverage: object,
    dispatch_permitted: object,
    end_to_end_fulfilled: object,
) -> None:
    _closed(
        source_authenticity,
        MasterSourceAuthenticity.NOT_ESTABLISHED,
        "source_authenticity_not_established",
    )
    _closed(
        production_authority,
        MasterProductionAuthority.DENIED,
        "production_authority_not_issued",
    )
    _closed(
        execution_authority,
        MasterRouteExecutionAuthority.DENIED,
        "execution_authority_not_granted",
    )
    _closed(
        request_coverage,
        MasterRequestCoverage.NOT_ESTABLISHED,
        "request_coverage_not_established",
    )
    if dispatch_permitted is not False or end_to_end_fulfilled is not False:
        raise ContractValidationError("execution_not_granted")


def _evidence_of(
    subject: ToolResult | Evidence | ProposedDispatcherExecutionEvent,
) -> tuple[Evidence, ...]:
    if isinstance(subject, Evidence):
        return (subject,)
    if isinstance(subject, ToolResult):
        return subject.evidence
    return ()


def _partial(completeness: SourceCompleteness | None) -> bool:
    if completeness is None or completeness.status is None:
        return False
    return completeness.status.strip().upper() in _PARTIAL_COMPLETENESS


def _tool_call_id(
    subject: ToolResult | Evidence | ProposedDispatcherExecutionEvent,
) -> str:
    return subject.tool_call_id


class UntrustedAbsenceKind(str, Enum):
    """One caller-supplied population signal. None is an authenticated zero.

    ``MISSING_SOURCE_RECORDS`` means that evidence item carried no source
    records. It is missing metadata, not a report that the requested
    population has zero rows. ``SOURCE_RECORDS_PRESENT`` keeps a populated
    item visible when another item lacks records. ``CALLER_REPORTED_QUERY_ROWS``
    copies one query execution's ``rows_returned``, including zero. A zero on
    one execution does not describe the others. Exact cardinality zero,
    ``NOT_FOUND``, and a ``VerifiedZeroClaim`` stay caller-reported.
    Authenticated complete-population absence is not a member.
    """

    MISSING_SOURCE_RECORDS = "MISSING_SOURCE_RECORDS"
    SOURCE_RECORDS_PRESENT = "SOURCE_RECORDS_PRESENT"
    CALLER_REPORTED_EXACT_ZERO = "CALLER_REPORTED_EXACT_ZERO"
    CALLER_REPORTED_QUERY_ROWS = "CALLER_REPORTED_QUERY_ROWS"
    CALLER_REPORTED_NOT_FOUND = "CALLER_REPORTED_NOT_FOUND"
    CALLER_REPORTED_VERIFIED_ZERO_CLAIM = "CALLER_REPORTED_VERIFIED_ZERO_CLAIM"


def _nonnegative(value: object, field_name: str, *, required: bool) -> None:
    if value is None:
        if required:
            raise ContractValidationError(f"invalid_{field_name}")
        return
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ContractValidationError(f"invalid_{field_name}")


@dataclass(frozen=True)
class UntrustedAbsenceIndication:
    """One untrusted population signal. It does not establish absence.

    The evidence and query indexes bind the signal to the item that supplied
    it. A missing-record signal on one item does not cover another item, and
    a zero ``rows_returned`` does not cover another query. ``rows_returned``
    is copied from the caller-supplied query trace. ``None`` means that trace
    omitted a count and is not treated as zero. ``ToolResult.data`` is not a
    source for this object. Both establishment flags stay false: this module
    cannot authenticate complete-population absence or a verified zero.
    """

    kind: UntrustedAbsenceKind
    evidence_index: int | None = None
    query_index: int | None = None
    rows_returned: int | None = None
    establishes_complete_population_absence: bool = False
    establishes_verified_zero: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.kind, UntrustedAbsenceKind):
            raise ContractValidationError("invalid_untrusted_absence_indication")
        if (
            self.establishes_complete_population_absence is not False
            or self.establishes_verified_zero is not False
        ):
            raise ContractValidationError("complete_population_absence_not_available")
        record_kinds = (
            UntrustedAbsenceKind.MISSING_SOURCE_RECORDS,
            UntrustedAbsenceKind.SOURCE_RECORDS_PRESENT,
            UntrustedAbsenceKind.CALLER_REPORTED_EXACT_ZERO,
        )
        if self.kind in record_kinds:
            _nonnegative(self.evidence_index, "evidence_index", required=True)
            _nonnegative(self.query_index, "query_index", required=False)
            if self.query_index is not None or self.rows_returned is not None:
                raise ContractValidationError("invalid_untrusted_absence_indication")
            return
        if self.kind is UntrustedAbsenceKind.CALLER_REPORTED_QUERY_ROWS:
            _nonnegative(self.evidence_index, "evidence_index", required=True)
            _nonnegative(self.query_index, "query_index", required=True)
            _nonnegative(self.rows_returned, "rows_returned", required=False)
            return
        if self.kind in (
            UntrustedAbsenceKind.CALLER_REPORTED_NOT_FOUND,
            UntrustedAbsenceKind.CALLER_REPORTED_VERIFIED_ZERO_CLAIM,
        ):
            if (
                self.evidence_index is not None
                or self.query_index is not None
                or self.rows_returned is not None
            ):
                raise ContractValidationError("invalid_untrusted_absence_indication")
            return
        raise ContractValidationError("invalid_untrusted_absence_indication")


def _exact_zero(evidence: Evidence) -> bool:
    cardinality = evidence.match_cardinality
    return (
        cardinality is not None
        and cardinality.is_exact is True
        and cardinality.exact_count == 0
    )


def _untrusted_absence_indication(
    subject: ToolResult | Evidence | ProposedDispatcherExecutionEvent,
    verified_zero_claim: VerifiedZeroClaim | None,
) -> tuple[UntrustedAbsenceIndication, ...]:
    """Copy per-item signals. Do not reduce them to one population result."""

    indications: list[UntrustedAbsenceIndication] = []
    for evidence_index, evidence in enumerate(_evidence_of(subject)):
        record_kind = (
            UntrustedAbsenceKind.SOURCE_RECORDS_PRESENT
            if evidence.source_records
            else UntrustedAbsenceKind.MISSING_SOURCE_RECORDS
        )
        indications.append(
            UntrustedAbsenceIndication(kind=record_kind, evidence_index=evidence_index)
        )
        if _exact_zero(evidence):
            indications.append(
                UntrustedAbsenceIndication(
                    kind=UntrustedAbsenceKind.CALLER_REPORTED_EXACT_ZERO,
                    evidence_index=evidence_index,
                )
            )
        for query_index, query in enumerate(evidence.query_executions):
            indications.append(
                UntrustedAbsenceIndication(
                    kind=UntrustedAbsenceKind.CALLER_REPORTED_QUERY_ROWS,
                    evidence_index=evidence_index,
                    query_index=query_index,
                    rows_returned=query.rows_returned,
                )
            )
    if isinstance(subject, ToolResult) and subject.status is ToolResultStatus.NOT_FOUND:
        indications.append(
            UntrustedAbsenceIndication(kind=UntrustedAbsenceKind.CALLER_REPORTED_NOT_FOUND)
        )
    if _claim_for(subject, verified_zero_claim) is not None:
        indications.append(
            UntrustedAbsenceIndication(
                kind=UntrustedAbsenceKind.CALLER_REPORTED_VERIFIED_ZERO_CLAIM
            )
        )
    return tuple(indications)


def _proposed(
    subject: ToolResult | Evidence | ProposedDispatcherExecutionEvent,
    *,
    authorization: MasterInvocationAuthorization | None,
    executed_invocation: ExecutedToolInvocation | None,
    disposition: MasterDisposition | None,
) -> frozenset[SourceReadTraceObligation]:
    proposed: set[SourceReadTraceObligation] = set()
    event = subject if isinstance(subject, ProposedDispatcherExecutionEvent) else None
    if authorization is not None or event is not None:
        proposed.add(SourceReadTraceObligation.AUTHORIZED_INVOCATION)
    if authorization is not None:
        proposed.add(SourceReadTraceObligation.SPECIALIST_IDENTITY)
    if event is not None and event.permit_consumption_ref is not None:
        proposed.add(SourceReadTraceObligation.CAPABILITY_CONSUMPTION)
    if event is not None and event.adapter_identity is not None:
        proposed.add(SourceReadTraceObligation.SELECTED_ADAPTER)
    if executed_invocation is not None or _tool_call_id(subject):
        proposed.add(SourceReadTraceObligation.EXECUTED_TOOL)
    if executed_invocation is not None or (event is not None and event.tool_arguments):
        proposed.add(SourceReadTraceObligation.TOOL_ARGUMENTS)
    if event is not None and (
        event.source_response_ref is not None or event.execution_trace_ref is not None
    ):
        proposed.add(SourceReadTraceObligation.SOURCE_RESPONSE_IDENTITY)
    if any(item.source or item.source_records or item.query_executions for item in _evidence_of(subject)):
        proposed.add(SourceReadTraceObligation.SOURCE_RESPONSE_IDENTITY)
    if event is not None and event.observation_time_utc is not None:
        proposed.add(SourceReadTraceObligation.OBSERVATION_CLOCK)
    if any(
        item.query_timestamp_utc or any(record.event_timestamp_utc for record in item.source_records)
        for item in _evidence_of(subject)
    ):
        proposed.add(SourceReadTraceObligation.OBSERVATION_CLOCK)
    if authorization is not None and authorization.requested_evaluation_time_utc is not None:
        proposed.add(SourceReadTraceObligation.EVALUATION_CLOCK)
    if event is not None and (
        event.evaluation_time_utc is not None
        or (event.completeness is not None and event.completeness.evaluated_as_of_utc is not None)
    ):
        proposed.add(SourceReadTraceObligation.EVALUATION_CLOCK)
    if isinstance(subject, ToolResult) and subject.as_of_utc is not None:
        proposed.add(SourceReadTraceObligation.EVALUATION_CLOCK)
    if any(
        item.completeness is not None and item.completeness.evaluated_as_of_utc is not None
        for item in _evidence_of(subject)
    ):
        proposed.add(SourceReadTraceObligation.EVALUATION_CLOCK)
    if (event is not None and event.source_version is not None) or any(
        record.source_version is not None
        for item in _evidence_of(subject)
        for record in item.source_records
    ):
        proposed.add(SourceReadTraceObligation.SOURCE_VERSION)
    if event is not None and event.entity_ids:
        proposed.add(SourceReadTraceObligation.ENTITY_BINDING)
    if authorization is not None and (
        authorization.expected_aircraft_id is not None
        or authorization.expected_recommendation_id is not None
    ):
        proposed.add(SourceReadTraceObligation.ENTITY_BINDING)
    if event is not None and (
        event.population_scope is not ProposedPopulationScope.UNSPECIFIED or event.completeness is not None
    ):
        proposed.add(SourceReadTraceObligation.POPULATION_COMPLETENESS)
    if any(
        item.completeness is not None or item.match_cardinality is not None
        for item in _evidence_of(subject)
    ):
        proposed.add(SourceReadTraceObligation.POPULATION_COMPLETENESS)
    if event is not None and ProposedRetrievalLimit.PAGE_LIMIT in event.retrieval_limits:
        proposed.add(SourceReadTraceObligation.PAGINATION)
    if event is not None and ProposedRetrievalLimit.ROW_LIMIT in event.retrieval_limits:
        proposed.add(SourceReadTraceObligation.ROW_LIMIT)
    if executed_invocation is not None and executed_invocation.list_limit_default_applied is True:
        proposed.add(SourceReadTraceObligation.ROW_LIMIT)
    truncated = isinstance(subject, ToolResult) and subject.status is ToolResultStatus.PARTIAL
    if event is not None and (
        ProposedRetrievalLimit.RESULT_TRUNCATED in event.retrieval_limits
        or ProposedRetrievalLimit.SOURCE_WINDOW_LIMIT in event.retrieval_limits
        or _partial(event.completeness)
    ):
        truncated = True
    if any(_partial(item.completeness) for item in _evidence_of(subject)):
        truncated = True
    if truncated:
        proposed.add(SourceReadTraceObligation.TRUNCATION)
    if disposition in _ERROR_DISPOSITIONS or (
        isinstance(subject, ToolResult) and subject.status in _ERROR_TOOL_STATUSES
    ):
        proposed.add(SourceReadTraceObligation.ERROR_OUTCOME)
    if any(item.error_code is not None for item in _evidence_of(subject)):
        proposed.add(SourceReadTraceObligation.ERROR_OUTCOME)
    if disposition in _TIMEOUT_DISPOSITIONS:
        proposed.add(SourceReadTraceObligation.TIMEOUT_OUTCOME)
    if disposition in _CANCELLATION_DISPOSITIONS:
        proposed.add(SourceReadTraceObligation.CANCELLATION_OUTCOME)
    return frozenset(proposed)


def _argument_conflict(
    proposed: tuple[tuple[str, str], ...],
    executed: ExecutedToolInvocation,
) -> bool:
    if executed.requested_arguments != executed.executed_arguments:
        return True
    executed_args = executed.executed_arguments
    if any(not isinstance(value, str) for value in executed_args.values()):
        return bool(proposed) or bool(executed_args)
    return dict(proposed) != dict(executed_args)


def _claim_for(
    subject: ToolResult | Evidence | ProposedDispatcherExecutionEvent,
    verified_zero_claim: VerifiedZeroClaim | None,
) -> VerifiedZeroClaim | None:
    if isinstance(subject, ProposedDispatcherExecutionEvent):
        return subject.verified_zero_claim
    return verified_zero_claim


def _identity_conflict(
    subject: ToolResult | Evidence | ProposedDispatcherExecutionEvent,
    *,
    authorization: MasterInvocationAuthorization | None,
    executed_invocation: ExecutedToolInvocation | None,
    disposition: MasterDisposition | None,
    verified_zero_claim: VerifiedZeroClaim | None,
    classification: MasterProvenanceClassification,
) -> bool:
    conflict = any(
        limitation in _IDENTITY_LIMITATIONS
        for finding in classification.findings
        for limitation in finding.limitations
    )
    if (
        isinstance(subject, ProposedDispatcherExecutionEvent)
        and authorization is not None
        and authorization.expected_aircraft_id is not None
        and subject.entity_ids
        and authorization.expected_aircraft_id not in subject.entity_ids
    ):
        conflict = True
    if executed_invocation is not None:
        if isinstance(subject, (ProposedDispatcherExecutionEvent, ToolResult)) and (
            executed_invocation.tool_name != subject.tool_name
            or executed_invocation.tool_call_id != subject.tool_call_id
        ):
            conflict = True
        if isinstance(subject, Evidence) and executed_invocation.tool_call_id != subject.tool_call_id:
            conflict = True
        if isinstance(subject, ProposedDispatcherExecutionEvent) and _argument_conflict(
            subject.tool_arguments,
            executed_invocation,
        ):
            conflict = True
    claim = _claim_for(subject, verified_zero_claim)
    if claim is not None and claim.tool_call_id != _tool_call_id(subject):
        conflict = True
    success_shaped = (
        (isinstance(subject, ToolResult) and subject.status is ToolResultStatus.SUCCESS)
        or (
            isinstance(subject, ProposedDispatcherExecutionEvent)
            and subject.execution_trace_ref is not None
        )
        or disposition is MasterDisposition.EXECUTED
    )
    failure_shaped = disposition in _FAILURE_DISPOSITIONS or (
        isinstance(subject, ToolResult) and subject.status in _FAILURE_TOOL_STATUSES
    )
    if success_shaped and failure_shaped:
        conflict = True
    for evidence in _evidence_of(subject):
        cardinality = evidence.match_cardinality
        exact_zero = (
            cardinality is not None
            and cardinality.is_exact is True
            and cardinality.exact_count == 0
        )
        if evidence.source_records and exact_zero:
            conflict = True
        if evidence.source_records and any(
            item.rows_returned == 0 for item in evidence.query_executions
        ):
            conflict = True
    return conflict


@dataclass(frozen=True)
class SourceReadTraceObligationFinding:
    """One recording obligation. Presence is not observation and not proof."""

    obligation: SourceReadTraceObligation
    requirement: MasterProvenanceRequirement
    presence: SourceReadTracePresence
    trusted_runtime_observed: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.obligation, SourceReadTraceObligation):
            raise ContractValidationError("invalid_source_read_trace_obligation")
        if not isinstance(self.requirement, MasterProvenanceRequirement):
            raise ContractValidationError("invalid_provenance_requirement")
        if self.presence not in (
            SourceReadTracePresence.NOT_PROPOSED,
            SourceReadTracePresence.PROPOSED_UNOBSERVED,
        ):
            raise ContractValidationError("invalid_source_read_trace_presence")
        expected = dict(_OBLIGATION_REQUIREMENTS).get(self.obligation)
        if self.requirement is not expected:
            raise ContractValidationError("invalid_source_read_trace_obligation")
        if self.trusted_runtime_observed is not False:
            raise ContractValidationError("trusted_runtime_observation_not_available")


@dataclass(frozen=True)
class MasterSourceReadTraceAssessment:
    """Proposed trace metadata compared with an empty trusted-runtime record.

    ``classification`` is the existing provenance audit. None of the seven
    requirements is established. ``untrusted_absence_indication`` keeps each
    caller-supplied population signal on the evidence item or query that
    produced it. Missing source records are not a zero-row result, and the
    tuple is not an overall absence. Complete-population absence stays
    unavailable. The assessment is never true in a Boolean context, so it
    cannot be used as an execution gate.
    """

    classification: MasterProvenanceClassification
    obligations: tuple[SourceReadTraceObligationFinding, ...]
    proposed_identity_conflict: bool
    untrusted_absence_indication: tuple[UntrustedAbsenceIndication, ...]
    observed_trusted_runtime_events: tuple[object, ...] = ()
    trusted_dispatcher_implemented: bool = False
    trusted_runtime_observation_available: bool = False
    caller_supplied_trace_authenticates_read: bool = False
    matching_timestamp_authenticates_read: bool = False
    matching_source_version_authenticates_read: bool = False
    matching_request_id_authenticates_read: bool = False
    capability_consumption_permits_dispatch: bool = False
    empty_result_creates_verified_zero: bool = False
    verified_zero_claim_minted: bool = False
    verified_zero_established: bool = False
    complete_population_absence_available: bool = False
    source_authenticity: MasterSourceAuthenticity = MasterSourceAuthenticity.NOT_ESTABLISHED
    production_authority: MasterProductionAuthority = MasterProductionAuthority.DENIED
    execution_authority: MasterRouteExecutionAuthority = MasterRouteExecutionAuthority.DENIED
    request_coverage: MasterRequestCoverage = MasterRequestCoverage.NOT_ESTABLISHED
    dispatch_permitted: bool = False
    end_to_end_fulfilled: bool = False
    schema_version: str = MASTER_SOURCE_READ_TRACE_BOUNDARY_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != MASTER_SOURCE_READ_TRACE_BOUNDARY_SCHEMA_VERSION:
            raise ContractValidationError("invalid_schema_version")
        _deny_authority(
            source_authenticity=self.source_authenticity,
            production_authority=self.production_authority,
            execution_authority=self.execution_authority,
            request_coverage=self.request_coverage,
            dispatch_permitted=self.dispatch_permitted,
            end_to_end_fulfilled=self.end_to_end_fulfilled,
        )
        if not isinstance(self.classification, MasterProvenanceClassification):
            raise ContractValidationError("invalid_provenance_classification")
        if self.classification.authenticated_source_read_available is not False:
            raise ContractValidationError("source_authenticity_not_established")
        if tuple(item.requirement for item in self.classification.findings) != tuple(
            MasterProvenanceRequirement
        ):
            raise ContractValidationError("invalid_provenance_findings")
        if any(
            item.status
            not in (
                ProvenanceRequirementStatus.UNRESOLVED,
                ProvenanceRequirementStatus.UNSATISFIED,
            )
            for item in self.classification.findings
        ):
            raise ContractValidationError("provenance_requirement_not_satisfied")
        if not isinstance(self.obligations, tuple) or any(
            not isinstance(item, SourceReadTraceObligationFinding) for item in self.obligations
        ):
            raise ContractValidationError("invalid_source_read_trace_obligations")
        if tuple(item.obligation for item in self.obligations) != tuple(SourceReadTraceObligation):
            raise ContractValidationError("invalid_source_read_trace_obligations")
        if self.observed_trusted_runtime_events != ():
            raise ContractValidationError("trusted_runtime_observation_not_available")
        if not isinstance(self.proposed_identity_conflict, bool):
            raise ContractValidationError("invalid_source_read_trace_assessment")
        if not isinstance(self.untrusted_absence_indication, tuple) or any(
            not isinstance(item, UntrustedAbsenceIndication)
            for item in self.untrusted_absence_indication
        ):
            raise ContractValidationError("invalid_untrusted_absence_indication")
        closed_flags = (
            self.trusted_dispatcher_implemented,
            self.trusted_runtime_observation_available,
            self.caller_supplied_trace_authenticates_read,
            self.matching_timestamp_authenticates_read,
            self.matching_source_version_authenticates_read,
            self.matching_request_id_authenticates_read,
            self.capability_consumption_permits_dispatch,
            self.empty_result_creates_verified_zero,
            self.verified_zero_claim_minted,
            self.verified_zero_established,
            self.complete_population_absence_available,
        )
        if any(item is not False for item in closed_flags):
            raise ContractValidationError("source_authenticity_not_established")

    def __bool__(self) -> bool:
        return False

    @property
    def missing_provenance(self) -> tuple[MasterProvenanceRequirement, ...]:
        """Every provenance requirement stays missing."""

        return tuple(MasterProvenanceRequirement)

    @property
    def authenticated_source_read_available(self) -> bool:
        return AUTHENTICATED_SOURCE_READ_AVAILABLE

    def obligation(self, obligation: SourceReadTraceObligation) -> SourceReadTraceObligationFinding:
        if not isinstance(obligation, SourceReadTraceObligation):
            raise ContractValidationError("invalid_source_read_trace_obligation")
        for item in self.obligations:
            if item.obligation is obligation:
                return item
        raise ContractValidationError("invalid_source_read_trace_obligations")


def assess_source_read_trace(
    subject: ToolResult | Evidence | ProposedDispatcherExecutionEvent,
    *,
    authorization: MasterInvocationAuthorization | None = None,
    verified_zero_claim: VerifiedZeroClaim | None = None,
    executed_invocation: ExecutedToolInvocation | None = None,
    disposition: MasterDisposition | None = None,
    source_authenticity: object = MasterSourceAuthenticity.NOT_ESTABLISHED,
    production_authority: object = MasterProductionAuthority.DENIED,
    execution_authority: object = MasterRouteExecutionAuthority.DENIED,
    request_coverage: object = MasterRequestCoverage.NOT_ESTABLISHED,
    dispatch_permitted: object = False,
    end_to_end_fulfilled: object = False,
) -> MasterSourceReadTraceAssessment:
    """Assess proposed trace metadata. No trusted runtime event is observed.

    ``executed_invocation`` and ``disposition`` are caller-supplied records.
    They do not show that a dispatcher ran. ``ToolResult.data`` is not read.
    This function does not construct a ``VerifiedZeroClaim``.
    """

    _deny_authority(
        source_authenticity=source_authenticity,
        production_authority=production_authority,
        execution_authority=execution_authority,
        request_coverage=request_coverage,
        dispatch_permitted=dispatch_permitted,
        end_to_end_fulfilled=end_to_end_fulfilled,
    )
    if executed_invocation is not None and type(executed_invocation) is not ExecutedToolInvocation:
        raise ContractValidationError("invalid_executed_tool_invocation")
    if disposition is not None and not isinstance(disposition, MasterDisposition):
        raise ContractValidationError("invalid_disposition")
    if verified_zero_claim is not None and not isinstance(verified_zero_claim, VerifiedZeroClaim):
        raise ContractValidationError("invalid_verified_zero_claim")
    classification = classify_master_provenance(
        subject,
        authorization=authorization,
        verified_zero_claim=verified_zero_claim,
    )
    proposed = _proposed(
        subject,
        authorization=authorization,
        executed_invocation=executed_invocation,
        disposition=disposition,
    )
    obligations = tuple(
        SourceReadTraceObligationFinding(
            obligation=obligation,
            requirement=requirement,
            presence=(
                SourceReadTracePresence.PROPOSED_UNOBSERVED
                if obligation in proposed
                else SourceReadTracePresence.NOT_PROPOSED
            ),
        )
        for obligation, requirement in _OBLIGATION_REQUIREMENTS
    )
    return MasterSourceReadTraceAssessment(
        classification=classification,
        obligations=obligations,
        proposed_identity_conflict=_identity_conflict(
            subject,
            authorization=authorization,
            executed_invocation=executed_invocation,
            disposition=disposition,
            verified_zero_claim=verified_zero_claim,
            classification=classification,
        ),
        untrusted_absence_indication=_untrusted_absence_indication(
            subject,
            verified_zero_claim,
        ),
    )
