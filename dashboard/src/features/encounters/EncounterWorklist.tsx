import { useEffect, useMemo, useState, type KeyboardEvent } from 'react';

import { describeApiError } from '@/api/errors';
import { Panel } from '@/components/Panel';
import { EmptyState, LoadingState, Notice } from '@/components/QueryState';
import { StatusPill } from '@/components/StatusPill';
import { useAircraftLayerData } from '@/features/map/useAircraftLayerData';
import {
  describeLoadedVersusCurrent,
  uniqueTokens,
} from '@/features/worklist/worklist';
import { useCurrentEncounters, useOverview } from '@/hooks/useOperationalQueries';
import type { ActiveEncounterItem } from '@/types/api';
import { asString } from '@/utils/coerce';
import {
  formatAircraftLabel,
  formatRiskScore,
  formatUtcTime,
  humaniseToken,
  NOT_REPORTED,
} from '@/utils/format';
import {
  presentConfidence,
  presentEncounterState,
  presentInsideNow,
  presentOverlapStatus,
  presentRiskLevel,
} from '@/utils/status';

import {
  EMPTY_ENCOUNTER_FILTERS,
  describeLoadedEncounterScope,
  encounterIdOf,
  encounterRowKey,
  lookupCallsign,
  visibleCurrentEncounters,
  type EncounterFilters,
  type EncounterSortKey,
} from './encounterList';
import styles from './EncounterWorklist.module.css';

export interface EncounterWorklistProps {
  selectedEncounterId: string | null;
  onSelect: (encounterId: string) => void;
  filters: EncounterFilters;
  onFiltersChange: (filters: EncounterFilters) => void;
  onLoadedItems?: (items: readonly ActiveEncounterItem[]) => void;
}

const SORT_OPTIONS: Array<{ key: EncounterSortKey; label: string }> = [
  { key: 'attention', label: 'Risk' },
  { key: 'newest', label: 'Newest' },
  { key: 'aircraft', label: 'Aircraft' },
  { key: 'hazard', label: 'Hazard' },
  { key: 'insideNow', label: 'Inside now' },
];

/**
 * Dense current-encounter explorer.
 *
 * Rows come from `GET /encounters/active` only. Callsigns are resolved from
 * the already-loaded map feed. Aircraft detail is never requested here.
 */
