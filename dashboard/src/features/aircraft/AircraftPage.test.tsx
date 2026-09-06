import { fireEvent, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { describe, expect, it, vi } from 'vitest';

import { renderWithProviders } from '@/test/renderWithProviders';
import type { AircraftCurrentState } from '@/types/api';

import { AircraftPage } from './AircraftPage';

vi.mock('@/features/map/OperationsMap', () => ({
  OperationsMap: () => <div>Operations map</div>,
}));

const AIRCRAFT: AircraftCurrentState = {
  aircraft_id: 'aa0001',
  callsign: 'UAL9',
  baro_altitude_ft: 35000,
  ground_speed_kt: 430,
  freshness_status: 'FRESH',
  has_position: true,
  latitude: 37.6,
  longitude: -122.3,
};

function renderPage(path = '/aircraft') {
  const getAircraft = vi.fn(async () => ({
    aircraft: AIRCRAFT,
    projection: {
      projection_id: 'proj-1',
      projection_status: 'READY',
    },
    projectionPoints: [{ latitude: 37.7, longitude: -122.2, horizon_min: 5 }],
    currentContexts: [
      {
        encounter: { encounter_id: 'enc-1', hazard_id: 'sigmet-a' },
        risk: { risk_id: 'risk-1' },
      },
    ],
    recentEncounters: [],
    recentRisks: [],
    recentRecommendations: [],
    recentAlerts: [],
  }));

  const view = renderWithProviders(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route
          path="/aircraft/:aircraftId?"
          element={<AircraftPage mapStyleUrl={null} />}
        />
      </Routes>
    </MemoryRouter>,
    {
      client: {
        listAircraft: async () => ({
          items: [AIRCRAFT],
          count: 1,
          nextToken: null,
        }),
        getAircraft,
        mapAircraft: async () => ({
          generatedAt: '2026-09-06T02:30:00Z',
          columns: [
            'aircraftId',
            'callsign',
            'longitude',
            'latitude',
            'trackDeg',
            'baroAltitudeFt',
            'groundSpeedKt',
            'positionTimeEpoch',
          ],
          count: 1,
          truncated: false,
          aircraft: [['aa0001', 'UAL9', -122.3, 37.6, 270, 35000, 430, 1786515880]],
        }),
      },
    },
  );

  return { ...view, getAircraft };
}

describe('AircraftPage', () => {
  it('opens investigation only after an aircraft is selected', async () => {
    const { getAircraft } = renderPage();

    await waitFor(() => {
      expect(screen.getByText('UAL9')).toBeInTheDocument();
    });

    expect(getAircraft).not.toHaveBeenCalled();
    expect(
      screen.getByText(/Select an aircraft to open investigation/),
    ).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: /UAL9/i }));

    await waitFor(() => {
      expect(getAircraft).toHaveBeenCalledWith(
        'aa0001',
        expect.anything(),
      );
    });

    expect(screen.getByText('Operations map')).toBeInTheDocument();
  });

  it('loads investigation from the route aircraft id', async () => {
    const { getAircraft } = renderPage('/aircraft/aa0001');

    await waitFor(() => {
      expect(getAircraft).toHaveBeenCalledWith(
        'aa0001',
        expect.anything(),
      );
    });

    await waitFor(() => {
      expect(
        screen.getByRole('heading', { name: 'Current decision context' }),
      ).toBeInTheDocument();
    });

    expect(screen.getByRole('link', { name: 'Operations' })).toHaveAttribute(
      'href',
      '/',
    );
    expect(screen.queryByText('Current aircraft')).not.toBeInTheDocument();
  });

  it('passes worklist IDs from the query string into investigation', async () => {
    renderPage(
      '/aircraft/aa0001?source=encounter&encounterId=enc-missing&riskId=risk-missing',
    );

    await waitFor(() => {
      expect(
        screen.getByText(/not among this aircraft's current contexts/i),
      ).toBeInTheDocument();
    });
  });
});
