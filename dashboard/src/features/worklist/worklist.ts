import type {
  ActiveAlert,
  ActiveEncounterItem,
  Recommendation,
} from '@/types/api';
import { asNumber, asString } from '@/utils/coerce';
import { riskRank } from '@/utils/status';

import type { ContextSelection } from '@/features/aircraft/investigation';

/**
 * Presentation helpers for the current encounters + alerts worklist.
 *
 * Filtering and sorting read stored attributes only. Nothing here scores
 * risk, invents state, or decides whether a row is current — both list
 * endpoints already return the current operational set.
 */

export type WorklistTab = 'encounters' | 'alerts' | 'recommendations';

export type WorklistSortKey =
  | 'attention'
  | 'riskLevel'
  | 'state'
  | 'aircraft'
  | 'hazard'
  | 'timestamp';

export type WorklistSortDirection = 'asc' | 'desc';

export interface WorklistFilters {
  riskLevel: string;
  state: string;
  aircraft: string;
  hazard: string;
}

export const EMPTY_WORKLIST_FILTERS: WorklistFilters = {
  riskLevel: '',
  state: '',
  aircraft: '',
  hazard: '',
};

export function selectionFromEncounter(
  item: ActiveEncounterItem,
): ContextSelection | null {
  const aircraftId = asString(item.encounter?.aircraft_id);

  if (aircraftId === null) {
    return null;
  }

  return {
    aircraftId,
    hazardId: asString(item.encounter?.hazard_id),
    encounterId: asString(item.encounter?.encounter_id),
    riskId: asString(item.risk?.risk_id),
    source: 'encounter',
  };
}

export type CallsignLookup = ReadonlyMap<string, { readonly callsign: string | null }>;

export function selectionFromAlert(alert: ActiveAlert): ContextSelection | null {
  const aircraftId = asString(alert.aircraft_id);

  if (aircraftId === null) {
    return null;
  }

  return {
    aircraftId,
    hazardId: asString(alert.hazard_id),
    riskId: asString(alert.risk_id),
    recommendationId: asString(alert.recommendation_id),
    alertId: asString(alert.alert_id),
    fingerprint: asString(alert.fingerprint),
    source: 'alert',
  };
}

export function selectionFromRecommendation(
  recommendation: Recommendation,
): ContextSelection | null {
  const aircraftId = asString(recommendation.aircraft_id);

  if (aircraftId === null) {
    return null;
  }

  return {
    aircraftId,
    hazardId: asString(recommendation.hazard_id),
    riskId: asString(recommendation.risk_id),
    recommendationId: asString(recommendation.recommendation_id),
    source: 'recommendation',
  };
}

export function lookupCallsign(
  aircraftId: string | null | undefined,
  aircraftById: CallsignLookup | undefined,
): string | null {
  const id = asString(aircraftId);

  if (id === null) {
    return null;
  }

  return asString(aircraftById?.get(id)?.callsign);
}

export function encounterRowKey(
  item: ActiveEncounterItem,
  index: number,
): string {
  return (
    asString(item.encounter?.encounter_id) ??
    asString(item.risk?.risk_id) ??
    `encounter-${index}`
  );
}

export function alertRowKey(alert: ActiveAlert, index: number): string {
  return (
    asString(alert.alert_id) ??
    asString(alert.fingerprint) ??
    `alert-${index}`
  );
}

export function recommendationRowKey(
  recommendation: Recommendation,
  index: number,
): string {
  return (
    asString(recommendation.recommendation_id) ??
    asString(recommendation.risk_id) ??
    `recommendation-${index}`
  );
}

function matchesToken(value: unknown, filter: string): boolean {
  if (filter.length === 0) {
    return true;
  }

  return asString(value)?.toUpperCase() === filter.toUpperCase();
}

function matchesText(value: unknown, filter: string): boolean {
  if (filter.length === 0) {
    return true;
  }

  const haystack = asString(value)?.toLowerCase();

  return haystack != null && haystack.includes(filter.trim().toLowerCase());
}

function matchesAircraft(
  aircraftId: unknown,
  filters: WorklistFilters,
  aircraftById: CallsignLookup | undefined,
): boolean {
  if (filters.aircraft.trim().length === 0) {
    return true;
  }

  return (
    matchesText(aircraftId, filters.aircraft) ||
    matchesText(lookupCallsign(asString(aircraftId), aircraftById), filters.aircraft)
  );
}

