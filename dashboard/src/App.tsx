import { QueryClientProvider } from '@tanstack/react-query';
import { useMemo } from 'react';
import { BrowserRouter } from 'react-router-dom';

import { ApiProvider } from '@/api/ApiProvider';
import { createOperationalApiClient } from '@/api/operationalApi';
import { createQueryClient } from '@/api/queryClient';
import { ConfigurationError } from '@/components/ConfigurationError';
import { configResult, type ConfigResult } from '@/config/env';
import { AppRoutes } from '@/routes/AppRoutes';

export interface AppProps {
  /** Overridable so tests can mount the app with an explicit configuration. */
  config?: ConfigResult;
}

export function App({ config = configResult }: AppProps) {
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

  if (config.config === null || client === null) {
    return <ConfigurationError errors={config.errors} />;
  }

  return (
    <QueryClientProvider client={queryClient}>
      <ApiProvider client={client}>
        <BrowserRouter>
          <AppRoutes mapStyleUrl={config.config.mapStyleUrl} />
        </BrowserRouter>
      </ApiProvider>
    </QueryClientProvider>
  );
}
