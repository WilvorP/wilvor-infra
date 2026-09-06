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
import { useCurrentAlerts, useOverview } from '@/hooks/useOperationalQueries';
import type { ActiveAlert } from '@/types/api';
import { asString } from '@/utils/coerce';
import {
  formatAircraftLabel,
  formatRiskScore,
  formatUtcTime,
  humaniseToken,
  NOT_REPORTED,
} from '@/utils/format';
import {
  presentAlertState,
  presentRecommendationAction,
  presentRiskLevel,
} from '@/utils/status';

import {
  EMPTY_ALERT_FILTERS,
  alertIdOf,
  alertRowKey,
  describeLoadedAlertScope,
  lookupCallsign,
  visibleCurrentAlerts,
  type AlertFilters,
  type AlertSortKey,
} from './alertList';
import styles from './AlertWorklist.module.css';

export interface AlertWorklistProps {
  selectedAlertId: string | null;
  onSelect: (alertId: string) => void;
  filters: AlertFilters;
  onFiltersChange: (filters: AlertFilters) => void;
  onLoadedItems?: (items: readonly ActiveAlert[]) => void;
}

const SORT_OPTIONS: Array<{ key: AlertSortKey; label: string }> = [
  { key: 'newest', label: 'Newest update' },
  { key: 'risk', label: 'Risk' },
  { key: 'state', label: 'Alert state' },
  { key: 'aircraft', label: 'Aircraft' },
  { key: 'hazard', label: 'Hazard' },
];

/**
 * Dense current-alert explorer.
 *
 * Rows come from `GET /alerts/active` only. Callsigns are resolved from the
 * already-loaded map feed. Aircraft detail is never requested here.
 */
