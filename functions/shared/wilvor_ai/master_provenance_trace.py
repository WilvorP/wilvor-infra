"""Offline provenance-audit contracts. Not source authentication.

Three concepts stay distinct:

1. Descriptive ``ToolResult`` and ``Evidence`` metadata. Those public fields
   describe a caller-supplied object. They are not an authenticated read.
2. Proposed dispatcher-recorded execution events. ``ProposedDispatcherExecutionEvent``
   is the PROPOSED shape a future trusted dispatcher might record. This
   milestone has no trusted dispatcher, so a well-formed event is not a
   completed read and is not a consumed permit.
3. Actual authenticated source-read provenance. That proof is not available.
   No subject kind, constructor, or Boolean in this module marks provenance
   authenticated. ``AUTHENTICATED_SOURCE_READ_AVAILABLE`` is false.

``classify_master_provenance`` is fail-closed for the seven existing
``MasterProvenanceRequirement`` categories. Caller-supplied metadata and
proposed audit records stay ``UNRESOLVED`` or ``UNSATISFIED``. Matching
source versions, timestamps, or request identifiers do not establish the
origin of a read. Public dataclass fields are not proof of trust.

Clock roles stay distinct. Source-event time, query time, observation time,
evaluation time, requested evaluation time, tool as-of time, and completeness
evaluated-as-of time are different clocks. Different values across those
roles are not ``INCONSISTENT_TIMESTAMPS``. That limitation is assigned only
when one versioned source record states two source-event times. The identity
used for that comparison is already on the evidence: its ``source``, the
record's ``record_id``, and its ``source_version``. A missing version or
event time is left missing and is not compared.

Source-version strings from different records or datasets are not required
to be equal. ``SOURCE_VERSIONS_DISAGREE`` is assigned only when one evidence
``source``, ``record_id``, and source-event instant identify one record and
the present version strings differ. A present version otherwise stays
``SOURCE_VERSION_NOT_INTEGRITY_PROOF`` without a claimed contradiction.

Source authenticity stays ``NOT_ESTABLISHED``. Production authority stays
``DENIED``. Master execution authority stays ``DENIED``. A model object, a
planning authorization, a binder, or supplied evidence cannot grant
execution. This module does not call a specialist, a model, AWS, or a
provider, and it does not open a dispatch path.

PROPOSED interfaces, not implemented and not imported:

- A future permit authority might consume ``permit_consumption_ref``.
  This module does not consume a permit.
- A future capability registry might own ``adapter_identity``. This module
  does not resolve or trust an adapter.
- A future gate or execution-scope check might read a classification.
  This module does not import that check and does not open a gate.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from wilvor_ai.contracts import (
    PROVENANCE_TOKEN_MAX_LENGTH,
    ContractValidationError,
    Evidence,
    FreshnessStatus,
    SourceCompleteness,
    ToolResult,
    ToolResultStatus,
    _validate_bounded_text,
    _validate_string_tuple,
    _validate_utc,
)
from wilvor_ai.master_contracts import (
    MasterInvocationAuthorization,
    MasterProductionAuthority,
)
from wilvor_ai.master_evidence_admission import MasterSourceAuthenticity
from wilvor_ai.master_parallel_contracts import MasterProvenanceRequirement
from wilvor_ai.master_routing import (
    MasterRequestCoverage,
    MasterRouteExecutionAuthority,
)
from wilvor_ai.specialist_contracts import VerifiedZeroClaim


MASTER_PROVENANCE_TRACE_SCHEMA_VERSION = "wilvor.ai.master_provenance_trace.v1"

# The third concept. There is no constructor that flips this.
AUTHENTICATED_SOURCE_READ_AVAILABLE = False

MAX_PROVENANCE_ITEMS = 32

_PARTIAL_COMPLETENESS = frozenset({"PARTIAL", "INCOMPLETE", "LIMITED", "TRUNCATED"})


class ProvenanceSubjectKind(str, Enum):
    """Which supplied object a classification describes.

    Authenticated source-read provenance is not a subject kind. It cannot
    be constructed in this milestone.
    """

    DESCRIPTIVE_TOOL_RESULT = "DESCRIPTIVE_TOOL_RESULT"
    DESCRIPTIVE_EVIDENCE = "DESCRIPTIVE_EVIDENCE"
    PROPOSED_DISPATCHER_EVENT = "PROPOSED_DISPATCHER_EVENT"


class ProvenanceRequirementStatus(str, Enum):
    """Fail-closed status. Satisfied is not a member."""

    UNRESOLVED = "UNRESOLVED"
    UNSATISFIED = "UNSATISFIED"


class ProvenanceAuditLimitation(str, Enum):
    """Why a provenance requirement stays closed."""

    AUTHENTICATED_SOURCE_READ_NOT_AVAILABLE = "AUTHENTICATED_SOURCE_READ_NOT_AVAILABLE"
    CALLER_SUPPLIED_METADATA_NOT_PROOF = "CALLER_SUPPLIED_METADATA_NOT_PROOF"
    PROPOSED_DISPATCHER_EVENT_NOT_TRUSTED = "PROPOSED_DISPATCHER_EVENT_NOT_TRUSTED"
    PUBLIC_FIELDS_NOT_TRUST = "PUBLIC_FIELDS_NOT_TRUST"
    MATCHING_IDENTIFIERS_NOT_ORIGIN = "MATCHING_IDENTIFIERS_NOT_ORIGIN"
    FOREIGN_REQUEST = "FOREIGN_REQUEST"
    FOREIGN_INVOCATION = "FOREIGN_INVOCATION"
    CORRELATION_MISMATCH = "CORRELATION_MISMATCH"
    MISSING_SOURCE_VERSION = "MISSING_SOURCE_VERSION"
    SOURCE_VERSION_NOT_INTEGRITY_PROOF = "SOURCE_VERSION_NOT_INTEGRITY_PROOF"
    SOURCE_VERSIONS_DISAGREE = "SOURCE_VERSIONS_DISAGREE"
    INCONSISTENT_TIMESTAMPS = "INCONSISTENT_TIMESTAMPS"
    TIMESTAMPS_NOT_OBSERVATION_PROOF = "TIMESTAMPS_NOT_OBSERVATION_PROOF"
    PARTIAL_POPULATION_SCOPE = "PARTIAL_POPULATION_SCOPE"
    POPULATION_SCOPE_NOT_ESTABLISHED = "POPULATION_SCOPE_NOT_ESTABLISHED"
    RETRIEVAL_LIMITED = "RETRIEVAL_LIMITED"
    STALE_DATA = "STALE_DATA"
    FRESHNESS_NOT_AUTHENTICATED = "FRESHNESS_NOT_AUTHENTICATED"
    FABRICATED_VERIFIED_ZERO = "FABRICATED_VERIFIED_ZERO"
    FORGED_EXECUTION_TRACE = "FORGED_EXECUTION_TRACE"
    NO_TRUSTED_ADAPTER = "NO_TRUSTED_ADAPTER"
    NO_AUTHORIZED_EXECUTION_CONTEXT = "NO_AUTHORIZED_EXECUTION_CONTEXT"
    PERMIT_REFERENCE_NOT_CONSUMPTION = "PERMIT_REFERENCE_NOT_CONSUMPTION"
    ENTITY_SCOPE_NOT_BINDING = "ENTITY_SCOPE_NOT_BINDING"
    INVOCATION_NOT_IN_DESCRIPTIVE_METADATA = "INVOCATION_NOT_IN_DESCRIPTIVE_METADATA"


class ProposedPopulationScope(str, Enum):
    """PROPOSED population label. A complete claim is not completeness."""

    UNSPECIFIED = "UNSPECIFIED"
    PARTIAL = "PARTIAL"
    CLAIMED_COMPLETE = "CLAIMED_COMPLETE"


class ProposedRetrievalLimit(str, Enum):
    """PROPOSED retrieval limitation. Declaring none does not prove completeness."""

    RESULT_TRUNCATED = "RESULT_TRUNCATED"
    ROW_LIMIT = "ROW_LIMIT"
    PAGE_LIMIT = "PAGE_LIMIT"
    SOURCE_WINDOW_LIMIT = "SOURCE_WINDOW_LIMIT"


def _token(value: object, field_name: str, *, optional: bool = False) -> None:
    errors = _validate_bounded_text(
        value,
        field_name,
        optional=optional,
        max_length=PROVENANCE_TOKEN_MAX_LENGTH,
    )
    if errors:
        raise ContractValidationError(errors)
    if isinstance(value, str) and value != value.strip():
        raise ContractValidationError(f"invalid_{field_name}")


def _clock(value: object, field_name: str) -> None:
    errors = _validate_utc(value, field_name, optional=True)
    if errors:
        raise ContractValidationError(errors)


def _bounded_count(value: tuple[object, ...], field_name: str) -> None:
    if len(value) > MAX_PROVENANCE_ITEMS:
        raise ContractValidationError("excessive_provenance_items")
    if not isinstance(value, tuple):
        raise ContractValidationError(f"invalid_{field_name}")


def _deny_authenticity(
    *,
    source_authenticity: object,
    production_authority: object,
    execution_authority: object,
    request_coverage: object,
    dispatch_permitted: object,
    end_to_end_fulfilled: object,
) -> None:
    if source_authenticity is not MasterSourceAuthenticity.NOT_ESTABLISHED:
        raise ContractValidationError("source_authenticity_not_established")
    if production_authority is not MasterProductionAuthority.DENIED:
        raise ContractValidationError("production_authority_not_issued")
    if execution_authority is not MasterRouteExecutionAuthority.DENIED:
        raise ContractValidationError("execution_authority_not_granted")
    if request_coverage is not MasterRequestCoverage.NOT_ESTABLISHED:
        raise ContractValidationError("request_coverage_not_established")
    if dispatch_permitted is not False:
        raise ContractValidationError("execution_not_granted")
    if end_to_end_fulfilled is not False:
        raise ContractValidationError("end_to_end_fulfillment_not_granted")


def _tool_arguments(value: object) -> tuple[tuple[str, str], ...]:
    if not isinstance(value, tuple):
        raise ContractValidationError("invalid_tool_arguments")
    _bounded_count(value, "tool_arguments")
    names: list[str] = []
    checked: list[tuple[str, str]] = []
    for item in value:
        if not isinstance(item, tuple) or len(item) != 2:
            raise ContractValidationError("invalid_tool_arguments")
        name, argument = item
        _token(name, "tool_arguments")
        _token(argument, "tool_arguments")
        if name in names:
            raise ContractValidationError("duplicate_tool_argument")
        names.append(name)
        checked.append((name, argument))
    return tuple(checked)


def _entity_ids(value: object) -> tuple[str, ...]:
    if not isinstance(value, tuple):
        raise ContractValidationError("invalid_entity_ids")
    _bounded_count(value, "entity_ids")
    checked: list[str] = []
    for item in value:
        _token(item, "entity_ids")
        checked.append(item)
    return tuple(checked)


def _notes(value: object) -> tuple[str, ...]:
    if not isinstance(value, tuple):
        raise ContractValidationError("invalid_limitation_notes")
    _bounded_count(value, "limitation_notes")
    errors = _validate_string_tuple(
        value,
        "limitation_notes",
        max_length=PROVENANCE_TOKEN_MAX_LENGTH,
    )
    if errors:
        raise ContractValidationError(errors)
    for item in value:
        if item != item.strip():
            raise ContractValidationError("invalid_limitation_notes")
    return value


def _retrieval_limits(value: object) -> tuple[ProposedRetrievalLimit, ...]:
    if not isinstance(value, tuple) or any(
        not isinstance(item, ProposedRetrievalLimit) for item in value
    ):
        raise ContractValidationError("invalid_retrieval_limits")
    _bounded_count(value, "retrieval_limits")
    if len(value) != len(set(value)):
        raise ContractValidationError("invalid_retrieval_limits")
    return value


@dataclass(frozen=True)
class ProposedDispatcherExecutionEvent:
    """PROPOSED dispatcher audit record. Not a trusted execution.

    Every identity, permit reference, adapter label, tool argument, source
    response reference, clock, version, and scope below is caller-supplied.
    Constructing this object does not call a tool, consume a permit, or
    establish the origin of a read.
    """

    master_request_id: str
    master_invocation_id: str
    tool_name: str
    tool_call_id: str
    population_scope: ProposedPopulationScope
    specialist_invocation_id: str | None = None
    correlation_id: str | None = None
    permit_consumption_ref: str | None = None
    adapter_identity: str | None = None
    tool_arguments: tuple[tuple[str, str], ...] = ()
    source_response_ref: str | None = None
    observation_time_utc: str | None = None
    evaluation_time_utc: str | None = None
    source_version: str | None = None
    entity_ids: tuple[str, ...] = ()
    completeness: SourceCompleteness | None = None
    retrieval_limits: tuple[ProposedRetrievalLimit, ...] = ()
    limitation_notes: tuple[str, ...] = ()
    freshness_status: FreshnessStatus | None = None
    execution_trace_ref: str | None = None
    verified_zero_claim: VerifiedZeroClaim | None = None
    source_authenticity: MasterSourceAuthenticity = MasterSourceAuthenticity.NOT_ESTABLISHED
    production_authority: MasterProductionAuthority = MasterProductionAuthority.DENIED
    execution_authority: MasterRouteExecutionAuthority = MasterRouteExecutionAuthority.DENIED
    request_coverage: MasterRequestCoverage = MasterRequestCoverage.NOT_ESTABLISHED
    dispatch_permitted: bool = False
    end_to_end_fulfilled: bool = False
    schema_version: str = MASTER_PROVENANCE_TRACE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != MASTER_PROVENANCE_TRACE_SCHEMA_VERSION:
            raise ContractValidationError("invalid_schema_version")
        _deny_authenticity(
            source_authenticity=self.source_authenticity,
            production_authority=self.production_authority,
            execution_authority=self.execution_authority,
            request_coverage=self.request_coverage,
            dispatch_permitted=self.dispatch_permitted,
            end_to_end_fulfilled=self.end_to_end_fulfilled,
        )
        _token(self.master_request_id, "master_request_id")
        _token(self.master_invocation_id, "master_invocation_id")
        _token(self.tool_name, "tool_name")
        _token(self.tool_call_id, "tool_call_id")
        _token(self.specialist_invocation_id, "specialist_invocation_id", optional=True)
        _token(self.correlation_id, "correlation_id", optional=True)
        _token(self.permit_consumption_ref, "permit_consumption_ref", optional=True)
        _token(self.adapter_identity, "adapter_identity", optional=True)
        _token(self.source_response_ref, "source_response_ref", optional=True)
        _token(self.source_version, "source_version", optional=True)
        _token(self.execution_trace_ref, "execution_trace_ref", optional=True)
        _clock(self.observation_time_utc, "observation_time_utc")
        _clock(self.evaluation_time_utc, "evaluation_time_utc")
        if not isinstance(self.population_scope, ProposedPopulationScope):
            raise ContractValidationError("invalid_population_scope")
        if self.completeness is not None and not isinstance(self.completeness, SourceCompleteness):
            raise ContractValidationError("invalid_completeness")
        if self.freshness_status is not None and not isinstance(self.freshness_status, FreshnessStatus):
            raise ContractValidationError("invalid_freshness_status")
        if self.verified_zero_claim is not None and not isinstance(
            self.verified_zero_claim,
            VerifiedZeroClaim,
        ):
            raise ContractValidationError("invalid_verified_zero_claim")
        object.__setattr__(self, "tool_arguments", _tool_arguments(self.tool_arguments))
        object.__setattr__(self, "entity_ids", _entity_ids(self.entity_ids))
        object.__setattr__(self, "retrieval_limits", _retrieval_limits(self.retrieval_limits))
        object.__setattr__(self, "limitation_notes", _notes(self.limitation_notes))

    @property
    def authenticated_source_read_available(self) -> bool:
        """Authenticated source-read provenance is not available on this record."""

        return AUTHENTICATED_SOURCE_READ_AVAILABLE


@dataclass(frozen=True)
class ProvenanceRequirementFinding:
    """One closed category. The status cannot say the requirement is satisfied."""

    requirement: MasterProvenanceRequirement
    status: ProvenanceRequirementStatus
    limitations: tuple[ProvenanceAuditLimitation, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.requirement, MasterProvenanceRequirement):
            raise ContractValidationError("invalid_provenance_requirement")
        if self.status not in (
            ProvenanceRequirementStatus.UNRESOLVED,
            ProvenanceRequirementStatus.UNSATISFIED,
        ):
            raise ContractValidationError("provenance_requirement_not_satisfied")
        if not isinstance(self.limitations, tuple) or any(
            not isinstance(item, ProvenanceAuditLimitation) for item in self.limitations
        ):
            raise ContractValidationError("invalid_provenance_limitations")
        if ProvenanceAuditLimitation.AUTHENTICATED_SOURCE_READ_NOT_AVAILABLE not in self.limitations:
            raise ContractValidationError("source_authenticity_not_established")
        if len(self.limitations) != len(set(self.limitations)):
            raise ContractValidationError("invalid_provenance_limitations")


@dataclass(frozen=True)
class MasterProvenanceClassification:
    """Fail-closed audit of the seven provenance requirements.

    The findings cover ``MasterProvenanceRequirement`` in definition order.
    None of them is satisfied. Authority fields stay denied.
    """

    subject_kind: ProvenanceSubjectKind
    findings: tuple[ProvenanceRequirementFinding, ...]
    source_authenticity: MasterSourceAuthenticity = MasterSourceAuthenticity.NOT_ESTABLISHED
    production_authority: MasterProductionAuthority = MasterProductionAuthority.DENIED
    execution_authority: MasterRouteExecutionAuthority = MasterRouteExecutionAuthority.DENIED
    request_coverage: MasterRequestCoverage = MasterRequestCoverage.NOT_ESTABLISHED
    dispatch_permitted: bool = False
    end_to_end_fulfilled: bool = False
    schema_version: str = MASTER_PROVENANCE_TRACE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != MASTER_PROVENANCE_TRACE_SCHEMA_VERSION:
            raise ContractValidationError("invalid_schema_version")
        _deny_authenticity(
            source_authenticity=self.source_authenticity,
            production_authority=self.production_authority,
            execution_authority=self.execution_authority,
            request_coverage=self.request_coverage,
            dispatch_permitted=self.dispatch_permitted,
            end_to_end_fulfilled=self.end_to_end_fulfilled,
        )
        if not isinstance(self.subject_kind, ProvenanceSubjectKind):
            raise ContractValidationError("invalid_provenance_subject_kind")
        if not isinstance(self.findings, tuple) or any(
            not isinstance(item, ProvenanceRequirementFinding) for item in self.findings
        ):
            raise ContractValidationError("invalid_provenance_findings")
        if tuple(item.requirement for item in self.findings) != tuple(MasterProvenanceRequirement):
            raise ContractValidationError("invalid_provenance_findings")

    @property
    def authenticated_source_read_available(self) -> bool:
        """Authenticated source-read provenance is not available for this classification."""

        return AUTHENTICATED_SOURCE_READ_AVAILABLE

    def finding(self, requirement: MasterProvenanceRequirement) -> ProvenanceRequirementFinding:
        if not isinstance(requirement, MasterProvenanceRequirement):
            raise ContractValidationError("invalid_provenance_requirement")
        for item in self.findings:
            if item.requirement is requirement:
                return item
        raise ContractValidationError("invalid_provenance_findings")


def _kind_limitation(kind: ProvenanceSubjectKind) -> ProvenanceAuditLimitation:
    if kind is ProvenanceSubjectKind.PROPOSED_DISPATCHER_EVENT:
        return ProvenanceAuditLimitation.PROPOSED_DISPATCHER_EVENT_NOT_TRUSTED
    return ProvenanceAuditLimitation.CALLER_SUPPLIED_METADATA_NOT_PROOF


def _finding(
    requirement: MasterProvenanceRequirement,
    status: ProvenanceRequirementStatus,
    limitations: tuple[ProvenanceAuditLimitation, ...] | list[ProvenanceAuditLimitation],
    *,
    kind: ProvenanceSubjectKind,
) -> ProvenanceRequirementFinding:
    ordered = (
        ProvenanceAuditLimitation.AUTHENTICATED_SOURCE_READ_NOT_AVAILABLE,
        _kind_limitation(kind),
        *limitations,
    )
    return ProvenanceRequirementFinding(
        requirement=requirement,
        status=status,
        limitations=tuple(dict.fromkeys(ordered)),
    )


def _closed_status(material: bool) -> ProvenanceRequirementStatus:
    if material:
        return ProvenanceRequirementStatus.UNSATISFIED
    return ProvenanceRequirementStatus.UNRESOLVED


@dataclass(frozen=True)
class _RecordedSource:
    """One caller-supplied source row. Missing fields stay missing."""

    dataset: str
    record_id: str
    source_version: str | None
    event_timestamp_utc: str | None


def _parse_utc(value: str) -> datetime:
    text = value[:-1] + "+00:00" if value.endswith("Z") else value
    return datetime.fromisoformat(text)


def _clocks(*values: str | None) -> tuple[str, ...]:
    return tuple(value for value in values if value is not None)


def _recorded_sources(evidence_items: tuple[Evidence, ...]) -> tuple[_RecordedSource, ...]:
    return tuple(
        _RecordedSource(
            dataset=evidence.source,
            record_id=record.record_id,
            source_version=record.source_version,
            event_timestamp_utc=record.event_timestamp_utc,
        )
        for evidence in evidence_items
        for record in evidence.source_records
    )


def _version_conflict(records: tuple[_RecordedSource, ...]) -> bool:
    """Two present versions for one dataset, record, and source-event instant.

    Records that omit a version or an event time are not given stand-in
    values, and records from different datasets are not compared.
    """

    grouped: dict[tuple[str, str, datetime], set[str]] = {}
    for record in records:
        if record.source_version is None or record.event_timestamp_utc is None:
            continue
        key = (record.dataset, record.record_id, _parse_utc(record.event_timestamp_utc))
        grouped.setdefault(key, set()).add(record.source_version)
    return any(len(versions) > 1 for versions in grouped.values())


def _event_time_conflict(records: tuple[_RecordedSource, ...]) -> bool:
    """Two source-event times for one dataset, record, and source version.

    This is the only timestamp contradiction this module assigns. Query time,
    observation time, evaluation time, requested evaluation time, tool as-of
    time, and completeness evaluated-as-of time are other roles and are not
    compared here. A missing version or event time is not invented.
    """

    grouped: dict[tuple[str, str, str], set[datetime]] = {}
    for record in records:
        if record.source_version is None or record.event_timestamp_utc is None:
            continue
        key = (record.dataset, record.record_id, record.source_version)
        grouped.setdefault(key, set()).add(_parse_utc(record.event_timestamp_utc))
    return any(len(instants) > 1 for instants in grouped.values())


def _authorization_or_none(
    authorization: MasterInvocationAuthorization | None,
) -> MasterInvocationAuthorization | None:
    if authorization is None:
        return None
    if not isinstance(authorization, MasterInvocationAuthorization):
        raise ContractValidationError("invalid_invocation_authorization")
    return authorization


def _identity_limitations(
    *,
    authorization: MasterInvocationAuthorization | None,
    master_request_id: str | None,
    master_invocation_id: str | None,
    specialist_invocation_id: str | None,
    correlation_id: str | None,
    descriptive_only: bool,
) -> tuple[ProvenanceAuditLimitation, ...]:
    """Compare labels. A match is not the origin of a read."""

    limits: list[ProvenanceAuditLimitation] = []
    if descriptive_only:
        limits.append(ProvenanceAuditLimitation.INVOCATION_NOT_IN_DESCRIPTIVE_METADATA)
    labels = (
        master_request_id,
        master_invocation_id,
        specialist_invocation_id,
        correlation_id,
    )
    if any(item is not None for item in labels):
        limits.append(ProvenanceAuditLimitation.PUBLIC_FIELDS_NOT_TRUST)
    if authorization is None:
        return tuple(dict.fromkeys(limits))

    request_foreign = (
        master_request_id is not None and master_request_id != authorization.master_request_id
    )
    invocation_foreign = (
        master_invocation_id is not None and master_invocation_id != authorization.invocation_id
    )
    specialist_foreign = (
        specialist_invocation_id is not None
        and authorization.specialist_request_id is not None
        and specialist_invocation_id != authorization.specialist_request_id
    )
    correlation_foreign = (
        correlation_id is not None
        and authorization.correlation_id is not None
        and correlation_id != authorization.correlation_id
    )
    if request_foreign:
        limits.append(ProvenanceAuditLimitation.FOREIGN_REQUEST)
    if invocation_foreign or specialist_foreign:
        limits.append(ProvenanceAuditLimitation.FOREIGN_INVOCATION)
    if correlation_foreign:
        limits.append(ProvenanceAuditLimitation.CORRELATION_MISMATCH)
    compared = (
        master_request_id is not None
        or master_invocation_id is not None
        or (specialist_invocation_id is not None and authorization.specialist_request_id is not None)
        or (correlation_id is not None and authorization.correlation_id is not None)
    )
    if compared and not any((request_foreign, invocation_foreign, specialist_foreign, correlation_foreign)):
        limits.append(ProvenanceAuditLimitation.MATCHING_IDENTIFIERS_NOT_ORIGIN)
    return tuple(dict.fromkeys(limits))


def _zero_limitations(
    claim: VerifiedZeroClaim | None,
    *,
    tool_call_id: str | None,
    exact_zero: bool,
) -> tuple[ProvenanceAuditLimitation, ...]:
    if claim is None and not exact_zero:
        return ()
    limits = [ProvenanceAuditLimitation.FABRICATED_VERIFIED_ZERO]
    if claim is not None and tool_call_id is not None and claim.tool_call_id == tool_call_id:
        limits.append(ProvenanceAuditLimitation.MATCHING_IDENTIFIERS_NOT_ORIGIN)
    return tuple(limits)


def _version_limits(
    *,
    present: bool,
    missing: bool,
    conflict: bool,
) -> tuple[ProvenanceRequirementStatus, tuple[ProvenanceAuditLimitation, ...]]:
    limits: list[ProvenanceAuditLimitation] = []
    if missing:
        limits.append(ProvenanceAuditLimitation.MISSING_SOURCE_VERSION)
    if present:
        # A version string is caller-supplied text. Agreement is not integrity.
        limits.append(ProvenanceAuditLimitation.SOURCE_VERSION_NOT_INTEGRITY_PROOF)
        limits.append(ProvenanceAuditLimitation.PUBLIC_FIELDS_NOT_TRUST)
    if conflict:
        limits.append(ProvenanceAuditLimitation.SOURCE_VERSIONS_DISAGREE)
    return _closed_status(present), tuple(limits)


def _version_assessment(
    records: tuple[_RecordedSource, ...],
) -> tuple[ProvenanceRequirementStatus, tuple[ProvenanceAuditLimitation, ...]]:
    if not records:
        return _version_limits(present=False, missing=True, conflict=False)
    present_versions = tuple(
        record.source_version for record in records if record.source_version is not None
    )
    return _version_limits(
        present=bool(present_versions),
        missing=not present_versions or any(record.source_version is None for record in records),
        conflict=_version_conflict(records),
    )


def _lone_version_assessment(
    source_version: str | None,
) -> tuple[ProvenanceRequirementStatus, tuple[ProvenanceAuditLimitation, ...]]:
    """One proposed version label. It has no second record to contradict."""

    if source_version is None:
        return _version_limits(present=False, missing=True, conflict=False)
    return _version_limits(present=True, missing=False, conflict=False)


def _time_assessment(
    *,
    clocks_present: bool,
    contradictory: bool,
    freshness: tuple[FreshnessStatus, ...],
) -> tuple[ProvenanceRequirementStatus, tuple[ProvenanceAuditLimitation, ...]]:
    limits: list[ProvenanceAuditLimitation] = []
    if contradictory:
        limits.append(ProvenanceAuditLimitation.INCONSISTENT_TIMESTAMPS)
    if clocks_present:
        limits.append(ProvenanceAuditLimitation.TIMESTAMPS_NOT_OBSERVATION_PROOF)
        limits.append(ProvenanceAuditLimitation.PUBLIC_FIELDS_NOT_TRUST)
    if any(item is FreshnessStatus.STALE for item in freshness):
        limits.append(ProvenanceAuditLimitation.STALE_DATA)
    if freshness:
        limits.append(ProvenanceAuditLimitation.FRESHNESS_NOT_AUTHENTICATED)
    return _closed_status(bool(limits)), tuple(limits)


def _proposed_clocks_present(
    event: ProposedDispatcherExecutionEvent,
    authorization: MasterInvocationAuthorization | None,
) -> bool:
    requested = None if authorization is None else authorization.requested_evaluation_time_utc
    return any(
        value is not None
        for value in (
            event.observation_time_utc,
            event.evaluation_time_utc,
            _completeness_clock(event.completeness),
            requested,
        )
    )


def _evidence_clocks_present(
    evidence_items: tuple[Evidence, ...],
    *,
    extra_timestamps: tuple[str, ...],
    authorization: MasterInvocationAuthorization | None,
) -> bool:
    if extra_timestamps:
        return True
    if authorization is not None and authorization.requested_evaluation_time_utc is not None:
        return True
    for evidence in evidence_items:
        if evidence.query_timestamp_utc or _completeness_clock(evidence.completeness) is not None:
            return True
        if any(record.event_timestamp_utc is not None for record in evidence.source_records):
            return True
    return False


def _partial_completeness(completeness: SourceCompleteness | None) -> bool:
    if completeness is None or completeness.status is None:
        return False
    return completeness.status.strip().upper() in _PARTIAL_COMPLETENESS


def _population_from_evidence(evidence: Evidence) -> list[ProvenanceAuditLimitation]:
    limits: list[ProvenanceAuditLimitation] = [
        ProvenanceAuditLimitation.POPULATION_SCOPE_NOT_ESTABLISHED,
    ]
    if evidence.limitations:
        limits.append(ProvenanceAuditLimitation.RETRIEVAL_LIMITED)
    if evidence.completeness is not None:
        if _partial_completeness(evidence.completeness):
            limits.append(ProvenanceAuditLimitation.PARTIAL_POPULATION_SCOPE)
        limits.append(ProvenanceAuditLimitation.PUBLIC_FIELDS_NOT_TRUST)
    cardinality = evidence.match_cardinality
    if cardinality is not None and cardinality.is_exact is False:
        limits.append(ProvenanceAuditLimitation.PARTIAL_POPULATION_SCOPE)
    if cardinality is not None and cardinality.is_exact is True:
        if cardinality.exact_count == 0:
            limits.append(ProvenanceAuditLimitation.FABRICATED_VERIFIED_ZERO)
        limits.append(ProvenanceAuditLimitation.PUBLIC_FIELDS_NOT_TRUST)
    return limits


def _population_material(limits: list[ProvenanceAuditLimitation]) -> bool:
    baseline = ProvenanceAuditLimitation.POPULATION_SCOPE_NOT_ESTABLISHED
    return any(item is not baseline for item in limits)


def _completeness_clock(completeness: SourceCompleteness | None) -> str | None:
    if completeness is None:
        return None
    return completeness.evaluated_as_of_utc


def _has_execution_trace(evidence_items: tuple[Evidence, ...]) -> bool:
    return any(item.query_executions for item in evidence_items)


def _exact_zero(evidence_items: tuple[Evidence, ...]) -> bool:
    return any(
        item.match_cardinality is not None
        and item.match_cardinality.is_exact is True
        and item.match_cardinality.exact_count == 0
        for item in evidence_items
    )


def _assemble(
    kind: ProvenanceSubjectKind,
    findings: dict[MasterProvenanceRequirement, ProvenanceRequirementFinding],
) -> MasterProvenanceClassification:
    ordered = tuple(findings[requirement] for requirement in MasterProvenanceRequirement)
    return MasterProvenanceClassification(subject_kind=kind, findings=ordered)


def _classify_proposed_event(
    event: ProposedDispatcherExecutionEvent,
    authorization: MasterInvocationAuthorization | None,
) -> MasterProvenanceClassification:
    kind = ProvenanceSubjectKind.PROPOSED_DISPATCHER_EVENT
    identity = _identity_limitations(
        authorization=authorization,
        master_request_id=event.master_request_id,
        master_invocation_id=event.master_invocation_id,
        specialist_invocation_id=event.specialist_invocation_id,
        correlation_id=event.correlation_id,
        descriptive_only=False,
    )
    zero = _zero_limitations(
        event.verified_zero_claim,
        tool_call_id=event.tool_call_id,
        exact_zero=False,
    )
    version_status, version_limits = _lone_version_assessment(event.source_version)
    time_status, time_limits = _time_assessment(
        clocks_present=_proposed_clocks_present(event, authorization),
        contradictory=False,
        freshness=_clocks_freshness(event.freshness_status),
    )
    population = [ProvenanceAuditLimitation.POPULATION_SCOPE_NOT_ESTABLISHED]
    if event.population_scope is ProposedPopulationScope.PARTIAL:
        population.append(ProvenanceAuditLimitation.PARTIAL_POPULATION_SCOPE)
    elif event.population_scope is ProposedPopulationScope.CLAIMED_COMPLETE:
        population.append(ProvenanceAuditLimitation.PUBLIC_FIELDS_NOT_TRUST)
    if event.retrieval_limits or event.limitation_notes:
        population.append(ProvenanceAuditLimitation.RETRIEVAL_LIMITED)
    if event.completeness is not None:
        if _partial_completeness(event.completeness):
            population.append(ProvenanceAuditLimitation.PARTIAL_POPULATION_SCOPE)
        population.append(ProvenanceAuditLimitation.PUBLIC_FIELDS_NOT_TRUST)
    population.extend(zero)

    execution_limits: list[ProvenanceAuditLimitation] = [
        ProvenanceAuditLimitation.NO_AUTHORIZED_EXECUTION_CONTEXT,
        *identity,
    ]
    if event.permit_consumption_ref is not None:
        execution_limits.append(ProvenanceAuditLimitation.PERMIT_REFERENCE_NOT_CONSUMPTION)
    if event.execution_trace_ref is not None:
        execution_limits.append(ProvenanceAuditLimitation.FORGED_EXECUTION_TRACE)
    source_limits: list[ProvenanceAuditLimitation] = [
        ProvenanceAuditLimitation.PUBLIC_FIELDS_NOT_TRUST,
        *identity,
        *zero,
    ]
    if event.execution_trace_ref is not None:
        source_limits.append(ProvenanceAuditLimitation.FORGED_EXECUTION_TRACE)
    if event.source_response_ref is not None:
        source_limits.append(ProvenanceAuditLimitation.PUBLIC_FIELDS_NOT_TRUST)

    adapter_material = event.adapter_identity is not None
    findings = {
        MasterProvenanceRequirement.TRUSTED_SOURCE_ADAPTER: _finding(
            MasterProvenanceRequirement.TRUSTED_SOURCE_ADAPTER,
            _closed_status(adapter_material),
            (
                ProvenanceAuditLimitation.NO_TRUSTED_ADAPTER,
                *((ProvenanceAuditLimitation.PUBLIC_FIELDS_NOT_TRUST,) if adapter_material else ()),
            ),
            kind=kind,
        ),
        MasterProvenanceRequirement.AUTHORIZED_EXECUTION_CONTEXT: _finding(
            MasterProvenanceRequirement.AUTHORIZED_EXECUTION_CONTEXT,
            ProvenanceRequirementStatus.UNSATISFIED,
            execution_limits,
            kind=kind,
        ),
        MasterProvenanceRequirement.SOURCE_READ_PROVENANCE: _finding(
            MasterProvenanceRequirement.SOURCE_READ_PROVENANCE,
            ProvenanceRequirementStatus.UNSATISFIED,
            source_limits,
            kind=kind,
        ),
        MasterProvenanceRequirement.REQUEST_ENTITY_BINDING: _finding(
            MasterProvenanceRequirement.REQUEST_ENTITY_BINDING,
            ProvenanceRequirementStatus.UNSATISFIED,
            (
                ProvenanceAuditLimitation.ENTITY_SCOPE_NOT_BINDING,
                *identity,
                *((ProvenanceAuditLimitation.PUBLIC_FIELDS_NOT_TRUST,) if event.entity_ids else ()),
            ),
            kind=kind,
        ),
        MasterProvenanceRequirement.OBSERVATION_EVALUATION_TIME: _finding(
            MasterProvenanceRequirement.OBSERVATION_EVALUATION_TIME,
            time_status,
            time_limits,
            kind=kind,
        ),
        MasterProvenanceRequirement.SOURCE_VERSION_INTEGRITY: _finding(
            MasterProvenanceRequirement.SOURCE_VERSION_INTEGRITY,
            version_status,
            version_limits,
            kind=kind,
        ),
        MasterProvenanceRequirement.POPULATION_SCOPE_COMPLETENESS: _finding(
            MasterProvenanceRequirement.POPULATION_SCOPE_COMPLETENESS,
            _closed_status(_population_material(population)),
            population,
            kind=kind,
        ),
    }
    return _assemble(kind, findings)


def _clocks_freshness(status: FreshnessStatus | None) -> tuple[FreshnessStatus, ...]:
    if status is None:
        return ()
    return (status,)


def _classify_evidence_group(
    kind: ProvenanceSubjectKind,
    evidence_items: tuple[Evidence, ...],
    *,
    authorization: MasterInvocationAuthorization | None,
    correlation_id: str | None,
    tool_call_id: str | None,
    extra_timestamps: tuple[str, ...],
    tool_status: ToolResultStatus | None,
    verified_zero_claim: VerifiedZeroClaim | None,
) -> MasterProvenanceClassification:
    identity = _identity_limitations(
        authorization=authorization,
        master_request_id=None,
        master_invocation_id=None,
        specialist_invocation_id=None,
        correlation_id=correlation_id,
        descriptive_only=True,
    )
    recorded = _recorded_sources(evidence_items)
    freshness = tuple(evidence.freshness_status for evidence in evidence_items)
    if tool_status is ToolResultStatus.STALE:
        freshness = (*freshness, FreshnessStatus.STALE)
    version_status, version_limits = _version_assessment(recorded)
    time_status, time_limits = _time_assessment(
        clocks_present=_evidence_clocks_present(
            evidence_items,
            extra_timestamps=extra_timestamps,
            authorization=authorization,
        ),
        contradictory=_event_time_conflict(recorded),
        freshness=freshness,
    )
    population: list[ProvenanceAuditLimitation] = []
    for evidence in evidence_items:
        population.extend(_population_from_evidence(evidence))
    if not population:
        population.append(ProvenanceAuditLimitation.POPULATION_SCOPE_NOT_ESTABLISHED)
    if tool_status is ToolResultStatus.PARTIAL:
        population.append(ProvenanceAuditLimitation.RETRIEVAL_LIMITED)
    zero = _zero_limitations(
        verified_zero_claim,
        tool_call_id=tool_call_id,
        exact_zero=_exact_zero(evidence_items),
    )
    population.extend(zero)
    forged = _has_execution_trace(evidence_items)
    execution_material = authorization is not None or forged or correlation_id is not None
    binding_material = authorization is not None or correlation_id is not None
    source_limits: list[ProvenanceAuditLimitation] = [
        ProvenanceAuditLimitation.PUBLIC_FIELDS_NOT_TRUST,
        *identity,
        *zero,
    ]
    if forged:
        source_limits.append(ProvenanceAuditLimitation.FORGED_EXECUTION_TRACE)
    execution_limits: list[ProvenanceAuditLimitation] = [
        ProvenanceAuditLimitation.NO_AUTHORIZED_EXECUTION_CONTEXT,
        *identity,
    ]
    if forged:
        execution_limits.append(ProvenanceAuditLimitation.FORGED_EXECUTION_TRACE)
    findings = {
        MasterProvenanceRequirement.TRUSTED_SOURCE_ADAPTER: _finding(
            MasterProvenanceRequirement.TRUSTED_SOURCE_ADAPTER,
            ProvenanceRequirementStatus.UNRESOLVED,
            (ProvenanceAuditLimitation.NO_TRUSTED_ADAPTER,),
            kind=kind,
        ),
        MasterProvenanceRequirement.AUTHORIZED_EXECUTION_CONTEXT: _finding(
            MasterProvenanceRequirement.AUTHORIZED_EXECUTION_CONTEXT,
            _closed_status(execution_material),
            execution_limits,
            kind=kind,
        ),
        MasterProvenanceRequirement.SOURCE_READ_PROVENANCE: _finding(
            MasterProvenanceRequirement.SOURCE_READ_PROVENANCE,
            ProvenanceRequirementStatus.UNSATISFIED,
            source_limits,
            kind=kind,
        ),
        MasterProvenanceRequirement.REQUEST_ENTITY_BINDING: _finding(
            MasterProvenanceRequirement.REQUEST_ENTITY_BINDING,
            _closed_status(binding_material),
            (ProvenanceAuditLimitation.ENTITY_SCOPE_NOT_BINDING, *identity),
            kind=kind,
        ),
        MasterProvenanceRequirement.OBSERVATION_EVALUATION_TIME: _finding(
            MasterProvenanceRequirement.OBSERVATION_EVALUATION_TIME,
            time_status,
            time_limits,
            kind=kind,
        ),
        MasterProvenanceRequirement.SOURCE_VERSION_INTEGRITY: _finding(
            MasterProvenanceRequirement.SOURCE_VERSION_INTEGRITY,
            version_status,
            version_limits,
            kind=kind,
        ),
        MasterProvenanceRequirement.POPULATION_SCOPE_COMPLETENESS: _finding(
            MasterProvenanceRequirement.POPULATION_SCOPE_COMPLETENESS,
            _closed_status(_population_material(population)),
            population,
            kind=kind,
        ),
    }
    return _assemble(kind, findings)


def classify_master_provenance(
    subject: ToolResult | Evidence | ProposedDispatcherExecutionEvent,
    *,
    authorization: MasterInvocationAuthorization | None = None,
    verified_zero_claim: VerifiedZeroClaim | None = None,
) -> MasterProvenanceClassification:
    """Classify one caller-supplied subject. No requirement is satisfied.

    ``authorization`` is an existing Master planning record. Matching its
    identifiers does not establish a read and does not grant execution.
    ``verified_zero_claim`` is caller-supplied specialist metadata. It does
    not authenticate an empty read. ``ToolResult.data`` is not inspected:
    a payload that claims authority, a source version, or a verified zero
    is not proof.
    """

    if verified_zero_claim is not None and not isinstance(verified_zero_claim, VerifiedZeroClaim):
        raise ContractValidationError("invalid_verified_zero_claim")
    bound = _authorization_or_none(authorization)
    if isinstance(subject, ProposedDispatcherExecutionEvent):
        if verified_zero_claim is not None:
            raise ContractValidationError("invalid_verified_zero_claim")
        return _classify_proposed_event(subject, bound)
    if isinstance(subject, ToolResult):
        return _classify_evidence_group(
            ProvenanceSubjectKind.DESCRIPTIVE_TOOL_RESULT,
            subject.evidence,
            authorization=bound,
            correlation_id=subject.correlation_id,
            tool_call_id=subject.tool_call_id,
            extra_timestamps=_clocks(subject.as_of_utc),
            tool_status=subject.status,
            verified_zero_claim=verified_zero_claim,
        )
    if isinstance(subject, Evidence):
        return _classify_evidence_group(
            ProvenanceSubjectKind.DESCRIPTIVE_EVIDENCE,
            (subject,),
            authorization=bound,
            correlation_id=None,
            tool_call_id=subject.tool_call_id,
            extra_timestamps=(),
            tool_status=None,
            verified_zero_claim=verified_zero_claim,
        )
    raise ContractValidationError("unsupported_provenance_subject")
