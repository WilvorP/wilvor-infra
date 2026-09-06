import { screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, expect, it, vi } from 'vitest';

import { ApiError } from '@/api/errors';
import { renderWithProviders } from '@/test/renderWithProviders';
import type { AirportDetailResponse } from '@/types/api';

import { AirportInvestigation } from './AirportInvestigation';

vi.mock('@/features/map/OperationsMap', () => ({
  OperationsMap: () => <div>Airport map</div>,
}));

const FULL: AirportDetailResponse = {
  airport: {
    airport_id: 'KDEN',
    station_name: 'Denver Intl',
    weather_impact_status: 'WEATHER_IMPACTED',
    weather_risk_level: 'HIGH',
    assessment_status: 'EVALUATED',
    status_reasons: ['Flight category is IFR.'],
    known_limitations: [],
    is_diversion_weather_ready: false,
    latitude: 39.86,
    longitude: -104.67,
    updated_at_utc: '2026-01-01T00:00:00Z',
  },
  metar: {
    station_id: 'KDEN',
    flight_category: 'IFR',
    visibility_sm: 2,
    ceiling_ft: 800,
    wind_direction_deg: 270,
    wind_speed_kt: 18,
    wind_gust_kt: 28,
    temperature_c: 1,
    dewpoint_c: -2,
    observed_time_utc: '2026-01-01T00:00:00Z',
    freshness_status: 'FRESH',
    raw_text: 'KDEN 010000Z ...',
  },
  taf: {
    station_id: 'KDEN',
    issued_at_utc: '2026-01-01T00:00:00Z',
    valid_from_utc: '2026-01-01T00:00:00Z',
    valid_to_utc: '2026-01-01T06:00:00Z',
    freshness_status: 'FRESH',
    forecast_period_count: 2,
    period_materialization_status: 'READY',
    raw_text: 'TAF KDEN ...',
  },
  tafForecastPeriods: [
    {
      period_id: 'p2',
      period_from_epoch: 200,
      sequence_number: 2,
      change_type: 'TEMPO',
      forecast_flight_category: 'MVFR',
      period_from_utc: '2026-01-01T03:00:00Z',
      period_to_utc: '2026-01-01T05:00:00Z',
    },
    {
      period_id: 'p1',
      period_from_epoch: 100,
      sequence_number: 1,
      change_type: 'BASE',
      forecast_flight_category: 'IFR',
      period_from_utc: '2026-01-01T00:00:00Z',
      period_to_utc: '2026-01-01T03:00:00Z',
    },
  ],
  recentAssessments: [
    {
      airport_assessment_id: 'aa#1',
      assessment_status: 'COMPLETE',
      weather_risk_level: 'HIGH',
      congestion_evidence_status: 'UNAVAILABLE',
      known_limitations: ['Airport congestion evidence is not implemented yet.'],
    },
  ],
};

function renderInvestigation(
  getAirport: () => Promise<AirportDetailResponse>,
) {
  return renderWithProviders(
    <MemoryRouter>
      <AirportInvestigation airportId="KDEN" mapStyleUrl={null} />
    </MemoryRouter>,
    { client: { getAirport: vi.fn(() => getAirport()) } },
  );
}

describe('AirportInvestigation', () => {
  it('keeps METAR, TAF and assessment visually distinct', async () => {
    renderInvestigation(async () => FULL);

    await waitFor(() => {
      expect(screen.getByText('Flight category is IFR.')).toBeInTheDocument();
    });

    expect(screen.getByRole('heading', { name: 'Forecast / TAF' })).toBeInTheDocument();
    expect(screen.getByText('Current operational status')).toBeInTheDocument();
    expect(screen.getByText('Flight category is IFR.')).toBeInTheDocument();
    expect(screen.getByText('2.0 SM')).toBeInTheDocument();
    expect(screen.getByText('BASE')).toBeInTheDocument();
    expect(screen.getByText('TEMPO')).toBeInTheDocument();
    expect(screen.getByText('Recent diversion assessments (1)')).toBeInTheDocument();
  });

  it('orders forecast periods by stored from-time, not by merge', async () => {
    renderInvestigation(async () => FULL);

    await waitFor(() => {
      expect(screen.getByText('BASE')).toBeInTheDocument();
    });

    const types = screen.getAllByText(/^(BASE|TEMPO)$/).map((node) => node.textContent);

    expect(types[0]).toBe('BASE');
    expect(types[1]).toBe('TEMPO');
  });

  it('says METAR is unavailable instead of Normal', async () => {
    renderInvestigation(async () => ({
      ...FULL,
      metar: null,
      airport: {
        ...FULL.airport,
        has_metar: false,
        metar_freshness_status: 'UNAVAILABLE',
      },
    }));

    await waitFor(() => {
      expect(screen.getByText('Current METAR unavailable.')).toBeInTheDocument();
    });

    const metarSection = screen
      .getByRole('heading', { name: 'Current observation / METAR' })
      .closest('section');

    expect(metarSection).not.toHaveTextContent('Normal');
  });

  it('says TAF is unavailable when the current TAF is missing', async () => {
    renderInvestigation(async () => ({ ...FULL, taf: null, tafForecastPeriods: [] }));

    await waitFor(() => {
      expect(screen.getByText('TAF unavailable.')).toBeInTheDocument();
    });
  });

  it('says assessment has not been generated when recentAssessments is empty', async () => {
    renderInvestigation(async () => ({ ...FULL, recentAssessments: [] }));

    await waitFor(() => {
      expect(
        screen.getByText('Airport assessment has not been generated.'),
      ).toBeInTheDocument();
    });
  });

  it('explains a 404 without treating the airport as Normal', async () => {
    renderInvestigation(async () => {
      throw new ApiError('Airport not found', { kind: 'client', status: 404 });
    });

    await waitFor(() => {
      expect(
        screen.getByText(/No current AirportStatus record exists/),
      ).toBeInTheDocument();
    });

    expect(screen.queryByText('Normal')).not.toBeInTheDocument();
  });

  it('keeps the page available when detail fails for a non-404 error', async () => {
    renderInvestigation(async () => {
      throw new ApiError('The operational API did not respond in time.', {
        kind: 'timeout',
      });
    });

    await waitFor(() => {
      expect(
        screen.getByText(/Airport investigation is unavailable/),
      ).toBeInTheDocument();
    });

    expect(screen.getByText('Airport map')).toBeInTheDocument();
  });
});
