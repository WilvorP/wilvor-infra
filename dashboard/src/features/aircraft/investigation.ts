import type {
  AircraftHazardEncounter,
  AircraftOperationalContext,
  AircraftProjectionPoint,
  Recommendation,
  RecommendationEvidenceReference,
  RiskResult,
} from '@/types/api';
import { asArray, asString } from '@/utils/coerce';
import { humaniseToken, NOT_REPORTED } from '@/utils/format';

/**
 * Presentation helpers for `GET /aircraft/{aircraftId}`.
 *
 * These reshape API payloads for display. They do not score risk, infer
 * altitude overlap, invent trajectory points, or decide a recommendation.
 */

/** Latest-first arrays as returned by `_query_latest` (limit 20). */
export function asRecordList<T>(value: readonly T[] | null | undefined): T[] {
  return asArray<T>(value);
}

/** Hazard identifiers referenced by returned encounters, de-duplicated. */
export function encounterHazardIds(
  encounters: readonly AircraftHazardEncounter[] | null | undefined,
): string[] {
  const ids: string[] = [];
  const seen = new Set<string>();

  for (const encounter of asRecordList(encounters)) {
    const hazardId = asString(encounter.hazard_id);

    if (hazardId === null || seen.has(hazardId)) {
      continue;
    }

    seen.add(hazardId);
    ids.push(hazardId);
  }

  return ids;
}

/** Hazard identifiers from current operational contexts only. */
export function currentContextHazardIds(
  contexts: readonly AircraftOperationalContext[] | null | undefined,
): string[] {
  return encounterHazardIds(
    asRecordList(contexts)
      .map((context) => context.encounter)
      .filter((encounter): encounter is AircraftHazardEncounter => {
        return encounter != null;
      }),
  );
}

const RISK_RANK: Record<string, number> = {
  HIGH: 4,
  MEDIUM: 3,
  LOW: 2,
  UNKNOWN: 1,
};

/**
 * Worklist or map-driven pointer into `currentContexts`.
 *
 * Matching is by stored IDs only. Never pick a context because it is the
 * newest timestamp.
 */
export interface ContextSelection {
  aircraftId: string;
  hazardId?: string | null;
  encounterId?: string | null;
  riskId?: string | null;
  recommendationId?: string | null;
  alertId?: string | null;
  fingerprint?: string | null;
  source?: 'encounter' | 'alert' | 'recommendation' | 'map';
}

function hasExplicitContextId(
  selection: ContextSelection | null | undefined,
): boolean {
  if (selection == null) {
    return false;
  }

  return (
    asString(selection.encounterId) !== null ||
    asString(selection.recommendationId) !== null ||
    asString(selection.riskId) !== null ||
    asString(selection.alertId) !== null ||
    asString(selection.fingerprint) !== null
  );
}

/**
 * Find the current context that owns the selected IDs.
 *
 * Keys are tried in strength order, each across the full list:
 * encounter_id, recommendation_id, risk_id, alert_id, fingerprint.
 * Recency is never a tie-break.
 */
export function matchCurrentContext(
  contexts: readonly AircraftOperationalContext[] | null | undefined,
  selection: ContextSelection | null | undefined,
): AircraftOperationalContext | null {
  const items = asRecordList(contexts);

  if (!hasExplicitContextId(selection) || items.length === 0) {
    return null;
  }

  const encounterId = asString(selection?.encounterId);
  if (encounterId !== null) {
    const match = items.find(
      (context) => asString(context.encounter?.encounter_id) === encounterId,
    );
    if (match) {
      return match;
    }
  }

  const recommendationId = asString(selection?.recommendationId);
  if (recommendationId !== null) {
    const match = items.find(
      (context) =>
        asString(context.recommendation?.recommendation_id) ===
        recommendationId,
    );
    if (match) {
      return match;
    }
  }

  const riskId = asString(selection?.riskId);
  if (riskId !== null) {
    const match = items.find(
      (context) => asString(context.risk?.risk_id) === riskId,
    );
    if (match) {
      return match;
    }
  }

  const alertId = asString(selection?.alertId);
  if (alertId !== null) {
    const match = items.find(
      (context) => asString(context.alert?.alert_id) === alertId,
    );
    if (match) {
      return match;
    }
  }

  const fingerprint = asString(selection?.fingerprint);
  if (fingerprint !== null) {
    const match = items.find(
      (context) => asString(context.alert?.fingerprint) === fingerprint,
    );
    if (match) {
      return match;
    }
  }

  return null;
}

