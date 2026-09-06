import { describe, expect, it } from 'vitest';

import { presentOverlapStatus } from '@/utils/status';

import {
  aircraftInvestigationPath,
  contextSelectionFromSearch,
  contextSelectionIsExplicit,
  currentContextHazardIds,
  encounterHazardIds,
  formatAdvisoryAction,
  formatEvidenceReference,
  formatSourceVersions,
  highestCurrentRisk,
  matchCurrentContext,
  uniqueHorizons,
} from './investigation';

describe('investigation presentation helpers', () => {
  it('collects unique encounter hazard ids in first-seen order', () => {
    expect(
      encounterHazardIds([
        { hazard_id: 'sigmet-a' },
        { hazard_id: 'sigmet-b' },
        { hazard_id: 'sigmet-a' },
        { hazard_id: '  ' },
        {},
      ]),
    ).toEqual(['sigmet-a', 'sigmet-b']);
  });

  it('preserves advisory action wording instead of turning it into an instruction', () => {
    expect(formatAdvisoryAction('EVALUATE_DIVERSION')).toBe(
      'Evaluate diversion',
    );
    expect(formatAdvisoryAction('EVALUATE_DIVERSION')).not.toMatch(/divert to/i);
    expect(formatAdvisoryAction('MONITOR_AND_PREPARE_OPTIONS')).toBe(
      'Monitor and prepare options',
    );
  });

  it('formats evidence references from stored type and identifiers', () => {
    expect(
      formatEvidenceReference({
        type: 'RISK_RESULT',
        id: 'risk#1',
      }),
    ).toBe('Risk result · risk#1');
    expect(formatEvidenceReference({})).toBeNull();
  });

  it('renders source versions as labelled stored values', () => {
    expect(
      formatSourceVersions({
        hazard_source_version: 'v3',
        airport_evaluation_id: null,
      }),
    ).toEqual(['Hazard source version: v3']);
  });

  it('lists distinct projection horizons without inventing any', () => {
    expect(
      uniqueHorizons([
        { horizon_min: 5 },
        { horizon_min: 10 },
        { horizon_min: 5 },
        { latitude: 1 },
      ]),
    ).toEqual([5, 10]);
  });
});

describe('current operational context helpers', () => {
  it('collects hazard ids from current contexts only', () => {
    expect(
      currentContextHazardIds([
        { encounter: { hazard_id: 'sigmet-a' } },
        { encounter: { hazard_id: 'sigmet-b' } },
        { encounter: { hazard_id: 'sigmet-a' } },
        { risk: { risk_id: 'risk-1' } },
      ]),
    ).toEqual(['sigmet-a', 'sigmet-b']);
  });

  it('matches a current context by stored IDs, not latest timestamp', () => {
    const older = {
      encounter: { encounter_id: 'enc-old', hazard_id: 'hz-old' },
      risk: { risk_id: 'risk-old', generated_at_utc: '2026-09-06T03:00:00Z' },
      alert: { alert_id: 'alert-old', fingerprint: 'fp-old' },
    };
    const target = {
      encounter: { encounter_id: 'enc-2', hazard_id: 'hz-2' },
      risk: { risk_id: 'risk-2', generated_at_utc: '2026-09-06T01:00:00Z' },
      alert: { alert_id: 'alert-2', fingerprint: 'fp-2' },
    };

    expect(
      matchCurrentContext([older, target], {
        aircraftId: 'aa0001',
        encounterId: 'enc-2',
      })?.risk?.risk_id,
    ).toBe('risk-2');
    expect(
      matchCurrentContext([older, target], {
        aircraftId: 'aa0001',
        riskId: 'risk-2',
      })?.encounter?.encounter_id,
    ).toBe('enc-2');
    expect(
      matchCurrentContext([older, target], {
        aircraftId: 'aa0001',
        alertId: 'alert-2',
      })?.alert?.fingerprint,
    ).toBe('fp-2');
    expect(
      matchCurrentContext([older, target], {
        aircraftId: 'aa0001',
        fingerprint: 'fp-2',
      })?.alert?.alert_id,
    ).toBe('alert-2');
    expect(
      matchCurrentContext([older, target], {
        aircraftId: 'aa0001',
        encounterId: 'missing',
        riskId: 'also-missing',
      }),
    ).toBeNull();
    expect(
      matchCurrentContext(
        [
          {
            encounter: { encounter_id: 'enc-a', hazard_id: 'hz-a' },
            risk: { risk_id: 'risk-a' },
            recommendation: { recommendation_id: 'rec-a' },
          },
          {
            encounter: { encounter_id: 'enc-b', hazard_id: 'hz-b' },
            risk: { risk_id: 'risk-b', generated_at_utc: '2026-09-06T09:00:00Z' },
            recommendation: { recommendation_id: 'rec-b' },
          },
        ],
        { aircraftId: 'aa0001', recommendationId: 'rec-a' },
      )?.encounter?.hazard_id,
    ).toBe('hz-a');
    expect(
      contextSelectionIsExplicit({ aircraftId: 'aa0001', hazardId: 'hz-2' }),
    ).toBe(false);
  });

  it('round-trips worklist IDs through the aircraft investigation URL', () => {
    const path = aircraftInvestigationPath({
      aircraftId: 'aa0001',
      encounterId: 'enc-2',
      riskId: 'risk-2',
      hazardId: 'hz-2',
      source: 'encounter',
    });

    expect(path).toContain('/aircraft/aa0001?');

    const search = new URLSearchParams(path.split('?')[1]);
    const restored = contextSelectionFromSearch('aa0001', search);

    expect(restored.encounterId).toBe('enc-2');
    expect(restored.riskId).toBe('risk-2');
    expect(restored.hazardId).toBe('hz-2');
    expect(restored.source).toBe('encounter');
  });

  it('selects the highest stored current risk without mixing timestamps', () => {
    expect(
      highestCurrentRisk([
        { risk: { risk_id: 'low', risk_level: 'LOW', risk_score: 90 } },
        { risk: { risk_id: 'high', risk_level: 'HIGH', risk_score: 10 } },
        { risk: { risk_id: 'med', risk_level: 'MEDIUM', risk_score: 80 } },
      ])?.risk_id,
    ).toBe('high');
  });
});

describe('altitude overlap presentation', () => {
  it('keeps UNKNOWN as Unknown and never promotes it to Yes or No', () => {
    const unknown = presentOverlapStatus('UNKNOWN');

    expect(unknown.label).toBe('Unknown');
    expect(unknown.tone).toBe('unknown');
    expect(presentOverlapStatus('YES').label).toBe('Yes');
    expect(presentOverlapStatus('NO').label).toBe('No');
    expect(presentOverlapStatus(undefined).label).toBe('Unknown');
    expect(presentOverlapStatus('INSIDE_NOW').label).toBe('Inside hazard now');
    expect(presentOverlapStatus('OVERLAP').label).toBe('Overlap');
    expect(presentOverlapStatus('NO_OVERLAP').label).toBe('No overlap');
    expect(presentOverlapStatus('INSIDE_NOW').label).not.toBe('Yes');
  });
});
