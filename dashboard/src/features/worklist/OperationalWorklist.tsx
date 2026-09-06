import { useMemo, useState, type ReactNode } from 'react';

import { describeApiError } from '@/api/errors';
import { Panel } from '@/components/Panel';
import { EmptyState, LoadingState, Notice } from '@/components/QueryState';
import { StatusPill } from '@/components/StatusPill';
import type { ContextSelection } from '@/features/aircraft/investigation';
import { useAircraftLayerData } from '@/features/map/useAircraftLayerData';
import {
  useCurrentAlerts,
  useCurrentEncounters,
  useCurrentRecommendations,
} from '@/hooks/useOperationalQueries';
import type {
  ActiveAlert,
  ActiveEncounterItem,
  Recommendation,
} from '@/types/api';
import { asBoolean, asString } from '@/utils/coerce';
import {
  formatAircraftLabel,
  formatBoolean,
  formatCount,
  formatRiskScore,
  formatUtcTime,
  humaniseToken,
  NOT_REPORTED,
} from '@/utils/format';
import {
  presentAlertState,
  presentConfidence,
  presentEncounterState,
  presentRecommendationAction,
  presentOverlapStatus,
  presentRiskLevel,
} from '@/utils/status';

import styles from './OperationalWorklist.module.css';
import {
  EMPTY_WORKLIST_FILTERS,
  alertIsSelected,
  alertRowKey,
  describeLoadedVersusCurrent,
  encounterIsSelected,
  encounterRowKey,
  filterAlerts,
  filterEncounters,
  filterRecommendations,
  lookupCallsign,
  recommendationIsSelected,
  recommendationRowKey,
  selectionFromAlert,
  selectionFromEncounter,
  selectionFromRecommendation,
  sortAlerts,
  sortEncounters,
  sortRecommendations,
  uniqueTokens,
  type WorklistFilters,
  type WorklistSortDirection,
  type WorklistSortKey,
  type WorklistTab,
} from './worklist';

export interface OperationalWorklistProps {
  selected: ContextSelection | null;
  onSelect: (selection: ContextSelection) => void;
  /** Authoritative current-set total from `/overview`. Not the loaded page. */
  currentEncounterCount?: number | null;
  currentAlertCount?: number | null;
  currentRecommendationCount?: number | null;
}

const SORT_OPTIONS: Array<{ key: WorklistSortKey; label: string }> = [
  { key: 'attention', label: 'Attention' },
  { key: 'timestamp', label: 'Newest' },
  { key: 'riskLevel', label: 'Risk level' },
  { key: 'aircraft', label: 'Aircraft' },
  { key: 'hazard', label: 'Hazard' },
];

/**
 * Current encounters, alerts and recommendations as an operator worklist.
 *
 * Rows come from the current-set list routes. This panel only presents,
 * filters and sorts stored fields.
 */
