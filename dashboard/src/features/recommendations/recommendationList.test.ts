import { describe, expect, it } from 'vitest';

import type { Recommendation } from '@/types/api';
import { presentRecommendationAction } from '@/utils/status';

import {
  EMPTY_RECOMMENDATION_FILTERS,
  RECOMMENDATION_ACTION_KPI,
  countLoadedActions,
  describeLoadedRecommendationScope,
  filterCurrentRecommendations,
  firstReason,
  loadedRecommendationAircraftIds,
  loadedRecommendationsForAircraft,
  matchesRecommendationActionFilter,
  pickRecommendationForHazard,
  presentAirportEvidence,
  recommendationIdOf,
  resolveMapAircraftRecommendationClick,
  resolveRecommendationSelection,
  selectionFromRecommendation,
  sortCurrentRecommendations,
  withRecommendationAction,
} from './recommendationList';

const ITEMS: Recommendation[] = [
  {
    recommendation_id: 'rec-a1',
    aircraft_id: 'aa0001',
    hazard_id: 'sigmet-a',
    risk_id: 'risk-a1',
    recommendation_status: 'ACTIVE',
    primary_action_type: 'MONITOR',
    risk_level: 'LOW',
    risk_score: 40,
    confidence: 'HIGH',
    reasons: ['Remain on the planned route and continue monitoring.'],
    created_at_epoch: 30,
  },
  {
    recommendation_id: 'rec-a2',
    aircraft_id: 'aa0001',
    hazard_id: 'sigmet-b',
    risk_id: 'risk-a2',
    recommendation_status: 'ACTIVE',
    primary_action_type: 'MONITOR_AND_PREPARE_OPTIONS',
    risk_level: 'MEDIUM',
    risk_score: 57,
    confidence: 'LOW',
    created_at_epoch: 40,
  },
  {
    recommendation_id: 'rec-c1',
    aircraft_id: 'cc0003',
    hazard_id: 'sigmet-a',
    risk_id: 'risk-c1',
    recommendation_status: 'ACTIVE',
    primary_action_type: 'EVALUATE_DIVERSION',
    risk_level: 'HIGH',
    risk_score: 80,
    confidence: 'MEDIUM',
    created_at_epoch: 10,
  },
];

