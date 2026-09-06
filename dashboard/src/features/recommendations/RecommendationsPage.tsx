import { useCallback, useEffect, useMemo, useState } from 'react';
import { useSearchParams } from 'react-router-dom';

import { describeApiError } from '@/api/errors';
import { Notice } from '@/components/QueryState';
import { OperationsMap } from '@/features/map/OperationsMap';
import { useAircraftLayerData } from '@/features/map/useAircraftLayerData';
import {
  useActiveHazards,
  useAircraftDetail,
  useOverview,
} from '@/hooks/useOperationalQueries';
import type { ActiveHazard, Recommendation } from '@/types/api';
import { asString } from '@/utils/coerce';

import { AircraftRecommendationChooser } from './AircraftRecommendationChooser';
import { RecommendationKpis } from './RecommendationKpis';
import { RecommendationWorklist } from './RecommendationWorklist';
import { SelectedRecommendationStrip } from './SelectedRecommendationStrip';
import {
  EMPTY_RECOMMENDATION_FILTERS,
  countLoadedActions,
  filterCurrentRecommendations,
  loadedRecommendationAircraftIds,
  loadedRecommendationHazardIds,
  loadedRecommendationsForAircraft,
  matchesRecommendationActionFilter,
  pickRecommendationForHazard,
  recommendationAircraftId,
  recommendationHazardId,
  recommendationIdOf,
  recordSeenRecommendationIds,
  resolveMapAircraftRecommendationClick,
  resolveRecommendationSelection,
  sortCurrentRecommendations,
  withRecommendationAction,
  type RecommendationActionKpi,
  type RecommendationFilters,
} from './recommendationList';
import styles from './RecommendationsPage.module.css';

export interface RecommendationsPageProps {
  mapStyleUrl: string | null;
}

/**
 * Dedicated current-recommendation explorer.
 *
 * Membership comes only from `GET /recommendations/active`. The network
 * total comes from `overview.recommendations.currentCount`. Aircraft detail
 * is fetched for the selected recommendation only.
 */
