import { describe, expect, it } from 'vitest';

import type { FreshnessResponse } from '@/types/api';

import { summariseFreshness } from './freshness';

describe('summariseFreshness', () => {
  it('returns every known source even when the response is missing', () => {
    // An unreachable API must still render four labelled slots, so the
    // operator sees which sources are unknown rather than an empty strip.
    const summary = summariseFreshness(undefined);

    expect(summary.sources.map((source) => source.key)).toEqual([
      'opensky',
      'sigmet',
      'metar',
      'taf',
    ]);
    expect(summary.sources.every((source) => source.status === null)).toBe(
      true,
    );
    expect(summary.generatedAt).toBeNull();
  });

  it('preserves the SIGMET availability vocabulary and its note', () => {
    // SIGMET intentionally reports AVAILABLE/UNAVAILABLE rather than an age
    // band, because an unchanged valid product is not stale.
    const response: FreshnessResponse = {
      generatedAt: '2026-09-03T12:00:00Z',
      sources: {
        sigmet: {
          latestAt: '2026-09-03T09:00:00Z',
          ageSeconds: 10_800,
          status: 'AVAILABLE',
          note: 'SIGMET table age reflects the newest materialized hazard product.',
        },
      },
    };

    const sigmet = summariseFreshness(response).sources.find(
      (source) => source.key === 'sigmet',
    );

    expect(sigmet?.status).toBe('AVAILABLE');
    expect(sigmet?.isAgeBanded).toBe(false);
    expect(sigmet?.note).toContain('newest materialized hazard product');
  });

  it('marks the age-banded sources as such', () => {
    const summary = summariseFreshness({ sources: {} });

    expect(
      summary.sources
        .filter((source) => source.isAgeBanded)
        .map((source) => source.key),
    ).toEqual(['opensky', 'metar', 'taf']);
  });

  it('collects STALE and UNAVAILABLE sources as problems', () => {
    const response: FreshnessResponse = {
      sources: {
        opensky: { status: 'FRESH', ageSeconds: 20 },
        sigmet: { status: 'UNAVAILABLE' },
        metar: { status: 'STALE', ageSeconds: 4_000 },
        taf: { status: 'ACCEPTABLE', ageSeconds: 9_000 },
      },
    };

    expect(
      summariseFreshness(response).problemSources.map((source) => source.key),
    ).toEqual(['sigmet', 'metar']);
  });

  it('normalises status casing and discards unusable ages', () => {
    const summary = summariseFreshness({
      sources: {
        opensky: {
          status: 'fresh',
          ageSeconds: null,
          latestAt: '',
        },
      },
    });

    const opensky = summary.sources[0]!;

    expect(opensky.status).toBe('FRESH');
    expect(opensky.ageSeconds).toBeNull();
    expect(opensky.latestAt).toBeNull();
  });
});
