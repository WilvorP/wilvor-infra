import { render, screen } from '@testing-library/react';
import type { ComponentProps } from 'react';
import { MemoryRouter } from 'react-router-dom';
import { describe, expect, it, vi } from 'vitest';

import type { ActiveAlert } from '@/types/api';

import { SelectedAlertStrip } from './SelectedAlertStrip';

const ITEM: ActiveAlert = {
  alert_id: 'alert-a2',
  fingerprint: 'fp-a2',
  alert_type: 'WEATHER_HAZARD_RISK',
  aircraft_id: 'aa0001',
  hazard_id: 'sigmet-b',
  risk_id: 'risk-a2',
  recommendation_id: 'rec-a2',
  alert_state: 'UPDATED',
  state_reason: 'Supporting recommendation changed materially.',
  message:
    'Aircraft aa0001 has HIGH weather-hazard risk. Advisory action: EVALUATE_DIVERSION.',
  risk_level: 'HIGH',
  risk_score: 80,
  primary_action_type: 'EVALUATE_DIVERSION',
  preferred_airport_id: 'KDEN',
  notification_count: 2,
  last_notified_at_utc: '2026-09-06T02:40:00Z',
  created_at_utc: '2026-09-06T02:00:00Z',
  updated_at_utc: '2026-09-06T02:40:00Z',
  valid_until_utc: '2026-09-06T06:00:00Z',
};

function renderStrip(
  props: Partial<ComponentProps<typeof SelectedAlertStrip>> = {},
) {
  return render(
    <MemoryRouter>
      <SelectedAlertStrip
        alertId="alert-a2"
        item={ITEM}
        callsign="UAL9"
        presence="current"
        onClear={vi.fn()}
        {...props}
      />
    </MemoryRouter>,
  );
}

