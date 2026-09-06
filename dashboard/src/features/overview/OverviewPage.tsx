import { useCallback, useMemo, useState } from 'react';

import { describeApiError } from '@/api/errors';
import { ErrorState, Notice } from '@/components/QueryState';
import { encounterHazardIds } from '@/features/aircraft/investigation';
import {
  useAircraftDetail,
  useOverview,
} from '@/hooks/useOperationalQueries';
import { AircraftSelectionPanel } from '@/features/aircraft/AircraftSelectionPanel';
import { OperationsMap } from '@/features/map/OperationsMap';
import { useAircraftLayerData } from '@/features/map/useAircraftLayerData';
import type { ActiveHazard } from '@/types/api';
import { asString } from '@/utils/coerce';

import { AlertsSummaryPanel } from './AlertsSummaryPanel';
import { HazardInvestigationDrawer } from './HazardInvestigationDrawer';
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

  // The aircraft is held by id, not as a snapshot, so the drawer follows each
  // poll instead of freezing at the position it had when it was clicked. This
  // reads from the same cached response the map layer uses.
  const aircraft = useAircraftLayerData();
  const selectedAircraft =
    selectedAircraftId === null
      ? null
      : (aircraft.result.aircraftById.get(selectedAircraftId) ?? null);

  // Same query the investigation panel uses; sharing the key means one request.
  const detail = useAircraftDetail(selectedAircraftId);
  const emphasizedHazardIds = useMemo(
    () => encounterHazardIds(detail.data?.recentEncounters),
    [detail.data?.recentEncounters],
  );

  // One investigation surface, so the two selections are mutually exclusive.
  const handleSelectAircraft = useCallback((aircraftId: string) => {
    setSelectedHazard(null);
    setSelectedAircraftId(aircraftId);
  }, []);

  const handleSelectHazard = useCallback((hazard: ActiveHazard | null) => {
    setSelectedAircraftId(null);
    setSelectedHazard(hazard);
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
          <AlertsSummaryPanel
            activeCount={data?.alerts?.activeCount}
            byState={data?.alerts?.byState}
            loading={loading}
            failed={failedWithoutData}
          />
          <RecommendationsPanel
            recommendations={data?.recommendations?.latest ?? undefined}
            activeCount={data?.recommendations?.activeCount}
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

      <div className={styles.drawer}>
        {selectedAircraftId !== null ? (
          <AircraftSelectionPanel
            aircraftId={selectedAircraftId}
            aircraft={selectedAircraft}
            onClose={() => setSelectedAircraftId(null)}
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
