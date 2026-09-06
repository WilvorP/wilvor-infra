import { describe, expect, it } from 'vitest';

import type { ActiveAlert } from '@/types/api';
import { presentAlertState, presentRiskLevel } from '@/utils/status';

import {
  ALERT_STATE_KPI,
  EMPTY_ALERT_FILTERS,
  alertIdOf,
  countLoadedAlertStates,
  describeLoadedAlertScope,
  filterCurrentAlerts,
  loadedAlertAircraftIds,
  loadedAlertsForAircraft,
  matchesAlertStateFilter,
  pickAlertForHazard,
  resolveAlertSelection,
  resolveMapAircraftAlertClick,
  selectionFromAlert,
  sortCurrentAlerts,
  withAlertState,
} from './alertList';

const ITEMS: ActiveAlert[] = [
  {
    alert_id: 'alert-a1',
    fingerprint: 'fp-a1',
    aircraft_id: 'aa0001',
    hazard_id: 'sigmet-a',
    risk_id: 'risk-a1',
    recommendation_id: 'rec-a1',
    alert_state: 'NEW',
    risk_level: 'MEDIUM',
    risk_score: 57,
    primary_action_type: 'MONITOR_AND_PREPARE_OPTIONS',
    message:
      'Aircraft aa0001 has MEDIUM weather-hazard risk. Advisory action: MONITOR_AND_PREPARE_OPTIONS.',
    state_reason: 'New material weather-hazard advisory condition.',
    updated_at_epoch: 30,
  },
  {
    alert_id: 'alert-a2',
    fingerprint: 'fp-a2',
    aircraft_id: 'aa0001',
    hazard_id: 'sigmet-b',
    risk_id: 'risk-a2',
    recommendation_id: 'rec-a2',
    alert_state: 'UPDATED',
    risk_level: 'HIGH',
    risk_score: 80,
    primary_action_type: 'EVALUATE_DIVERSION',
    updated_at_epoch: 40,
  },
  {
    alert_id: 'alert-c1',
    fingerprint: 'fp-c1',
    aircraft_id: 'cc0003',
    hazard_id: 'sigmet-a',
    risk_id: 'risk-c1',
    recommendation_id: 'rec-c1',
    alert_state: 'MONITORING',
    risk_level: 'LOW',
    risk_score: 40,
    primary_action_type: 'MONITOR',
    updated_at_epoch: 10,
  },
];