export function AlertWorklist({
  selectedAlertId,
  onSelect,
  filters,
  onFiltersChange,
  onLoadedItems,
}: AlertWorklistProps) {
  const overview = useOverview();
  const query = useCurrentAlerts();
  const aircraft = useAircraftLayerData();
  const aircraftById = aircraft.result.aircraftById;

  const [sortKey, setSortKey] = useState<AlertSortKey>('newest');

  const loaded = useMemo(
    () => (query.data?.pages ?? []).flatMap((page) => page.items),
    [query.data?.pages],
  );

  useEffect(() => {
    onLoadedItems?.(loaded);
  }, [loaded, onLoadedItems]);

  const items = useMemo(
    () => visibleCurrentAlerts(loaded, filters, sortKey, aircraftById),
    [aircraftById, filters, loaded, sortKey],
  );

  const stateOptions = useMemo(
    () => uniqueTokens([...loaded.map((item) => item.alert_state), filters.state]),
    [filters.state, loaded],
  );
  const riskOptions = useMemo(
    () => uniqueTokens(loaded.map((item) => item.risk_level)),
    [loaded],
  );
  const actionOptions = useMemo(
    () =>
      uniqueTokens([
        ...loaded.map((item) => item.primary_action_type),
        filters.action,
      ]),
    [filters.action, loaded],
  );

  const loading = query.isPending && query.data === undefined;
  const failed = query.isError && query.data === undefined;
  const stale = query.isError && query.data !== undefined;
  const hasMore = query.hasNextPage === true;
  const currentTotal = overview.data?.alerts?.currentCount;
  const filtersActive = hasActiveAlertFilters(filters);

  return (
    <Panel
      title="Alert worklist"
      meta={
        failed || loading
          ? undefined
          : `${describeLoadedVersusCurrent(loaded.length, currentTotal)} · ${describeLoadedAlertScope(loaded)}`
      }
    >
      <form className={styles.toolbar} onSubmit={(event) => event.preventDefault()}>
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
                {presentAlertState(state).label}
              </option>
            ))}
          </select>
        </label>

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
          <span>Action</span>
          <select
            value={filters.action}
            onChange={(event) =>
              onFiltersChange({ ...filters, action: event.target.value })
            }
          >
            <option value="">All</option>
            {actionOptions.map((action) => (
              <option key={action} value={action}>
                {presentRecommendationAction(action).label}
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
            placeholder="Hazard ID"
            onChange={(event) =>
              onFiltersChange({ ...filters, hazard: event.target.value })
            }
          />
        </label>

        <label className={styles.field}>
          <span>Sort loaded</span>
          <select
            value={sortKey}
            onChange={(event) => setSortKey(event.target.value as AlertSortKey)}
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
            onClick={() => onFiltersChange(EMPTY_ALERT_FILTERS)}
          >
            Clear
          </button>
        </div>
      </form>

      <Notice>
        Filters and sort apply to loaded pages only. API pages are newest
        current alerts first. Risk sort does not invent an alert priority
        score.
      </Notice>

      {stale ? (
        <Notice tone="warning">
          Showing the last successful current-alert page. The most recent
          update failed: {describeApiError(query.error)}
        </Notice>
      ) : null}

      {loading ? <LoadingState label="Loading current alerts" /> : null}

      {failed ? (
        <EmptyState
          title="Current alerts unavailable"
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
          title="No current alerts"
          detail="The current-set endpoint returned no operational alerts."
        />
      ) : null}

      {!loading && !failed && loaded.length > 0 && items.length === 0 ? (
        <EmptyState
          title="No alerts match these filters"
          detail="Loaded current alerts remain; the filters hid every row."
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
                <th>Message</th>
                <th>Action</th>
                <th>Created</th>
                <th>Updated</th>
                <th>Valid until</th>
              </tr>
            </thead>
            <tbody>
              {items.map((item, index) => {
                const alertId = alertIdOf(item);
                const selected = alertId !== null && alertId === selectedAlertId;

                return (
                  <AlertRow
                    key={alertRowKey(item, index)}
                    item={item}
                    selected={selected}
                    callsign={lookupCallsign(item.aircraft_id, aircraftById)}
                    disabled={alertId === null}
                    onSelect={() => {
                      if (alertId !== null) {
                        onSelect(alertId);
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
          The next current-alert page could not be loaded.{' '}
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
              : 'Load more current alerts'}
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

function hasActiveAlertFilters(filters: AlertFilters): boolean {
  return (
    filters.state.length > 0 ||
    filters.riskLevel.length > 0 ||
    filters.action.length > 0 ||
    filters.aircraft.trim().length > 0 ||
    filters.hazard.trim().length > 0
  );
}

function AlertRow({
  item,
  selected,
  callsign,
  disabled,
  onSelect,
}: {
  item: ActiveAlert;
  selected: boolean;
  callsign: string | null;
  disabled: boolean;
  onSelect: () => void;
}) {
  const aircraftId = asString(item.aircraft_id);
  const label = formatAircraftLabel(callsign, aircraftId);
  const message = asString(item.message);

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
          {asString(item.hazard_id) ?? NOT_REPORTED}
        </span>
      </td>
      <td>
        <StatusPill size="sm" presentation={presentAlertState(item.alert_state)} />
      </td>
      <td>
        <StatusPill size="sm" presentation={presentRiskLevel(item.risk_level)} />
      </td>
      <td className="wv-numeric">
        {formatRiskScore(item.risk_score)}
        <span className={styles.scoreMax}>/100</span>
      </td>
      <td className={styles.secondary}>{message ?? NOT_REPORTED}</td>
      <td>
        <StatusPill
          size="sm"
          presentation={presentRecommendationAction(item.primary_action_type)}
        />
      </td>
      <td className={`${styles.timestamp} wv-numeric`}>
        {formatUtcTime(item.created_at_utc)}
      </td>
      <td className={`${styles.timestamp} wv-numeric`}>
        {formatUtcTime(item.updated_at_utc)}
      </td>
      <td className={`${styles.timestamp} wv-numeric`}>
        {formatUtcTime(item.valid_until_utc)}
      </td>
    </tr>
  );
}