describe('SelectedAlertStrip', () => {
  it('renders the stored /alerts/active fields for the selected alert_id', () => {
    renderStrip();

    expect(screen.getByRole('region', { name: 'Selected alert' })).toBeInTheDocument();
    expect(screen.getByText('Alert')).toBeInTheDocument();
    expect(screen.getByText('Supporting Risk')).toBeInTheDocument();
    expect(screen.getByText('Recommendation')).toBeInTheDocument();
    expect(screen.getByText('Lifecycle')).toBeInTheDocument();
    expect(screen.getByText('Linked Operational Context')).toBeInTheDocument();
    expect(screen.getAllByText('Updated').length).toBeGreaterThan(0);
    expect(screen.getByText('alert-a2')).toBeInTheDocument();
    expect(screen.getByText('fp-a2')).toBeInTheDocument();
    expect(screen.getByText('Weather hazard risk')).toBeInTheDocument();
    expect(
      screen.getByText('Supporting recommendation changed materially.'),
    ).toBeInTheDocument();
    expect(
      screen.getByText(
        'Aircraft aa0001 has HIGH weather-hazard risk. Advisory action: EVALUATE_DIVERSION.',
      ),
    ).toBeInTheDocument();
    expect(screen.getAllByText('UAL9').length).toBeGreaterThan(0);
    expect(screen.getByText('AA0001')).toBeInTheDocument();
    expect(screen.getByText('sigmet-b')).toBeInTheDocument();
    expect(screen.getByText('risk-a2')).toBeInTheDocument();
    expect(screen.getByText('80/100')).toBeInTheDocument();
    expect(screen.getByText('rec-a2')).toBeInTheDocument();
    expect(screen.getByText('Evaluate diversion')).toBeInTheDocument();
    expect(screen.getByText('KDEN')).toBeInTheDocument();
    expect(screen.getByText('2')).toBeInTheDocument();
    expect(screen.getByText('2026-09-06 02:00:00Z')).toBeInTheDocument();
    expect(
      screen.getByRole('link', { name: 'Open Aircraft Investigation' }),
    ).toHaveAttribute(
      'href',
      '/aircraft/aa0001?hazardId=sigmet-b&riskId=risk-a2&recommendationId=rec-a2&alertId=alert-a2&fingerprint=fp-a2&source=alert',
    );
    expect(
      screen.getByRole('link', { name: 'Open Aircraft Investigation' }),
    ).not.toHaveAttribute('href', expect.stringContaining('encounterId='));
  });

  it('uses — for missing stored fields and does not fabricate a message', () => {
    renderStrip({
      alertId: 'alert-a1',
      item: {
        alert_id: 'alert-a1',
        aircraft_id: 'aa0001',
        alert_state: 'NEW',
        risk_level: 'LOW',
        risk_score: 22,
      },
      callsign: null,
    });

    expect(screen.getByText('New')).toBeInTheDocument();
    expect(screen.getByText('Low')).toBeInTheDocument();
    expect(screen.getAllByText('—').length).toBeGreaterThan(3);
    expect(screen.queryByText(/requires immediate/i)).not.toBeInTheDocument();
    expect(screen.queryByText('KDEN')).not.toBeInTheDocument();
  });

  it('shows the exact matched currentContext instead of another context on the same aircraft', () => {
    renderStrip({
      detail: {
        aircraft: { aircraft_id: 'aa0001' },
        projection: { projection_id: 'proj-1' },
        projectionPoints: [
          { projection_id: 'proj-1', latitude: 37.6, longitude: -122.3 },
        ],
        currentContexts: [
          {
            encounter: {
              encounter_id: 'enc-other',
              hazard_id: 'sigmet-a',
            },
            alert: { alert_id: 'alert-other', fingerprint: 'fp-other' },
            risk: { risk_id: 'risk-other' },
            recommendation: { recommendation_id: 'rec-other' },
          },
          {
            encounter: {
              encounter_id: 'enc-a2',
              hazard_id: 'sigmet-b',
            },
            alert: { alert_id: 'alert-a2', fingerprint: 'fp-a2' },
            risk: { risk_id: 'risk-a2' },
            recommendation: { recommendation_id: 'rec-a2' },
          },
        ],
      },
    });

    expect(
      screen.getByText(
        'Exact currentContext match. Selected projection shown on the map.',
      ),
    ).toBeInTheDocument();
    expect(screen.getByText('enc-a2')).toBeInTheDocument();
    expect(screen.getByText('proj-1')).toBeInTheDocument();
    expect(screen.queryByText('enc-other')).not.toBeInTheDocument();
    expect(screen.queryByText('alert-other')).not.toBeInTheDocument();
    expect(screen.queryByText('risk-other')).not.toBeInTheDocument();
    expect(
      screen.getByRole('link', { name: 'Open Aircraft Investigation' }),
    ).toHaveAttribute('href', expect.stringContaining('encounterId=enc-a2'));
  });

  it('does not independently choose a latest context when no exact match exists', () => {
    renderStrip({
      detail: {
        aircraft: { aircraft_id: 'aa0001' },
        projection: { projection_id: 'proj-latest' },
        projectionPoints: [
          { projection_id: 'proj-latest', latitude: 1, longitude: 2 },
        ],
        currentContexts: [
          {
            encounter: { encounter_id: 'enc-latest' },
            alert: { alert_id: 'alert-other' },
            risk: { risk_id: 'risk-other' },
            recommendation: { recommendation_id: 'rec-other' },
          },
        ],
      },
    });

    expect(
      screen.getByText('No exact currentContext match. Unavailable'),
    ).toBeInTheDocument();
    expect(screen.queryByText('enc-latest')).not.toBeInTheDocument();
    expect(screen.queryByText('proj-latest')).not.toBeInTheDocument();
    expect(screen.queryByText('Selected projection shown on the map.')).not.toBeInTheDocument();
  });

  it('keeps a resolved alert selected instead of substituting another', () => {
    renderStrip({
      alertId: 'alert-gone',
      item: null,
      callsign: null,
      presence: 'resolved',
    });

    expect(
      screen.getByText('This alert is no longer current.'),
    ).toBeInTheDocument();
    expect(screen.getAllByText('Unavailable').length).toBeGreaterThan(0);
    expect(
      screen.queryByRole('link', { name: 'Open Aircraft Investigation' }),
    ).not.toBeInTheDocument();
  });
});
