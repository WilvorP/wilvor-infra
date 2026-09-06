import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import type { ComponentProps } from 'react';
import { describe, expect, it, vi } from 'vitest';

import { ApiError } from '@/api/errors';
import type { MapAircraft } from '@/features/map/aircraftGeoJson';
import { renderWithProviders } from '@/test/renderWithProviders';
import type { AircraftDetailResponse } from '@/types/api';

import { AircraftSelectionPanel } from './AircraftSelectionPanel';

/** 2026-01-01T00:00:00Z. */
const POSITION_EPOCH = 1767225600;

const NOW_MS = (POSITION_EPOCH + 45) * 1000;

function aircraft(overrides: Partial<MapAircraft> = {}): MapAircraft {
  return {
    aircraftId: 'a1b2c3',
    callsign: 'UAL123',
    longitude: -122.375,
    latitude: 37.6188,
    trackDeg: 270,
    baroAltitudeFt: 35000,
    groundSpeedKt: 450,
    positionTimeEpoch: POSITION_EPOCH,
    ...overrides,
  };
}

const EMPTY_DETAIL: AircraftDetailResponse = {
  aircraft: { aircraft_id: 'a1b2c3', callsign: 'UAL123' },
  projection: null,
  projectionPoints: [],
  currentContexts: [],
  recentEncounters: [],
  recentRisks: [],
  recentRecommendations: [],
  recentAlerts: [],
};

const FULL_DETAIL: AircraftDetailResponse = {
  aircraft: {
    aircraft_id: 'a1b2c3',
    callsign: 'UAL123',
    latitude: 37.6188,
    longitude: -122.375,
    baro_altitude_ft: 35000,
    geo_altitude_ft: 35100,
    ground_speed_kt: 450,
    track_deg: 270,
    vertical_rate_fpm: 64,
    origin_country: 'United States',
    on_ground: false,
    freshness_status: 'FRESH',
    current_h3_cell: '8428347ffffffff',
    position_time_utc: '2026-01-01T00:00:00Z',
    position_age_seconds: 45,
  },
  projection: {
    projection_id: 'projection-1',
    generated_at_utc: '2026-01-01T00:00:10Z',
    valid_until_utc: '2026-01-01T00:20:10Z',
    projection_horizon_min: 20,
    point_count: 2,
    confidence: 'HIGH',
    projection_status: 'READY',
    trigger_hazard_ids: ['sigmet-abc'],
  },
  projectionPoints: [
    {
      point_sequence_number: 1,
      horizon_min: 5,
      latitude: 37.62,
      longitude: -122.37,
      estimated_altitude_ft: 35000,
      projected_time_utc: '2026-01-01T00:05:10Z',
    },
    {
      point_sequence_number: 2,
      horizon_min: 10,
      latitude: 37.64,
      longitude: -122.4,
      estimated_altitude_ft: 34800,
      projected_time_utc: '2026-01-01T00:10:10Z',
    },
  ],
  currentContexts: [
    {
      encounter: {
        encounter_id: 'enc-1',
        hazard_id: 'sigmet-abc',
        hazard_type: 'TURBULENCE',
        severity: 'SEVERE',
        encounter_state: 'DETECTED',
        detected_at_utc: '2026-01-01T00:01:00Z',
        inside_now: false,
        altitude_overlap_status: 'UNKNOWN',
        geometry_overlap_status: 'YES',
        time_overlap_status: 'YES',
        trajectory_confidence: 'HIGH',
      },
      risk: {
        risk_id: 'risk-1',
        encounter_id: 'enc-1',
        risk_level: 'HIGH',
        risk_score: 82,
        confidence: 'MEDIUM',
        freshness_status: 'FRESH',
        reasons: ['Projected path intersects active SIGMET'],
        limitations: ['Altitude overlap is unknown'],
      },
      recommendation: {
        recommendation_id: 'rec-1',
        primary_action_type: 'EVALUATE_DIVERSION',
        primary_action_details: {
          advisory: 'Evaluate diversion options using ranked airport evidence.',
          requires_human_review: true,
          candidate_count: 1,
        },
      },
      alert: {
        alert_id: 'alert-1',
        alert_state: 'NEW',
      },
    },
  ],
  recentEncounters: [
    {
      encounter_id: 'enc-1',
      hazard_id: 'sigmet-abc',
      hazard_type: 'TURBULENCE',
      severity: 'SEVERE',
      encounter_state: 'DETECTED',
      detected_at_utc: '2026-01-01T00:01:00Z',
      inside_now: false,
      altitude_overlap_status: 'UNKNOWN',
      geometry_overlap_status: 'YES',
      time_overlap_status: 'YES',
      trajectory_confidence: 'HIGH',
    },
  ],
  recentRisks: [
    {
      risk_id: 'risk-1',
      encounter_id: 'enc-1',
      risk_level: 'HIGH',
      risk_score: 82,
      confidence: 'MEDIUM',
      freshness_status: 'FRESH',
      hazard_component_score: 20,
      geometry_component_score: 18,
      reasons: ['Projected path intersects active SIGMET'],
      limitations: ['Altitude overlap is unknown'],
      generated_at_utc: '2026-01-01T00:01:05Z',
    },
  ],
  recentRecommendations: [
    {
      recommendation_id: 'rec-1',
      primary_action_type: 'EVALUATE_DIVERSION',
      primary_action_details: {
        advisory: 'Evaluate diversion options using ranked airport evidence.',
        requires_human_review: true,
        candidate_count: 1,
      },
      preferred_airport_id: 'KDEN',
      reasons: ['High risk with nearby diversion candidates'],
      limitations: ['Fuel state is unavailable.'],
      advisory_notice:
        'Advisory decision support only. Human operational review is required.',
      confidence: 'MEDIUM',
      candidate_airport_summaries: [
        { airport_id: 'KDEN', rank: 1, total_airport_score: 78 },
      ],
    },
  ],
  recentAlerts: [
    {
      fingerprint: 'fp-1',
      alert_id: 'alert-1',
      alert_state: 'NEW',
      state_reason: 'NEW_HIGH_RISK',
      message: 'High risk encounter requires review.',
      risk_level: 'HIGH',
      risk_score: 82,
      updated_at_utc: '2026-01-01T00:01:10Z',
    },
  ],
};

