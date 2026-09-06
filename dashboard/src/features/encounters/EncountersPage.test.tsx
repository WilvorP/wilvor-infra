import { act, fireEvent, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { describe, expect, it, vi } from 'vitest';

import type { OperationsMapProps } from '@/features/map/OperationsMap';
import { createTestQueryClient, renderWithProviders } from '@/test/renderWithProviders';
import type { ActiveEncounterItem } from '@/types/api';

import { EncountersPage } from './EncountersPage';

const mapProps: { current: OperationsMapProps | null } = { current: null };

vi.mock('@/features/map/OperationsMap', () => ({
  OperationsMap: (props: OperationsMapProps) => {
    mapProps.current = props;
    return <div>Operations map</div>;
  },
}));

const ENCOUNTERS: ActiveEncounterItem[] = [
  {
    encounter: {
      encounter_id: 'enc-a1',
      aircraft_id: 'aa0001',
      hazard_id: 'sigmet-a',
      hazard_type: 'CONVECTION',
      projection_id: 'proj-1',
      encounter_state: 'DETECTED',
      geometry_overlap_status: 'INSIDE_NOW',
      time_overlap_status: 'OVERLAP',
      altitude_overlap_status: 'UNKNOWN',
      inside_now: true,
      detected_at_utc: '2026-09-06T02:30:00Z',
    },
    risk: {
      risk_id: 'risk-a1',
      risk_level: 'MEDIUM',
      risk_score: 65,
      confidence: 'HIGH',
      reasons: ['Inside convection now'],
    },
  },
  {
    encounter: {
      encounter_id: 'enc-a2',
      aircraft_id: 'aa0001',
      hazard_id: 'sigmet-b',
      hazard_type: 'ICING',
      encounter_state: 'MONITORING',
      altitude_overlap_status: 'NO_OVERLAP',
      inside_now: false,
      detected_at_utc: '2026-09-06T02:10:00Z',
    },
    risk: { risk_id: 'risk-a2', risk_level: 'LOW', risk_score: 40 },
  },
];

function renderPage(
  path = '/encounters',
  extra: {
    items?: ActiveEncounterItem[] | (() => ActiveEncounterItem[]);
    getAircraft?: ReturnType<typeof vi.fn>;
    listActiveEncounters?: ReturnType<typeof vi.fn>;
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
          encounter: ENCOUNTERS[0]!.encounter,
          risk: ENCOUNTERS[0]!.risk,
        },
      ],
    }));

  const listActiveEncounters =
    extra.listActiveEncounters ??
    vi.fn(async () => {
      const items =
        typeof extra.items === 'function'
          ? extra.items()
          : (extra.items ?? ENCOUNTERS);

      return { items, count: items.length, nextToken: null };
    });

  mapProps.current = null;

  return {
    ...renderWithProviders(
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route
            path="/encounters"
            element={<EncountersPage mapStyleUrl={null} />}
          />
        </Routes>
      </MemoryRouter>,
      {
        queryClient: extra.queryClient,
        client: {
          listActiveEncounters,
          getAircraft,
          overview: async () => ({
            encounters: {
              activeCount: 1326,
              lowRiskCount: 1200,
              mediumRiskCount: 126,
              highRiskCount: 0,
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
    listActiveEncounters,
  };
}

describe('EncountersPage', () => {
  it('uses overview totals and does not fetch detail until an encounter is selected', async () => {
    const { getAircraft } = renderPage();

    await waitFor(() => {
      expect(screen.getByText('1,326')).toBeInTheDocument();
      expect(screen.getAllByText('UAL9').length).toBeGreaterThan(0);
    });

    expect(screen.getByRole('heading', { name: 'Current Encounters' })).toBeInTheDocument();
    expect(screen.getByText('1,200')).toBeInTheDocument();
    expect(getAircraft).not.toHaveBeenCalled();
    expect(mapProps.current?.visibleAircraftIds).toEqual(['aa0001']);
  });

  it('selects by encounter id, focuses the map, and deep-links Aircraft Investigation', async () => {
    const { getAircraft } = renderPage();

    await waitFor(() => {
      expect(screen.getByText('sigmet-b')).toBeInTheDocument();
    });

    fireEvent.click(screen.getByText('sigmet-b').closest('tr') as HTMLElement);

    await waitFor(() => {
      expect(
        screen.getByRole('link', { name: 'Open Aircraft Investigation' }),
      ).toHaveAttribute(
        'href',
        '/aircraft/aa0001?hazardId=sigmet-b&encounterId=enc-a2&riskId=risk-a2&source=encounter',
      );
    });

    expect(getAircraft).toHaveBeenCalledTimes(1);
    expect(getAircraft.mock.calls[0]?.[0]).toBe('aa0001');
    expect(mapProps.current?.selectedAircraftId).toBe('aa0001');
    expect(mapProps.current?.selectedHazardId).toBe('sigmet-b');
    expect(mapProps.current?.emphasizedHazardIds).toEqual(['sigmet-b']);
  });

  it('preserves encounterId in the URL and restores selection from it', async () => {
    renderPage('/encounters?encounterId=enc-a1');

    await waitFor(() => {
      expect(screen.getByText('Inside convection now')).toBeInTheDocument();
    });

    expect(screen.getByText('Selected')).toBeInTheDocument();
    expect(
      screen.getByRole('link', { name: 'Open Aircraft Investigation' }),
    ).toHaveAttribute(
      'href',
      '/aircraft/aa0001?hazardId=sigmet-a&encounterId=enc-a1&riskId=risk-a1&source=encounter',
    );
    await waitFor(() => {
      expect(mapProps.current?.projectionPoints).toEqual([
        { projection_id: 'proj-1', latitude: 37.6, longitude: -122.3 },
      ]);
    });
  });

  it('keeps a vanished selection instead of picking another encounter', async () => {
    let items = ENCOUNTERS;
    const queryClient = createTestQueryClient();
    const listActiveEncounters = vi.fn(async () => ({
      items,
      count: items.length,
      nextToken: null,
    }));

    renderPage('/encounters?encounterId=enc-a1', {
      queryClient,
      listActiveEncounters,
    });

    await waitFor(() => {
      expect(screen.getByText('Inside convection now')).toBeInTheDocument();
    });

    items = [ENCOUNTERS[1]!];
    await queryClient.invalidateQueries();

    await waitFor(() => {
      expect(
        screen.getByText('This encounter is no longer current.'),
      ).toBeInTheDocument();
    });

    expect(screen.queryByText('Inside convection now')).not.toBeInTheDocument();
    expect(mapProps.current?.selectedAircraftId).toBeNull();
  });

  it('keeps an explicit encounter_id when the same multi-encounter aircraft is clicked', async () => {
    renderPage('/encounters?encounterId=enc-a2');

    await waitFor(() => {
      expect(
        screen.getByRole('link', { name: 'Open Aircraft Investigation' }),
      ).toHaveAttribute('href', expect.stringContaining('encounterId=enc-a2'));
      expect(mapProps.current?.onSelectAircraft).toBeTypeOf('function');
    });

    act(() => {
      mapProps.current?.onSelectAircraft('aa0001');
    });

    expect(
      await screen.findByRole('region', {
        name: 'Current encounters for this aircraft',
      }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole('link', { name: 'Open Aircraft Investigation' }),
    ).toHaveAttribute('href', expect.stringContaining('encounterId=enc-a2'));
    expect(mapProps.current?.selectedHazardId).toBe('sigmet-b');
  });

  it('auto-selects the only current loaded encounter when a map aircraft is clicked', async () => {
    const { getAircraft } = renderPage('/encounters', {
      items: [ENCOUNTERS[0]!],
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
      ).toHaveAttribute('href', expect.stringContaining('encounterId=enc-a1'));
    });

    expect(screen.queryByText(/choose one/)).not.toBeInTheDocument();
    expect(getAircraft).toHaveBeenCalledTimes(1);
    expect(mapProps.current?.selectedHazardId).toBe('sigmet-a');
  });

  it('opens a chooser when a map aircraft has several current loaded encounters', async () => {
    const { getAircraft } = renderPage();

    await waitFor(() => {
      expect(screen.getByText('sigmet-a')).toBeInTheDocument();
      expect(mapProps.current?.onSelectAircraft).toBeTypeOf('function');
    });

    act(() => {
      mapProps.current?.onSelectAircraft('aa0001');
    });

    const chooser = await screen.findByRole('region', {
      name: 'Current encounters for this aircraft',
    });

    expect(within(chooser).getByText('sigmet-a')).toBeInTheDocument();
    expect(within(chooser).getByText('sigmet-b')).toBeInTheDocument();
    expect(within(chooser).getByText('Convection')).toBeInTheDocument();
    expect(within(chooser).getByText('Icing')).toBeInTheDocument();
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
        '/aircraft/aa0001?hazardId=sigmet-b&encounterId=enc-a2&riskId=risk-a2&source=encounter',
      );
    });

    expect(
      screen.queryByRole('region', {
        name: 'Current encounters for this aircraft',
      }),
    ).not.toBeInTheDocument();
    expect(mapProps.current?.selectedHazardId).toBe('sigmet-b');
    expect(mapProps.current?.emphasizedHazardIds).toEqual(['sigmet-b']);
    expect(getAircraft.mock.calls[0]?.[0]).toBe('aa0001');
  });

  it('uses KPI cards as loaded-page risk filters with an active state', async () => {
    const { listActiveEncounters } = renderPage();

    await waitFor(() => {
      expect(screen.getByText('sigmet-b')).toBeInTheDocument();
    });

    const current = kpi('Current Encounters');
    const low = kpi('LOW');
    const medium = kpi('MEDIUM');
    const high = kpi('HIGH');

    expect(current).toHaveAttribute('aria-pressed', 'true');
    expect(within(low).getByText('stored risk')).toBeInTheDocument();

    fireEvent.click(low);

    expect(screen.getByLabelText('Risk')).toHaveValue('LOW');
    expect(screen.getByText('sigmet-b')).toBeInTheDocument();
    expect(screen.queryByText('sigmet-a')).not.toBeInTheDocument();
    expect(low).toHaveAttribute('aria-pressed', 'true');
    expect(current).toHaveAttribute('aria-pressed', 'false');
    expect(low.className).toMatch(/active/);
    expect(mapProps.current?.visibleAircraftIds).toEqual(['aa0001']);
    expect(
      listActiveEncounters.mock.calls.every(
        (call) => call[0] == null || call[0].riskLevel == null,
      ),
    ).toBe(true);

    fireEvent.click(medium);

    expect(screen.getByLabelText('Risk')).toHaveValue('MEDIUM');
    expect(screen.getByText('sigmet-a')).toBeInTheDocument();
    expect(screen.queryByText('sigmet-b')).not.toBeInTheDocument();

    fireEvent.click(high);

    expect(screen.getByLabelText('Risk')).toHaveValue('HIGH');
    expect(
      screen.getByText('No encounters match these filters'),
    ).toBeInTheDocument();
    expect(high).toHaveAttribute('aria-pressed', 'true');
    expect(mapProps.current?.visibleAircraftIds).toEqual([]);
    expect(within(high).getByText('0')).toBeInTheDocument();

    fireEvent.click(current);

    expect(screen.getByLabelText('Risk')).toHaveValue('');
    expect(screen.getByText('sigmet-a')).toBeInTheDocument();
    expect(screen.getByText('sigmet-b')).toBeInTheDocument();
    expect(current).toHaveAttribute('aria-pressed', 'true');
    expect(mapProps.current?.visibleAircraftIds).toEqual(['aa0001']);
  });

  it('clears a selection that the KPI risk filter excludes instead of substituting another', async () => {
    renderPage('/encounters?encounterId=enc-a1');

    await waitFor(() => {
      expect(
        screen.getByRole('link', { name: 'Open Aircraft Investigation' }),
      ).toHaveAttribute('href', expect.stringContaining('encounterId=enc-a1'));
    });

    fireEvent.click(kpi('LOW'));

    expect(screen.getByLabelText('Risk')).toHaveValue('LOW');
    expect(screen.getByText('sigmet-b')).toBeInTheDocument();
    expect(screen.queryByText('sigmet-a')).not.toBeInTheDocument();
    expect(
      screen.queryByRole('link', { name: 'Open Aircraft Investigation' }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByText('This encounter is no longer current.'),
    ).not.toBeInTheDocument();
    expect(mapProps.current?.selectedAircraftId).toBeNull();
    expect(mapProps.current?.selectedHazardId).toBeNull();
  });
});

function kpi(label: string) {
  return screen.getByRole('button', {
    name: (_accessibleName, element) =>
      element.querySelector('p')?.textContent === label,
  });
}
