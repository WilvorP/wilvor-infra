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
import type { ActiveEncounterItem, ActiveHazard } from '@/types/api';
import { asString } from '@/utils/coerce';

import { AircraftEncounterChooser } from './AircraftEncounterChooser';
import { EncounterKpis } from './EncounterKpis';
import { EncounterWorklist } from './EncounterWorklist';
import { SelectedEncounterStrip } from './SelectedEncounterStrip';
import {
  EMPTY_ENCOUNTER_FILTERS,
  encounterAircraftId,
  encounterHazardId,
  encounterIdOf,
  filterCurrentEncounters,
  loadedEncounterAircraftIds,
  loadedEncounterHazardIds,
  loadedEncountersForAircraft,
  matchesEncounterRiskFilter,
  pickEncounterForHazard,
  recordSeenEncounterIds,
  resolveEncounterSelection,
  resolveMapAircraftClick,
  sortCurrentEncounters,
  withEncounterRisk,
  type EncounterFilters,
  type EncounterRiskKpi,
} from './encounterList';
import styles from './EncountersPage.module.css';

export interface EncountersPageProps {
  mapStyleUrl: string | null;
}

/**
 * Dedicated current-encounter explorer.
 *
 * Membership comes only from `GET /encounters/active`. Totals come from
 * `/overview`. Aircraft detail is fetched for the selected encounter only.
 */