describe('alert list helpers', () => {
  it('keeps one row per alert instead of collapsing by aircraft', () => {
    expect(ITEMS.filter((item) => item.aircraft_id === 'aa0001')).toHaveLength(2);
    expect(loadedAlertAircraftIds(ITEMS)).toEqual(['aa0001', 'cc0003']);
    expect(describeLoadedAlertScope(ITEMS)).toBe(
      '2 aircraft · 2 hazards on loaded pages',
    );
  });

  it('defaults to newest updated_at_epoch without inventing a priority score', () => {
    expect(sortCurrentAlerts(ITEMS, 'newest').map(alertIdOf)).toEqual([
      'alert-a2',
      'alert-a1',
      'alert-c1',
    ]);
    expect(sortCurrentAlerts(ITEMS, 'risk').map((item) => item.risk_level)).toEqual(
      ['HIGH', 'MEDIUM', 'LOW'],
    );
    expect(sortCurrentAlerts(ITEMS, 'state').map((item) => item.alert_state)).toEqual(
      ['MONITORING', 'NEW', 'UPDATED'],
    );
    expect(
      sortCurrentAlerts(ITEMS, 'aircraft').map((item) => item.aircraft_id),
    ).toEqual(['aa0001', 'aa0001', 'cc0003']);
    expect(sortCurrentAlerts(ITEMS, 'hazard').map((item) => item.hazard_id)).toEqual(
      ['sigmet-a', 'sigmet-a', 'sigmet-b'],
    );
  });

  it('applies KPI state filters to the existing loaded-page filter state', () => {
    expect(
      matchesAlertStateFilter(ITEMS[0]!, ALERT_STATE_KPI.new),
    ).toBe(true);
    expect(
      matchesAlertStateFilter(ITEMS[1]!, ALERT_STATE_KPI.new),
    ).toBe(false);
    expect(matchesAlertStateFilter(ITEMS[1]!, '')).toBe(true);

    const next = withAlertState(EMPTY_ALERT_FILTERS, ALERT_STATE_KPI.new);
    expect(filterCurrentAlerts(ITEMS, next).map(alertIdOf)).toEqual(['alert-a1']);
  });

  it('filters loaded pages by state, risk and action without deriving them', () => {
    const updated = filterCurrentAlerts(ITEMS, {
      ...EMPTY_ALERT_FILTERS,
      state: 'UPDATED',
    });
    expect(updated.map(alertIdOf)).toEqual(['alert-a2']);

    const medium = filterCurrentAlerts(ITEMS, {
      ...EMPTY_ALERT_FILTERS,
      riskLevel: 'MEDIUM',
    });
    expect(medium).toHaveLength(1);
    expect(medium[0]?.alert_state).toBe('NEW');

    const monitor = filterCurrentAlerts(ITEMS, {
      ...EMPTY_ALERT_FILTERS,
      action: 'MONITOR',
    });
    expect(monitor.map(alertIdOf)).toEqual(['alert-c1']);
  });

  it('presents backend alert states without changing their meaning', () => {
    expect(presentAlertState('NEW').label).toBe('New');
    expect(presentAlertState('UPDATED').label).toBe('Updated');
    expect(presentAlertState('ESCALATED').label).toBe('Escalated');
    expect(presentAlertState('MONITORING').label).toBe('Monitoring');
    expect(presentAlertState('NEW').glyph).toBe('◆');
    expect(presentRiskLevel('LOW').label).toBe('Low');
    expect(presentRiskLevel('MEDIUM').label).toBe('Medium');
    expect(presentRiskLevel('HIGH').label).toBe('High');
  });

  it('selects by alert_id, not merely aircraft id', () => {
    const seen = new Set(['alert-a1', 'alert-a2', 'alert-gone']);

    expect(resolveAlertSelection('alert-a2', ITEMS, seen).status).toBe('current');
    expect(resolveAlertSelection('alert-gone', ITEMS, seen)).toEqual({
      status: 'resolved',
      alertId: 'alert-gone',
    });
    expect(resolveAlertSelection('alert-later', ITEMS, new Set())).toEqual({
      status: 'unloaded',
      alertId: 'alert-later',
    });
    expect(pickAlertForHazard(ITEMS, 'sigmet-a', 'alert-c1')?.alert_id).toBe(
      'alert-c1',
    );
  });

  it('does not auto-pick a newest alert when a map aircraft has several current rows', () => {
    expect(resolveMapAircraftAlertClick(ITEMS, 'cc0003')).toEqual({
      kind: 'single',
      item: ITEMS[2],
    });

    const multiple = resolveMapAircraftAlertClick(ITEMS, 'aa0001');

    expect(multiple.kind).toBe('multiple');
    if (multiple.kind !== 'multiple') {
      return;
    }

    expect(multiple.items.map(alertIdOf)).toEqual(['alert-a1', 'alert-a2']);
    expect(
      loadedAlertsForAircraft(ITEMS, 'aa0001').map((item) => item.updated_at_epoch),
    ).not.toEqual([40, 30]);
    expect(resolveMapAircraftAlertClick(ITEMS, 'missing').kind).toBe('none');
    expect(resolveMapAircraftAlertClick(ITEMS, 'AA0001').kind).toBe('multiple');
  });

  it('counts loaded-page states only', () => {
    expect(countLoadedAlertStates(ITEMS)).toEqual({
      new: 1,
      updated: 1,
      escalated: 0,
      monitoring: 1,
    });
    expect(
      countLoadedAlertStates([
        ...ITEMS,
        { alert_id: 'alert-e1', alert_state: 'ESCALATED' },
      ]),
    ).toEqual({
      new: 1,
      updated: 1,
      escalated: 1,
      monitoring: 1,
    });
  });

  it('maps investigation IDs without fabricating encounter_id', () => {
    expect(selectionFromAlert(ITEMS[0]!)).toEqual({
      aircraftId: 'aa0001',
      hazardId: 'sigmet-a',
      riskId: 'risk-a1',
      recommendationId: 'rec-a1',
      alertId: 'alert-a1',
      fingerprint: 'fp-a1',
      source: 'alert',
    });
    expect(selectionFromAlert(ITEMS[0]!)).not.toHaveProperty('encounterId');
  });
});
