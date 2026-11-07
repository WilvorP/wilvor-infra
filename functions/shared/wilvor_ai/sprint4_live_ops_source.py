"""Sprint 4 Live Ops authenticated source projection.

This module records provenance, freshness, pagination, and geographic
coverage for host-authorized Live Ops reads. It does not call AWS, invent
aircraft/SIGMET intersections, or mint a verified zero from an empty page.

Freshness uses source timestamps when present. Evaluation time is a
separate clock. A region label, including California, is not polygon
overlap. ``MasterSourceAuthenticity`` stays ``NOT_ESTABLISHED``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Mapping

from wilvor_ai.contracts import (
    ContractValidationError,
    Evidence,
    FreshnessStatus,
    QueryExecutionTrace,
    SourceRecord,
    TemporalScope,
    ToolResult,
    ToolResultStatus,
)
from wilvor_ai.master_evidence_admission import MasterSourceAuthenticity
from wilvor_ai.master_read_resource_guard import ReadOperation
from wilvor_ai.sprint4_live_ops_authorization import (
    ADAPTER_ORIGIN,
    APPROVED_SOURCE_ADAPTER,
    EMPTY_PAGE_IS_VERIFIED_ZERO,
    LIVE_AWS_OPERATION_PERMITTED,
    REGION_LABEL_IS_SIGMET_POLYGON,
    LiveOpsReadAuthorization,
)
from wilvor_operational.linking import Coverage, QUERY_PAGE_TRUNCATED
from wilvor_operational.observed import epoch_to_utc_z

_SPATIAL_INTERSECTS = "INTERSECTS"
_SPATIAL_NON_INTERSECTION = "DOES_NOT_INTERSECT"
_SPATIAL_UNEVALUATED = "UNEVALUATED"


SPRINT4_LIVE_OPS_SOURCE_SCHEMA_VERSION = "wilvor.ai.sprint4.live_ops_source.v1"

FRESH_MAX_AGE_SECONDS = 300
ACCEPTABLE_MAX_AGE_SECONDS = 1800
RELATED_RESOURCE_NOT_ALLOWLISTED = "RELATED_RESOURCE_NOT_ALLOWLISTED"
EMPTY_PAGE_NOT_VERIFIED_ZERO = "EMPTY_PAGE_NOT_VERIFIED_ZERO"
REGION_LABEL_NOT_POLYGON = "REGION_LABEL_NOT_POLYGON"
GEOGRAPHIC_PROVENANCE_ABSENT = "GEOGRAPHIC_PROVENANCE_ABSENT"

_SOURCE_TIME_FIELDS = (
    "position_time_epoch",
    "last_contact_epoch",
    "detected_at_epoch",
    "generated_at_epoch",
    "updated_at_epoch",
    "observed_at_epoch",
)
_SOURCE_UTC_FIELDS = (
    "position_time_utc",
    "last_contact_utc",
    "detected_at_utc",
    "generated_at_utc",
    "updated_at_utc",
    "source_observed_at_utc",
    "observed_at_utc",
    "event_timestamp_utc",
)
_PLACE_MARKERS = frozenset({"california", "sigmet", "region", "census"})


class LiveOpsFreshnessVerdict(str, Enum):
    FRESH = "FRESH"
    ACCEPTABLE = "ACCEPTABLE"
    STALE = "STALE"
    UNKNOWN = "UNKNOWN"


class LiveOpsCompletenessVerdict(str, Enum):
    COMPLETE = "COMPLETE"
    PARTIAL = "PARTIAL"
    EMPTY_PAGE = "EMPTY_PAGE"
    NOT_ESTABLISHED = "NOT_ESTABLISHED"


class LiveOpsGeographicCoverage(str, Enum):
    INTERSECTION = "INTERSECTION"
    NON_INTERSECTION = "NON_INTERSECTION"
    NOT_ESTABLISHED = "NOT_ESTABLISHED"


class LiveOpsGeographicBasis(str, Enum):
    COMPUTED_GEOMETRY = "COMPUTED_GEOMETRY"
    NOT_ESTABLISHED = "NOT_ESTABLISHED"


@dataclass(frozen=True)
class LiveOpsReadProvenance:
    resource_id: str
    operation: ReadOperation
    coverage: Coverage
    query_id: str | None
    source_version: str | None
    rows_returned: int
    has_more: bool
    truncated: bool
    adapter_origin: str = ADAPTER_ORIGIN
    live_aws_initiated: bool = False
    approved_source_adapter: bool = False

    def __post_init__(self) -> None:
        if self.live_aws_initiated is not False:
            raise ContractValidationError("live_aws_operation_not_initiated")
        if self.approved_source_adapter is not False:
            raise ContractValidationError("approved_source_adapter_unavailable")
        if self.adapter_origin != ADAPTER_ORIGIN:
            raise ContractValidationError("invalid_adapter_origin")
        if type(self.rows_returned) is not int or self.rows_returned < 0:
            raise ContractValidationError("invalid_rows_returned")
        if self.operation is ReadOperation.SCAN:
            raise ContractValidationError("scan_not_permitted")


@dataclass(frozen=True)
class LiveOpsSourceReport:
    freshness: LiveOpsFreshnessVerdict
    completeness: LiveOpsCompletenessVerdict
    geographic_coverage: LiveOpsGeographicCoverage
    geographic_basis: LiveOpsGeographicBasis
    source_authenticity: MasterSourceAuthenticity
    evaluation_utc: str
    source_observed_at_utc: str | None
    query_timestamp_utc: str
    verified_zero: bool
    region_label_is_polygon: bool
    intersecting_hazard_ids: tuple[str, ...]
    provenance: tuple[LiveOpsReadProvenance, ...]
    limitations: tuple[str, ...]
    query_executions: tuple[QueryExecutionTrace, ...]

    def __post_init__(self) -> None:
        if self.source_authenticity is not MasterSourceAuthenticity.NOT_ESTABLISHED:
            raise ContractValidationError("source_authenticity_not_established")
        if self.verified_zero is not False:
            raise ContractValidationError("empty_page_is_not_verified_zero")
        if self.region_label_is_polygon is not False:
            raise ContractValidationError("region_label_is_not_sigmet_polygon")
        if (
            self.geographic_coverage is LiveOpsGeographicCoverage.INTERSECTION
            and self.geographic_basis is not LiveOpsGeographicBasis.COMPUTED_GEOMETRY
        ):
            raise ContractValidationError("geographic_provenance_absent")
        if LIVE_AWS_OPERATION_PERMITTED is not False:
            raise ContractValidationError("live_aws_operation_not_initiated")
        if APPROVED_SOURCE_ADAPTER is not False:
            raise ContractValidationError("approved_source_adapter_unavailable")
        if EMPTY_PAGE_IS_VERIFIED_ZERO is not False:
            raise ContractValidationError("empty_page_is_not_verified_zero")
        if REGION_LABEL_IS_SIGMET_POLYGON is not False:
            raise ContractValidationError("region_label_is_not_sigmet_polygon")


def _utc(epoch: int) -> str:
    return epoch_to_utc_z(int(epoch))


def _parse_utc(value: object) -> datetime | None:
    if not isinstance(value, str) or len(value) != 20 or not value.endswith("Z"):
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _int_epoch(value: object) -> int | None:
    if type(value) is int and not isinstance(value, bool):
        return value
    return None


def observe_source_freshness(
    records: tuple[Mapping[str, Any], ...],
    *,
    now_epoch: int,
) -> tuple[LiveOpsFreshnessVerdict, str | None]:
    """Freshness from stored source clocks. Evaluation time is not copied."""

    observed_utc: str | None = None
    stale = False
    ages: list[int] = []
    for record in records:
        expires = _int_epoch(record.get("expires_at_epoch"))
        if expires is not None and expires <= now_epoch:
            stale = True
        valid_to = _int_epoch(record.get("valid_to_epoch"))
        if valid_to is not None and valid_to < now_epoch:
            stale = True
        source_epoch = None
        for field in _SOURCE_TIME_FIELDS:
            source_epoch = _int_epoch(record.get(field))
            if source_epoch is not None:
                break
        if source_epoch is not None:
            ages.append(now_epoch - source_epoch)
            if observed_utc is None:
                observed_utc = _utc(source_epoch)
            continue
        for field in _SOURCE_UTC_FIELDS:
            parsed = _parse_utc(record.get(field))
            if parsed is None:
                continue
            ages.append(now_epoch - int(parsed.timestamp()))
            if observed_utc is None:
                observed_utc = parsed.strftime("%Y-%m-%dT%H:%M:%SZ")
            break
    if stale:
        return LiveOpsFreshnessVerdict.STALE, observed_utc
    if not ages:
        return LiveOpsFreshnessVerdict.UNKNOWN, observed_utc
    age = max(ages)
    if age < 0:
        return LiveOpsFreshnessVerdict.UNKNOWN, observed_utc
    if age <= FRESH_MAX_AGE_SECONDS:
        return LiveOpsFreshnessVerdict.FRESH, observed_utc
    if age <= ACCEPTABLE_MAX_AGE_SECONDS:
        return LiveOpsFreshnessVerdict.ACCEPTABLE, observed_utc
    return LiveOpsFreshnessVerdict.STALE, observed_utc


def observe_completeness(
    *,
    rows_returned: int,
    truncated: bool,
    selector_required_missing: bool = False,
) -> LiveOpsCompletenessVerdict:
    if selector_required_missing:
        return LiveOpsCompletenessVerdict.NOT_ESTABLISHED
    if truncated:
        return LiveOpsCompletenessVerdict.PARTIAL
    if rows_returned == 0:
        return LiveOpsCompletenessVerdict.EMPTY_PAGE
    return LiveOpsCompletenessVerdict.COMPLETE


def _region_text_is_not_geometry(value: object) -> bool:
    if not isinstance(value, str):
        return False
    folded = value.casefold()
    return any(marker in folded for marker in _PLACE_MARKERS)


def observe_geographic_coverage(
    *,
    spatial_statuses: tuple[str, ...],
    region_query: str | None,
    coordinates_queried: bool,
    region_resolved: bool,
    spatial_supported: bool,
) -> tuple[LiveOpsGeographicCoverage, LiveOpsGeographicBasis, tuple[str, ...]]:
    """Admit intersection only from computed SpatialStatus.INTERSECTS."""

    limitations: list[str] = []
    if _region_text_is_not_geometry(region_query):
        limitations.append(REGION_LABEL_NOT_POLYGON)
    if not coordinates_queried or not region_resolved or not spatial_supported:
        limitations.append(GEOGRAPHIC_PROVENANCE_ABSENT)
        return (
            LiveOpsGeographicCoverage.NOT_ESTABLISHED,
            LiveOpsGeographicBasis.NOT_ESTABLISHED,
            tuple(dict.fromkeys(limitations)),
        )
    statuses = tuple(item for item in spatial_statuses if item)
    if not statuses:
        limitations.append(GEOGRAPHIC_PROVENANCE_ABSENT)
        return (
            LiveOpsGeographicCoverage.NOT_ESTABLISHED,
            LiveOpsGeographicBasis.NOT_ESTABLISHED,
            tuple(dict.fromkeys(limitations)),
        )
    if _SPATIAL_INTERSECTS in statuses:
        if _SPATIAL_UNEVALUATED in statuses:
            limitations.append(GEOGRAPHIC_PROVENANCE_ABSENT)
        return (
            LiveOpsGeographicCoverage.INTERSECTION,
            LiveOpsGeographicBasis.COMPUTED_GEOMETRY,
            tuple(dict.fromkeys(limitations)),
        )
    if all(item == _SPATIAL_NON_INTERSECTION for item in statuses):
        return (
            LiveOpsGeographicCoverage.NON_INTERSECTION,
            LiveOpsGeographicBasis.COMPUTED_GEOMETRY,
            tuple(dict.fromkeys(limitations)),
        )
    limitations.append(GEOGRAPHIC_PROVENANCE_ABSENT)
    return (
        LiveOpsGeographicCoverage.NOT_ESTABLISHED,
        LiveOpsGeographicBasis.NOT_ESTABLISHED,
        tuple(dict.fromkeys(limitations)),
    )


def provenance_for_page(
    *,
    resource_id: str,
    operation: ReadOperation,
    coverage: Coverage,
    rows_returned: int,
    truncated: bool,
    query_id: str | None = None,
    source_version: str | None = None,
) -> LiveOpsReadProvenance:
    return LiveOpsReadProvenance(
        resource_id=resource_id,
        operation=operation,
        coverage=coverage,
        query_id=query_id,
        source_version=source_version,
        rows_returned=rows_returned,
        has_more=truncated,
        truncated=truncated,
    )


def _query_id(resource_id: str, tool_call_id: str, index: int) -> str:
    return f"live-ops-{resource_id}-{tool_call_id}-{index}"


def build_live_ops_source_report(
    authorization: LiveOpsReadAuthorization,
    *,
    now_epoch: int,
    records: tuple[Mapping[str, Any], ...],
    provenance: tuple[LiveOpsReadProvenance, ...],
    spatial_statuses: tuple[str, ...] = (),
    intersecting_hazard_ids: tuple[str, ...] = (),
    region_query: str | None = None,
    coordinates_queried: bool = False,
    region_resolved: bool = False,
    spatial_supported: bool = False,
    selector_required_missing: bool = False,
    extra_limitations: tuple[str, ...] = (),
) -> LiveOpsSourceReport:
    if type(authorization) is not LiveOpsReadAuthorization:
        raise ContractValidationError("invalid_live_ops_authorization")
    evaluation_utc = _utc(now_epoch)
    freshness, source_observed = observe_source_freshness(records, now_epoch=now_epoch)
    rows_returned = sum(item.rows_returned for item in provenance)
    truncated = any(item.truncated for item in provenance)
    completeness = observe_completeness(
        rows_returned=rows_returned,
        truncated=truncated,
        selector_required_missing=selector_required_missing,
    )
    coverage, basis, geo_limitations = observe_geographic_coverage(
        spatial_statuses=spatial_statuses,
        region_query=region_query,
        coordinates_queried=coordinates_queried,
        region_resolved=region_resolved,
        spatial_supported=spatial_supported,
    )
    limitations = list(extra_limitations)
    limitations.extend(geo_limitations)
    if completeness is LiveOpsCompletenessVerdict.EMPTY_PAGE:
        limitations.append(EMPTY_PAGE_NOT_VERIFIED_ZERO)
    if truncated:
        limitations.append(QUERY_PAGE_TRUNCATED)
    traces: list[QueryExecutionTrace] = []
    for index, item in enumerate(provenance):
        if item.operation is ReadOperation.QUERY:
            traces.append(
                QueryExecutionTrace(
                    query_id=item.query_id
                    or _query_id(item.resource_id, authorization.binding.correlation_id, index),
                    rows_returned=item.rows_returned,
                    engine_scope=item.resource_id,
                )
            )
    return LiveOpsSourceReport(
        freshness=freshness,
        completeness=completeness,
        geographic_coverage=coverage,
        geographic_basis=basis,
        source_authenticity=MasterSourceAuthenticity.NOT_ESTABLISHED,
        evaluation_utc=evaluation_utc,
        source_observed_at_utc=source_observed,
        query_timestamp_utc=evaluation_utc,
        verified_zero=False,
        region_label_is_polygon=False,
        intersecting_hazard_ids=(
            intersecting_hazard_ids
            if coverage is LiveOpsGeographicCoverage.INTERSECTION
            else ()
        ),
        provenance=provenance,
        limitations=tuple(dict.fromkeys(limitations)),
        query_executions=tuple(traces),
    )


def refused_live_ops_source_report(
    *,
    now_epoch: int,
    extra_limitations: tuple[str, ...],
) -> LiveOpsSourceReport:
    evaluation_utc = _utc(now_epoch)
    return LiveOpsSourceReport(
        freshness=LiveOpsFreshnessVerdict.UNKNOWN,
        completeness=LiveOpsCompletenessVerdict.NOT_ESTABLISHED,
        geographic_coverage=LiveOpsGeographicCoverage.NOT_ESTABLISHED,
        geographic_basis=LiveOpsGeographicBasis.NOT_ESTABLISHED,
        source_authenticity=MasterSourceAuthenticity.NOT_ESTABLISHED,
        evaluation_utc=evaluation_utc,
        source_observed_at_utc=None,
        query_timestamp_utc=evaluation_utc,
        verified_zero=False,
        region_label_is_polygon=False,
        intersecting_hazard_ids=(),
        provenance=(),
        limitations=tuple(dict.fromkeys(extra_limitations)),
        query_executions=(),
    )


def overlay_tool_result_source(
    result: ToolResult,
    report: LiveOpsSourceReport,
) -> ToolResult:
    """Copy mapped ToolResult and attach observed freshness plus query traces.

    Empty pages stay empty. Freshness overlay does not mint verified-zero
    and does not change ``MasterSourceAuthenticity``.
    """

    if not isinstance(result, ToolResult):
        raise ContractValidationError("invalid_tool_result")
    mapped_freshness = {
        LiveOpsFreshnessVerdict.FRESH: FreshnessStatus.FRESH,
        LiveOpsFreshnessVerdict.ACCEPTABLE: FreshnessStatus.ACCEPTABLE,
        LiveOpsFreshnessVerdict.STALE: FreshnessStatus.STALE,
        LiveOpsFreshnessVerdict.UNKNOWN: FreshnessStatus.UNKNOWN,
    }[report.freshness]
    evidence: list[Evidence] = []
    for item in result.evidence:
        limitations = list(item.limitations)
        if mapped_freshness is not FreshnessStatus.UNKNOWN:
            limitations = [code for code in limitations if code != "FRESHNESS_NOT_ESTABLISHED"]
        for code in report.limitations:
            if code not in limitations:
                limitations.append(code)
        records = item.source_records
        if report.source_observed_at_utc:
            records = tuple(
                SourceRecord(
                    record_id=record.record_id,
                    source_version=record.source_version,
                    event_timestamp_utc=record.event_timestamp_utc
                    or report.source_observed_at_utc,
                )
                for record in records
            )
        evidence.append(
            Evidence(
                source=item.source,
                source_records=records,
                query_timestamp_utc=item.query_timestamp_utc,
                freshness_status=mapped_freshness,
                confidence=item.confidence,
                limitations=tuple(limitations),
                tool_call_id=item.tool_call_id,
                temporal_scope=TemporalScope.CURRENT,
                completeness=item.completeness,
                match_cardinality=item.match_cardinality,
                query_executions=item.query_executions or report.query_executions,
                error_code=item.error_code,
            )
        )
    status = result.status
    if report.freshness is LiveOpsFreshnessVerdict.STALE and status is ToolResultStatus.SUCCESS:
        status = ToolResultStatus.STALE
    limitations = list(result.limitations)
    if mapped_freshness is not FreshnessStatus.UNKNOWN:
        limitations = [code for code in limitations if code != "FRESHNESS_NOT_ESTABLISHED"]
    for code in report.limitations:
        if code not in limitations:
            limitations.append(code)
    return ToolResult(
        tool_name=result.tool_name,
        tool_call_id=result.tool_call_id,
        status=status,
        temporal_scope=TemporalScope.CURRENT,
        data=result.data,
        evidence=tuple(evidence),
        as_of_utc=result.as_of_utc,
        limitations=tuple(limitations),
        correlation_id=result.correlation_id,
    )


__all__ = [
    "EMPTY_PAGE_NOT_VERIFIED_ZERO",
    "FRESH_MAX_AGE_SECONDS",
    "GEOGRAPHIC_PROVENANCE_ABSENT",
    "LiveOpsCompletenessVerdict",
    "LiveOpsFreshnessVerdict",
    "LiveOpsGeographicBasis",
    "LiveOpsGeographicCoverage",
    "LiveOpsReadProvenance",
    "LiveOpsSourceReport",
    "REGION_LABEL_NOT_POLYGON",
    "RELATED_RESOURCE_NOT_ALLOWLISTED",
    "SPRINT4_LIVE_OPS_SOURCE_SCHEMA_VERSION",
    "build_live_ops_source_report",
    "observe_completeness",
    "observe_geographic_coverage",
    "observe_source_freshness",
    "overlay_tool_result_source",
    "provenance_for_page",
    "refused_live_ops_source_report",
]