export function EncountersPage({ mapStyleUrl }: EncountersPageProps) {
  const [searchParams, setSearchParams] = useSearchParams();
  const selectedEncounterId = asString(searchParams.get('encounterId'));
  const [loaded, setLoaded] = useState<readonly ActiveEncounterItem[]>([]);
  const [seenIds, setSeenIds] = useState<ReadonlySet<string>>(
    () => new Set<string>(),
  );
  const [chooserAircraftId, setChooserAircraftId] = useState<string | null>(
    null,
  );
  const [filters, setFilters] = useState<EncounterFilters>(EMPTY_ENCOUNTER_FILTERS);

  const overview = useOverview();
  const hazards = useActiveHazards();
  const aircraft = useAircraftLayerData();

  const handleLoadedItems = useCallback((items: readonly ActiveEncounterItem[]) => {
    setLoaded(items);
    setSeenIds((current) => {
      const next = new Set(current);
      const before = next.size;
      recordSeenEncounterIds(next, items);
      return next.size === before ? current : next;
    });
  }, []);

  const selection = useMemo(
    () => resolveEncounterSelection(selectedEncounterId, loaded, seenIds),
    [loaded, seenIds, selectedEncounterId],
  );

  const currentItem =
    selection.status === 'current' ? selection.item : null;
  const filtered = useMemo(
    () =>
      filterCurrentEncounters(loaded, filters, aircraft.result.aircraftById),
    [aircraft.result.aircraftById, filters, loaded],
  );
  const selectedAircraftId =
    chooserAircraftId ??
    (currentItem ? encounterAircraftId(currentItem) : null);
  const selectedHazardId = currentItem ? encounterHazardId(currentItem) : null;
  const chooserItems = useMemo(
    () =>
      chooserAircraftId === null
        ? []
        : loadedEncountersForAircraft(filtered, chooserAircraftId),
    [chooserAircraftId, filtered],
  );

  const detailAircraftId = currentItem
    ? encounterAircraftId(currentItem)
    : null;
  const detail = useAircraftDetail(detailAircraftId);
  const selectedMapAircraft =
    selectedAircraftId === null
      ? null
      : (aircraft.result.aircraftById.get(selectedAircraftId) ?? null);

  const visibleAircraftIds = useMemo(
    () => loadedEncounterAircraftIds(filtered),
    [filtered],
  );
  const loadedHazardIds = useMemo(
    () => loadedEncounterHazardIds(filtered),
    [filtered],
  );
  const chooserHazardIds = useMemo(
    () => loadedEncounterHazardIds(chooserItems),
    [chooserItems],
  );
  const emphasizedHazardIds = selectedHazardId
    ? [selectedHazardId]
    : chooserAircraftId !== null
      ? chooserHazardIds
      : loadedHazardIds;

  const rankedLoaded = useMemo(
    () => sortCurrentEncounters(filtered, 'attention'),
    [filtered],
  );

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

  const selectEncounter = useCallback(
    (encounterId: string) => {
      setChooserAircraftId(null);
      setSearchParams({ encounterId }, { replace: true });
    },
    [setSearchParams],
  );

  const clearSelection = useCallback(() => {
    setChooserAircraftId(null);
    setSearchParams({}, { replace: true });
  }, [setSearchParams]);

  const applyFilters = useCallback(
    (next: EncounterFilters) => {
      const riskChanged = next.riskLevel !== filters.riskLevel;

      setFilters(next);

      if (
        riskChanged &&
        currentItem &&
        !matchesEncounterRiskFilter(currentItem, next.riskLevel)
      ) {
        clearSelection();
      }
    },
    [clearSelection, currentItem, filters.riskLevel],
  );

  const handleRiskFilterChange = useCallback(
    (riskLevel: EncounterRiskKpi) => {
      applyFilters(withEncounterRisk(filters, riskLevel));
    },
    [applyFilters, filters],
  );

  const handleMapAircraft = useCallback(
    (aircraftId: string) => {
      const choice = resolveMapAircraftClick(filtered, aircraftId);

      if (choice.kind === 'single') {
        const encounterId = encounterIdOf(choice.item);

        if (encounterId !== null) {
          selectEncounter(encounterId);
        }

        return;
      }

      if (choice.kind === 'multiple') {
        const currentBelongsToAircraft = choice.items.some(
          (item) => encounterIdOf(item) === selectedEncounterId,
        );

        setChooserAircraftId(aircraftId);

        if (!currentBelongsToAircraft) {
          setSearchParams({}, { replace: true });
        }
      }
    },
    [filtered, selectEncounter, selectedEncounterId, setSearchParams],
  );

  useEffect(() => {
    if (chooserAircraftId === null) {
      return;
    }

    const remaining = resolveMapAircraftClick(filtered, chooserAircraftId);

    if (remaining.kind === 'none') {
      setChooserAircraftId(null);
      return;
    }

    if (remaining.kind === 'single') {
      const encounterId = encounterIdOf(remaining.item);

      if (encounterId !== null) {
        selectEncounter(encounterId);
      }
    }
  }, [chooserAircraftId, filtered, selectEncounter]);

  const handleMapHazard = useCallback(
    (hazard: ActiveHazard | null) => {
      const hazardId = asString(hazard?.hazard_id);

      if (hazardId === null) {
        return;
      }

      const match = pickEncounterForHazard(
        rankedLoaded,
        hazardId,
        selectedEncounterId,
      );
      const encounterId = match ? asString(match.encounter?.encounter_id) : null;

      if (encounterId !== null) {
        selectEncounter(encounterId);
      }
    },
    [rankedLoaded, selectEncounter, selectedEncounterId],
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
          worklist still reads `GET /encounters/active`.
        </Notice>
      ) : null}

      {overviewStale ? (
        <Notice tone="warning">
          Showing the last successful overview counts. The most recent update
          failed: {describeApiError(overview.error)}
        </Notice>
      ) : null}

      <header className={styles.intro}>
        <h1 className={styles.title}>Current Encounters</h1>
        <p className={styles.lede}>
          Aircraft-hazard interactions requiring operational awareness. This
          list is the backend current set, not retained encounter history.
        </p>
      </header>

      <EncounterKpis
        data={overview.data}
        riskFilter={filters.riskLevel}
        onRiskFilterChange={handleRiskFilterChange}
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

        <aside className={styles.rail} aria-label="Encounter worklist">
          <EncounterWorklist
            selectedEncounterId={selectedEncounterId}
            onSelect={selectEncounter}
            filters={filters}
            onFiltersChange={applyFilters}
            onLoadedItems={handleLoadedItems}
          />
        </aside>
      </div>

      {chooserAircraftId !== null && chooserItems.length > 1 ? (
        <AircraftEncounterChooser
          aircraftId={chooserAircraftId}
          callsign={selectedMapAircraft?.callsign ?? null}
          items={chooserItems}
          selectedEncounterId={selectedEncounterId}
          onSelect={selectEncounter}
          onDismiss={() => setChooserAircraftId(null)}
        />
      ) : null}

      {selection.status !== 'none' ? (
        <SelectedEncounterStrip
          encounterId={
            selection.status === 'current'
              ? (asString(currentItem?.encounter?.encounter_id) ??
                selectedEncounterId ??
                '')
              : selection.encounterId
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
