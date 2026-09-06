import { useMemo, useState, type FormEvent } from 'react';

import { describeApiError } from '@/api/errors';
import { Panel } from '@/components/Panel';
import { EmptyState, LoadingState, Notice } from '@/components/QueryState';
import { StatusPill } from '@/components/StatusPill';
import { useAircraftList, useOverview } from '@/hooks/useOperationalQueries';
import type { AircraftCurrentState } from '@/types/api';
import { asBoolean, asString } from '@/utils/coerce';
import {
  formatAge,
  formatAircraftLabel,
  formatBoolean,
  formatNumber,
  NOT_REPORTED,
  secondsSince,
} from '@/utils/format';
import { presentFreshness } from '@/utils/status';

import {
  EMPTY_AIRCRAFT_LIST_FILTERS,
  aircraftIdFromListItem,
  aircraftListRowKey,
  committedCallsign,
  committedH3Cell,
  describeLoadedFleet,
  type AircraftListFilters,
} from './aircraftList';
import styles from './AircraftFleetPanel.module.css';

export interface AircraftFleetPanelProps {
  selectedAircraftId: string | null;
  onSelect: (aircraftId: string) => void;
  now?: number;
}

/**
 * Current-state aircraft listing from `GET /aircraft`.
 *
 * Callsign and H3 finds are server-side and exact. The unfiltered list is a
 * scan page, not a ranked attention set. Detail is fetched only after select.
 */
