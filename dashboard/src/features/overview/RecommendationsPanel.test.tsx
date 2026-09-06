import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { RecommendationsPanel } from './RecommendationsPanel';

describe('RecommendationsPanel', () => {
  it('uses the current-set total, not retained activeCount', () => {
    render(
      <RecommendationsPanel
        recommendations={[
          {
            recommendationId: 'rec-1',
            aircraftId: 'aa9038',
            action: 'MONITOR',
            riskLevel: 'LOW',
            validUntilUtc: '2026-09-06T03:55:00Z',
          },
        ]}
        currentCount={1326}
        loading={false}
        failed={false}
      />,
    );

    expect(screen.getByText('Current recommendations')).toBeInTheDocument();
    expect(screen.getByText('1 of 1326')).toBeInTheDocument();
  });

  it('does not invent a zero when currentCount is absent', () => {
    render(
      <RecommendationsPanel
        recommendations={[]}
        currentCount={undefined}
        loading={false}
        failed={false}
      />,
    );

    expect(screen.getByText('No current recommendations')).toBeInTheDocument();
    expect(screen.queryByText(/of /)).not.toBeInTheDocument();
  });
});
