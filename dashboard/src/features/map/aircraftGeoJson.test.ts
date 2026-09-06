import { describe, expect, it } from 'vitest';

import type { MapAircraftResponse } from '@/types/api';

import {
  decodeMapAircraft,
  EXPECTED_AIRCRAFT_COLUMNS,
  resolveAircraftColumns,
} from './aircraftGeoJson';

const COLUMNS = [...EXPECTED_AIRCRAFT_COLUMNS];

/**
 * One row in the declared column order.
 *
 * Typed as `unknown[]` because that is what the decoder actually receives:
 * individual cells are nulled in tests to stand in for attributes the
 * pipeline omitted.
 */
const ROW: unknown[] = [
  'a1b2c3',
  'UAL123',
  -122.375,
  37.6188,
  270.5,
  35000,
  450,
  1786515880,
];

function response(
  overrides: Partial<MapAircraftResponse> = {},
): MapAircraftResponse {
  return {
    generatedAt: '2026-09-05T12:00:00Z',
    columns: COLUMNS,
    count: 1,
    truncated: false,
    aircraft: [ROW],
    ...overrides,
  };
}

describe('resolveAircraftColumns', () => {
  it('resolves every expected column to its position', () => {
    const resolution = resolveAircraftColumns(COLUMNS);

    expect(resolution.ok).toBe(true);

    if (resolution.ok) {
      expect(resolution.indices.aircraftId).toBe(0);
      expect(resolution.indices.longitude).toBe(2);
      expect(resolution.indices.latitude).toBe(3);
    }
  });

  it('names the missing columns so a contract break is diagnosable', () => {
    const resolution = resolveAircraftColumns([
      'aircraftId',
      'longitude',
      'latitude',
    ]);

    expect(resolution.ok).toBe(false);

    if (!resolution.ok) {
      expect(resolution.error).toContain('callsign');
      expect(resolution.error).toContain('trackDeg');
    }
  });

  it('rejects a payload that declares no columns', () => {
    for (const columns of [undefined, [], 'aircraftId', {}]) {
      expect(resolveAircraftColumns(columns).ok).toBe(false);
    }
  });
});

