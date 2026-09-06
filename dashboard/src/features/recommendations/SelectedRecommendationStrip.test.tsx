import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, expect, it, vi } from 'vitest';

import type { Recommendation } from '@/types/api';

import { SelectedRecommendationStrip } from './SelectedRecommendationStrip';

const ITEM: Recommendation = {
  recommendation_id: 'rec-a2',
  aircraft_id: 'aa0001',
  hazard_id: 'sigmet-b',
  risk_id: 'risk-a2',
  recommendation_status: 'ACTIVE',
  primary_action_type: 'MONITOR_AND_PREPARE_OPTIONS',
  primary_action_details: {
    advisory: 'Continue the flight and prepare diversion options.',
  },
  risk_level: 'MEDIUM',
  risk_score: 57,
  confidence: 'LOW',
  reasons: ['Corridor intersects convection within the projection horizon.'],
  limitations: ['Airport weather evidence is incomplete.'],
};

describe('SelectedRecommendationStrip', () => {
  it('links to Aircraft Investigation with stored recommendation and risk ids', () => {
    render(
      <MemoryRouter>
        <SelectedRecommendationStrip
          recommendationId="rec-a2"
          item={ITEM}
          callsign="UAL9"
          presence="current"
          onClear={vi.fn()}
        />
      </MemoryRouter>,
    );

    expect(screen.getByText('UAL9')).toBeInTheDocument();
    expect(screen.getAllByText('Monitor and prepare options').length).toBeGreaterThan(
      0,
    );
    expect(
      screen.getByText('Corridor intersects convection within the projection horizon.'),
    ).toBeInTheDocument();
    expect(
      screen.getByText('Airport weather evidence is incomplete.'),
    ).toBeInTheDocument();
    expect(screen.queryByText('DIVERT NOW')).not.toBeInTheDocument();
    expect(
      screen.getByRole('link', { name: 'Open Aircraft Investigation' }),
    ).toHaveAttribute(
      'href',
      '/aircraft/aa0001?hazardId=sigmet-b&riskId=risk-a2&recommendationId=rec-a2&source=recommendation',
    );
    expect(
      screen.getByRole('link', { name: 'Open Aircraft Investigation' }),
    ).not.toHaveAttribute('href', expect.stringContaining('encounterId='));
  });

  it('reports missing reasons and limitations without fabricating them', () => {
    render(
      <MemoryRouter>
        <SelectedRecommendationStrip
          recommendationId="rec-a1"
          item={{
            ...ITEM,
            recommendation_id: 'rec-a1',
            primary_action_type: 'MONITOR',
            reasons: [],
            limitations: [],
          }}
          callsign="UAL9"
          presence="current"
          onClear={vi.fn()}
        />
      </MemoryRouter>,
    );

    expect(
      screen.getByText('No stored reasons on this recommendation.'),
    ).toBeInTheDocument();
    expect(
      screen.getByText('No stored limitations on this recommendation.'),
    ).toBeInTheDocument();
  });

  it('states airport evidence is unavailable for diversion without candidates', () => {
    render(
      <MemoryRouter>
        <SelectedRecommendationStrip
          recommendationId="rec-c1"
          item={{
            ...ITEM,
            recommendation_id: 'rec-c1',
            primary_action_type: 'EVALUATE_DIVERSION',
            reasons: ['Stronger evidence is required before diversion.'],
            limitations: [],
          }}
          callsign={null}
          presence="current"
          onClear={vi.fn()}
        />
      </MemoryRouter>,
    );

    expect(screen.getAllByText('Evaluate diversion').length).toBeGreaterThan(0);
    expect(screen.getByText('Airport evidence unavailable')).toBeInTheDocument();
    expect(screen.queryByText('DIVERT')).not.toBeInTheDocument();
    expect(screen.queryByText('RECOMMENDED AIRPORT')).not.toBeInTheDocument();
  });

  it('keeps a resolved recommendation selected instead of substituting another', () => {
    render(
      <MemoryRouter>
        <SelectedRecommendationStrip
          recommendationId="rec-gone"
          item={null}
          callsign={null}
          presence="resolved"
          onClear={vi.fn()}
        />
      </MemoryRouter>,
    );

    expect(
      screen.getByText('This recommendation is no longer current.'),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole('link', { name: 'Open Aircraft Investigation' }),
    ).not.toBeInTheDocument();
  });
});
