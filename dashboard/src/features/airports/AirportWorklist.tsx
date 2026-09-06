import { useEffect, useMemo, useState, type FormEvent } from 'react';

import { describeApiError } from '@/api/errors';
import { Panel } from '@/components/Panel';
import { EmptyState, LoadingState, Notice } from '@/components/QueryState';
import { StatusPill } from '@/components/StatusPill';
import { useAirportList, useOverview } from '@/hooks/useOperationalQueries';
import type { AirportStatus } from '@/types/api';
import { asString } from '@/utils/coerce';
import { formatAge, formatUtcDateTime, NOT_REPORTED } from '@/utils/format';
import {
  presentAssessmentStatus,
  presentFlightCategory,
  presentFreshness,
  presentRiskLevel,
  presentWeatherImpact,
} from '@/utils/status';

import {
  EMPTY_AIRPORT_LIST_FILTERS,
  airportIdFromStatus,
  airportListRowKey,
  committedWeatherFilter,
  describeLoadedAirports,
  visibleAirports,
  type AirportListFilters,
  type AirportListSort,
} from './airportList';
import styles from './AirportWorklist.module.css';

export interface AirportWorklistProps {
  selectedAirportId: string | null;
  onSelect: (airportId: string) => void;
  filters: AirportListFilters;
  onFiltersChange: (filters: AirportListFilters) => void;
  onLoadedItems?: (items: readonly AirportStatus[]) => void;
  now?: number;
}

export function AirportWorklist({
  selectedAirportId,
  onSelect,
  filters,
  onFiltersChange,
  onLoadedItems,
  now = Date.now(),
}: AirportWorklistProps) {
  const overview = useOverview();
  const [draft, setDraft] = useState<AirportListFilters>(filters);
  const [sort, setSort] = useState<AirportListSort>('weatherRisk');

  useEffect(() => {
    setDraft(filters);
  }, [filters]);

  const weatherRisk = committedWeatherFilter(filters.weatherRisk);
  const weatherImpact = committedWeatherFilter(filters.weatherImpact);
  const query = useAirportList({ weatherRisk, weatherImpact });

  const loaded = useMemo(
    () => (query.data?.pages ?? []).flatMap((page) => page.items),
    [query.data?.pages],
  );

  useEffect(() => {
    onLoadedItems?.(loaded);
  }, [loaded, onLoadedItems]);

  const items = useMemo(
    () => visibleAirports(loaded, filters.query, sort),
    [filters.query, loaded, sort],
  );

  const loading = query.isPending && query.data === undefined;
  const failed = query.isError && query.data === undefined;
  const stale = query.isError && query.data !== undefined;
  const hasMore = query.hasNextPage === true;
  const serverFilter = weatherRisk != null || weatherImpact != null;

  function applyFilters(event: FormEvent) {
    event.preventDefault();
    onFiltersChange({
      weatherRisk: committedWeatherFilter(draft.weatherRisk) ?? '',
      weatherImpact: committedWeatherFilter(draft.weatherImpact) ?? '',
      query: draft.query,
    });
  }

  return (
    <Panel
      title="Airport worklist"
      meta={
        failed || loading
          ? undefined
          : describeLoadedAirports(
              loaded.length,
              overview.data?.airports?.currentCount,
            )
      }
    >
      <form className={styles.toolbar} onSubmit={applyFilters}>
        <label className={styles.field}>
          <span>Find</span>
          <input
            type="search"
            value={draft.query}
            placeholder="ICAO / name on loaded pages"
            onChange={(event) =>
              setDraft((current) => ({ ...current, query: event.target.value }))
            }
          />
        </label>

        <label className={styles.field}>
          <span>Weather risk</span>
          <select
            value={draft.weatherRisk}
            onChange={(event) =>
              setDraft((current) => ({
                ...current,
                weatherRisk: event.target.value,
              }))
            }
          >
            <option value="">All (scan)</option>
            <option value="HIGH">HIGH</option>
            <option value="MEDIUM">MEDIUM</option>
            <option value="LOW">LOW</option>
            <option value="UNKNOWN">UNKNOWN</option>
          </select>
        </label>

        <label className={styles.field}>
          <span>Weather impact</span>
          <select
            value={draft.weatherImpact}
            onChange={(event) =>
              setDraft((current) => ({
                ...current,
                weatherImpact: event.target.value,
              }))
            }
          >
            <option value="">All (scan)</option>
            <option value="WEATHER_IMPACTED">WEATHER_IMPACTED</option>
            <option value="NORMAL">NORMAL</option>
            <option value="UNKNOWN">UNKNOWN</option>
          </select>
        </label>

        <label className={styles.field}>
          <span>Sort loaded</span>
          <select
            value={sort}
            onChange={(event) =>
              setSort(event.target.value as AirportListSort)
            }
          >
            <option value="weatherRisk">Weather risk</option>
            <option value="weatherImpact">Weather impact</option>
            <option value="airportId">Airport ID</option>
            <option value="updated">Updated</option>
          </select>
        </label>

        <div className={styles.actions}>
          <button type="submit" className={styles.find}>
            Apply
          </button>
          <button
            type="button"
            className={styles.find}
            onClick={() => onFiltersChange(EMPTY_AIRPORT_LIST_FILTERS)}
          >
            Clear
          </button>
        </div>
      </form>

      <Notice>
        {serverFilter
          ? 'Weather risk and impact are exact server filters. Find and sort apply to loaded pages only.'
          : 'Unfiltered pages are an unexpired AirportStatus scan. Find and sort apply to loaded pages only.'}
      </Notice>

      {stale ? (
        <Notice tone="warning">
          Showing the last successful airport page. The most recent update
          failed: {describeApiError(query.error)}
        </Notice>
      ) : null}

      {loading ? <LoadingState label="Loading current airports" /> : null}

      {failed ? (
        <EmptyState
          title="Current airports unavailable"
          detail={describeApiError(query.error)}
        >
          <button
            type="button"
            className={styles.find}
            onClick={() => void query.refetch()}
          >
            Retry
          </button>
        </EmptyState>
      ) : null}

      {!loading && !failed && loaded.length === 0 ? (
        <EmptyState
          title="No current airport data available"
          detail="No unexpired AirportStatus row was returned. Missing data is not treated as Normal."
        />
      ) : null}

      {!loading && !failed && loaded.length > 0 && items.length === 0 ? (
        <EmptyState
          title="No airports match this find"
          detail="Find searches ICAO, station name and IATA on the pages already loaded."
        />
      ) : null}

      {!loading && !failed && items.length > 0 ? (
        <ul className={styles.list}>
          {items.map((item, index) => {
            const airportId = airportIdFromStatus(item);

            return (
              <AirportRow
                key={airportListRowKey(item, index)}
                item={item}
                selected={airportId !== null && airportId === selectedAirportId}
                disabled={airportId === null}
                now={now}
                onSelect={() => {
                  if (airportId) {
                    onSelect(airportId);
                  }
                }}
              />
            );
          })}
        </ul>
      ) : null}

      {query.isFetchNextPageError ? (
        <Notice tone="warning">
          The next airport page could not be loaded.{' '}
          {describeApiError(query.error)}
        </Notice>
      ) : null}

      {!loading && !failed && hasMore ? (
        <div className={styles.footer}>
          <button
            type="button"
            className={styles.find}
            disabled={query.isFetchingNextPage}
            onClick={() => void query.fetchNextPage()}
          >
            {query.isFetchingNextPage
              ? 'Loading next page…'
              : 'Load more airports'}
          </button>
        </div>
      ) : null}
    </Panel>
  );
}

