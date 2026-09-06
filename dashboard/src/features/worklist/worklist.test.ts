import { describe, expect, it } from 'vitest';

import type {
  ActiveAlert,
  ActiveEncounterItem,
  Recommendation,
} from '@/types/api';

import {
  EMPTY_WORKLIST_FILTERS,
  describeLoadedVersusCurrent,
  filterAlerts,
  filterEncounters,
  filterRecommendations,
  selectionFromAlert,
  selectionFromEncounter,
  selectionFromRecommendation,
  sortAlerts,
  sortEncounters,
  sortRecommendations,
} from './worklist';

const ENCOUNTERS: ActiveEncounterItem[] = [
  {
    encounter: {
      encounter_id: 'enc-low',
      aircraft_id: 'aa0001',
      hazard_id: 'sigmet-a',
      hazard_type: 'CONVECTION',
      encounter_state: 'DETECTED',
      detected_at_epoch: 20,
    },
    risk: { risk_id: 'risk-low', risk_level: 'LOW', risk_score: 40 },
  },
  {
    encounter: {
      encounter_id: 'enc-med',
      aircraft_id: 'bb0002',
      hazard_id: 'sigmet-b',
      hazard_type: 'ICING',
      encounter_state: 'MONITORING',
      detected_at_epoch: 10,
    },
    risk: { risk_id: 'risk-med', risk_level: 'MEDIUM', risk_score: 65 },
  },
];

const RECOMMENDATIONS: Recommendation[] = [
  {
    recommendation_id: 'rec-low',
    aircraft_id: 'aa0001',
    hazard_id: 'sigmet-a',
    risk_id: 'risk-low',
    recommendation_status: 'ACTIVE',
    primary_action_type: 'MONITOR',
    risk_level: 'LOW',
    created_at_epoch: 20,
  },
  {
    recommendation_id: 'rec-med',
    aircraft_id: 'bb0002',
    hazard_id: 'sigmet-b',
    risk_id: 'risk-med',
    recommendation_status: 'ACTIVE',
    primary_action_type: 'EVALUATE_DIVERSION',
    risk_level: 'MEDIUM',
    created_at_epoch: 10,
  },
];

const ALERTS: ActiveAlert[] = [
  {
    alert_id: 'alert-new',
    fingerprint: 'fp-new',
    aircraft_id: 'aa0001',
    hazard_id: 'sigmet-a',
    risk_id: 'risk-low',
    alert_state: 'NEW',
    risk_level: 'LOW',
    updated_at_epoch: 5,
  },
  {
    alert_id: 'alert-up',
    fingerprint: 'fp-up',
    aircraft_id: 'bb0002',
    hazard_id: 'sigmet-b',
    risk_id: 'risk-med',
    alert_state: 'UPDATED',
    risk_level: 'MEDIUM',
    updated_at_epoch: 15,
  },
];

describe('worklist selection mapping', () => {
  it('maps encounter and alert rows to explicit currentContext IDs', () => {
    expect(selectionFromEncounter(ENCOUNTERS[1]!)).toEqual({
      aircraftId: 'bb0002',
      hazardId: 'sigmet-b',
      encounterId: 'enc-med',
      riskId: 'risk-med',
      source: 'encounter',
    });
    expect(selectionFromAlert(ALERTS[0]!)).toEqual({
      aircraftId: 'aa0001',
      hazardId: 'sigmet-a',
      riskId: 'risk-low',
      recommendationId: null,
      alertId: 'alert-new',
      fingerprint: 'fp-new',
      source: 'alert',
    });
    expect(selectionFromRecommendation(RECOMMENDATIONS[1]!)).toEqual({
      aircraftId: 'bb0002',
      hazardId: 'sigmet-b',
      riskId: 'risk-med',
      recommendationId: 'rec-med',
      source: 'recommendation',
    });
  });

  it('does not invent a selection when aircraft_id is absent', () => {
    expect(selectionFromEncounter({ encounter: { encounter_id: 'enc-x' } })).toBeNull();
    expect(selectionFromAlert({ alert_id: 'alert-x' })).toBeNull();
  });
});

describe('worklist filter and sort', () => {
  it('filters and sorts by stored risk, state, aircraft, hazard and timestamps', () => {
    const medium = filterEncounters(ENCOUNTERS, {
      ...EMPTY_WORKLIST_FILTERS,
      riskLevel: 'MEDIUM',
    });

    expect(medium).toHaveLength(1);
    expect(medium[0]?.encounter?.encounter_id).toBe('enc-med');

    const icing = filterEncounters(ENCOUNTERS, {
      ...EMPTY_WORKLIST_FILTERS,
      hazard: 'icing',
    });

    expect(icing[0]?.encounter?.hazard_id).toBe('sigmet-b');

    const byTime = sortEncounters(ENCOUNTERS, 'timestamp', 'desc');

    expect(byTime.map((item) => item.encounter?.encounter_id)).toEqual([
      'enc-low',
      'enc-med',
    ]);

    const byRisk = sortEncounters(ENCOUNTERS, 'riskLevel', 'desc');

    expect(byRisk.map((item) => item.risk?.risk_level)).toEqual([
      'MEDIUM',
      'LOW',
    ]);

    const updated = filterAlerts(ALERTS, {
      ...EMPTY_WORKLIST_FILTERS,
      state: 'UPDATED',
      aircraft: 'bb',
    });

    expect(updated).toHaveLength(1);
    expect(updated[0]?.alert_id).toBe('alert-up');

    const alertsByTime = sortAlerts(ALERTS, 'timestamp', 'desc');

    expect(alertsByTime.map((item) => item.alert_id)).toEqual([
      'alert-up',
      'alert-new',
    ]);

    const attention = sortEncounters(ENCOUNTERS, 'attention', 'desc');

    expect(attention.map((item) => item.risk?.risk_level)).toEqual([
      'MEDIUM',
      'LOW',
    ]);

    const diversion = filterRecommendations(RECOMMENDATIONS, {
      ...EMPTY_WORKLIST_FILTERS,
      state: 'EVALUATE_DIVERSION',
    });

    expect(diversion).toHaveLength(1);
    expect(diversion[0]?.recommendation_id).toBe('rec-med');

    const recAttention = sortRecommendations(
      RECOMMENDATIONS,
      'attention',
      'desc',
    );

    expect(recAttention.map((item) => item.risk_level)).toEqual([
      'MEDIUM',
      'LOW',
    ]);
  });

  it('searches loaded aircraft by callsign from the map feed', () => {
    const lookup = new Map([
      ['aa0001', { callsign: 'UAL9' }],
      ['bb0002', { callsign: 'AAL2' }],
    ]);

    const matched = filterEncounters(
      ENCOUNTERS,
      { ...EMPTY_WORKLIST_FILTERS, aircraft: 'ual' },
      lookup,
    );

    expect(matched).toHaveLength(1);
    expect(matched[0]?.encounter?.aircraft_id).toBe('aa0001');
  });

  it('distinguishes loaded-page counts from current-set totals', () => {
    expect(describeLoadedVersusCurrent(50, 1326)).toBe(
      '50 loaded of 1,326 current',
    );
    expect(describeLoadedVersusCurrent(20, null)).toBe('20 loaded');
  });

  it('does not invent HIGH when the loaded set only has LOW and MEDIUM', () => {
    const attention = sortEncounters(ENCOUNTERS, 'attention', 'desc');

    expect(attention.map((item) => item.risk?.risk_level)).toEqual([
      'MEDIUM',
      'LOW',
    ]);
    expect(attention.some((item) => item.risk?.risk_level === 'HIGH')).toBe(
      false,
    );
  });
});