describe('recommendation list helpers', () => {
  it('keeps one row per recommendation instead of collapsing by aircraft', () => {
    expect(ITEMS.filter((item) => item.aircraft_id === 'aa0001')).toHaveLength(2);
    expect(loadedRecommendationAircraftIds(ITEMS)).toEqual(['aa0001', 'cc0003']);
    expect(describeLoadedRecommendationScope(ITEMS)).toBe(
      '2 aircraft · 2 hazards on loaded pages',
    );
  });

  it('defaults to newest created_at_epoch without inventing a priority score', () => {
    expect(
      sortCurrentRecommendations(ITEMS, 'newest').map(recommendationIdOf),
    ).toEqual(['rec-a2', 'rec-a1', 'rec-c1']);
    expect(
      sortCurrentRecommendations(ITEMS, 'risk').map((item) => item.risk_level),
    ).toEqual(['HIGH', 'MEDIUM', 'LOW']);
    expect(
      sortCurrentRecommendations(ITEMS, 'action').map(
        (item) => item.primary_action_type,
      ),
    ).toEqual([
      'EVALUATE_DIVERSION',
      'MONITOR',
      'MONITOR_AND_PREPARE_OPTIONS',
    ]);
    expect(
      sortCurrentRecommendations(ITEMS, 'aircraft').map((item) => item.aircraft_id),
    ).toEqual(['aa0001', 'aa0001', 'cc0003']);
    expect(
      sortCurrentRecommendations(ITEMS, 'hazard').map((item) => item.hazard_id),
    ).toEqual(['sigmet-a', 'sigmet-a', 'sigmet-b']);
  });

  it('applies KPI action filters to the existing loaded-page filter state', () => {
    expect(
      matchesRecommendationActionFilter(
        ITEMS[0]!,
        RECOMMENDATION_ACTION_KPI.monitor,
      ),
    ).toBe(true);
    expect(
      matchesRecommendationActionFilter(
        ITEMS[1]!,
        RECOMMENDATION_ACTION_KPI.monitor,
      ),
    ).toBe(false);
    expect(matchesRecommendationActionFilter(ITEMS[1]!, '')).toBe(true);

    const monitor = withRecommendationAction(
      EMPTY_RECOMMENDATION_FILTERS,
      RECOMMENDATION_ACTION_KPI.monitor,
    );
    expect(
      filterCurrentRecommendations(ITEMS, monitor).map(recommendationIdOf),
    ).toEqual(['rec-a1']);
  });

  it('filters loaded pages by action, state and risk without deriving an action', () => {
    const prepare = filterCurrentRecommendations(ITEMS, {
      ...EMPTY_RECOMMENDATION_FILTERS,
      action: 'MONITOR_AND_PREPARE_OPTIONS',
    });
    expect(prepare.map(recommendationIdOf)).toEqual(['rec-a2']);

    const medium = filterCurrentRecommendations(ITEMS, {
      ...EMPTY_RECOMMENDATION_FILTERS,
      riskLevel: 'MEDIUM',
    });
    expect(medium).toHaveLength(1);
    expect(medium[0]?.primary_action_type).toBe('MONITOR_AND_PREPARE_OPTIONS');
  });

  it('presents stored actions without renaming them into commands', () => {
    expect(presentRecommendationAction('MONITOR').label).toBe('Monitor');
    expect(presentRecommendationAction('MONITOR_AND_PREPARE_OPTIONS').label).toBe(
      'Monitor and prepare options',
    );
    expect(presentRecommendationAction('EVALUATE_DIVERSION').label).toBe(
      'Evaluate diversion',
    );
    expect(presentRecommendationAction('EVALUATE_DIVERSION').label).not.toBe(
      'Divert now',
    );
    expect(presentRecommendationAction(undefined).label).toBe('Unknown');
  });

  it('selects by recommendation_id, not merely aircraft id', () => {
    const seen = new Set(['rec-a1', 'rec-a2', 'rec-gone']);

    expect(resolveRecommendationSelection('rec-a2', ITEMS, seen).status).toBe(
      'current',
    );
    expect(resolveRecommendationSelection('rec-gone', ITEMS, seen)).toEqual({
      status: 'resolved',
      recommendationId: 'rec-gone',
    });
    expect(resolveRecommendationSelection('rec-later', ITEMS, new Set())).toEqual({
      status: 'unloaded',
      recommendationId: 'rec-later',
    });
    expect(
      pickRecommendationForHazard(ITEMS, 'sigmet-a', 'rec-c1')?.recommendation_id,
    ).toBe('rec-c1');
  });

  it('does not auto-pick a newest recommendation when a map aircraft has several current rows', () => {
    expect(resolveMapAircraftRecommendationClick(ITEMS, 'cc0003')).toEqual({
      kind: 'single',
      item: ITEMS[2],
    });

    const multiple = resolveMapAircraftRecommendationClick(ITEMS, 'aa0001');

    expect(multiple.kind).toBe('multiple');
    if (multiple.kind !== 'multiple') {
      return;
    }

    expect(multiple.items.map(recommendationIdOf)).toEqual(['rec-a1', 'rec-a2']);
    expect(
      loadedRecommendationsForAircraft(ITEMS, 'aa0001').map(
        (item) => item.created_at_epoch,
      ),
    ).not.toEqual([40, 30]);
    expect(resolveMapAircraftRecommendationClick(ITEMS, 'missing').kind).toBe(
      'none',
    );
  });

  it('counts loaded-page actions only', () => {
    expect(countLoadedActions(ITEMS)).toEqual({
      monitor: 1,
      prepare: 1,
      diversion: 1,
    });
  });

  it('maps investigation IDs without fabricating encounter_id', () => {
    expect(selectionFromRecommendation(ITEMS[0]!)).toEqual({
      aircraftId: 'aa0001',
      hazardId: 'sigmet-a',
      riskId: 'risk-a1',
      recommendationId: 'rec-a1',
      source: 'recommendation',
    });
    expect(selectionFromRecommendation(ITEMS[0]!)).not.toHaveProperty(
      'encounterId',
    );
  });

  it('exposes stored reasons and airport evidence without inventing them', () => {
    expect(firstReason(ITEMS[0]!)).toBe(
      'Remain on the planned route and continue monitoring.',
    );
    expect(firstReason(ITEMS[1]!)).toBeNull();
    expect(presentAirportEvidence(ITEMS[2]!)).toEqual({ kind: 'unavailable' });
    expect(
      presentAirportEvidence({
        ...ITEMS[2]!,
        no_suitable_candidate_reason: 'No suitable diversion airport scored.',
      }),
    ).toEqual({
      kind: 'limitation',
      reason: 'No suitable diversion airport scored.',
    });
    expect(
      presentAirportEvidence({
        ...ITEMS[2]!,
        preferred_airport_id: 'KDEN',
      }),
    ).toEqual({ kind: 'preferred', airportId: 'KDEN' });
    expect(presentAirportEvidence(ITEMS[0]!)).toEqual({ kind: 'none' });
  });
});
