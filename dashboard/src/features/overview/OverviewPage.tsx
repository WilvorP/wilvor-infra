import { useCallback, useMemo, useState } from 'react';

import { describeApiError } from '@/api/errors';
import { ErrorState, Notice } from '@/components/QueryState';
import {
  currentContextHazardIds,
  type ContextSelection,
} from '@/features/aircraft/investigation';
import {
  useAircraftDetail,
  useOverview,
} from '@/hooks/useOperationalQueries';
import { OperationsMap } from '@/features/map/OperationsMap';
import { useAircraftLayerData } from '@/features/map/useAircraftLayerData';
import { OperationalWorklist } from '@/features/worklist/OperationalWorklist';
import type { ActiveHazard } from '@/types/api';
import { asString } from '@/utils/coerce';

import { HazardInvestigationDrawer } from './HazardInvestigationDrawer';
import { SelectedAircraftStrip } from './SelectedAircraftStrip';
import { ImpactedAirportsPanel } from './ImpactedAirportsPanel';
import { OverviewKpis } from './OverviewKpis';
import { RecommendationsPanel } from './RecommendationsPanel';
import { TopRisksPanel } from './TopRisksPanel';
import styles from './OverviewPage.module.css';

const NO_EMPHASIZED_HAZARDS: string[] = [];

export interface OverviewPageProps {
  mapStyleUrl: string | null;
}

/**
 * Operations Overview.
 *
 * Composition:
 *   - KPI strip, risk/alert/recommendation/airport panels: `GET /overview`
 *   - Map hazard layer:                                    `GET /hazards/active`
 *   - Source freshness strip (in the shell):               `GET /freshness`
 *
 * Aircraft selection keeps the map focus and current projection path.
 * Full Aircraft Investigation lives on `/aircraft/{id}`.
 *
 * When a refresh fails but a previous response is still cached, the last known
 * picture stays on screen and is explicitly labelled stale. Blanking the
 * console on a transient API error would remove context an operator may still
 * be reasoning about.
 */
export function OverviewPage({ mapStyleUrl }: OverviewPageProps) {
  const overview = useOverview();

  const [selectedHazard, setSelectedHazard] = useState<ActiveHazard | null>(
    null,
  );
  const [selectedAircraftId, setSelectedAircraftId] = useState<string | null>(
    null,
  );
  const [contextSelection, setContextSelection] =
    useState<ContextSelection | null>(null);

  // The aircraft is held by id, not as a snapshot, so the map follows each
  // poll instead of freezing at the position it had when it was clicked. This
  // reads from the same cached response the map layer uses.
  const aircraft = useAircraftLayerData();
  const selectedAircraft =
    selectedAircraftId === null
      ? null
      : (aircraft.result.aircraftById.get(selectedAircraftId) ?? null);

  // Same query the investigation panel uses; sharing the key means one request.
  const detail = useAircraftDetail(selectedAircraftId);
  const emphasizedHazardIds = useMemo(() => {
    const selectedHazardId = asString(contextSelection?.hazardId);

    if (selectedHazardId !== null) {
      return [selectedHazardId];
    }

    return currentContextHazardIds(detail.data?.currentContexts);
  }, [contextSelection?.hazardId, detail.data?.currentContexts]);

  // Map selections are mutually exclusive so the projection and hazard
  // emphasis cannot describe two different objects at once.
  const handleSelectAircraft = useCallback((aircraftId: string) => {
    setSelectedHazard(null);
    setContextSelection(null);
    setSelectedAircraftId(aircraftId);
  }, []);

  const handleSelectHazard = useCallback((hazard: ActiveHazard | null) => {
    setSelectedAircraftId(null);
    setContextSelection(null);
    setSelectedHazard(hazard);
  }, []);

  const handleSelectWorkItem = useCallback((selection: ContextSelection) => {
    setSelectedHazard(null);
    setSelectedAircraftId(selection.aircraftId);
    setContextSelection(selection);
  }, []);

  const data = overview.data;
  const hasData = data !== undefined;
  const loading = overview.isPending && !hasData;
  const failedWithoutData = overview.isError && !hasData;
  const failedWithStaleData = overview.isError && hasData;

  if (failedWithoutData) {
    return (
      <div className={styles.page}>
        <div className={styles.fatal}>
          <ErrorState
            error={overview.error}
            subject="the operations overview"
            onRetry={() => void overview.refetch()}
          />
        </div>
      </div>
    );
  }

  return (
    <div className={styles.page}>
      {failedWithStaleData ? (
        <Notice tone="warning">
          Showing the last successful refresh. The most recent update failed:{' '}
          {describeApiError(overview.error)}
        </Notice>
      ) : null}

      <OverviewKpis data={data} stale={failedWithStaleData} />

      <div className={styles.main}>
        <div className={styles.mapColumn}>
          <OperationsMap
            styleUrl={mapStyleUrl}
            selectedHazardId={asString(selectedHazard?.hazard_id)}
            onSelectHazard={handleSelectHazard}
            selectedAircraftId={selectedAircraftId}
            onSelectAircraft={handleSelectAircraft}
            projectionPoints={
              selectedAircraftId === null
                ? null
                : (detail.data?.projectionPoints ?? null)
            }
            emphasizedHazardIds={
              selectedAircraftId === null
                ? NO_EMPHASIZED_HAZARDS
                : emphasizedHazardIds
            }
          />
        </div>

        <aside className={styles.rail} aria-label="Operational detail">
          <TopRisksPanel
            risks={data?.topRisks ?? undefined}
            loading={loading}
            failed={failedWithoutData}
          />
          <RecommendationsPanel
            recommendations={data?.recommendations?.latest ?? undefined}
            currentCount={data?.recommendations?.currentCount}
            loading={loading}
            failed={failedWithoutData}
          />
          <ImpactedAirportsPanel
            airports={data?.airports?.topImpacted ?? undefined}
            loading={loading}
            failed={failedWithoutData}
          />
        </aside>
      </div>

      <div className={styles.worklist}>
        <OperationalWorklist
          selected={contextSelection}
          onSelect={handleSelectWorkItem}
          currentEncounterCount={data?.encounters?.activeCount}
          currentAlertCount={data?.alerts?.currentCount}
          currentRecommendationCount={data?.recommendations?.currentCount}
        />
      </div>

      <div
        className={`${styles.drawer} ${selectedAircraftId !== null ? styles.drawerCompact : ''}`}
      >
        {selectedAircraftId !== null ? (
          <SelectedAircraftStrip
            selection={
              contextSelection ?? {
                aircraftId: selectedAircraftId,
                source: 'map',
              }
            }
            aircraft={selectedAircraft}
            hasProjection={(detail.data?.projectionPoints?.length ?? 0) > 0}
            onClear={() => {
              setSelectedAircraftId(null);
              setContextSelection(null);
            }}
          />
        ) : (
          <HazardInvestigationDrawer
            hazard={selectedHazard}
            onClose={() => setSelectedHazard(null)}
          />
        )}
      </div>
    </div>
  );
}
