import { renderHook } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import type { AircraftProjectionPoint } from '@/types/api';

import { MAP_IDS } from './mapStyle';
import { EMPTY_PROJECTION_COLLECTION } from './projectionGeoJson';
import { useTrajectoryLayer } from './useTrajectoryLayer';

function point(
  overrides: Partial<AircraftProjectionPoint> = {},
): AircraftProjectionPoint {
  return {
    point_sequence_number: 1,
    latitude: 40,
    longitude: -100,
    ...overrides,
  };
}

function createFakeMap() {
  const sources = new Map<
    string,
    { setData: ReturnType<typeof vi.fn>; data: unknown }
  >();
  const layers = new Map<string, unknown>();

  return {
    sources,
    layers,
    getSource: (id: string) => sources.get(id),
    addSource: vi.fn((id: string, spec: { data: unknown }) => {
      const source = {
        data: spec.data,
        setData: vi.fn((data: unknown) => {
          source.data = data;
        }),
      };
      sources.set(id, source);
    }),
    getLayer: (id: string) => layers.get(id),
    addLayer: vi.fn((layer: { id: string }) => {
      layers.set(layer.id, layer);
    }),
  };
}

describe('useTrajectoryLayer', () => {
  it('creates the source once and updates it with setData', () => {
    const map = createFakeMap();
    const firstPoints = [
      point(),
      point({ point_sequence_number: 2, latitude: 40.2, longitude: -100.2 }),
    ];

    const { rerender } = renderHook(
      ({ points, selected }) =>
        useTrajectoryLayer(
          map as never,
          true,
          selected,
          points,
        ),
      { initialProps: { points: firstPoints, selected: true } },
    );

    expect(map.addSource).toHaveBeenCalledTimes(1);
    expect(map.sources.get(MAP_IDS.trajectorySource)?.setData).toHaveBeenCalled();

    const nextPoints = [
      point({ latitude: 41, longitude: -101 }),
      point({ point_sequence_number: 2, latitude: 41.2, longitude: -101.2 }),
    ];

    rerender({ points: nextPoints, selected: true });

    expect(map.addSource).toHaveBeenCalledTimes(1);

    const source = map.sources.get(MAP_IDS.trajectorySource);
    const lastData = source?.setData.mock.calls.at(-1)?.[0] as {
      features: Array<{ geometry: { type: string; coordinates: unknown } }>;
    };
    const line = lastData.features.find(
      (feature) => feature.geometry.type === 'LineString',
    );

    expect(line?.geometry.coordinates).toEqual([
      [-101, 41],
      [-101.2, 41.2],
    ]);
  });

  it('writes an empty collection when the aircraft is deselected', () => {
    const map = createFakeMap();
    const points = [
      point(),
      point({ point_sequence_number: 2, latitude: 40.2, longitude: -100.2 }),
    ];

    const { rerender } = renderHook(
      ({ selected }) =>
        useTrajectoryLayer(map as never, true, selected, points),
      { initialProps: { selected: true } },
    );

    rerender({ selected: false });

    const source = map.sources.get(MAP_IDS.trajectorySource);
    expect(source?.setData).toHaveBeenLastCalledWith(
      EMPTY_PROJECTION_COLLECTION,
    );
    expect(map.addSource).toHaveBeenCalledTimes(1);
  });
});
