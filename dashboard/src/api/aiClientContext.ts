import { createContext, useContext } from 'react';

import type { CopilotQueryClient } from './aiClient';

/**
 * Context carrying the Copilot query client.
 *
 * Kept separate from the provider component so Fast Refresh can treat the
 * provider file as component-only.
 */
export const AiClientContext = createContext<CopilotQueryClient | null>(null);

export function useAiClient(): CopilotQueryClient {
  const client = useContext(AiClientContext);

  if (client === null) {
    throw new Error('useAiClient must be used within an AiProvider.');
  }

  return client;
}
