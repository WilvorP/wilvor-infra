import type { MapAircraftResponse } from '@/types/api';
import { asArray, asCoordinate, asNumber, asString } from '@/utils/coerce';

/**
 * Convert `GET /map/aircraft` into a GeoJSON source for MapLibre.
 *
 * The endpoint returns positional rows plus a `columns` array naming each
 * position (`get_map_aircraft` in repository.py). Fields are therefore
 * resolved *by name* before any row is read: a reordered payload decodes
 * correctly, and a payload missing an expected column is rejected outright.
 *
 * The alternative — trusting a fixed offset — would misread every aircraft
 * silently if the backend projection ever changed, putting traffic at wrong
 * positions and wrong altitudes with no visible failure. On a decision-support
 * surface that is the worst available outcome, so the contract is checked
 * first and a mismatch renders nothing.
 *
 * No operational meaning is derived here. This module only reshapes rows.
 */

/** Columns `get_map_aircraft` declares. Extra columns are ignored. */
export const EXPECTED_AIRCRAFT_COLUMNS = [
  'aircraftId',
  'callsign',
  'longitude',
  'latitude',
  'trackDeg',
  'baroAltitudeFt',
  'groundSpeedKt',
  'positionTimeEpoch',
] as const;

export type AircraftColumn = (typeof EXPECTED_AIRCRAFT_COLUMNS)[number];

export type AircraftColumnIndices = Record<AircraftColumn, number>;

export type ColumnResolution =
  | { readonly ok: true; readonly indices: AircraftColumnIndices }
  | { readonly ok: false; readonly error: string };

/** A decoded aircraft, used for selection and investigation display. */
export interface MapAircraft {
  readonly aircraftId: string;
  readonly callsign: string | null;
  readonly longitude: number;
  readonly latitude: number;
  /** Degrees true, clockwise from north. `null` when not reported. */
  readonly trackDeg: number | null;
  readonly baroAltitudeFt: number | null;
  readonly groundSpeedKt: number | null;
  readonly positionTimeEpoch: number | null;
}

/**
 * Feature properties are kept to the minimum the layers actually read.
 *
 * With several thousand features, every additional property is paid for in
 * serialisation and in the data uploaded to the GPU on each poll. Display
 * fields are looked up from `aircraftById` on selection instead.
 */
export interface AircraftFeatureProperties {
  readonly aircraftId: string;
  /**
   * Always numeric so `icon-rotate` receives a valid value. Meaningful only
   * when `hasTrack` is true.
   */
  readonly trackDeg: number;
  /**
   * Whether the source reported a heading. Aircraft without one render as a
   * plain dot rather than an arrow pointing north, which would fabricate a
   * heading the platform does not have.
   */
  readonly hasTrack: boolean;
}

export interface AircraftFeature {
  readonly type: 'Feature';
  readonly id: string;
  readonly geometry: { readonly type: 'Point'; readonly coordinates: [number, number] };
  readonly properties: AircraftFeatureProperties;
}

export interface AircraftFeatureCollection {
  readonly type: 'FeatureCollection';
  readonly features: readonly AircraftFeature[];
}

export interface MapAircraftResult {
  readonly collection: AircraftFeatureCollection;
  /** Decoded aircraft by id, for selection lookup. */
  readonly aircraftById: ReadonlyMap<string, MapAircraft>;
  readonly renderedCount: number;
  /** Rows discarded because identity or position was unusable. */
  readonly droppedCount: number;
  /** Rendered aircraft with no reported heading. */
  readonly withoutHeadingCount: number;
  /** Backend capped its response; the fleet shown is incomplete. */
  readonly truncated: boolean;
  /** Row count the backend reported, for cross-checking against decoding. */
  readonly reportedCount: number | null;
  readonly generatedAt: string | null;
  /** Set when the column contract was violated; no rows are decoded. */
  readonly contractError: string | null;
}