export function OperationalWorklist({
  selected,
  onSelect,
  currentEncounterCount,
  currentAlertCount,
  currentRecommendationCount,
}: OperationalWorklistProps) {
  const encountersQuery = useCurrentEncounters();
  const alertsQuery = useCurrentAlerts();
  const [tab, setTab] = useState<WorklistTab>('encounters');
  const recommendationsQuery = useCurrentRecommendations({
    enabled: tab === 'recommendations',
  });
  const aircraft = useAircraftLayerData();
  const aircraftById = aircraft.result.aircraftById;

  const [filters, setFilters] = useState<WorklistFilters>(EMPTY_WORKLIST_FILTERS);
  const [sortKey, setSortKey] = useState<WorklistSortKey>('attention');
  const [sortDirection, setSortDirection] =
    useState<WorklistSortDirection>('desc');

  const encounterItems = useMemo(
    () =>
      (encountersQuery.data?.pages ?? []).flatMap((page) => page.items),
    [encountersQuery.data?.pages],
  );
  const alertItems = useMemo(
    () => (alertsQuery.data?.pages ?? []).flatMap((page) => page.items),
    [alertsQuery.data?.pages],
  );
  const recommendationItems = useMemo(
    () =>
      (recommendationsQuery.data?.pages ?? []).flatMap((page) => page.items),
    [recommendationsQuery.data?.pages],
  );

  const visibleEncounters = useMemo(
    () =>
      sortEncounters(
        filterEncounters(encounterItems, filters, aircraftById),
        sortKey,
        sortDirection,
      ),
    [aircraftById, encounterItems, filters, sortKey, sortDirection],
  );
  const visibleAlerts = useMemo(
    () =>
      sortAlerts(
        filterAlerts(alertItems, filters, aircraftById),
        sortKey,
        sortDirection,
      ),
    [aircraftById, alertItems, filters, sortKey, sortDirection],
  );
  const visibleRecommendations = useMemo(
    () =>
      sortRecommendations(
        filterRecommendations(recommendationItems, filters, aircraftById),
        sortKey,
        sortDirection,
      ),
    [aircraftById, recommendationItems, filters, sortKey, sortDirection],
  );

  const encounterStates = useMemo(
    () =>
      uniqueTokens(
        encounterItems.map((item) => item.encounter?.encounter_state),
      ),
    [encounterItems],
  );
  const alertStates = useMemo(
    () => uniqueTokens(alertItems.map((item) => item.alert_state)),
    [alertItems],
  );
  const recommendationActions = useMemo(
    () =>
      uniqueTokens(
        recommendationItems.map((item) => item.primary_action_type),
      ),
    [recommendationItems],
  );
  const riskOptions = useMemo(
    () =>
      uniqueTokens(
        tab === 'encounters'
          ? encounterItems.map((item) => item.risk?.risk_level)
          : tab === 'alerts'
            ? alertItems.map((item) => item.risk_level)
            : recommendationItems.map((item) => item.risk_level),
      ),
    [alertItems, encounterItems, recommendationItems, tab],
  );

  const activeQuery =
    tab === 'encounters'
      ? encountersQuery
      : tab === 'alerts'
        ? alertsQuery
        : recommendationsQuery;
  const loadedCount =
    tab === 'encounters'
      ? encounterItems.length
      : tab === 'alerts'
        ? alertItems.length
        : recommendationItems.length;
  const loading = activeQuery.isPending && activeQuery.data === undefined;
  const failed = activeQuery.isError && activeQuery.data === undefined;
  const stale = activeQuery.isError && activeQuery.data !== undefined;
  const hasMore = activeQuery.hasNextPage === true;

  const stateOptions =
    tab === 'encounters'
      ? encounterStates
      : tab === 'alerts'
        ? alertStates
        : recommendationActions;
  const currentTotal =
    tab === 'encounters'
      ? currentEncounterCount
      : tab === 'alerts'
        ? currentAlertCount
        : currentRecommendationCount;
  const filtersActive =
    filters.riskLevel !== '' ||
    filters.state !== '' ||
    filters.aircraft.trim() !== '' ||
    filters.hazard.trim() !== '';
  const selectedInLoaded =
    selected != null &&
    (tab === 'encounters'
      ? encounterItems.some((item) => encounterIsSelected(selected, item))
      : tab === 'alerts'
        ? alertItems.some((item) => alertIsSelected(selected, item))
        : recommendationItems.some((item) =>
            recommendationIsSelected(selected, item),
          ));
  const selectedSourceMatchesTab =
    selected?.source === 'encounter'
      ? tab === 'encounters'
      : selected?.source === 'alert'
        ? tab === 'alerts'
        : selected?.source === 'recommendation'
          ? tab === 'recommendations'
          : false;

  return (
    <Panel
      title="Current attention"
      meta={
        failed || loading
          ? undefined
          : describeLoadedVersusCurrent(loadedCount, currentTotal)
      }
      actions={
        <div className={styles.tabs} role="tablist" aria-label="Worklist source">
          <TabButton
            active={tab === 'encounters'}
            onClick={() => {
              setTab('encounters');
              setFilters((current) => ({ ...current, state: '', riskLevel: '' }));
            }}
          >
            Encounters
            {currentEncounterCount != null
              ? ` ${formatCount(currentEncounterCount)}`
              : ''}
          </TabButton>
          <TabButton
            active={tab === 'alerts'}
            onClick={() => {
              setTab('alerts');
              setFilters((current) => ({ ...current, state: '', riskLevel: '' }));
            }}
          >
            Alerts
            {currentAlertCount != null ? ` ${formatCount(currentAlertCount)}` : ''}
          </TabButton>
          <TabButton
            active={tab === 'recommendations'}
            onClick={() => {
              setTab('recommendations');
              setFilters((current) => ({ ...current, state: '', riskLevel: '' }));
            }}
          >
            Recommendations
            {currentRecommendationCount != null
              ? ` ${formatCount(currentRecommendationCount)}`
              : ''}
          </TabButton>
        </div>
      }
    >
      <div className={styles.toolbar}>
        <label className={styles.field}>
          <span>Risk</span>
          <select
            value={filters.riskLevel}
            onChange={(event) =>
              setFilters((current) => ({
                ...current,
                riskLevel: event.target.value,
              }))
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
          <span>{tab === 'recommendations' ? 'Action' : 'State'}</span>
          <select
            value={filters.state}
            onChange={(event) =>
              setFilters((current) => ({
                ...current,
                state: event.target.value,
              }))
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
            placeholder="id or callsign"
            onChange={(event) =>
              setFilters((current) => ({
                ...current,
                aircraft: event.target.value,
              }))
            }
          />
        </label>

        <label className={styles.field}>
          <span>Hazard</span>
          <input
            type="search"
            value={filters.hazard}
            placeholder="hazard id or type"
            onChange={(event) =>
              setFilters((current) => ({
                ...current,
                hazard: event.target.value,
              }))
            }
          />
        </label>

        <label className={styles.field}>
          <span>Sort</span>
          <select
            value={sortKey}
            onChange={(event) =>
              setSortKey(event.target.value as WorklistSortKey)
            }
          >
            {SORT_OPTIONS.map((option) => (
              <option key={option.key} value={option.key}>
                {option.label}
              </option>
            ))}
          </select>
        </label>

        <label className={styles.field}>
          <span>Order</span>
          <select
            value={sortDirection}
            onChange={(event) =>
              setSortDirection(event.target.value as WorklistSortDirection)
            }
          >
            <option value="desc">Newest / highest first</option>
            <option value="asc">Oldest / lowest first</option>
          </select>
        </label>
      </div>

      {filtersActive ? (
        <Notice>
          Filters apply to the loaded page only. They do not search unloaded
          current rows.
        </Notice>
      ) : null}

      {stale ? (
        <Notice tone="warning">
          Showing the last successful current {tab} page. The most recent
          update failed: {describeApiError(activeQuery.error)}
        </Notice>
      ) : null}

      {selected != null &&
      selectedSourceMatchesTab &&
      !selectedInLoaded &&
      !loading &&
      !failed ? (
        <Notice tone="warning">
          {hasMore
            ? 'The selected record is not in the loaded current page. It may sit on an unloaded page or may no longer be current.'
            : 'The selected record is no longer in the current operational set. Aircraft Investigation was not switched to another item.'}
        </Notice>
      ) : null}

      {loading ? (
        <LoadingState
          label={
            tab === 'encounters'
              ? 'Loading current encounters'
              : tab === 'alerts'
                ? 'Loading current alerts'
                : 'Loading current recommendations'
          }
        />
      ) : null}

      {failed ? (
        <EmptyState
          title={
            tab === 'encounters'
              ? 'Current encounters unavailable'
              : tab === 'alerts'
                ? 'Current alerts unavailable'
                : 'Current recommendations unavailable'
          }
          detail={describeApiError(activeQuery.error)}
        >
          <button
            type="button"
            className={styles.more}
            onClick={() => void activeQuery.refetch()}
          >
            Retry
          </button>
        </EmptyState>
      ) : null}

      {!loading && !failed && tab === 'encounters' && visibleEncounters.length === 0 ? (
        <EmptyState
          title={
            encounterItems.length === 0
              ? 'No current encounters'
              : 'No encounters match these filters'
          }
          detail={
            encounterItems.length === 0
              ? 'No DETECTED or MONITORING encounter is supported by a current projection and hazard version.'
              : 'Loaded current encounters remain; the filters hid every row.'
          }
        />
      ) : null}

      {!loading && !failed && tab === 'alerts' && visibleAlerts.length === 0 ? (
        <EmptyState
          title={
            alertItems.length === 0
              ? 'No current alerts'
              : 'No alerts match these filters'
          }
          detail={
            alertItems.length === 0
              ? 'No alert is tied to a current risk or current recommendation.'
              : 'Loaded current alerts remain; the filters hid every row.'
          }
        />
      ) : null}

      {!loading &&
      !failed &&
      tab === 'recommendations' &&
      visibleRecommendations.length === 0 ? (
        <EmptyState
          title={
            recommendationItems.length === 0
              ? 'No current recommendations'
              : 'No recommendations match these filters'
          }
          detail={
            recommendationItems.length === 0
              ? 'No advisory recommendation is tied to a current risk.'
              : 'Loaded current recommendations remain; the filters hid every row.'
          }
        />
      ) : null}

      {!loading && !failed && tab === 'encounters' && visibleEncounters.length > 0 ? (
        <ul className={styles.list}>
          {visibleEncounters.map((item, index) => {
            const key = encounterRowKey(item, index);
            const selection = selectionFromEncounter(item);

            return (
              <EncounterRow
                key={key}
                item={item}
                selected={encounterIsSelected(selected, item)}
                callsign={lookupCallsign(item.encounter?.aircraft_id, aircraftById)}
                disabled={selection === null}
                onSelect={() => {
                  if (selection) {
                    onSelect(selection);
                  }
                }}
              />
            );
          })}
        </ul>
      ) : null}

      {!loading && !failed && tab === 'alerts' && visibleAlerts.length > 0 ? (
        <ul className={styles.list}>
          {visibleAlerts.map((item, index) => {
            const key = alertRowKey(item, index);
            const selection = selectionFromAlert(item);

            return (
              <AlertRow
                key={key}
                alert={item}
                selected={alertIsSelected(selected, item)}
                callsign={lookupCallsign(item.aircraft_id, aircraftById)}
                disabled={selection === null}
                onSelect={() => {
                  if (selection) {
                    onSelect(selection);
                  }
                }}
              />
            );
          })}
        </ul>
      ) : null}

      {!loading &&
      !failed &&
      tab === 'recommendations' &&
      visibleRecommendations.length > 0 ? (
        <ul className={styles.list}>
          {visibleRecommendations.map((item, index) => {
            const key = recommendationRowKey(item, index);
            const selection = selectionFromRecommendation(item);

            return (
              <RecommendationRow
                key={key}
                recommendation={item}
                selected={recommendationIsSelected(selected, item)}
                callsign={lookupCallsign(item.aircraft_id, aircraftById)}
                disabled={selection === null}
                onSelect={() => {
                  if (selection) {
                    onSelect(selection);
                  }
                }}
              />
            );
          })}
        </ul>
      ) : null}

      {activeQuery.isFetchNextPageError ? (
        <Notice tone="warning">
          The next current page could not be loaded.{' '}
          {describeApiError(activeQuery.error)}
        </Notice>
      ) : null}

      {!loading && !failed && hasMore ? (
        <div className={styles.footer}>
          <Notice>
            Loaded {formatCount(loadedCount)} current {tab}. More current rows
            exist beyond this page.
          </Notice>
          <button
            type="button"
            className={styles.more}
            disabled={activeQuery.isFetchingNextPage}
            onClick={() => void activeQuery.fetchNextPage()}
          >
            {activeQuery.isFetchingNextPage
              ? 'Loading next page…'
              : 'Load more current rows'}
          </button>
        </div>
      ) : null}
    </Panel>
  );
}

function TabButton({
  active,
  onClick,
  children,
}: {
  active: boolean;
  onClick: () => void;
  children: ReactNode;
}) {
  return (
    <button
      type="button"
      role="tab"
      aria-selected={active}
      className={`${styles.tab} ${active ? styles.tabActive : ''}`}
      onClick={onClick}
    >
      {children}
    </button>
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
  const geometry = presentOverlapStatus(encounter?.geometry_overlap_status);

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
          <StatusPill
            size="sm"
            prefix="Risk"
            presentation={presentRiskLevel(risk?.risk_level)}
          />
          <span className={`${styles.score} wv-numeric`}>
            {formatRiskScore(risk?.risk_score)}
            <span className={styles.scoreMax}>/100</span>
          </span>
          <StatusPill
            size="sm"
            presentation={presentEncounterState(encounter?.encounter_state)}
          />
          {selected ? (
            <span className={styles.selectedMark}>Selected</span>
          ) : null}
          <span className={`${styles.timestamp} wv-numeric`}>
            {formatUtcTime(encounter?.detected_at_utc)}
          </span>
        </div>

        <div className={styles.rowBody}>
          <span className={styles.primary}>
            {formatAircraftLabel(callsign, encounter?.aircraft_id)}
          </span>
          <span className={styles.separator} aria-hidden="true">
            ·
          </span>
          <span className={styles.secondary}>
            {humaniseToken(encounter?.hazard_type)}
          </span>
          <span className={styles.separator} aria-hidden="true">
            ·
          </span>
          <span className={`${styles.secondary} wv-numeric`}>
            {asString(encounter?.hazard_id) ?? NOT_REPORTED}
          </span>
          <span className={styles.separator} aria-hidden="true">
            ·
          </span>
          <span className={styles.secondary}>
            Inside now {formatBoolean(asBoolean(encounter?.inside_now))}
          </span>
          <span className={styles.separator} aria-hidden="true">
            ·
          </span>
          <span className={styles.secondary}>
            Geometry {geometry.label}
          </span>
          <span className={styles.separator} aria-hidden="true">
            ·
          </span>
          <span className={styles.secondary}>
            Confidence {presentConfidence(risk?.confidence).label}
          </span>
        </div>
      </button>
      {disabled ? (
        <p className={styles.rowNote}>
          This current encounter has no aircraft id, so it cannot open
          investigation.
        </p>
      ) : null}
    </li>
  );
}

function AlertRow({
  alert,
  selected,
  callsign,
  disabled,
  onSelect,
}: {
  alert: ActiveAlert;
  selected: boolean;
  callsign: string | null;
  disabled: boolean;
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
          <StatusPill
            size="sm"
            prefix="Risk"
            presentation={presentRiskLevel(alert.risk_level)}
          />
          <span className={`${styles.score} wv-numeric`}>
            {formatRiskScore(alert.risk_score)}
            <span className={styles.scoreMax}>/100</span>
          </span>
          <StatusPill
            size="sm"
            prefix="Alert"
            presentation={presentAlertState(alert.alert_state)}
          />
          {selected ? (
            <span className={styles.selectedMark}>Selected</span>
          ) : null}
          <span className={`${styles.timestamp} wv-numeric`}>
            {formatUtcTime(alert.updated_at_utc)}
          </span>
        </div>

        <div className={styles.rowBody}>
          <span className={styles.primary}>
            {formatAircraftLabel(callsign, alert.aircraft_id)}
          </span>
          <span className={styles.separator} aria-hidden="true">
            ·
          </span>
          <span className={`${styles.secondary} wv-numeric`}>
            {asString(alert.hazard_id) ?? NOT_REPORTED}
          </span>
          <span className={styles.separator} aria-hidden="true">
            ·
          </span>
          <span className={styles.secondary}>
            {asString(alert.message) ?? NOT_REPORTED}
          </span>
        </div>
      </button>
      {disabled ? (
        <p className={styles.rowNote}>
          This current alert has no aircraft id, so it cannot open
          investigation.
        </p>
      ) : null}
    </li>
  );
}

function RecommendationRow({
  recommendation,
  selected,
  callsign,
  disabled,
  onSelect,
}: {
  recommendation: Recommendation;
  selected: boolean;
  callsign: string | null;
  disabled: boolean;
  onSelect: () => void;
}) {
  const preferredAirport = asString(recommendation.preferred_airport_id);

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
          <StatusPill
            size="sm"
            prefix="Risk"
            presentation={presentRiskLevel(recommendation.risk_level)}
          />
          <span className={`${styles.score} wv-numeric`}>
            {formatRiskScore(recommendation.risk_score)}
            <span className={styles.scoreMax}>/100</span>
          </span>
          <StatusPill
            size="sm"
            presentation={presentRecommendationAction(
              recommendation.primary_action_type,
            )}
          />
          {selected ? (
            <span className={styles.selectedMark}>Selected</span>
          ) : null}
          <span className={`${styles.timestamp} wv-numeric`}>
            {formatUtcTime(recommendation.created_at_utc)}
          </span>
        </div>

        <div className={styles.rowBody}>
          <span className={styles.primary}>
            {formatAircraftLabel(callsign, recommendation.aircraft_id)}
          </span>
          <span className={styles.separator} aria-hidden="true">
            ·
          </span>
          <span className={`${styles.secondary} wv-numeric`}>
            {asString(recommendation.hazard_id) ?? NOT_REPORTED}
          </span>
          <span className={styles.separator} aria-hidden="true">
            ·
          </span>
          <span className={styles.secondary}>
            {preferredAirport
              ? `Preferred ${preferredAirport}`
              : 'No preferred airport'}
          </span>
          <span className={styles.separator} aria-hidden="true">
            ·
          </span>
          <span className={styles.secondary}>
            Confidence {presentConfidence(recommendation.confidence).label}
          </span>
        </div>
      </button>
      {disabled ? (
        <p className={styles.rowNote}>
          This current recommendation has no aircraft id, so it cannot open
          investigation.
        </p>
      ) : null}
    </li>
  );
}

