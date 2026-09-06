import type { AircraftProjectionPoint } from '@/types/api';
import { asCoordinate, asNumber, asString } from '@/utils/coerce';

/**
 * Convert `projectionPoints` from `GET /aircraft/{aircraftId}` into a GeoJSON
 * source for the selected-aircraft trajectory.
 *
 * No intermediate points are invented. Coordinates that are missing, infinite
 * or out of range are dropped; remaining points keep the backend order after
 * a stable sort on `point_sequence_number` when that attribute is present.
 */

export interface ProjectionPointProperties {
  sequence: number | null;
  horizonMin: number | null;
  projectedTimeUtc: string | null;
  estimatedAltitudeFt: number | null;
  confidence: string | null;
}

export interface ProjectionLineProperties {
  kind: 'trajectory';
  pointCount: number;
  /** Distinct `horizon_min` values actually present on the points. */
  horizons: number[];
}

export type ProjectionFeature =
  | {
      type: 'Feature';
      id: string;
      geometry: {
        type: 'LineString';
        coordinates: [number, number][];
      };
      properties: ProjectionLineProperties;
    }
  | {
      type: 'Feature';
      id: string;
      geometry: {
        type: 'Point';
        coordinates: [number, number];
      };
      properties: ProjectionPointProperties;
    };

export interface ProjectionFeatureCollection {
  type: 'FeatureCollection';
  features: ProjectionFeature[];
}

export interface ProjectionGeoJsonResult {
  readonly collection: ProjectionFeatureCollection;
  readonly renderedPointCount: number;
  readonly droppedPointCount: number;
  readonly horizons: number[];
}

export const EMPTY_PROJECTION_COLLECTION: ProjectionFeatureCollection = {
  type: 'FeatureCollection',
  features: [],
};

interface ValidatedPoint {
  sequence: number | null;
  order: number;
  coordinates: [number, number];
  horizonMin: number | null;
  projectedTimeUtc: string | null;
  estimatedAltitudeFt: number | null;
  confidence: string | null;
}

function validatePoint(
  point: AircraftProjectionPoint,
  index: number,
): ValidatedPoint | null {
  const coordinates = asCoordinate(point.longitude, point.latitude);

  if (coordinates === null) {
    return null;
  }

  const sequence = asNumber(point.point_sequence_number);

  return {
    sequence,
    order: sequence ?? index,
    coordinates,
    horizonMin: asNumber(point.horizon_min),
    projectedTimeUtc: asString(point.projected_time_utc),
    estimatedAltitudeFt: asNumber(point.estimated_altitude_ft),
    confidence: asString(point.confidence),
  };
}

export function buildProjectionGeoJson(
  points: readonly AircraftProjectionPoint[] | null | undefined,
): ProjectionGeoJsonResult {
  if (points == null || points.length === 0) {
    return {
      collection: EMPTY_PROJECTION_COLLECTION,
      renderedPointCount: 0,
      droppedPointCount: 0,
      horizons: [],
    };
  }

  const valid: ValidatedPoint[] = [];
  let droppedPointCount = 0;

  points.forEach((point, index) => {
    const validated = validatePoint(point, index);

    if (validated === null) {
      droppedPointCount += 1;
      return;
    }

    valid.push(validated);
  });

  valid.sort((left, right) => {
    if (left.order !== right.order) {
      return left.order - right.order;
    }

    return left.sequence === null || right.sequence === null
      ? 0
      : left.sequence - right.sequence;
  });

  const horizons: number[] = [];
  const seenHorizons = new Set<number>();

  for (const point of valid) {
    if (point.horizonMin === null || seenHorizons.has(point.horizonMin)) {
      continue;
    }

    seenHorizons.add(point.horizonMin);
    horizons.push(point.horizonMin);
  }

  const features: ProjectionFeature[] = [];

  if (valid.length >= 2) {
    features.push({
      type: 'Feature',
      id: 'trajectory',
      geometry: {
        type: 'LineString',
        coordinates: valid.map((point) => point.coordinates),
      },
      properties: {
        kind: 'trajectory',
        pointCount: valid.length,
        horizons,
      },
    });
  }

  valid.forEach((point, index) => {
    features.push({
      type: 'Feature',
      id: `point-${point.sequence ?? index}`,
      geometry: {
        type: 'Point',
        coordinates: point.coordinates,
      },
      properties: {
        sequence: point.sequence,
        horizonMin: point.horizonMin,
        projectedTimeUtc: point.projectedTimeUtc,
        estimatedAltitudeFt: point.estimatedAltitudeFt,
        confidence: point.confidence,
      },
    });
  });

  return {
    collection:
      features.length === 0
        ? EMPTY_PROJECTION_COLLECTION
        : { type: 'FeatureCollection', features },
    renderedPointCount: valid.length,
    droppedPointCount,
    horizons,
  };
}

/**
 * Source payload for the selected-aircraft trajectory layer.
 *
 * Deselection and a missing point list both produce an empty collection so
 * the layer can be cleared with `setData` instead of being removed.
 */
export function projectionCollectionForSelection(
  selected: boolean,
  points: readonly AircraftProjectionPoint[] | null | undefined,
): ProjectionFeatureCollection {
  if (!selected) {
    return EMPTY_PROJECTION_COLLECTION;
  }

  return buildProjectionGeoJson(points).collection;
}
