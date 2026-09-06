import { useState } from 'react';
import { fireEvent, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { renderWithProviders } from '@/test/renderWithProviders';
import type { Recommendation } from '@/types/api';

import { RecommendationWorklist } from './RecommendationWorklist';
import {
  EMPTY_RECOMMENDATION_FILTERS,
  type RecommendationFilters,
} from './recommendationList';

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
    created_at_utc: '2026-09-06T02:30:00Z',
    created_at_epoch: 30,
    valid_until_utc: '2026-09-06T06:00:00Z',
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
    created_at_utc: '2026-09-06T02:40:00Z',
    created_at_epoch: 40,
    valid_until_utc: '2026-09-06T06:00:00Z',
  },
  {
    recommendation_id: 'rec-c1',
    aircraft_id: 'cc0003',
    hazard_id: 'sigmet-a',
    risk_id: 'risk-c1',
    recommendation_status: 'ACTIVE',
    primary_action_type: 'EVALUATE_DIVERSION',
    risk_level: 'HIGH',
    risk_score: 80,
    confidence: 'MEDIUM',
    reasons: ['Evaluate diversion options against stored airport evidence.'],
    created_at_utc: '2026-09-06T02:00:00Z',
    created_at_epoch: 10,
  },
];

function renderList(
  extra: {
    items?: Recommendation[];
    nextToken?: string | null;
    onSelect?: (recommendationId: string) => void;
    selectedRecommendationId?: string | null;
    listActiveRecommendations?: ReturnType<typeof vi.fn>;
    getAircraft?: ReturnType<typeof vi.fn>;
  } = {},
) {
  const listActiveRecommendations =
    extra.listActiveRecommendations ??
    vi.fn(async () => ({
      items: extra.items ?? RECOMMENDATIONS,
      count: (extra.items ?? RECOMMENDATIONS).length,
      nextToken: extra.nextToken ?? null,
    }));
  const getAircraft = extra.getAircraft ?? vi.fn();
  const onSelect = extra.onSelect ?? vi.fn();

  function WorklistHarness() {
    const [filters, setFilters] = useState<RecommendationFilters>(
      EMPTY_RECOMMENDATION_FILTERS,
    );

    return (
      <RecommendationWorklist
        selectedRecommendationId={extra.selectedRecommendationId ?? null}
        onSelect={onSelect}
        filters={filters}
        onFiltersChange={setFilters}
      />
    );
  }

  const view = renderWithProviders(
    <WorklistHarness />,
    {
      client: {
        listActiveRecommendations,
        getAircraft,
        overview: async () => ({
          recommendations: { currentCount: 293, activeCount: 5000, latest: [] },
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
  );

  return { ...view, listActiveRecommendations, getAircraft, onSelect };
}

describe('RecommendationWorklist', () => {
  it('renders stored actions, risk and multiple recommendations for one aircraft', async () => {
    renderList();

    await waitFor(() => {
      expect(screen.getAllByText('UAL9').length).toBe(2);
    });

    expect(screen.getAllByText('AA0001').length).toBeGreaterThan(0);
    expect(screen.getByText('CC0003')).toBeInTheDocument();
    expect(screen.getByText('sigmet-b')).toBeInTheDocument();
    expect(screen.getAllByText('Monitor').length).toBeGreaterThan(0);
    expect(
      screen.getAllByText('Monitor and prepare options').length,
    ).toBeGreaterThan(0);
    expect(screen.getAllByText('Evaluate diversion').length).toBeGreaterThan(0);
    expect(screen.getAllByText('Low').length).toBeGreaterThan(0);
    expect(screen.getAllByText('Medium').length).toBeGreaterThan(0);
    expect(screen.getAllByText('High').length).toBeGreaterThan(0);
    expect(
      screen.getByText('Continue monitoring the convection SIGMET.'),
    ).toBeInTheDocument();
    expect(screen.getAllByText('—').length).toBeGreaterThan(0);
    expect(screen.queryByText('DIVERT NOW')).not.toBeInTheDocument();
    expect(
      screen.getByText(
        /3 loaded of 293 current · 2 aircraft · 2 hazards on loaded pages/,
      ),
    ).toBeInTheDocument();
    expect(screen.queryByText(/5,000/)).not.toBeInTheDocument();
  });

  it('selects by recommendation id so one aircraft can keep multiple current rows', async () => {
    const { onSelect } = renderList();

    await waitFor(() => {
      expect(screen.getByText('sigmet-b')).toBeInTheDocument();
    });

    fireEvent.click(screen.getByText('sigmet-b').closest('tr') as HTMLElement);

    expect(onSelect).toHaveBeenCalledWith('rec-a2');
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

    fireEvent.change(screen.getByLabelText('Action'), {
      target: { value: 'MONITOR' },
    });
    expect(screen.getAllByText('UAL9').length).toBeGreaterThan(0);
    expect(screen.queryByText('CC0003')).not.toBeInTheDocument();
    expect(screen.getByText(/1 of 3 loaded rows match the filters/)).toBeInTheDocument();
  });

  it('sorts loaded recommendations by aircraft without inventing a composite score', async () => {
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
    const listActiveRecommendations = vi.fn(
      async (request: { nextToken?: string | null }) => {
        if (request.nextToken) {
          expect(request.nextToken).toBe('opaque-token');
          return { items: [RECOMMENDATIONS[2]!], count: 1, nextToken: null };
        }

        return { items: [RECOMMENDATIONS[0]!], count: 1, nextToken: 'opaque-token' };
      },
    );

    renderList({ listActiveRecommendations });

    await waitFor(() => {
      expect(
        screen.getByText('Load more current recommendations'),
      ).toBeInTheDocument();
    });

    fireEvent.click(screen.getByText('Load more current recommendations'));

    await waitFor(() => {
      expect(screen.getByText('CC0003')).toBeInTheDocument();
    });

    expect(listActiveRecommendations).toHaveBeenLastCalledWith(
      expect.objectContaining({ nextToken: 'opaque-token' }),
    );
  });

  it('reports an empty current set without saying recommendations do not exist', async () => {
    renderList({ items: [] });

    await waitFor(() => {
      expect(screen.getByText('No current recommendations')).toBeInTheDocument();
    });
    expect(screen.queryByText('No recommendations exist')).not.toBeInTheDocument();
  });

  it('surfaces a current-set API error', async () => {
    renderList({
      listActiveRecommendations: vi.fn(async () => {
        throw new Error('recommendations unavailable');
      }),
    });

    await waitFor(() => {
      expect(
        screen.getByText('Current recommendations unavailable'),
      ).toBeInTheDocument();
    });
  });

  it('surfaces a pagination failure without dropping the loaded page', async () => {
    const listActiveRecommendations = vi.fn(
      async (request: { nextToken?: string | null }) => {
        if (request.nextToken) {
          throw new Error('page two failed');
        }

        return { items: [RECOMMENDATIONS[0]!], count: 1, nextToken: 'opaque-token' };
      },
    );

    renderList({ listActiveRecommendations });

    await waitFor(() => {
      expect(
        screen.getByText('Load more current recommendations'),
      ).toBeInTheDocument();
    });

    fireEvent.click(screen.getByText('Load more current recommendations'));

    await waitFor(() => {
      expect(
        screen.getByText(/The next current-recommendation page could not be loaded/),
      ).toBeInTheDocument();
    });
    expect(screen.getByText('UAL9')).toBeInTheDocument();
  });
});
