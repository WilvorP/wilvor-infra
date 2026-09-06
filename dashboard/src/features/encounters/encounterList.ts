import type { ActiveEncounterItem } from '@/types/api';
import { asBoolean, asNumber, asString } from '@/utils/coerce';

import {
  type CallsignLookup,
  type WorklistFilters,
  encounterRowKey,
  filterEncounters,
  lookupCallsign,
  selectionFromEncounter,
  sortEncounters,
} from '@/features/worklist/worklist';

/**
 * Presentation helpers for the dedicated Current Encounters page.
 *
 * Filtering and sorting operate on pages already returned by
 * `GET /encounters/active`. They do not recreate current-set membership,
 * score risk, or infer altitude overlap from geometry.
 */

export type EncounterSortKey =
  | 'attention'
  | 'newest'
  | 'aircraft'
  | 'hazard'
  | 'insideNow';

export interface EncounterFilters extends WorklistFilters {
  insideNow: '' | 'yes' | 'no';
  altitude: string;
}

export const EMPTY_ENCOUNTER_FILTERS: EncounterFilters = {
  riskLevel: '',
  state: '',
  aircraft: '',
  hazard: '',
  insideNow: '',
  altitude: '',
};

export const ENCOUNTER_RISK_KPI = {
  all: '',
  low: 'LOW',
  medium: 'MEDIUM',
  high: 'HIGH',
} as const;

export type EncounterRiskKpi =
  (typeof ENCOUNTER_RISK_KPI)[keyof typeof ENCOUNTER_RISK_KPI];

export function withEncounterRisk(
  filters: EncounterFilters,
  riskLevel: string,
): EncounterFilters {
  return { ...filters, riskLevel };
}

export function matchesEncounterRiskFilter(
  item: ActiveEncounterItem,
  riskLevel: string,
): boolean {
  if (riskLevel.length === 0) {
    return true;
  }

  return (
    asString(item.risk?.risk_level)?.toUpperCase() === riskLevel.toUpperCase()
  );
}

export type EncounterSelectionStatus =
  | { readonly status: 'none' }
  | { readonly status: 'current'; readonly item: ActiveEncounterItem }
  | { readonly status: 'resolved'; readonly encounterId: string }
  | { readonly status: 'unloaded'; readonly encounterId: string };

export function encounterIdOf(item: ActiveEncounterItem): string | null {
  return asString(item.encounter?.encounter_id);
}

export function encounterAircraftId(item: ActiveEncounterItem): string | null {
  return asString(item.encounter?.aircraft_id);
}

export function encounterHazardId(item: ActiveEncounterItem): string | null {
  return asString(item.encounter?.hazard_id);
}

