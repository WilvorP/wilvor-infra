import { fireEvent, render, screen, within } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { AlertKpis } from './AlertKpis';
import { ALERT_STATE_KPI } from './alertList';

function tile(label: string) {
  return screen.getByRole('button', {
    name: (_accessibleName, element) =>
      element.querySelector('p')?.textContent === label,
  });
}

describe('AlertKpis', () => {
  it('uses overview currentCount, not retained activeCount', () => {
    render(
      <AlertKpis
        data={{
          alerts: {
            currentCount: 132,
            activeCount: 2356,
            byState: { NEW: 800, UPDATED: 900 },
          },
        }}
        loadedStates={{ new: 20, updated: 18, escalated: 2, monitoring: 10 }}
        onStateFilterChange={vi.fn()}
      />,
    );

    expect(within(tile('Current Alerts')).getByText('132')).toBeInTheDocument();
    expect(
      within(tile('Current Alerts')).queryByText('2,356'),
    ).not.toBeInTheDocument();
    expect(screen.queryByText('800')).not.toBeInTheDocument();
    expect(within(tile('New')).getByText('20')).toBeInTheDocument();
    expect(within(tile('New')).getByText('loaded pages')).toBeInTheDocument();
    expect(within(tile('Updated')).getByText('18')).toBeInTheDocument();
    expect(within(tile('Escalated')).getByText('2')).toBeInTheDocument();
    expect(within(tile('Monitoring')).getByText('10')).toBeInTheDocument();
  });

  it('writes the existing state filter and marks the selected KPI active', () => {
    const onStateFilterChange = vi.fn();

    const { rerender } = render(
      <AlertKpis
        data={{ alerts: { currentCount: 132 } }}
        loadedStates={{ new: 20, updated: 18, escalated: 0, monitoring: 10 }}
        stateFilter=""
        onStateFilterChange={onStateFilterChange}
      />,
    );

    expect(tile('Current Alerts')).toHaveAttribute('aria-pressed', 'true');
    expect(tile('New')).toHaveAttribute('aria-pressed', 'false');

    fireEvent.click(tile('New'));
    expect(onStateFilterChange).toHaveBeenCalledWith(ALERT_STATE_KPI.new);

    fireEvent.click(tile('Updated'));
    expect(onStateFilterChange).toHaveBeenCalledWith(ALERT_STATE_KPI.updated);

    fireEvent.click(tile('Escalated'));
    expect(onStateFilterChange).toHaveBeenCalledWith(ALERT_STATE_KPI.escalated);

    fireEvent.click(tile('Monitoring'));
    expect(onStateFilterChange).toHaveBeenCalledWith(ALERT_STATE_KPI.monitoring);

    rerender(
      <AlertKpis
        data={{ alerts: { currentCount: 132 } }}
        loadedStates={{ new: 20, updated: 18, escalated: 0, monitoring: 10 }}
        stateFilter={ALERT_STATE_KPI.new}
        onStateFilterChange={onStateFilterChange}
      />,
    );

    expect(tile('Current Alerts')).toHaveAttribute('aria-pressed', 'false');
    expect(tile('New')).toHaveAttribute('aria-pressed', 'true');
    expect(tile('New').className).toMatch(/active/);

    fireEvent.click(tile('Current Alerts'));
    expect(onStateFilterChange).toHaveBeenCalledWith(ALERT_STATE_KPI.all);
  });
});
