import { render, screen, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import type { OverviewResponse } from '@/types/api';

import { OverviewKpis } from './OverviewKpis';

const OVERVIEW: OverviewResponse = {
  generatedAt: '2026-09-03T12:00:00Z',
  aircraft: { activeCount: 3412 },
  hazards: { activeCount: 27 },
  encounters: {
    activeCount: 14,
    riskEvaluatedCount: 12,
    highRiskCount: 3,
    mediumRiskCount: 5,
    lowRiskCount: 4,
    riskCounts: { HIGH: 3, MEDIUM: 5, LOW: 4 },
  },
  recommendations: { activeCount: 6, latest: [] },
  alerts: { activeCount: 4, byState: { NEW: 2, ESCALATED: 1, UPDATED: 1 } },
  airports: { currentCount: 118, weatherImpactedCount: 9 },
};

function tile(label: string): HTMLElement {
  return screen.getByText(label).closest('div') as HTMLElement;
}

describe('OverviewKpis', () => {
  it('renders counts from the overview response', () => {
    render(<OverviewKpis data={OVERVIEW} />);

    expect(within(tile('Aircraft')).getByText('3,412')).toBeInTheDocument();
    expect(within(tile('Active hazards')).getByText('27')).toBeInTheDocument();
    expect(within(tile('Encounters')).getByText('14')).toBeInTheDocument();
    expect(within(tile('Airports')).getByText('118')).toBeInTheDocument();
  });

  it('renders the encounter risk breakdown', () => {
    render(<OverviewKpis data={OVERVIEW} />);

    const encounters = tile('Encounters');

    expect(within(encounters).getByText('High')).toBeInTheDocument();
    expect(within(encounters).getByText('3')).toBeInTheDocument();
    expect(within(encounters).getByText('5')).toBeInTheDocument();
  });

  it('shows a not-reported marker rather than zero for absent metrics', () => {
    // Absent must never be rendered as 0: "no aircraft tracked" and "aircraft
    // count unknown" are different operational situations.
    render(<OverviewKpis data={{}} />);

    expect(within(tile('Aircraft')).getByText('—')).toBeInTheDocument();
  });

  it('distinguishes a genuine zero from an absent value', () => {
    render(<OverviewKpis data={{ hazards: { activeCount: 0 } }} />);

    expect(within(tile('Active hazards')).getByText('0')).toBeInTheDocument();
  });

  it('tolerates null sub-objects without throwing', () => {
    render(
      <OverviewKpis
        data={{ aircraft: null, encounters: null, alerts: null, airports: null }}
      />,
    );

    expect(screen.getAllByText('—').length).toBeGreaterThan(0);
  });

  it('marks values carried over from an earlier refresh as stale', () => {
    render(<OverviewKpis data={OVERVIEW} stale />);

    expect(screen.getAllByText('stale').length).toBe(6);
  });

  it('replaces values with a problem message when supplied', () => {
    render(<OverviewKpis data={undefined} problem="Overview unavailable" />);

    expect(screen.getAllByText('Overview unavailable').length).toBe(6);
    expect(screen.queryByText('3,412')).not.toBeInTheDocument();
  });
});
