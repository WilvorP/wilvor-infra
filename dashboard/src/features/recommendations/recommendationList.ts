import type { Recommendation } from '@/types/api';
import { asNumber, asString } from '@/utils/coerce';

import {
  type CallsignLookup,
  filterRecommendations,
  lookupCallsign,
  recommendationRowKey,
  selectionFromRecommendation,
  sortRecommendations,
} from '@/features/worklist/worklist';

/**
 * Presentation helpers for the dedicated Current Recommendations page.
 *
 * Filtering and sorting operate on pages already returned by
 * `GET /recommendations/active`. They do not recreate current-set membership
 * or derive an action from stored risk.
 */

export type RecommendationSortKey =
  | 'newest'
  | 'risk'
  | 'action'
  | 'aircraft'
  | 'hazard';

export interface RecommendationFilters {
  action: string;
  status: string;
  riskLevel: string;
  aircraft: string;
  hazard: string;
}

export const EMPTY_RECOMMENDATION_FILTERS: RecommendationFilters = {
  action: '',
  status: '',
  riskLevel: '',
  aircraft: '',
  hazard: '',
};

export const RECOMMENDATION_ACTION_KPI = {
  all: '',
  monitor: 'MONITOR',
  prepare: 'MONITOR_AND_PREPARE_OPTIONS',
  diversion: 'EVALUATE_DIVERSION',
} as const;

export type RecommendationActionKpi =
  (typeof RECOMMENDATION_ACTION_KPI)[keyof typeof RECOMMENDATION_ACTION_KPI];

export function withRecommendationAction(
  filters: RecommendationFilters,
  action: string,
): RecommendationFilters {
  return { ...filters, action };
}

export function matchesRecommendationActionFilter(
  item: Recommendation,
  action: string,
): boolean {
  if (action.length === 0) {
    return true;
  }

  return (
    asString(item.primary_action_type)?.toUpperCase() === action.toUpperCase()
  );
}

export type RecommendationSelectionStatus =
  | { readonly status: 'none' }
  | { readonly status: 'current'; readonly item: Recommendation }
  | { readonly status: 'resolved'; readonly recommendationId: string }
  | { readonly status: 'unloaded'; readonly recommendationId: string };

export function recommendationIdOf(item: Recommendation): string | null {
  return asString(item.recommendation_id);
}

export function recommendationAircraftId(item: Recommendation): string | null {
  return asString(item.aircraft_id);
}

export function recommendationHazardId(item: Recommendation): string | null {
  return asString(item.hazard_id);
}

