import { fireEvent, render, screen, within } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { EncounterKpis } from './EncounterKpis';
import { ENCOUNTER_RISK_KPI } from './encounterList';

function tile(label: string) {
  return screen.getByRole('button', {
    name: (_accessibleName, element) =>
      element.querySelector('p')?.textContent === label,
  });
}

describe('EncounterKpis', () => {
  it('reads current-set totals from overview, not loaded-page sums', () => {
    render(
      <EncounterKpis
        data={{
          encounters: {
            activeCount: 1326,
            lowRiskCount: 1200,
            mediumRiskCount: 126,
            highRiskCount: 0,
          },
        }}
        onRiskFilterChange={vi.fn()}
      />,
    );

    expect(within(tile('Current Encounters')).getByText('1,326')).toBeInTheDocument();
    expect(within(tile('LOW')).getByText('1,200')).toBeInTheDocument();
    expect(within(tile('MEDIUM')).getByText('126')).toBeInTheDocument();
    expect(within(tile('HIGH')).getByText('0')).toBeInTheDocument();
    expect(within(tile('LOW')).getByText('stored risk')).toBeInTheDocument();
  });

  it('writes the existing risk filter and marks the selected KPI active', () => {
    const onRiskFilterChange = vi.fn();

    const { rerender } = render(
      <EncounterKpis
        data={{
          encounters: {
            activeCount: 1326,
            lowRiskCount: 1200,
            mediumRiskCount: 126,
            highRiskCount: 0,
          },
        }}
        riskFilter=""
        onRiskFilterChange={onRiskFilterChange}
      />,
    );

    expect(tile('Current Encounters')).toHaveAttribute('aria-pressed', 'true');
    fireEvent.click(tile('LOW'));
    expect(onRiskFilterChange).toHaveBeenCalledWith(ENCOUNTER_RISK_KPI.low);
    fireEvent.click(tile('HIGH'));
    expect(onRiskFilterChange).toHaveBeenCalledWith(ENCOUNTER_RISK_KPI.high);

    rerender(
      <EncounterKpis
        data={{
          encounters: {
            activeCount: 1326,
            lowRiskCount: 1200,
            mediumRiskCount: 126,
            highRiskCount: 0,
          },
        }}
        riskFilter={ENCOUNTER_RISK_KPI.low}
        onRiskFilterChange={onRiskFilterChange}
      />,
    );

    expect(tile('Current Encounters')).toHaveAttribute('aria-pressed', 'false');
    expect(tile('LOW')).toHaveAttribute('aria-pressed', 'true');
    expect(tile('LOW').className).toMatch(/active/);

    fireEvent.click(tile('Current Encounters'));
    expect(onRiskFilterChange).toHaveBeenCalledWith(ENCOUNTER_RISK_KPI.all);
  });
});
