import type { ActiveAlert } from '@/types/api';
import { asString } from '@/utils/coerce';

import {
  type CallsignLookup,
  filterAlerts,
  lookupCallsign,
  alertRowKey,
  selectionFromAlert,
  sortAlerts,
} from '@/features/worklist/worklist';

/**
 * Presentation helpers for the dedicated Current Alerts page.
 *
 * Filtering and sorting operate on pages already returned by
 * `GET /alerts/active`. They do not recreate current-set membership.
 */

export type AlertSortKey = 'newest' | 'risk' | 'state' | 'aircraft' | 'hazard';

export interface AlertFilters {
  state: string;
  riskLevel: string;
  aircraft: string;
  hazard: string;
  action: string;
}

export const EMPTY_ALERT_FILTERS: AlertFilters = {
  state: '',
  riskLevel: '',
  aircraft: '',
  hazard: '',
  action: '',
};

export const ALERT_STATE_KPI = {
  all: '',
  new: 'NEW',
  updated: 'UPDATED',
  escalated: 'ESCALATED',
  monitoring: 'MONITORING',
} as const;

export type AlertStateKpi = (typeof ALERT_STATE_KPI)[keyof typeof ALERT_STATE_KPI];

export function withAlertState(
  filters: AlertFilters,
  state: string,
): AlertFilters {
  return { ...filters, state };
}

export function matchesAlertStateFilter(
  item: ActiveAlert,
  state: string,
): boolean {
  if (state.length === 0) {
    return true;
  }

  return asString(item.alert_state)?.toUpperCase() === state.toUpperCase();
}

export type AlertSelectionStatus =
  | { readonly status: 'none' }
  | { readonly status: 'current'; readonly item: ActiveAlert }
  | { readonly status: 'resolved'; readonly alertId: string }
  | { readonly status: 'unloaded'; readonly alertId: string };

export function alertIdOf(item: ActiveAlert): string | null {
  return asString(item.alert_id);
}

export function alertAircraftId(item: ActiveAlert): string | null {
  return asString(item.aircraft_id);
}

export function alertHazardId(item: ActiveAlert): string | null {
  return asString(item.hazard_id);
}

export function uniqueAlertIds(
  items: readonly ActiveAlert[],
  pick: (item: ActiveAlert) => string | null,
): string[] {
  const ids: string[] = [];
  const seen = new Set<string>();

  for (const item of items) {
    const id = pick(item);

    if (id === null || seen.has(id)) {
      continue;
    }

    seen.add(id);
    ids.push(id);
  }

  return ids;
}

export function loadedAlertAircraftIds(items: readonly ActiveAlert[]): string[] {
  return uniqueAlertIds(items, alertAircraftId);
}

export function loadedAlertHazardIds(items: readonly ActiveAlert[]): string[] {
  return uniqueAlertIds(items, alertHazardId);
}

export function recordSeenAlertIds(
  seen: Set<string>,
  items: readonly ActiveAlert[],
): void {
  for (const item of items) {
    const id = alertIdOf(item);

    if (id !== null) {
      seen.add(id);
    }
  }
}

export function resolveAlertSelection(
  alertId: string | null,
  loaded: readonly ActiveAlert[],
  previouslySeen: ReadonlySet<string>,
): AlertSelectionStatus {
  const id = asString(alertId);

  if (id === null) {
    return { status: 'none' };
  }

  const current = loaded.find((item) => alertIdOf(item) === id);

  if (current) {
    return { status: 'current', item: current };
  }

  if (previouslySeen.has(id)) {
    return { status: 'resolved', alertId: id };
  }

  return { status: 'unloaded', alertId: id };
}

export function filterCurrentAlerts(
  items: readonly ActiveAlert[],
  filters: AlertFilters,
  aircraftById?: CallsignLookup,
): ActiveAlert[] {
  return filterAlerts(
    items,
    {
      riskLevel: filters.riskLevel,
      state: filters.state,
      aircraft: filters.aircraft,
      hazard: filters.hazard,
    },
    aircraftById,
  ).filter((item) => {
    if (filters.action.length === 0) {
      return true;
    }

    return (
      asString(item.primary_action_type)?.toUpperCase() ===
      filters.action.toUpperCase()
    );
  });
}

