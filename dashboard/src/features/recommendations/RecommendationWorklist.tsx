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
import {
  useCurrentRecommendations,
  useOverview,
} from '@/hooks/useOperationalQueries';
import type { Recommendation } from '@/types/api';
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
  presentRecommendationAction,
  presentRiskLevel,
} from '@/utils/status';

import {
  EMPTY_RECOMMENDATION_FILTERS,
  describeLoadedRecommendationScope,
  firstReason,
  lookupCallsign,
  recommendationIdOf,
  recommendationRowKey,
  visibleCurrentRecommendations,
  type RecommendationFilters,
  type RecommendationSortKey,
} from './recommendationList';
import styles from './RecommendationWorklist.module.css';

export interface RecommendationWorklistProps {
  selectedRecommendationId: string | null;
  onSelect: (recommendationId: string) => void;
  filters: RecommendationFilters;
  onFiltersChange: (filters: RecommendationFilters) => void;
  onLoadedItems?: (items: readonly Recommendation[]) => void;
}

const SORT_OPTIONS: Array<{ key: RecommendationSortKey; label: string }> = [
  { key: 'newest', label: 'Newest' },
  { key: 'risk', label: 'Risk' },
  { key: 'action', label: 'Action' },
  { key: 'aircraft', label: 'Aircraft' },
  { key: 'hazard', label: 'Hazard' },
];

/**
 * Dense current-recommendation explorer.
 *
 * Rows come from `GET /recommendations/active` only. Callsigns are resolved
 * from the already-loaded map feed. Aircraft detail is never requested here.
 */
export function RecommendationWorklist({
  selectedRecommendationId,
  onSelect,
  filters,
  onFiltersChange,
  onLoadedItems,
}: RecommendationWorklistProps) {
  const overview = useOverview();
  const query = useCurrentRecommendations();
  const aircraft = useAircraftLayerData();
  const aircraftById = aircraft.result.aircraftById;

  const [sortKey, setSortKey] = useState<RecommendationSortKey>('newest');

  const loaded = useMemo(
    () => (query.data?.pages ?? []).flatMap((page) => page.items),
    [query.data?.pages],
  );

  useEffect(() => {
    onLoadedItems?.(loaded);
  }, [loaded, onLoadedItems]);

  const items = useMemo(
    () => visibleCurrentRecommendations(loaded, filters, sortKey, aircraftById),
    [aircraftById, filters, loaded, sortKey],
  );

  const actionOptions = useMemo(
    () =>
      uniqueTokens([
        ...loaded.map((item) => item.primary_action_type),
        filters.action,
      ]),
    [filters.action, loaded],
  );
  const statusOptions = useMemo(
    () => uniqueTokens(loaded.map((item) => item.recommendation_status)),
    [loaded],
  );
  const riskOptions = useMemo(
    () => uniqueTokens(loaded.map((item) => item.risk_level)),
    [loaded],
  );

  const loading = query.isPending && query.data === undefined;
  const failed = query.isError && query.data === undefined;
  const stale = query.isError && query.data !== undefined;
  const hasMore = query.hasNextPage === true;
  const currentTotal = overview.data?.recommendations?.currentCount;
  const filtersActive = hasActiveRecommendationFilters(filters);

  return (
    <Panel
      title="Recommendation worklist"
      meta={
        failed || loading
          ? undefined
          : `${describeLoadedVersusCurrent(loaded.length, currentTotal)} · ${describeLoadedRecommendationScope(loaded)}`
      }
    >
      <form className={styles.toolbar} onSubmit={(event) => event.preventDefault()}>
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
          <span>State</span>
          <select
            value={filters.status}
            onChange={(event) =>
              onFiltersChange({ ...filters, status: event.target.value })
            }
          >
            <option value="">All</option>
            {statusOptions.map((status) => (
              <option key={status} value={status}>
                {humaniseToken(status)}
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
            onChange={(event) =>
              setSortKey(event.target.value as RecommendationSortKey)
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
            onClick={() => onFiltersChange(EMPTY_RECOMMENDATION_FILTERS)}
          >
            Clear
          </button>
        </div>
      </form>

      <Notice>
        Filters and sort apply to loaded pages only. API pages are newest
        current recommendations first. Risk sort does not invent a priority
        score.
      </Notice>

      {stale ? (
        <Notice tone="warning">
          Showing the last successful current-recommendation page. The most
          recent update failed: {describeApiError(query.error)}
        </Notice>
      ) : null}

      {loading ? <LoadingState label="Loading current recommendations" /> : null}

      {failed ? (
        <EmptyState
          title="Current recommendations unavailable"
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
          title="No current recommendations"
          detail="The current-set endpoint returned no advisory recommendations."
        />
      ) : null}

      {!loading && !failed && loaded.length > 0 && items.length === 0 ? (
        <EmptyState
          title="No recommendations match these filters"
          detail="Loaded current recommendations remain; the filters hid every row."
        />
      ) : null}

      {!loading && !failed && items.length > 0 ? (
        <div className={styles.tableWrap}>
          <table className={styles.table}>
            <thead>
              <tr>
                <th>Aircraft</th>
                <th>Hazard</th>
                <th>Action</th>
                <th>State</th>
                <th>Risk</th>
                <th>Score</th>
                <th>Confidence</th>
                <th>Created</th>
                <th>Valid until</th>
                <th>Why</th>
              </tr>
            </thead>
            <tbody>
              {items.map((item, index) => {
                const recommendationId = recommendationIdOf(item);
                const selected =
                  recommendationId !== null &&
                  recommendationId === selectedRecommendationId;

                return (
                  <RecommendationRow
                    key={recommendationRowKey(item, index)}
                    item={item}
                    selected={selected}
                    callsign={lookupCallsign(item.aircraft_id, aircraftById)}
                    disabled={recommendationId === null}
                    onSelect={() => {
                      if (recommendationId !== null) {
                        onSelect(recommendationId);
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
          The next current-recommendation page could not be loaded.{' '}
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
              : 'Load more current recommendations'}
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

function hasActiveRecommendationFilters(filters: RecommendationFilters): boolean {
  return (
    filters.action.length > 0 ||
    filters.status.length > 0 ||
    filters.riskLevel.length > 0 ||
    filters.aircraft.trim().length > 0 ||
    filters.hazard.trim().length > 0
  );
}

function RecommendationRow({
  item,
  selected,
  callsign,
  disabled,
  onSelect,
}: {
  item: Recommendation;
  selected: boolean;
  callsign: string | null;
  disabled: boolean;
  onSelect: () => void;
}) {
  const aircraftId = asString(item.aircraft_id);
  const label = formatAircraftLabel(callsign, aircraftId);
  const reason = firstReason(item);

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
        <StatusPill
          size="sm"
          presentation={presentRecommendationAction(item.primary_action_type)}
        />
      </td>
      <td>{humaniseToken(item.recommendation_status)}</td>
      <td>
        <StatusPill size="sm" presentation={presentRiskLevel(item.risk_level)} />
      </td>
      <td className="wv-numeric">
        {formatRiskScore(item.risk_score)}
        <span className={styles.scoreMax}>/100</span>
      </td>
      <td>
        <StatusPill size="sm" presentation={presentConfidence(item.confidence)} />
      </td>
      <td className={`${styles.timestamp} wv-numeric`}>
        {formatUtcTime(item.created_at_utc)}
      </td>
      <td className={`${styles.timestamp} wv-numeric`}>
        {formatUtcTime(item.valid_until_utc)}
      </td>
      <td className={styles.secondary}>{reason ?? NOT_REPORTED}</td>
    </tr>
  );
}
