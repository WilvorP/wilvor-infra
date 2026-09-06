import { QueryClient } from '@tanstack/react-query';

import { isApiError } from './errors';

const MAX_RETRIES = 2;

/**
 * Shared TanStack Query configuration.
 *
 * Two behaviours matter operationally:
 *
 *   - `refetchIntervalInBackground: false` lets TanStack Query suspend polling
 *     while the tab is hidden. With the operational API Lambda pinned to
 *     `reserved_concurrent_executions = 2` (envs/dev/main.tf), background tabs
 *     polling every 20s would consume capacity for a picture nobody is reading.
 *
 *   - Retries are skipped for non-retryable failures. A 400 from `_parse_limit`
 *     or an invalid `nextToken` will fail identically on every attempt, and a
 *     parse failure means the base URL is wrong.
 */
export function createQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: {
        retry: (failureCount, error) => {
          if (isApiError(error) && !error.isRetryable) {
            return false;
          }

          return failureCount < MAX_RETRIES;
        },
        retryDelay: (attemptIndex) =>
          Math.min(1_000 * 2 ** attemptIndex, 8_000),

        refetchOnWindowFocus: true,
        refetchOnReconnect: true,
        refetchIntervalInBackground: false,

        // Keep the last successful operational picture on screen while a
        // refetch is in flight, so panels do not flash empty every poll.
        // Staleness is communicated explicitly instead.
        placeholderData: <T,>(previous: T) => previous,
      },
    },
  });
}
