"""Server-owned source-read receipt and MA4 provenance boundary.

A public ``ToolResult``, ``Evidence`` object, or proposed dispatcher event
is descriptive metadata. It is not a receipt. Production source authenticity
is not established in this wave: no constructor, Boolean, or caller-supplied
trace can authenticate a read, mint a verified zero, or prove a SIGMET
polygon impact.

What this module can record is narrower. ``LocalTrustedReadHost`` is an
in-process test seam. It registers one local adapter callable, approves one
executor action, and seals a receipt to that action with a non-serializable
token. The seal is not a grant id. Serializing the receipt, rebuilding it
from a dict, or presenting a Lambda event, Authorization header,
``requestContext.authorizer.jwt`` map, or environment mapping does not
create a seal. API Gateway JWT validation is an edge control and is not
checked here. This module does not decide who can invoke a Lambda.

Five population facts stay distinct, and none of them is a verified zero:

- the query returned zero rows
- the source omitted a record
- the source is stale
- pagination is incomplete
- a scoped authoritative empty population was reported with complete coverage

A verified zero would require every production condition in
``VERIFIED_ZERO_ALWAYS_MISSING`` plus those descriptive conditions. The
production conditions are unresolved here, so the missing set is never empty.

Clocks stay distinct. The request clock is the descriptive accepted time.
The observation clock is read from the host clock after the local adapter
returns. The evaluation clock and the query clock are copied only when the
adapter reports them and they are not the request clock. A missing native
field stays missing. Historical run results still omit host correlation,
``source_version``, and ``query_id``; this module does not invent them.

Cross-module interfaces this branch does not import:

- Production grant consumption: a future host-issued seal bound to the
  request, step, invocation, and tool call. A grant-id string is not that
  seal.
- Production ledger consumption: a future append-only host seal for the
  same action. A ledger-id string is not that seal.
- Production adapter registry: an executor owned by the application host
  and AWS isolation. ``register_local_adapter`` is not that registry.
- Historical native identity: ``HistoricalSpecialistRunResult`` must grow
  the fields in ``HISTORICAL_NATIVE_FIELDS_NOT_ON_RUN_RESULT`` in a later
  migration. Until then those values are not backfilled.

``MasterSourceAuthenticity`` stays ``NOT_ESTABLISHED``. Admission constants
and the source-read trace boundary are not modified.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, fields
from datetime import timedelta
from enum import Enum
from types import SimpleNamespace

from wilvor_ai.contracts import (
    PROVENANCE_TOKEN_MAX_LENGTH,
    AgentCapability,
    ContractValidationError,
    Evidence,
    FreshnessStatus,
    ToolResult,
    ToolResultStatus,
    _validate_bounded_text,
    _validate_utc,
)
from wilvor_ai.master_contracts import (
    MasterProductionAuthority,
    MasterPurpose,
    MasterSpecialist,
)
from wilvor_ai.master_evidence_admission import MasterSourceAuthenticity
from wilvor_ai.master_parallel_contracts import MasterProvenanceRequirement
from wilvor_ai.master_provenance_trace import (
    AUTHENTICATED_SOURCE_READ_AVAILABLE,
    ProposedDispatcherExecutionEvent,
    ProvenanceAuditLimitation,
    ProvenanceRequirementFinding,
    ProvenanceRequirementStatus,
)
from wilvor_ai.master_read_resource_guard import (
    DISPATCH_GRANT_AVAILABLE,
    PRODUCTION_PERMIT_ISSUER_INTEGRATED,
    MasterReadDenialReason,
    MasterReadResourceProposal,
    ReadOperation,
    ServerCancellation,
    ServerReadEvaluation,
    check_master_read_resource,
    server_read_argument_names,
    server_read_budget,
    server_read_resources,
    server_read_tools,
)
from wilvor_ai.master_routing import (
    MAX_DECISION_STEPS,
    MAX_HISTORICAL_WINDOW,
    MAX_ROUTE_STEPS,
    MasterRequestCoverage,
    MasterRouteExecutionAuthority,
)


MASTER_AUTHENTICATED_READ_RECEIPT_SCHEMA_VERSION = (
    "wilvor.ai.master_authenticated_read_receipt.v1"
)

SOURCE_AUTHENTICATED_READ_AVAILABLE = False
PRODUCTION_ORIGIN_RESOLVED = False
LOCAL_TRUSTED_ADAPTER_IS_PRODUCTION_AUTHENTICITY = False
TOOL_RESULT_IS_READ_RECEIPT = False
CALLER_SUPPLIED_TRACE_ADMITTED = False
EMPTY_ROWS_CREATE_VERIFIED_ZERO = False
REGIONAL_TEXT_ESTABLISHES_POLYGON_INTERSECTION = False
REGIONAL_TEXT_ESTABLISHES_IMPACTED_AIRCRAFT_COUNT = False
PRODUCTION_GRANT_CONSUMPTION_AVAILABLE = False
PRODUCTION_LEDGER_CONSUMPTION_AVAILABLE = False
LIVE_READS_ENABLED = False
AWS_CONNECTION_IMPLEMENTED = False
PRODUCTION_ADAPTER_REGISTRY_INTEGRATED = False

VERIFIED_ZERO_ALWAYS_MISSING = (
    "production_origin_resolved",
    "source_authenticated",
    "production_grant_consumed",
    "production_ledger_consumed",
    "registered_production_adapter",
    "approved_production_executor_action",
)

HISTORICAL_NATIVE_FIELDS_NOT_ON_RUN_RESULT = (
    "master_request_id",
    "invocation_id",
    "specialist_request_id",
    "correlation_id",
    "application_request_time_utc",
    "requested_evaluation_time_utc",
    "source_version",
    "query_id",
)

HISTORICAL_NATIVE_MIGRATION_NOTES = (
    "HistoricalSpecialistRunResult does not carry host correlation fields.",
    "source_version is not a run field. Do not fabricate one when no SourceRecord is present.",
    "query_id is not a run field. Do not fabricate one when no QueryExecutionTrace is present.",
    "requested_evaluation_time_utc is not copied into an evaluation clock.",
    "application request time is not copied into the observation clock.",
    "ToolResult.correlation_id is not a result-level correlation contract.",
    "A regional name or state grouping is not polygon intersection and is not an impacted-aircraft count.",
)

MA4_CROSS_MODULE_INTERFACES = (
    "Production grant consumption requires a host-issued non-serializable seal bound to master_request_id, step_id, invocation_id, and tool_call_id. A grant id string does not qualify. That module is not imported.",
    "Production ledger consumption requires a host-issued append-only seal for the same action. A ledger id string does not qualify. That module is not imported.",
    "A production adapter registry must resolve an executor owned by the application host and AWS isolation. register_local_adapter is a local test seam, not that registry.",
    "Historical native results must grow HISTORICAL_NATIVE_FIELDS_NOT_ON_RUN_RESULT before those values can be read from the run. This module does not add them.",
    "MasterSourceAuthenticity remains NOT_ESTABLISHED. No admission constant is changed.",
    "The source-read trace boundary remains an empty trusted-runtime record. This module does not write into it.",
    "Who can invoke the Lambda is not established. A forged gateway-looking event is not host origin.",
)

_REGION_TEXT_KEYS = frozenset(
    {"region", "region_text", "region_name", "state", "state_code", "state_grouping"}
)
_IMPACT_KEYS = frozenset(
    {"impacted_aircraft_count", "impacted_aircraft", "aircraft_count"}
)
_POLYGON_KEYS = frozenset({"polygon_intersection", "intersects_polygon", "polygon"})
_PRINCIPAL_ONLY_DENIALS = frozenset({MasterReadDenialReason.PRINCIPAL_NOT_TRUSTED})
_PARTIAL_COMPLETENESS = frozenset({"PARTIAL", "INCOMPLETE", "LIMITED", "TRUNCATED"})

_LIVE_HOSTS: set[int] = set()
_ISSUED: dict[int, tuple[object, ...]] = {}
_HOST_LOCK = threading.Lock()


def _assert_baseline_ceilings() -> None:
    if MAX_ROUTE_STEPS != 4 or MAX_DECISION_STEPS != 1:
        raise ContractValidationError("route_ceiling_changed")
    if MAX_HISTORICAL_WINDOW != timedelta(days=7):
        raise ContractValidationError("historical_window_ceiling_changed")
    if PRODUCTION_PERMIT_ISSUER_INTEGRATED is not False or DISPATCH_GRANT_AVAILABLE is not False:
        raise ContractValidationError("production_permit_issuer_not_integrated")
    if AUTHENTICATED_SOURCE_READ_AVAILABLE is not False:
        raise ContractValidationError("source_authenticity_not_established")
    if SOURCE_AUTHENTICATED_READ_AVAILABLE is not False or PRODUCTION_ORIGIN_RESOLVED is not False:
        raise ContractValidationError("source_authenticity_not_established")


_assert_baseline_ceilings()


class _HostToken:
    """Process-local host identity. Event bodies cannot mint one."""

    __slots__ = ()

    def __reduce__(self) -> object:
        raise ContractValidationError("receipt_not_serializable")


class _ReceiptSeal:
    """Non-serializable ownership of one issued receipt."""

    __slots__ = ("host_token", "fingerprint", "spent")

    def __init__(self, host_token: _HostToken) -> None:
        if type(host_token) is not _HostToken or id(host_token) not in _LIVE_HOSTS:
            raise ContractValidationError("receipt_not_host_issued")
        self.host_token = host_token
        self.fingerprint: tuple[object, ...] | None = None
        self.spent = False

    def __reduce__(self) -> object:
        raise ContractValidationError("receipt_not_serializable")


class _RegistrationSeal:
    __slots__ = ("host_token",)

    def __init__(self, host_token: _HostToken) -> None:
        if type(host_token) is not _HostToken or id(host_token) not in _LIVE_HOSTS:
            raise ContractValidationError("adapter_not_registered")
        self.host_token = host_token

    def __reduce__(self) -> object:
        raise ContractValidationError("receipt_not_serializable")


class _ActionSeal:
    __slots__ = ("host_token", "registration_seal", "spent")

    def __init__(self, host_token: _HostToken, registration_seal: _RegistrationSeal) -> None:
        if type(host_token) is not _HostToken or host_token is not registration_seal.host_token:
            raise ContractValidationError("executor_action_not_approved")
        self.host_token = host_token
        self.registration_seal = registration_seal
        self.spent = False

    def __reduce__(self) -> object:
        raise ContractValidationError("receipt_not_serializable")


class PopulationCondition(str, Enum):
    """Host reading of one local observation. Not a verified zero."""

    QUERY_RETURNED_ZERO_ROWS = "QUERY_RETURNED_ZERO_ROWS"
    SOURCE_OMITTED_RECORD = "SOURCE_OMITTED_RECORD"
    SOURCE_STALE = "SOURCE_STALE"
    PAGINATION_INCOMPLETE = "PAGINATION_INCOMPLETE"
    SCOPED_AUTHORITATIVE_EMPTY_POPULATION = "SCOPED_AUTHORITATIVE_EMPTY_POPULATION"
    ROWS_PRESENT = "ROWS_PRESENT"
    PARTIAL_RESULT = "PARTIAL_RESULT"
    MISSING_QUERY_ID = "MISSING_QUERY_ID"
    SOURCE_VERSION_ABSENT = "SOURCE_VERSION_ABSENT"
    SOURCE_VERSION_MISMATCH = "SOURCE_VERSION_MISMATCH"
    NOT_FOUND_NOT_VERIFIED_ZERO = "NOT_FOUND_NOT_VERIFIED_ZERO"
    TIMEOUT = "TIMEOUT"
    CANCELLED = "CANCELLED"


class UntrustedPopulationIndication(str, Enum):
    """Caller-supplied population hint. Not a host observation."""

    CALLER_REPORTED_ZERO_ROWS = "CALLER_REPORTED_ZERO_ROWS"
    CALLER_REPORTED_OMITTED_RECORDS = "CALLER_REPORTED_OMITTED_RECORDS"
    CALLER_REPORTED_STALE = "CALLER_REPORTED_STALE"
    CALLER_REPORTED_PARTIAL = "CALLER_REPORTED_PARTIAL"
    CALLER_REPORTED_EXACT_ZERO = "CALLER_REPORTED_EXACT_ZERO"
    CALLER_REPORTED_NOT_FOUND = "CALLER_REPORTED_NOT_FOUND"
    FORGED_REGIONAL_IMPACT = "FORGED_REGIONAL_IMPACT"


class GeographicLimitation(str, Enum):
    """Why a regional string is not geospatial proof."""

    REGIONAL_TEXT_NOT_POLYGON = "REGIONAL_TEXT_NOT_POLYGON"
    STATE_GROUPING_NOT_POLYGON = "STATE_GROUPING_NOT_POLYGON"
    IMPACT_COUNT_NOT_ESTABLISHED = "IMPACT_COUNT_NOT_ESTABLISHED"
    POLYGON_INTERSECTION_NOT_ESTABLISHED = "POLYGON_INTERSECTION_NOT_ESTABLISHED"


class ReadResponseDisposition(str, Enum):
    """How the host classified the attempt. Not an evidence admission."""

    COMPLETED = "COMPLETED"
    ZERO_ROWS = "ZERO_ROWS"
    PARTIAL = "PARTIAL"
    STALE = "STALE"
    NOT_FOUND = "NOT_FOUND"
    TIMEOUT = "TIMEOUT"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"


class ReadOrigin(str, Enum):
    """Receipt origin. Production-authenticated is not a member."""

    UNRESOLVED = "UNRESOLVED"
    LOCAL_TRUSTED_ADAPTER_OBSERVATION = "LOCAL_TRUSTED_ADAPTER_OBSERVATION"


class ProductionOrigin(str, Enum):
    """Production origin. Authenticated is not a member."""

    UNRESOLVED = "UNRESOLVED"


class ReadReceiptVerificationStatus(str, Enum):
    LOCAL_OBSERVATION_RECORDED = "LOCAL_OBSERVATION_RECORDED"
    LOCAL_FAILURE_RECORDED = "LOCAL_FAILURE_RECORDED"
    NOT_A_RECEIPT = "NOT_A_RECEIPT"
    UNTRUSTED_SUBJECT = "UNTRUSTED_SUBJECT"
    REPLAYED = "REPLAYED"
    TAMPERED = "TAMPERED"
    BINDING_MISMATCH = "BINDING_MISMATCH"
    DUPLICATE = "DUPLICATE"
    REJECTED = "REJECTED"


class ReadReceiptGap(str, Enum):
    SOURCE_AUTHENTICITY_NOT_ESTABLISHED = "SOURCE_AUTHENTICITY_NOT_ESTABLISHED"
    PRODUCTION_ORIGIN_UNRESOLVED = "PRODUCTION_ORIGIN_UNRESOLVED"
    LOCAL_OBSERVATION_NOT_PRODUCTION_AUTHENTICITY = "LOCAL_OBSERVATION_NOT_PRODUCTION_AUTHENTICITY"
    PRODUCTION_GRANT_NOT_CONSUMED = "PRODUCTION_GRANT_NOT_CONSUMED"
    PRODUCTION_LEDGER_NOT_CONSUMED = "PRODUCTION_LEDGER_NOT_CONSUMED"
    NOT_AVIATION_EVIDENCE = "NOT_AVIATION_EVIDENCE"
    PRINCIPAL_REF_NOT_AUTHENTICATION = "PRINCIPAL_REF_NOT_AUTHENTICATION"
    POLICY_CHECK_NOT_GRANT = "POLICY_CHECK_NOT_GRANT"
    TIMESTAMPS_NOT_ORIGIN_PROOF = "TIMESTAMPS_NOT_ORIGIN_PROOF"
    CALLER_SUPPLIED_METADATA_NOT_PROOF = "CALLER_SUPPLIED_METADATA_NOT_PROOF"
    TOOL_RESULT_NOT_RECEIPT = "TOOL_RESULT_NOT_RECEIPT"
    SOURCE_VERSION_NOT_INTEGRITY_PROOF = "SOURCE_VERSION_NOT_INTEGRITY_PROOF"
    MISSING_SOURCE_VERSION = "MISSING_SOURCE_VERSION"
    SOURCE_VERSION_MISMATCH = "SOURCE_VERSION_MISMATCH"
    MISSING_QUERY_ID = "MISSING_QUERY_ID"
    QUERY_ID_NOT_AUTHENTICATED = "QUERY_ID_NOT_AUTHENTICATED"
    PAGINATION_INCOMPLETE = "PAGINATION_INCOMPLETE"
    QUERY_RETURNED_ZERO_ROWS = "QUERY_RETURNED_ZERO_ROWS"
    SOURCE_OMITTED_RECORD = "SOURCE_OMITTED_RECORD"
    SOURCE_STALE = "SOURCE_STALE"
    PARTIAL_RESULT = "PARTIAL_RESULT"
    TIMEOUT = "TIMEOUT"
    CANCELLED = "CANCELLED"
    WRONG_CLOCK = "WRONG_CLOCK"
    FORGED_REGIONAL_IMPACT = "FORGED_REGIONAL_IMPACT"
    SCOPED_EMPTY_NOT_VERIFIED_ZERO = "SCOPED_EMPTY_NOT_VERIFIED_ZERO"
    REQUESTED_QUERY_NOT_EXECUTED = "REQUESTED_QUERY_NOT_EXECUTED"
    HISTORICAL_NATIVE_FIELD_ABSENT = "HISTORICAL_NATIVE_FIELD_ABSENT"
    CALLER_CEILING_IGNORED = "CALLER_CEILING_IGNORED"
    STRUCTURAL_ALLOWLIST_DENIAL = "STRUCTURAL_ALLOWLIST_DENIAL"
    CONTRADICTORY_ROW_COUNT = "CONTRADICTORY_ROW_COUNT"
    NOT_FOUND_NOT_VERIFIED_ZERO = "NOT_FOUND_NOT_VERIFIED_ZERO"


class Ma4IntegrationRequirement(str, Enum):
    """Later-wave producers. Satisfied is not a status this module can set."""

    APPROVED_PRODUCTION_EXECUTOR_ACTION = "APPROVED_PRODUCTION_EXECUTOR_ACTION"
    PRODUCTION_GRANT_CONSUMPTION = "PRODUCTION_GRANT_CONSUMPTION"
    PRODUCTION_LEDGER_CONSUMPTION = "PRODUCTION_LEDGER_CONSUMPTION"
    REGISTERED_PRODUCTION_ADAPTER = "REGISTERED_PRODUCTION_ADAPTER"
    HOST_AWS_ISOLATION = "HOST_AWS_ISOLATION"
    NATIVE_SOURCE_VERSION = "NATIVE_SOURCE_VERSION"
    NATIVE_QUERY_IDENTITY = "NATIVE_QUERY_IDENTITY"
    DISTINCT_RUNTIME_CLOCKS = "DISTINCT_RUNTIME_CLOCKS"
    COMPLETE_AUTHORIZED_POPULATION = "COMPLETE_AUTHORIZED_POPULATION"
    POLYGON_INTERSECTION_PROOF = "POLYGON_INTERSECTION_PROOF"
    HISTORICAL_NATIVE_IDENTITY_FIELDS = "HISTORICAL_NATIVE_IDENTITY_FIELDS"


_MA4_PRODUCERS = {
    Ma4IntegrationRequirement.APPROVED_PRODUCTION_EXECUTOR_ACTION: (
        "Application host must approve an executor action inside AWS isolation. "
        "A local test action seal is not that approval."
    ),
    Ma4IntegrationRequirement.PRODUCTION_GRANT_CONSUMPTION: (
        "Future grant module must return a non-serializable consumption seal "
        "bound to the request, step, invocation, and tool call. Not imported."
    ),
    Ma4IntegrationRequirement.PRODUCTION_LEDGER_CONSUMPTION: (
        "Future ledger module must append a non-serializable consumption seal "
        "for the same action. Not imported."
    ),
    Ma4IntegrationRequirement.REGISTERED_PRODUCTION_ADAPTER: (
        "Future adapter registry must bind the executor to the reviewed host. "
        "register_local_adapter does not."
    ),
    Ma4IntegrationRequirement.HOST_AWS_ISOLATION: (
        "Lambda invoke permission and AWS isolation are not verified. "
        "A gateway-looking event is not attestation."
    ),
    Ma4IntegrationRequirement.NATIVE_SOURCE_VERSION: (
        "source_version must arrive on the native result. A missing version stays missing."
    ),
    Ma4IntegrationRequirement.NATIVE_QUERY_IDENTITY: (
        "query_id must arrive on the native query trace. A missing query id stays missing."
    ),
    Ma4IntegrationRequirement.DISTINCT_RUNTIME_CLOCKS: (
        "Observation and evaluation clocks must come from the source runtime, "
        "separate from the application request clock."
    ),
    Ma4IntegrationRequirement.COMPLETE_AUTHORIZED_POPULATION: (
        "Complete authorized population coverage is a future proof, not a local flag."
    ),
    Ma4IntegrationRequirement.POLYGON_INTERSECTION_PROOF: (
        "Polygon intersection requires geospatial proof. California text and state codes do not."
    ),
    Ma4IntegrationRequirement.HISTORICAL_NATIVE_IDENTITY_FIELDS: (
        "HistoricalSpecialistRunResult must carry HISTORICAL_NATIVE_FIELDS_NOT_ON_RUN_RESULT."
    ),
}

_ALWAYS_GAPS = (
    ReadReceiptGap.SOURCE_AUTHENTICITY_NOT_ESTABLISHED,
    ReadReceiptGap.PRODUCTION_ORIGIN_UNRESOLVED,
    ReadReceiptGap.LOCAL_OBSERVATION_NOT_PRODUCTION_AUTHENTICITY,
    ReadReceiptGap.PRODUCTION_GRANT_NOT_CONSUMED,
    ReadReceiptGap.PRODUCTION_LEDGER_NOT_CONSUMED,
    ReadReceiptGap.NOT_AVIATION_EVIDENCE,
    ReadReceiptGap.PRINCIPAL_REF_NOT_AUTHENTICATION,
    ReadReceiptGap.POLICY_CHECK_NOT_GRANT,
    ReadReceiptGap.TIMESTAMPS_NOT_ORIGIN_PROOF,
)

_PROVENANCE_LIMITATIONS = (
    ProvenanceAuditLimitation.AUTHENTICATED_SOURCE_READ_NOT_AVAILABLE,
    ProvenanceAuditLimitation.NO_TRUSTED_ADAPTER,
)


class LocalReadTimeout(Exception):
    """Timeout raised by a local adapter. Not an AWS timeout."""


class LocalReadCancellation(Exception):
    """Cancellation raised by a local adapter."""


class ReadReceiptError(Exception):
    """A host outcome that must not be read as admitted evidence."""

    def __init__(self, verification: "ReadReceiptVerification") -> None:
        self.verification = verification
        super().__init__(verification.status.value)


class ReadReceiptRejected(ReadReceiptError):
    """The allowlist or binding check refused the read. The adapter was not called."""


class ReadReceiptTimeout(ReadReceiptError):
    """The local adapter timed out. The result is not evidence."""


class ReadReceiptCancelled(ReadReceiptError):
    """The read was cancelled. The result is not evidence."""


class DuplicateReadReceipt(ReadReceiptError):
    """This tool call or idempotency key was already used on this host."""


class ReadCancellation:
    """Cooperative cancel flag. Setting it does not touch a remote system."""

    def __init__(self) -> None:
        self._flag = False
        self._lock = threading.Lock()

    def cancel(self) -> None:
        with self._lock:
            self._flag = True

    def is_cancelled(self) -> bool:
        with self._lock:
            return self._flag


def _token(value: object, field_name: str, *, optional: bool = False) -> str | None:
    errors = _validate_bounded_text(
        value,
        field_name,
        optional=optional,
        max_length=PROVENANCE_TOKEN_MAX_LENGTH,
    )
    if errors:
        raise ContractValidationError(errors)
    if value is None:
        return None
    if not isinstance(value, str) or value != value.strip() or any(character.isspace() for character in value):
        raise ContractValidationError(f"invalid_{field_name}")
    return value


def _label(value: object, field_name: str, *, optional: bool = False) -> str | None:
    errors = _validate_bounded_text(
        value,
        field_name,
        optional=optional,
        max_length=PROVENANCE_TOKEN_MAX_LENGTH,
    )
    if errors:
        raise ContractValidationError(errors)
    if value is None:
        return None
    if not isinstance(value, str) or not value or value != value.strip():
        raise ContractValidationError(f"invalid_{field_name}")
    return value


def _whole_second(value: object, field_name: str, *, optional: bool = False) -> str | None:
    if value is None and optional:
        return None
    if not isinstance(value, str) or len(value) != 20 or not value.endswith("Z") or value[10] != "T":
        raise ContractValidationError(f"invalid_{field_name}")
    errors = _validate_utc(value, field_name)
    if errors:
        raise ContractValidationError(errors)
    return value


def _count(value: object, field_name: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ContractValidationError(f"invalid_{field_name}")
    return value


def _query(value: object, field_name: str) -> tuple[tuple[str, str | int], ...]:
    if not isinstance(value, tuple):
        raise ContractValidationError(f"invalid_{field_name}")
    names: list[str] = []
    checked: list[tuple[str, str | int]] = []
    for item in value:
        if not isinstance(item, tuple) or len(item) != 2:
            raise ContractValidationError(f"invalid_{field_name}")
        name, argument = item
        _token(name, field_name)
        if isinstance(argument, bool) or not isinstance(argument, (str, int)):
            raise ContractValidationError(f"invalid_{field_name}")
        if isinstance(argument, str):
            _label(argument, field_name)
        if name in names:
            raise ContractValidationError("duplicate_tool_argument")
        names.append(name)
        checked.append((name, argument))
    return tuple(checked)


def _enum_tuple(value: object, enum_type: type[Enum], field_name: str) -> tuple[Enum, ...]:
    if not isinstance(value, tuple) or any(not isinstance(item, enum_type) for item in value):
        raise ContractValidationError(f"invalid_{field_name}")
    return tuple(value)


def _ordered(enum_type: type[Enum], selected: set[Enum]) -> tuple[Enum, ...]:
    return tuple(item for item in enum_type if item in selected)


def _gaps(*extra: ReadReceiptGap) -> tuple[ReadReceiptGap, ...]:
    selected = set(_ALWAYS_GAPS)
    selected.update(extra)
    return tuple(item for item in ReadReceiptGap if item in selected)


def _clock_reader(clock: object) -> str:
    reader = getattr(clock, "now_utc", None)
    if not callable(reader):
        raise ContractValidationError("invalid_host_clock")
    instant = reader()
    checked = _whole_second(instant, "host_clock")
    if checked is None:
        raise ContractValidationError("invalid_host_clock")
    return checked


def _window_bounds(query: tuple[tuple[str, str | int], ...]) -> tuple[str | None, str | None]:
    arguments = dict(query)
    start = arguments.get("start_utc")
    end = arguments.get("end_utc")
    if start is not None and not isinstance(start, str):
        start = None
    if end is not None and not isinstance(end, str):
        end = None
    return start, end


def _state_like(values: tuple[str, ...]) -> bool:
    for value in values:
        folded = value.casefold()
        if "california" in folded:
            return True
        if len(value) == 2 and value.isalpha():
            return True
    return False


def _pagination_incomplete(observation: "LocalAdapterObservation") -> bool:
    if observation.truncated or observation.has_more:
        return True
    if (
        observation.page_index is not None
        and observation.page_count is not None
        and observation.page_index + 1 < observation.page_count
    ):
        return True
    return False


def _freeze(value: object) -> object:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, tuple):
        return tuple(_freeze(item) for item in value)
    return value


def _material(source: object) -> tuple[object, ...]:
    present = {item.name for item in fields(source)} if hasattr(source, "__dataclass_fields__") else None
    values: list[object] = []
    for name in _FINGERPRINT_FIELDS:
        if present is not None and name not in present and name != "_seal":
            raise ContractValidationError("receipt_fingerprint_incomplete")
        values.append(_freeze(getattr(source, name)))
    return tuple(values)


def _provenance_findings() -> tuple[ProvenanceRequirementFinding, ...]:
    return tuple(
        ProvenanceRequirementFinding(
            requirement=requirement,
            status=ProvenanceRequirementStatus.UNRESOLVED,
            limitations=_PROVENANCE_LIMITATIONS,
        )
        for requirement in MasterProvenanceRequirement
    )


def _missing_verified_zero(
    *,
    conditions: set[PopulationCondition],
    evaluation_clock: str | None,
    observation_clock: str | None,
    query_clock: str | None,
    geographic: tuple[GeographicLimitation, ...],
    host_observed: bool,
    receipt_issued: bool,
) -> tuple[str, ...]:
    missing = list(VERIFIED_ZERO_ALWAYS_MISSING)
    if not receipt_issued:
        missing.append("host_issued_receipt")
    if PopulationCondition.QUERY_RETURNED_ZERO_ROWS not in conditions:
        missing.append("query_returned_zero_rows")
    if PopulationCondition.SOURCE_OMITTED_RECORD in conditions or not host_observed:
        missing.append("source_did_not_merely_omit_record")
    if PopulationCondition.SOURCE_STALE in conditions or not host_observed:
        missing.append("source_not_stale")
    if PopulationCondition.PAGINATION_INCOMPLETE in conditions or not host_observed:
        missing.append("pagination_complete")
    if PopulationCondition.PARTIAL_RESULT in conditions or not host_observed:
        missing.append("not_partial")
    if PopulationCondition.TIMEOUT in conditions:
        missing.append("not_timeout")
    if PopulationCondition.CANCELLED in conditions:
        missing.append("not_cancelled")
    if PopulationCondition.MISSING_QUERY_ID in conditions or not host_observed:
        missing.append("query_id_present")
    if (
        PopulationCondition.SOURCE_VERSION_ABSENT in conditions
        or PopulationCondition.SOURCE_VERSION_MISMATCH in conditions
        or not host_observed
    ):
        missing.append("source_version_present_and_matching")
    if PopulationCondition.SCOPED_AUTHORITATIVE_EMPTY_POPULATION not in conditions:
        missing.append("scoped_authoritative_population")
    if evaluation_clock is None:
        missing.append("evaluation_clock_distinct")
    if observation_clock is None:
        missing.append("observation_clock_from_host_runtime")
    if query_clock is None:
        missing.append("query_clock_distinct")
    if geographic:
        missing.append("polygon_intersection_when_geographic")
    return tuple(dict.fromkeys(missing))


@dataclass(frozen=True)
class Ma4IntegrationFinding:
    """One later-wave producer. The status cannot say the requirement is satisfied."""

    requirement: Ma4IntegrationRequirement
    status: ProvenanceRequirementStatus
    producer: str
    satisfied: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.requirement, Ma4IntegrationRequirement):
            raise ContractValidationError("invalid_ma4_requirement")
        if self.status is not ProvenanceRequirementStatus.UNRESOLVED:
            raise ContractValidationError("ma4_requirement_not_unresolved")
        if self.satisfied is not False:
            raise ContractValidationError("ma4_requirement_not_satisfied")
        if not isinstance(self.producer, str) or not self.producer:
            raise ContractValidationError("invalid_ma4_producer")


@dataclass(frozen=True)
class ObservedSourceRow:
    """One row the local adapter reported. Missing identity stays missing."""

    record_id: str | None
    source_version: str | None
    event_timestamp_utc: str | None = None

    def __post_init__(self) -> None:
        _token(self.record_id, "record_id", optional=True)
        _token(self.source_version, "source_version", optional=True)
        _whole_second(self.event_timestamp_utc, "event_timestamp_utc", optional=True)


@dataclass(frozen=True)
class LocalAdapterObservation:
    """What a local adapter callable returned. Not a receipt and not evidence.

    ``observation_clock_utc`` is ignored. The host reads its own clock.
    ``source_version`` and ``query_id`` are recorded only when present.
    """

    executed_query: tuple[tuple[str, str | int], ...]
    response_status: ToolResultStatus
    rows_returned: int | None = None
    rows: tuple[ObservedSourceRow, ...] = ()
    page_index: int | None = None
    page_count: int | None = None
    page_limit: int | None = None
    row_limit: int | None = None
    truncated: bool = False
    has_more: bool = False
    query_clock_utc: str | None = None
    evaluation_clock_utc: str | None = None
    observation_clock_utc: str | None = None
    freshness: FreshnessStatus | None = None
    query_id: str | None = None
    response_source_version: str | None = None
    region_text: str | None = None
    state_grouping: tuple[str, ...] = ()
    claimed_impacted_aircraft_count: int | None = None
    claimed_polygon_intersection: bool = False
    scoped_authoritative_empty: bool = False
    source_omitted_record: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "executed_query", _query(self.executed_query, "executed_query"))
        if not isinstance(self.response_status, ToolResultStatus):
            raise ContractValidationError("invalid_response_status")
        _count(self.rows_returned, "rows_returned")
        _count(self.page_index, "page_index")
        _count(self.page_count, "page_count")
        _count(self.page_limit, "page_limit")
        _count(self.row_limit, "row_limit")
        _count(self.claimed_impacted_aircraft_count, "claimed_impacted_aircraft_count")
        if not isinstance(self.rows, tuple) or any(not isinstance(item, ObservedSourceRow) for item in self.rows):
            raise ContractValidationError("invalid_rows")
        if not isinstance(self.truncated, bool) or not isinstance(self.has_more, bool):
            raise ContractValidationError("invalid_pagination")
        if not isinstance(self.claimed_polygon_intersection, bool) or not isinstance(
            self.scoped_authoritative_empty,
            bool,
        ):
            raise ContractValidationError("invalid_observation")
        if not isinstance(self.source_omitted_record, bool):
            raise ContractValidationError("invalid_observation")
        _whole_second(self.query_clock_utc, "query_clock_utc", optional=True)
        _whole_second(self.evaluation_clock_utc, "evaluation_clock_utc", optional=True)
        _whole_second(self.observation_clock_utc, "observation_clock_utc", optional=True)
        if self.freshness is not None and not isinstance(self.freshness, FreshnessStatus):
            raise ContractValidationError("invalid_freshness_status")
        _token(self.query_id, "query_id", optional=True)
        _token(self.response_source_version, "response_source_version", optional=True)
        _label(self.region_text, "region_text", optional=True)
        if not isinstance(self.state_grouping, tuple):
            raise ContractValidationError("invalid_state_grouping")
        for item in self.state_grouping:
            _label(item, "state_grouping")


@dataclass(frozen=True)
class DescriptiveReadContext:
    """Shared descriptive envelope. Not an authentication token.

    Values are accepted only as arguments to a ``LocalTrustedReadHost``
    constructed in this process. This object has no event parser.
    """

    master_request_id: str
    step_id: str
    invocation_id: str
    specialist_invocation_id: str
    specialist: MasterSpecialist
    purpose: MasterPurpose
    capability: AgentCapability
    principal_ref: str
    resource_id: str
    operation: ReadOperation
    accepted_at_utc: str
    deadline_utc: str
    adapter_identity: str
    tool_name: str
    tool_call_id: str
    requested_query: tuple[tuple[str, str | int], ...]
    planning_session_id: str | None = None
    policy_revision: str | None = None
    region_scope: tuple[str, ...] = ()
    historical_window_start_utc: str | None = None
    historical_window_end_utc: str | None = None
    decision_target_scope: str | None = None
    idempotency_key: str | None = None
    max_model_turns: int | None = None
    max_tool_calls: int | None = None
    correlation_id: str | None = None
    expected_source_version: str | None = None
    asserted_grant_ref: str | None = None
    asserted_ledger_ref: str | None = None

    def __post_init__(self) -> None:
        for name in (
            "master_request_id",
            "step_id",
            "invocation_id",
            "specialist_invocation_id",
            "principal_ref",
            "resource_id",
            "adapter_identity",
            "tool_name",
            "tool_call_id",
        ):
            _token(getattr(self, name), name)
        for name in (
            "planning_session_id",
            "policy_revision",
            "decision_target_scope",
            "idempotency_key",
            "correlation_id",
            "expected_source_version",
            "asserted_grant_ref",
            "asserted_ledger_ref",
        ):
            _token(getattr(self, name), name, optional=True)
        if not isinstance(self.specialist, MasterSpecialist):
            raise ContractValidationError("invalid_specialist")
        if not isinstance(self.purpose, MasterPurpose):
            raise ContractValidationError("invalid_purpose")
        if not isinstance(self.capability, AgentCapability):
            raise ContractValidationError("invalid_capability")
        if not isinstance(self.operation, ReadOperation):
            raise ContractValidationError("invalid_operation")
        _whole_second(self.accepted_at_utc, "accepted_at_utc")
        _whole_second(self.deadline_utc, "deadline_utc")
        _whole_second(self.historical_window_start_utc, "historical_window_start_utc", optional=True)
        _whole_second(self.historical_window_end_utc, "historical_window_end_utc", optional=True)
        object.__setattr__(self, "requested_query", _query(self.requested_query, "requested_query"))
        if not isinstance(self.region_scope, tuple):
            raise ContractValidationError("invalid_region_scope")
        for item in self.region_scope:
            _label(item, "region_scope")
        if self.max_model_turns is not None:
            _count(self.max_model_turns, "max_model_turns")
        if self.max_tool_calls is not None:
            _count(self.max_tool_calls, "max_tool_calls")
        if self.master_request_id == self.invocation_id:
            raise ContractValidationError("request_identity_not_distinct")

    @property
    def resource_scope(self) -> tuple[str, ...]:
        return (self.resource_id,)


@dataclass(frozen=True)
class LocalAdapterRegistration:
    """A callable registered on one local test host. Not a production adapter."""

    adapter_identity: str
    _seal: object = field(repr=False, compare=False)

    def __post_init__(self) -> None:
        _token(self.adapter_identity, "adapter_identity")
        if type(self._seal) is not _RegistrationSeal:
            raise ContractValidationError("adapter_not_registered")

    @property
    def production_registered(self) -> bool:
        return False

    def __bool__(self) -> bool:
        return False


@dataclass(frozen=True)
class ApprovedExecutorAction:
    """One host-approved local action. Not a production grant."""

    master_request_id: str
    step_id: str
    invocation_id: str
    tool_call_id: str
    tool_name: str
    adapter_identity: str
    _seal: object = field(repr=False, compare=False)

    def __post_init__(self) -> None:
        if type(self._seal) is not _ActionSeal:
            raise ContractValidationError("executor_action_not_approved")
        for name in (
            "master_request_id",
            "step_id",
            "invocation_id",
            "tool_call_id",
            "tool_name",
            "adapter_identity",
        ):
            _token(getattr(self, name), name)

    @property
    def production_grant(self) -> bool:
        return False

    def __bool__(self) -> bool:
        return False


@dataclass(frozen=True)
class HostIssuedReadReceipt:
    """One sealed local read record. A ``ToolResult`` cannot be substituted for it."""

    schema_version: str
    receipt_id: str
    master_request_id: str
    planning_session_id: str | None
    step_id: str
    invocation_id: str
    specialist_invocation_id: str
    specialist: MasterSpecialist
    purpose: MasterPurpose
    capability: AgentCapability
    principal_ref: str
    policy_revision: str | None
    resource_id: str
    resource_scope: tuple[str, ...]
    operation: ReadOperation
    region_scope: tuple[str, ...]
    historical_window_start_utc: str | None
    historical_window_end_utc: str | None
    decision_target_scope: str | None
    accepted_at_utc: str
    deadline_utc: str
    idempotency_key: str | None
    correlation_id: str | None
    server_max_executions: int
    server_max_model_turns: int
    server_max_tool_calls: int
    caller_max_model_turns: int | None
    caller_max_tool_calls: int | None
    adapter_identity: str
    tool_name: str
    tool_call_id: str
    requested_query: tuple[tuple[str, str | int], ...]
    executed_query: tuple[tuple[str, str | int], ...]
    query_diverged: bool
    local_consumption_id: str
    asserted_grant_ref: str | None
    asserted_ledger_ref: str | None
    response_status: ToolResultStatus | None
    disposition: ReadResponseDisposition
    page_index: int | None
    page_count: int | None
    page_limit: int | None
    row_limit: int | None
    truncated: bool
    has_more: bool
    rows_returned: int | None
    record_ids: tuple[str, ...]
    source_versions: tuple[str | None, ...]
    response_source_version: str | None
    query_ids: tuple[str, ...]
    adapter_invoked: bool
    request_clock_utc: str
    observation_clock_utc: str | None
    evaluation_clock_utc: str | None
    query_clock_utc: str | None
    population_conditions: tuple[PopulationCondition, ...]
    reported_pagination_complete: bool
    geographic_limitations: tuple[GeographicLimitation, ...]
    untrusted_claimed_impacted_aircraft_count: int | None
    claimed_polygon_intersection: bool
    denial_reasons: tuple[MasterReadDenialReason, ...]
    gaps: tuple[ReadReceiptGap, ...]
    expected_source_version: str | None
    _seal: object = field(repr=False, compare=False)

    def __post_init__(self) -> None:
        if self.schema_version != MASTER_AUTHENTICATED_READ_RECEIPT_SCHEMA_VERSION:
            raise ContractValidationError("invalid_schema_version")
        if type(self._seal) is not _ReceiptSeal:
            raise ContractValidationError("receipt_not_host_issued")
        data_fields = {item.name for item in fields(self) if item.name != "_seal"}
        if data_fields != set(_FINGERPRINT_FIELDS):
            raise ContractValidationError("receipt_fingerprint_incomplete")
        if _material(self) != self._seal.fingerprint or _ISSUED.get(id(self._seal)) != self._seal.fingerprint:
            raise ContractValidationError("receipt_not_host_issued")
        if not isinstance(self.specialist, MasterSpecialist):
            raise ContractValidationError("invalid_specialist")
        if not isinstance(self.purpose, MasterPurpose):
            raise ContractValidationError("invalid_purpose")
        if not isinstance(self.capability, AgentCapability):
            raise ContractValidationError("invalid_capability")
        if not isinstance(self.operation, ReadOperation):
            raise ContractValidationError("invalid_operation")
        if not isinstance(self.disposition, ReadResponseDisposition):
            raise ContractValidationError("invalid_disposition")
        if self.response_status is not None and not isinstance(self.response_status, ToolResultStatus):
            raise ContractValidationError("invalid_response_status")
        _enum_tuple(self.population_conditions, PopulationCondition, "population_conditions")
        _enum_tuple(self.geographic_limitations, GeographicLimitation, "geographic_limitations")
        _enum_tuple(self.denial_reasons, MasterReadDenialReason, "denial_reasons")
        _enum_tuple(self.gaps, ReadReceiptGap, "gaps")
        if any(gap not in self.gaps for gap in _ALWAYS_GAPS):
            raise ContractValidationError("source_authenticity_not_established")

    def __getstate__(self) -> object:
        raise ContractValidationError("receipt_not_serializable")

    def __reduce__(self) -> object:
        raise ContractValidationError("receipt_not_serializable")

    def __bool__(self) -> bool:
        return False

    @property
    def source_authenticity(self) -> MasterSourceAuthenticity:
        return MasterSourceAuthenticity.NOT_ESTABLISHED

    @property
    def production_authority(self) -> MasterProductionAuthority:
        return MasterProductionAuthority.DENIED

    @property
    def execution_authority(self) -> MasterRouteExecutionAuthority:
        return MasterRouteExecutionAuthority.DENIED

    @property
    def request_coverage(self) -> MasterRequestCoverage:
        return MasterRequestCoverage.NOT_ESTABLISHED

    @property
    def dispatch_permitted(self) -> bool:
        return False

    @property
    def end_to_end_fulfilled(self) -> bool:
        return False

    @property
    def evidence_admitted(self) -> bool:
        return False

    @property
    def aviation_evidence_admitted(self) -> bool:
        return False

    @property
    def verified_zero_established(self) -> bool:
        return False

    @property
    def polygon_intersection_established(self) -> bool:
        return False

    @property
    def impacted_aircraft_count_established(self) -> bool:
        return False

    @property
    def production_origin(self) -> ProductionOrigin:
        return ProductionOrigin.UNRESOLVED

    @property
    def production_grant_consumed(self) -> bool:
        return False

    @property
    def production_ledger_consumed(self) -> bool:
        return False

    @property
    def policy_check_is_grant(self) -> bool:
        return False

    @property
    def origin(self) -> ReadOrigin:
        return ReadOrigin.LOCAL_TRUSTED_ADAPTER_OBSERVATION

    @property
    def authenticated_source_read_available(self) -> bool:
        return AUTHENTICATED_SOURCE_READ_AVAILABLE


_FINGERPRINT_FIELDS = tuple(
    item.name for item in fields(HostIssuedReadReceipt) if item.name != "_seal"
)


@dataclass(frozen=True)
class ReadReceiptVerification:
    """Boundary result. Local observation and production authenticity stay separate."""

    status: ReadReceiptVerificationStatus
    origin: ReadOrigin
    host_issued: bool
    adapter_invoked: bool
    local_test_host_observation: bool
    population_conditions: tuple[PopulationCondition, ...]
    untrusted_indications: tuple[UntrustedPopulationIndication, ...]
    geographic_limitations: tuple[GeographicLimitation, ...]
    gaps: tuple[ReadReceiptGap, ...]
    missing_verified_zero_conditions: tuple[str, ...]
    provenance_findings: tuple[ProvenanceRequirementFinding, ...]
    denial_reasons: tuple[MasterReadDenialReason, ...]
    receipt: HostIssuedReadReceipt | None = None
    request_clock_utc: str | None = None
    observation_clock_utc: str | None = None
    evaluation_clock_utc: str | None = None
    query_clock_utc: str | None = None
    master_request_id: str | None = None
    step_id: str | None = None
    tool_call_id: str | None = None
    query_diverged: bool = False
    asserted_grant_ref: str | None = None
    asserted_ledger_ref: str | None = None
    local_consumption_id: str | None = None
    untrusted_claimed_impacted_aircraft_count: int | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.status, ReadReceiptVerificationStatus):
            raise ContractValidationError("invalid_read_receipt_status")
        if not isinstance(self.origin, ReadOrigin):
            raise ContractValidationError("invalid_read_origin")
        if self.receipt is not None and type(self.receipt) is not HostIssuedReadReceipt:
            raise ContractValidationError("receipt_not_host_issued")
        _enum_tuple(self.population_conditions, PopulationCondition, "population_conditions")
        _enum_tuple(self.untrusted_indications, UntrustedPopulationIndication, "untrusted_indications")
        _enum_tuple(self.geographic_limitations, GeographicLimitation, "geographic_limitations")
        _enum_tuple(self.gaps, ReadReceiptGap, "gaps")
        _enum_tuple(self.denial_reasons, MasterReadDenialReason, "denial_reasons")
        if tuple(item.requirement for item in self.provenance_findings) != tuple(MasterProvenanceRequirement):
            raise ContractValidationError("invalid_provenance_findings")
        if any(item.status is not ProvenanceRequirementStatus.UNRESOLVED for item in self.provenance_findings):
            raise ContractValidationError("provenance_requirement_not_satisfied")
        if any(name not in self.missing_verified_zero_conditions for name in VERIFIED_ZERO_ALWAYS_MISSING):
            raise ContractValidationError("verified_zero_not_established")
        local_status = {
            ReadReceiptVerificationStatus.LOCAL_OBSERVATION_RECORDED,
            ReadReceiptVerificationStatus.LOCAL_FAILURE_RECORDED,
        }
        if self.status in local_status:
            if self.origin is not ReadOrigin.LOCAL_TRUSTED_ADAPTER_OBSERVATION or self.receipt is None:
                raise ContractValidationError("invalid_read_receipt_status")
            if self.host_issued is not True:
                raise ContractValidationError("invalid_read_receipt_status")
        elif self.status in {
            ReadReceiptVerificationStatus.REPLAYED,
            ReadReceiptVerificationStatus.TAMPERED,
            ReadReceiptVerificationStatus.BINDING_MISMATCH,
        }:
            if self.receipt is None or self.host_issued is not True:
                raise ContractValidationError("invalid_read_receipt_status")
        elif self.host_issued is not False or self.receipt is not None:
            if self.status is not ReadReceiptVerificationStatus.DUPLICATE:
                raise ContractValidationError("invalid_read_receipt_status")
        if self.status is not ReadReceiptVerificationStatus.LOCAL_OBSERVATION_RECORDED:
            if self.local_test_host_observation is not False:
                raise ContractValidationError("local_observation_not_authenticated")
        elif self.local_test_host_observation is not True:
            raise ContractValidationError("invalid_read_receipt_status")
        if any(gap not in self.gaps for gap in _ALWAYS_GAPS):
            raise ContractValidationError("source_authenticity_not_established")

    def __bool__(self) -> bool:
        return False

    @property
    def source_authenticity(self) -> MasterSourceAuthenticity:
        return MasterSourceAuthenticity.NOT_ESTABLISHED

    @property
    def production_authority(self) -> MasterProductionAuthority:
        return MasterProductionAuthority.DENIED

    @property
    def execution_authority(self) -> MasterRouteExecutionAuthority:
        return MasterRouteExecutionAuthority.DENIED

    @property
    def request_coverage(self) -> MasterRequestCoverage:
        return MasterRequestCoverage.NOT_ESTABLISHED

    @property
    def dispatch_permitted(self) -> bool:
        return False

    @property
    def evidence_admitted(self) -> bool:
        return False

    @property
    def aviation_evidence_admitted(self) -> bool:
        return False

    @property
    def verified_zero_established(self) -> bool:
        return False

    @property
    def polygon_intersection_established(self) -> bool:
        return False

    @property
    def impacted_aircraft_count_established(self) -> bool:
        return False

    @property
    def production_origin(self) -> ProductionOrigin:
        return ProductionOrigin.UNRESOLVED

    @property
    def production_grant_consumed(self) -> bool:
        return False

    @property
    def production_ledger_consumed(self) -> bool:
        return False

    @property
    def authenticated_source_read_available(self) -> bool:
        return False


def ma4_integration_requirements() -> tuple[Ma4IntegrationFinding, ...]:
    """Exact MA4 producers. Every requirement stays unresolved."""

    return tuple(
        Ma4IntegrationFinding(
            requirement=requirement,
            status=ProvenanceRequirementStatus.UNRESOLVED,
            producer=_MA4_PRODUCERS[requirement],
        )
        for requirement in Ma4IntegrationRequirement
    )


def _geographic(
    *,
    region_text: str | None,
    state_grouping: tuple[str, ...],
    region_scope: tuple[str, ...],
    claimed_count: int | None,
    claimed_polygon: bool,
) -> tuple[GeographicLimitation, ...]:
    selected: set[GeographicLimitation] = set()
    if region_text:
        selected.add(GeographicLimitation.REGIONAL_TEXT_NOT_POLYGON)
    if state_grouping or _state_like(region_scope) or (region_text is not None and _state_like((region_text,))):
        selected.add(GeographicLimitation.STATE_GROUPING_NOT_POLYGON)
    if claimed_count is not None:
        selected.add(GeographicLimitation.IMPACT_COUNT_NOT_ESTABLISHED)
    if claimed_polygon:
        selected.add(GeographicLimitation.POLYGON_INTERSECTION_NOT_ESTABLISHED)
        selected.add(GeographicLimitation.IMPACT_COUNT_NOT_ESTABLISHED)
    return tuple(_ordered(GeographicLimitation, selected))


def _structural_denials(
    context: DescriptiveReadContext,
    query: tuple[tuple[str, str | int], ...],
    *,
    observed_utc: str,
    tool_calls_used: int,
    cancelled: bool,
) -> tuple[MasterReadDenialReason, ...]:
    """Allowlist ceilings checked at execution.

    ``PRINCIPAL_NOT_TRUSTED`` is expected: this host does not seal an ingress
    principal. Any other denial blocks the adapter call. An empty structural
    result is not a production grant and is not source authentication.
    """

    start, end = _window_bounds(query)
    proposal_start = context.historical_window_start_utc or start
    proposal_end = context.historical_window_end_utc or end
    proposal = MasterReadResourceProposal(
        specialist=context.specialist,
        purpose=context.purpose,
        capability=context.capability,
        tool_name=context.tool_name,
        resource_id=context.resource_id,
        operation=context.operation,
        arguments=query,
        master_request_id=context.master_request_id,
        invocation_id=context.invocation_id,
        historical_start_utc=proposal_start,
        historical_end_utc=proposal_end,
    )
    known_tools = server_read_tools(context.specialist)
    _executions, _model_turns, tool_ceiling = server_read_budget(context.specialist)
    if not isinstance(tool_ceiling, int):
        raise ContractValidationError("invalid_server_budget")
    if context.tool_name in known_tools:
        allowed_names = server_read_argument_names(context.specialist, context.tool_name)
        allowed_resources = server_read_resources(context.specialist, context.tool_name)
        if not isinstance(allowed_names, frozenset) or not isinstance(allowed_resources, frozenset):
            raise ContractValidationError("invalid_server_allowlist")
    evaluation = ServerReadEvaluation(
        observed_utc=observed_utc,
        deadline_utc=context.deadline_utc,
        executions_used=0,
        model_turns_used=0,
        tool_calls_used=tool_calls_used,
        cancellation=ServerCancellation(cancelled=cancelled),
    )
    check = check_master_read_resource(proposal, evaluation=evaluation)
    return tuple(reason for reason in check.denial_reasons if reason not in _PRINCIPAL_ONLY_DENIALS)


def _classify_observation(
    context: DescriptiveReadContext,
    observation: LocalAdapterObservation,
    *,
    observation_clock: str | None,
    request_clock: str,
) -> tuple[
    tuple[PopulationCondition, ...],
    tuple[ReadReceiptGap, ...],
    tuple[GeographicLimitation, ...],
    str | None,
    str | None,
    ReadResponseDisposition,
    bool,
]:
    conditions: set[PopulationCondition] = set()
    extra: set[ReadReceiptGap] = set()
    present_versions = [row.source_version for row in observation.rows if row.source_version is not None]
    if observation.response_source_version is not None:
        present_versions.append(observation.response_source_version)
    version_omitted = bool(observation.rows) and any(row.source_version is None for row in observation.rows)
    if not present_versions or version_omitted:
        conditions.add(PopulationCondition.SOURCE_VERSION_ABSENT)
        extra.add(ReadReceiptGap.MISSING_SOURCE_VERSION)
        if not present_versions:
            extra.add(ReadReceiptGap.HISTORICAL_NATIVE_FIELD_ABSENT)
    if present_versions:
        extra.add(ReadReceiptGap.SOURCE_VERSION_NOT_INTEGRITY_PROOF)
    if (
        context.expected_source_version is not None
        and present_versions
        and any(item != context.expected_source_version for item in present_versions)
    ):
        conditions.add(PopulationCondition.SOURCE_VERSION_MISMATCH)
        extra.add(ReadReceiptGap.SOURCE_VERSION_MISMATCH)
    if observation.query_id is None:
        conditions.add(PopulationCondition.MISSING_QUERY_ID)
        extra.add(ReadReceiptGap.MISSING_QUERY_ID)
        extra.add(ReadReceiptGap.HISTORICAL_NATIVE_FIELD_ABSENT)
    else:
        extra.add(ReadReceiptGap.QUERY_ID_NOT_AUTHENTICATED)
    incomplete = _pagination_incomplete(observation)
    if incomplete:
        conditions.add(PopulationCondition.PAGINATION_INCOMPLETE)
        extra.add(ReadReceiptGap.PAGINATION_INCOMPLETE)
    stale = (
        observation.freshness is FreshnessStatus.STALE
        or observation.response_status is ToolResultStatus.STALE
    )
    if stale:
        conditions.add(PopulationCondition.SOURCE_STALE)
        extra.add(ReadReceiptGap.SOURCE_STALE)
    partial = observation.response_status is ToolResultStatus.PARTIAL or observation.truncated
    if partial:
        conditions.add(PopulationCondition.PARTIAL_RESULT)
        extra.add(ReadReceiptGap.PARTIAL_RESULT)
    omitted = observation.source_omitted_record or any(row.record_id is None for row in observation.rows)
    if omitted:
        conditions.add(PopulationCondition.SOURCE_OMITTED_RECORD)
        extra.add(ReadReceiptGap.SOURCE_OMITTED_RECORD)
    contradictory = (
        observation.rows_returned is not None
        and observation.rows
        and observation.rows_returned != len(observation.rows)
    )
    if contradictory:
        extra.add(ReadReceiptGap.CONTRADICTORY_ROW_COUNT)
    zero_rows = observation.rows_returned == 0 and not contradictory
    if zero_rows:
        conditions.add(PopulationCondition.QUERY_RETURNED_ZERO_ROWS)
        extra.add(ReadReceiptGap.QUERY_RETURNED_ZERO_ROWS)
    elif observation.rows_returned is not None and observation.rows_returned > 0 and not contradictory:
        conditions.add(PopulationCondition.ROWS_PRESENT)
    if observation.response_status is ToolResultStatus.NOT_FOUND:
        conditions.add(PopulationCondition.NOT_FOUND_NOT_VERIFIED_ZERO)
        extra.add(ReadReceiptGap.NOT_FOUND_NOT_VERIFIED_ZERO)
    evaluation_clock = observation.evaluation_clock_utc
    query_clock = observation.query_clock_utc
    if observation.observation_clock_utc is not None:
        extra.add(ReadReceiptGap.WRONG_CLOCK)
    if evaluation_clock is not None and evaluation_clock in {request_clock, observation_clock, query_clock}:
        extra.add(ReadReceiptGap.WRONG_CLOCK)
        evaluation_clock = None
    if query_clock is not None and query_clock == request_clock:
        extra.add(ReadReceiptGap.WRONG_CLOCK)
        query_clock = None
    if (
        context.max_model_turns is not None
        or context.max_tool_calls is not None
    ):
        _executions, model_turns, tool_calls = server_read_budget(context.specialist)
        if context.max_model_turns not in (None, model_turns) or context.max_tool_calls not in (None, tool_calls):
            extra.add(ReadReceiptGap.CALLER_CEILING_IGNORED)
    geographic = _geographic(
        region_text=observation.region_text,
        state_grouping=observation.state_grouping,
        region_scope=context.region_scope,
        claimed_count=observation.claimed_impacted_aircraft_count,
        claimed_polygon=observation.claimed_polygon_intersection,
    )
    if geographic:
        extra.add(ReadReceiptGap.FORGED_REGIONAL_IMPACT)
    scoped_shape = (
        observation.scoped_authoritative_empty
        and zero_rows
        and not omitted
        and not incomplete
        and not stale
        and not partial
        and observation.query_id is not None
        and bool(present_versions)
        and PopulationCondition.SOURCE_VERSION_MISMATCH not in conditions
        and evaluation_clock is not None
        and observation_clock is not None
        and query_clock is not None
        and not geographic
    )
    if observation.scoped_authoritative_empty:
        extra.add(ReadReceiptGap.SCOPED_EMPTY_NOT_VERIFIED_ZERO)
        if scoped_shape:
            conditions.add(PopulationCondition.SCOPED_AUTHORITATIVE_EMPTY_POPULATION)
    if contradictory:
        disposition = ReadResponseDisposition.REJECTED
    elif observation.response_status is ToolResultStatus.NOT_FOUND:
        disposition = ReadResponseDisposition.NOT_FOUND
    elif partial or incomplete:
        disposition = ReadResponseDisposition.PARTIAL
    elif stale:
        disposition = ReadResponseDisposition.STALE
    elif zero_rows:
        disposition = ReadResponseDisposition.ZERO_ROWS
    else:
        disposition = ReadResponseDisposition.COMPLETED
    reported_complete = not incomplete and not partial
    return (
        tuple(_ordered(PopulationCondition, conditions)),
        _gaps(*extra),
        geographic,
        evaluation_clock,
        query_clock,
        disposition,
        reported_complete,
    )


def _verification_from_receipt(
    receipt: HostIssuedReadReceipt,
    *,
    status: ReadReceiptVerificationStatus,
    local_observation: bool,
) -> ReadReceiptVerification:
    return ReadReceiptVerification(
        status=status,
        origin=ReadOrigin.LOCAL_TRUSTED_ADAPTER_OBSERVATION,
        host_issued=True,
        adapter_invoked=receipt.adapter_invoked,
        local_test_host_observation=local_observation,
        population_conditions=receipt.population_conditions,
        untrusted_indications=(),
        geographic_limitations=receipt.geographic_limitations,
        gaps=receipt.gaps,
        missing_verified_zero_conditions=_missing_verified_zero(
            conditions=set(receipt.population_conditions),
            evaluation_clock=receipt.evaluation_clock_utc,
            observation_clock=receipt.observation_clock_utc,
            query_clock=receipt.query_clock_utc,
            geographic=receipt.geographic_limitations,
            host_observed=status is ReadReceiptVerificationStatus.LOCAL_OBSERVATION_RECORDED,
            receipt_issued=True,
        ),
        provenance_findings=_provenance_findings(),
        denial_reasons=receipt.denial_reasons,
        receipt=receipt,
        request_clock_utc=receipt.request_clock_utc,
        observation_clock_utc=receipt.observation_clock_utc,
        evaluation_clock_utc=receipt.evaluation_clock_utc,
        query_clock_utc=receipt.query_clock_utc,
        master_request_id=receipt.master_request_id,
        step_id=receipt.step_id,
        tool_call_id=receipt.tool_call_id,
        query_diverged=receipt.query_diverged,
        asserted_grant_ref=receipt.asserted_grant_ref,
        asserted_ledger_ref=receipt.asserted_ledger_ref,
        local_consumption_id=receipt.local_consumption_id,
        untrusted_claimed_impacted_aircraft_count=receipt.untrusted_claimed_impacted_aircraft_count,
    )


def _untrusted_verification(subject: object) -> ReadReceiptVerification:
    indications: set[UntrustedPopulationIndication] = set()
    geographic: set[GeographicLimitation] = set()
    extra = {
        ReadReceiptGap.CALLER_SUPPLIED_METADATA_NOT_PROOF,
        ReadReceiptGap.TOOL_RESULT_NOT_RECEIPT,
    }
    claimed_count: int | None = None
    if isinstance(subject, (ToolResult, Evidence, ProposedDispatcherExecutionEvent)):
        evidence = subject.evidence if isinstance(subject, ToolResult) else ()
        if isinstance(subject, Evidence):
            evidence = (subject,)
        for item in evidence:
            if not item.source_records:
                indications.add(UntrustedPopulationIndication.CALLER_REPORTED_OMITTED_RECORDS)
            if item.freshness_status is FreshnessStatus.STALE:
                indications.add(UntrustedPopulationIndication.CALLER_REPORTED_STALE)
            cardinality = item.match_cardinality
            if cardinality is not None and cardinality.is_exact is True and cardinality.exact_count == 0:
                indications.add(UntrustedPopulationIndication.CALLER_REPORTED_EXACT_ZERO)
            if item.query_executions and all(trace.rows_returned == 0 for trace in item.query_executions):
                indications.add(UntrustedPopulationIndication.CALLER_REPORTED_ZERO_ROWS)
            if item.completeness is not None and (item.completeness.status or "").strip().upper() in _PARTIAL_COMPLETENESS:
                indications.add(UntrustedPopulationIndication.CALLER_REPORTED_PARTIAL)
        if isinstance(subject, ToolResult):
            if subject.status is ToolResultStatus.NOT_FOUND:
                indications.add(UntrustedPopulationIndication.CALLER_REPORTED_NOT_FOUND)
            if subject.status is ToolResultStatus.PARTIAL:
                indications.add(UntrustedPopulationIndication.CALLER_REPORTED_PARTIAL)
            if subject.status is ToolResultStatus.STALE:
                indications.add(UntrustedPopulationIndication.CALLER_REPORTED_STALE)
            claimed_count, found_geo = _forged_regional_payload(subject.data)
            if found_geo:
                indications.add(UntrustedPopulationIndication.FORGED_REGIONAL_IMPACT)
                extra.add(ReadReceiptGap.FORGED_REGIONAL_IMPACT)
                geographic.update(found_geo)
    else:
        extra.add(ReadReceiptGap.FORGED_REGIONAL_IMPACT) if _payload_is_regional(subject) else None
        if _payload_is_regional(subject):
            indications.add(UntrustedPopulationIndication.FORGED_REGIONAL_IMPACT)
            geographic.add(GeographicLimitation.REGIONAL_TEXT_NOT_POLYGON)
            geographic.add(GeographicLimitation.POLYGON_INTERSECTION_NOT_ESTABLISHED)
    geo_tuple = tuple(_ordered(GeographicLimitation, geographic))
    return ReadReceiptVerification(
        status=ReadReceiptVerificationStatus.NOT_A_RECEIPT,
        origin=ReadOrigin.UNRESOLVED,
        host_issued=False,
        adapter_invoked=False,
        local_test_host_observation=False,
        population_conditions=(),
        untrusted_indications=tuple(_ordered(UntrustedPopulationIndication, indications)),
        geographic_limitations=geo_tuple,
        gaps=_gaps(*extra),
        missing_verified_zero_conditions=_missing_verified_zero(
            conditions=set(),
            evaluation_clock=None,
            observation_clock=None,
            query_clock=None,
            geographic=geo_tuple,
            host_observed=False,
            receipt_issued=False,
        ),
        provenance_findings=_provenance_findings(),
        denial_reasons=(),
        untrusted_claimed_impacted_aircraft_count=claimed_count,
    )


def _payload_is_regional(subject: object) -> bool:
    if isinstance(subject, Mapping):
        keys = {str(key).casefold() for key in subject}
        if keys & {item.casefold() for item in _REGION_TEXT_KEYS | _IMPACT_KEYS | _POLYGON_KEYS}:
            return True
        blob = " ".join(str(value).casefold() for value in subject.values() if isinstance(value, str))
        return "california" in blob
    return False


def _forged_regional_payload(data: object) -> tuple[int | None, set[GeographicLimitation]]:
    found: set[GeographicLimitation] = set()
    claimed: int | None = None
    if not isinstance(data, Mapping):
        return None, found
    for key, value in data.items():
        if not isinstance(key, str):
            continue
        folded = key.casefold()
        if folded in _REGION_TEXT_KEYS or (isinstance(value, str) and "california" in value.casefold()):
            found.add(GeographicLimitation.REGIONAL_TEXT_NOT_POLYGON)
            if isinstance(value, str) and (_state_like((value,)) or folded in {"state", "state_code", "state_grouping"}):
                found.add(GeographicLimitation.STATE_GROUPING_NOT_POLYGON)
        if folded in _IMPACT_KEYS:
            found.add(GeographicLimitation.IMPACT_COUNT_NOT_ESTABLISHED)
            if type(value) is int:
                claimed = value
        if folded in _POLYGON_KEYS:
            found.add(GeographicLimitation.POLYGON_INTERSECTION_NOT_ESTABLISHED)
            found.add(GeographicLimitation.IMPACT_COUNT_NOT_ESTABLISHED)
    return claimed, found


class LocalTrustedReadHost:
    """In-process test host. Production input cannot obtain a seal from it.

    There is no event constructor, environment switch, or authorizer parser.
    Registering an adapter here does not register a production adapter.
    """

    def __init__(self, clock: object) -> None:
        if not callable(getattr(clock, "now_utc", None)):
            raise ContractValidationError("invalid_host_clock")
        self._clock = clock
        self._token = _HostToken()
        with _HOST_LOCK:
            _LIVE_HOSTS.add(id(self._token))
        self._executors: dict[int, Callable[[ApprovedExecutorAction], object]] = {}
        self._contexts: dict[int, DescriptiveReadContext] = {}
        self._tool_calls: set[str] = set()
        self._idempotency: dict[str, str] = {}
        self._route_counts: dict[str, int] = {}
        self._decision_counts: dict[str, int] = {}
        self._tool_calls_used: dict[tuple[str, str], int] = {}
        self._receipt_count = 0
        self._lock = threading.RLock()

    def register_local_adapter(
        self,
        adapter_identity: str,
        executor: Callable[[ApprovedExecutorAction], object],
    ) -> LocalAdapterRegistration:
        """Bind a callable to this host. The callable is not a production adapter."""

        _token(adapter_identity, "adapter_identity")
        if not callable(executor):
            raise ContractValidationError("adapter_not_callable")
        if getattr(executor, "wilvor_production_adapter", False) is True:
            raise ContractValidationError("production_adapter_not_available")
        seal = _RegistrationSeal(self._token)
        registration = LocalAdapterRegistration(adapter_identity=adapter_identity, _seal=seal)
        self._executors[id(seal)] = executor
        return registration

    def approve_executor_action(
        self,
        registration: LocalAdapterRegistration,
        context: DescriptiveReadContext,
    ) -> ApprovedExecutorAction:
        """Bind one descriptive envelope to a registered local adapter.

        Route and decision ceilings are applied here. Tool allowlist,
        historical interval, budget, and cancellation are applied again when
        ``execute_read`` runs.
        """

        if type(registration) is not LocalAdapterRegistration or type(context) is not DescriptiveReadContext:
            raise ContractValidationError("invalid_executor_action")
        if type(registration._seal) is not _RegistrationSeal or registration._seal.host_token is not self._token:
            raise ContractValidationError("adapter_not_registered")
        if registration.adapter_identity != context.adapter_identity:
            raise ContractValidationError("adapter_identity_mismatch")
        if id(registration._seal) not in self._executors:
            raise ContractValidationError("adapter_not_registered")
        with self._lock:
            if context.tool_call_id in self._tool_calls:
                raise DuplicateReadReceipt(_duplicate_verification(context))
            if (
                context.idempotency_key is not None
                and context.idempotency_key in self._idempotency
            ):
                raise DuplicateReadReceipt(_duplicate_verification(context))
            route_count = self._route_counts.get(context.master_request_id, 0)
            if route_count >= MAX_ROUTE_STEPS:
                raise ReadReceiptRejected(_rejected_verification(context, "route_ceiling"))
            if context.specialist is MasterSpecialist.DECISION:
                decision_count = self._decision_counts.get(context.master_request_id, 0)
                if decision_count >= MAX_DECISION_STEPS:
                    raise ReadReceiptRejected(_rejected_verification(context, "decision_ceiling"))
            self._tool_calls.add(context.tool_call_id)
            if context.idempotency_key is not None:
                self._idempotency[context.idempotency_key] = context.tool_call_id
            self._route_counts[context.master_request_id] = route_count + 1
            if context.specialist is MasterSpecialist.DECISION:
                self._decision_counts[context.master_request_id] = (
                    self._decision_counts.get(context.master_request_id, 0) + 1
                )
            action_seal = _ActionSeal(self._token, registration._seal)
            action = ApprovedExecutorAction(
                master_request_id=context.master_request_id,
                step_id=context.step_id,
                invocation_id=context.invocation_id,
                tool_call_id=context.tool_call_id,
                tool_name=context.tool_name,
                adapter_identity=context.adapter_identity,
                _seal=action_seal,
            )
            self._contexts[id(action_seal)] = context
        return action

    def execute_read(
        self,
        action: ApprovedExecutorAction,
        *,
        cancellation: ReadCancellation | None = None,
    ) -> HostIssuedReadReceipt:
        """Run the bound local adapter, or record a failure without admitting evidence."""

        if type(action) is not ApprovedExecutorAction or type(action._seal) is not _ActionSeal:
            raise ContractValidationError("executor_action_not_approved")
        if action._seal.host_token is not self._token:
            raise ContractValidationError("executor_action_not_approved")
        with self._lock:
            if action._seal.spent:
                raise DuplicateReadReceipt(_duplicate_verification(self._contexts[id(action._seal)]))
            action._seal.spent = True
            context = self._contexts.get(id(action._seal))
            if context is None:
                raise ContractValidationError("executor_action_not_approved")
            executor = self._executors.get(id(action._seal.registration_seal))
            if executor is None:
                raise ContractValidationError("adapter_not_registered")
            observed_utc = _clock_reader(self._clock)
            cancelled = cancellation is not None and cancellation.is_cancelled()
            key = (context.master_request_id, context.invocation_id)
            used = self._tool_calls_used.get(key, 0)
            denials = _structural_denials(
                context,
                context.requested_query,
                observed_utc=observed_utc,
                tool_calls_used=used,
                cancelled=cancelled,
            )
            if denials or cancelled:
                self._tool_calls_used[key] = used + 1
                disposition = (
                    ReadResponseDisposition.CANCELLED
                    if cancelled or MasterReadDenialReason.CANCELLED in denials
                    else ReadResponseDisposition.REJECTED
                )
                receipt = self._build_receipt(
                    context,
                    executed_query=(),
                    query_diverged=False,
                    response_status=None,
                    disposition=disposition,
                    adapter_invoked=False,
                    observation_clock=None,
                    evaluation_clock=None,
                    query_clock=None,
                    conditions=(
                        {PopulationCondition.CANCELLED}
                        if disposition is ReadResponseDisposition.CANCELLED
                        else set()
                    ),
                    extra_gaps={
                        ReadReceiptGap.CANCELLED
                        if disposition is ReadResponseDisposition.CANCELLED
                        else ReadReceiptGap.STRUCTURAL_ALLOWLIST_DENIAL
                    },
                    geographic=(),
                    page_index=None,
                    page_count=None,
                    page_limit=None,
                    row_limit=None,
                    truncated=False,
                    has_more=False,
                    rows_returned=None,
                    record_ids=(),
                    source_versions=(),
                    response_source_version=None,
                    query_ids=(),
                    reported_pagination_complete=False,
                    claimed_count=None,
                    claimed_polygon=False,
                    denial_reasons=denials,
                )
                verification = _verification_from_receipt(
                    receipt,
                    status=ReadReceiptVerificationStatus.LOCAL_FAILURE_RECORDED,
                    local_observation=False,
                )
                if disposition is ReadResponseDisposition.CANCELLED:
                    raise ReadReceiptCancelled(verification)
                raise ReadReceiptRejected(verification)
            if cancellation is not None and cancellation.is_cancelled():
                raise ReadReceiptCancelled(
                    _verification_from_receipt(
                        self._failure_shell(context, ReadResponseDisposition.CANCELLED, invoked=False),
                        status=ReadReceiptVerificationStatus.LOCAL_FAILURE_RECORDED,
                        local_observation=False,
                    )
                )
            self._tool_calls_used[key] = used + 1
        try:
            raw = executor(action)
        except LocalReadTimeout:
            receipt = self._failure_shell(context, ReadResponseDisposition.TIMEOUT, invoked=True)
            raise ReadReceiptTimeout(
                _verification_from_receipt(
                    receipt,
                    status=ReadReceiptVerificationStatus.LOCAL_FAILURE_RECORDED,
                    local_observation=False,
                )
            ) from None
        except LocalReadCancellation:
            receipt = self._failure_shell(context, ReadResponseDisposition.CANCELLED, invoked=True)
            raise ReadReceiptCancelled(
                _verification_from_receipt(
                    receipt,
                    status=ReadReceiptVerificationStatus.LOCAL_FAILURE_RECORDED,
                    local_observation=False,
                )
            ) from None
        if cancellation is not None and cancellation.is_cancelled():
            receipt = self._failure_shell(context, ReadResponseDisposition.CANCELLED, invoked=True)
            raise ReadReceiptCancelled(
                _verification_from_receipt(
                    receipt,
                    status=ReadReceiptVerificationStatus.LOCAL_FAILURE_RECORDED,
                    local_observation=False,
                )
            )
        if type(raw) is not LocalAdapterObservation:
            receipt = self._failure_shell(context, ReadResponseDisposition.REJECTED, invoked=True)
            raise ReadReceiptRejected(
                _verification_from_receipt(
                    receipt,
                    status=ReadReceiptVerificationStatus.LOCAL_FAILURE_RECORDED,
                    local_observation=False,
                )
            )
        observation_clock = _clock_reader(self._clock)
        executed_denials = _structural_denials(
            context,
            raw.executed_query,
            observed_utc=observed_utc,
            tool_calls_used=used,
            cancelled=False,
        )
        diverged = raw.executed_query != context.requested_query
        if executed_denials:
            receipt = self._build_receipt(
                context,
                executed_query=raw.executed_query,
                query_diverged=diverged,
                response_status=None,
                disposition=ReadResponseDisposition.REJECTED,
                adapter_invoked=True,
                observation_clock=observation_clock,
                evaluation_clock=None,
                query_clock=None,
                conditions=set(),
                extra_gaps={
                    ReadReceiptGap.STRUCTURAL_ALLOWLIST_DENIAL,
                    ReadReceiptGap.REQUESTED_QUERY_NOT_EXECUTED,
                },
                geographic=(),
                page_index=None,
                page_count=None,
                page_limit=None,
                row_limit=None,
                truncated=False,
                has_more=False,
                rows_returned=None,
                record_ids=(),
                source_versions=(),
                response_source_version=None,
                query_ids=(),
                reported_pagination_complete=False,
                claimed_count=None,
                claimed_polygon=False,
                denial_reasons=executed_denials,
            )
            raise ReadReceiptRejected(
                _verification_from_receipt(
                    receipt,
                    status=ReadReceiptVerificationStatus.LOCAL_FAILURE_RECORDED,
                    local_observation=False,
                )
            )
        (
            conditions,
            gaps,
            geographic,
            evaluation_clock,
            query_clock,
            disposition,
            reported_complete,
        ) = _classify_observation(
            context,
            raw,
            observation_clock=observation_clock,
            request_clock=context.accepted_at_utc,
        )
        if diverged:
            gaps = _gaps(*gaps, ReadReceiptGap.REQUESTED_QUERY_NOT_EXECUTED)
        record_ids = tuple(row.record_id for row in raw.rows if row.record_id is not None)
        source_versions = tuple(row.source_version for row in raw.rows)
        if not raw.rows and raw.response_source_version is not None:
            source_versions = (raw.response_source_version,)
        query_ids = (raw.query_id,) if raw.query_id is not None else ()
        return self._build_receipt(
            context,
            executed_query=raw.executed_query,
            query_diverged=diverged,
            response_status=raw.response_status,
            disposition=disposition,
            adapter_invoked=True,
            observation_clock=observation_clock,
            evaluation_clock=evaluation_clock,
            query_clock=query_clock,
            conditions=set(conditions),
            extra_gaps=set(gaps),
            geographic=geographic,
            page_index=raw.page_index,
            page_count=raw.page_count,
            page_limit=raw.page_limit,
            row_limit=raw.row_limit,
            truncated=raw.truncated,
            has_more=raw.has_more,
            rows_returned=raw.rows_returned,
            record_ids=record_ids,
            source_versions=source_versions,
            response_source_version=raw.response_source_version,
            query_ids=query_ids,
            reported_pagination_complete=reported_complete,
            claimed_count=raw.claimed_impacted_aircraft_count,
            claimed_polygon=raw.claimed_polygon_intersection,
            denial_reasons=(),
            preset_gaps=gaps,
            preset_conditions=conditions,
        )

    def _failure_shell(
        self,
        context: DescriptiveReadContext,
        disposition: ReadResponseDisposition,
        *,
        invoked: bool,
    ) -> HostIssuedReadReceipt:
        gap = {
            ReadResponseDisposition.TIMEOUT: ReadReceiptGap.TIMEOUT,
            ReadResponseDisposition.CANCELLED: ReadReceiptGap.CANCELLED,
        }.get(disposition, ReadReceiptGap.STRUCTURAL_ALLOWLIST_DENIAL)
        condition = {
            ReadResponseDisposition.TIMEOUT: PopulationCondition.TIMEOUT,
            ReadResponseDisposition.CANCELLED: PopulationCondition.CANCELLED,
        }.get(disposition)
        observation_clock = _clock_reader(self._clock) if invoked else None
        return self._build_receipt(
            context,
            executed_query=(),
            query_diverged=False,
            response_status=None,
            disposition=disposition,
            adapter_invoked=invoked,
            observation_clock=observation_clock,
            evaluation_clock=None,
            query_clock=None,
            conditions={condition} if condition is not None else set(),
            extra_gaps={gap},
            geographic=(),
            page_index=None,
            page_count=None,
            page_limit=None,
            row_limit=None,
            truncated=False,
            has_more=False,
            rows_returned=None,
            record_ids=(),
            source_versions=(),
            response_source_version=None,
            query_ids=(),
            reported_pagination_complete=False,
            claimed_count=None,
            claimed_polygon=False,
            denial_reasons=(),
        )

    def _build_receipt(self, context: DescriptiveReadContext, **kwargs: object) -> HostIssuedReadReceipt:
        with self._lock:
            self._receipt_count += 1
            receipt_id = f"local-receipt-{self._receipt_count}"
            consumption_id = f"local-consumption-{self._receipt_count}"
        executions, model_turns, tool_calls = server_read_budget(context.specialist)
        preset_gaps = kwargs.get("preset_gaps")
        preset_conditions = kwargs.get("preset_conditions")
        extra_gaps = kwargs["extra_gaps"]
        conditions = kwargs["conditions"]
        gaps = preset_gaps if isinstance(preset_gaps, tuple) else _gaps(*extra_gaps)
        population = (
            preset_conditions
            if isinstance(preset_conditions, tuple)
            else tuple(_ordered(PopulationCondition, conditions))
        )
        payload = dict(
            schema_version=MASTER_AUTHENTICATED_READ_RECEIPT_SCHEMA_VERSION,
            receipt_id=receipt_id,
            master_request_id=context.master_request_id,
            planning_session_id=context.planning_session_id,
            step_id=context.step_id,
            invocation_id=context.invocation_id,
            specialist_invocation_id=context.specialist_invocation_id,
            specialist=context.specialist,
            purpose=context.purpose,
            capability=context.capability,
            principal_ref=context.principal_ref,
            policy_revision=context.policy_revision,
            resource_id=context.resource_id,
            resource_scope=context.resource_scope,
            operation=context.operation,
            region_scope=context.region_scope,
            historical_window_start_utc=context.historical_window_start_utc,
            historical_window_end_utc=context.historical_window_end_utc,
            decision_target_scope=context.decision_target_scope,
            accepted_at_utc=context.accepted_at_utc,
            deadline_utc=context.deadline_utc,
            idempotency_key=context.idempotency_key,
            correlation_id=context.correlation_id,
            server_max_executions=executions,
            server_max_model_turns=model_turns,
            server_max_tool_calls=tool_calls,
            caller_max_model_turns=context.max_model_turns,
            caller_max_tool_calls=context.max_tool_calls,
            adapter_identity=context.adapter_identity,
            tool_name=context.tool_name,
            tool_call_id=context.tool_call_id,
            requested_query=context.requested_query,
            executed_query=kwargs["executed_query"],
            query_diverged=kwargs["query_diverged"],
            local_consumption_id=consumption_id,
            asserted_grant_ref=context.asserted_grant_ref,
            asserted_ledger_ref=context.asserted_ledger_ref,
            response_status=kwargs["response_status"],
            disposition=kwargs["disposition"],
            page_index=kwargs["page_index"],
            page_count=kwargs["page_count"],
            page_limit=kwargs["page_limit"],
            row_limit=kwargs["row_limit"],
            truncated=kwargs["truncated"],
            has_more=kwargs["has_more"],
            rows_returned=kwargs["rows_returned"],
            record_ids=kwargs["record_ids"],
            source_versions=kwargs["source_versions"],
            response_source_version=kwargs["response_source_version"],
            query_ids=kwargs["query_ids"],
            adapter_invoked=kwargs["adapter_invoked"],
            request_clock_utc=context.accepted_at_utc,
            observation_clock_utc=kwargs["observation_clock"],
            evaluation_clock_utc=kwargs["evaluation_clock"],
            query_clock_utc=kwargs["query_clock"],
            population_conditions=population,
            reported_pagination_complete=kwargs["reported_pagination_complete"],
            geographic_limitations=kwargs["geographic"],
            untrusted_claimed_impacted_aircraft_count=kwargs["claimed_count"],
            claimed_polygon_intersection=kwargs["claimed_polygon"],
            denial_reasons=kwargs["denial_reasons"],
            gaps=gaps,
            expected_source_version=context.expected_source_version,
        )
        draft = SimpleNamespace(**payload)
        fingerprint = _material(draft)
        seal = _ReceiptSeal(self._token)
        seal.fingerprint = fingerprint
        with _HOST_LOCK:
            _ISSUED[id(seal)] = fingerprint
        return HostIssuedReadReceipt(**payload, _seal=seal)


def _duplicate_verification(context: DescriptiveReadContext) -> ReadReceiptVerification:
    return ReadReceiptVerification(
        status=ReadReceiptVerificationStatus.DUPLICATE,
        origin=ReadOrigin.UNRESOLVED,
        host_issued=False,
        adapter_invoked=False,
        local_test_host_observation=False,
        population_conditions=(),
        untrusted_indications=(),
        geographic_limitations=(),
        gaps=_gaps(),
        missing_verified_zero_conditions=_missing_verified_zero(
            conditions=set(),
            evaluation_clock=None,
            observation_clock=None,
            query_clock=None,
            geographic=(),
            host_observed=False,
            receipt_issued=False,
        ),
        provenance_findings=_provenance_findings(),
        denial_reasons=(),
        master_request_id=context.master_request_id,
        step_id=context.step_id,
        tool_call_id=context.tool_call_id,
    )


def _rejected_verification(context: DescriptiveReadContext, _reason: str) -> ReadReceiptVerification:
    return ReadReceiptVerification(
        status=ReadReceiptVerificationStatus.REJECTED,
        origin=ReadOrigin.UNRESOLVED,
        host_issued=False,
        adapter_invoked=False,
        local_test_host_observation=False,
        population_conditions=(),
        untrusted_indications=(),
        geographic_limitations=(),
        gaps=_gaps(ReadReceiptGap.STRUCTURAL_ALLOWLIST_DENIAL),
        missing_verified_zero_conditions=_missing_verified_zero(
            conditions=set(),
            evaluation_clock=None,
            observation_clock=None,
            query_clock=None,
            geographic=(),
            host_observed=False,
            receipt_issued=False,
        ),
        provenance_findings=_provenance_findings(),
        denial_reasons=(),
        master_request_id=context.master_request_id,
        step_id=context.step_id,
        tool_call_id=context.tool_call_id,
    )


def assess_untrusted_read_subject(subject: object) -> ReadReceiptVerification:
    """Classify caller-supplied metadata. This does not issue a receipt."""

    return _untrusted_verification(subject)


def verify_read_receipt(
    host: LocalTrustedReadHost,
    receipt: object,
    *,
    expected_master_request_id: str | None = None,
    expected_step_id: str | None = None,
) -> ReadReceiptVerification:
    """Check host ownership, tamper, and replay. Production origin stays unresolved."""

    if type(host) is not LocalTrustedReadHost:
        raise ContractValidationError("invalid_read_host")
    if type(receipt) is not HostIssuedReadReceipt:
        return assess_untrusted_read_subject(receipt)
    seal = receipt._seal
    if type(seal) is not _ReceiptSeal or seal.host_token is not host._token:
        return ReadReceiptVerification(
            status=ReadReceiptVerificationStatus.UNTRUSTED_SUBJECT,
            origin=ReadOrigin.UNRESOLVED,
            host_issued=False,
            adapter_invoked=False,
            local_test_host_observation=False,
            population_conditions=(),
            untrusted_indications=(),
            geographic_limitations=(),
            gaps=_gaps(ReadReceiptGap.CALLER_SUPPLIED_METADATA_NOT_PROOF),
            missing_verified_zero_conditions=_missing_verified_zero(
                conditions=set(),
                evaluation_clock=None,
                observation_clock=None,
                query_clock=None,
                geographic=(),
                host_observed=False,
                receipt_issued=False,
            ),
            provenance_findings=_provenance_findings(),
            denial_reasons=(),
        )
    if _material(receipt) != seal.fingerprint or _ISSUED.get(id(seal)) != seal.fingerprint:
        return _verification_from_receipt(
            receipt,
            status=ReadReceiptVerificationStatus.TAMPERED,
            local_observation=False,
        )
    if seal.spent:
        return _verification_from_receipt(
            receipt,
            status=ReadReceiptVerificationStatus.REPLAYED,
            local_observation=False,
        )
    if (
        expected_master_request_id is not None and expected_master_request_id != receipt.master_request_id
    ) or (expected_step_id is not None and expected_step_id != receipt.step_id):
        return _verification_from_receipt(
            receipt,
            status=ReadReceiptVerificationStatus.BINDING_MISMATCH,
            local_observation=False,
        )
    seal.spent = True
    status = (
        ReadReceiptVerificationStatus.LOCAL_FAILURE_RECORDED
        if receipt.disposition
        in {
            ReadResponseDisposition.TIMEOUT,
            ReadResponseDisposition.CANCELLED,
            ReadResponseDisposition.REJECTED,
        }
        else ReadReceiptVerificationStatus.LOCAL_OBSERVATION_RECORDED
    )
    return _verification_from_receipt(
        receipt,
        status=status,
        local_observation=status is ReadReceiptVerificationStatus.LOCAL_OBSERVATION_RECORDED,
    )
