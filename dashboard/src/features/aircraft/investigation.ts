import type {
  AircraftHazardEncounter,
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