export function AircraftFleetPanel({
  selectedAircraftId,
  onSelect,
  now = Date.now(),
}: AircraftFleetPanelProps) {
  const overview = useOverview();
  const [draft, setDraft] = useState<AircraftListFilters>(
    EMPTY_AIRCRAFT_LIST_FILTERS,
  );
  const [applied, setApplied] = useState<AircraftListFilters>(
    EMPTY_AIRCRAFT_LIST_FILTERS,
  );

  const callsign = committedCallsign(applied.callsign);
  const h3Cell = committedH3Cell(applied.h3Cell);
  const query = useAircraftList({ callsign, h3Cell });

  const items = useMemo(
    () => (query.data?.pages ?? []).flatMap((page) => page.items),
    [query.data?.pages],
  );

  const loading = query.isPending && query.data === undefined;
  const failed = query.isError && query.data === undefined;
  const stale = query.isError && query.data !== undefined;
  const hasMore = query.hasNextPage === true;
  const findActive = callsign != null || h3Cell != null;

  function applyFind(event: FormEvent) {
    event.preventDefault();

    const next: AircraftListFilters = {
      callsign: committedCallsign(draft.callsign) ?? '',
      h3Cell: committedH3Cell(draft.h3Cell) ?? '',
    };

    if (next.callsign && next.h3Cell) {
      next.h3Cell = '';
    }

    setApplied(next);
    setDraft(next);
  }

  return (
    <Panel
      title="Current aircraft"
      meta={
        failed || loading
          ? undefined
          : describeLoadedFleet(items.length, overview.data?.aircraft?.activeCount)
      }
    >
      <form className={styles.toolbar} onSubmit={applyFind}>
        <label className={styles.field}>
          <span>Callsign</span>
          <input
            type="search"
            value={draft.callsign}
            placeholder="exact match"
            disabled={committedH3Cell(draft.h3Cell) != null}
            onChange={(event) =>
              setDraft((current) => ({
                ...current,
                callsign: event.target.value,
                h3Cell:
                  event.target.value.trim().length > 0 ? '' : current.h3Cell,
              }))
            }
          />
        </label>

        <label className={styles.field}>
          <span>H3 cell</span>
          <input
            type="search"
            value={draft.h3Cell}
            placeholder="exact cell"
            disabled={committedCallsign(draft.callsign) != null}
            onChange={(event) =>
              setDraft((current) => ({
                ...current,
                h3Cell: event.target.value,
                callsign:
                  event.target.value.trim().length > 0 ? '' : current.callsign,
              }))
            }
          />
        </label>

        <div className={styles.actions}>
          <button type="submit" className={styles.find}>
            Find
          </button>
          <button
            type="button"
            className={styles.find}
            disabled={!findActive}
            onClick={() => {
              setDraft(EMPTY_AIRCRAFT_LIST_FILTERS);
              setApplied(EMPTY_AIRCRAFT_LIST_FILTERS);
            }}
          >
            Clear find
          </button>
        </div>
      </form>

      {findActive ? (
        <Notice>
          Find is an exact server match on callsign or H3 cell. It is not a
          partial search across the fleet.
        </Notice>
      ) : (
        <Notice>
          Unfiltered pages are a current-state scan. They are not ordered by
          risk. Load more walks opaque nextToken pages only.
        </Notice>
      )}

      {stale ? (
        <Notice tone="warning">
          Showing the last successful aircraft page. The most recent update
          failed: {describeApiError(query.error)}
        </Notice>
      ) : null}

      {loading ? <LoadingState label="Loading current aircraft" /> : null}

      {failed ? (
        <EmptyState
          title="Current aircraft unavailable"
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

      {!loading && !failed && items.length === 0 ? (
        <EmptyState
          title={
            findActive ? 'No aircraft match this find' : 'No current aircraft'
          }
          detail={
            findActive
              ? 'The callsign or H3 cell did not match a current-state row.'
              : 'No unexpired AircraftCurrentState row was returned.'
          }
        />
      ) : null}

      {!loading && !failed && items.length > 0 ? (
        <ul className={styles.list}>
          {items.map((item, index) => {
            const aircraftId = aircraftIdFromListItem(item);

            return (
              <AircraftListRow
                key={aircraftListRowKey(item, index)}
                item={item}
                selected={
                  aircraftId !== null && aircraftId === selectedAircraftId
                }
                disabled={aircraftId === null}
                now={now}
                onSelect={() => {
                  if (aircraftId) {
                    onSelect(aircraftId);
                  }
                }}
              />
            );
          })}
        </ul>
      ) : null}

      {query.isFetchNextPageError ? (
        <Notice tone="warning">
          The next aircraft page could not be loaded.{' '}
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
              : 'Load more aircraft'}
          </button>
        </div>
      ) : null}
    </Panel>
  );
}

function AircraftListRow({
  item,
  selected,
  disabled,
  now,
  onSelect,
}: {
  item: AircraftCurrentState;
  selected: boolean;
  disabled: boolean;
  now: number;
  onSelect: () => void;
}) {
  return (
    <li>
      <button
        type="button"
        className={`${styles.row} ${selected ? styles.rowSelected : ''}`}
        disabled={disabled}
        aria-current={selected ? 'true' : undefined}
        onClick={onSelect}
      >
        <div className={styles.rowHeader}>
          <span className={styles.primary}>
            {formatAircraftLabel(item.callsign, item.aircraft_id)}
          </span>
          <StatusPill
            size="sm"
            presentation={presentFreshness(item.freshness_status)}
          />
          {selected ? (
            <span className={styles.selectedMark}>Selected</span>
          ) : null}
          <span className={`${styles.timestamp} wv-numeric`}>
            {formatAge(secondsSince(item.position_time_utc, now))}
          </span>
        </div>

        <div className={styles.rowBody}>
          <span className={`${styles.secondary} wv-numeric`}>
            {asString(item.aircraft_id)?.toUpperCase() ?? NOT_REPORTED}
          </span>
          <span className={styles.separator} aria-hidden="true">
            ·
          </span>
          <span className={styles.secondary}>
            {formatNumber(item.baro_altitude_ft, { unit: 'ft' })}
          </span>
          <span className={styles.separator} aria-hidden="true">
            ·
          </span>
          <span className={styles.secondary}>
            {formatNumber(item.ground_speed_kt, { unit: 'kt' })}
          </span>
          <span className={styles.separator} aria-hidden="true">
            ·
          </span>
          <span className={styles.secondary}>
            On ground {formatBoolean(asBoolean(item.on_ground))}
          </span>
        </div>
      </button>
      {disabled ? (
        <p className={styles.rowNote}>
          This current-state row has no aircraft id, so it cannot open
          investigation.
        </p>
      ) : null}
    </li>
  );
}
