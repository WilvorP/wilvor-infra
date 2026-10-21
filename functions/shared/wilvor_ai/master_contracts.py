"""Master-owned invocation contracts, schema wilvor.ai.master_invocation.v1.

This module classifies specialist artifacts for a future Master Agent.
It does not run specialists, install an orchestrator, or issue production
factual authority. Structural validity and specialist-reported verification
are not production authorization.

A later application-controlled adapter may call specialists. This module
reserves that authority and exposes no constructor that can mint it.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping

from wilvor_ai.contracts import (
    PROVENANCE_TOKEN_MAX_LENGTH,
    ContractValidationError,
    JsonValue,
    TemporalScope,
    _parse_enum,
    _required,
    _sequence,
    _validate_bounded_text,
    _validate_utc,
)
from wilvor_ai.decision_specialist_runtime_contracts import (
    DecisionSpecialistRunResult,
    DecisionSpecialistRunStatus,
)
from wilvor_ai.live_ops_public_contracts import (
    LIVE_OPS_COMPLETENESS_NOTICE,
    LiveOpsDiscovery,
    LiveOpsDiscoveryStatus,
    LiveOpsPublicVerificationOutcome,
    LiveOpsRunRequest,
    LiveOpsRunResult,
    LiveOpsRunStatus,
    LiveOpsTrustedContext,
    present_live_ops_result,
    validate_live_ops_presentation,
)
from wilvor_ai.specialist_contracts import (
    SpecialistResult,
    SpecialistStatus,
    VerifiedZeroClaim,
    VerifierOutcome,
)
from wilvor_ai.specialist_runtime_contracts import (
    HistoricalSpecialistRunResult,
    SpecialistRunStatus,
)

MASTER_INVOCATION_SCHEMA_VERSION = "wilvor.ai.master_invocation.v1"
_RESULT_TRUNCATED = "RESULT_TRUNCATED"
_FINAL_CLAIMS = "FINAL_CLAIMS"
_FACTUAL = "FACTUAL"


class MasterSpecialist(str, Enum):
    HISTORICAL_ANALYTICS = "HISTORICAL_ANALYTICS"
    DECISION = "DECISION"
    LIVE_OPS = "LIVE_OPS"


class MasterPurpose(str, Enum):
    DISCOVERY = "DISCOVERY"
    ANSWER = "ANSWER"


class MasterRequirement(str, Enum):
    REQUIRED = "REQUIRED"
    OPTIONAL = "OPTIONAL"


class MasterDisposition(str, Enum):
    EXECUTED = "EXECUTED"
    UNFINALIZED = "UNFINALIZED"
    VERIFICATION_FAILED = "VERIFICATION_FAILED"
    UNSUPPORTED = "UNSUPPORTED"
    INVALID_REQUEST = "INVALID_REQUEST"
    UNAVAILABLE = "UNAVAILABLE"
    PROVIDER_FAILED = "PROVIDER_FAILED"
    PROVIDER_TIMEOUT = "PROVIDER_TIMEOUT"
    RUNTIME_TIMEOUT = "RUNTIME_TIMEOUT"
    BUDGET_EXHAUSTED = "BUDGET_EXHAUSTED"
    CANCELLED = "CANCELLED"
    NOT_RUN = "NOT_RUN"
    TARGET_UNRESOLVED = "TARGET_UNRESOLVED"
    TOOL_FAILED = "TOOL_FAILED"
    REFUSED = "REFUSED"


class MasterVerification(str, Enum):
    NOT_RUN = "NOT_RUN"
    PASSED = "PASSED"
    FAILED = "FAILED"
    NO_VERIFIED_FACTS = "NO_VERIFIED_FACTS"


class MasterCondition(str, Enum):
    """Independent quality flags. Several may apply to one invocation."""

    INCOMPLETE_INVESTIGATION = "INCOMPLETE_INVESTIGATION"
    TRUNCATED = "TRUNCATED"
    VERIFIED_ZERO = "VERIFIED_ZERO"
    SOURCE_COMPLETENESS_NOT_ESTABLISHED = "SOURCE_COMPLETENESS_NOT_ESTABLISHED"
    FRESHNESS_UNKNOWN = "FRESHNESS_UNKNOWN"


class MasterReportedShape(str, Enum):
    """What the specialist artifact reports. Not production authority."""

    QUALIFIES = "QUALIFIES"
    DOES_NOT_QUALIFY = "DOES_NOT_QUALIFY"


class MasterProductionAuthority(str, Enum):
    """PRODUCTION is reserved for a future adapter. This module cannot set it."""

    PRODUCTION = "PRODUCTION"
    DENIED = "DENIED"


class MasterBindingKind(str, Enum):
    UNBOUND = "UNBOUND"
    CONTRACT_TEST = "CONTRACT_TEST"
    DESERIALIZED = "DESERIALIZED"


class MasterBindingDefect(str, Enum):
    CORRELATION_MISMATCH = "CORRELATION_MISMATCH"
    TIME_MISMATCH = "TIME_MISMATCH"
    TARGET_MISMATCH = "TARGET_MISMATCH"


def _token(value: Any, field_name: str, *, optional: bool = False) -> None:
    errors = _validate_bounded_text(
        value,
        field_name,
        optional=optional,
        max_length=PROVENANCE_TOKEN_MAX_LENGTH,
    )
    if errors:
        raise ContractValidationError(errors)
    if value is None:
        return
    if value != value.strip():
        raise ContractValidationError(f"invalid_{field_name}")


def _clock(value: str | None, field_name: str) -> None:
    errors = _validate_utc(value, field_name, optional=True)
    if errors:
        raise ContractValidationError(errors)


def _text_tuple(value: tuple[str, ...], field_name: str) -> tuple[str, ...]:
    if not isinstance(value, tuple):
        raise ContractValidationError(f"invalid_{field_name}")
    checked: list[str] = []
    for item in value:
        _token(item, field_name)
        checked.append(item)
    return tuple(checked)


def _enum_tuple(value: tuple[Any, ...], enum_type: type[Enum], field_name: str) -> tuple[Any, ...]:
    if not isinstance(value, tuple) or any(not isinstance(item, enum_type) for item in value):
        raise ContractValidationError(f"invalid_{field_name}")
    return tuple(value)


def _deny_production(authority: MasterProductionAuthority) -> MasterProductionAuthority:
    if authority is not MasterProductionAuthority.DENIED:
        raise ContractValidationError("production_authority_not_issued")
    return authority


@dataclass(frozen=True)
class MasterInvocationAuthorization:
    """Application plan for one future invocation. It does not execute anything.

    ``expected_aircraft_id`` and ``expected_recommendation_id`` are comparison
    inputs only. They are not trusted Decision targets.
    """

    master_request_id: str
    invocation_id: str
    specialist: MasterSpecialist
    purpose: MasterPurpose
    requirement: MasterRequirement
    specialist_request_id: str | None = None
    correlation_id: str | None = None
    application_request_time_utc: str | None = None
    requested_evaluation_time_utc: str | None = None
    expected_aircraft_id: str | None = None
    expected_recommendation_id: str | None = None
    schema_version: str = MASTER_INVOCATION_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != MASTER_INVOCATION_SCHEMA_VERSION:
            raise ContractValidationError("invalid_schema_version")
        _token(self.master_request_id, "master_request_id")
        _token(self.invocation_id, "invocation_id")
        _token(self.specialist_request_id, "specialist_request_id", optional=True)
        _token(self.correlation_id, "correlation_id", optional=True)
        _token(self.expected_aircraft_id, "expected_aircraft_id", optional=True)
        _token(self.expected_recommendation_id, "expected_recommendation_id", optional=True)
        _clock(self.application_request_time_utc, "application_request_time_utc")
        _clock(self.requested_evaluation_time_utc, "requested_evaluation_time_utc")
        if not isinstance(self.specialist, MasterSpecialist):
            raise ContractValidationError("invalid_specialist")
        if not isinstance(self.purpose, MasterPurpose):
            raise ContractValidationError("invalid_purpose")
        if not isinstance(self.requirement, MasterRequirement):
            raise ContractValidationError("invalid_requirement")

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "schema_version": self.schema_version,
            "master_request_id": self.master_request_id,
            "invocation_id": self.invocation_id,
            "specialist": self.specialist.value,
            "purpose": self.purpose.value,
            "requirement": self.requirement.value,
            "specialist_request_id": self.specialist_request_id,
            "correlation_id": self.correlation_id,
            "application_request_time_utc": self.application_request_time_utc,
            "requested_evaluation_time_utc": self.requested_evaluation_time_utc,
            "expected_aircraft_id": self.expected_aircraft_id,
            "expected_recommendation_id": self.expected_recommendation_id,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "MasterInvocationAuthorization":
        if not isinstance(data, Mapping):
            raise ContractValidationError("invalid_master_invocation_authorization")
        allowed = {
            "schema_version",
            "master_request_id",
            "invocation_id",
            "specialist",
            "purpose",
            "requirement",
            "specialist_request_id",
            "correlation_id",
            "application_request_time_utc",
            "requested_evaluation_time_utc",
            "expected_aircraft_id",
            "expected_recommendation_id",
        }
        unexpected = set(data) - allowed
        if unexpected:
            raise ContractValidationError("unexpected_authorization_field")
        return cls(
            master_request_id=_required(data, "master_request_id"),
            invocation_id=_required(data, "invocation_id"),
            specialist=_parse_enum(MasterSpecialist, _required(data, "specialist"), "specialist"),
            purpose=_parse_enum(MasterPurpose, _required(data, "purpose"), "purpose"),
            requirement=_parse_enum(
                MasterRequirement,
                _required(data, "requirement"),
                "requirement",
            ),
            specialist_request_id=data.get("specialist_request_id"),
            correlation_id=data.get("correlation_id"),
            application_request_time_utc=data.get("application_request_time_utc"),
            requested_evaluation_time_utc=data.get("requested_evaluation_time_utc"),
            expected_aircraft_id=data.get("expected_aircraft_id"),
            expected_recommendation_id=data.get("expected_recommendation_id"),
            schema_version=_required(data, "schema_version"),
        )


@dataclass(frozen=True)
class MasterInvocationRecord:
    """Serializable classification of one invocation.

    ``reported_shape`` describes the specialist artifact. ``production_authority``
    is always DENIED here. A serialized eligibility flag is not trusted.
    """

    invocation_id: str
    specialist: MasterSpecialist
    purpose: MasterPurpose
    requirement: MasterRequirement
    disposition: MasterDisposition
    verification: MasterVerification
    condition_flags: tuple[MasterCondition, ...]
    reported_shape: MasterReportedShape
    temporal_scopes: tuple[TemporalScope, ...]
    answer: str
    limitation_codes: tuple[str, ...]
    binding_defects: tuple[MasterBindingDefect, ...] = ()
    specialist_request_id: str | None = None
    correlation_id: str | None = None
    artifact_correlation_id: str | None = None
    application_request_time_utc: str | None = None
    requested_evaluation_time_utc: str | None = None
    specialist_reference_time_utc: str | None = None
    evidence_evaluation_time_utc: str | None = None
    source_observation_time_utc: str | None = None
    completeness_notice: str = ""
    failure_code: str | None = None
    production_authority: MasterProductionAuthority = MasterProductionAuthority.DENIED
    schema_version: str = MASTER_INVOCATION_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != MASTER_INVOCATION_SCHEMA_VERSION:
            raise ContractValidationError("invalid_schema_version")
        _deny_production(self.production_authority)
        _token(self.invocation_id, "invocation_id")
        _token(self.specialist_request_id, "specialist_request_id", optional=True)
        _token(self.correlation_id, "correlation_id", optional=True)
        _token(self.artifact_correlation_id, "artifact_correlation_id", optional=True)
        _token(self.failure_code, "failure_code", optional=True)
        _clock(self.application_request_time_utc, "application_request_time_utc")
        _clock(self.requested_evaluation_time_utc, "requested_evaluation_time_utc")
        _clock(self.specialist_reference_time_utc, "specialist_reference_time_utc")
        _clock(self.evidence_evaluation_time_utc, "evidence_evaluation_time_utc")
        _clock(self.source_observation_time_utc, "source_observation_time_utc")
        if not isinstance(self.answer, str) or not self.answer.strip():
            raise ContractValidationError("invalid_answer")
        if not isinstance(self.completeness_notice, str):
            raise ContractValidationError("invalid_completeness_notice")
        for field_name, enum_type in (
            ("specialist", MasterSpecialist),
            ("purpose", MasterPurpose),
            ("requirement", MasterRequirement),
            ("disposition", MasterDisposition),
            ("verification", MasterVerification),
            ("reported_shape", MasterReportedShape),
        ):
            if not isinstance(getattr(self, field_name), enum_type):
                raise ContractValidationError(f"invalid_{field_name}")
        object.__setattr__(
            self,
            "condition_flags",
            tuple(sorted(
                _enum_tuple(self.condition_flags, MasterCondition, "condition_flags"),
                key=lambda item: item.value,
            )),
        )
        object.__setattr__(
            self,
            "binding_defects",
            tuple(sorted(
                _enum_tuple(self.binding_defects, MasterBindingDefect, "binding_defects"),
                key=lambda item: item.value,
            )),
        )
        object.__setattr__(
            self,
            "temporal_scopes",
            _enum_tuple(self.temporal_scopes, TemporalScope, "temporal_scopes"),
        )
        object.__setattr__(
            self,
            "limitation_codes",
            _text_tuple(self.limitation_codes, "limitation_codes"),
        )

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "schema_version": self.schema_version,
            "invocation_id": self.invocation_id,
            "specialist": self.specialist.value,
            "purpose": self.purpose.value,
            "requirement": self.requirement.value,
            "disposition": self.disposition.value,
            "verification": self.verification.value,
            "condition_flags": [item.value for item in self.condition_flags],
            "reported_shape": self.reported_shape.value,
            "temporal_scopes": [item.value for item in self.temporal_scopes],
            "answer": self.answer,
            "limitation_codes": list(self.limitation_codes),
            "binding_defects": [item.value for item in self.binding_defects],
            "specialist_request_id": self.specialist_request_id,
            "correlation_id": self.correlation_id,
            "artifact_correlation_id": self.artifact_correlation_id,
            "application_request_time_utc": self.application_request_time_utc,
            "requested_evaluation_time_utc": self.requested_evaluation_time_utc,
            "specialist_reference_time_utc": self.specialist_reference_time_utc,
            "evidence_evaluation_time_utc": self.evidence_evaluation_time_utc,
            "source_observation_time_utc": self.source_observation_time_utc,
            "completeness_notice": self.completeness_notice,
            "failure_code": self.failure_code,
            "production_authority": MasterProductionAuthority.DENIED.value,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "MasterInvocationRecord":
        if not isinstance(data, Mapping):
            raise ContractValidationError("invalid_master_invocation_record")
        unexpected = set(data) - {
            "schema_version",
            "invocation_id",
            "specialist",
            "purpose",
            "requirement",
            "disposition",
            "verification",
            "condition_flags",
            "reported_shape",
            "temporal_scopes",
            "answer",
            "limitation_codes",
            "binding_defects",
            "specialist_request_id",
            "correlation_id",
            "artifact_correlation_id",
            "application_request_time_utc",
            "requested_evaluation_time_utc",
            "specialist_reference_time_utc",
            "evidence_evaluation_time_utc",
            "source_observation_time_utc",
            "completeness_notice",
            "failure_code",
            "production_authority",
            "reported_eligibility",
            "eligibility",
        }
        if unexpected:
            raise ContractValidationError("unexpected_record_field")
        if "production_authority" in data or "reported_eligibility" in data or "eligibility" in data:
            pass
        flags = tuple(
            _parse_enum(MasterCondition, item, "condition_flags")
            for item in _sequence(_required(data, "condition_flags"), "condition_flags")
        )
        scopes = tuple(
            _parse_enum(TemporalScope, item, "temporal_scopes")
            for item in _sequence(_required(data, "temporal_scopes"), "temporal_scopes")
        )
        defects = tuple(
            _parse_enum(MasterBindingDefect, item, "binding_defects")
            for item in _sequence(data.get("binding_defects", ()), "binding_defects")
        )
        limitations = tuple(_sequence(_required(data, "limitation_codes"), "limitation_codes"))
        return cls(
            invocation_id=_required(data, "invocation_id"),
            specialist=_parse_enum(MasterSpecialist, _required(data, "specialist"), "specialist"),
            purpose=_parse_enum(MasterPurpose, _required(data, "purpose"), "purpose"),
            requirement=_parse_enum(
                MasterRequirement,
                _required(data, "requirement"),
                "requirement",
            ),
            disposition=_parse_enum(
                MasterDisposition,
                _required(data, "disposition"),
                "disposition",
            ),
            verification=_parse_enum(
                MasterVerification,
                _required(data, "verification"),
                "verification",
            ),
            condition_flags=flags,
            reported_shape=_parse_enum(
                MasterReportedShape,
                _required(data, "reported_shape"),
                "reported_shape",
            ),
            temporal_scopes=scopes,
            answer=_required(data, "answer"),
            limitation_codes=limitations,
            binding_defects=defects,
            specialist_request_id=data.get("specialist_request_id"),
            correlation_id=data.get("correlation_id"),
            artifact_correlation_id=data.get("artifact_correlation_id"),
            application_request_time_utc=data.get("application_request_time_utc"),
            requested_evaluation_time_utc=data.get("requested_evaluation_time_utc"),
            specialist_reference_time_utc=data.get("specialist_reference_time_utc"),
            evidence_evaluation_time_utc=data.get("evidence_evaluation_time_utc"),
            source_observation_time_utc=data.get("source_observation_time_utc"),
            completeness_notice=data.get("completeness_notice", ""),
            failure_code=data.get("failure_code"),
            production_authority=MasterProductionAuthority.DENIED,
            schema_version=_required(data, "schema_version"),
        )


@dataclass(frozen=True)
class MasterResultBinding:
    """In-memory classification binding. It has no serializer and no production seal."""

    invocation_id: str
    binding_kind: MasterBindingKind
    production_authority: MasterProductionAuthority = MasterProductionAuthority.DENIED

    def __post_init__(self) -> None:
        _token(self.invocation_id, "invocation_id")
        if not isinstance(self.binding_kind, MasterBindingKind):
            raise ContractValidationError("invalid_binding_kind")
        if self.binding_kind is not MasterBindingKind.UNBOUND and self.binding_kind is not MasterBindingKind.CONTRACT_TEST and self.binding_kind is not MasterBindingKind.DESERIALIZED:
            raise ContractValidationError("invalid_binding_kind")
        _deny_production(self.production_authority)


@dataclass(frozen=True)
class MasterClassification:
    record: MasterInvocationRecord
    binding: MasterResultBinding

    def __post_init__(self) -> None:
        if not isinstance(self.record, MasterInvocationRecord):
            raise ContractValidationError("invalid_record")
        if not isinstance(self.binding, MasterResultBinding):
            raise ContractValidationError("invalid_binding")
        if self.record.invocation_id != self.binding.invocation_id:
            raise ContractValidationError("binding_invocation_mismatch")
        _deny_production(self.record.production_authority)
        _deny_production(self.binding.production_authority)


@dataclass(frozen=True)
class MasterPublicSection:
    """Allowlisted browser projection. It cannot carry production authority."""

    invocation_id: str
    specialist: MasterSpecialist
    disposition: MasterDisposition
    verification: MasterVerification
    condition_flags: tuple[MasterCondition, ...]
    reported_shape: MasterReportedShape
    temporal_scopes: tuple[TemporalScope, ...]
    answer: str
    limitation_codes: tuple[str, ...]
    completeness_notice: str
    binding_defects: tuple[MasterBindingDefect, ...]
    evidence_evaluation_time_utc: str | None
    specialist_reference_time_utc: str | None
    source_observation_time_utc: str | None
    failure_code: str | None
    production_authority: MasterProductionAuthority = MasterProductionAuthority.DENIED
    schema_version: str = MASTER_INVOCATION_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != MASTER_INVOCATION_SCHEMA_VERSION:
            raise ContractValidationError("invalid_schema_version")
        _deny_production(self.production_authority)
        _token(self.invocation_id, "invocation_id")
        if not isinstance(self.answer, str) or not self.answer.strip():
            raise ContractValidationError("invalid_answer")

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "schema_version": self.schema_version,
            "invocation_id": self.invocation_id,
            "specialist": self.specialist.value,
            "disposition": self.disposition.value,
            "verification": self.verification.value,
            "condition_flags": [item.value for item in self.condition_flags],
            "reported_shape": self.reported_shape.value,
            "temporal_scopes": [item.value for item in self.temporal_scopes],
            "answer": self.answer,
            "limitation_codes": list(self.limitation_codes),
            "completeness_notice": self.completeness_notice,
            "binding_defects": [item.value for item in self.binding_defects],
            "evidence_evaluation_time_utc": self.evidence_evaluation_time_utc,
            "specialist_reference_time_utc": self.specialist_reference_time_utc,
            "source_observation_time_utc": self.source_observation_time_utc,
            "failure_code": self.failure_code,
            "production_authority": MasterProductionAuthority.DENIED.value,
        }


_PUBLIC_KEYS = frozenset({
    "schema_version",
    "invocation_id",
    "specialist",
    "disposition",
    "verification",
    "condition_flags",
    "reported_shape",
    "temporal_scopes",
    "answer",
    "limitation_codes",
    "completeness_notice",
    "binding_defects",
    "evidence_evaluation_time_utc",
    "specialist_reference_time_utc",
    "source_observation_time_utc",
    "failure_code",
    "production_authority",
})


def project_public(record: MasterInvocationRecord) -> MasterPublicSection:
    """Copy allowlisted fields. Specialist facts and limitations are not rewritten."""

    if not isinstance(record, MasterInvocationRecord):
        raise TypeError("public projection requires a MasterInvocationRecord")
    section = MasterPublicSection(
        invocation_id=record.invocation_id,
        specialist=record.specialist,
        disposition=record.disposition,
        verification=record.verification,
        condition_flags=record.condition_flags,
        reported_shape=record.reported_shape,
        temporal_scopes=record.temporal_scopes,
        answer=record.answer,
        limitation_codes=record.limitation_codes,
        completeness_notice=record.completeness_notice,
        binding_defects=record.binding_defects,
        evidence_evaluation_time_utc=record.evidence_evaluation_time_utc,
        specialist_reference_time_utc=record.specialist_reference_time_utc,
        source_observation_time_utc=record.source_observation_time_utc,
        failure_code=record.failure_code,
    )
    leaked = set(section.to_dict()) - _PUBLIC_KEYS
    if leaked:
        raise ContractValidationError("public_projection_leak")
    return section


@dataclass(frozen=True)
class MasterInvocationCollection:
    records: tuple[MasterInvocationRecord, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.records, tuple):
            raise ContractValidationError("invalid_records")
        seen: set[str] = set()
        for item in self.records:
            if not isinstance(item, MasterInvocationRecord):
                raise ContractValidationError("invalid_record")
            if item.invocation_id in seen:
                raise ContractValidationError("duplicate_invocation_id")
            seen.add(item.invocation_id)

    def add(self, record: MasterInvocationRecord) -> "MasterInvocationCollection":
        if not isinstance(record, MasterInvocationRecord):
            raise ContractValidationError("invalid_record")
        if any(item.invocation_id == record.invocation_id for item in self.records):
            raise ContractValidationError("duplicate_invocation_id")
        return MasterInvocationCollection(self.records + (record,))


@dataclass(frozen=True)
class MasterCallsignObservation:
    """One callsign discovery. It is not a Decision target."""

    callsign: str
    status: LiveOpsDiscoveryStatus
    aircraft_ids: tuple[str, ...]
    evidence_tool_call_id: str
    reference_epoch: int
    identity_resolved: bool
    limitation_codes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _token(self.callsign, "callsign")
        _token(self.evidence_tool_call_id, "evidence_tool_call_id")
        if not isinstance(self.status, LiveOpsDiscoveryStatus):
            raise ContractValidationError("invalid_discovery_status")
        if type(self.reference_epoch) is not int:
            raise ContractValidationError("invalid_reference_epoch")
        if not isinstance(self.identity_resolved, bool):
            raise ContractValidationError("invalid_identity_resolved")
        object.__setattr__(self, "aircraft_ids", _text_tuple(self.aircraft_ids, "aircraft_ids"))
        object.__setattr__(
            self,
            "limitation_codes",
            _text_tuple(self.limitation_codes, "limitation_codes"),
        )


@dataclass(frozen=True)
class MasterTypedIdObservation:
    """A typed aircraft id. Validation is deferred, so this cannot be marked validated."""

    aircraft_id: str

    def __post_init__(self) -> None:
        _token(self.aircraft_id, "aircraft_id")


@dataclass(frozen=True)
class MasterPopulationObservation:
    """Aircraft ids returned by a regional query. Not Decision targets."""

    aircraft_ids: tuple[str, ...]
    limitation_codes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "aircraft_ids", _text_tuple(self.aircraft_ids, "aircraft_ids"))
        object.__setattr__(
            self,
            "limitation_codes",
            _text_tuple(self.limitation_codes, "limitation_codes"),
        )


def observe_callsign(
    discovery: LiveOpsDiscovery,
    limitation_codes: tuple[str, ...] = (),
) -> MasterCallsignObservation:
    if not isinstance(discovery, LiveOpsDiscovery):
        raise TypeError("callsign observation requires a LiveOpsDiscovery")
    return MasterCallsignObservation(
        callsign=discovery.callsign,
        status=discovery.status,
        aircraft_ids=discovery.aircraft_ids,
        evidence_tool_call_id=discovery.evidence_tool_call_id,
        reference_epoch=discovery.reference_epoch,
        identity_resolved=discovery.identity_resolved,
        limitation_codes=limitation_codes,
    )


def observe_typed_aircraft_id(aircraft_id: str) -> MasterTypedIdObservation:
    return MasterTypedIdObservation(aircraft_id)


def observe_population(
    aircraft_ids: tuple[str, ...],
    limitation_codes: tuple[str, ...] = (),
) -> MasterPopulationObservation:
    return MasterPopulationObservation(aircraft_ids, limitation_codes)


def reserved_production_adapter(*_args: Any, **_kwargs: Any) -> None:
    """Future invocation entry. It does not call specialists or issue authority."""

    raise ContractValidationError("production_authority_not_issued")


def _flags(*items: MasterCondition) -> tuple[MasterCondition, ...]:
    return tuple(sorted(set(items), key=lambda item: item.value))


def _shape(qualifies: bool) -> MasterReportedShape:
    if qualifies:
        return MasterReportedShape.QUALIFIES
    return MasterReportedShape.DOES_NOT_QUALIFY


def _time_defect(
    authorization: MasterInvocationAuthorization,
    evidence_time: str | None,
) -> tuple[MasterBindingDefect, ...]:
    requested = authorization.requested_evaluation_time_utc
    if requested is not None and evidence_time is not None and requested != evidence_time:
        return (MasterBindingDefect.TIME_MISMATCH,)
    return ()


def _correlation_defect(
    authorization: MasterInvocationAuthorization,
    artifact_correlation_id: str | None,
) -> tuple[MasterBindingDefect, ...]:
    if (
        authorization.correlation_id is not None
        and artifact_correlation_id is not None
        and authorization.correlation_id != artifact_correlation_id
    ):
        return (MasterBindingDefect.CORRELATION_MISMATCH,)
    return ()


def _record_from(
    authorization: MasterInvocationAuthorization,
    *,
    disposition: MasterDisposition,
    verification: MasterVerification,
    condition_flags: tuple[MasterCondition, ...],
    qualifies: bool,
    temporal_scopes: tuple[TemporalScope, ...],
    answer: str,
    limitation_codes: tuple[str, ...],
    binding_defects: tuple[MasterBindingDefect, ...] = (),
    artifact_correlation_id: str | None = None,
    specialist_reference_time_utc: str | None = None,
    evidence_evaluation_time_utc: str | None = None,
    completeness_notice: str = "",
    failure_code: str | None = None,
    binding_kind: MasterBindingKind = MasterBindingKind.CONTRACT_TEST,
) -> MasterClassification:
    record = MasterInvocationRecord(
        invocation_id=authorization.invocation_id,
        specialist=authorization.specialist,
        purpose=authorization.purpose,
        requirement=authorization.requirement,
        disposition=disposition,
        verification=verification,
        condition_flags=condition_flags,
        reported_shape=_shape(qualifies and not binding_defects),
        temporal_scopes=temporal_scopes,
        answer=answer,
        limitation_codes=limitation_codes,
        binding_defects=binding_defects,
        specialist_request_id=authorization.specialist_request_id,
        correlation_id=authorization.correlation_id,
        artifact_correlation_id=artifact_correlation_id,
        application_request_time_utc=authorization.application_request_time_utc,
        requested_evaluation_time_utc=authorization.requested_evaluation_time_utc,
        specialist_reference_time_utc=specialist_reference_time_utc,
        evidence_evaluation_time_utc=evidence_evaluation_time_utc,
        source_observation_time_utc=None,
        completeness_notice=completeness_notice,
        failure_code=failure_code,
    )
    return MasterClassification(
        record,
        MasterResultBinding(authorization.invocation_id, binding_kind),
    )


def classify_historical_run(
    authorization: MasterInvocationAuthorization,
    run_result: HistoricalSpecialistRunResult,
) -> MasterClassification:
    """Classify an unfinalized historical run. This does not call the finalizer."""

    if not isinstance(authorization, MasterInvocationAuthorization):
        raise TypeError("authorization is required")
    if authorization.specialist is not MasterSpecialist.HISTORICAL_ANALYTICS:
        raise ContractValidationError("specialist_mismatch")
    if not isinstance(run_result, HistoricalSpecialistRunResult):
        raise TypeError("historical run classification requires HistoricalSpecialistRunResult")
    if run_result.status is SpecialistRunStatus.PROPOSED_CLAIMS:
        answer = "Historical claims were proposed and have not been finalized."
    else:
        answer = "Historical execution has not been finalized."
    return _record_from(
        authorization,
        disposition=MasterDisposition.UNFINALIZED,
        verification=MasterVerification.NOT_RUN,
        condition_flags=_flags(MasterCondition.FRESHNESS_UNKNOWN),
        qualifies=False,
        temporal_scopes=(),
        answer=answer,
        limitation_codes=(),
        evidence_evaluation_time_utc=run_result.evaluated_as_of_utc,
        binding_defects=_time_defect(authorization, run_result.evaluated_as_of_utc),
    )


def classify_historical_result(
    authorization: MasterInvocationAuthorization,
    result: SpecialistResult,
) -> MasterClassification:
    """Classify a finalized historical artifact. PASSED is not production authority."""

    if not isinstance(authorization, MasterInvocationAuthorization):
        raise TypeError("authorization is required")
    if authorization.specialist is not MasterSpecialist.HISTORICAL_ANALYTICS:
        raise ContractValidationError("specialist_mismatch")
    if not isinstance(result, SpecialistResult):
        raise TypeError("historical result classification requires SpecialistResult")
    flags: list[MasterCondition] = [MasterCondition.FRESHNESS_UNKNOWN]
    verified_zero = any(isinstance(item, VerifiedZeroClaim) for item in result.verified_claims)
    if _RESULT_TRUNCATED in result.limitations:
        flags.append(MasterCondition.TRUNCATED)
    if result.status is SpecialistStatus.PARTIAL:
        flags.append(MasterCondition.INCOMPLETE_INVESTIGATION)
        flags.append(MasterCondition.SOURCE_COMPLETENESS_NOT_ESTABLISHED)
    if verified_zero and result.status is SpecialistStatus.ANSWERED:
        flags.append(MasterCondition.VERIFIED_ZERO)
    elif result.verifier_outcome is not VerifierOutcome.PASSED:
        flags.append(MasterCondition.SOURCE_COMPLETENESS_NOT_ESTABLISHED)
    verification = MasterVerification.NOT_RUN
    disposition = _historical_disposition(result.status)
    if result.verifier_outcome is VerifierOutcome.PASSED:
        verification = MasterVerification.PASSED
    elif result.verifier_outcome is VerifierOutcome.FAILED:
        verification = MasterVerification.FAILED
        disposition = MasterDisposition.VERIFICATION_FAILED
    qualifies = (
        result.verifier_outcome is VerifierOutcome.PASSED
        and result.status in {SpecialistStatus.ANSWERED, SpecialistStatus.PARTIAL}
    )
    return _record_from(
        authorization,
        disposition=disposition,
        verification=verification,
        condition_flags=_flags(*flags),
        qualifies=qualifies,
        temporal_scopes=(result.temporal_scope,),
        answer=result.answer,
        limitation_codes=result.limitations,
        evidence_evaluation_time_utc=result.evaluated_as_of_utc,
        binding_defects=_time_defect(authorization, result.evaluated_as_of_utc),
    )


def _historical_disposition(status: SpecialistStatus) -> MasterDisposition:
    mapping = {
        SpecialistStatus.ANSWERED: MasterDisposition.EXECUTED,
        SpecialistStatus.PARTIAL: MasterDisposition.EXECUTED,
        SpecialistStatus.UNSUPPORTED: MasterDisposition.UNSUPPORTED,
        SpecialistStatus.INVALID_REQUEST: MasterDisposition.INVALID_REQUEST,
        SpecialistStatus.UNAVAILABLE: MasterDisposition.UNAVAILABLE,
        SpecialistStatus.PROVIDER_FAILED: MasterDisposition.PROVIDER_FAILED,
    }
    return mapping[status]


def classify_decision_result(
    authorization: MasterInvocationAuthorization,
    result: DecisionSpecialistRunResult,
) -> MasterClassification:
    """Classify a Decision run artifact. COMPLETED is not verification or authority."""

    if not isinstance(authorization, MasterInvocationAuthorization):
        raise TypeError("authorization is required")
    if authorization.specialist is not MasterSpecialist.DECISION:
        raise ContractValidationError("specialist_mismatch")
    if not isinstance(result, DecisionSpecialistRunResult):
        raise TypeError("decision classification requires DecisionSpecialistRunResult")
    defects = list(_target_defects(authorization, result))
    verification = MasterVerification.NOT_RUN
    flags = [MasterCondition.FRESHNESS_UNKNOWN]
    scopes: tuple[TemporalScope, ...] = ()
    limitations: tuple[str, ...] = ()
    answer = "Decision execution did not produce a rendered answer."
    evidence_time = None
    if result.verification is not None:
        scopes = tuple(result.verification.verified_temporal_scopes)
        limitations = result.verification.mandatory_limitations
        evidence_time = result.verification.current_as_of_utc
        if result.verification.outcome is VerifierOutcome.PASSED:
            verification = MasterVerification.PASSED
        elif result.verification.outcome is VerifierOutcome.FAILED:
            verification = MasterVerification.FAILED
    if result.render is not None:
        answer = result.render.answer
    if result.collection_partial_reason is not None or result.status is DecisionSpecialistRunStatus.PARTIAL:
        flags.append(MasterCondition.INCOMPLETE_INVESTIGATION)
        flags.append(MasterCondition.SOURCE_COMPLETENESS_NOT_ESTABLISHED)
    factual = (
        result.render is not None
        and result.render.outcome.value == _FACTUAL
    )
    final_claims = result.terminal_kind is not None and result.terminal_kind.value == _FINAL_CLAIMS
    qualifies = (
        result.status in {
            DecisionSpecialistRunStatus.COMPLETED,
            DecisionSpecialistRunStatus.PARTIAL,
        }
        and verification is MasterVerification.PASSED
        and factual
        and final_claims
        and not result.runtime_errors
        and not defects
    )
    disposition = _decision_disposition(result)
    if verification is MasterVerification.FAILED:
        disposition = MasterDisposition.VERIFICATION_FAILED
    if defects:
        disposition = MasterDisposition.TARGET_UNRESOLVED
        qualifies = False
    defects.extend(_time_defect(authorization, evidence_time))
    if _time_defect(authorization, evidence_time):
        qualifies = False
    failure_code = None
    if result.runtime_errors:
        failure_code = result.runtime_errors[0].value
    return _record_from(
        authorization,
        disposition=disposition,
        verification=verification,
        condition_flags=_flags(*flags),
        qualifies=qualifies,
        temporal_scopes=scopes,
        answer=answer,
        limitation_codes=limitations,
        binding_defects=tuple(defects),
        evidence_evaluation_time_utc=evidence_time,
        failure_code=failure_code,
    )


def _target_defects(
    authorization: MasterInvocationAuthorization,
    result: DecisionSpecialistRunResult,
) -> tuple[MasterBindingDefect, ...]:
    if (
        authorization.expected_aircraft_id is not None
        and authorization.expected_aircraft_id != result.aircraft_id
    ):
        return (MasterBindingDefect.TARGET_MISMATCH,)
    if (
        authorization.expected_recommendation_id is not None
        and authorization.expected_recommendation_id != result.recommendation_id
    ):
        return (MasterBindingDefect.TARGET_MISMATCH,)
    return ()


def _decision_disposition(result: DecisionSpecialistRunResult) -> MasterDisposition:
    mapping = {
        DecisionSpecialistRunStatus.COMPLETED: MasterDisposition.EXECUTED,
        DecisionSpecialistRunStatus.PARTIAL: MasterDisposition.EXECUTED,
        DecisionSpecialistRunStatus.UNSUPPORTED: MasterDisposition.UNSUPPORTED,
        DecisionSpecialistRunStatus.INVALID_REQUEST: MasterDisposition.INVALID_REQUEST,
        DecisionSpecialistRunStatus.UNAVAILABLE: MasterDisposition.UNAVAILABLE,
        DecisionSpecialistRunStatus.PROVIDER_FAILED: MasterDisposition.PROVIDER_FAILED,
    }
    return mapping[result.status]


_LIVE_OPS_DISPOSITION = {
    LiveOpsRunStatus.COMPLETED: MasterDisposition.EXECUTED,
    LiveOpsRunStatus.UNSUPPORTED: MasterDisposition.UNSUPPORTED,
    LiveOpsRunStatus.REFUSED: MasterDisposition.REFUSED,
    LiveOpsRunStatus.INVALID_REQUEST: MasterDisposition.INVALID_REQUEST,
    LiveOpsRunStatus.TOOL_FAILED: MasterDisposition.TOOL_FAILED,
    LiveOpsRunStatus.PROVIDER_FAILED: MasterDisposition.PROVIDER_FAILED,
    LiveOpsRunStatus.PROVIDER_TIMEOUT: MasterDisposition.PROVIDER_TIMEOUT,
    LiveOpsRunStatus.RUNTIME_TIMEOUT: MasterDisposition.RUNTIME_TIMEOUT,
    LiveOpsRunStatus.BUDGET_EXHAUSTED: MasterDisposition.BUDGET_EXHAUSTED,
    LiveOpsRunStatus.CANCELLED: MasterDisposition.CANCELLED,
    LiveOpsRunStatus.UNAVAILABLE: MasterDisposition.UNAVAILABLE,
}


def classify_live_ops_result(
    authorization: MasterInvocationAuthorization,
    request: LiveOpsRunRequest,
    context: LiveOpsTrustedContext,
    result: LiveOpsRunResult,
) -> MasterClassification:
    """Classify a public Live Ops result. ALL_VERIFIED is not production authority."""

    if not isinstance(authorization, MasterInvocationAuthorization):
        raise TypeError("authorization is required")
    if authorization.specialist is not MasterSpecialist.LIVE_OPS:
        raise ContractValidationError("specialist_mismatch")
    if not isinstance(request, LiveOpsRunRequest) or not isinstance(result, LiveOpsRunResult):
        raise TypeError("live ops classification requires the public request and result")
    if not isinstance(context, LiveOpsTrustedContext):
        raise TypeError("live ops classification requires LiveOpsTrustedContext")
    section = present_live_ops_result(result)
    validate_live_ops_presentation(result, section)
    if section.factual_text != result.answer or section.mandatory_limitations != result.mandatory_limitations:
        raise ContractValidationError("presentation_rewrite")
    defects = list(_correlation_defect(authorization, result.correlation_id))
    if context.correlation_id != result.correlation_id:
        defects.append(MasterBindingDefect.CORRELATION_MISMATCH)
    flags = [
        MasterCondition.FRESHNESS_UNKNOWN,
        MasterCondition.SOURCE_COMPLETENESS_NOT_ESTABLISHED,
    ]
    verification = MasterVerification.NOT_RUN
    outcome = result.verification_outcome
    if outcome is LiveOpsPublicVerificationOutcome.ALL_VERIFIED:
        verification = MasterVerification.PASSED
    elif outcome is LiveOpsPublicVerificationOutcome.PARTIALLY_VERIFIED:
        verification = MasterVerification.PASSED
        flags.append(MasterCondition.INCOMPLETE_INVESTIGATION)
    elif outcome is LiveOpsPublicVerificationOutcome.NONE_VERIFIED:
        verification = MasterVerification.NO_VERIFIED_FACTS
    elif outcome is LiveOpsPublicVerificationOutcome.NOT_AUTHORIZED:
        verification = MasterVerification.FAILED
    qualifies = (
        result.execution_status is LiveOpsRunStatus.COMPLETED
        and outcome in {
            LiveOpsPublicVerificationOutcome.ALL_VERIFIED,
            LiveOpsPublicVerificationOutcome.PARTIALLY_VERIFIED,
        }
        and result.answer_origin.value == "DETERMINISTIC_RENDERER"
        and not defects
    )
    failure_code = None
    if result.failure is not None:
        failure_code = result.failure.reason.value
    return _record_from(
        authorization,
        disposition=_LIVE_OPS_DISPOSITION[result.execution_status],
        verification=verification,
        condition_flags=_flags(*flags),
        qualifies=qualifies,
        temporal_scopes=(),
        answer=result.answer,
        limitation_codes=result.mandatory_limitations,
        binding_defects=tuple(defects),
        artifact_correlation_id=result.correlation_id,
        specialist_reference_time_utc=result.reference_utc,
        completeness_notice=result.completeness_notice,
        failure_code=failure_code,
    )


def classify_deserialized(record: MasterInvocationRecord) -> MasterClassification:
    """Reload a record. Any serialized authority flag has already been discarded."""

    if not isinstance(record, MasterInvocationRecord):
        raise TypeError("deserialized classification requires MasterInvocationRecord")
    _deny_production(record.production_authority)
    return MasterClassification(
        record,
        MasterResultBinding(record.invocation_id, MasterBindingKind.DESERIALIZED),
    )
