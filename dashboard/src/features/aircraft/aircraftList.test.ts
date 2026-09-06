import { describe, expect, it } from 'vitest';

import {
  aircraftIdFromListItem,
  committedCallsign,
  committedH3Cell,
  describeLoadedFleet,
  mapAircraftFromListItem,
} from './aircraftList';

describe('aircraft list helpers', () => {
  it('commits exact callsign and H3 finds and omits blanks', () => {
    expect(committedCallsign('  ual9  ')).toBe('UAL9');
    expect(committedCallsign('   ')).toBeUndefined();
    expect(committedH3Cell('8428347ffffffff')).toBe('8428347ffffffff');
    expect(committedH3Cell('')).toBeUndefined();
  });

  it('builds a map aircraft only when the list row has a usable position', () => {
    expect(
      mapAircraftFromListItem({
        aircraft_id: 'AA0001',
        callsign: 'UAL9',
        longitude: -122.3,
        latitude: 37.6,
        has_position: true,
        track_deg: 270,
        baro_altitude_ft: 35000,
        ground_speed_kt: 430,
        position_time_epoch: 1786515880,
      }),
    ).toEqual({
      aircraftId: 'aa0001',
      callsign: 'UAL9',
      longitude: -122.3,
      latitude: 37.6,
      trackDeg: 270,
      baroAltitudeFt: 35000,
      groundSpeedKt: 430,
      positionTimeEpoch: 1786515880,
    });

    expect(
      mapAircraftFromListItem({
        aircraft_id: 'aa0001',
        has_position: false,
        longitude: -122.3,
        latitude: 37.6,
      }),
    ).toBeNull();
  });

  it('does not invent an aircraft id or a tracked total', () => {
    expect(aircraftIdFromListItem({})).toBeNull();
    expect(describeLoadedFleet(50, 3412)).toBe('50 loaded of 3,412 tracked');
    expect(describeLoadedFleet(20, null)).toBe('20 loaded');
  });
});
