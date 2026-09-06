import { useState } from 'react';
import { fireEvent, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { renderWithProviders } from '@/test/renderWithProviders';
import type { ActiveAlert } from '@/types/api';

import { AlertWorklist } from './AlertWorklist';
import { EMPTY_ALERT_FILTERS, type AlertFilters } from './alertList';

const ALERTS: ActiveAlert[] = [
  {
    alert_id: 'alert-a1',
    fingerprint: 'fp-a1',
    aircraft_id: 'aa0001',
    hazard_id: 'sigmet-a',
    risk_id: 'risk-a1',
    recommendation_id: 'rec-a1',
    alert_state: 'NEW',
    risk_level: 'MEDIUM',
    risk_score: 57,
    primary_action_type: 'MONITOR_AND_PREPARE_OPTIONS',
    message:
      'Aircraft aa0001 has MEDIUM weather-hazard risk. Advisory action: MONITOR_AND_PREPARE_OPTIONS.',
    created_at_utc: '2026-09-06T02:30:00Z',
    updated_at_utc: '2026-09-06T02:30:00Z',
    updated_at_epoch: 30,
    valid_until_utc: '2026-09-06T06:00:00Z',
  },
  {
    alert_id: 'alert-a2',
    fingerprint: 'fp-a2',
    aircraft_id: 'aa0001',
    hazard_id: 'sigmet-b',
    risk_id: 'risk-a2',
    recommendation_id: 'rec-a2',
    alert_state: 'UPDATED',
    risk_level: 'HIGH',
    risk_score: 80,
    primary_action_type: 'EVALUATE_DIVERSION',
    created_at_utc: '2026-09-06T02:40:00Z',
    updated_at_utc: '2026-09-06T02:45:00Z',
    updated_at_epoch: 40,
    valid_until_utc: '2026-09-06T06:00:00Z',
  },
  {
    alert_id: 'alert-c1',
    fingerprint: 'fp-c1',
    aircraft_id: 'cc0003',
    hazard_id: 'sigmet-a',
    risk_id: 'risk-c1',
    recommendation_id: 'rec-c1',
    alert_state: 'MONITORING',
    risk_level: 'LOW',
    risk_score: 22,
    primary_action_type: 'MONITOR',
    message:
      'Aircraft cc0003 has LOW weather-hazard risk. Advisory action: MONITOR.',
    created_at_utc: '2026-09-06T02:00:00Z',
    updated_at_utc: '2026-09-06T02:00:00Z',
    updated_at_epoch: 10,
  },
];

function renderList(
  extra: {
    items?: ActiveAlert[];
    nextToken?: string | null;
    onSelect?: (alertId: string) => void;
    selectedAlertId?: string | null;
    listActiveAlerts?: ReturnType<typeof vi.fn>;
    getAircraft?: ReturnType<typeof vi.fn>;
  } = {},
) {
  const listActiveAlerts =
    extra.listActiveAlerts ??
    vi.fn(async () => ({
      items: extra.items ?? ALERTS,
      count: (extra.items ?? ALERTS).length,
      nextToken: extra.nextToken ?? null,
    }));
  const getAircraft = extra.getAircraft ?? vi.fn();
  const onSelect = extra.onSelect ?? vi.fn();

  function WorklistHarness() {
    const [filters, setFilters] = useState<AlertFilters>(EMPTY_ALERT_FILTERS);

    return (
      <AlertWorklist
        selectedAlertId={extra.selectedAlertId ?? null}
        onSelect={onSelect}
        filters={filters}
        onFiltersChange={setFilters}
      />
    );
  }

  const view = renderWithProviders(<WorklistHarness />, {
    client: {
      listActiveAlerts,
      getAircraft,
      overview: async () => ({
        alerts: { currentCount: 132, activeCount: 2356 },
      }),
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
  });

  return { ...view, listActiveAlerts, getAircraft, onSelect };
}

describe('AlertWorklist', () => {
  it('renders stored states, risk and multiple alerts for one aircraft', async () => {
    renderList();

    await waitFor(() => {
      expect(screen.getAllByText('UAL9').length).toBe(2);
    });

    expect(screen.getAllByText('AA0001').length).toBeGreaterThan(0);
    expect(screen.getByText('CC0003')).toBeInTheDocument();
    expect(screen.getByText('sigmet-b')).toBeInTheDocument();
    expect(screen.getAllByText('New').length).toBeGreaterThan(0);
    expect(screen.getAllByText('Updated').length).toBeGreaterThan(0);
    expect(screen.getAllByText('Monitoring').length).toBeGreaterThan(0);
    expect(screen.getAllByText('Medium').length).toBeGreaterThan(0);
    expect(screen.getAllByText('High').length).toBeGreaterThan(0);
    expect(screen.getAllByText('Low').length).toBeGreaterThan(0);
    expect(
      screen.getByText(
        'Aircraft aa0001 has MEDIUM weather-hazard risk. Advisory action: MONITOR_AND_PREPARE_OPTIONS.',
      ),
    ).toBeInTheDocument();
    expect(screen.getAllByText('—').length).toBeGreaterThan(0);
    expect(
      screen.getByText(
        /3 loaded of 132 current · 2 aircraft · 2 hazards on loaded pages/,
      ),
    ).toBeInTheDocument();
    expect(screen.queryByText(/2,356/)).not.toBeInTheDocument();
  });

  it('selects by alert id so one aircraft can keep multiple current rows', async () => {
    const { onSelect } = renderList();

    await waitFor(() => {
      expect(screen.getByText('sigmet-b')).toBeInTheDocument();
    });

    fireEvent.click(screen.getByText('sigmet-b').closest('tr') as HTMLElement);

    expect(onSelect).toHaveBeenCalledWith('alert-a2');
  });

  it('does not request aircraft detail for worklist rows', async () => {
    const { getAircraft } = renderList();

    await waitFor(() => {
      expect(screen.getAllByText('UAL9').length).toBeGreaterThan(0);
    });

    expect(getAircraft).not.toHaveBeenCalled();
  });

  it('filters loaded pages only', async () => {
    renderList();

    await waitFor(() => {
      expect(screen.getByText('CC0003')).toBeInTheDocument();
    });

    fireEvent.change(screen.getByLabelText('State'), {
      target: { value: 'NEW' },
    });
    expect(screen.getAllByText('UAL9').length).toBeGreaterThan(0);
    expect(screen.queryByText('CC0003')).not.toBeInTheDocument();
    expect(screen.getByText(/1 of 3 loaded rows match the filters/)).toBeInTheDocument();
  });

  it('sorts loaded alerts by aircraft without inventing a composite score', async () => {
    renderList();

    await waitFor(() => {
      expect(screen.getByText('CC0003')).toBeInTheDocument();
    });

    fireEvent.change(screen.getByLabelText('Sort loaded'), {
      target: { value: 'aircraft' },
    });

    const rows = screen.getAllByRole('row').slice(1);
    expect(rows[0]).toHaveTextContent(/UAL9/);
    expect(rows[2]).toHaveTextContent('CC0003');
  });

  it('walks an opaque nextToken with Load more', async () => {
    const listActiveAlerts = vi.fn(
      async (request: { nextToken?: string | null }) => {
        if (request.nextToken) {
          expect(request.nextToken).toBe('opaque-token');
          return { items: [ALERTS[2]!], count: 1, nextToken: null };
        }

        return { items: [ALERTS[0]!], count: 1, nextToken: 'opaque-token' };
      },
    );

    renderList({ listActiveAlerts });

    await waitFor(() => {
      expect(screen.getByText('Load more current alerts')).toBeInTheDocument();
    });

    fireEvent.click(screen.getByText('Load more current alerts'));

    await waitFor(() => {
      expect(screen.getByText('CC0003')).toBeInTheDocument();
    });

    expect(listActiveAlerts).toHaveBeenLastCalledWith(
      expect.objectContaining({ nextToken: 'opaque-token' }),
    );
  });

  it('reports an empty current set without saying alerts do not exist', async () => {
    renderList({ items: [] });

    await waitFor(() => {
      expect(screen.getByText('No current alerts')).toBeInTheDocument();
    });
    expect(screen.queryByText('No alerts exist')).not.toBeInTheDocument();
  });

  it('surfaces a current-set API error', async () => {
    renderList({
      listActiveAlerts: vi.fn(async () => {
        throw new Error('alerts unavailable');
      }),
    });

    await waitFor(() => {
      expect(screen.getByText('Current alerts unavailable')).toBeInTheDocument();
    });
  });

  it('surfaces a pagination failure without dropping the loaded page', async () => {
    const listActiveAlerts = vi.fn(
      async (request: { nextToken?: string | null }) => {
        if (request.nextToken) {
          throw new Error('page two failed');
        }

        return { items: [ALERTS[0]!], count: 1, nextToken: 'opaque-token' };
      },
    );

    renderList({ listActiveAlerts });

    await waitFor(() => {
      expect(screen.getByText('Load more current alerts')).toBeInTheDocument();
    });

    fireEvent.click(screen.getByText('Load more current alerts'));

    await waitFor(() => {
      expect(
        screen.getByText(/The next current-alert page could not be loaded/),
      ).toBeInTheDocument();
    });
    expect(screen.getByText('UAL9')).toBeInTheDocument();
  });
});