export function uniqueEncounterIds(
  items: readonly ActiveEncounterItem[],
  pick: (item: ActiveEncounterItem) => string | null,
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

export function loadedEncounterAircraftIds(
  items: readonly ActiveEncounterItem[],
): string[] {
  return uniqueEncounterIds(items, encounterAircraftId);
}

export function loadedEncounterHazardIds(
  items: readonly ActiveEncounterItem[],
): string[] {
  return uniqueEncounterIds(items, encounterHazardId);
}

export function recordSeenEncounterIds(
  seen: Set<string>,
  items: readonly ActiveEncounterItem[],
): void {
  for (const item of items) {
    const id = encounterIdOf(item);

    if (id !== null) {
      seen.add(id);
    }
  }
}

/**
 * Resolve a URL encounter id against loaded current pages.
 *
 * A previously seen id that is no longer in the loaded current set is
 * treated as resolved. An id that was never loaded is not silently replaced.
 */
export function resolveEncounterSelection(
  encounterId: string | null,
  loaded: readonly ActiveEncounterItem[],
  previouslySeen: ReadonlySet<string>,
): EncounterSelectionStatus {
  const id = asString(encounterId);

  if (id === null) {
    return { status: 'none' };
  }

  const current = loaded.find((item) => encounterIdOf(item) === id);

  if (current) {
    return { status: 'current', item: current };
  }

  if (previouslySeen.has(id)) {
    return { status: 'resolved', encounterId: id };
  }

  return { status: 'unloaded', encounterId: id };
}

export function filterCurrentEncounters(
  items: readonly ActiveEncounterItem[],
  filters: EncounterFilters,
  aircraftById?: CallsignLookup,
): ActiveEncounterItem[] {
  return filterEncounters(items, filters, aircraftById).filter((item) => {
    if (filters.insideNow === 'yes' && item.encounter?.inside_now !== true) {
      return false;
    }

    if (filters.insideNow === 'no' && item.encounter?.inside_now !== false) {
      return false;
    }

    if (filters.altitude.length > 0) {
      const altitude = asString(item.encounter?.altitude_overlap_status);

      if (altitude?.toUpperCase() !== filters.altitude.toUpperCase()) {
        return false;
      }
    }

    return true;
  });
}

function compareInsideNow(left: unknown, right: unknown): number {
  const a = asBoolean(left);
  const b = asBoolean(right);
  const rank = (value: boolean | null): number => {
    if (value === true) {
      return 2;
    }

    if (value === false) {
      return 1;
    }

    return 0;
  };

  return rank(a) - rank(b);
}

export function sortCurrentEncounters(
  items: readonly ActiveEncounterItem[],
  sortKey: EncounterSortKey,
): ActiveEncounterItem[] {
  if (sortKey === 'attention') {
    return sortEncounters(items, 'attention', 'desc');
  }

  if (sortKey === 'newest') {
    return sortEncounters(items, 'timestamp', 'desc');
  }

  if (sortKey === 'aircraft') {
    return sortEncounters(items, 'aircraft', 'asc');
  }

  if (sortKey === 'hazard') {
    return sortEncounters(items, 'hazard', 'asc');
  }

  return [...items].sort((left, right) => {
    const inside = compareInsideNow(
      left.encounter?.inside_now,
      right.encounter?.inside_now,
    );

    if (inside !== 0) {
      return -inside;
    }

    return (
      (asNumber(right.encounter?.detected_at_epoch) ?? 0) -
      (asNumber(left.encounter?.detected_at_epoch) ?? 0)
    );
  });
}

export function visibleCurrentEncounters(
  items: readonly ActiveEncounterItem[],
  filters: EncounterFilters,
  sortKey: EncounterSortKey,
  aircraftById?: CallsignLookup,
): ActiveEncounterItem[] {
  return sortCurrentEncounters(
    filterCurrentEncounters(items, filters, aircraftById),
    sortKey,
  );
}

/**
 * Loaded current encounters for one aircraft.
 *
 * Order is hazard id, then encounter id. Recency and risk are not used, so a
 * map click cannot imply "newest" or "highest attention" as a default pick.
 */
export function loadedEncountersForAircraft(
  items: readonly ActiveEncounterItem[],
  aircraftId: string,
): ActiveEncounterItem[] {
  return items
    .filter((item) => encounterAircraftId(item) === aircraftId)
    .filter((item) => encounterIdOf(item) !== null)
    .sort((left, right) => {
      const hazard = (encounterHazardId(left) ?? '').localeCompare(
        encounterHazardId(right) ?? '',
      );

      if (hazard !== 0) {
        return hazard;
      }

      return (encounterIdOf(left) ?? '').localeCompare(encounterIdOf(right) ?? '');
    });
}

export type MapAircraftEncounterChoice =
  | { readonly kind: 'none' }
  | { readonly kind: 'single'; readonly item: ActiveEncounterItem }
  | { readonly kind: 'multiple'; readonly items: ActiveEncounterItem[] };

/**
 * Map-marker click against loaded current encounters.
 *
 * One match is unambiguous. Several matches stay unresolved until the
 * operator chooses an `encounter_id`.
 */
export function resolveMapAircraftClick(
  items: readonly ActiveEncounterItem[],
  aircraftId: string,
): MapAircraftEncounterChoice {
  const matches = loadedEncountersForAircraft(items, aircraftId);

  if (matches.length === 0) {
    return { kind: 'none' };
  }

  if (matches.length === 1 && matches[0]) {
    return { kind: 'single', item: matches[0] };
  }

  return { kind: 'multiple', items: matches };
}

/** Prefer the current selection when it already belongs to this aircraft. */
export function pickEncounterForAircraft(
  items: readonly ActiveEncounterItem[],
  aircraftId: string,
  selectedEncounterId: string | null,
): ActiveEncounterItem | null {
  const matches = loadedEncountersForAircraft(items, aircraftId);

  if (matches.length === 0) {
    return null;
  }

  return (
    matches.find((item) => encounterIdOf(item) === selectedEncounterId) ??
    matches[0] ??
    null
  );
}

/** Prefer the current selection when it already belongs to this hazard. */
export function pickEncounterForHazard(
  items: readonly ActiveEncounterItem[],
  hazardId: string,
  selectedEncounterId: string | null,
): ActiveEncounterItem | null {
  const matches = items.filter((item) => encounterHazardId(item) === hazardId);

  if (matches.length === 0) {
    return null;
  }

  return (
    matches.find((item) => encounterIdOf(item) === selectedEncounterId) ??
    matches[0] ??
    null
  );
}

export function describeLoadedEncounterScope(
  items: readonly ActiveEncounterItem[],
): string {
  const aircraft = loadedEncounterAircraftIds(items).length;
  const hazards = loadedEncounterHazardIds(items).length;

  return `${aircraft.toLocaleString('en-US')} aircraft · ${hazards.toLocaleString('en-US')} hazards on loaded pages`;
}

export {
  encounterRowKey,
  lookupCallsign,
  selectionFromEncounter,
};