export const EMPTY_AIRCRAFT_COLLECTION: AircraftFeatureCollection = {
  type: 'FeatureCollection',
  features: [],
};

const EMPTY_RESULT: MapAircraftResult = {
  collection: EMPTY_AIRCRAFT_COLLECTION,
  aircraftById: new Map(),
  renderedCount: 0,
  droppedCount: 0,
  withoutHeadingCount: 0,
  truncated: false,
  reportedCount: null,
  generatedAt: null,
  contractError: null,
};

export function resolveAircraftColumns(columns: unknown): ColumnResolution {
  const declared = asArray<unknown>(columns).map(asString);

  if (declared.length === 0) {
    return {
      ok: false,
      error:
        'The aircraft map response declared no columns, so its rows cannot ' +
        'be decoded safely.',
    };
  }

  const indices = {} as Record<AircraftColumn, number>;
  const missing: AircraftColumn[] = [];

  for (const column of EXPECTED_AIRCRAFT_COLUMNS) {
    const index = declared.indexOf(column);

    if (index === -1) {
      missing.push(column);
      continue;
    }

    indices[column] = index;
  }

  if (missing.length > 0) {
    return {
      ok: false,
      error:
        `The aircraft map response is missing expected ` +
        `column${missing.length === 1 ? '' : 's'}: ${missing.join(', ')}.`,
    };
  }

  return { ok: true, indices };
}

export function decodeMapAircraft(
  response: MapAircraftResponse | undefined,
): MapAircraftResult {
  if (response === undefined) {
    return EMPTY_RESULT;
  }

  const generatedAt = asString(response.generatedAt);
  const reportedCount = asNumber(response.count);
  const truncated = response.truncated === true;

  const resolution = resolveAircraftColumns(response.columns);

  if (!resolution.ok) {
    return {
      ...EMPTY_RESULT,
      truncated,
      reportedCount,
      generatedAt,
      contractError: resolution.error,
    };
  }

  const { indices } = resolution;

  const features: AircraftFeature[] = [];
  const aircraftById = new Map<string, MapAircraft>();

  let droppedCount = 0;
  let withoutHeadingCount = 0;

  for (const row of asArray<unknown>(response.aircraft)) {
    if (!Array.isArray(row)) {
      droppedCount += 1;
      continue;
    }

    const aircraftId = asString(row[indices.aircraftId]);

    const coordinates = asCoordinate(
      row[indices.longitude],
      row[indices.latitude],
    );

    // Identity and a valid position are the minimum needed to place a symbol.
    if (aircraftId === null || coordinates === null) {
      droppedCount += 1;
      continue;
    }

    // The backend caps its response but does not deduplicate; a repeated id
    // would otherwise produce overlapping symbols and an ambiguous selection.
    if (aircraftById.has(aircraftId)) {
      droppedCount += 1;
      continue;
    }

    const trackDeg = asNumber(row[indices.trackDeg]);
    const hasTrack = trackDeg !== null;

    if (!hasTrack) {
      withoutHeadingCount += 1;
    }

    aircraftById.set(aircraftId, {
      aircraftId,
      callsign: asString(row[indices.callsign]),
      longitude: coordinates[0],
      latitude: coordinates[1],
      trackDeg,
      baroAltitudeFt: asNumber(row[indices.baroAltitudeFt]),
      groundSpeedKt: asNumber(row[indices.groundSpeedKt]),
      positionTimeEpoch: asNumber(row[indices.positionTimeEpoch]),
    });

    features.push({
      type: 'Feature',
      id: aircraftId,
      geometry: { type: 'Point', coordinates },
      properties: {
        aircraftId,
        trackDeg: trackDeg ?? 0,
        hasTrack,
      },
    });
  }

  return {
    collection: { type: 'FeatureCollection', features },
    aircraftById,
    renderedCount: features.length,
    droppedCount,
    withoutHeadingCount,
    truncated,
    reportedCount,
    generatedAt,
    contractError: null,
  };
}
