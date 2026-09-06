import { useMemo, type ReactNode } from 'react';

import { ApiClientContext } from './apiClientContext';
import type { OperationalApiClient } from './operationalApi';

export interface ApiProviderProps {
  client: OperationalApiClient;
  children: ReactNode;
}

/**
 * Supplies the operational API client to feature hooks.
 *
 * Injecting the client rather than importing a module singleton keeps hooks
 * testable against a stub without patching global `fetch`.
 */
export function ApiProvider({ client, children }: ApiProviderProps) {
  const value = useMemo(() => client, [client]);

  return (
    <ApiClientContext.Provider value={value}>
      {children}
    </ApiClientContext.Provider>
  );
}
