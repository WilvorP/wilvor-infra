import { QueryClientProvider } from '@tanstack/react-query';
import { useMemo } from 'react';
import { BrowserRouter } from 'react-router-dom';

import { AiProvider } from '@/api/AiProvider';
import { ApiProvider } from '@/api/ApiProvider';
import {
  createDisabledCopilotQueryClient,
  createLocalDevCopilotQueryClient,
  type CopilotQueryClient,
} from '@/api/aiClient';
import { createOperationalApiClient } from '@/api/operationalApi';
import { createQueryClient } from '@/api/queryClient';
import { ConfigurationError } from '@/components/ConfigurationError';
import { configResult, type ConfigResult } from '@/config/env';
import { AppRoutes } from '@/routes/AppRoutes';

export interface AppProps {
  /** Overridable so tests can mount the app with an explicit configuration. */
  config?: ConfigResult;
  /**
   * Copilot client. Defaults to the local disabled client, which never POSTs
   * `/ai/query`. Tests inject fixture data through this prop.
   */
  aiClient?: CopilotQueryClient;
}

export function App({ config = configResult, aiClient }: AppProps) {
  const queryClient = useMemo(() => createQueryClient(), []);

  const client = useMemo(
    () =>
      config.config === null
        ? null
        : createOperationalApiClient({
            baseUrl: config.config.apiBaseUrl,
            timeoutMs: config.config.apiTimeoutMs,
          }),
    [config.config],
  );

  const copilotClient = useMemo(() => {
    if (aiClient) {
      return aiClient;
    }

    if (config.config?.localDevCopilotEnabled) {
      return createLocalDevCopilotQueryClient();
    }

    return createDisabledCopilotQueryClient();
  }, [aiClient, config.config]);

  if (config.config === null || client === null) {
    return <ConfigurationError errors={config.errors} />;
  }

  return (
    <QueryClientProvider client={queryClient}>
      <ApiProvider client={client}>
        <AiProvider client={copilotClient}>
          <BrowserRouter>
            <AppRoutes mapStyleUrl={config.config.mapStyleUrl} />
          </BrowserRouter>
        </AiProvider>
      </ApiProvider>
    </QueryClientProvider>
  );
}