export function RecommendationsPage({ mapStyleUrl }: RecommendationsPageProps) {
  const [searchParams, setSearchParams] = useSearchParams();
  const selectedRecommendationId = asString(searchParams.get('recommendationId'));
  const [loaded, setLoaded] = useState<readonly Recommendation[]>([]);
  const [seenIds, setSeenIds] = useState<ReadonlySet<string>>(
    () => new Set<string>(),
  );
  const [chooserAircraftId, setChooserAircraftId] = useState<string | null>(
    null,
  );
  const [filters, setFilters] = useState<RecommendationFilters>(
    EMPTY_RECOMMENDATION_FILTERS,
  );

  const overview = useOverview();
  const hazards = useActiveHazards();
  const aircraft = useAircraftLayerData();

  const handleLoadedItems = useCallback((items: readonly Recommendation[]) => {
    setLoaded(items);
    setSeenIds((current) => {
      const next = new Set(current);
      const before = next.size;
      recordSeenRecommendationIds(next, items);
      return next.size === before ? current : next;
    });
  }, []);

  const selection = useMemo(
    () =>
      resolveRecommendationSelection(selectedRecommendationId, loaded, seenIds),
    [loaded, seenIds, selectedRecommendationId],
  );

  const currentItem = selection.status === 'current' ? selection.item : null;
  const filtered = useMemo(
    () =>
      filterCurrentRecommendations(loaded, filters, aircraft.result.aircraftById),
    [aircraft.result.aircraftById, filters, loaded],
  );
  const selectedAircraftId =
    chooserAircraftId ??
    (currentItem ? recommendationAircraftId(currentItem) : null);
  const selectedHazardId = currentItem
    ? recommendationHazardId(currentItem)
    : null;
  const chooserItems = useMemo(
    () =>
      chooserAircraftId === null
        ? []
        : loadedRecommendationsForAircraft(filtered, chooserAircraftId),
    [chooserAircraftId, filtered],
  );

  const detailAircraftId = currentItem
    ? recommendationAircraftId(currentItem)
    : null;
  const detail = useAircraftDetail(detailAircraftId);
  const selectedMapAircraft =
    selectedAircraftId === null
      ? null
      : (aircraft.result.aircraftById.get(selectedAircraftId) ?? null);

  const visibleAircraftIds = useMemo(
    () => loadedRecommendationAircraftIds(filtered),
    [filtered],
  );
  const loadedHazardIds = useMemo(
    () => loadedRecommendationHazardIds(filtered),
    [filtered],
  );
  const chooserHazardIds = useMemo(
    () => loadedRecommendationHazardIds(chooserItems),
    [chooserItems],
  );
  const emphasizedHazardIds = selectedHazardId
    ? [selectedHazardId]
    : chooserAircraftId !== null
      ? chooserHazardIds
      : loadedHazardIds;

  const rankedLoaded = useMemo(
    () => sortCurrentRecommendations(filtered, 'newest'),
    [filtered],
  );
  const loadedActions = useMemo(() => countLoadedActions(loaded), [loaded]);

  const activeHazardIds = useMemo(() => {
    return new Set(
      (hazards.data?.items ?? [])
        .map((hazard) => asString(hazard.hazard_id))
        .filter((id): id is string => id !== null),
    );
  }, [hazards.data?.items]);

  const hasOverview = overview.data !== undefined;
  const overviewFailed = overview.isError && !hasOverview;
  const overviewStale = overview.isError && hasOverview;

  const selectRecommendation = useCallback(
    (recommendationId: string) => {
      setChooserAircraftId(null);
      setSearchParams({ recommendationId }, { replace: true });
    },
    [setSearchParams],
  );

  const clearSelection = useCallback(() => {
    setChooserAircraftId(null);
    setSearchParams({}, { replace: true });
  }, [setSearchParams]);

  const applyFilters = useCallback(
    (next: RecommendationFilters) => {
      const actionChanged = next.action !== filters.action;

      setFilters(next);

      if (
        actionChanged &&
        currentItem &&
        !matchesRecommendationActionFilter(currentItem, next.action)
      ) {
        clearSelection();
      }
    },
    [clearSelection, currentItem, filters.action],
  );

  const handleActionFilterChange = useCallback(
    (action: RecommendationActionKpi) => {
      applyFilters(withRecommendationAction(filters, action));
    },
    [applyFilters, filters],
  );

  const handleMapAircraft = useCallback(
    (aircraftId: string) => {
      const choice = resolveMapAircraftRecommendationClick(filtered, aircraftId);

      if (choice.kind === 'single') {
        const recommendationId = recommendationIdOf(choice.item);

        if (recommendationId !== null) {
          selectRecommendation(recommendationId);
        }

        return;
      }

      if (choice.kind === 'multiple') {
        const currentBelongsToAircraft = choice.items.some(
          (item) => recommendationIdOf(item) === selectedRecommendationId,
        );

        setChooserAircraftId(aircraftId);

        if (!currentBelongsToAircraft) {
          setSearchParams({}, { replace: true });
        }
      }
    },
    [filtered, selectRecommendation, selectedRecommendationId, setSearchParams],
  );

  useEffect(() => {
    if (chooserAircraftId === null) {
      return;
    }

    const remaining = resolveMapAircraftRecommendationClick(
      filtered,
      chooserAircraftId,
    );

    if (remaining.kind === 'none') {
      setChooserAircraftId(null);
      return;
    }

    if (remaining.kind === 'single') {
      const recommendationId = recommendationIdOf(remaining.item);

      if (recommendationId !== null) {
        selectRecommendation(recommendationId);
      }
    }
  }, [chooserAircraftId, filtered, selectRecommendation]);

  const handleMapHazard = useCallback(
    (hazard: ActiveHazard | null) => {
      const hazardId = asString(hazard?.hazard_id);

      if (hazardId === null) {
        return;
      }

      const match = pickRecommendationForHazard(
        rankedLoaded,
        hazardId,
        selectedRecommendationId,
      );
      const recommendationId = match ? recommendationIdOf(match) : null;

      if (recommendationId !== null) {
        selectRecommendation(recommendationId);
      }
    },
    [rankedLoaded, selectRecommendation, selectedRecommendationId],
  );

  const stripPresence =
    selection.status === 'resolved'
      ? 'resolved'
      : selection.status === 'unloaded'
        ? 'unloaded'
        : selectedHazardId !== null &&
            hazards.data !== undefined &&
            !activeHazardIds.has(selectedHazardId)
          ? 'missing-hazard'
          : 'current';

  return (
    <div className={styles.page}>
      {overviewFailed ? (
        <Notice tone="warning">
          Overview counts are unavailable. {describeApiError(overview.error)} The
          worklist still reads `GET /recommendations/active`.
        </Notice>
      ) : null}

      {overviewStale ? (
        <Notice tone="warning">
          Showing the last successful overview counts. The most recent update
          failed: {describeApiError(overview.error)}
        </Notice>
      ) : null}

      <header className={styles.intro}>
        <h1 className={styles.title}>Current Recommendations</h1>
        <p className={styles.lede}>
          Advisory operational decision support. This list is the backend
          current set, not retained ACTIVE recommendation history.
        </p>
      </header>

      <RecommendationKpis
        data={overview.data}
        loadedActions={loadedActions}
        actionFilter={filters.action}
        onActionFilterChange={handleActionFilterChange}
        problem={
          overviewFailed ? describeApiError(overview.error) : undefined
        }
        stale={overviewStale}
      />

      <div className={styles.main}>
        <div className={styles.mapColumn}>
          <OperationsMap
            styleUrl={mapStyleUrl}
            selectedHazardId={selectedHazardId}
            onSelectHazard={handleMapHazard}
            selectedAircraftId={selectedAircraftId}
            onSelectAircraft={handleMapAircraft}
            visibleAircraftIds={visibleAircraftIds}
            emphasizedHazardIds={emphasizedHazardIds}
            projectionPoints={
              selection.status === 'current'
                ? (detail.data?.projectionPoints ?? null)
                : null
            }
          />
        </div>

        <aside className={styles.rail} aria-label="Recommendation worklist">
          <RecommendationWorklist
            selectedRecommendationId={selectedRecommendationId}
            onSelect={selectRecommendation}
            filters={filters}
            onFiltersChange={applyFilters}
            onLoadedItems={handleLoadedItems}
          />
        </aside>
      </div>

      {chooserAircraftId !== null && chooserItems.length > 1 ? (
        <AircraftRecommendationChooser
          aircraftId={chooserAircraftId}
          callsign={selectedMapAircraft?.callsign ?? null}
          items={chooserItems}
          selectedRecommendationId={selectedRecommendationId}
          onSelect={selectRecommendation}
          onDismiss={() => setChooserAircraftId(null)}
        />
      ) : null}

      {selection.status !== 'none' ? (
        <SelectedRecommendationStrip
          recommendationId={
            selection.status === 'current'
              ? (asString(currentItem?.recommendation_id) ??
                selectedRecommendationId ??
                '')
              : selection.recommendationId
          }
          item={currentItem}
          callsign={selectedMapAircraft?.callsign ?? null}
          presence={stripPresence}
          detail={detail.data ?? null}
          detailFailed={detail.isError && detail.data === undefined}
          mapAircraftMissing={
            selectedAircraftId !== null &&
            selectedMapAircraft === null &&
            !aircraft.isPending
          }
          onClear={clearSelection}
        />
      ) : null}
    </div>
  );
}
