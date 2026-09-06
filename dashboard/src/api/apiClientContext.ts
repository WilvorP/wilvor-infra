import { createContext, useContext } from 'react';

import type { OperationalApiClient } from './operationalApi';

/**
 * Context carrying the operational API client.
 *
 * Kept separate from the provider component so that module exports only
 * components (required for React Fast Refresh) or only non-components.
 */
export const ApiClientContext = createContext<OperationalApiClient | null>(
  null,
);

export function useApiClient(): OperationalApiClient {
  const client = useContext(ApiClientContext);

  if (client === null) {
    throw new Error('useApiClient must be used within an ApiProvider.');
  }

  return client;
}