export function contextSelectionIsExplicit(
  selection: ContextSelection | null | undefined,
): boolean {
  return hasExplicitContextId(selection);
}

const CONTEXT_SEARCH_KEYS = [
  'hazardId',
  'encounterId',
  'riskId',
  'recommendationId',
  'alertId',
  'fingerprint',
  'source',
] as const;

/** Encode worklist IDs for `/aircraft/{id}` so Overview does not host investigation. */
export function contextSelectionSearchParams(
  selection: ContextSelection,
): URLSearchParams {
  const params = new URLSearchParams();

  for (const key of CONTEXT_SEARCH_KEYS) {
    const value = asString(selection[key]);

    if (value !== null) {
      params.set(key, value);
    }
  }

  return params;
}

export function aircraftInvestigationPath(selection: ContextSelection): string {
  const params = contextSelectionSearchParams(selection);
  const path = `/aircraft/${encodeURIComponent(selection.aircraftId)}`;

  return params.size === 0 ? path : `${path}?${params.toString()}`;
}

export function contextSelectionFromSearch(
  aircraftId: string,
  search: URLSearchParams,
): ContextSelection {
  const source = asString(search.get('source'));

  return {
    aircraftId,
    hazardId: asString(search.get('hazardId')),
    encounterId: asString(search.get('encounterId')),
    riskId: asString(search.get('riskId')),
    recommendationId: asString(search.get('recommendationId')),
    alertId: asString(search.get('alertId')),
    fingerprint: asString(search.get('fingerprint')),
    source:
      source === 'encounter' ||
      source === 'alert' ||
      source === 'recommendation' ||
      source === 'map'
        ? source
        : 'map',
  };
}

/** Highest stored current-context risk. Does not recompute score. */
export function highestCurrentRisk(
  contexts: readonly AircraftOperationalContext[] | null | undefined,
): RiskResult | null {
  let selected: RiskResult | null = null;
  let selectedRank = -1;

  for (const context of asRecordList(contexts)) {
    const risk = context.risk;

    if (risk == null) {
      continue;
    }

    const rank = RISK_RANK[asString(risk.risk_level)?.toUpperCase() ?? ''] ?? 0;

    if (rank > selectedRank) {
      selected = risk;
      selectedRank = rank;
    }
  }

  return selected;
}

/**
 * Action token as stored (`EVALUATE_DIVERSION` → `Evaluate diversion`).
 *
 * Must not be rewritten into an instruction (`Divert to…`).
 */
export function formatAdvisoryAction(value: unknown): string {
  return humaniseToken(value);
}

export function formatEvidenceReference(
  reference: RecommendationEvidenceReference,
): string | null {
  const type = humaniseToken(reference.type);
  const id = asString(reference.id);
  const airportId = asString(reference.airport_id);

  if (type === NOT_REPORTED && id === null && airportId === null) {
    return null;
  }

  const parts = [type === NOT_REPORTED ? null : type, id, airportId].filter(
    (part): part is string => part !== null,
  );

  return parts.join(' · ');
}

export function formatSourceVersions(
  versions: Record<string, unknown> | null | undefined,
): string[] {
  if (versions === null || versions === undefined || typeof versions !== 'object') {
    return [];
  }

  const lines: string[] = [];

  for (const [key, value] of Object.entries(versions)) {
    const rendered = asString(value);

    if (rendered === null) {
      continue;
    }

    lines.push(`${humaniseToken(key)}: ${rendered}`);
  }

  return lines;
}

export function uniqueHorizons(
  points: readonly AircraftProjectionPoint[] | null | undefined,
): number[] {
  const seen = new Set<number>();
  const horizons: number[] = [];

  for (const point of asRecordList(points)) {
    const horizon = point.horizon_min;

    if (typeof horizon !== 'number' || !Number.isFinite(horizon) || seen.has(horizon)) {
      continue;
    }

    seen.add(horizon);
    horizons.push(horizon);
  }

  return horizons;
}

export function latestRisk(
  risks: readonly RiskResult[] | null | undefined,
): RiskResult | null {
  return asRecordList(risks)[0] ?? null;
}

export function latestRecommendation(
  recommendations: readonly Recommendation[] | null | undefined,
): Recommendation | null {
  return asRecordList(recommendations)[0] ?? null;
}