function renderPanel(
  props: Partial<ComponentProps<typeof AircraftSelectionPanel>> = {},
  getAircraft: (id: string) => Promise<AircraftDetailResponse> = async () =>
    EMPTY_DETAIL,
) {
  return renderWithProviders(
    <AircraftSelectionPanel
      aircraftId="a1b2c3"
      aircraft={aircraft()}
      onClose={() => {}}
      now={NOW_MS}
      {...props}
    />,
    {
      client: {
        getAircraft: vi.fn((id: string) => getAircraft(id)),
      },
    },
  );
}

describe('AircraftSelectionPanel', () => {
  it('renders the fields the map projection actually carries', async () => {
    renderPanel();

    expect(screen.getAllByText('UAL123').length).toBeGreaterThan(0);
    expect(screen.getAllByText('A1B2C3').length).toBeGreaterThan(0);
    expect(screen.getByText('37.6188')).toBeInTheDocument();
    expect(screen.getByText('-122.3750')).toBeInTheDocument();
    expect(screen.getByText('270° true')).toBeInTheDocument();
    expect(screen.getByText('35,000 ft')).toBeInTheDocument();
    expect(screen.getByText('450 kt')).toBeInTheDocument();

    await waitFor(() => {
      expect(screen.getByText('Current state')).toBeInTheDocument();
    });
  });

  it('reports observation age and an absolute UTC position time', () => {
    renderPanel();

    expect(screen.getByText('45s')).toBeInTheDocument();
    expect(screen.getByText('2026-01-01 00:00:00Z')).toBeInTheDocument();
  });

  it('falls back to the ICAO24 id when no callsign is reported', () => {
    renderPanel({ aircraft: aircraft({ callsign: null }) });

    expect(screen.getAllByText('A1B2C3').length).toBeGreaterThan(0);
  });

  it('shows absent measurements as not reported rather than zero', () => {
    renderPanel({
      aircraft: aircraft({
        trackDeg: null,
        baroAltitudeFt: null,
        groundSpeedKt: null,
        positionTimeEpoch: null,
      }),
    });

    expect(screen.getAllByText('—').length).toBeGreaterThanOrEqual(5);
    expect(screen.queryByText('0 ft')).not.toBeInTheDocument();
    expect(screen.queryByText('0 kt')).not.toBeInTheDocument();
    expect(screen.queryByText('0° true')).not.toBeInTheDocument();
  });

  it('keeps map-level fields visible while investigation context is loading', () => {
    renderPanel({}, () => new Promise(() => undefined));

    expect(screen.getAllByText('UAL123').length).toBeGreaterThan(0);
    expect(screen.getByText('35,000 ft')).toBeInTheDocument();
    expect(screen.getByText(/Loading investigation context/)).toBeInTheDocument();
    expect(screen.queryByText('Short-term motion projection')).not.toBeInTheDocument();
  });

  it('keeps map data and reports unavailable context when detail fails', async () => {
    renderPanel({}, async () => {
      throw new ApiError('The operational API did not respond in time.', {
        kind: 'timeout',
      });
    });

    expect(screen.getAllByText('UAL123').length).toBeGreaterThan(0);

    await waitFor(() => {
      expect(
        screen.getByText(/Detailed investigation context is unavailable/),
      ).toBeInTheDocument();
    });

    expect(screen.getByText('35,000 ft')).toBeInTheDocument();
    expect(screen.queryByText('Short-term motion projection')).not.toBeInTheDocument();
  });

  it('explains a 404 without crashing the investigation panel', async () => {
    renderPanel({}, async () => {
      throw new ApiError('Aircraft not found', {
        kind: 'client',
        status: 404,
      });
    });

    await waitFor(() => {
      expect(
        screen.getByText(/No investigation record exists for this aircraft/),
      ).toBeInTheDocument();
    });

    expect(screen.getAllByText('UAL123').length).toBeGreaterThan(0);
  });

  it('explains a selection that dropped out of the feed', () => {
    renderPanel({ aircraft: null });

    expect(screen.getByText(/not present in the most recent map refresh/)).toBeInTheDocument();
    expect(screen.getAllByText('A1B2C3').length).toBeGreaterThan(0);
  });

  it('keeps investigation context when the aircraft leaves the map feed', async () => {
    renderPanel({ aircraft: null }, async () => FULL_DETAIL);

    await waitFor(() => {
      expect(screen.getByText('Short-term motion projection')).toBeInTheDocument();
    });

    expect(screen.getByText(/not present in the most recent map refresh/)).toBeInTheDocument();
    expect(screen.getAllByText('Score 82').length).toBeGreaterThan(0);
  });

  it('keeps the selection dismissable when the aircraft is gone', () => {
    const onClose = vi.fn();

    renderPanel({ aircraft: null, onClose });

    screen.getByRole('button', { name: 'Close' }).click();

    expect(onClose).toHaveBeenCalledOnce();
  });

  it('renders returned risk and recommendation values without rewriting them', async () => {
    renderPanel({}, async () => FULL_DETAIL);

    await waitFor(() => {
      expect(screen.getAllByText('Evaluate diversion').length).toBeGreaterThan(0);
    });

    expect(screen.getAllByText('Score 82').length).toBeGreaterThan(0);
    expect(
      screen.getAllByText('Projected path intersects active SIGMET').length,
    ).toBeGreaterThan(0);
    expect(
      screen.getAllByText(
        'Evaluate diversion options using ranked airport evidence.',
      ).length,
    ).toBeGreaterThan(0);
    expect(screen.queryByText(/^Divert to/)).not.toBeInTheDocument();
    expect(screen.getAllByText('KDEN').length).toBeGreaterThan(0);
    expect(
      screen.getByRole('heading', { name: 'Current decision context' }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole('heading', { name: 'Recent encounters' }),
    ).toBeInTheDocument();
    expect(screen.getByText('Recent history')).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'Why' })).toBeInTheDocument();
    expect(
      screen.getByRole('heading', { name: 'Uncertainties / limitations' }),
    ).toBeInTheDocument();
  });

  it('highlights the current context matched by stored IDs', async () => {
    renderPanel(
      {
        contextSelection: {
          aircraftId: 'a1b2c3',
          encounterId: 'enc-1',
          riskId: 'risk-1',
          source: 'encounter',
        },
      },
      async () => FULL_DETAIL,
    );

    await waitFor(() => {
      expect(screen.getByText('Selected context')).toBeInTheDocument();
    });

    const selectedCard = screen.getByText('Selected context').closest('article');

    expect(selectedCard).toHaveAttribute('aria-current', 'true');
    expect(selectedCard).toHaveTextContent('enc-1');
  });

  it('does not fall back to the latest context when IDs do not match', async () => {
    renderPanel(
      {
        contextSelection: {
          aircraftId: 'a1b2c3',
          encounterId: 'enc-missing',
          riskId: 'risk-missing',
          source: 'encounter',
        },
      },
      async () => FULL_DETAIL,
    );

    await waitFor(() => {
      expect(
        screen.getByText(/not among this aircraft's current contexts/i),
      ).toBeInTheDocument();
    });

    expect(screen.queryByText('Selected context')).not.toBeInTheDocument();
  });

  it('renders stored UNKNOWN altitude overlap as Unknown, not Yes or No', async () => {
    renderPanel({}, async () => FULL_DETAIL);

    await waitFor(() => {
      expect(
        screen.getByRole('heading', { name: 'Current decision context' }),
      ).toBeInTheDocument();
    });

    const decision = screen
      .getByRole('heading', { name: 'Current decision context' })
      .closest('section');
    const altitude = within(decision as HTMLElement).getByText(
      'Altitude overlap',
    ).parentElement;

    expect(altitude).toHaveTextContent('Unknown');
    expect(altitude).not.toHaveTextContent('Yes');
    expect(altitude).not.toHaveTextContent('No');
  });

  it('replaces investigation content when the selected aircraft changes', async () => {
    const getAircraft = vi.fn(async (id: string) => {
      if (id === 'bbbbbb') {
        return {
          ...FULL_DETAIL,
          aircraft: { aircraft_id: 'bbbbbb', callsign: 'AAL9' },
          currentContexts: [],
          recentRisks: [
            {
              risk_id: 'risk-b',
              risk_level: 'LOW',
              risk_score: 12,
              reasons: ['Low residual exposure'],
            },
          ],
          recentRecommendations: [],
        } satisfies AircraftDetailResponse;
      }

      return FULL_DETAIL;
    });

    const { rerender } = renderWithProviders(
      <AircraftSelectionPanel
        aircraftId="a1b2c3"
        aircraft={aircraft()}
        onClose={() => {}}
        now={NOW_MS}
      />,
      { client: { getAircraft } },
    );

    await waitFor(() => {
      expect(screen.getAllByText('Score 82').length).toBeGreaterThan(0);
    });

    rerender(
      <AircraftSelectionPanel
        aircraftId="bbbbbb"
        aircraft={aircraft({ aircraftId: 'bbbbbb', callsign: 'AAL9' })}
        onClose={() => {}}
        now={NOW_MS}
      />,
    );

    await waitFor(() => {
      expect(screen.getByText('Score 12')).toBeInTheDocument();
    });

    expect(screen.queryByText('Score 82')).not.toBeInTheDocument();
    expect(screen.getAllByText('AAL9').length).toBeGreaterThan(0);
  });

  it('lets the operator switch among multiple current contexts without rematching by recency', async () => {
    renderPanel({}, async () => ({
      ...FULL_DETAIL,
      currentContexts: [
        FULL_DETAIL.currentContexts![0],
        {
          encounter: {
            encounter_id: 'enc-2',
            hazard_id: 'sigmet-xyz',
            hazard_type: 'ICING',
            altitude_overlap_status: 'NO',
            geometry_overlap_status: 'YES',
            time_overlap_status: 'YES',
          },
          risk: {
            risk_id: 'risk-2',
            risk_level: 'LOW',
            risk_score: 18,
            reasons: ['Geometry overlap is distant'],
          },
          recommendation: {
            recommendation_id: 'rec-2',
            primary_action_type: 'MONITOR',
          },
        },
      ],
    }));

    await waitFor(() => {
      expect(screen.getByText('Current contexts (2)')).toBeInTheDocument();
    });

    expect(
      screen.getByRole('tab', { name: /sigmet-abc/i }),
    ).toHaveAttribute('aria-selected', 'true');
    expect(screen.getAllByText('Evaluate diversion').length).toBeGreaterThan(0);
    expect(screen.queryByText('Selected context')).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('tab', { name: /sigmet-xyz/i }));

    expect(
      screen.getByRole('tab', { name: /sigmet-xyz/i }),
    ).toHaveAttribute('aria-selected', 'true');
    expect(screen.getByText('Monitor')).toBeInTheDocument();
    expect(screen.getByText('Score 18')).toBeInTheDocument();
    expect(screen.getByText('Geometry overlap is distant')).toBeInTheDocument();
  });
});
