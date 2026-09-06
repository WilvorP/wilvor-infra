import type { ActiveHazard, HazardGeometry } from '@/types/api';
import { asNumber, asString } from '@/utils/coerce';
import { hazardSeverityToRiskLevel, riskRank } from '@/utils/status';

/**
 * Convert `GET /hazards/active` items into a GeoJSON source for MapLibre.
 *
 * The API reconstructs ordered polygon rings from the `HazardCoordinates`
 * rows and attaches them as `geometry`, but only when reconstruction produced
 * at least one valid ring (`_hazard_geometry` in repository.py). Hazards whose
 * geometry could not be rebuilt are therefore counted and reported rather than
 * silently dropped: on an operational display, a hazard that exists but cannot
 * be drawn must not be indistinguishable from no hazard at all.
 *
 * No geometry is computed here. Rings arrive closed and ordered from the
 * backend; this module only reshapes them into features.
 */

export interface HazardFeatureProperties {
  hazardId: string;
  hazardType: string;
  severity: string | null;
  productType: string | null;
  validFromUtc: string | null;
  validToUtc: string | null;
  minAltitudeFt: number | null;
  maxAltitudeFt: number | null;
  /** Drives layer paint. Derived from `severity`, never recomputed from data. */
  severityRank: number;
  /** True when the selected aircraft's encounter references this hazard. */
  emphasized: boolean;
}

export interface HazardFeature {
  type: 'Feature';
  id: string;
  geometry: HazardGeometry;
  properties: HazardFeatureProperties;
}

export interface HazardFeatureCollection {
  type: 'FeatureCollection';
  features: HazardFeature[];
}

export interface HazardGeoJsonResult {
  readonly collection: HazardFeatureCollection;
  /** Hazards returned by the API that carried no renderable geometry. */
  readonly withoutGeometryCount: number;
  readonly renderedCount: number;
}

export const EMPTY_HAZARD_COLLECTION: HazardFeatureCollection = {
  type: 'FeatureCollection',
  features: [],
};

const NO_EMPHASIZED_HAZARDS: ReadonlySet<string> = new Set();

function hasRenderableGeometry(
  geometry: HazardGeometry | undefined,
): geometry is HazardGeometry {
  if (!geometry || !Array.isArray(geometry.coordinates)) {
    return false;
  }

  return geometry.coordinates.length > 0;
}

export function buildHazardGeoJson(
  hazards: readonly ActiveHazard[],
  emphasizedHazardIds: ReadonlySet<string> = NO_EMPHASIZED_HAZARDS,
): HazardGeoJsonResult {
  const features: HazardFeature[] = [];
  let withoutGeometryCount = 0;

  for (const hazard of hazards) {
    const hazardId = asString(hazard.hazard_id);

    if (hazardId === null) {
      continue;
    }

    if (!hasRenderableGeometry(hazard.geometry)) {
      withoutGeometryCount += 1;
      continue;
    }

    features.push({
      type: 'Feature',
      id: hazardId,
      geometry: hazard.geometry,
      properties: {
        hazardId,
        hazardType: asString(hazard.hazard_type) ?? 'UNKNOWN',
        severity: asString(hazard.severity),
        productType: asString(hazard.product_type),
        validFromUtc: asString(hazard.valid_from_utc),
        validToUtc: asString(hazard.valid_to_utc),
        minAltitudeFt: asNumber(hazard.minimum_lower_altitude_ft),
        maxAltitudeFt: asNumber(hazard.maximum_upper_altitude_ft),
        severityRank: riskRank(hazardSeverityToRiskLevel(hazard.severity)),
        emphasized: emphasizedHazardIds.has(hazardId),
      },
    });
  }

  return {
    collection: { type: 'FeatureCollection', features },
    withoutGeometryCount,
    renderedCount: features.length,
  };
}
