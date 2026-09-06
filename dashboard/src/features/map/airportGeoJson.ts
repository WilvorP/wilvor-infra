import type { AirportStatus } from '@/types/api';
import { asCoordinate, asString } from '@/utils/coerce';
import { riskRank } from '@/utils/status';

export interface AirportFeatureProperties {
  readonly airportId: string;
  readonly weatherRisk: string;
  readonly weatherImpact: string;
  readonly riskRank: number;
}

export interface AirportFeature {
  readonly type: 'Feature';
  readonly id: string;
  readonly geometry: { readonly type: 'Point'; readonly coordinates: [number, number] };
  readonly properties: AirportFeatureProperties;
}

export interface AirportFeatureCollection {
  readonly type: 'FeatureCollection';
  readonly features: readonly AirportFeature[];
}

export interface AirportGeoJsonResult {
  readonly collection: AirportFeatureCollection;
  readonly renderedCount: number;
  readonly droppedCount: number;
}

export const EMPTY_AIRPORT_COLLECTION: AirportFeatureCollection = {
  type: 'FeatureCollection',
  features: [],
};

const EMPTY_RESULT: AirportGeoJsonResult = {
  collection: EMPTY_AIRPORT_COLLECTION,
  renderedCount: 0,
  droppedCount: 0,
};

/**
 * Reshape current `AirportStatus` rows into a MapLibre GeoJSON source.
 *
 * Coordinates come from the stored item. Status paint uses the stored
 * `weather_risk_level` rank. No weather or congestion is derived here.
 */
export function buildAirportGeoJson(
  items: readonly AirportStatus[] | null | undefined,
): AirportGeoJsonResult {
  if (items == null || items.length === 0) {
    return EMPTY_RESULT;
  }

  const features: AirportFeature[] = [];
  let droppedCount = 0;

  for (const item of items) {
    const airportId = asString(item.airport_id)?.toUpperCase();
    const coordinates = asCoordinate(item.longitude, item.latitude);

    if (airportId == null || coordinates == null) {
      droppedCount += 1;
      continue;
    }

    const weatherRisk = asString(item.weather_risk_level)?.toUpperCase() ?? 'UNKNOWN';
    const weatherImpact =
      asString(item.weather_impact_status)?.toUpperCase() ?? 'UNKNOWN';

    features.push({
      type: 'Feature',
      id: airportId,
      geometry: { type: 'Point', coordinates },
      properties: {
        airportId,
        weatherRisk,
        weatherImpact,
        riskRank: riskRank(weatherRisk),
      },
    });
  }

  return {
    collection: { type: 'FeatureCollection', features },
    renderedCount: features.length,
    droppedCount,
  };
}