export function EncounterWorklist({
  selectedEncounterId,
  onSelect,
  filters,
  onFiltersChange,
  onLoadedItems,
}: EncounterWorklistProps) {
  const overview = useOverview();
  const query = useCurrentEncounters();
  const aircraft = useAircraftLayerData();
  const aircraftById = aircraft.result.aircraftById;

  const [sortKey, setSortKey] = useState<EncounterSortKey>('attention');

  const loaded = useMemo(
    () => (query.data?.pages ?? []).flatMap((page) => page.items),
    [query.data?.pages],
  );

  useEffect(() => {
    onLoadedItems?.(loaded);
  }, [loaded, onLoadedItems]);

  const items = useMemo(
    () => visibleCurrentEncounters(loaded, filters, sortKey, aircraftById),
    [aircraftById, filters, loaded, sortKey],
  );

  const riskOptions = useMemo(
    () =>
      uniqueTokens([
        ...loaded.map((item) => item.risk?.risk_level),
        filters.riskLevel,
      ]),
    [filters.riskLevel, loaded],
  );
  const stateOptions = useMemo(
    () => uniqueTokens(loaded.map((item) => item.encounter?.encounter_state)),
    [loaded],
  );
  const altitudeOptions = useMemo(
    () =>
      uniqueTokens(loaded.map((item) => item.encounter?.altitude_overlap_status)),
    [loaded],
  );

  const loading = query.isPending && query.data === undefined;
  const failed = query.isError && query.data === undefined;
  const stale = query.isError && query.data !== undefined;
  const hasMore = query.hasNextPage === true;
  const currentTotal = overview.data?.encounters?.activeCount;
  const filtersActive = hasActiveEncounterFilters(filters);

  return (
    <Panel
      title="Encounter worklist"
      meta={
        failed || loading
          ? undefined
          : `${describeLoadedVersusCurrent(loaded.length, currentTotal)} · ${describeLoadedEncounterScope(loaded)}`
      }
    >
      <form className={styles.toolbar} onSubmit={(event) => event.preventDefault()}>
        <label className={styles.field}>
          <span>Risk</span>
          <select
            value={filters.riskLevel}
            onChange={(event) =>
              onFiltersChange({ ...filters, riskLevel: event.target.value })
            }
          >
            <option value="">All</option>
            {riskOptions.map((level) => (
              <option key={level} value={level}>
                {humaniseToken(level)}
              </option>
            ))}
          </select>
        </label>

        <label className={styles.field}>
          <span>State</span>
          <select
            value={filters.state}
            onChange={(event) =>
              onFiltersChange({ ...filters, state: event.target.value })
            }
          >
            <option value="">All</option>
            {stateOptions.map((state) => (
              <option key={state} value={state}>
                {humaniseToken(state)}
              </option>
            ))}
          </select>
        </label>

        <label className={styles.field}>
          <span>Aircraft</span>
          <input
            type="search"
            value={filters.aircraft}
            placeholder="ID or callsign"
            onChange={(event) =>
              onFiltersChange({ ...filters, aircraft: event.target.value })
            }
          />
        </label>

        <label className={styles.field}>
          <span>Hazard</span>
          <input
            type="search"
            value={filters.hazard}
            placeholder="ID or type"
            onChange={(event) =>
              onFiltersChange({ ...filters, hazard: event.target.value })
            }
          />
        </label>

        <label className={styles.field}>
          <span>Inside now</span>
          <select
            value={filters.insideNow}
            onChange={(event) =>
              onFiltersChange({
                ...filters,
                insideNow: event.target.value as EncounterFilters['insideNow'],
              })
            }
          >
            <option value="">All</option>
            <option value="yes">Inside hazard now</option>
            <option value="no">Not inside now</option>
          </select>
        </label>

        <label className={styles.field}>
          <span>Altitude</span>
          <select
            value={filters.altitude}
            onChange={(event) =>
              onFiltersChange({ ...filters, altitude: event.target.value })
            }
          >
            <option value="">All</option>
            {altitudeOptions.map((token) => (
              <option key={token} value={token}>
                {presentOverlapStatus(token).label}
              </option>
            ))}
          </select>
        </label>

        <label className={styles.field}>
          <span>Sort loaded</span>
          <select
            value={sortKey}
            onChange={(event) =>
              setSortKey(event.target.value as EncounterSortKey)
            }
          >
            {SORT_OPTIONS.map((option) => (
              <option key={option.key} value={option.key}>
                {option.label}
              </option>
            ))}
          </select>
        </label>

        <div className={styles.actions}>
          <button
            type="button"
            className={styles.action}
            onClick={() => onFiltersChange(EMPTY_ENCOUNTER_FILTERS)}
          >
            Clear
          </button>
        </div>
      </form>

      <Notice>
        Filters and sort apply to loaded pages only. They do not query the
        remaining current set. Default order is stored risk HIGH → MEDIUM →
        LOW, then detection time.
      </Notice>

      {stale ? (
        <Notice tone="warning">
          Showing the last successful current-encounter page. The most recent
          update failed: {describeApiError(query.error)}
        </Notice>
      ) : null}

      {loading ? <LoadingState label="Loading current encounters" /> : null}

      {failed ? (
        <EmptyState
          title="Current encounters unavailable"
          detail={describeApiError(query.error)}
        >
          <button
            type="button"
            className={styles.action}
            onClick={() => void query.refetch()}
          >
            Retry
          </button>
        </EmptyState>
      ) : null}

      {!loading && !failed && loaded.length === 0 ? (
        <EmptyState
          title="No current encounters"
          detail="The current-set endpoint returned no aircraft-hazard encounters."
        />
      ) : null}

      {!loading && !failed && loaded.length > 0 && items.length === 0 ? (
        <EmptyState
          title="No encounters match these filters"
          detail="Loaded current encounters remain; the filters hid every row."
        />
      ) : null}

      {!loading && !failed && items.length > 0 ? (
        <div className={styles.tableWrap}>
          <table className={styles.table}>
            <thead>
              <tr>
                <th>Aircraft</th>
                <th>Hazard</th>
                <th>State</th>
                <th>Risk</th>
                <th>Score</th>
                <th>Confidence</th>
                <th>Inside now</th>
                <th>Geometry</th>
                <th>Time</th>
                <th>Altitude</th>
                <th>Detected</th>
              </tr>
            </thead>
            <tbody>
              {items.map((item, index) => {
                const encounterId = encounterIdOf(item);
                const selected =
                  encounterId !== null && encounterId === selectedEncounterId;

                return (
                  <EncounterRow
                    key={encounterRowKey(item, index)}
                    item={item}
                    selected={selected}
                    callsign={lookupCallsign(
                      item.encounter?.aircraft_id,
                      aircraftById,
                    )}
                    disabled={encounterId === null}
                    onSelect={() => {
                      if (encounterId !== null) {
                        onSelect(encounterId);
                      }
                    }}
                  />
                );
              })}
            </tbody>
          </table>
        </div>
      ) : null}

      {query.isFetchNextPageError ? (
        <Notice tone="warning">
          The next current-encounter page could not be loaded.{' '}
          {describeApiError(query.error)}
        </Notice>
      ) : null}

      {!loading && !failed && hasMore ? (
        <div className={styles.footer}>
          <button
            type="button"
            className={styles.action}
            disabled={query.isFetchingNextPage}
            onClick={() => void query.fetchNextPage()}
          >
            {query.isFetchingNextPage
              ? 'Loading next page…'
              : 'Load more current encounters'}
          </button>
        </div>
      ) : null}

      {!loading && !failed && filtersActive ? (
        <p className={styles.scopeNote}>
          {items.length.toLocaleString('en-US')} of{' '}
          {loaded.length.toLocaleString('en-US')} loaded rows match the filters.
        </p>
      ) : null}
    </Panel>
  );
}

