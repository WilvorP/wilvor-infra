import type { FreshnessRecord, FreshnessResponse } from '@/types/api';
import { asNumber, asString } from '@/utils/coerce';

/**
 * Normalisation for `GET /freshness`.
 *
 * The backend deliberately reports SIGMET using a different vocabulary from
 * the other sources: an unchanged but still-valid SIGMET must not be labelled
 * stale simply because no newer product was issued, so its record carries
 * `AVAILABLE`/`UNAVAILABLE` plus an explanatory `note` instead of age banding
 * (`get_freshness` in functions/operational_api/repository.py).
 *
 * This module reconciles the two shapes into one view model for the status
 * strip while preserving that distinction, rather than flattening SIGMET into
 * a freshness band the backend intentionally does not assert.
 */

export const FRESHNESS_SOURCE_KEYS = [
  'opensky',
  'sigmet',
  'metar',
  'taf',
] as const;

export type FreshnessSourceKey = (typeof FRESHNESS_SOURCE_KEYS)[number];

const SOURCE_LABELS: Record<FreshnessSourceKey, string> = {
  opensky: 'Aircraft',
  sigmet: 'SIGMET',
  metar: 'METAR',
  taf: 'TAF',
};

export interface SourceFreshness {
  readonly key: FreshnessSourceKey;
  readonly label: string;
  /** Raw backend status token, or `null` when the source is missing entirely. */
  readonly status: string | null;
  readonly latestAt: string | null;
  readonly ageSeconds: number | null;
  /** Present only for SIGMET, explaining why age banding does not apply. */
  readonly note: string | null;
  /**
   * Whether this source reports age-banded freshness. False for SIGMET, whose
   * age is informational rather than a health signal.
   */
  readonly isAgeBanded: boolean;
}

export interface FreshnessSummary {
  readonly generatedAt: string | null;
  readonly sources: readonly SourceFreshness[];
  /** Sources reporting STALE or UNAVAILABLE, matching the backend's own rule. */
  readonly problemSources: readonly SourceFreshness[];
}

function normaliseRecord(
  key: FreshnessSourceKey,
  record: FreshnessRecord | null | undefined,
): SourceFreshness {
  return {
    key,
    label: SOURCE_LABELS[key],
    status: asString(record?.status)?.toUpperCase() ?? null,
    latestAt: asString(record?.latestAt),
    ageSeconds: asNumber(record?.ageSeconds),
    note: asString(record?.note),
    isAgeBanded: key !== 'sigmet',
  };
}

export function summariseFreshness(
  response: FreshnessResponse | undefined,
): FreshnessSummary {
  const sources = FRESHNESS_SOURCE_KEYS.map((key) =>
    normaliseRecord(key, response?.sources?.[key]),
  );

  return {
    generatedAt: asString(response?.generatedAt),
    sources,
    problemSources: sources.filter(
      (source) => source.status === 'STALE' || source.status === 'UNAVAILABLE',
    ),
  };
}
