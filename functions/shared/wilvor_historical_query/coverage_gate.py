"""Fail-closed historical coverage gate.

Loads authoritative control evidence, determines epoch ownership for the
requested interval, and calls Phase 2A.1 ``evaluate_collection_window``.
This module does not call Athena or compute VERIFIED_ZERO.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime

from wilvor_historical.contracts import Dataset
from wilvor_historical.coverage import (
    DEFAULT_COLLECTION_EVALUATION_POLICY,
    CollectionEvaluationPolicy,
    collection_epoch_active_intervals,
    covers_window,
    evaluate_collection_window,
    required_streams_for,
    uncovered_slices,
)
from wilvor_historical.coverage_contracts import (
    CollectionActivationRecord,
    CollectionDeactivationRecord,
    Evaluability,
    EvaluabilityResult,
    HistoricalCoverageError,
    OPERATIONAL_DATASETS,
)
from wilvor_historical.query_contracts import (
    COVERAGE_REASON_EPOCH_AMBIGUOUS,
    CoverageEvidence,
)
from wilvor_historical.time import HistoricalTimeError, canonicalize_utc_z, parse_utc_datetime

from .coverage_store import (
    CoverageMetadataSnapshot,
    CoverageStore,
    CoverageStoreError,
    CoverageStoreErrorCode,
)


REASON_STORE_UNAVAILABLE = CoverageStoreErrorCode.COVERAGE_STORE_UNAVAILABLE.value
REASON_METADATA_MALFORMED = CoverageStoreErrorCode.COVERAGE_METADATA_MALFORMED.value
REASON_PAGINATION_MALFORMED = CoverageStoreErrorCode.COVERAGE_PAGINATION_MALFORMED.value
REASON_EVALUATOR_INCONSISTENT = "COVERAGE_EVALUATOR_INCONSISTENT"
REASON_INVALID_REQUEST = CoverageStoreErrorCode.INVALID_REQUEST.value


@dataclass(frozen=True)
class OwnedCoverageSlice:
    collection_epoch_id: str
    start_utc: str
    end_utc: str


@dataclass(frozen=True)
class CoverageSliceEvaluation:
    collection_epoch_id: str
    start_utc: str
    end_utc: str
    dataset: str
    evaluability: Evaluability
    reason: str
    required_horizon_seconds: int
    required_streams: tuple[str, ...]


@dataclass(frozen=True)
class CoverageGateResult:
    allowed_to_query: bool
    coverage: CoverageEvidence
    slice_evaluations: tuple[CoverageSliceEvaluation, ...] = ()

    @property
    def evaluability(self) -> Evaluability | None:
        return self.coverage.evaluability

    @property
    def reason(self) -> str:
        return self.coverage.reason


def _blocked(
    *,
    reason: str,
    datasets: Sequence[str],
    epoch_ids: Sequence[str] = (),
    policy: CollectionEvaluationPolicy,
    slice_evaluations: Sequence[CoverageSliceEvaluation] = (),
    evaluability: Evaluability | None = None,
) -> CoverageGateResult:
    streams = ()
    horizon = 0
    if datasets:
        streams = tuple(item.value for item in required_streams_for(datasets))
        horizon = policy.required_horizon_seconds(datasets)
    return CoverageGateResult(
        allowed_to_query=False,
        coverage=CoverageEvidence(
            evaluability=evaluability,
            reason=reason,
            required_horizon_seconds=horizon,
            required_streams=streams,
            collection_epoch_ids=tuple(epoch_ids),
        ),
        slice_evaluations=tuple(slice_evaluations),
    )


def _normalize_datasets(datasets: Iterable[Dataset | str]) -> tuple[str, ...]:
    ordered: list[str] = []
    seen: set[str] = set()
    for item in datasets:
        value = item.value if isinstance(item, Dataset) else item
        if not isinstance(value, str) or value not in OPERATIONAL_DATASETS:
            raise CoverageStoreError(
                CoverageStoreErrorCode.INVALID_REQUEST,
                "unknown historical dataset",
            )
        if value not in seen:
            ordered.append(value)
            seen.add(value)
    if not ordered:
        raise CoverageStoreError(
            CoverageStoreErrorCode.INVALID_REQUEST,
            "datasets is required",
        )
    return tuple(ordered)


def _require_utc(value: str, field_name: str) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise CoverageStoreError(
            CoverageStoreErrorCode.INVALID_REQUEST,
            f"{field_name} must be canonical UTC Z",
        )
    try:
        return parse_utc_datetime(value)
    except HistoricalTimeError as exc:
        raise CoverageStoreError(
            CoverageStoreErrorCode.INVALID_REQUEST,
            f"invalid {field_name}",
        ) from exc


def _epoch_records(
    snapshot: CoverageMetadataSnapshot,
    epoch_id: str,
) -> tuple[tuple[CollectionActivationRecord, ...], tuple[CollectionDeactivationRecord, ...]]:
    activations = tuple(
        item for item in snapshot.activations if item.collection_epoch_id == epoch_id
    )
    deactivations = tuple(
        item
        for item in snapshot.deactivations
        if item.collection_epoch_id == epoch_id
    )
    return activations, deactivations


def _ownership(
    snapshot: CoverageMetadataSnapshot,
    window_start: datetime,
    window_end: datetime,
) -> tuple[tuple[OwnedCoverageSlice, ...], str | None]:
    epoch_intervals: dict[str, tuple[tuple[datetime, datetime], ...]] = {}
    boundaries = {window_start, window_end}
    for epoch in snapshot.epochs:
        activations, deactivations = _epoch_records(snapshot, epoch.collection_epoch_id)
        intervals = collection_epoch_active_intervals(activations, deactivations)
        epoch_intervals[epoch.collection_epoch_id] = intervals
        for start, end in intervals:
            if window_start < start < window_end:
                boundaries.add(start)
            if window_start < end < window_end:
                boundaries.add(end)
    stamps = sorted(boundaries)
    owned: list[OwnedCoverageSlice] = []
    for left, right in zip(stamps, stamps[1:], strict=False):
        if left >= right:
            continue
        owners = [
            epoch_id
            for epoch_id, intervals in epoch_intervals.items()
            if covers_window(left, right, intervals)
        ]
        if len(owners) > 1:
            return (), COVERAGE_REASON_EPOCH_AMBIGUOUS
        if not owners:
            return (), Evaluability.NOT_ACTIVE.value
        owned.append(
            OwnedCoverageSlice(
                collection_epoch_id=owners[0],
                start_utc=canonicalize_utc_z(left),
                end_utc=canonicalize_utc_z(right),
            )
        )
    return tuple(owned), None


class CoverageGate:
    """Decide whether a historical query window may proceed to Athena."""

    def __init__(
        self,
        *,
        store: CoverageStore,
        policy: CollectionEvaluationPolicy = DEFAULT_COLLECTION_EVALUATION_POLICY,
        evaluator: Callable[..., EvaluabilityResult] = evaluate_collection_window,
    ) -> None:
        if store is None or not isinstance(store, CoverageStore):
            raise CoverageStoreError(
                CoverageStoreErrorCode.INVALID_REQUEST,
                "invalid coverage store",
            )
        self._store = store
        self._policy = policy
        self._evaluator = evaluator

    def evaluate(
        self,
        *,
        datasets: Iterable[Dataset | str],
        start_utc: str,
        end_utc: str,
        as_of_utc: str,
    ) -> CoverageGateResult:
        try:
            requested = _normalize_datasets(datasets)
            start = _require_utc(start_utc, "start_utc")
            end = _require_utc(end_utc, "end_utc")
            as_of = _require_utc(as_of_utc, "as_of_utc")
            if start >= end:
                raise CoverageStoreError(
                    CoverageStoreErrorCode.INVALID_REQUEST,
                    "start_utc must precede end_utc",
                )
        except CoverageStoreError as exc:
            return _blocked(reason=exc.code.value, datasets=(), policy=self._policy)

        try:
            snapshot = self._store.load()
        except CoverageStoreError as exc:
            return _blocked(
                reason=exc.code.value,
                datasets=requested,
                policy=self._policy,
            )

        try:
            return self._evaluate_snapshot(
                snapshot,
                requested=requested,
                start=start,
                end=end,
                as_of_utc=canonicalize_utc_z(as_of),
            )
        except CoverageStoreError as exc:
            return _blocked(
                reason=exc.code.value,
                datasets=requested,
                epoch_ids=tuple(item.collection_epoch_id for item in snapshot.epochs),
                policy=self._policy,
            )
        except HistoricalCoverageError:
            return _blocked(
                reason=REASON_EVALUATOR_INCONSISTENT,
                datasets=requested,
                epoch_ids=tuple(item.collection_epoch_id for item in snapshot.epochs),
                policy=self._policy,
            )

    def _evaluate_snapshot(
        self,
        snapshot: CoverageMetadataSnapshot,
        *,
        requested: tuple[str, ...],
        start: datetime,
        end: datetime,
        as_of_utc: str,
    ) -> CoverageGateResult:
        epoch_ids = tuple(
            sorted({item.collection_epoch_id for item in snapshot.epochs})
        )
        if not epoch_ids:
            return _blocked(
                reason=REASON_STORE_UNAVAILABLE,
                datasets=requested,
                policy=self._policy,
            )
        owned, ownership_reason = _ownership(snapshot, start, end)
        if ownership_reason == COVERAGE_REASON_EPOCH_AMBIGUOUS:
            overlapping = _overlapping_epoch_ids(snapshot, start, end)
            return _blocked(
                reason=COVERAGE_REASON_EPOCH_AMBIGUOUS,
                datasets=requested,
                epoch_ids=overlapping or epoch_ids,
                policy=self._policy,
            )
        if ownership_reason == Evaluability.NOT_ACTIVE.value:
            return _blocked(
                reason=Evaluability.NOT_ACTIVE.value,
                datasets=requested,
                epoch_ids=epoch_ids,
                policy=self._policy,
                evaluability=Evaluability.NOT_ACTIVE,
            )
        slice_evaluations: list[CoverageSliceEvaluation] = []
        for owned_slice in owned:
            for dataset in requested:
                result = self._evaluator(
                    datasets=(dataset,),
                    start_utc=owned_slice.start_utc,
                    end_utc=owned_slice.end_utc,
                    as_of_utc=as_of_utc,
                    activations=snapshot.activations,
                    deactivations=snapshot.deactivations,
                    coverage_intervals=snapshot.coverage_intervals,
                    gap_records=snapshot.gaps,
                    resolutions=snapshot.resolutions,
                    unbound_incidents=snapshot.incidents,
                    policy=self._policy,
                    collection_epoch_id=owned_slice.collection_epoch_id,
                )
                if result.evaluability is Evaluability.NOT_ACTIVE:
                    return _blocked(
                        reason=REASON_EVALUATOR_INCONSISTENT,
                        datasets=requested,
                        epoch_ids=epoch_ids,
                        policy=self._policy,
                    )
                slice_evaluations.append(
                    CoverageSliceEvaluation(
                        collection_epoch_id=owned_slice.collection_epoch_id,
                        start_utc=owned_slice.start_utc,
                        end_utc=owned_slice.end_utc,
                        dataset=dataset,
                        evaluability=result.evaluability,
                        reason=result.reason,
                        required_horizon_seconds=result.required_horizon_seconds,
                        required_streams=tuple(result.required_streams),
                    )
                )
        owned_epochs = tuple(
            dict.fromkeys(item.collection_epoch_id for item in owned)
        )
        return _combine(requested, owned_epochs, slice_evaluations, self._policy)


def _overlapping_epoch_ids(
    snapshot: CoverageMetadataSnapshot,
    window_start: datetime,
    window_end: datetime,
) -> tuple[str, ...]:
    ids: list[str] = []
    for epoch in snapshot.epochs:
        activations, deactivations = _epoch_records(snapshot, epoch.collection_epoch_id)
        intervals = collection_epoch_active_intervals(activations, deactivations)
        if uncovered_slices(window_start, window_end, intervals) != [
            (window_start, window_end)
        ]:
            ids.append(epoch.collection_epoch_id)
    return tuple(sorted(set(ids)))


def _combine(
    requested: tuple[str, ...],
    epoch_ids: tuple[str, ...],
    slice_evaluations: Sequence[CoverageSliceEvaluation],
    policy: CollectionEvaluationPolicy,
) -> CoverageGateResult:
    if any(item.evaluability is Evaluability.GAP_OR_UNCERTAIN for item in slice_evaluations):
        chosen = Evaluability.GAP_OR_UNCERTAIN
    elif any(item.evaluability is Evaluability.NOT_YET_EVALUABLE for item in slice_evaluations):
        chosen = Evaluability.NOT_YET_EVALUABLE
    elif slice_evaluations and all(
        item.evaluability is Evaluability.EVALUABLE for item in slice_evaluations
    ):
        chosen = Evaluability.EVALUABLE
    else:
        return _blocked(
            reason=REASON_EVALUATOR_INCONSISTENT,
            datasets=requested,
            epoch_ids=epoch_ids,
            policy=policy,
            slice_evaluations=slice_evaluations,
        )
    reason = next(
        item.reason for item in slice_evaluations if item.evaluability is chosen
    )
    streams = tuple(item.value for item in required_streams_for(requested))
    return CoverageGateResult(
        allowed_to_query=chosen is Evaluability.EVALUABLE,
        coverage=CoverageEvidence(
            evaluability=chosen,
            reason=reason,
            required_horizon_seconds=policy.required_horizon_seconds(requested),
            required_streams=streams,
            collection_epoch_ids=epoch_ids,
        ),
        slice_evaluations=tuple(slice_evaluations),
    )
