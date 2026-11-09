import { describe, expect, it } from 'vitest';

import {
  emptyEvidenceCaption,
  evidenceIdentity,
  evidenceInvestigationPath,
  presentCopilotState,
  presentLimitationCode,
  presentSourceAuthenticity,
} from './display';
import { AIRCRAFT_EVIDENCE, HAZARD_EVIDENCE } from './fixtures';

describe('Copilot display helpers', () => {
  it('labels closed response states without inventing an operational answer', () => {
    expect(presentCopilotState('UNAVAILABLE').label).toBe('Unavailable');
    expect(presentCopilotState('UNSUPPORTED').label).toBe('Unsupported');
    expect(presentCopilotState('INSUFFICIENT_EVIDENCE').label).toBe(
      'Insufficient evidence',
    );
    expect(presentSourceAuthenticity().label).toBe('Not established');
  });

  it('explains empty evidence as not a verified zero', () => {
    expect(emptyEvidenceCaption(true)).toMatch(/not a verified-zero claim/i);
    expect(emptyEvidenceCaption(false)).toBeNull();
  });

  it('prefers callsign for aircraft identity and links the investigation route', () => {
    expect(evidenceIdentity(AIRCRAFT_EVIDENCE)).toBe('UAL9');
    expect(evidenceInvestigationPath(AIRCRAFT_EVIDENCE)).toBe('/aircraft/aa0001');
    expect(evidenceIdentity(HAZARD_EVIDENCE)).toBe('sigmet-a');
    expect(evidenceInvestigationPath(HAZARD_EVIDENCE)).toBeNull();
  });

  it('humanises limitation codes without adding instructions', () => {
    expect(presentLimitationCode('NO_OPERATIONAL_CLAIM')).toBe(
      'No operational claim',
    );
  });
});