function compareText(left: unknown, right: unknown): number {
  const a = asString(left)?.toLowerCase() ?? '';
  const b = asString(right)?.toLowerCase() ?? '';

  return a.localeCompare(b);
}

function compareEpoch(left: unknown, right: unknown): number {
  return (asNumber(left) ?? 0) - (asNumber(right) ?? 0);
}

export function filterEncounters(
  items: readonly ActiveEncounterItem[],
  filters: WorklistFilters,
  aircraftById?: CallsignLookup,
): ActiveEncounterItem[] {
  return items.filter((item) => {
    const hazardNeedle = filters.hazard.trim();
    const hazardMatch =
      hazardNeedle.length === 0 ||
      matchesText(item.encounter?.hazard_id, hazardNeedle) ||
      matchesText(item.encounter?.hazard_type, hazardNeedle);

    return (
      matchesToken(item.risk?.risk_level, filters.riskLevel) &&
      matchesToken(item.encounter?.encounter_state, filters.state) &&
      matchesAircraft(item.encounter?.aircraft_id, filters, aircraftById) &&
      hazardMatch
    );
  });
}

export function filterAlerts(
  items: readonly ActiveAlert[],
  filters: WorklistFilters,
  aircraftById?: CallsignLookup,
): ActiveAlert[] {
  return items.filter((item) => {
    return (
      matchesToken(item.risk_level, filters.riskLevel) &&
      matchesToken(item.alert_state, filters.state) &&
      matchesAircraft(item.aircraft_id, filters, aircraftById) &&
      matchesText(item.hazard_id, filters.hazard)
    );
  });
}

export function filterRecommendations(
  items: readonly Recommendation[],
  filters: WorklistFilters,
  aircraftById?: CallsignLookup,
): Recommendation[] {
  return items.filter((item) => {
    return (
      matchesToken(item.risk_level, filters.riskLevel) &&
      matchesToken(item.primary_action_type, filters.state) &&
      matchesAircraft(item.aircraft_id, filters, aircraftById) &&
      matchesText(item.hazard_id, filters.hazard)
    );
  });
}

export function sortEncounters(
  items: readonly ActiveEncounterItem[],
  sortKey: WorklistSortKey,
  direction: WorklistSortDirection,
): ActiveEncounterItem[] {
  const sign = direction === 'asc' ? 1 : -1;

  return [...items].sort((left, right) => {
    let result = 0;

    switch (sortKey) {
      case 'attention':
        result =
          riskRank(left.risk?.risk_level) - riskRank(right.risk?.risk_level);
        if (result === 0) {
          result = compareEpoch(
            left.encounter?.detected_at_epoch,
            right.encounter?.detected_at_epoch,
          );
        }
        break;
      case 'riskLevel':
        result = riskRank(left.risk?.risk_level) - riskRank(right.risk?.risk_level);
        break;
      case 'state':
        result = compareText(
          left.encounter?.encounter_state,
          right.encounter?.encounter_state,
        );
        break;
      case 'aircraft':
        result = compareText(
          left.encounter?.aircraft_id,
          right.encounter?.aircraft_id,
        );
        break;
      case 'hazard':
        result = compareText(left.encounter?.hazard_id, right.encounter?.hazard_id);
        break;
      case 'timestamp':
        result = compareEpoch(
          left.encounter?.detected_at_epoch,
          right.encounter?.detected_at_epoch,
        );
        break;
      default:
        result = 0;
    }

    return result * sign;
  });
}

export function sortAlerts(
  items: readonly ActiveAlert[],
  sortKey: WorklistSortKey,
  direction: WorklistSortDirection,
): ActiveAlert[] {
  const sign = direction === 'asc' ? 1 : -1;

  return [...items].sort((left, right) => {
    let result = 0;

    switch (sortKey) {
      case 'attention':
        result = riskRank(left.risk_level) - riskRank(right.risk_level);
        if (result === 0) {
          result = compareEpoch(left.updated_at_epoch, right.updated_at_epoch);
        }
        break;
      case 'riskLevel':
        result = riskRank(left.risk_level) - riskRank(right.risk_level);
        break;
      case 'state':
        result = compareText(left.alert_state, right.alert_state);
        break;
      case 'aircraft':
        result = compareText(left.aircraft_id, right.aircraft_id);
        break;
      case 'hazard':
        result = compareText(left.hazard_id, right.hazard_id);
        break;
      case 'timestamp':
        result = compareEpoch(left.updated_at_epoch, right.updated_at_epoch);
        break;
      default:
        result = 0;
    }

    return result * sign;
  });
}

