import type { MapAircraft } from '@/features/map/aircraftGeoJson';
import type { AircraftCurrentState } from '@/types/api';
import { asBoolean, asCoordinate, asNumber, asString } from '@/utils/coerce';

/**
 * Presentation helpers for `GET /aircraft`.
 *
 * That route returns current AircraftCurrentState rows. It is not a current
 * encounter/alert set and it does not carry risk.
 */

export interface AircraftListFilters {
  callsign: string;
  h3Cell: string;
}

export const EMPTY_AIRCRAFT_LIST_FILTERS: AircraftListFilters = {
  callsign: '',
  h3Cell: '',
};

export function aircraftListRowKey(
  item: AircraftCurrentState,
  index: number,
): string {
  return asString(item.aircraft_id) ?? `aircraft-${index}`;
}

export function aircraftIdFromListItem(
  item: AircraftCurrentState,
): string | null {
  return asString(item.aircraft_id)?.toLowerCase() ?? null;
}

/** Exact callsign sent to `/aircraft?callsign=`. Empty → omitted. */
export function committedCallsign(value: string): string | undefined {
  const callsign = asString(value)?.toUpperCase();

  return callsign ?? undefined;
}

/** H3 cell sent to `/aircraft?h3Cell=`. Empty → omitted. */
export function committedH3Cell(value: string): string | undefined {
  return asString(value) ?? undefined;
}

/**
 * Build a map-layer aircraft from a list row when the map feed has not
 * placed it. Position is required; no coordinates are invented.
 */
export function mapAircraftFromListItem(
  item: AircraftCurrentState,
): MapAircraft | null {
  const aircraftId = aircraftIdFromListItem(item);

  if (aircraftId === null || asBoolean(item.has_position) === false) {
    return null;
  }

  const coordinates = asCoordinate(item.longitude, item.latitude);

  if (coordinates === null) {
    return null;
  }

  return {
    aircraftId,
    callsign: asString(item.callsign),
    longitude: coordinates[0],
    latitude: coordinates[1],
    trackDeg: asNumber(item.track_deg),
    baroAltitudeFt: asNumber(item.baro_altitude_ft),
    groundSpeedKt: asNumber(item.ground_speed_kt),
    positionTimeEpoch: asNumber(item.position_time_epoch),
  };
}

export function describeLoadedFleet(
  loadedCount: number,
  trackedCount: number | null | undefined,
): string {
  if (trackedCount == null) {
    return `${loadedCount.toLocaleString('en-US')} loaded`;
  }

  return `${loadedCount.toLocaleString('en-US')} loaded of ${trackedCount.toLocaleString('en-US')} tracked`;
}
