import { QueryClientProvider } from '@tanstack/react-query';
import { renderHook, waitFor } from '@testing-library/react';
import type { ReactNode } from 'react';
import { describe, expect, it, vi } from 'vitest';

import { ApiProvider } from '@/api/ApiProvider';
import type { OperationalApiClient } from '@/api/operationalApi';
import { REFRESH } from '@/config/refresh';
import { createTestQueryClient } from '@/test/renderWithProviders';
import type { AircraftDetailResponse } from '@/types/api';

import {
  retainAircraftDetailPlaceholder,
  useAircraftDetail,
} from './useOperationalQueries';

const EMPTY_DETAIL: AircraftDetailResponse = {
  aircraft: { aircraft_id: 'aaa111' },
  projection: null,
  projectionPoints: [],
  recentEncounters: [],
  recentRisks: [],
  recentRecommendations: [],
  recentAlerts: [],
};

function wrapperFor(getAircraft: OperationalApiClient['getAircraft']) {
  const queryClient = createTestQueryClient();
  const client = { getAircraft } as OperationalApiClient;

  return function Wrapper({ children }: { children: ReactNode }) {
    return (
      <QueryClientProvider client={queryClient}>
        <ApiProvider client={client}>{children}</ApiProvider>
      </QueryClientProvider>
    );
  };
}

describe('useAircraftDetail', () => {
  it('does not request detail when no aircraft is selected', () => {
    const getAircraft = vi.fn();

    const { result } = renderHook(() => useAircraftDetail(null), {
      wrapper: wrapperFor(getAircraft),
    });

    expect(getAircraft).not.toHaveBeenCalled();
    expect(result.current.fetchStatus).toBe('idle');
    expect(result.current.isFetching).toBe(false);
  });

  it('requests the selected aircraft and refreshes on the investigation cadence', async () => {
    const getAircraft = vi.fn(async (id: string) => ({
      ...EMPTY_DETAIL,
      aircraft: { aircraft_id: id },
    }));

    const { result } = renderHook(() => useAircraftDetail('aaa111'), {
      wrapper: wrapperFor(getAircraft),
    });

    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true);
    });

    expect(getAircraft).toHaveBeenCalledWith(
      'aaa111',
      expect.objectContaining({ signal: expect.any(AbortSignal) }),
    );
    expect(result.current.data?.aircraft?.aircraft_id).toBe('aaa111');
    expect(REFRESH.aircraftDetail.refetchIntervalMs).toBe(12_000);
  });

  it('cancels the in-flight request when the selection changes', async () => {
    let firstSignal: AbortSignal | undefined;

    const getAircraft = vi.fn((id: string, options?: { signal?: AbortSignal }) => {
      if (id === 'aaa111') {
        firstSignal = options?.signal;
        return new Promise<AircraftDetailResponse>(() => undefined);
      }

      return Promise.resolve({
        ...EMPTY_DETAIL,
        aircraft: { aircraft_id: id },
      });
    });

    const { rerender, result } = renderHook(
      ({ aircraftId }: { aircraftId: string | null }) =>
        useAircraftDetail(aircraftId),
      {
        wrapper: wrapperFor(getAircraft),
        initialProps: { aircraftId: 'aaa111' },
      },
    );

    await waitFor(() => {
      expect(getAircraft).toHaveBeenCalledWith(
        'aaa111',
        expect.objectContaining({ signal: expect.any(AbortSignal) }),
      );
    });

    rerender({ aircraftId: 'bbb222' });

    await waitFor(() => {
      expect(result.current.data?.aircraft?.aircraft_id).toBe('bbb222');
    });

    expect(firstSignal?.aborted).toBe(true);
  });
});

describe('retainAircraftDetailPlaceholder', () => {
  it('keeps previous detail only for the same aircraft id', () => {
    const previous = {
      ...EMPTY_DETAIL,
      recentRisks: [{ risk_id: 'keep' }],
    };

    expect(
      retainAircraftDetailPlaceholder('aaa111', previous, {
        queryKey: ['wilvor', 'aircraft', 'detail', 'aaa111'],
      }),
    ).toBe(previous);

    expect(
      retainAircraftDetailPlaceholder('bbb222', previous, {
        queryKey: ['wilvor', 'aircraft', 'detail', 'aaa111'],
      }),
    ).toBeUndefined();
  });
});
