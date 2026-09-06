import { describe, expect, it } from 'vitest';

import type { AirportStatus } from '@/types/api';

import {
  EMPTY_AIRPORT_LIST_FILTERS,
  compareAirports,
  matchesAirportQuery,
  matchesAirportWeatherFilters,
  visibleAirports,
  withAirportWeatherRisk,
} from './airportList';

const DEN: AirportStatus = {
  airport_id: 'KDEN',
  station_name: 'Denver Intl',
  iata_code: 'DEN',
  weather_risk_level: 'HIGH',
  weather_impact_status: 'WEATHER_IMPACTED',
  updated_at_epoch: 100,
};

const SFO: AirportStatus = {
  airport_id: 'KSFO',
  station_name: 'San Francisco Intl',
  weather_risk_level: 'LOW',
  weather_impact_status: 'NORMAL',
  updated_at_epoch: 200,
};

const ORD: AirportStatus = {
  airport_id: 'KORD',
  weather_risk_level: 'UNKNOWN',
  weather_impact_status: 'UNKNOWN',
  updated_at_epoch: 150,
};

describe('airportList', () => {
  it('matches KPI weather filters without inventing impact or risk', () => {
    const high = withAirportWeatherRisk(
      { ...EMPTY_AIRPORT_LIST_FILTERS, weatherImpact: 'WEATHER_IMPACTED' },
      'HIGH',
    );

    expect(high.weatherImpact).toBe('');
    expect(matchesAirportWeatherFilters(DEN, high)).toBe(true);
    expect(matchesAirportWeatherFilters(SFO, high)).toBe(false);
    expect(matchesAirportWeatherFilters(ORD, high)).toBe(false);
  });

  it('finds ICAO, name and IATA on loaded rows only', () => {
    expect(matchesAirportQuery(DEN, 'den')).toBe(true);
    expect(matchesAirportQuery(DEN, 'denver')).toBe(true);
    expect(matchesAirportQuery(SFO, 'den')).toBe(false);
  });

  it('sorts by stored weather risk without inventing a composite score', () => {
    const ordered = [SFO, ORD, DEN].sort((left, right) =>
      compareAirports(left, right, 'weatherRisk'),
    );

    expect(ordered.map((item) => item.airport_id)).toEqual([
      'KDEN',
      'KSFO',
      'KORD',
    ]);
  });

  it('keeps UNKNOWN ahead of NORMAL when sorting by impact', () => {
    const ordered = visibleAirports([SFO, ORD], '', 'weatherImpact');

    expect(ordered.map((item) => item.airport_id)).toEqual(['KORD', 'KSFO']);
  });
});
