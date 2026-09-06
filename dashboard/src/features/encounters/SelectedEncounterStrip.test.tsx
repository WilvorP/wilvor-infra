import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, expect, it, vi } from 'vitest';

import { SelectedEncounterStrip } from './SelectedEncounterStrip';

const ITEM = {
  encounter: {
    encounter_id: 'enc-a1',
    aircraft_id: 'aa0001',
    hazard_id: 'sigmet-a',
    projection_id: 'proj-1',
    encounter_state: 'DETECTED',
    geometry_overlap_status: 'INSIDE_NOW',
    time_overlap_status: 'OVERLAP',
    altitude_overlap_status: 'UNKNOWN',
    inside_now: true,
  },
  risk: {
    risk_id: 'risk-a1',
    risk_level: 'MEDIUM',
    risk_score: 65,
    confidence: 'HIGH',
    reasons: ['Aircraft is inside the convection SIGMET now.'],
  },
};

describe('SelectedEncounterStrip', () => {
  it('links to Aircraft Investigation with stored encounter and risk ids', () => {
    render(
      <MemoryRouter>
        <SelectedEncounterStrip
          encounterId="enc-a1"
          item={ITEM}
          callsign="UAL9"
          presence="current"
          onClear={vi.fn()}
        />
      </MemoryRouter>,
    );

    expect(screen.getByText('UAL9')).toBeInTheDocument();
    expect(screen.getByText('Aircraft is inside the convection SIGMET now.')).toBeInTheDocument();
    expect(screen.getByText(/Altitude Unknown/)).toBeInTheDocument();
    expect(
      screen.getByRole('link', { name: 'Open Aircraft Investigation' }),
    ).toHaveAttribute(
      'href',
      '/aircraft/aa0001?hazardId=sigmet-a&encounterId=enc-a1&riskId=risk-a1&source=encounter',
    );
    expect(screen.queryByText('Recent encounters')).not.toBeInTheDocument();
  });

  it('keeps a resolved encounter selected instead of substituting another', () => {
    render(
      <MemoryRouter>
        <SelectedEncounterStrip
          encounterId="enc-gone"
          item={null}
          callsign={null}
          presence="resolved"
          onClear={vi.fn()}
        />
      </MemoryRouter>,
    );

    expect(screen.getByText('This encounter is no longer current.')).toBeInTheDocument();
    expect(
      screen.queryByRole('link', { name: 'Open Aircraft Investigation' }),
    ).not.toBeInTheDocument();
  });
});
