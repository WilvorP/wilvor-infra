import { describe, expect, it } from 'vitest';

import type { ActiveEncounterItem } from '@/types/api';
import { presentInsideNow, presentOverlapStatus } from '@/utils/status';

import {
  EMPTY_ENCOUNTER_FILTERS,
  ENCOUNTER_RISK_KPI,
  describeLoadedEncounterScope,
  filterCurrentEncounters,
  loadedEncounterAircraftIds,
  loadedEncounterHazardIds,
  matchesEncounterRiskFilter,
  pickEncounterForAircraft,
  pickEncounterForHazard,
  resolveEncounterSelection,
  resolveMapAircraftClick,
  sortCurrentEncounters,
  withEncounterRisk,
} from './encounterList';

const ITEMS: ActiveEncounterItem[] = [
  {
    encounter: {
      encounter_id: 'enc-a1',
      aircraft_id: 'aa0001',
      hazard_id: 'sigmet-a',
      encounter_state: 'DETECTED',
      inside_now: true,
      altitude_overlap_status: 'UNKNOWN',
      detected_at_epoch: 30,
    },
    risk: { risk_id: 'risk-a1', risk_level: 'MEDIUM', risk_score: 65 },
  },
  {
    encounter: {
      encounter_id: 'enc-a2',
      aircraft_id: 'aa0001',
      hazard_id: 'sigmet-b',
      encounter_state: 'MONITORING',
      inside_now: false,
      altitude_overlap_status: 'NO_OVERLAP',
      detected_at_epoch: 20,
    },
    risk: { risk_id: 'risk-a2', risk_level: 'LOW', risk_score: 40 },
  },
  {
    encounter: {
      encounter_id: 'enc-c1',
      aircraft_id: 'cc0003',
      hazard_id: 'sigmet-a',
      encounter_state: 'DETECTED',
      inside_now: false,
      altitude_overlap_status: 'OVERLAP',
      detected_at_epoch: 10,
    },
    risk: { risk_id: 'risk-c1', risk_level: 'LOW', risk_score: 35 },
  },
];

describe('encounter list helpers', () => {
  it('keeps one row per current encounter instead of collapsing by aircraft or hazard', () => {
    expect(ITEMS.filter((item) => item.encounter?.aircraft_id === 'aa0001')).toHaveLength(2);
    expect(ITEMS.filter((item) => item.encounter?.hazard_id === 'sigmet-a')).toHaveLength(2);
    expect(loadedEncounterAircraftIds(ITEMS)).toEqual(['aa0001', 'cc0003']);
    expect(loadedEncounterHazardIds(ITEMS)).toEqual(['sigmet-a', 'sigmet-b']);
    expect(describeLoadedEncounterScope(ITEMS)).toBe(
      '2 aircraft · 2 hazards on loaded pages',
    );
  });

  it('defaults to stored risk HIGH → MEDIUM → LOW then recency, without inventing HIGH', () => {
    const ordered = sortCurrentEncounters(ITEMS, 'attention');

    expect(ordered.map((item) => item.encounter?.encounter_id)).toEqual([
      'enc-a1',
      'enc-a2',
      'enc-c1',
    ]);
    expect(ordered.some((item) => item.risk?.risk_level === 'HIGH')).toBe(false);
  });

  it('sorts loaded rows by newest, aircraft, hazard and inside-now', () => {
    expect(
      sortCurrentEncounters(ITEMS, 'newest').map(
        (item) => item.encounter?.encounter_id,
      ),
    ).toEqual(['enc-a1', 'enc-a2', 'enc-c1']);
    expect(
      sortCurrentEncounters(ITEMS, 'aircraft').map(
        (item) => item.encounter?.aircraft_id,
      ),
    ).toEqual(['aa0001', 'aa0001', 'cc0003']);
    expect(
      sortCurrentEncounters(ITEMS, 'hazard').map(
        (item) => item.encounter?.hazard_id,
      ),
    ).toEqual(['sigmet-a', 'sigmet-a', 'sigmet-b']);
    expect(
      sortCurrentEncounters(ITEMS, 'insideNow')[0]?.encounter?.encounter_id,
    ).toBe('enc-a1');
  });

  it('applies KPI risk filters to the existing loaded-page filter state', () => {
    const low = withEncounterRisk(
      EMPTY_ENCOUNTER_FILTERS,
      ENCOUNTER_RISK_KPI.low,
    );

    expect(matchesEncounterRiskFilter(ITEMS[0]!, ENCOUNTER_RISK_KPI.low)).toBe(
      false,
    );
    expect(matchesEncounterRiskFilter(ITEMS[1]!, ENCOUNTER_RISK_KPI.low)).toBe(
      true,
    );
    expect(
      filterCurrentEncounters(ITEMS, low).map(
        (item) => item.encounter?.encounter_id,
      ),
    ).toEqual(['enc-a2', 'enc-c1']);
  });

  it('filters loaded pages by risk, inside-now and altitude without changing UNKNOWN', () => {
    const inside = filterCurrentEncounters(ITEMS, {
      ...EMPTY_ENCOUNTER_FILTERS,
      insideNow: 'yes',
    });
    expect(inside.map((item) => item.encounter?.encounter_id)).toEqual(['enc-a1']);

    const altitude = filterCurrentEncounters(ITEMS, {
      ...EMPTY_ENCOUNTER_FILTERS,
      altitude: 'UNKNOWN',
    });
    expect(altitude).toHaveLength(1);
    expect(altitude[0]?.encounter?.altitude_overlap_status).toBe('UNKNOWN');
    expect(presentOverlapStatus('UNKNOWN').label).toBe('Unknown');
    expect(presentOverlapStatus('UNKNOWN').label).not.toBe('Yes');
    expect(presentInsideNow(undefined).label).toBe('—');
  });

  it('selects by encounter id, not merely aircraft id', () => {
    const seen = new Set(['enc-a1', 'enc-a2', 'enc-gone']);

    expect(
      resolveEncounterSelection('enc-a2', ITEMS, seen).status,
    ).toBe('current');
    expect(
      resolveEncounterSelection('enc-gone', ITEMS, seen),
    ).toEqual({ status: 'resolved', encounterId: 'enc-gone' });
    expect(
      resolveEncounterSelection('enc-later', ITEMS, new Set()),
    ).toEqual({ status: 'unloaded', encounterId: 'enc-later' });

    expect(
      pickEncounterForAircraft(ITEMS, 'aa0001', 'enc-a2')?.encounter
        ?.encounter_id,
    ).toBe('enc-a2');
    expect(
      pickEncounterForHazard(ITEMS, 'sigmet-a', 'enc-c1')?.encounter
        ?.encounter_id,
    ).toBe('enc-c1');
  });

  it('does not auto-pick a newest encounter when a map aircraft has several current rows', () => {
    expect(resolveMapAircraftClick(ITEMS, 'cc0003')).toEqual({
      kind: 'single',
      item: ITEMS[2],
    });

    const multiple = resolveMapAircraftClick(ITEMS, 'aa0001');

    expect(multiple.kind).toBe('multiple');
    if (multiple.kind !== 'multiple') {
      return;
    }

    expect(multiple.items.map((item) => item.encounter?.encounter_id)).toEqual([
      'enc-a1',
      'enc-a2',
    ]);
    expect(resolveMapAircraftClick(ITEMS, 'missing').kind).toBe('none');
  });
});
