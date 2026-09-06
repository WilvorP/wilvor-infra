import { useCallback, useMemo } from 'react';
import { Link, useNavigate, useParams, useSearchParams } from 'react-router-dom';

import { StatusPill } from '@/components/StatusPill';
import { AircraftSelectionPanel } from '@/features/aircraft/AircraftSelectionPanel';
import {
  contextSelectionFromSearch,
  currentContextHazardIds,
  highestCurrentRisk,
} from '@/features/aircraft/investigation';
import type { MapAircraft } from '@/features/map/aircraftGeoJson';
import { OperationsMap } from '@/features/map/OperationsMap';
import { useAircraftLayerData } from '@/features/map/useAircraftLayerData';
import { useAircraftDetail } from '@/hooks/useOperationalQueries';
import type { AircraftCurrentState } from '@/types/api';
import { asNumber, asString } from '@/utils/coerce';
import {
  formatAircraftLabel,
  formatNumber,
  NOT_REPORTED,
} from '@/utils/format';
import { presentFreshness, presentRiskLevel } from '@/utils/status';

import { AircraftFleetPanel } from './AircraftFleetPanel';
import { mapAircraftFromListItem } from './aircraftList';
import styles from './AircraftPage.module.css';

const NO_EMPHASIZED_HAZARDS: string[] = [];

export interface AircraftPageProps {
  mapStyleUrl: string | null;
}

/**
 * Aircraft Investigation workflow.
 *
 * Listing: `GET /aircraft` current-state pages plus the fleet map.
 * Investigation: selected aircraft, current projection, and currentContext
 * matched by stored IDs from the worklist.
 */
export function AircraftPage({ mapStyleUrl }: AircraftPageProps) {
  const { aircraftId: rawAircraftId } = useParams();
  const [searchParams] = useSearchParams();
  const navigate = useNavigate();
  const selectedAircraftId = asString(rawAircraftId)?.toLowerCase() ?? null;
  const contextSelection =
    selectedAircraftId === null
      ? null
      : contextSelectionFromSearch(selectedAircraftId, searchParams);

  const aircraft = useAircraftLayerData();
  const detail = useAircraftDetail(selectedAircraftId);

  const selectedFromMap =
    selectedAircraftId === null
      ? null
      : (aircraft.result.aircraftById.get(selectedAircraftId) ?? null);

  const selectedFromDetail = useMemo(() => {
    if (selectedAircraftId === null || detail.data?.aircraft == null) {
      return null;
    }

    return mapAircraftFromListItem(detail.data.aircraft);
  }, [detail.data?.aircraft, selectedAircraftId]);

  const selectedAircraft = selectedFromMap ?? selectedFromDetail;

  const emphasizedHazardIds = useMemo(
    () => currentContextHazardIds(detail.data?.currentContexts),
    [detail.data?.currentContexts],
  );

  const handleSelectAircraft = useCallback(
    (nextAircraftId: string) => {
      navigate(`/aircraft/${encodeURIComponent(nextAircraftId)}`);
    },
    [navigate],
  );

  const handleClear = useCallback(() => {
    navigate('/aircraft');
  }, [navigate]);

  const map = (
    <OperationsMap
      styleUrl={mapStyleUrl}
      selectedHazardId={null}
      onSelectHazard={() => {}}
      selectedAircraftId={selectedAircraftId}
      onSelectAircraft={handleSelectAircraft}
      isolateSelectedAircraft={selectedAircraftId !== null}
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
  );

  if (selectedAircraftId === null) {
    return (
      <div className={styles.page}>
        <div className={styles.listing}>
          <aside className={styles.rail} aria-label="Current aircraft listing">
            <AircraftFleetPanel
              selectedAircraftId={selectedAircraftId}
              onSelect={handleSelectAircraft}
            />
          </aside>

          <div className={styles.mapColumn}>{map}</div>
        </div>

        <p className={styles.empty}>
          Select an aircraft to open investigation. The map shows the current
          fleet; the projection path appears after a current projection is
          returned.
        </p>
      </div>
    );
  }

  return (
    <div className={`${styles.page} ${styles.investigating}`}>
      <InvestigationChrome
        aircraftId={selectedAircraftId}
        aircraft={selectedAircraft}
        current={detail.data?.aircraft ?? null}
        riskLevel={highestCurrentRisk(detail.data?.currentContexts)?.risk_level}
        onClose={handleClear}
      />

      <AircraftSelectionPanel
        aircraftId={selectedAircraftId}
        aircraft={selectedAircraft}
        contextSelection={contextSelection}
        onClose={handleClear}
        variant="page"
      >
        {map}
      </AircraftSelectionPanel>
    </div>
  );
}

function InvestigationChrome({
  aircraftId,
  aircraft,
  current,
  riskLevel,
  onClose,
}: {
  aircraftId: string;
  aircraft: MapAircraft | null;
  current: AircraftCurrentState | null;
  riskLevel: unknown;
  onClose: () => void;
}) {
  const callsign = aircraft?.callsign ?? asString(current?.callsign);
  const latitude = aircraft?.latitude ?? asNumber(current?.latitude);
  const longitude = aircraft?.longitude ?? asNumber(current?.longitude);
  const track = aircraft?.trackDeg ?? asNumber(current?.track_deg);
  const altitude = aircraft?.baroAltitudeFt ?? asNumber(current?.baro_altitude_ft);
  const speed = aircraft?.groundSpeedKt ?? asNumber(current?.ground_speed_kt);
  const freshness = current?.freshness_status;

  return (
    <header className={styles.chrome}>
      <div className={styles.chromeRow}>
        <Link className={styles.back} to="/">
          Operations
        </Link>

        <div className={styles.identity}>
          <span className={styles.callsign}>
            {formatAircraftLabel(callsign, aircraftId)}
          </span>
          <span className={`${styles.id} wv-numeric`}>
            {aircraftId.toUpperCase()}
          </span>
        </div>

        {riskLevel != null && asString(riskLevel) !== null ? (
          <StatusPill
            size="sm"
            prefix="Risk"
            presentation={presentRiskLevel(riskLevel)}
          />
        ) : null}

        {freshness ? (
          <StatusPill size="sm" presentation={presentFreshness(freshness)} />
        ) : null}

        <button type="button" className={styles.close} onClick={onClose}>
          Close
        </button>
      </div>

      <p className={`${styles.kinematics} wv-numeric`}>
        {formatNumber(latitude, { digits: 4 })},{' '}
        {formatNumber(longitude, { digits: 4 })}
        {' · '}
        {altitude === null
          ? NOT_REPORTED
          : formatNumber(altitude, { unit: 'ft' })}
        {' · '}
        {speed === null ? NOT_REPORTED : formatNumber(speed, { unit: 'kt' })}
        {' · '}
        {track === null ? NOT_REPORTED : `${formatNumber(track)}°`}
      </p>
    </header>
  );
}
