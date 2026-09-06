import { useState } from 'react';
import { fireEvent, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { renderWithProviders } from '@/test/renderWithProviders';
import type { AirportStatus } from '@/types/api';

import { AirportWorklist } from './AirportWorklist';
import {
  EMPTY_AIRPORT_LIST_FILTERS,
  type AirportListFilters,
} from './airportList';

const AIRPORTS: AirportStatus[] = [
  {
    airport_id: 'KDEN',
    station_name: 'Denver Intl',
    weather_risk_level: 'HIGH',
    weather_impact_status: 'WEATHER_IMPACTED',
    flight_category: 'IFR',
    metar_freshness_status: 'FRESH',
    taf_freshness_status: 'FRESH',
    assessment_status: 'EVALUATED',
    updated_at_utc: '2026-01-01T00:00:00Z',
    updated_at_epoch: 1767225600,
  },
  {
    airport_id: 'KSFO',
    station_name: 'San Francisco Intl',
    weather_risk_level: 'LOW',
    weather_impact_status: 'NORMAL',
    flight_category: 'VFR',
    metar_freshness_status: 'STALE',
    taf_freshness_status: 'ACCEPTABLE',
    assessment_status: 'PARTIALLY_EVALUATED',
    updated_at_utc: '2026-01-01T00:10:00Z',
    updated_at_epoch: 1767226200,
  },
];

function WorklistHarness(
  props: Partial<Parameters<typeof AirportWorklist>[0]> = {},
) {
  const [filters, setFilters] = useState<AirportListFilters>(
    EMPTY_AIRPORT_LIST_FILTERS,
  );

  return (
    <AirportWorklist
      selectedAirportId={null}
      onSelect={vi.fn()}
      filters={filters}
      onFiltersChange={setFilters}
      {...props}
    />
  );
}

function renderList(
  props: Partial<Parameters<typeof AirportWorklist>[0]> = {},
  items: AirportStatus[] = AIRPORTS,
) {
  return renderWithProviders(
    <WorklistHarness {...props} />,
    {
      client: {
        listAirports: vi.fn(async (request) => ({
          items:
            request.weatherRisk === 'HIGH'
              ? items.filter((item) => item.weather_risk_level === 'HIGH')
              : items,
          count: items.length,
          nextToken: 'cursor-2',
        })),
        overview: async () => ({
          airports: { currentCount: 12, weatherImpactedCount: 1 },
        }),
      },
    },
  );
}

describe('AirportWorklist', () => {
  it('renders stored status text, not colour alone', async () => {
    renderList();

    await waitFor(() => {
      expect(screen.getByText('KDEN')).toBeInTheDocument();
    });

    expect(screen.getByText('Weather impacted')).toBeInTheDocument();
    expect(screen.getByText('High')).toBeInTheDocument();
    expect(screen.getByText('IFR')).toBeInTheDocument();
    expect(screen.getByText('Stale')).toBeInTheDocument();
    expect(screen.queryByText('Congested')).not.toBeInTheDocument();
  });

  it('selects an airport by stored id without fetching detail', async () => {
    const onSelect = vi.fn();
    const getAirport = vi.fn();

    renderWithProviders(
      <WorklistHarness onSelect={onSelect} />,
      {
        client: {
          listAirports: async () => ({
            items: AIRPORTS,
            count: 2,
            nextToken: null,
          }),
          overview: async () => ({}),
          getAirport,
        },
      },
    );

    await waitFor(() => {
      expect(screen.getByText('KDEN')).toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole('button', { name: /KDEN/ }));

    expect(onSelect).toHaveBeenCalledWith('KDEN');
    expect(getAirport).not.toHaveBeenCalled();
  });

  it('applies weather-risk as a server filter', async () => {
    const listAirports = vi.fn(async () => ({
      items: [AIRPORTS[0]],
      count: 1,
      nextToken: null,
    }));

    renderWithProviders(
      <WorklistHarness />,
      {
        client: {
          listAirports,
          overview: async () => ({}),
        },
      },
    );

    await waitFor(() => {
      expect(screen.getByText('KDEN')).toBeInTheDocument();
    });

    fireEvent.change(screen.getByLabelText('Weather risk'), {
      target: { value: 'HIGH' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Apply' }));

    await waitFor(() => {
      expect(listAirports).toHaveBeenCalledWith(
        expect.objectContaining({ weatherRisk: 'HIGH' }),
      );
    });
  });

  it('does not treat an empty page as Normal', async () => {
    renderWithProviders(
      <WorklistHarness />,
      {
        client: {
          listAirports: async () => ({ items: [], count: 0, nextToken: null }),
          overview: async () => ({ airports: { currentCount: 0 } }),
        },
      },
    );

    await waitFor(() => {
      expect(
        screen.getByText('No current airport data available'),
      ).toBeInTheDocument();
    });

    expect(screen.queryByText('Normal')).not.toBeInTheDocument();
  });
});
