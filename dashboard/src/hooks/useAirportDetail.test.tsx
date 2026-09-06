import { QueryClientProvider } from '@tanstack/react-query';
import { renderHook, waitFor } from '@testing-library/react';
import type { ReactNode } from 'react';
import { describe, expect, it, vi } from 'vitest';

import { ApiProvider } from '@/api/ApiProvider';
import type { OperationalApiClient } from '@/api/operationalApi';
import { REFRESH } from '@/config/refresh';
import { createTestQueryClient } from '@/test/renderWithProviders';
import type { AirportDetailResponse } from '@/types/api';

import {
  retainAirportDetailPlaceholder,
  useAirportDetail,
} from './useOperationalQueries';

const EMPTY: AirportDetailResponse = {
  airport: { airport_id: 'KDEN' },
  metar: null,
  taf: null,
  tafForecastPeriods: [],
  recentAssessments: [],
};

function wrapperFor(getAirport: OperationalApiClient['getAirport']) {
  const queryClient = createTestQueryClient();
  const client = { getAirport } as OperationalApiClient;

  return function Wrapper({ children }: { children: ReactNode }) {
    return (
      <QueryClientProvider client={queryClient}>
        <ApiProvider client={client}>{children}</ApiProvider>
      </QueryClientProvider>
    );
  };
}

describe('useAirportDetail', () => {
  it('does not request detail when no airport is selected', () => {
    const getAirport = vi.fn();

    const { result } = renderHook(() => useAirportDetail(null), {
      wrapper: wrapperFor(getAirport),
    });

    expect(getAirport).not.toHaveBeenCalled();
    expect(result.current.fetchStatus).toBe('idle');
  });

  it('requests the selected airport and does not reuse another airport as placeholder', async () => {
    const getAirport = vi.fn(async (id: string) => ({
      ...EMPTY,
      airport: { airport_id: id },
    }));

    const { result } = renderHook(() => useAirportDetail('kden'), {
      wrapper: wrapperFor(getAirport),
    });

    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true);
    });

    expect(getAirport).toHaveBeenCalledWith(
      'KDEN',
      expect.objectContaining({ signal: expect.any(AbortSignal) }),
    );
    expect(REFRESH.airportDetail.refetchIntervalMs).toBe(20_000);
    expect(
      retainAirportDetailPlaceholder('KDEN', EMPTY, {
        queryKey: ['wilvor', 'airports', 'detail', 'KSFO'],
      }),
    ).toBeUndefined();
  });
});