function AirportRow({
  item,
  selected,
  disabled,
  now,
  onSelect,
}: {
  item: AirportStatus;
  selected: boolean;
  disabled: boolean;
  now: number;
  onSelect: () => void;
}) {
  const airportId = airportIdFromStatus(item) ?? NOT_REPORTED;
  const name = asString(item.station_name);
  const ageSeconds =
    item.updated_at_epoch != null
      ? Math.max(0, Math.round(now / 1000 - Number(item.updated_at_epoch)))
      : null;

  return (
    <li>
      <button
        type="button"
        className={`${styles.row} ${selected ? styles.rowSelected : ''}`}
        disabled={disabled}
        onClick={onSelect}
      >
        <div className={styles.rowHeader}>
          <span className={styles.primary}>{airportId}</span>
          {name ? <span className={styles.secondary}>{name}</span> : null}
          {selected ? (
            <span className={styles.selectedMark}>Selected</span>
          ) : null}
          <span className={`${styles.timestamp} wv-numeric`}>
            {ageSeconds === null ? NOT_REPORTED : formatAge(ageSeconds)}
          </span>
        </div>
        <div className={styles.rowBody}>
          <StatusPill
            size="sm"
            presentation={presentWeatherImpact(item.weather_impact_status)}
          />
          <StatusPill
            size="sm"
            prefix="Wx risk"
            presentation={presentRiskLevel(item.weather_risk_level)}
          />
          <StatusPill
            size="sm"
            prefix="Cat"
            presentation={presentFlightCategory(item.flight_category)}
          />
          <StatusPill
            size="sm"
            prefix="METAR"
            presentation={presentFreshness(item.metar_freshness_status)}
          />
          <StatusPill
            size="sm"
            prefix="TAF"
            presentation={presentFreshness(item.taf_freshness_status)}
          />
          <StatusPill
            size="sm"
            presentation={presentAssessmentStatus(item.assessment_status)}
          />
        </div>
        <p className={styles.rowNote}>
          Updated {formatUtcDateTime(item.updated_at_utc)}
        </p>
      </button>
    </li>
  );
}
