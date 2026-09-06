import type { AirportStatus } from '@/types/api';
import { asNumber, asString } from '@/utils/coerce';
import { riskRank, weatherImpactRank } from '@/utils/status';

export const EMPTY_AIRPORT_LIST_FILTERS = {
  weatherRisk: '',
  weatherImpact: '',
  query: '',
} as const;

export type AirportListFilters = {
  weatherRisk: string;
  weatherImpact: string;
  query: string;
};

export const AIRPORT_KPI_FILTER = {
  all: '',
  impacted: 'WEATHER_IMPACTED',
  highRisk: 'HIGH',
  unknownRisk: 'UNKNOWN',
} as const;

export function withAirportImpact(
  filters: AirportListFilters,
  weatherImpact: string,
): AirportListFilters {
  return { ...filters, weatherRisk: '', weatherImpact };
}

export function withAirportWeatherRisk(
  filters: AirportListFilters,
  weatherRisk: string,
): AirportListFilters {
  return { ...filters, weatherRisk, weatherImpact: '' };
}

export function clearAirportWeatherFilters(
  filters: AirportListFilters,
): AirportListFilters {
  return { ...filters, weatherRisk: '', weatherImpact: '' };
}

export function matchesAirportWeatherFilters(
  item: AirportStatus,
  filters: AirportListFilters,
): boolean {
  const risk = committedWeatherFilter(filters.weatherRisk);
  const impact = committedWeatherFilter(filters.weatherImpact);

  if (
    risk !== undefined &&
    asString(item.weather_risk_level)?.toUpperCase() !== risk
  ) {
    return false;
  }

  if (
    impact !== undefined &&
    asString(item.weather_impact_status)?.toUpperCase() !== impact
  ) {
    return false;
  }

  return matchesAirportQuery(item, filters.query);
}

export type AirportListSort =
  | 'weatherRisk'
  | 'weatherImpact'
  | 'airportId'
  | 'updated';

export function airportIdFromStatus(item: AirportStatus): string | null {
  return asString(item.airport_id)?.toUpperCase() ?? null;
}

export function airportListRowKey(item: AirportStatus, index: number): string {
  return airportIdFromStatus(item) ?? `airport-${index}`;
}

export function committedWeatherFilter(value: string): string | undefined {
  const token = asString(value)?.toUpperCase();

  return token ?? undefined;
}

export function describeLoadedAirports(
  loadedCount: number,
  currentCount: number | null | undefined,
): string {
  if (asNumber(currentCount) === null) {
    return `${loadedCount} loaded`;
  }

  return `${loadedCount} of ${currentCount} current`;
}

export function matchesAirportQuery(
  item: AirportStatus,
  query: string,
): boolean {
  const needle = asString(query)?.toUpperCase() ?? null;

  if (needle === null) {
    return true;
  }

  const haystacks = [
    asString(item.airport_id),
    asString(item.station_id),
    asString(item.station_name),
    asString(item.iata_code),
  ]
    .map((value) => value?.toUpperCase() ?? '')
    .filter((value) => value.length > 0);

  return haystacks.some((value) => value.includes(needle));
}

export function compareAirports(
  left: AirportStatus,
  right: AirportStatus,
  sort: AirportListSort,
): number {
  if (sort === 'airportId') {
    return (airportIdFromStatus(left) ?? '').localeCompare(
      airportIdFromStatus(right) ?? '',
    );
  }

  if (sort === 'updated') {
    return (
      (asNumber(right.updated_at_epoch) ?? 0) -
      (asNumber(left.updated_at_epoch) ?? 0)
    );
  }

  if (sort === 'weatherImpact') {
    const impact =
      weatherImpactRank(right.weather_impact_status) -
      weatherImpactRank(left.weather_impact_status);

    if (impact !== 0) {
      return impact;
    }
  }

  const risk =
    riskRank(right.weather_risk_level) - riskRank(left.weather_risk_level);

  if (risk !== 0) {
    return risk;
  }

  const impact =
    weatherImpactRank(right.weather_impact_status) -
    weatherImpactRank(left.weather_impact_status);

  if (impact !== 0) {
    return impact;
  }

  return (airportIdFromStatus(left) ?? '').localeCompare(
    airportIdFromStatus(right) ?? '',
  );
}

export function visibleAirports(
  items: readonly AirportStatus[],
  query: string,
  sort: AirportListSort,
): AirportStatus[] {
  return items
    .filter((item) => matchesAirportQuery(item, query))
    .slice()
    .sort((left, right) => compareAirports(left, right, sort));
}