describe('decodeMapAircraft', () => {
  it('decodes a row into a point feature and a lookup entry', () => {
    const result = decodeMapAircraft(response());

    expect(result.contractError).toBeNull();
    expect(result.renderedCount).toBe(1);
    expect(result.droppedCount).toBe(0);

    const feature = result.collection.features[0]!;

    expect(feature.id).toBe('a1b2c3');
    expect(feature.geometry.type).toBe('Point');
    expect(feature.properties.aircraftId).toBe('a1b2c3');

    expect(result.aircraftById.get('a1b2c3')).toEqual({
      aircraftId: 'a1b2c3',
      callsign: 'UAL123',
      longitude: -122.375,
      latitude: 37.6188,
      trackDeg: 270.5,
      baroAltitudeFt: 35000,
      groundSpeedKt: 450,
      positionTimeEpoch: 1786515880,
    });
  });

  it('emits GeoJSON longitude-before-latitude order', () => {
    // Swapping these would silently misplace every aircraft on the map.
    const feature = decodeMapAircraft(response()).collection.features[0]!;

    expect(feature.geometry.coordinates).toEqual([-122.375, 37.6188]);
  });

  it('follows the declared column order rather than fixed offsets', () => {
    // The whole point of validating `columns` first: a reordered projection
    // must decode correctly instead of reading altitude as a longitude.
    const reordered = decodeMapAircraft(
      response({
        columns: [
          'positionTimeEpoch',
          'latitude',
          'longitude',
          'aircraftId',
          'groundSpeedKt',
          'baroAltitudeFt',
          'trackDeg',
          'callsign',
        ],
        aircraft: [
          [1786515880, 37.6188, -122.375, 'a1b2c3', 450, 35000, 270.5, 'UAL123'],
        ],
      }),
    );

    expect(reordered.contractError).toBeNull();
    expect(reordered.aircraftById.get('a1b2c3')).toEqual(
      decodeMapAircraft(response()).aircraftById.get('a1b2c3'),
    );
  });

  it('ignores columns it does not know about', () => {
    // Additive backend changes must not disable the layer.
    const result = decodeMapAircraft(
      response({
        columns: [...COLUMNS, 'riskLevel'],
        aircraft: [[...ROW, 'HIGH']],
      }),
    );

    expect(result.contractError).toBeNull();
    expect(result.renderedCount).toBe(1);
  });

  it('draws nothing when an expected column is absent', () => {
    // Decoding against an unknown layout could place traffic at wrong
    // positions, which is worse than showing no traffic at all.
    const result = decodeMapAircraft(
      response({ columns: COLUMNS.filter((name) => name !== 'latitude') }),
    );

    expect(result.contractError).toContain('latitude');
    expect(result.renderedCount).toBe(0);
    expect(result.collection.features).toEqual([]);
    expect(result.aircraftById.size).toBe(0);
  });

  it('still reports feed metadata when the contract is broken', () => {
    const result = decodeMapAircraft(
      response({ columns: [], truncated: true, count: 42 }),
    );

    expect(result.contractError).not.toBeNull();
    expect(result.truncated).toBe(true);
    expect(result.reportedCount).toBe(42);
    expect(result.generatedAt).toBe('2026-09-05T12:00:00Z');
  });

  it('marks aircraft with no reported track as directionless', () => {
    // Rendering a rotated arrow at 0 degrees would assert a heading the
    // platform never received.
    const row = [...ROW];
    row[COLUMNS.indexOf('trackDeg')] = null;

    const result = decodeMapAircraft(response({ aircraft: [row] }));
    const feature = result.collection.features[0]!;

    expect(feature.properties.hasTrack).toBe(false);
    expect(feature.properties.trackDeg).toBe(0);
    expect(result.aircraftById.get('a1b2c3')!.trackDeg).toBeNull();
    expect(result.withoutHeadingCount).toBe(1);
  });

  it('keeps absent measurements null rather than zero', () => {
    const row = [...ROW];
    row[COLUMNS.indexOf('baroAltitudeFt')] = null;
    row[COLUMNS.indexOf('groundSpeedKt')] = undefined;

    const aircraft = decodeMapAircraft(
      response({ aircraft: [row] }),
    ).aircraftById.get('a1b2c3')!;

    // Zero would read as sea level and stationary.
    expect(aircraft.baroAltitudeFt).toBeNull();
    expect(aircraft.groundSpeedKt).toBeNull();
  });

  it('drops rows without an aircraft id', () => {
    const row = [...ROW];
    row[0] = '';

    const result = decodeMapAircraft(response({ aircraft: [row, ROW] }));

    expect(result.renderedCount).toBe(1);
    expect(result.droppedCount).toBe(1);
  });

  it('drops rows whose position is missing or out of range', () => {
    const noPosition = [...ROW];
    noPosition[0] = 'no-position';
    noPosition[COLUMNS.indexOf('longitude')] = null;

    const offEarth = [...ROW];
    offEarth[0] = 'off-earth';
    offEarth[COLUMNS.indexOf('latitude')] = 991;

    const notNumeric = [...ROW];
    notNumeric[0] = 'not-numeric';
    notNumeric[COLUMNS.indexOf('longitude')] = '-122.375';

    const result = decodeMapAircraft(
      response({ aircraft: [ROW, noPosition, offEarth, notNumeric] }),
    );

    expect(result.renderedCount).toBe(1);
    expect(result.droppedCount).toBe(3);
  });

  it('drops rows that are not arrays', () => {
    const result = decodeMapAircraft(
      response({ aircraft: [ROW, { aircraftId: 'object-form' }, null] }),
    );

    expect(result.renderedCount).toBe(1);
    expect(result.droppedCount).toBe(2);
  });

  it('keeps only the first of a duplicated aircraft id', () => {
    // A repeated id would stack symbols and make the selection ambiguous.
    const duplicate = [...ROW];
    duplicate[COLUMNS.indexOf('callsign')] = 'SECOND';

    const result = decodeMapAircraft(
      response({ aircraft: [ROW, duplicate] }),
    );

    expect(result.renderedCount).toBe(1);
    expect(result.droppedCount).toBe(1);
    expect(result.aircraftById.get('a1b2c3')!.callsign).toBe('UAL123');
  });

  it('reports a truncated feed so a partial picture is visible', () => {
    const result = decodeMapAircraft(response({ truncated: true }));

    expect(result.truncated).toBe(true);
  });

  it('returns an empty result before the first response arrives', () => {
    const result = decodeMapAircraft(undefined);

    expect(result.collection.features).toEqual([]);
    expect(result.renderedCount).toBe(0);
    expect(result.contractError).toBeNull();
    expect(result.generatedAt).toBeNull();
  });

  it('handles an empty fleet without reporting a fault', () => {
    const result = decodeMapAircraft(response({ aircraft: [], count: 0 }));

    expect(result.renderedCount).toBe(0);
    expect(result.droppedCount).toBe(0);
    expect(result.contractError).toBeNull();
  });
});
