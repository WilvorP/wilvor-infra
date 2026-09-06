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
import type { ActiveAlert, ActiveHazard } from '@/types/api';
import { asString } from '@/utils/coerce';

import { AircraftAlertChooser } from './AircraftAlertChooser';
import { AlertKpis } from './AlertKpis';
import { AlertWorklist } from './AlertWorklist';
import { SelectedAlertStrip } from './SelectedAlertStrip';
import {
  EMPTY_ALERT_FILTERS,
  alertAircraftId,
  alertHazardId,
  alertIdOf,
  countLoadedAlertStates,
  filterCurrentAlerts,
  loadedAlertAircraftIds,
  loadedAlertHazardIds,
  loadedAlertsForAircraft,
  matchesAlertStateFilter,
  pickAlertForHazard,
  recordSeenAlertIds,
  resolveAlertSelection,
  resolveMapAircraftAlertClick,
  sortCurrentAlerts,
  withAlertState,
  type AlertFilters,
  type AlertStateKpi,
} from './alertList';
import styles from './AlertsPage.module.css';

export interface AlertsPageProps {
  mapStyleUrl: string | null;
}

/**
 * Dedicated current-alert explorer.
 *
 * Membership comes only from `GET /alerts/active`. The network total comes
 * from `overview.alerts.currentCount`. Aircraft detail is fetched for the
 * selected alert only.
 */
