import { useState } from 'react';
import { fireEvent, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { renderWithProviders } from '@/test/renderWithProviders';
import type { ActiveEncounterItem } from '@/types/api';

import { EncounterWorklist } from './EncounterWorklist';
import {
  EMPTY_ENCOUNTER_FILTERS,
  type EncounterFilters,
} from './encounterList';

const ENCOUNTERS: ActiveEncounterItem[] = [
  {
    encounter: {
      encounter_id: 'enc-a1',
      aircraft_id: 'aa0001',
      hazard_id: 'sigmet-a',
      hazard_type: 'CONVECTION',
      encounter_state: 'DETECTED',
      geometry_overlap_status: 'INSIDE_NOW',
      time_overlap_status: 'OVERLAP',
      altitude_overlap_status: 'UNKNOWN',
      inside_now: true,
      detected_at_utc: '2026-09-06T02:30:00Z',
      detected_at_epoch: 30,
    },
    risk: {
      risk_id: 'risk-a1',
      risk_level: 'MEDIUM',
      risk_score: 65,
      confidence: 'HIGH',
    },
  },
  {
    encounter: {
      encounter_id: 'enc-a2',
      aircraft_id: 'aa0001',
      hazard_id: 'sigmet-b',
      hazard_type: 'ICING',
      encounter_state: 'MONITORING',
      geometry_overlap_status: 'UNKNOWN',
      time_overlap_status: 'OVERLAP',
      altitude_overlap_status: 'NO_OVERLAP',
      inside_now: false,
      detected_at_utc: '2026-09-06T02:10:00Z',
      detected_at_epoch: 20,
    },
    risk: {
      risk_id: 'risk-a2',
      risk_level: 'LOW',
      risk_score: 40,
      confidence: 'MEDIUM',
    },
  },
  {
    encounter: {
      encounter_id: 'enc-c1',
      aircraft_id: 'cc0003',
      hazard_id: 'sigmet-a',
      hazard_type: 'CONVECTION',
      encounter_state: 'DETECTED',
      geometry_overlap_status: 'CORRIDOR_ONLY_INTERSECTION',
      time_overlap_status: 'NO_OVERLAP',
      altitude_overlap_status: 'OVERLAP',
      inside_now: false,
      detected_at_utc: '2026-09-06T02:00:00Z',
      detected_at_epoch: 10,
    },
    risk: {
      risk_id: 'risk-c1',
      risk_level: 'LOW',
      risk_score: 35,
      confidence: 'LOW',
    },
  },
];

function renderList(
  extra: {
    items?: ActiveEncounterItem[];
    nextToken?: string | null;
    onSelect?: (encounterId: string) => void;
    selectedEncounterId?: string | null;
    listActiveEncounters?: ReturnType<typeof vi.fn>;
    getAircraft?: ReturnType<typeof vi.fn>;
  } = {},
) {
  const listActiveEncounters =
    extra.listActiveEncounters ??
    vi.fn(async () => ({
      items: extra.items ?? ENCOUNTERS,
      count: (extra.items ?? ENCOUNTERS).length,
      nextToken: extra.nextToken ?? null,
    }));
  const getAircraft = extra.getAircraft ?? vi.fn();
  const onSelect = extra.onSelect ?? vi.fn();

  function WorklistHarness() {
    const [filters, setFilters] = useState<EncounterFilters>(
      EMPTY_ENCOUNTER_FILTERS,
    );

    return (
      <EncounterWorklist
        selectedEncounterId={extra.selectedEncounterId ?? null}
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
        listActiveEncounters,
        getAircraft,
        overview: async () => ({
          encounters: { activeCount: 1326, lowRiskCount: 1200, mediumRiskCount: 126, highRiskCount: 0 },
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

  return { ...view, listActiveEncounters, getAircraft, onSelect };
}

describe('EncounterWorklist', () => {
  it('renders stored risk, UNKNOWN altitude and multiple hazards for one aircraft', async () => {
    renderList();

    await waitFor(() => {
      expect(screen.getAllByText('UAL9').length).toBe(2);
    });

    expect(screen.getAllByText('AA0001').length).toBeGreaterThan(0);
    expect(screen.getAllByText('sigmet-a').length).toBeGreaterThan(0);
    expect(screen.getByText('sigmet-b')).toBeInTheDocument();
    expect(screen.getByText('CC0003')).toBeInTheDocument();
    expect(screen.getAllByText('Low').length).toBeGreaterThan(0);
    expect(screen.getAllByText('Medium').length).toBeGreaterThan(0);
    expect(screen.getAllByText('Inside hazard now').length).toBeGreaterThan(0);
    expect(screen.getAllByText('Unknown').length).toBeGreaterThan(0);
    expect(screen.getAllByText('No overlap').length).toBeGreaterThan(0);
    expect(
      screen.getByText(/3 loaded of 1,326 current · 2 aircraft · 2 hazards on loaded pages/),
    ).toBeInTheDocument();
  });

  it('selects by encounter id so one aircraft can keep multiple current rows', async () => {
    const { onSelect } = renderList();

    await waitFor(() => {
      expect(screen.getByText('sigmet-b')).toBeInTheDocument();
    });

    fireEvent.click(screen.getByText('sigmet-b').closest('tr') as HTMLElement);

    expect(onSelect).toHaveBeenCalledWith('enc-a2');
  });

  it('does not request aircraft detail for worklist rows', async () => {
    const { getAircraft } = renderList();

    await waitFor(() => {
      expect(screen.getAllByText('UAL9').length).toBeGreaterThan(0);
    });

    expect(getAircraft).not.toHaveBeenCalled();
  });

  it('filters loaded pages only and keeps UNKNOWN as Unknown', async () => {
    renderList();

    await waitFor(() => {
      expect(screen.getByText('CC0003')).toBeInTheDocument();
    });

    fireEvent.change(screen.getByLabelText('Risk'), { target: { value: 'MEDIUM' } });
    expect(screen.getAllByText('UAL9').length).toBeGreaterThan(0);
    expect(screen.queryByText('CC0003')).not.toBeInTheDocument();

    fireEvent.change(screen.getByLabelText('Risk'), { target: { value: '' } });
    fireEvent.change(screen.getByLabelText('Altitude'), {
      target: { value: 'UNKNOWN' },
    });

    expect(screen.getAllByText('Unknown').length).toBeGreaterThan(0);
    expect(screen.queryByText('CC0003')).not.toBeInTheDocument();
    expect(screen.getByText(/1 of 3 loaded rows match the filters/)).toBeInTheDocument();
  });

  it('sorts loaded encounters by aircraft without inventing a composite score', async () => {
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
    const listActiveEncounters = vi.fn(async (request: { nextToken?: string | null }) => {
      if (request.nextToken) {
        expect(request.nextToken).toBe('opaque-token');
        return { items: [ENCOUNTERS[2]!], count: 1, nextToken: null };
      }

      return { items: [ENCOUNTERS[0]!], count: 1, nextToken: 'opaque-token' };
    });

    renderList({ listActiveEncounters });

    await waitFor(() => {
      expect(screen.getByText('Load more current encounters')).toBeInTheDocument();
    });

    fireEvent.click(screen.getByText('Load more current encounters'));

    await waitFor(() => {
      expect(screen.getByText('CC0003')).toBeInTheDocument();
    });

    expect(listActiveEncounters).toHaveBeenLastCalledWith(
      expect.objectContaining({ nextToken: 'opaque-token' }),
    );
  });

  it('reports an empty current set without saying encounters do not exist', async () => {
    renderList({ items: [] });

    await waitFor(() => {
      expect(screen.getByText('No current encounters')).toBeInTheDocument();
    });
    expect(screen.queryByText('No encounters exist')).not.toBeInTheDocument();
  });

  it('surfaces a current-set API error', async () => {
    renderList({
      listActiveEncounters: vi.fn(async () => {
        throw new Error('encounters unavailable');
      }),
    });

    await waitFor(() => {
      expect(screen.getByText('Current encounters unavailable')).toBeInTheDocument();
    });
  });

  it('surfaces a pagination failure without dropping the loaded page', async () => {
    const listActiveEncounters = vi.fn(async (request: { nextToken?: string | null }) => {
      if (request.nextToken) {
        throw new Error('page two failed');
      }

      return { items: [ENCOUNTERS[0]!], count: 1, nextToken: 'opaque-token' };
    });

    renderList({ listActiveEncounters });

    await waitFor(() => {
      expect(screen.getByText('Load more current encounters')).toBeInTheDocument();
    });

    fireEvent.click(screen.getByText('Load more current encounters'));

    await waitFor(() => {
      expect(
        screen.getByText(/The next current-encounter page could not be loaded/),
      ).toBeInTheDocument();
    });
    expect(screen.getByText('UAL9')).toBeInTheDocument();
  });
});
