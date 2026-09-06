import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, expect, it, vi } from 'vitest';

import { SelectedAirportStrip } from './SelectedAirportStrip';

describe('SelectedAirportStrip', () => {
  it('links to airport investigation without embedding METAR or TAF', () => {
    render(
      <MemoryRouter>
        <SelectedAirportStrip
          airportId="KDEN"
          airport={{
            airport_id: 'KDEN',
            station_name: 'Denver Intl',
            weather_impact_status: 'WEATHER_IMPACTED',
            weather_risk_level: 'HIGH',
          }}
          onClear={vi.fn()}
        />
      </MemoryRouter>,
    );

    expect(screen.getByText('Denver Intl')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Open investigation' })).toHaveAttribute(
      'href',
      '/airports/KDEN',
    );
    expect(screen.queryByText('Current observation / METAR')).not.toBeInTheDocument();
  });
});
