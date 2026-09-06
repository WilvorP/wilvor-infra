import { fireEvent, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { renderWithProviders } from '@/test/renderWithProviders';
import type { AircraftCurrentState } from '@/types/api';

import { AircraftFleetPanel } from './AircraftFleetPanel';

const AIRCRAFT: AircraftCurrentState[] = [
  {
    aircraft_id: 'aa0001',
    callsign: 'UAL9',
    baro_altitude_ft: 35000,
    ground_speed_kt: 430,
    on_ground: false,
    freshness_status: 'FRESH',
    position_time_utc: '2026-09-06T02:30:00Z',
    has_position: true,
    latitude: 37.6,
    longitude: -122.3,
  },
  {
    aircraft_id: 'bb0002',
    callsign: 'AAL2',
    baro_altitude_ft: 28000,
    ground_speed_kt: 390,
    on_ground: false,
    freshness_status: 'ACCEPTABLE',
    position_time_utc: '2026-09-06T02:29:00Z',
    has_position: true,
    latitude: 40.1,
    longitude: -74.2,
  },
];

describe('AircraftFleetPanel', () => {
  it('renders current-state rows without fetching aircraft detail', async () => {
    const getAircraft = vi.fn();
    const onSelect = vi.fn();

    renderWithProviders(
      <AircraftFleetPanel selectedAircraftId={null} onSelect={onSelect} />,
      {
        client: {
          listAircraft: async () => ({
            items: AIRCRAFT,
            count: 2,
            nextToken: null,
          }),
          overview: async () => ({
            aircraft: { activeCount: 3412 },
          }),
          getAircraft,
        },
      },
    );

    await waitFor(() => {
      expect(screen.getByText('UAL9')).toBeInTheDocument();
    });

    expect(screen.getByText('AAL2')).toBeInTheDocument();
    expect(screen.getByText('2 loaded of 3,412 tracked')).toBeInTheDocument();
    expect(screen.getByText('35,000 ft')).toBeInTheDocument();
    expect(getAircraft).not.toHaveBeenCalled();
  });

  it('selects an aircraft by stored id', async () => {
    const onSelect = vi.fn();

    renderWithProviders(
      <AircraftFleetPanel selectedAircraftId={null} onSelect={onSelect} />,
      {
        client: {
          listAircraft: async () => ({
            items: AIRCRAFT,
            count: 2,
            nextToken: null,
          }),
        },
      },
    );

    await waitFor(() => {
      expect(screen.getByText('AAL2')).toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole('button', { name: /AAL2/i }));

    expect(onSelect).toHaveBeenCalledWith('bb0002');
  });

  it('forwards an exact callsign find and does not send h3Cell with it', async () => {
    const listAircraft = vi.fn(async (request: { callsign?: string }) => ({
      items: request.callsign === 'UAL9' ? [AIRCRAFT[0]!] : AIRCRAFT,
      count: 1,
      nextToken: null,
    }));

    renderWithProviders(
      <AircraftFleetPanel selectedAircraftId={null} onSelect={() => {}} />,
      { client: { listAircraft } },
    );

    await waitFor(() => {
      expect(screen.getByText('UAL9')).toBeInTheDocument();
    });

    fireEvent.change(screen.getByLabelText('Callsign'), {
      target: { value: 'ual9' },
    });
    fireEvent.click(screen.getByText('Find'));

    await waitFor(() => {
      expect(listAircraft).toHaveBeenCalledWith(
        expect.objectContaining({ callsign: 'UAL9', h3Cell: undefined }),
      );
    });
  });

  it('walks opaque nextToken pages without decoding the cursor', async () => {
    const listAircraft = vi.fn(async (request: { nextToken?: string | null }) => {
      if (request.nextToken) {
        expect(request.nextToken).toBe('opaque-token');
        return { items: [AIRCRAFT[1]!], count: 1, nextToken: null };
      }

      return { items: [AIRCRAFT[0]!], count: 1, nextToken: 'opaque-token' };
    });

    renderWithProviders(
      <AircraftFleetPanel selectedAircraftId={null} onSelect={() => {}} />,
      { client: { listAircraft } },
    );

    await waitFor(() => {
      expect(screen.getByText('Load more aircraft')).toBeInTheDocument();
    });

    fireEvent.click(screen.getByText('Load more aircraft'));

    await waitFor(() => {
      expect(screen.getByText('AAL2')).toBeInTheDocument();
    });

    expect(screen.getByText('UAL9')).toBeInTheDocument();
    expect(listAircraft).toHaveBeenCalledTimes(2);
  });

  it('keeps the list usable when overview counts fail', async () => {
    renderWithProviders(
      <AircraftFleetPanel selectedAircraftId={null} onSelect={() => {}} />,
      {
        client: {
          listAircraft: async () => ({
            items: AIRCRAFT,
            count: 2,
            nextToken: null,
          }),
          overview: async () => {
            throw new Error('overview unavailable');
          },
        },
      },
    );

    await waitFor(() => {
      expect(screen.getByText('UAL9')).toBeInTheDocument();
    });

    expect(screen.getByText('2 loaded')).toBeInTheDocument();
  });

  it('shows a quiet empty current-state list', async () => {
    renderWithProviders(
      <AircraftFleetPanel selectedAircraftId={null} onSelect={() => {}} />,
      {
        client: {
          listAircraft: async () => ({ items: [], count: 0, nextToken: null }),
        },
      },
    );

    await waitFor(() => {
      expect(screen.getByText('No current aircraft')).toBeInTheDocument();
    });
  });
});
