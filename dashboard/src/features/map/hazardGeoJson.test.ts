import { describe, expect, it } from 'vitest';

import type { ActiveHazard } from '@/types/api';

import { buildHazardGeoJson } from './hazardGeoJson';

const SQUARE: number[][][] = [
  [
    [-100, 40],
    [-99, 40],
    [-99, 41],
    [-100, 41],
    [-100, 40],
  ],
];

function hazard(overrides: Partial<ActiveHazard> = {}): ActiveHazard {
  return {
    hazard_id: 'sigmet-abc',
    source_version: 'v1',
    hazard_type: 'TURBULENCE',
    geometry: { type: 'Polygon', coordinates: SQUARE },
    ...overrides,
  };
}

describe('buildHazardGeoJson', () => {
  it('converts hazards with geometry into features', () => {
    const result = buildHazardGeoJson([hazard()]);

    expect(result.renderedCount).toBe(1);
    expect(result.withoutGeometryCount).toBe(0);

    const feature = result.collection.features[0]!;

    expect(feature.id).toBe('sigmet-abc');
    expect(feature.geometry.type).toBe('Polygon');
    expect(feature.properties.hazardType).toBe('TURBULENCE');
    expect(feature.properties.emphasized).toBe(false);
  });

  it('marks an existing hazard as emphasized without duplicating its geometry', () => {
    const result = buildHazardGeoJson(
      [hazard(), hazard({ hazard_id: 'sigmet-other' })],
      new Set(['sigmet-abc']),
    );

    expect(result.renderedCount).toBe(2);
    expect(result.collection.features.map((feature) => feature.id)).toEqual([
      'sigmet-abc',
      'sigmet-other',
    ]);
    expect(result.collection.features[0]?.properties.emphasized).toBe(true);
    expect(result.collection.features[1]?.properties.emphasized).toBe(false);
    expect(result.collection.features[0]?.geometry.coordinates).toEqual(SQUARE);
  });

  it('does not invent a feature for an emphasized hazard that is not in the feed', () => {
    const result = buildHazardGeoJson([hazard()], new Set(['missing-hazard']));

    expect(result.renderedCount).toBe(1);
    expect(result.collection.features[0]?.properties.emphasized).toBe(false);
    expect(result.collection.features.map((feature) => feature.id)).not.toContain(
      'missing-hazard',
    );
  });

  it('preserves backend coordinates without modification', () => {
    // Ring ordering and closure are the backend's responsibility
    // (`_hazard_geometry`). Re-deriving geometry here would risk disagreeing
    // with the authoritative shape.
    const result = buildHazardGeoJson([hazard()]);

    expect(result.collection.features[0]!.geometry.coordinates).toEqual(SQUARE);
  });

  it('counts hazards that carry no renderable geometry instead of dropping them', () => {
    // The API omits `geometry` when reconstruction fails. A hazard that exists
    // but cannot be drawn must remain visible to the operator as a gap.
    const result = buildHazardGeoJson([
      hazard(),
      hazard({ hazard_id: 'sigmet-def', geometry: undefined }),
      hazard({
        hazard_id: 'sigmet-ghi',
        geometry: { type: 'Polygon', coordinates: [] },
      }),
    ]);

    expect(result.renderedCount).toBe(1);
    expect(result.withoutGeometryCount).toBe(2);
  });

  it('skips records with no hazard id', () => {
    const result = buildHazardGeoJson([hazard({ hazard_id: undefined })]);

    expect(result.renderedCount).toBe(0);
    expect(result.withoutGeometryCount).toBe(0);
  });

  it('supports MultiPolygon geometry', () => {
    const result = buildHazardGeoJson([
      hazard({
        geometry_type: 'MULTIPOLYGON',
        geometry: { type: 'MultiPolygon', coordinates: [SQUARE] },
      }),
    ]);

    expect(result.collection.features[0]!.geometry.type).toBe('MultiPolygon');
  });

  it('ranks severity onto the shared risk scale', () => {
    const ranks = buildHazardGeoJson([
      hazard({ hazard_id: 'a', severity: 'SEVERE' }),
      hazard({ hazard_id: 'b', severity: 'MODERATE' }),
      hazard({ hazard_id: 'c', severity: 'LIGHT' }),
      hazard({ hazard_id: 'd', severity: undefined }),
    ]).collection.features.map((feature) => feature.properties.severityRank);

    expect(ranks).toEqual([4, 3, 2, 1]);
  });

  it('treats an unrecognised severity as unknown rather than guessing', () => {
    // `severity` is a NOAA pass-through string, not a fixed enum.
    const result = buildHazardGeoJson([hazard({ severity: 'WEIRD_VALUE' })]);

    expect(result.collection.features[0]!.properties.severityRank).toBe(1);
  });

  it('returns an empty collection for no hazards', () => {
    const result = buildHazardGeoJson([]);

    expect(result.collection.features).toEqual([]);
    expect(result.renderedCount).toBe(0);
  });

  it('maps absent optional attributes to null rather than undefined', () => {
    const properties = buildHazardGeoJson([hazard()]).collection.features[0]!
      .properties;

    expect(properties.severity).toBeNull();
    expect(properties.minAltitudeFt).toBeNull();
    expect(properties.validToUtc).toBeNull();
  });
});