export function sortCurrentAlerts(
  items: readonly ActiveAlert[],
  sortKey: AlertSortKey,
): ActiveAlert[] {
  if (sortKey === 'newest') {
    return sortAlerts(items, 'timestamp', 'desc');
  }

  if (sortKey === 'risk') {
    return sortAlerts(items, 'attention', 'desc');
  }

  if (sortKey === 'state') {
    return sortAlerts(items, 'state', 'asc');
  }

  if (sortKey === 'aircraft') {
    return sortAlerts(items, 'aircraft', 'asc');
  }

  return sortAlerts(items, 'hazard', 'asc');
}

export function visibleCurrentAlerts(
  items: readonly ActiveAlert[],
  filters: AlertFilters,
  sortKey: AlertSortKey,
  aircraftById?: CallsignLookup,
): ActiveAlert[] {
  return sortCurrentAlerts(
    filterCurrentAlerts(items, filters, aircraftById),
    sortKey,
  );
}

/**
 * Loaded current alerts for one aircraft.
 *
 * Order is hazard id, then alert id. Recency and risk are not used.
 */
function sameAircraftId(left: string | null, right: string): boolean {
  return left !== null && left.toLowerCase() === right.toLowerCase();
}

export function loadedAlertsForAircraft(
  items: readonly ActiveAlert[],
  aircraftId: string,
): ActiveAlert[] {
  return items
    .filter((item) => sameAircraftId(alertAircraftId(item), aircraftId))
    .filter((item) => alertIdOf(item) !== null)
    .sort((left, right) => {
      const hazard = (alertHazardId(left) ?? '').localeCompare(
        alertHazardId(right) ?? '',
      );

      if (hazard !== 0) {
        return hazard;
      }

      return (alertIdOf(left) ?? '').localeCompare(alertIdOf(right) ?? '');
    });
}

export type MapAircraftAlertChoice =
  | { readonly kind: 'none' }
  | { readonly kind: 'single'; readonly item: ActiveAlert }
  | { readonly kind: 'multiple'; readonly items: ActiveAlert[] };

export function resolveMapAircraftAlertClick(
  items: readonly ActiveAlert[],
  aircraftId: string,
): MapAircraftAlertChoice {
  const matches = loadedAlertsForAircraft(items, aircraftId);

  if (matches.length === 0) {
    return { kind: 'none' };
  }

  if (matches.length === 1 && matches[0]) {
    return { kind: 'single', item: matches[0] };
  }

  return { kind: 'multiple', items: matches };
}

export function countLoadedAlertStates(items: readonly ActiveAlert[]): {
  new: number;
  updated: number;
  escalated: number;
  monitoring: number;
} {
  let next = 0;
  let updated = 0;
  let escalated = 0;
  let monitoring = 0;

  for (const item of items) {
    switch (asString(item.alert_state)?.toUpperCase()) {
      case 'NEW':
        next += 1;
        break;
      case 'UPDATED':
        updated += 1;
        break;
      case 'ESCALATED':
        escalated += 1;
        break;
      case 'MONITORING':
        monitoring += 1;
        break;
      default:
        break;
    }
  }

  return { new: next, updated, escalated, monitoring };
}

export function describeLoadedAlertScope(items: readonly ActiveAlert[]): string {
  const aircraft = loadedAlertAircraftIds(items).length;
  const hazards = loadedAlertHazardIds(items).length;

  return `${aircraft.toLocaleString('en-US')} aircraft · ${hazards.toLocaleString('en-US')} hazards on loaded pages`;
}

export function pickAlertForHazard(
  items: readonly ActiveAlert[],
  hazardId: string,
  selectedAlertId: string | null,
): ActiveAlert | null {
  const matches = items.filter((item) => alertHazardId(item) === hazardId);

  if (matches.length === 0) {
    return null;
  }

  return (
    matches.find((item) => alertIdOf(item) === selectedAlertId) ??
    matches[0] ??
    null
  );
}

export { lookupCallsign, alertRowKey, selectionFromAlert };
