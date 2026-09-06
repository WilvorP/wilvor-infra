import { fireEvent, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { renderWithProviders } from '@/test/renderWithProviders';
import type {
  ActiveAlert,
  ActiveEncounterItem,
  Recommendation,
} from '@/types/api';
import { NOT_REPORTED } from '@/utils/format';

import { OperationalWorklist } from './OperationalWorklist';

const ENCOUNTERS: ActiveEncounterItem[] = [
  {
    encounter: {
      encounter_id: 'enc-1',
      aircraft_id: 'aa0001',
      hazard_id: 'sigmet-a',
      hazard_type: 'CONVECTION',
      encounter_state: 'DETECTED',
      detected_at_utc: '2026-09-06T02:30:00Z',
    },
    risk: {
      risk_id: 'risk-1',
      risk_level: 'LOW',
      risk_score: 40,
    },
  },
  {
    encounter: {
      encounter_id: 'enc-2',
      aircraft_id: 'bb0002',
      hazard_id: 'sigmet-b',
      hazard_type: 'ICING',
      encounter_state: 'MONITORING',
      detected_at_utc: '2026-09-06T02:10:00Z',
    },
    risk: {
      risk_id: 'risk-2',
      risk_level: 'MEDIUM',
      risk_score: 65,
    },
  },
];

const RECOMMENDATIONS: Recommendation[] = [
  {
    recommendation_id: 'rec-1',
    aircraft_id: 'aa0001',
    hazard_id: 'sigmet-a',
    risk_id: 'risk-1',
    recommendation_status: 'ACTIVE',
    primary_action_type: 'MONITOR',
    risk_level: 'LOW',
    risk_score: 40,
    confidence: 'HIGH',
    preferred_airport_id: 'KDEN',
    created_at_utc: '2026-09-06T02:32:00Z',
  },
];

const ALERTS: ActiveAlert[] = [
  {
    alert_id: 'alert-1',
    fingerprint: 'fp-1',
    aircraft_id: 'aa0001',
    hazard_id: 'sigmet-a',
    risk_id: 'risk-1',
    recommendation_id: 'rec-1',
    alert_state: 'NEW',
    risk_level: 'LOW',
    risk_score: 40,
    message: 'Aircraft aa0001 has LOW weather-hazard risk.',
    updated_at_utc: '2026-09-06T02:31:00Z',
  },
];

function renderWorklist(
  onSelect = vi.fn(),
  extra: {
    encounters?: ActiveEncounterItem[];
    alerts?: ActiveAlert[];
    recommendations?: Recommendation[];
    encounterNextToken?: string | null;
  } = {},
) {
  const listActiveEncounters = vi.fn(async () => ({
    items: extra.encounters ?? ENCOUNTERS,
    count: (extra.encounters ?? ENCOUNTERS).length,
    nextToken: extra.encounterNextToken ?? null,
  }));
  const listActiveAlerts = vi.fn(async () => ({
    items: extra.alerts ?? ALERTS,
    count: (extra.alerts ?? ALERTS).length,
    nextToken: null,
  }));
  const listActiveRecommendations = vi.fn(async () => ({
    items: extra.recommendations ?? RECOMMENDATIONS,
    count: (extra.recommendations ?? RECOMMENDATIONS).length,
    nextToken: null,
  }));

  const view = renderWithProviders(
    <OperationalWorklist selected={null} onSelect={onSelect} />,
    {
      client: {
        listActiveEncounters,
        listActiveAlerts,
        listActiveRecommendations,
      },
    },
  );

  return {
    ...view,
    onSelect,
    listActiveEncounters,
    listActiveAlerts,
    listActiveRecommendations,
  };
}

describe('OperationalWorklist', () => {
  it('renders current encounters with stored risk text, not colour alone', async () => {
    renderWorklist();

    await waitFor(() => {
      expect(screen.getByText('AA0001')).toBeInTheDocument();
    });

    expect(screen.getAllByText('Low').length).toBeGreaterThan(0);
    expect(screen.getAllByText('Detected').length).toBeGreaterThan(0);
    expect(screen.getByText('Convection')).toBeInTheDocument();
    expect(screen.getAllByText('40').length).toBeGreaterThan(0);
  });

  it('selects an encounter by stored IDs', async () => {
    const { onSelect } = renderWorklist();

    await waitFor(() => {
      expect(screen.getByText('BB0002')).toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole('button', { name: /BB0002/i }));

    expect(onSelect).toHaveBeenCalledWith({
      aircraftId: 'bb0002',
      hazardId: 'sigmet-b',
      encounterId: 'enc-2',
      riskId: 'risk-2',
      source: 'encounter',
    });
  });

  it('filters loaded encounters by stored risk level', async () => {
    renderWorklist();

    await waitFor(() => {
      expect(screen.getByText('AA0001')).toBeInTheDocument();
    });

    fireEvent.change(screen.getByLabelText('Risk'), {
      target: { value: 'MEDIUM' },
    });

    expect(screen.getByText('BB0002')).toBeInTheDocument();
    expect(screen.queryByText('AA0001')).not.toBeInTheDocument();
  });

  it('switches to current alerts and selects by alert IDs', async () => {
    const { onSelect } = renderWorklist();

    await waitFor(() => {
      expect(screen.getByRole('tab', { name: 'Alerts' })).toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole('tab', { name: 'Alerts' }));

    await waitFor(() => {
      expect(screen.getByText(/LOW weather-hazard risk/)).toBeInTheDocument();
    });

    expect(screen.getAllByText('New').length).toBeGreaterThan(0);

    fireEvent.click(screen.getByRole('button', { name: /AA0001/i }));

    expect(onSelect).toHaveBeenCalledWith({
      aircraftId: 'aa0001',
      hazardId: 'sigmet-a',
      riskId: 'risk-1',
      recommendationId: 'rec-1',
      alertId: 'alert-1',
      fingerprint: 'fp-1',
      source: 'alert',
    });
  });

  it('does not offer HIGH when the loaded current set has none', async () => {
    renderWorklist();

    await waitFor(() => {
      expect(screen.getByText('BB0002')).toBeInTheDocument();
    });

    expect(
      screen.queryByRole('option', { name: 'High' }),
    ).not.toBeInTheDocument();
    expect(screen.getByRole('option', { name: 'Low' })).toBeInTheDocument();
    expect(screen.getByRole('option', { name: 'Medium' })).toBeInTheDocument();
  });

  it('walks opaque nextToken pages without decoding the cursor', async () => {
    const listActiveEncounters = vi.fn(async (request: { nextToken?: string | null }) => {
      if (request.nextToken) {
        expect(request.nextToken).toBe('opaque-token');
        return { items: [ENCOUNTERS[1]!], count: 1, nextToken: null };
      }

      return { items: [ENCOUNTERS[0]!], count: 1, nextToken: 'opaque-token' };
    });

    renderWithProviders(
      <OperationalWorklist selected={null} onSelect={() => {}} />,
      {
        client: {
          listActiveEncounters,
          listActiveAlerts: async () => ({
            items: [],
            count: 0,
            nextToken: null,
          }),
        },
      },
    );

    await waitFor(() => {
      expect(screen.getByText('Load more current rows')).toBeInTheDocument();
    });

    fireEvent.click(screen.getByText('Load more current rows'));

    await waitFor(() => {
      expect(screen.getByText('BB0002')).toBeInTheDocument();
    });

    expect(screen.getByText('AA0001')).toBeInTheDocument();
    expect(listActiveEncounters).toHaveBeenCalledTimes(2);
  });

  it('keeps the selected IDs after a poll and reports when they leave the current set', async () => {
    const selected = {
      aircraftId: 'aa0001',
      encounterId: 'enc-1',
      riskId: 'risk-1',
      source: 'encounter' as const,
    };

    const { rerender } = renderWithProviders(
      <OperationalWorklist
        selected={selected}
        onSelect={() => {}}
        currentEncounterCount={1326}
        currentAlertCount={132}
      />,
      {
        client: {
          listActiveEncounters: async () => ({
            items: ENCOUNTERS,
            count: 2,
            nextToken: null,
          }),
          listActiveAlerts: async () => ({
            items: ALERTS,
            count: 1,
            nextToken: null,
          }),
        },
      },
    );

    await waitFor(() => {
      expect(screen.getByText('Selected')).toBeInTheDocument();
    });

    expect(screen.getByText('2 loaded of 1,326 current')).toBeInTheDocument();

    rerender(
      <OperationalWorklist
        selected={selected}
        onSelect={() => {}}
        currentEncounterCount={1326}
        currentAlertCount={132}
      />,
    );

    expect(screen.getByText('Selected')).toBeInTheDocument();

    renderWithProviders(
      <OperationalWorklist
        selected={{
          aircraftId: 'gone',
          encounterId: 'enc-gone',
          source: 'encounter',
        }}
        onSelect={() => {}}
      />,
      {
        client: {
          listActiveEncounters: async () => ({
            items: ENCOUNTERS,
            count: 2,
            nextToken: null,
          }),
          listActiveAlerts: async () => ({
            items: [],
            count: 0,
            nextToken: null,
          }),
        },
      },
    );

    await waitFor(() => {
      expect(
        screen.getByText(/no longer in the current operational set/i),
      ).toBeInTheDocument();
    });
  });

  it('renders missing optional encounter fields as not reported', async () => {
    renderWorklist(vi.fn(), {
      encounters: [
        {
          encounter: { aircraft_id: 'cc0003', encounter_id: 'enc-sparse' },
          risk: { risk_id: 'risk-sparse' },
        },
      ],
    });

    await waitFor(() => {
      expect(screen.getByText('CC0003')).toBeInTheDocument();
    });

    expect(screen.getAllByText(NOT_REPORTED).length).toBeGreaterThan(0);
    expect(screen.getAllByText('Unknown').length).toBeGreaterThan(0);
  });

  it('keeps current encounters visible when current alerts fail', async () => {
    renderWithProviders(
      <OperationalWorklist selected={null} onSelect={() => {}} />,
      {
        client: {
          listActiveEncounters: async () => ({
            items: ENCOUNTERS,
            count: 2,
            nextToken: null,
          }),
          listActiveAlerts: async () => {
            throw new Error('alerts unavailable');
          },
        },
      },
    );

    await waitFor(() => {
      expect(screen.getByText('AA0001')).toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole('tab', { name: 'Alerts' }));

    await waitFor(() => {
      expect(screen.getByText('Current alerts unavailable')).toBeInTheDocument();
    });
  });

  it('shows a quiet current-set empty state', async () => {
    renderWorklist(vi.fn(), { encounters: [] });

    await waitFor(() => {
      expect(screen.getByText('No current encounters')).toBeInTheDocument();
    });
  });

  it('distinguishes a failed current-encounter request from a quiet network', async () => {
    renderWithProviders(
      <OperationalWorklist selected={null} onSelect={() => {}} />,
      {
        client: {
          listActiveEncounters: async () => {
            throw new Error('encounters unavailable');
          },
          listActiveAlerts: async () => ({
            items: [],
            count: 0,
            nextToken: null,
          }),
        },
      },
    );

    await waitFor(() => {
      expect(
        screen.getByText('Current encounters unavailable'),
      ).toBeInTheDocument();
    });
  });

  it('switches to current recommendations and selects by recommendation IDs', async () => {
    const { onSelect } = renderWorklist();

    fireEvent.click(
      await screen.findByRole('tab', { name: 'Recommendations' }),
    );

    await waitFor(() => {
      expect(screen.getByText('Preferred KDEN')).toBeInTheDocument();
    });

    expect(screen.getAllByText('Monitor').length).toBeGreaterThan(0);

    fireEvent.click(screen.getByRole('button', { name: /AA0001/i }));

    expect(onSelect).toHaveBeenCalledWith({
      aircraftId: 'aa0001',
      hazardId: 'sigmet-a',
      riskId: 'risk-1',
      recommendationId: 'rec-1',
      source: 'recommendation',
    });
  });

  it('shows a quiet empty state for current recommendations', async () => {
    renderWorklist(vi.fn(), { recommendations: [] });

    fireEvent.click(
      await screen.findByRole('tab', { name: 'Recommendations' }),
    );

    await waitFor(() => {
      expect(screen.getByText('No current recommendations')).toBeInTheDocument();
    });
  });

  it('does not fetch recommendations until the operator opens that tab', async () => {
    const { listActiveRecommendations } = renderWorklist();

    await waitFor(() => {
      expect(screen.getByText('AA0001')).toBeInTheDocument();
    });

    expect(listActiveRecommendations).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole('tab', { name: 'Recommendations' }));

    await waitFor(() => {
      expect(listActiveRecommendations).toHaveBeenCalledTimes(1);
    });
  });

  it('offers another page only when the current-set API returns nextToken', async () => {
    renderWorklist(vi.fn(), { encounterNextToken: 'page-2' });

    await waitFor(() => {
      expect(screen.getByText('Load more current rows')).toBeInTheDocument();
    });
  });

  it('walks opaque nextToken pages on current alerts', async () => {
    const firstAlert = ALERTS[0]!;
    const secondAlert: ActiveAlert = {
      alert_id: 'alert-2',
      fingerprint: 'fp-2',
      aircraft_id: 'bb0002',
      hazard_id: 'sigmet-b',
      risk_id: 'risk-2',
      alert_state: 'UPDATED',
      risk_level: 'MEDIUM',
      message: 'Aircraft bb0002 has MEDIUM weather-hazard risk.',
      updated_at_utc: '2026-09-06T02:40:00Z',
    };
    const listActiveAlerts = vi.fn(async (request: { nextToken?: string | null }) => {
      if (request.nextToken) {
        expect(request.nextToken).toBe('alert-page-2');
        return { items: [secondAlert], count: 1, nextToken: null };
      }

      return { items: [firstAlert], count: 1, nextToken: 'alert-page-2' };
    });
    const getAircraft = vi.fn();

    renderWithProviders(
      <OperationalWorklist selected={null} onSelect={() => {}} />,
      {
        client: {
          listActiveEncounters: async () => ({
            items: [],
            count: 0,
            nextToken: null,
          }),
          listActiveAlerts,
          getAircraft,
        },
      },
    );

    fireEvent.click(await screen.findByRole('tab', { name: 'Alerts' }));

    await waitFor(() => {
      expect(screen.getByText('Load more current rows')).toBeInTheDocument();
    });

    fireEvent.click(screen.getByText('Load more current rows'));

    await waitFor(() => {
      expect(screen.getByText('BB0002')).toBeInTheDocument();
    });

    expect(screen.getByText('AA0001')).toBeInTheDocument();
    expect(listActiveAlerts).toHaveBeenCalledTimes(2);
    expect(getAircraft).not.toHaveBeenCalled();
  });

  it('reports a pagination failure without replacing loaded current rows', async () => {
    const listActiveEncounters = vi.fn(async (request: { nextToken?: string | null }) => {
      if (request.nextToken) {
        throw new Error('page unavailable');
      }

      return { items: [ENCOUNTERS[0]!], count: 1, nextToken: 'opaque-token' };
    });

    renderWithProviders(
      <OperationalWorklist selected={null} onSelect={() => {}} />,
      {
        client: {
          listActiveEncounters,
          listActiveAlerts: async () => ({
            items: [],
            count: 0,
            nextToken: null,
          }),
        },
      },
    );

    await waitFor(() => {
      expect(screen.getByText('AA0001')).toBeInTheDocument();
    });

    fireEvent.click(screen.getByText('Load more current rows'));

    await waitFor(() => {
      expect(
        screen.getByText(/next current page could not be loaded/i),
      ).toBeInTheDocument();
    });

    expect(screen.getByText('AA0001')).toBeInTheDocument();
  });

  it('sorts loaded current encounters by stored aircraft id', async () => {
    renderWorklist();

    await waitFor(() => {
      expect(screen.getByText('BB0002')).toBeInTheDocument();
    });

    fireEvent.change(screen.getByLabelText('Sort'), {
      target: { value: 'aircraft' },
    });
    fireEvent.change(screen.getByLabelText('Order'), {
      target: { value: 'asc' },
    });

    const rows = screen.getAllByRole('button').filter((button) =>
      /AA0001|BB0002/.test(button.textContent ?? ''),
    );

    expect(rows[0]).toHaveTextContent('AA0001');
    expect(rows[1]).toHaveTextContent('BB0002');
  });

  it('shows a quiet empty state for current alerts', async () => {
    renderWorklist(vi.fn(), { alerts: [] });

    fireEvent.click(await screen.findByRole('tab', { name: 'Alerts' }));

    await waitFor(() => {
      expect(screen.getByText('No current alerts')).toBeInTheDocument();
    });
    expect(screen.queryByText('No alerts exist')).not.toBeInTheDocument();
  });

  it('resolves callsign from the already-loaded map feed', async () => {
    renderWithProviders(
      <OperationalWorklist selected={null} onSelect={() => {}} />,
      {
        client: {
          listActiveEncounters: async () => ({
            items: [ENCOUNTERS[0]!],
            count: 1,
            nextToken: null,
          }),
          listActiveAlerts: async () => ({
            items: [],
            count: 0,
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
            aircraft: [
              ['aa0001', 'UAL9', -122.3, 37.6, 270, 35000, 430, 1786515880],
            ],
          }),
        },
      },
    );

    await waitFor(() => {
      expect(screen.getByText('UAL9')).toBeInTheDocument();
    });
  });
});