export function sortRecommendations(
  items: readonly Recommendation[],
  sortKey: WorklistSortKey,
  direction: WorklistSortDirection,
): Recommendation[] {
  const sign = direction === 'asc' ? 1 : -1;

  return [...items].sort((left, right) => {
    let result = 0;

    switch (sortKey) {
      case 'attention':
        result = riskRank(left.risk_level) - riskRank(right.risk_level);
        if (result === 0) {
          result = compareEpoch(left.created_at_epoch, right.created_at_epoch);
        }
        break;
      case 'riskLevel':
        result = riskRank(left.risk_level) - riskRank(right.risk_level);
        break;
      case 'state':
        result = compareText(
          left.primary_action_type,
          right.primary_action_type,
        );
        break;
      case 'aircraft':
        result = compareText(left.aircraft_id, right.aircraft_id);
        break;
      case 'hazard':
        result = compareText(left.hazard_id, right.hazard_id);
        break;
      case 'timestamp':
        result = compareEpoch(left.created_at_epoch, right.created_at_epoch);
        break;
      default:
        result = 0;
    }

    return result * sign;
  });
}

export function describeLoadedVersusCurrent(
  loadedCount: number,
  currentCount: number | null | undefined,
): string {
  if (currentCount == null) {
    return `${loadedCount.toLocaleString('en-US')} loaded`;
  }

  return `${loadedCount.toLocaleString('en-US')} loaded of ${currentCount.toLocaleString('en-US')} current`;
}

export function encounterIsSelected(
  selection: ContextSelection | null | undefined,
  item: ActiveEncounterItem,
): boolean {
  if (
    selection == null ||
    selection.source === 'alert' ||
    selection.source === 'recommendation'
  ) {
    return false;
  }

  const encounterId = asString(item.encounter?.encounter_id);
  const riskId = asString(item.risk?.risk_id);

  return (
    (encounterId !== null && encounterId === asString(selection.encounterId)) ||
    (riskId !== null &&
      riskId === asString(selection.riskId) &&
      asString(selection.encounterId) === null)
  );
}

export function alertIsSelected(
  selection: ContextSelection | null | undefined,
  item: ActiveAlert,
): boolean {
  if (
    selection == null ||
    selection.source === 'encounter' ||
    selection.source === 'recommendation'
  ) {
    return false;
  }

  const alertId = asString(item.alert_id);
  const fingerprint = asString(item.fingerprint);
  const recommendationId = asString(item.recommendation_id);
  const riskId = asString(item.risk_id);

  return (
    (alertId !== null && alertId === asString(selection.alertId)) ||
    (fingerprint !== null && fingerprint === asString(selection.fingerprint)) ||
    (recommendationId !== null &&
      recommendationId === asString(selection.recommendationId)) ||
    (riskId !== null && riskId === asString(selection.riskId))
  );
}

export function recommendationIsSelected(
  selection: ContextSelection | null | undefined,
  item: Recommendation,
): boolean {
  if (selection == null || selection.source !== 'recommendation') {
    return false;
  }

  const recommendationId = asString(item.recommendation_id);
  const riskId = asString(item.risk_id);

  return (
    (recommendationId !== null &&
      recommendationId === asString(selection.recommendationId)) ||
    (riskId !== null &&
      riskId === asString(selection.riskId) &&
      asString(selection.recommendationId) === null)
  );
}

export function uniqueTokens(values: readonly unknown[]): string[] {
  const seen = new Set<string>();
  const tokens: string[] = [];

  for (const value of values) {
    const token = asString(value)?.toUpperCase();

    if (token == null || seen.has(token)) {
      continue;
    }

    seen.add(token);
    tokens.push(token);
  }

  return tokens.sort((a, b) => a.localeCompare(b));
}