function hasActiveEncounterFilters(filters: EncounterFilters): boolean {
  return (
    filters.riskLevel.length > 0 ||
    filters.state.length > 0 ||
    filters.aircraft.trim().length > 0 ||
    filters.hazard.trim().length > 0 ||
    filters.insideNow.length > 0 ||
    filters.altitude.length > 0
  );
}

function EncounterRow({
  item,
  selected,
  callsign,
  disabled,
  onSelect,
}: {
  item: ActiveEncounterItem;
  selected: boolean;
  callsign: string | null;
  disabled: boolean;
  onSelect: () => void;
}) {
  const encounter = item.encounter;
  const risk = item.risk;
  const aircraftId = asString(encounter?.aircraft_id);
  const label = formatAircraftLabel(callsign, aircraftId);

  function activate(event: KeyboardEvent<HTMLTableRowElement>) {
    if (disabled) {
      return;
    }

    if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault();
      onSelect();
    }
  }

  return (
    <tr
      className={selected ? styles.rowSelected : undefined}
      aria-selected={selected}
      aria-disabled={disabled}
      tabIndex={disabled ? -1 : 0}
      onClick={() => {
        if (!disabled) {
          onSelect();
        }
      }}
      onKeyDown={activate}
    >
      <td>
        <span className={styles.primary}>{label}</span>
        {callsign && aircraftId ? (
          <span className={`${styles.id} wv-numeric`}>
            {aircraftId.toUpperCase()}
          </span>
        ) : null}
        {selected ? <span className={styles.selectedMark}>Selected</span> : null}
      </td>
      <td>
        <span className={`${styles.id} wv-numeric`}>
          {asString(encounter?.hazard_id) ?? NOT_REPORTED}
        </span>
        <span className={styles.secondary}>
          {humaniseToken(encounter?.hazard_type)}
        </span>
      </td>
      <td>
        <StatusPill
          size="sm"
          presentation={presentEncounterState(encounter?.encounter_state)}
        />
      </td>
      <td>
        <StatusPill
          size="sm"
          presentation={presentRiskLevel(risk?.risk_level)}
        />
      </td>
      <td className="wv-numeric">
        {formatRiskScore(risk?.risk_score)}
        <span className={styles.scoreMax}>/100</span>
      </td>
      <td>
        <StatusPill size="sm" presentation={presentConfidence(risk?.confidence)} />
      </td>
      <td>
        <StatusPill
          size="sm"
          presentation={presentInsideNow(encounter?.inside_now)}
        />
      </td>
      <td>
        <StatusPill
          size="sm"
          presentation={presentOverlapStatus(encounter?.geometry_overlap_status)}
        />
      </td>
      <td>
        <StatusPill
          size="sm"
          presentation={presentOverlapStatus(encounter?.time_overlap_status)}
        />
      </td>
      <td>
        <StatusPill
          size="sm"
          presentation={presentOverlapStatus(encounter?.altitude_overlap_status)}
        />
      </td>
      <td className={`${styles.timestamp} wv-numeric`}>
        {formatUtcTime(encounter?.detected_at_utc)}
      </td>
    </tr>
  );
}
