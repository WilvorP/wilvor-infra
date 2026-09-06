import { act, fireEvent, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { describe, expect, it, vi } from 'vitest';

import type { OperationsMapProps } from '@/features/map/OperationsMap';
import { createTestQueryClient, renderWithProviders } from '@/test/renderWithProviders';
import type { Recommendation } from '@/types/api';

import { RecommendationsPage } from './RecommendationsPage';

const mapProps: { current: OperationsMapProps | null } = { current: null };

vi.mock('@/features/map/OperationsMap', () => ({
  OperationsMap: (props: OperationsMapProps) => {
    mapProps.current = props;
    return <div>Operations map</div>;
  },
}));

const RECOMMENDATIONS: Recommendation[] = [
  {
    recommendation_id: 'rec-a1',
    aircraft_id: 'aa0001',
    hazard_id: 'sigmet-a',
    risk_id: 'risk-a1',
    recommendation_status: 'ACTIVE',
    primary_action_type: 'MONITOR',
    risk_level: 'LOW',
    risk_score: 40,
    confidence: 'HIGH',
    reasons: ['Continue monitoring the convection SIGMET.'],
    created_at_epoch: 30,
  },
  {
    recommendation_id: 'rec-a2',
    aircraft_id: 'aa0001',
    hazard_id: 'sigmet-b',
    risk_id: 'risk-a2',
    recommendation_status: 'ACTIVE',
    primary_action_type: 'MONITOR_AND_PREPARE_OPTIONS',
    risk_level: 'MEDIUM',
    risk_score: 57,
    confidence: 'LOW',
    reasons: ['Prepare options while remaining on the current route.'],
    created_at_epoch: 40,
  },
];

function renderPage(
  path = '/recommendations',
  extra: {
    items?: Recommendation[] | (() => Recommendation[]);
    getAircraft?: ReturnType<typeof vi.fn>;
    listActiveRecommendations?: ReturnType<typeof vi.fn>;
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
          recommendation: RECOMMENDATIONS[0],
          risk: {
            risk_id: 'risk-a1',
            risk_level: 'LOW',
            risk_score: 40,
          },
        },
      ],
    }));

  const listActiveRecommendations =
    extra.listActiveRecommendations ??
    vi.fn(async () => {
      const items =
        typeof extra.items === 'function'
          ? extra.items()
          : (extra.items ?? RECOMMENDATIONS);

      return { items, count: items.length, nextToken: null };
    });

  mapProps.current = null;

  return {
    ...renderWithProviders(
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route
            path="/recommendations"
            element={<RecommendationsPage mapStyleUrl={null} />}
          />
        </Routes>
      </MemoryRouter>,
      {
        queryClient: extra.queryClient,
        client: {
          listActiveRecommendations,
          getAircraft,
          overview: async () => ({
            recommendations: {
              currentCount: 293,
              activeCount: 5000,
              latest: [],
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
    listActiveRecommendations,
  };
}

describe('RecommendationsPage', () => {
  it('uses overview currentCount and does not fetch detail until a recommendation is selected', async () => {
    const { getAircraft } = renderPage();

    await waitFor(() => {
      expect(screen.getByText('293')).toBeInTheDocument();
      expect(screen.getAllByText('UAL9').length).toBeGreaterThan(0);
    });

    expect(
      screen.getByRole('heading', { name: 'Current Recommendations' }),
    ).toBeInTheDocument();
    expect(screen.queryByText('5,000')).not.toBeInTheDocument();
    expect(getAircraft).not.toHaveBeenCalled();
    expect(mapProps.current?.visibleAircraftIds).toEqual(['aa0001']);
  });

  it('selects by recommendation id, focuses the map, and deep-links Aircraft Investigation', async () => {
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
        '/aircraft/aa0001?hazardId=sigmet-b&riskId=risk-a2&recommendationId=rec-a2&source=recommendation',
      );
    });

    expect(getAircraft).toHaveBeenCalledTimes(1);
    expect(getAircraft.mock.calls[0]?.[0]).toBe('aa0001');
    expect(mapProps.current?.selectedAircraftId).toBe('aa0001');
    expect(mapProps.current?.selectedHazardId).toBe('sigmet-b');
    expect(mapProps.current?.emphasizedHazardIds).toEqual(['sigmet-b']);
  });

  it('preserves recommendationId in the URL and restores selection from it', async () => {
    renderPage('/recommendations?recommendationId=rec-a1');

    await waitFor(() => {
      expect(
        screen.getByText('Continue monitoring the convection SIGMET.'),
      ).toBeInTheDocument();
    });

    expect(screen.getByText('Selected')).toBeInTheDocument();
    expect(
      screen.getByRole('link', { name: 'Open Aircraft Investigation' }),
    ).toHaveAttribute(
      'href',
      '/aircraft/aa0001?hazardId=sigmet-a&riskId=risk-a1&recommendationId=rec-a1&source=recommendation',
    );
    await waitFor(() => {
      expect(mapProps.current?.projectionPoints).toEqual([
        { projection_id: 'proj-1', latitude: 37.6, longitude: -122.3 },
      ]);
    });
  });

  it('reports a URL recommendation that is not among the loaded pages', async () => {
    renderPage('/recommendations?recommendationId=rec-later');

    await waitFor(() => {
      expect(
        screen.getByText('This recommendation is not among the loaded pages.'),
      ).toBeInTheDocument();
    });

    expect(
      screen.queryByRole('link', { name: 'Open Aircraft Investigation' }),
    ).not.toBeInTheDocument();
  });

  it('keeps a vanished selection instead of picking another recommendation', async () => {
    let items = RECOMMENDATIONS;
    const queryClient = createTestQueryClient();
    const listActiveRecommendations = vi.fn(async () => ({
      items,
      count: items.length,
      nextToken: null,
    }));

    renderPage('/recommendations?recommendationId=rec-a1', {
      queryClient,
      listActiveRecommendations,
    });

    await waitFor(() => {
      expect(
        screen.getByText('Continue monitoring the convection SIGMET.'),
      ).toBeInTheDocument();
    });

    items = [RECOMMENDATIONS[1]!];
    await queryClient.invalidateQueries();

    await waitFor(() => {
      expect(
        screen.getByText('This recommendation is no longer current.'),
      ).toBeInTheDocument();
    });

    expect(
      screen.queryByText('Continue monitoring the convection SIGMET.'),
    ).not.toBeInTheDocument();
    expect(mapProps.current?.selectedAircraftId).toBeNull();
  });

  it('keeps an explicit recommendation_id when the same multi-recommendation aircraft is clicked', async () => {
    renderPage('/recommendations?recommendationId=rec-a2');

    await waitFor(() => {
      expect(
        screen.getByRole('link', { name: 'Open Aircraft Investigation' }),
      ).toHaveAttribute(
        'href',
        expect.stringContaining('recommendationId=rec-a2'),
      );
      expect(mapProps.current?.onSelectAircraft).toBeTypeOf('function');
    });

    act(() => {
      mapProps.current?.onSelectAircraft('aa0001');
    });

    expect(
      await screen.findByRole('region', {
        name: 'Current recommendations for this aircraft',
      }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole('link', { name: 'Open Aircraft Investigation' }),
    ).toHaveAttribute('href', expect.stringContaining('recommendationId=rec-a2'));
    expect(mapProps.current?.selectedHazardId).toBe('sigmet-b');
  });

  it('auto-selects the only current loaded recommendation when a map aircraft is clicked', async () => {
    const { getAircraft } = renderPage('/recommendations', {
      items: [RECOMMENDATIONS[0]!],
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
      ).toHaveAttribute(
        'href',
        expect.stringContaining('recommendationId=rec-a1'),
      );
    });

    expect(screen.queryByText(/choose one/)).not.toBeInTheDocument();
    expect(getAircraft).toHaveBeenCalledTimes(1);
    expect(mapProps.current?.selectedHazardId).toBe('sigmet-a');
  });

  it('opens a chooser when a map aircraft has several current loaded recommendations', async () => {
    const { getAircraft } = renderPage();

    await waitFor(() => {
      expect(screen.getByText('sigmet-a')).toBeInTheDocument();
      expect(mapProps.current?.onSelectAircraft).toBeTypeOf('function');
    });

    act(() => {
      mapProps.current?.onSelectAircraft('aa0001');
    });

    const chooser = await screen.findByRole('region', {
      name: 'Current recommendations for this aircraft',
    });

    expect(within(chooser).getByText('sigmet-a')).toBeInTheDocument();
    expect(within(chooser).getByText('sigmet-b')).toBeInTheDocument();
    expect(within(chooser).getByText('Monitor')).toBeInTheDocument();
    expect(
      within(chooser).getByText('Monitor and prepare options'),
    ).toBeInTheDocument();
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
        '/aircraft/aa0001?hazardId=sigmet-b&riskId=risk-a2&recommendationId=rec-a2&source=recommendation',
      );
    });

    expect(
      screen.queryByRole('region', {
        name: 'Current recommendations for this aircraft',
      }),
    ).not.toBeInTheDocument();
    expect(mapProps.current?.selectedHazardId).toBe('sigmet-b');
    expect(mapProps.current?.emphasizedHazardIds).toEqual(['sigmet-b']);
    expect(getAircraft.mock.calls[0]?.[0]).toBe('aa0001');
  });

  it('uses KPI cards as loaded-page action filters with an active state', async () => {
    const { listActiveRecommendations } = renderPage();

    await waitFor(() => {
      expect(screen.getByText('sigmet-b')).toBeInTheDocument();
    });

    const current = kpi('Current Recommendations');
    const monitor = kpi('Monitor');
    const prepare = kpi('Monitor and prepare');
    const diversion = kpi('Evaluate diversion');

    expect(current).toHaveAttribute('aria-pressed', 'true');
    expect(monitor).toHaveAttribute('aria-pressed', 'false');
    expect(within(monitor).getByText('loaded pages')).toBeInTheDocument();

    fireEvent.click(monitor);

    expect(screen.getByLabelText('Action')).toHaveValue('MONITOR');
    expect(screen.getByText('sigmet-a')).toBeInTheDocument();
    expect(screen.queryByText('sigmet-b')).not.toBeInTheDocument();
    expect(monitor).toHaveAttribute('aria-pressed', 'true');
    expect(current).toHaveAttribute('aria-pressed', 'false');
    expect(monitor.className).toMatch(/active/);
    expect(mapProps.current?.visibleAircraftIds).toEqual(['aa0001']);
    expect(
      listActiveRecommendations.mock.calls.every(
        (call) => call[0] == null || call[0].action == null,
      ),
    ).toBe(true);

    fireEvent.click(prepare);

    expect(screen.getByLabelText('Action')).toHaveValue(
      'MONITOR_AND_PREPARE_OPTIONS',
    );
    expect(screen.getByText('sigmet-b')).toBeInTheDocument();
    expect(screen.queryByText('Continue monitoring the convection SIGMET.')).not.toBeInTheDocument();

    fireEvent.click(diversion);

    expect(screen.getByLabelText('Action')).toHaveValue('EVALUATE_DIVERSION');
    expect(
      screen.getByText('No recommendations match these filters'),
    ).toBeInTheDocument();
    expect(screen.queryByText('sigmet-a')).not.toBeInTheDocument();
    expect(screen.queryByText('sigmet-b')).not.toBeInTheDocument();
    expect(diversion).toHaveAttribute('aria-pressed', 'true');
    expect(mapProps.current?.visibleAircraftIds).toEqual([]);
    expect(within(diversion).getByText('0')).toBeInTheDocument();

    fireEvent.click(current);

    expect(screen.getByLabelText('Action')).toHaveValue('');
    expect(screen.getByText('sigmet-a')).toBeInTheDocument();
    expect(screen.getByText('sigmet-b')).toBeInTheDocument();
    expect(current).toHaveAttribute('aria-pressed', 'true');
    expect(diversion).toHaveAttribute('aria-pressed', 'false');
    expect(mapProps.current?.visibleAircraftIds).toEqual(['aa0001']);
  });

  it('clears a selection that the KPI action filter excludes instead of substituting another', async () => {
    renderPage('/recommendations?recommendationId=rec-a2');

    await waitFor(() => {
      expect(
        screen.getByRole('link', { name: 'Open Aircraft Investigation' }),
      ).toHaveAttribute('href', expect.stringContaining('recommendationId=rec-a2'));
    });

    fireEvent.click(kpi('Monitor'));

    expect(screen.getByLabelText('Action')).toHaveValue('MONITOR');
    expect(screen.getByText('sigmet-a')).toBeInTheDocument();
    expect(screen.queryByText('sigmet-b')).not.toBeInTheDocument();
    expect(
      screen.queryByRole('link', { name: 'Open Aircraft Investigation' }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByText('This recommendation is no longer current.'),
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
