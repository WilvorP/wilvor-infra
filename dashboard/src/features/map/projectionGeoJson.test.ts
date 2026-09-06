import { describe, expect, it } from 'vitest';

import type { AircraftProjectionPoint } from '@/types/api';

import {
  buildProjectionGeoJson,
  EMPTY_PROJECTION_COLLECTION,
  projectionCollectionForSelection,
} from './projectionGeoJson';

function point(
  overrides: Partial<AircraftProjectionPoint> = {},
): AircraftProjectionPoint {
  return {
    point_sequence_number: 1,
    horizon_min: 5,
    latitude: 40.1,
    longitude: -100.1,
    projected_time_utc: '2026-09-06T00:38:14Z',
    estimated_altitude_ft: 41000,
    confidence: 'HIGH',
    ...overrides,
  };
}

describe('buildProjectionGeoJson', () => {
  it('builds a line and vertices from valid points in sequence order', () => {
    const result = buildProjectionGeoJson([
      point({
        point_sequence_number: 2,
        horizon_min: 10,
        latitude: 40.2,
        longitude: -100.2,
      }),
      point({
        point_sequence_number: 1,
        horizon_min: 5,
        latitude: 40.1,
        longitude: -100.1,
      }),
    ]);

    expect(result.renderedPointCount).toBe(2);
    expect(result.droppedPointCount).toBe(0);
    expect(result.horizons).toEqual([5, 10]);

    const line = result.collection.features.find(
      (feature) => feature.geometry.type === 'LineString',
    );

    expect(line?.geometry).toEqual({
      type: 'LineString',
      coordinates: [
        [-100.1, 40.1],
        [-100.2, 40.2],
      ],
    });
  });

  it('does not invent intermediate points when the backend returns only vertices', () => {
    const result = buildProjectionGeoJson([
      point({ latitude: 40, longitude: -100 }),
      point({
        point_sequence_number: 2,
        latitude: 41,
        longitude: -99,
      }),
    ]);

    const line = result.collection.features.find(
      (feature) => feature.geometry.type === 'LineString',
    );

    expect(line?.geometry.type === 'LineString' && line.geometry.coordinates).toEqual([
      [-100, 40],
      [-99, 41],
    ]);
  });

  it('drops malformed or incomplete coordinates instead of fabricating them', () => {
    const result = buildProjectionGeoJson([
      point({ latitude: undefined, longitude: -100 }),
      point({ latitude: 91, longitude: -100, point_sequence_number: 2 }),
      point({ latitude: 40.5, longitude: -100.5, point_sequence_number: 3 }),
      point({
        latitude: Number.NaN,
        longitude: -101,
        point_sequence_number: 4,
      }),
    ]);

    expect(result.renderedPointCount).toBe(1);
    expect(result.droppedPointCount).toBe(3);
    expect(
      result.collection.features.some(
        (feature) => feature.geometry.type === 'LineString',
      ),
    ).toBe(false);
    expect(result.collection.features[0]?.geometry).toEqual({
      type: 'Point',
      coordinates: [-100.5, 40.5],
    });
  });

  it('returns an empty collection for a missing or empty point list', () => {
    expect(buildProjectionGeoJson(null).collection).toEqual(
      EMPTY_PROJECTION_COLLECTION,
    );
    expect(buildProjectionGeoJson([]).renderedPointCount).toBe(0);
  });

  it('replaces the collection when a newer detail response arrives', () => {
    const first = buildProjectionGeoJson([
      point({ latitude: 40, longitude: -100 }),
      point({ point_sequence_number: 2, latitude: 40.1, longitude: -100.1 }),
    ]);
    const second = buildProjectionGeoJson([
      point({ latitude: 41, longitude: -101 }),
      point({ point_sequence_number: 2, latitude: 41.2, longitude: -101.2 }),
    ]);

    const firstLine = first.collection.features.find(
      (feature) => feature.geometry.type === 'LineString',
    );
    const secondLine = second.collection.features.find(
      (feature) => feature.geometry.type === 'LineString',
    );

    expect(firstLine?.geometry).not.toEqual(secondLine?.geometry);
    expect(
      secondLine?.geometry.type === 'LineString' &&
        secondLine.geometry.coordinates,
    ).toEqual([
      [-101, 41],
      [-101.2, 41.2],
    ]);
  });
});

describe('projectionCollectionForSelection', () => {
  it('clears the source payload when the selection is closed', () => {
    const selected = projectionCollectionForSelection(true, [
      point(),
      point({ point_sequence_number: 2, latitude: 40.2, longitude: -100.2 }),
    ]);

    expect(selected.features.length).toBeGreaterThan(0);
    expect(projectionCollectionForSelection(false, selected.features as never)).toEqual(
      EMPTY_PROJECTION_COLLECTION,
    );
    expect(projectionCollectionForSelection(false, [point()])).toEqual(
      EMPTY_PROJECTION_COLLECTION,
    );
  });
});
