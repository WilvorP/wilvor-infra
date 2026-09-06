import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { describe, expect, it, vi } from 'vitest';

import type { OperationsMapProps } from '@/features/map/OperationsMap';
import { renderWithProviders } from '@/test/renderWithProviders';
import type { AirportStatus } from '@/types/api';

import { AirportsPage } from './AirportsPage';

const mapProps: { current: OperationsMapProps | null } = { current: null };

vi.mock('@/features/map/OperationsMap', () => ({
  OperationsMap: (props: OperationsMapProps) => {
    mapProps.current = props;
    return <div>Operations map</div>;
  },
}));

const KDEN: AirportStatus = {
  airport_id: 'KDEN',
  station_name: 'Denver Intl',
  weather_risk_level: 'HIGH',
  weather_impact_status: 'WEATHER_IMPACTED',
  latitude: 39.86,
  longitude: -104.67,
};

const KSFO: AirportStatus = {
  airport_id: 'KSFO',
  station_name: 'San Francisco Intl',
  weather_risk_level: 'LOW',
  weather_impact_status: 'NORMAL',
  latitude: 37.62,
  longitude: -122.38,
};

const AIRPORTS = [KDEN, KSFO];

function renderPage(path = '/airports') {
  const listAirports = vi.fn(async (request: {
    weatherRisk?: string;
    weatherImpact?: string;
  }) => {
    let items = AIRPORTS;

    if (request.weatherRisk) {
      items = items.filter(
        (item) => item.weather_risk_level === request.weatherRisk,
      );
    }

    if (request.weatherImpact) {
      items = items.filter(
        (item) => item.weather_impact_status === request.weatherImpact,
      );
    }

    return { items, count: items.length, nextToken: null };
  });

  mapProps.current = null;

  return {
    ...renderWithProviders(
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route
            path="/airports/:airportId?"
            element={<AirportsPage mapStyleUrl={null} />}
          />
        </Routes>
      </MemoryRouter>,
      {
        client: {
          overview: async () => ({
            airports: {
              currentCount: 4,
              weatherImpactedCount: 1,
              byWeatherRisk: { HIGH: 1, UNKNOWN: 0 },
            },
          }),
          listAirports,
          getAirport: vi.fn(async () => ({
            airport: KDEN,
            metar: { flight_category: 'IFR' },
            taf: null,
            tafForecastPeriods: [],
            recentAssessments: [],
          })),
        },
      },
    ),
    listAirports,
  };
}

function kpi(label: string) {
  return screen.getByRole('button', {
    name: (_accessibleName, element) =>
      element.querySelector('p')?.textContent === label,
  });
}

function mapAirportIds() {
  return (
    mapProps.current?.airports?.features.map((feature) => feature.id) ?? []
  );
}

describe('AirportsPage', () => {
  it('keeps the network page on the worklist until investigation is opened', async () => {
    renderPage();

    await waitFor(() => {
      expect(screen.getByText('KDEN')).toBeInTheDocument();
    });

    fireEvent.click(screen.getByText('KDEN').closest('button') as HTMLElement);

    expect(screen.getByRole('link', { name: 'Open investigation' })).toHaveAttribute(
      'href',
      '/airports/KDEN',
    );
    expect(
      screen.queryByRole('heading', { name: 'Current observation / METAR' }),
    ).not.toBeInTheDocument();
  });

  it('opens investigation from the route airport id', async () => {
    renderPage('/airports/KDEN');

    await waitFor(() => {
      expect(
        screen.getByRole('heading', { name: 'Current observation / METAR' }),
      ).toBeInTheDocument();
    });

    expect(screen.queryByText('Airport Intelligence')).not.toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Airports' })).toHaveAttribute(
      'href',
      '/airports',
    );
  });

  it('uses KPI cards as airport weather filters with an active state', async () => {
    const { listAirports } = renderPage();

    await waitFor(() => {
      expect(screen.getByText('KSFO')).toBeInTheDocument();
    });

    const monitored = kpi('Monitored');
    const impacted = kpi('Weather impacted');
    const high = kpi('High weather risk');
    const unknown = kpi('Unknown weather risk');

    expect(monitored).toHaveAttribute('aria-pressed', 'true');
    expect(mapAirportIds()).toEqual(['KDEN', 'KSFO']);

    fireEvent.click(impacted);

    await waitFor(() => {
      expect(screen.getByLabelText('Weather impact')).toHaveValue(
        'WEATHER_IMPACTED',
      );
      expect(screen.getByText('KDEN')).toBeInTheDocument();
      expect(screen.queryByText('KSFO')).not.toBeInTheDocument();
    });

    expect(impacted).toHaveAttribute('aria-pressed', 'true');
    expect(monitored).toHaveAttribute('aria-pressed', 'false');
    expect(impacted.className).toMatch(/active/);
    expect(mapAirportIds()).toEqual(['KDEN']);
    expect(listAirports).toHaveBeenCalledWith(
      expect.objectContaining({ weatherImpact: 'WEATHER_IMPACTED' }),
    );

    fireEvent.click(high);

    await waitFor(() => {
      expect(screen.getByLabelText('Weather risk')).toHaveValue('HIGH');
      expect(screen.getByLabelText('Weather impact')).toHaveValue('');
    });

    expect(high).toHaveAttribute('aria-pressed', 'true');
    expect(impacted).toHaveAttribute('aria-pressed', 'false');

    fireEvent.click(unknown);

    await waitFor(() => {
      expect(screen.getByLabelText('Weather risk')).toHaveValue('UNKNOWN');
      expect(
        screen.getByText('No current airport data available'),
      ).toBeInTheDocument();
    });

    expect(unknown).toHaveAttribute('aria-pressed', 'true');
    expect(within(unknown).getByText('0')).toBeInTheDocument();
    expect(mapAirportIds()).toEqual([]);
    expect(listAirports).toHaveBeenCalledWith(
      expect.objectContaining({ weatherRisk: 'UNKNOWN' }),
    );

    fireEvent.click(monitored);

    await waitFor(() => {
      expect(screen.getByLabelText('Weather risk')).toHaveValue('');
      expect(screen.getByLabelText('Weather impact')).toHaveValue('');
      expect(screen.getByText('KDEN')).toBeInTheDocument();
      expect(screen.getByText('KSFO')).toBeInTheDocument();
    });

    expect(monitored).toHaveAttribute('aria-pressed', 'true');
    expect(mapAirportIds()).toEqual(['KDEN', 'KSFO']);
  });

  it('clears a selection that the KPI weather filter excludes instead of substituting another', async () => {
    renderPage();

    await waitFor(() => {
      expect(screen.getByText('KSFO')).toBeInTheDocument();
    });

    fireEvent.click(screen.getByText('KSFO').closest('button') as HTMLElement);

    expect(screen.getByRole('link', { name: 'Open investigation' })).toHaveAttribute(
      'href',
      '/airports/KSFO',
    );

    fireEvent.click(kpi('High weather risk'));

    await waitFor(() => {
      expect(screen.getByText('KDEN')).toBeInTheDocument();
      expect(screen.queryByText('KSFO')).not.toBeInTheDocument();
    });

    expect(
      screen.queryByRole('link', { name: 'Open investigation' }),
    ).not.toBeInTheDocument();
    expect(mapProps.current?.selectedAirportId).toBeNull();
  });
});
