import { screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { ApiError } from '@/api/errors';
import { renderWithProviders } from '@/test/renderWithProviders';
import type { FreshnessResponse } from '@/types/api';

import { FreshnessStrip } from './FreshnessStrip';

const RESPONSE: FreshnessResponse = {
  generatedAt: '2026-09-03T12:00:00Z',
  mode: 'SOURCE_TABLE_LATEST_RECORD',
  sources: {
    opensky: {
      latestAt: '2026-09-03T11:59:45Z',
      ageSeconds: 15,
      status: 'FRESH',
    },
    sigmet: {
      latestAt: '2026-09-03T09:00:00Z',
      ageSeconds: 10_800,
      status: 'AVAILABLE',
      note: 'SIGMET table age reflects the newest materialized hazard product.',
    },
    metar: {
      latestAt: '2026-09-03T11:20:00Z',
      ageSeconds: 2_400,
      status: 'STALE',
    },
    taf: { latestAt: null, ageSeconds: null, status: 'UNAVAILABLE' },
  },
};

describe('FreshnessStrip', () => {
  it('shows a pending message before the first response', () => {
    renderWithProviders(<FreshnessStrip />, {
      client: { freshness: () => new Promise(() => {}) },
    });

    expect(
      screen.getByText(/Checking source freshness/i),
    ).toBeInTheDocument();
  });

  it('labels every source with text, not colour alone', async () => {
    renderWithProviders(<FreshnessStrip />, {
      client: { freshness: vi.fn(async () => RESPONSE) },
    });

    await waitFor(() => {
      expect(screen.getByText('Aircraft')).toBeInTheDocument();
    });

    for (const label of ['Aircraft', 'SIGMET', 'METAR', 'TAF']) {
      expect(screen.getByText(label)).toBeInTheDocument();
    }

    // Status words carry the meaning; the tone colour only reinforces it.
    expect(screen.getByText('Fresh')).toBeInTheDocument();
    expect(screen.getByText('Available')).toBeInTheDocument();
    expect(screen.getByText('Stale')).toBeInTheDocument();
    expect(screen.getByText('Unavailable')).toBeInTheDocument();
  });

  it('renders source ages and the backend generation time', async () => {
    renderWithProviders(<FreshnessStrip />, {
      client: { freshness: vi.fn(async () => RESPONSE) },
    });

    await waitFor(() => {
      expect(screen.getByText('15s')).toBeInTheDocument();
    });

    expect(screen.getByText('3h 00m')).toBeInTheDocument();
    expect(screen.getByText('12:00:00Z')).toBeInTheDocument();
  });

  it('reports an unreachable API instead of rendering a blank strip', async () => {
    renderWithProviders(<FreshnessStrip />, {
      client: {
        freshness: vi.fn(async () => {
          throw new ApiError('boom', { kind: 'network' });
        }),
      },
    });

    await waitFor(() => {
      expect(screen.getByRole('alert')).toBeInTheDocument();
    });

    expect(
      screen.getByText(/Source freshness unavailable/i),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/Cannot reach the operational API/i),
    ).toBeInTheDocument();
  });
});
