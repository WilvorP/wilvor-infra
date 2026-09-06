import { fireEvent, render, screen, within } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { RecommendationKpis } from './RecommendationKpis';
import { RECOMMENDATION_ACTION_KPI } from './recommendationList';

function tile(label: string) {
  return screen.getByRole('button', {
    name: (_accessibleName, element) =>
      element.querySelector('p')?.textContent === label,
  });
}

describe('RecommendationKpis', () => {
  it('uses overview currentCount, not retained activeCount', () => {
    render(
      <RecommendationKpis
        data={{
          recommendations: {
            currentCount: 293,
            activeCount: 5000,
            latest: [],
          },
        }}
        loadedActions={{ monitor: 40, prepare: 9, diversion: 1 }}
        onActionFilterChange={vi.fn()}
      />,
    );

    expect(within(tile('Current Recommendations')).getByText('293')).toBeInTheDocument();
    expect(
      within(tile('Current Recommendations')).queryByText('5,000'),
    ).not.toBeInTheDocument();
    expect(within(tile('Monitor')).getByText('40')).toBeInTheDocument();
    expect(within(tile('Monitor')).getByText('loaded pages')).toBeInTheDocument();
    expect(within(tile('Monitor and prepare')).getByText('9')).toBeInTheDocument();
    expect(within(tile('Evaluate diversion')).getByText('1')).toBeInTheDocument();
  });

  it('writes the existing action filter and marks the selected KPI active', () => {
    const onActionFilterChange = vi.fn();

    const { rerender } = render(
      <RecommendationKpis
        data={{ recommendations: { currentCount: 293, latest: [] } }}
        loadedActions={{ monitor: 40, prepare: 9, diversion: 0 }}
        actionFilter=""
        onActionFilterChange={onActionFilterChange}
      />,
    );

    expect(tile('Current Recommendations')).toHaveAttribute('aria-pressed', 'true');
    expect(tile('Monitor')).toHaveAttribute('aria-pressed', 'false');

    fireEvent.click(tile('Monitor'));
    expect(onActionFilterChange).toHaveBeenCalledWith(
      RECOMMENDATION_ACTION_KPI.monitor,
    );

    fireEvent.click(tile('Monitor and prepare'));
    expect(onActionFilterChange).toHaveBeenCalledWith(
      RECOMMENDATION_ACTION_KPI.prepare,
    );

    fireEvent.click(tile('Evaluate diversion'));
    expect(onActionFilterChange).toHaveBeenCalledWith(
      RECOMMENDATION_ACTION_KPI.diversion,
    );

    rerender(
      <RecommendationKpis
        data={{ recommendations: { currentCount: 293, latest: [] } }}
        loadedActions={{ monitor: 40, prepare: 9, diversion: 0 }}
        actionFilter={RECOMMENDATION_ACTION_KPI.monitor}
        onActionFilterChange={onActionFilterChange}
      />,
    );

    expect(tile('Current Recommendations')).toHaveAttribute('aria-pressed', 'false');
    expect(tile('Monitor')).toHaveAttribute('aria-pressed', 'true');
    expect(tile('Monitor').className).toMatch(/active/);

    fireEvent.click(tile('Current Recommendations'));
    expect(onActionFilterChange).toHaveBeenCalledWith(
      RECOMMENDATION_ACTION_KPI.all,
    );
  });
});