export function AlertsPage({ mapStyleUrl }: AlertsPageProps) {
  const [searchParams, setSearchParams] = useSearchParams();
  const selectedAlertId = asString(searchParams.get('alertId'));
  const [loaded, setLoaded] = useState<readonly ActiveAlert[]>([]);
  const [seenIds, setSeenIds] = useState<ReadonlySet<string>>(
    () => new Set<string>(),
  );
  const [chooserAircraftId, setChooserAircraftId] = useState<string | null>(
    null,
  );
  const [filters, setFilters] = useState<AlertFilters>(EMPTY_ALERT_FILTERS);

  const overview = useOverview();
  const hazards = useActiveHazards();
  const aircraft = useAircraftLayerData();

  const handleLoadedItems = useCallback((items: readonly ActiveAlert[]) => {
    setLoaded(items);
    setSeenIds((current) => {
      const next = new Set(current);
      const before = next.size;
      recordSeenAlertIds(next, items);
      return next.size === before ? current : next;
    });
  }, []);

  const selection = useMemo(
    () => resolveAlertSelection(selectedAlertId, loaded, seenIds),
    [loaded, seenIds, selectedAlertId],
  );

  const currentItem = selection.status === 'current' ? selection.item : null;
  const filtered = useMemo(
    () => filterCurrentAlerts(loaded, filters, aircraft.result.aircraftById),
    [aircraft.result.aircraftById, filters, loaded],
  );
  const selectedAircraftId =
    chooserAircraftId ?? (currentItem ? alertAircraftId(currentItem) : null);
  const selectedHazardId = currentItem ? alertHazardId(currentItem) : null;
  const chooserItems = useMemo(
    () =>
      chooserAircraftId === null
        ? []
        : loadedAlertsForAircraft(filtered, chooserAircraftId),
    [chooserAircraftId, filtered],
  );

  const detailAircraftId = currentItem ? alertAircraftId(currentItem) : null;
  const detail = useAircraftDetail(detailAircraftId);
  const selectedMapAircraft =
    selectedAircraftId === null
      ? null
      : (aircraft.result.aircraftById.get(selectedAircraftId) ?? null);

  const visibleAircraftIds = useMemo(
    () => loadedAlertAircraftIds(filtered),
    [filtered],
  );
  const loadedHazardIds = useMemo(
    () => loadedAlertHazardIds(filtered),
    [filtered],
  );
  const chooserHazardIds = useMemo(
    () => loadedAlertHazardIds(chooserItems),
    [chooserItems],
  );
  const emphasizedHazardIds = selectedHazardId
    ? [selectedHazardId]
    : chooserAircraftId !== null
      ? chooserHazardIds
      : loadedHazardIds;

  const rankedLoaded = useMemo(
    () => sortCurrentAlerts(filtered, 'newest'),
    [filtered],
  );
  const loadedStates = useMemo(() => countLoadedAlertStates(loaded), [loaded]);

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

  const selectAlert = useCallback(
    (alertId: string) => {
      setChooserAircraftId(null);
      setSearchParams({ alertId }, { replace: true });
    },
    [setSearchParams],
  );

  const clearSelection = useCallback(() => {
    setChooserAircraftId(null);
    setSearchParams({}, { replace: true });
  }, [setSearchParams]);

  const applyFilters = useCallback(
    (next: AlertFilters) => {
      const stateChanged = next.state !== filters.state;

      setFilters(next);

      if (
        stateChanged &&
        currentItem &&
        !matchesAlertStateFilter(currentItem, next.state)
      ) {
        clearSelection();
      }
    },
    [clearSelection, currentItem, filters.state],
  );

  const handleStateFilterChange = useCallback(
    (state: AlertStateKpi) => {
      applyFilters(withAlertState(filters, state));
    },
    [applyFilters, filters],
  );

  const handleMapAircraft = useCallback(
    (aircraftId: string) => {
      const choice = resolveMapAircraftAlertClick(filtered, aircraftId);

      if (choice.kind === 'single') {
        const alertId = alertIdOf(choice.item);

        if (alertId !== null) {
          selectAlert(alertId);
        }

        return;
      }

      if (choice.kind === 'multiple') {
        const currentBelongsToAircraft = choice.items.some(
          (item) => alertIdOf(item) === selectedAlertId,
        );

        setChooserAircraftId(aircraftId);

        if (!currentBelongsToAircraft) {
          setSearchParams({}, { replace: true });
        }
      }
    },
    [filtered, selectAlert, selectedAlertId, setSearchParams],
  );

  useEffect(() => {
    if (chooserAircraftId === null) {
      return;
    }

    const remaining = resolveMapAircraftAlertClick(filtered, chooserAircraftId);

    if (remaining.kind === 'none') {
      setChooserAircraftId(null);
      return;
    }

    if (remaining.kind === 'single') {
      const alertId = alertIdOf(remaining.item);

      if (alertId !== null) {
        selectAlert(alertId);
      }
    }
  }, [chooserAircraftId, filtered, selectAlert]);

  const handleMapHazard = useCallback(
    (hazard: ActiveHazard | null) => {
      const hazardId = asString(hazard?.hazard_id);

      if (hazardId === null) {
        return;
      }

      const match = pickAlertForHazard(rankedLoaded, hazardId, selectedAlertId);
      const alertId = match ? alertIdOf(match) : null;

      if (alertId !== null) {
        selectAlert(alertId);
      }
    },
    [rankedLoaded, selectAlert, selectedAlertId],
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

  const dockOpen =
    selection.status !== 'none' ||
    (chooserAircraftId !== null && chooserItems.length > 1);

  return (
    <div className={dockOpen ? `${styles.page} ${styles.pageWithDock}` : styles.page}>
      <div className={styles.top}>
        {overviewFailed ? (
          <Notice tone="warning">
            Overview counts are unavailable. {describeApiError(overview.error)} The
            worklist still reads `GET /alerts/active`.
          </Notice>
        ) : null}

        {overviewStale ? (
          <Notice tone="warning">
            Showing the last successful overview counts. The most recent update
            failed: {describeApiError(overview.error)}
          </Notice>
        ) : null}

        <header className={styles.intro}>
          <h1 className={styles.title}>Current Alerts</h1>
          <p className={styles.lede}>
            Operational attention and alert lifecycle. This list is the backend
            current set, not retained ACTIVE alert history.
          </p>
        </header>

        <AlertKpis
          data={overview.data}
          loadedStates={loadedStates}
          stateFilter={filters.state}
          onStateFilterChange={handleStateFilterChange}
          problem={
            overviewFailed ? describeApiError(overview.error) : undefined
          }
          stale={overviewStale}
        />
      </div>

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

        <aside className={styles.rail} aria-label="Alert worklist">
          <AlertWorklist
            selectedAlertId={selectedAlertId}
            onSelect={selectAlert}
            filters={filters}
            onFiltersChange={applyFilters}
            onLoadedItems={handleLoadedItems}
          />
        </aside>
      </div>

      {dockOpen ? (
        <div className={styles.dock} data-testid="selected-alert-dock">
          {chooserAircraftId !== null && chooserItems.length > 1 ? (
            <AircraftAlertChooser
              aircraftId={chooserAircraftId}
              callsign={selectedMapAircraft?.callsign ?? null}
              items={chooserItems}
              selectedAlertId={selectedAlertId}
              onSelect={selectAlert}
              onDismiss={() => setChooserAircraftId(null)}
            />
          ) : null}

          {selection.status !== 'none' ? (
            <SelectedAlertStrip
              alertId={
                selection.status === 'current'
                  ? (asString(currentItem?.alert_id) ?? selectedAlertId ?? '')
                  : selection.alertId
              }
              item={currentItem}
              callsign={selectedMapAircraft?.callsign ?? null}
              presence={stripPresence}
              detail={detail.data ?? null}
              detailFailed={detail.isError && detail.data === undefined}
              detailPending={detail.isPending && detail.data === undefined}
              mapAircraftMissing={
                selectedAircraftId !== null &&
                selectedMapAircraft === null &&
                !aircraft.isPending
              }
              onClear={clearSelection}
            />
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
