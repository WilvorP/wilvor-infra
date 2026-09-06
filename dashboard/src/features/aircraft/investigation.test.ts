import { describe, expect, it } from 'vitest';

import { presentOverlapStatus } from '@/utils/status';

import {
  encounterHazardIds,
  formatAdvisoryAction,
  formatEvidenceReference,
  formatSourceVersions,
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

describe('altitude overlap presentation', () => {
  it('keeps UNKNOWN as Unknown and never promotes it to Yes or No', () => {
    const unknown = presentOverlapStatus('UNKNOWN');

    expect(unknown.label).toBe('Unknown');
    expect(unknown.tone).toBe('unknown');
    expect(presentOverlapStatus('YES').label).toBe('Yes');
    expect(presentOverlapStatus('NO').label).toBe('No');
    expect(presentOverlapStatus(undefined).label).toBe('Unknown');
    expect(presentOverlapStatus('INSIDE_NOW').label).toBe('Inside now');
    expect(presentOverlapStatus('OVERLAP').label).toBe('Overlap');
    expect(presentOverlapStatus('INSIDE_NOW').label).not.toBe('Yes');
  });
});
