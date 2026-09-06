import { describe, expect, it } from 'vitest';

import { buildAirportGeoJson } from './airportGeoJson';

describe('buildAirportGeoJson', () => {
  it('plots stored coordinates and weather-risk rank without deriving status', () => {
    const result = buildAirportGeoJson([
      {
        airport_id: 'kden',
        latitude: 39.86,
        longitude: -104.67,
        weather_risk_level: 'HIGH',
        weather_impact_status: 'WEATHER_IMPACTED',
      },
    ]);

    expect(result.renderedCount).toBe(1);
    expect(result.droppedCount).toBe(0);
    expect(result.collection.features[0]?.geometry.coordinates).toEqual([
      -104.67, 39.86,
    ]);
    expect(result.collection.features[0]?.properties).toEqual({
      airportId: 'KDEN',
      weatherRisk: 'HIGH',
      weatherImpact: 'WEATHER_IMPACTED',
      riskRank: 4,
    });
  });

  it('drops rows without a usable position instead of placing them at zero', () => {
    const result = buildAirportGeoJson([
      { airport_id: 'Kxxx', weather_risk_level: 'LOW' },
    ]);

    expect(result.renderedCount).toBe(0);
    expect(result.droppedCount).toBe(1);
  });
});