export function uniqueRecommendationIds(
  items: readonly Recommendation[],
  pick: (item: Recommendation) => string | null,
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

export function loadedRecommendationAircraftIds(
  items: readonly Recommendation[],
): string[] {
  return uniqueRecommendationIds(items, recommendationAircraftId);
}

export function loadedRecommendationHazardIds(
  items: readonly Recommendation[],
): string[] {
  return uniqueRecommendationIds(items, recommendationHazardId);
}

export function recordSeenRecommendationIds(
  seen: Set<string>,
  items: readonly Recommendation[],
): void {
  for (const item of items) {
    const id = recommendationIdOf(item);

    if (id !== null) {
      seen.add(id);
    }
  }
}

export function resolveRecommendationSelection(
  recommendationId: string | null,
  loaded: readonly Recommendation[],
  previouslySeen: ReadonlySet<string>,
): RecommendationSelectionStatus {
  const id = asString(recommendationId);

  if (id === null) {
    return { status: 'none' };
  }

  const current = loaded.find((item) => recommendationIdOf(item) === id);

  if (current) {
    return { status: 'current', item: current };
  }

  if (previouslySeen.has(id)) {
    return { status: 'resolved', recommendationId: id };
  }

  return { status: 'unloaded', recommendationId: id };
}

export function filterCurrentRecommendations(
  items: readonly Recommendation[],
  filters: RecommendationFilters,
  aircraftById?: CallsignLookup,
): Recommendation[] {
  return filterRecommendations(
    items,
    {
      riskLevel: filters.riskLevel,
      state: filters.action,
      aircraft: filters.aircraft,
      hazard: filters.hazard,
    },
    aircraftById,
  ).filter((item) => {
    if (filters.status.length === 0) {
      return true;
    }

    return (
      asString(item.recommendation_status)?.toUpperCase() ===
      filters.status.toUpperCase()
    );
  });
}

export function sortCurrentRecommendations(
  items: readonly Recommendation[],
  sortKey: RecommendationSortKey,
): Recommendation[] {
  if (sortKey === 'newest') {
    return sortRecommendations(items, 'timestamp', 'desc');
  }

  if (sortKey === 'risk') {
    return sortRecommendations(items, 'attention', 'desc');
  }

  if (sortKey === 'action') {
    return sortRecommendations(items, 'state', 'asc');
  }

  if (sortKey === 'aircraft') {
    return sortRecommendations(items, 'aircraft', 'asc');
  }

  return sortRecommendations(items, 'hazard', 'asc');
}

export function visibleCurrentRecommendations(
  items: readonly Recommendation[],
  filters: RecommendationFilters,
  sortKey: RecommendationSortKey,
  aircraftById?: CallsignLookup,
): Recommendation[] {
  return sortCurrentRecommendations(
    filterCurrentRecommendations(items, filters, aircraftById),
    sortKey,
  );
}

/**
 * Loaded current recommendations for one aircraft.
 *
 * Order is hazard id, then recommendation id. Recency and risk are not used,
 * so a map click cannot imply "newest" as a default pick.
 */
export function loadedRecommendationsForAircraft(
  items: readonly Recommendation[],
  aircraftId: string,
): Recommendation[] {
  return items
    .filter((item) => recommendationAircraftId(item) === aircraftId)
    .filter((item) => recommendationIdOf(item) !== null)
    .sort((left, right) => {
      const hazard = (recommendationHazardId(left) ?? '').localeCompare(
        recommendationHazardId(right) ?? '',
      );

      if (hazard !== 0) {
        return hazard;
      }

      return (recommendationIdOf(left) ?? '').localeCompare(
        recommendationIdOf(right) ?? '',
      );
    });
}

export type MapAircraftRecommendationChoice =
  | { readonly kind: 'none' }
  | { readonly kind: 'single'; readonly item: Recommendation }
  | { readonly kind: 'multiple'; readonly items: Recommendation[] };

export function resolveMapAircraftRecommendationClick(
  items: readonly Recommendation[],
  aircraftId: string,
): MapAircraftRecommendationChoice {
  const matches = loadedRecommendationsForAircraft(items, aircraftId);

  if (matches.length === 0) {
    return { kind: 'none' };
  }

  if (matches.length === 1 && matches[0]) {
    return { kind: 'single', item: matches[0] };
  }

  return { kind: 'multiple', items: matches };
}

export function countLoadedActions(
  items: readonly Recommendation[],
): { monitor: number; prepare: number; diversion: number } {
  let monitor = 0;
  let prepare = 0;
  let diversion = 0;

  for (const item of items) {
    switch (asString(item.primary_action_type)?.toUpperCase()) {
      case 'MONITOR':
        monitor += 1;
        break;
      case 'MONITOR_AND_PREPARE_OPTIONS':
        prepare += 1;
        break;
      case 'EVALUATE_DIVERSION':
        diversion += 1;
        break;
      default:
        break;
    }
  }

  return { monitor, prepare, diversion };
}

export function describeLoadedRecommendationScope(
  items: readonly Recommendation[],
): string {
  const aircraft = loadedRecommendationAircraftIds(items).length;
  const hazards = loadedRecommendationHazardIds(items).length;

  return `${aircraft.toLocaleString('en-US')} aircraft · ${hazards.toLocaleString('en-US')} hazards on loaded pages`;
}

export function pickRecommendationForHazard(
  items: readonly Recommendation[],
  hazardId: string,
  selectedRecommendationId: string | null,
): Recommendation | null {
  const matches = items.filter(
    (item) => recommendationHazardId(item) === hazardId,
  );

  if (matches.length === 0) {
    return null;
  }

  return (
    matches.find((item) => recommendationIdOf(item) === selectedRecommendationId) ??
    matches[0] ??
    null
  );
}

export function firstReason(item: Recommendation): string | null {
  const reasons = item.reasons;

  if (!Array.isArray(reasons)) {
    return null;
  }

  return asString(reasons[0]);
}

export type AirportEvidencePresentation =
  | { readonly kind: 'preferred'; readonly airportId: string }
  | { readonly kind: 'limitation'; readonly reason: string }
  | { readonly kind: 'unavailable' }
  | { readonly kind: 'none' };

/**
 * Stored airport evidence only. React never invents METAR/TAF or candidates.
 */
export function presentAirportEvidence(
  item: Recommendation,
): AirportEvidencePresentation {
  const preferred = asString(item.preferred_airport_id);
  const limitation = asString(item.no_suitable_candidate_reason);
  const action = asString(item.primary_action_type)?.toUpperCase();
  const candidateCount =
    asNumber(item.primary_action_details?.candidate_count) ??
    (Array.isArray(item.candidate_airport_summaries)
      ? item.candidate_airport_summaries.length
      : 0);

  if (preferred !== null) {
    return { kind: 'preferred', airportId: preferred };
  }

  if (limitation !== null) {
    return { kind: 'limitation', reason: limitation };
  }

  if (action === 'EVALUATE_DIVERSION' && candidateCount === 0) {
    return { kind: 'unavailable' };
  }

  return { kind: 'none' };
}

export {
  lookupCallsign,
  recommendationRowKey,
  selectionFromRecommendation,
};
