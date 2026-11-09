import { useMemo, type ReactNode } from 'react';

import { AiClientContext } from './aiClientContext';
import type { CopilotQueryClient } from './aiClient';

export interface AiProviderProps {
  client: CopilotQueryClient;
  children: ReactNode;
}

/**
 * Supplies the Copilot client to the conversational UI.
 *
 * Injecting the client keeps page tests on fixture data and keeps the default
 * app on the disabled local client. Neither path POSTs `/ai/query`.
 */
export function AiProvider({ client, children }: AiProviderProps) {
  const value = useMemo(() => client, [client]);

  return (
    <AiClientContext.Provider value={value}>{children}</AiClientContext.Provider>
  );
}
