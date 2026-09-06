import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import type { RiskResult } from '@/types/api';

import { TopRisksPanel } from './TopRisksPanel';

const RISKS: RiskResult[] = [
  {
    risk_id: 'risk#1',
    aircraft_id: 'a1b2c3',
    hazard_type: 'TURBULENCE',
    risk_level: 'HIGH',
    risk_score: 82,
    confidence: 'MEDIUM',
    generated_at_utc: '2026-09-03T11:58:00Z',
  },
  {
    risk_id: 'risk#2',
    aircraft_id: 'd4e5f6',
    hazard_type: 'ICING',
    risk_level: 'MEDIUM',
    risk_score: 54,
    confidence: 'HIGH',
    generated_at_utc: '2026-09-03T11:57:00Z',
  },
];

describe('TopRisksPanel', () => {
  it('shows a loading state before data arrives', () => {
    render(<TopRisksPanel risks={undefined} loading failed={false} />);

    expect(screen.getByRole('status')).toBeInTheDocument();
    expect(screen.getByText(/Loading risk evaluations/i)).toBeInTheDocument();
  });

  it('renders each risk with a text level label and score', () => {
    render(<TopRisksPanel risks={RISKS} loading={false} failed={false} />);

    expect(screen.getByText('High')).toBeInTheDocument();
    expect(screen.getByText('Medium')).toBeInTheDocument();
    expect(screen.getByText('82')).toBeInTheDocument();
    expect(screen.getByText('A1B2C3')).toBeInTheDocument();
    expect(screen.getByText('Turbulence')).toBeInTheDocument();
  });

  it('preserves the ranking returned by the backend', () => {
    // The overview already sorts by level, then score, then recency.
    render(<TopRisksPanel risks={RISKS} loading={false} failed={false} />);

    const items = screen.getAllByRole('listitem');

    expect(items[0]).toHaveTextContent('A1B2C3');
    expect(items[1]).toHaveTextContent('D4E5F6');
  });

  it('distinguishes a quiet network from a failed request', () => {
    const { rerender } = render(
      <TopRisksPanel risks={[]} loading={false} failed={false} />,
    );

    expect(screen.getByText('No active risk evaluations')).toBeInTheDocument();
    expect(screen.getByText(/normal quiet state/i)).toBeInTheDocument();

    rerender(<TopRisksPanel risks={undefined} loading={false} failed />);

    expect(screen.getByText('Risk evaluations unavailable')).toBeInTheDocument();
  });

  it('renders a risk whose optional attributes are absent', () => {
    render(
      <TopRisksPanel
        risks={[{ risk_id: 'risk#3' }]}
        loading={false}
        failed={false}
      />,
    );

    expect(screen.getByText('Unknown')).toBeInTheDocument();
    // Missing score, hazard type and confidence all fall back to the marker.
    expect(screen.getAllByText(/—/).length).toBeGreaterThan(0);
  });
});
