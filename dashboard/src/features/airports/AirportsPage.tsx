import { useCallback, useMemo, useState } from 'react';
import { useParams } from 'react-router-dom';

import { describeApiError } from '@/api/errors';
import { Notice } from '@/components/QueryState';
import { OperationsMap } from '@/features/map/OperationsMap';
import { buildAirportGeoJson } from '@/features/map/airportGeoJson';
import { useOverview } from '@/hooks/useOperationalQueries';
import type { AirportStatus } from '@/types/api';
import { asString } from '@/utils/coerce';

import { AirportInvestigation } from './AirportInvestigation';
import { AirportKpis, type AirportKpiSelection } from './AirportKpis';
import { AirportWorklist } from './AirportWorklist';
import { SelectedAirportStrip } from './SelectedAirportStrip';
import {
  EMPTY_AIRPORT_LIST_FILTERS,
  clearAirportWeatherFilters,
  matchesAirportWeatherFilters,
  visibleAirports,
  withAirportImpact,
  withAirportWeatherRisk,
  type AirportListFilters,
} from './airportList';
import styles from './AirportsPage.module.css';

export interface AirportsPageProps {
  mapStyleUrl: string | null;
}

export function AirportsPage({ mapStyleUrl }: AirportsPageProps) {
  const { airportId: rawAirportId } = useParams();
  const selectedAirportId = asString(rawAirportId)?.toUpperCase() ?? null;
  const [focusedAirportId, setFocusedAirportId] = useState<string | null>(null);

  if (selectedAirportId !== null) {
    return (
      <AirportInvestigation
        airportId={selectedAirportId}
        mapStyleUrl={mapStyleUrl}
      />
    );
  }

  return (
    <AirportsNetwork
      mapStyleUrl={mapStyleUrl}
      focusedAirportId={focusedAirportId}
      onFocus={setFocusedAirportId}
    />
  );
}

function AirportsNetwork({
  mapStyleUrl,
  focusedAirportId,
  onFocus,
}: {
  mapStyleUrl: string | null;
  focusedAirportId: string | null;
  onFocus: (airportId: string | null) => void;
}) {
  const overview = useOverview();
  const [loaded, setLoaded] = useState<readonly AirportStatus[]>([]);
  const [filters, setFilters] = useState<AirportListFilters>(
    EMPTY_AIRPORT_LIST_FILTERS,
  );
  const visible = useMemo(
    () => visibleAirports(loaded, filters.query, 'weatherRisk'),
    [filters.query, loaded],
  );
  const geoJson = useMemo(() => buildAirportGeoJson(visible), [visible]);
  const focused =
    focusedAirportId === null
      ? null
      : (visible.find(
          (item) => asString(item.airport_id)?.toUpperCase() === focusedAirportId,
        ) ?? null);

  const hasOverview = overview.data !== undefined;
  const overviewFailed = overview.isError && !hasOverview;
  const overviewStale = overview.isError && hasOverview;

  const handleSelect = useCallback(
    (airportId: string) => {
      onFocus(airportId.toUpperCase());
    },
    [onFocus],
  );

  const applyFilters = useCallback(
    (next: AirportListFilters) => {
      setFilters(next);

      if (focused && !matchesAirportWeatherFilters(focused, next)) {
        onFocus(null);
      }
    },
    [focused, onFocus],
  );

  const handleKpiSelect = useCallback(
    (selection: AirportKpiSelection) => {
      if (selection === 'all') {
        applyFilters(clearAirportWeatherFilters(filters));
        return;
      }

      if (selection === 'impacted') {
        applyFilters(withAirportImpact(filters, 'WEATHER_IMPACTED'));
        return;
      }

      if (selection === 'highRisk') {
        applyFilters(withAirportWeatherRisk(filters, 'HIGH'));
        return;
      }

      applyFilters(withAirportWeatherRisk(filters, 'UNKNOWN'));
    },
    [applyFilters, filters],
  );

  return (
    <div className={styles.page}>
      {overviewFailed ? (
        <Notice tone="warning">
          Overview counts are unavailable. {describeApiError(overview.error)} The
          worklist still reads `GET /airports`.
        </Notice>
      ) : null}

      {overviewStale ? (
        <Notice tone="warning">
          Showing the last successful overview counts. The most recent update
          failed: {describeApiError(overview.error)}
        </Notice>
      ) : null}

      <header className={styles.intro}>
        <h1 className={styles.title}>Airport Intelligence</h1>
        <p className={styles.lede}>
          Current airport operational conditions from stored AirportStatus.
          Congestion is not classified on this record.
        </p>
      </header>

      <AirportKpis
        data={overview.data}
        weatherRisk={filters.weatherRisk}
        weatherImpact={filters.weatherImpact}
        onKpiSelect={handleKpiSelect}
        stale={overviewStale}
      />

      <div className={styles.main}>
        <div className={styles.mapColumn}>
          <OperationsMap
            styleUrl={mapStyleUrl}
            selectedHazardId={null}
            onSelectHazard={() => {}}
            selectedAircraftId={null}
            onSelectAircraft={() => {}}
            showAircraft={false}
            airports={geoJson.collection}
            selectedAirportId={focusedAirportId}
            onSelectAirport={handleSelect}
          />
        </div>

        <aside className={styles.rail} aria-label="Airport worklist">
          <AirportWorklist
            selectedAirportId={focusedAirportId}
            onSelect={handleSelect}
            filters={filters}
            onFiltersChange={applyFilters}
            onLoadedItems={setLoaded}
          />
        </aside>
      </div>

      {focusedAirportId !== null ? (
        <SelectedAirportStrip
          airportId={focusedAirportId}
          airport={focused}
          onClear={() => onFocus(null)}
        />
      ) : null}
    </div>
  );
}
