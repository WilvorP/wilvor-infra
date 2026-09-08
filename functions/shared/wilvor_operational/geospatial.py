"""Compose Phase 1D.1 hazard discovery with region geometry evaluation.

This module pins an exact current hazard identity before geography.
It does not compose encounters, risks, recommendations, alerts, or
impacts, and it does not import Phase 0 AI contracts.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

from . import current_set
from . import discovery
from . import geometry
from . import linking
from . import readers
from . import regions


class RejectionReason(str, Enum):
    PARENT_MISSING = "PARENT_MISSING"
    NOT_CURRENT = "NOT_CURRENT"
    FILTER_MISMATCH = "FILTER_MISMATCH"
    IDENTITY_MISMATCH = "IDENTITY_MISMATCH"


GSI_EVENTUAL_VERSION_DIVERGENCE = (
    "GSI candidate source_version differed from the consistent exact parent; "
    "the exact parent was pinned."
)
MISSING_SOURCE_VERSION = (
    "Exact current parent has no usable source_version; HazardCoordinates "
    "were not queried."
)
FINAL_PARENT_DRIFT = (
    "Final consistent parent read differed from the pinned evaluation identity; "
    "the provisional spatial result was discarded."
)


def _text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _normalize_filter(value: Any) -> str:
    if value is None:
        return ""
    return _text(value).upper()


def _identity(*pairs: tuple[str, str]) -> tuple[tuple[str, str], ...]:
    return tuple((name, value) for name, value in pairs if value)


def _match_value(match: discovery.EntityMatch, name: str) -> str:
    for key, value in match.identity:
        if key == name:
            return value
    return ""


def _filters_match(item: dict[str, Any], *, product_type: str, hazard_type: str) -> bool:
    if product_type and _normalize_filter(item.get("product_type")) != product_type:
        return False
    if hazard_type and _normalize_filter(item.get("hazard_type")) != hazard_type:
        return False
    return True


def _optional_text(value: Any) -> str:
    return _text(value)


@dataclass(frozen=True)
class HazardRegionEvaluation:
    hazard: dict[str, Any] | None
    hazard_id: str
    source_version: str
    spatial_status: geometry.SpatialStatus
    parent_link: linking.Link
    geometry_link: linking.Link
    geometry_hash: str | None
    materialization_id: str | None
    limitations: tuple[str, ...]
    retrieval: tuple[linking.RetrievalObservation, ...]


@dataclass(frozen=True)
class RejectedHazardCandidate:
    hazard_id: str
    discovered_source_version: str
    exact_hazard: dict[str, Any] | None
    reason: RejectionReason
    limitations: tuple[str, ...]
    retrieval: tuple[linking.RetrievalObservation, ...]


@dataclass(frozen=True)
class RegionHazardDiscoveryResult:
    region_resolution: regions.RegionResolution
    intersecting: tuple[HazardRegionEvaluation, ...]
    non_intersecting: tuple[HazardRegionEvaluation, ...]
    unevaluated: tuple[HazardRegionEvaluation, ...]
    rejected_candidates: tuple[RejectedHazardCandidate, ...]
    retrieval: tuple[linking.RetrievalObservation, ...]


def _empty_result(resolution: regions.RegionResolution, *observations):
    return RegionHazardDiscoveryResult(
        region_resolution=resolution,
        intersecting=(),
        non_intersecting=(),
        unevaluated=(),
        rejected_candidates=(),
        retrieval=observations,
    )


def _parent_link(identity: geometry.PinnedHazardIdentity, observed: dict[str, Any] | None) -> linking.Link:
    selected = _identity(
        ("hazard_id", identity.hazard_id),
        ("source_version", identity.source_version),
    )
    if observed is None:
        return linking.Link(
            state=linking.LinkState.HYDRATION_MISSING,
            kind=linking.LinkKind.VERSIONED,
            selected_identity=selected,
        )
    observed_identity = _identity(
        ("hazard_id", _text(observed.get("hazard_id"))),
        ("source_version", _text(observed.get("source_version"))),
    )
    return linking.Link(
        state=linking.LinkState.PRESENT,
        kind=linking.LinkKind.VERSIONED,
        selected_identity=selected,
        observed_identity=observed_identity,
    )


def _drifted(identity: geometry.PinnedHazardIdentity, final: dict[str, Any] | None, now_epoch) -> bool:
    if final is None:
        return True
    if not current_set.is_current_hazard(final, now_epoch):
        return True
    if _text(final.get("hazard_id")) != identity.hazard_id:
        return True
    if _text(final.get("source_version")) != identity.source_version:
        return True
    final_type = _text(final.get("geometry_type")).upper()
    if identity.geometry_type and final_type and final_type != identity.geometry_type:
        return True
    final_hash = _text(final.get("geometry_hash"))
    if identity.geometry_hash and final_hash and final_hash != identity.geometry_hash:
        return True
    final_materialization = _text(final.get("materialization_id"))
    if (
        identity.materialization_id
        and final_materialization
        and final_materialization != identity.materialization_id
    ):
        return True
    return False


def _unevaluated(
    *,
    hazard: dict[str, Any] | None,
    identity: geometry.PinnedHazardIdentity,
    parent_link: linking.Link,
    geometry_link: linking.Link,
    limitations: tuple[str, ...],
    retrieval: tuple[linking.RetrievalObservation, ...],
) -> HazardRegionEvaluation:
    return HazardRegionEvaluation(
        hazard=hazard,
        hazard_id=identity.hazard_id,
        source_version=identity.source_version,
        spatial_status=geometry.SpatialStatus.UNEVALUATED,
        parent_link=parent_link,
        geometry_link=geometry_link,
        geometry_hash=identity.geometry_hash or None,
        materialization_id=identity.materialization_id or None,
        limitations=limitations + (linking.NO_SNAPSHOT_LIMITATION,),
        retrieval=retrieval,
    )


def discover_current_hazards_in_region(
    tables,
    region_query,
    *,
    now_epoch,
    product_type=None,
    hazard_type=None,
    hazard_ids=None,
):
    resolution = regions.resolve_region(region_query)
    if not resolution.resolved or resolution.region is None:
        return _empty_result(resolution)

    requested_product = _normalize_filter(product_type) if product_type is not None else ""
    requested_hazard_type = _normalize_filter(hazard_type) if hazard_type is not None else ""
    discovered = discovery.discover_current_hazards(
        tables,
        now_epoch=now_epoch,
        product_type=product_type,
        hazard_type=hazard_type,
        hazard_ids=hazard_ids,
    )
    retrieval: list[linking.RetrievalObservation] = list(discovered.retrieval)

    region_geometry = None
    region_limitation = None
    if resolution.region.spatial_supported:
        reconstructed_region = geometry.region_to_shapely(resolution.region)
        if reconstructed_region.ok:
            region_geometry = reconstructed_region.geometry
        else:
            region_limitation = reconstructed_region.limitation
    else:
        region_limitation = geometry.REGION_SPATIAL_UNSUPPORTED

    intersecting: list[HazardRegionEvaluation] = []
    non_intersecting: list[HazardRegionEvaluation] = []
    unevaluated: list[HazardRegionEvaluation] = []
    rejected: list[RejectedHazardCandidate] = []

    for match in discovered.matches:
        hazard_id = _match_value(match, "hazard_id") or _text(
            (match.source or {}).get("hazard_id")
        )
        discovered_version = _match_value(match, "source_version")
        parent_observation = linking.exact_pk_observation("get_hazard_record")
        retrieval.append(parent_observation)
        exact = readers.get_hazard_record(tables.hazards, hazard_id)

        if exact is None:
            rejected.append(
                RejectedHazardCandidate(
                    hazard_id=hazard_id,
                    discovered_source_version=discovered_version,
                    exact_hazard=None,
                    reason=RejectionReason.PARENT_MISSING,
                    limitations=(linking.NO_SNAPSHOT_LIMITATION,),
                    retrieval=(parent_observation,),
                )
            )
            continue
        if _text(exact.get("hazard_id")) != hazard_id:
            rejected.append(
                RejectedHazardCandidate(
                    hazard_id=hazard_id,
                    discovered_source_version=discovered_version,
                    exact_hazard=exact,
                    reason=RejectionReason.IDENTITY_MISMATCH,
                    limitations=(linking.NO_SNAPSHOT_LIMITATION,),
                    retrieval=(parent_observation,),
                )
            )
            continue
        if not current_set.is_current_hazard(exact, now_epoch):
            rejected.append(
                RejectedHazardCandidate(
                    hazard_id=hazard_id,
                    discovered_source_version=discovered_version,
                    exact_hazard=exact,
                    reason=RejectionReason.NOT_CURRENT,
                    limitations=(linking.NO_SNAPSHOT_LIMITATION,),
                    retrieval=(parent_observation,),
                )
            )
            continue
        if not _filters_match(
            exact,
            product_type=requested_product,
            hazard_type=requested_hazard_type,
        ):
            rejected.append(
                RejectedHazardCandidate(
                    hazard_id=hazard_id,
                    discovered_source_version=discovered_version,
                    exact_hazard=exact,
                    reason=RejectionReason.FILTER_MISMATCH,
                    limitations=(linking.NO_SNAPSHOT_LIMITATION,),
                    retrieval=(parent_observation,),
                )
            )
            continue

        source_version = _text(exact.get("source_version"))
        identity = geometry.PinnedHazardIdentity(
            hazard_id=hazard_id,
            source_version=source_version,
            geometry_type=_text(exact.get("geometry_type")).upper(),
            geometry_hash=_optional_text(exact.get("geometry_hash")),
            materialization_id=_optional_text(exact.get("materialization_id")),
        )
        parent_link = _parent_link(identity, exact)
        pin_limitations: list[str] = []
        if discovered_version and source_version and discovered_version != source_version:
            pin_limitations.append(GSI_EVENTUAL_VERSION_DIVERGENCE)

        if not source_version:
            unevaluated.append(
                _unevaluated(
                    hazard=exact,
                    identity=identity,
                    parent_link=parent_link,
                    geometry_link=linking.Link(
                        state=linking.LinkState.HYDRATION_MISSING,
                        kind=linking.LinkKind.VERSIONED,
                        reason=MISSING_SOURCE_VERSION,
                        selected_identity=_identity(("hazard_id", hazard_id)),
                    ),
                    limitations=tuple(pin_limitations + [MISSING_SOURCE_VERSION]),
                    retrieval=(parent_observation,),
                )
            )
            continue

        if region_limitation is not None:
            unevaluated.append(
                _unevaluated(
                    hazard=exact,
                    identity=identity,
                    parent_link=parent_link,
                    geometry_link=linking.Link(
                        state=linking.LinkState.HYDRATION_MISSING,
                        kind=linking.LinkKind.VERSIONED,
                        reason=region_limitation,
                        selected_identity=_identity(
                            ("hazard_id", hazard_id),
                            ("source_version", source_version),
                        ),
                    ),
                    limitations=tuple(pin_limitations + [region_limitation]),
                    retrieval=(parent_observation,),
                )
            )
            continue

        coordinate_observation = linking.query_observation(
            "query_hazard_coordinate_rows",
            consistency=linking.Consistency.CONSISTENT,
        )
        retrieval.append(coordinate_observation)
        rows = readers.query_hazard_coordinate_rows(
            tables.hazard_coordinates,
            f"{hazard_id}#{source_version}",
        )
        reconstructed = geometry.reconstruct_hazard_geometry(rows, identity=identity)
        if not reconstructed.ok:
            unevaluated.append(
                _unevaluated(
                    hazard=exact,
                    identity=identity,
                    parent_link=parent_link,
                    geometry_link=linking.Link(
                        state=linking.LinkState.HYDRATION_MISSING,
                        kind=linking.LinkKind.VERSIONED,
                        reason=reconstructed.limitation,
                        selected_identity=_identity(
                            ("hazard_id", hazard_id),
                            ("source_version", source_version),
                        ),
                    ),
                    limitations=tuple(pin_limitations + list(reconstructed.limitations)),
                    retrieval=(parent_observation, coordinate_observation),
                )
            )
            continue

        status = geometry.spatial_intersects(reconstructed.geometry, region_geometry)
        if status is geometry.SpatialStatus.UNEVALUATED:
            unevaluated.append(
                _unevaluated(
                    hazard=exact,
                    identity=identity,
                    parent_link=parent_link,
                    geometry_link=linking.Link(
                        state=linking.LinkState.PRESENT,
                        kind=linking.LinkKind.VERSIONED,
                        selected_identity=_identity(
                            ("hazard_id", hazard_id),
                            ("source_version", source_version),
                        ),
                    ),
                    limitations=tuple(pin_limitations + list(reconstructed.limitations)),
                    retrieval=(parent_observation, coordinate_observation),
                )
            )
            continue

        drift_observation = linking.exact_pk_observation("get_hazard_record")
        retrieval.append(drift_observation)
        final = readers.get_hazard_record(tables.hazards, hazard_id)
        evaluation_retrieval = (
            parent_observation,
            coordinate_observation,
            drift_observation,
        )
        if _drifted(identity, final, now_epoch):
            unevaluated.append(
                _unevaluated(
                    hazard=exact,
                    identity=identity,
                    parent_link=linking.Link(
                        state=linking.LinkState.HYDRATION_VERSION_MISMATCH,
                        kind=linking.LinkKind.VERSIONED,
                        reason=FINAL_PARENT_DRIFT,
                        selected_identity=_identity(
                            ("hazard_id", hazard_id),
                            ("source_version", source_version),
                        ),
                        observed_identity=_identity(
                            ("hazard_id", _text((final or {}).get("hazard_id"))),
                            ("source_version", _text((final or {}).get("source_version"))),
                        ),
                    ),
                    geometry_link=linking.Link(
                        state=linking.LinkState.PRESENT,
                        kind=linking.LinkKind.VERSIONED,
                        selected_identity=_identity(
                            ("hazard_id", hazard_id),
                            ("source_version", source_version),
                        ),
                    ),
                    limitations=tuple(
                        pin_limitations
                        + list(reconstructed.limitations)
                        + [FINAL_PARENT_DRIFT]
                    ),
                    retrieval=evaluation_retrieval,
                )
            )
            continue

        evaluated = HazardRegionEvaluation(
            hazard=exact,
            hazard_id=hazard_id,
            source_version=source_version,
            spatial_status=status,
            parent_link=parent_link,
            geometry_link=linking.Link(
                state=linking.LinkState.PRESENT,
                kind=linking.LinkKind.VERSIONED,
                selected_identity=_identity(
                    ("hazard_id", hazard_id),
                    ("source_version", source_version),
                ),
            ),
            geometry_hash=identity.geometry_hash or None,
            materialization_id=identity.materialization_id or None,
            limitations=tuple(
                pin_limitations
                + list(reconstructed.limitations)
                + [linking.NO_SNAPSHOT_LIMITATION]
            ),
            retrieval=evaluation_retrieval,
        )
        if status is geometry.SpatialStatus.INTERSECTS:
            intersecting.append(evaluated)
        else:
            non_intersecting.append(evaluated)

    return RegionHazardDiscoveryResult(
        region_resolution=resolution,
        intersecting=tuple(intersecting),
        non_intersecting=tuple(non_intersecting),
        unevaluated=tuple(unevaluated),
        rejected_candidates=tuple(rejected),
        retrieval=tuple(retrieval),
    )
