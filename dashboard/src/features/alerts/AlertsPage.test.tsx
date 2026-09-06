import { act, fireEvent, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { describe, expect, it, vi } from 'vitest';

import type { OperationsMapProps } from '@/features/map/OperationsMap';
import { createTestQueryClient, renderWithProviders } from '@/test/renderWithProviders';
import type { ActiveAlert } from '@/types/api';

import { AlertsPage } from './AlertsPage';

const mapProps: { current: OperationsMapProps | null } = { current: null };

vi.mock('@/features/map/OperationsMap', () => ({
  OperationsMap: (props: OperationsMapProps) => {
    mapProps.current = props;
    return <div>Operations map</div>;
  },
}));

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
    state_reason: 'New material weather-hazard advisory condition.',
    updated_at_epoch: 30,
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
    message:
      'Aircraft aa0001 has HIGH weather-hazard risk. Advisory action: EVALUATE_DIVERSION.',
    state_reason: 'Supporting recommendation changed materially.',
    updated_at_epoch: 40,
  },
];

function renderPage(
  path = '/alerts',
  extra: {
    items?: ActiveAlert[] | (() => ActiveAlert[]);
    getAircraft?: ReturnType<typeof vi.fn>;
    listActiveAlerts?: ReturnType<typeof vi.fn>;
    queryClient?: ReturnType<typeof createTestQueryClient>;
  } = {},
) {
  const getAircraft =
    extra.getAircraft ??
    vi.fn(async () => ({
      aircraft: { aircraft_id: 'aa0001' },
      projection: { projection_id: 'proj-1' },
      projectionPoints: [
        { projection_id: 'proj-1', latitude: 37.6, longitude: -122.3 },
      ],
      currentContexts: [
        {
          alert: ALERTS[0],
          recommendation: { recommendation_id: 'rec-a1' },
          risk: {
            risk_id: 'risk-a1',
            risk_level: 'MEDIUM',
            risk_score: 57,
          },
        },
        {
          alert: ALERTS[1],
          recommendation: { recommendation_id: 'rec-a2' },
          risk: {
            risk_id: 'risk-a2',
            risk_level: 'HIGH',
            risk_score: 80,
          },
        },
      ],
    }));

  const listActiveAlerts =
    extra.listActiveAlerts ??
    vi.fn(async () => {
      const items =
        typeof extra.items === 'function'
          ? extra.items()
          : (extra.items ?? ALERTS);

      return { items, count: items.length, nextToken: null };
    });

  mapProps.current = null;

  return {
    ...renderWithProviders(
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path="/alerts" element={<AlertsPage mapStyleUrl={null} />} />
        </Routes>
      </MemoryRouter>,
      {
        queryClient: extra.queryClient,
        client: {
          listActiveAlerts,
          getAircraft,
          overview: async () => ({
            alerts: {
              currentCount: 132,
              activeCount: 2356,
              byState: { NEW: 800, UPDATED: 900 },
            },
          }),
          listActiveHazards: async () => ({
            items: [{ hazard_id: 'sigmet-a' }, { hazard_id: 'sigmet-b' }],
            count: 2,
            nextToken: null,
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
      },
    ),
    getAircraft,
    listActiveAlerts,
  };
}

function kpi(label: string) {
  return screen.getByRole('button', {
    name: (_accessibleName, element) =>
      element.querySelector('p')?.textContent === label,
  });
}

describe('AlertsPage', () => {
  it('uses overview currentCount and does not fetch detail until an alert is selected', async () => {
    const { getAircraft } = renderPage();

    await waitFor(() => {
      expect(screen.getByText('132')).toBeInTheDocument();
      expect(screen.getAllByText('UAL9').length).toBeGreaterThan(0);
    });

    expect(
      screen.getByRole('heading', { name: 'Current Alerts' }),
    ).toBeInTheDocument();
    expect(screen.queryByText('2,356')).not.toBeInTheDocument();
    expect(screen.queryByText('800')).not.toBeInTheDocument();
    expect(getAircraft).not.toHaveBeenCalled();
    expect(mapProps.current?.visibleAircraftIds).toEqual(['aa0001']);
  });

  it('selects by alert id, focuses the map, and deep-links Aircraft Investigation', async () => {
    const { getAircraft } = renderPage();

    await waitFor(() => {
      expect(screen.getByText('sigmet-b')).toBeInTheDocument();
    });

    fireEvent.click(screen.getByText('sigmet-b').closest('tr') as HTMLElement);

    await waitFor(() => {
      expect(
        screen.getByTestId('selected-alert-dock'),
      ).toBeInTheDocument();
      expect(
        screen.getByRole('region', { name: 'Selected alert' }),
      ).toBeInTheDocument();
      expect(
        screen.getByRole('link', { name: 'Open Aircraft Investigation' }),
      ).toHaveAttribute(
        'href',
        '/aircraft/aa0001?hazardId=sigmet-b&riskId=risk-a2&recommendationId=rec-a2&alertId=alert-a2&fingerprint=fp-a2&source=alert',
      );
    });

    expect(screen.getByText('Supporting Risk')).toBeInTheDocument();
    expect(getAircraft).toHaveBeenCalledTimes(1);
    expect(getAircraft.mock.calls[0]?.[0]).toBe('aa0001');
    expect(mapProps.current?.selectedAircraftId).toBe('aa0001');
    expect(mapProps.current?.selectedHazardId).toBe('sigmet-b');
    expect(mapProps.current?.emphasizedHazardIds).toEqual(['sigmet-b']);
  });

  it('preserves alertId in the URL and restores selection from it', async () => {
    renderPage('/alerts?alertId=alert-a1');

    await waitFor(() => {
      expect(
        screen.getByText(
          'Aircraft aa0001 has MEDIUM weather-hazard risk. Advisory action: MONITOR_AND_PREPARE_OPTIONS.',
        ),
      ).toBeInTheDocument();
    });

    expect(screen.getByText('Selected')).toBeInTheDocument();
    expect(
      screen.getByRole('link', { name: 'Open Aircraft Investigation' }),
    ).toHaveAttribute(
      'href',
      '/aircraft/aa0001?hazardId=sigmet-a&riskId=risk-a1&recommendationId=rec-a1&alertId=alert-a1&fingerprint=fp-a1&source=alert',
    );
    await waitFor(() => {
      expect(mapProps.current?.projectionPoints).toEqual([
        { projection_id: 'proj-1', latitude: 37.6, longitude: -122.3 },
      ]);
    });
  });

  it('reports a URL alert that is not among the loaded pages', async () => {
    renderPage('/alerts?alertId=alert-later');

    await waitFor(() => {
      expect(
        screen.getByText('This alert is not among the loaded pages.'),
      ).toBeInTheDocument();
    });

    expect(
      screen.queryByRole('link', { name: 'Open Aircraft Investigation' }),
    ).not.toBeInTheDocument();
  });

  it('keeps a vanished selection instead of picking another alert', async () => {
    let items = ALERTS;
    const queryClient = createTestQueryClient();
    const listActiveAlerts = vi.fn(async () => ({
      items,
      count: items.length,
      nextToken: null,
    }));

    renderPage('/alerts?alertId=alert-a1', {
      queryClient,
      listActiveAlerts,
    });

    await waitFor(() => {
      expect(
        screen.getByText(
          'Aircraft aa0001 has MEDIUM weather-hazard risk. Advisory action: MONITOR_AND_PREPARE_OPTIONS.',
        ),
      ).toBeInTheDocument();
    });

    items = [ALERTS[1]!];
    await queryClient.invalidateQueries();

    await waitFor(() => {
      expect(screen.getByText('This alert is no longer current.')).toBeInTheDocument();
    });

    expect(
      screen.queryByText(
        'Aircraft aa0001 has MEDIUM weather-hazard risk. Advisory action: MONITOR_AND_PREPARE_OPTIONS.',
      ),
    ).not.toBeInTheDocument();
    expect(mapProps.current?.selectedAircraftId).toBeNull();
  });

  it('keeps an explicit alert_id when the same multi-alert aircraft is clicked', async () => {
    renderPage('/alerts?alertId=alert-a2');

    await waitFor(() => {
      expect(
        screen.getByRole('link', { name: 'Open Aircraft Investigation' }),
      ).toHaveAttribute('href', expect.stringContaining('alertId=alert-a2'));
      expect(mapProps.current?.onSelectAircraft).toBeTypeOf('function');
    });

    act(() => {
      mapProps.current?.onSelectAircraft('aa0001');
    });

    expect(
      await screen.findByRole('region', {
        name: 'Current alerts for this aircraft',
      }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole('link', { name: 'Open Aircraft Investigation' }),
    ).toHaveAttribute('href', expect.stringContaining('alertId=alert-a2'));
    expect(mapProps.current?.selectedHazardId).toBe('sigmet-b');
  });

  it('auto-selects the only current loaded alert when a map aircraft is clicked', async () => {
    const { getAircraft } = renderPage('/alerts', {
      items: [ALERTS[0]!],
    });

    await waitFor(() => {
      expect(screen.getByText('sigmet-a')).toBeInTheDocument();
      expect(mapProps.current?.onSelectAircraft).toBeTypeOf('function');
    });

    act(() => {
      mapProps.current?.onSelectAircraft('aa0001');
    });

    await waitFor(() => {
      expect(
        screen.getByRole('link', { name: 'Open Aircraft Investigation' }),
      ).toHaveAttribute('href', expect.stringContaining('alertId=alert-a1'));
    });

    expect(screen.queryByText(/choose one/)).not.toBeInTheDocument();
    expect(getAircraft).toHaveBeenCalledTimes(1);
    expect(mapProps.current?.selectedHazardId).toBe('sigmet-a');
  });

  it('opens a chooser when a map aircraft has several current loaded alerts', async () => {
    const { getAircraft } = renderPage();

    await waitFor(() => {
      expect(screen.getByText('sigmet-a')).toBeInTheDocument();
      expect(mapProps.current?.onSelectAircraft).toBeTypeOf('function');
    });

    act(() => {
      mapProps.current?.onSelectAircraft('aa0001');
    });

    const chooser = await screen.findByRole('region', {
      name: 'Current alerts for this aircraft',
    });

    expect(screen.getByTestId('selected-alert-dock')).toContainElement(chooser);

    expect(within(chooser).getByText('sigmet-a')).toBeInTheDocument();
    expect(within(chooser).getByText('sigmet-b')).toBeInTheDocument();
    expect(within(chooser).getByText('New')).toBeInTheDocument();
    expect(within(chooser).getByText('Updated')).toBeInTheDocument();
    expect(
      screen.queryByRole('link', { name: 'Open Aircraft Investigation' }),
    ).not.toBeInTheDocument();
    expect(getAircraft).not.toHaveBeenCalled();
    expect(mapProps.current?.selectedAircraftId).toBe('aa0001');
    expect(mapProps.current?.selectedHazardId).toBeNull();

    fireEvent.click(within(chooser).getByText('sigmet-b'));

    await waitFor(() => {
      expect(
        screen.getByRole('link', { name: 'Open Aircraft Investigation' }),
      ).toHaveAttribute(
        'href',
        '/aircraft/aa0001?hazardId=sigmet-b&riskId=risk-a2&recommendationId=rec-a2&alertId=alert-a2&fingerprint=fp-a2&source=alert',
      );
    });

    expect(
      screen.queryByRole('region', {
        name: 'Current alerts for this aircraft',
      }),
    ).not.toBeInTheDocument();
    expect(mapProps.current?.selectedHazardId).toBe('sigmet-b');
    expect(mapProps.current?.emphasizedHazardIds).toEqual(['sigmet-b']);
    expect(getAircraft.mock.calls[0]?.[0]).toBe('aa0001');
  });

  it('uses KPI cards as loaded-page state filters with an active state', async () => {
    const { listActiveAlerts } = renderPage();

    await waitFor(() => {
      expect(screen.getByText('sigmet-b')).toBeInTheDocument();
    });

    const current = kpi('Current Alerts');
    const next = kpi('New');
    const updated = kpi('Updated');
    const escalated = kpi('Escalated');

    expect(current).toHaveAttribute('aria-pressed', 'true');
    expect(next).toHaveAttribute('aria-pressed', 'false');
    expect(within(next).getByText('loaded pages')).toBeInTheDocument();

    fireEvent.click(next);

    expect(screen.getByLabelText('State')).toHaveValue('NEW');
    expect(screen.getByText('sigmet-a')).toBeInTheDocument();
    expect(screen.queryByText('sigmet-b')).not.toBeInTheDocument();
    expect(next).toHaveAttribute('aria-pressed', 'true');
    expect(current).toHaveAttribute('aria-pressed', 'false');
    expect(next.className).toMatch(/active/);
    expect(mapProps.current?.visibleAircraftIds).toEqual(['aa0001']);
    expect(
      listActiveAlerts.mock.calls.every(
        (call) => call[0] == null || call[0].alert_state == null,
      ),
    ).toBe(true);

    fireEvent.click(updated);

    expect(screen.getByLabelText('State')).toHaveValue('UPDATED');
    expect(screen.getByText('sigmet-b')).toBeInTheDocument();
    expect(
      screen.queryByText(
        'Aircraft aa0001 has MEDIUM weather-hazard risk. Advisory action: MONITOR_AND_PREPARE_OPTIONS.',
      ),
    ).not.toBeInTheDocument();

    fireEvent.click(escalated);

    expect(screen.getByLabelText('State')).toHaveValue('ESCALATED');
    expect(screen.getByText('No alerts match these filters')).toBeInTheDocument();
    expect(screen.queryByText('sigmet-a')).not.toBeInTheDocument();
    expect(screen.queryByText('sigmet-b')).not.toBeInTheDocument();
    expect(escalated).toHaveAttribute('aria-pressed', 'true');
    expect(mapProps.current?.visibleAircraftIds).toEqual([]);
    expect(within(escalated).getByText('0')).toBeInTheDocument();

    fireEvent.click(current);

    expect(screen.getByLabelText('State')).toHaveValue('');
    expect(screen.getByText('sigmet-a')).toBeInTheDocument();
    expect(screen.getByText('sigmet-b')).toBeInTheDocument();
    expect(current).toHaveAttribute('aria-pressed', 'true');
    expect(escalated).toHaveAttribute('aria-pressed', 'false');
    expect(mapProps.current?.visibleAircraftIds).toEqual(['aa0001']);
  });

  it('clears a selection that the KPI state filter excludes instead of substituting another', async () => {
    renderPage('/alerts?alertId=alert-a2');

    await waitFor(() => {
      expect(
        screen.getByRole('link', { name: 'Open Aircraft Investigation' }),
      ).toHaveAttribute('href', expect.stringContaining('alertId=alert-a2'));
    });

    fireEvent.click(kpi('New'));

    expect(screen.getByLabelText('State')).toHaveValue('NEW');
    expect(screen.getByText('sigmet-a')).toBeInTheDocument();
    expect(screen.queryByText('sigmet-b')).not.toBeInTheDocument();
    expect(
      screen.queryByRole('link', { name: 'Open Aircraft Investigation' }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByText('This alert is no longer current.'),
    ).not.toBeInTheDocument();
    expect(mapProps.current?.selectedAircraftId).toBeNull();
    expect(mapProps.current?.selectedHazardId).toBeNull();
  });
});
