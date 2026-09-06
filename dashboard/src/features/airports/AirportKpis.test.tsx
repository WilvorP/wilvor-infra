import { fireEvent, render, screen, within } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { AirportKpis } from './AirportKpis';

function tile(label: string) {
  return screen.getByRole('button', {
    name: (_accessibleName, element) =>
      element.querySelector('p')?.textContent === label,
  });
}

const DATA = {
  airports: {
    currentCount: 12,
    weatherImpactedCount: 3,
    byWeatherRisk: { HIGH: 2, UNKNOWN: 0 },
  },
};

describe('AirportKpis', () => {
  it('reads overview airport counts', () => {
    render(<AirportKpis data={DATA} onKpiSelect={vi.fn()} />);

    expect(within(tile('Monitored')).getByText('12')).toBeInTheDocument();
    expect(within(tile('Weather impacted')).getByText('3')).toBeInTheDocument();
    expect(within(tile('High weather risk')).getByText('2')).toBeInTheDocument();
    expect(within(tile('Unknown weather risk')).getByText('0')).toBeInTheDocument();
  });

  it('writes the existing weather filters and marks the selected KPI active', () => {
    const onKpiSelect = vi.fn();

    const { rerender } = render(
      <AirportKpis data={DATA} onKpiSelect={onKpiSelect} />,
    );

    expect(tile('Monitored')).toHaveAttribute('aria-pressed', 'true');
    fireEvent.click(tile('Weather impacted'));
    expect(onKpiSelect).toHaveBeenCalledWith('impacted');
    fireEvent.click(tile('High weather risk'));
    expect(onKpiSelect).toHaveBeenCalledWith('highRisk');
    fireEvent.click(tile('Unknown weather risk'));
    expect(onKpiSelect).toHaveBeenCalledWith('unknownRisk');

    rerender(
      <AirportKpis
        data={DATA}
        weatherRisk="HIGH"
        weatherImpact=""
        onKpiSelect={onKpiSelect}
      />,
    );

    expect(tile('Monitored')).toHaveAttribute('aria-pressed', 'false');
    expect(tile('Weather impacted')).toHaveAttribute('aria-pressed', 'false');
    expect(tile('High weather risk')).toHaveAttribute('aria-pressed', 'true');
    expect(tile('High weather risk').className).toMatch(/active/);

    rerender(
      <AirportKpis
        data={DATA}
        weatherRisk="HIGH"
        weatherImpact="WEATHER_IMPACTED"
        onKpiSelect={onKpiSelect}
      />,
    );

    expect(tile('Weather impacted')).toHaveAttribute('aria-pressed', 'false');
    expect(tile('High weather risk')).toHaveAttribute('aria-pressed', 'false');

    fireEvent.click(tile('Monitored'));
    expect(onKpiSelect).toHaveBeenCalledWith('all');
  });
});
