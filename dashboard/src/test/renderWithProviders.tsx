import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, type RenderResult } from '@testing-library/react';
import type { ReactElement, ReactNode } from 'react';
import type { Mock } from 'vitest';

import { ApiProvider } from '@/api/ApiProvider';
import type { OperationalApiClient } from '@/api/operationalApi';

/**
 * Test harness that mounts a component with the query and API providers.
 *
 * Retries and polling are disabled so a failing query settles immediately and
 * tests do not depend on timers.
 */
export function createTestQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: {
        retry: false,
        refetchInterval: false,
        refetchOnWindowFocus: false,
        gcTime: Number.POSITIVE_INFINITY,
      },
    },
  });
}

/**
 * Only the methods a test exercises need to be provided.
 * Vitest 4 mocks are both callable and constructable, and that union is not
 * assignable to a plain client method.
 */
type Procedure = (...args: any[]) => any;
type Constructable = new (...args: any[]) => any;

export type StubApiClient = {
  [K in keyof OperationalApiClient]?:
    | OperationalApiClient[K]
    | Mock<Procedure | Constructable>
    | Procedure;
};

export interface RenderOptions {
  client?: StubApiClient;
  queryClient?: QueryClient;
}

export function renderWithProviders(
  ui: ReactElement,
  options: RenderOptions = {},
): RenderResult {
  const queryClient = options.queryClient ?? createTestQueryClient();
  const client = (options.client ?? {}) as OperationalApiClient;

  function Wrapper({ children }: { children: ReactNode }) {
    return (
      <QueryClientProvider client={queryClient}>
        <ApiProvider client={client}>{children}</ApiProvider>
      </QueryClientProvider>
    );
  }

  return render(ui, { wrapper: Wrapper });
}
