import { Notice } from '@/components/QueryState';
import type { CopilotRouteStanding } from '@/api/aiContracts';
import { ROUTE_DISABLED_NOTICE } from '@/api/aiContracts';

import styles from './CopilotStatusBanner.module.css';

export interface CopilotStatusBannerProps {
  standing: CopilotRouteStanding;
}

/**
 * Standing of the Copilot route.
 *
 * Production AI is not active. The banner is the operator-visible record of
 * that fact so a conversational surface cannot be mistaken for a live AI
 * service.
 */
export function CopilotStatusBanner({ standing }: CopilotStatusBannerProps) {
  const transportLabel =
    standing.transport === 'INJECTED_TEST'
      ? 'Injected test client. Displayed evidence is fixture data, not a live AWS read.'
      : ROUTE_DISABLED_NOTICE;

  return (
    <div className={styles.banner} data-testid="copilot-status-banner">
      <Notice tone="warning">{transportLabel}</Notice>
      <dl className={styles.meta}>
        <div>
          <dt>Route</dt>
          <dd className="wv-numeric">{standing.routeKey}</dd>
        </div>
        <div>
          <dt>Route enabled</dt>
          <dd>No</dd>
        </div>
        <div>
          <dt>Production AI</dt>
          <dd>Not active</dd>
        </div>
        <div>
          <dt>Backend owner</dt>
          <dd>Integration owner</dd>
        </div>
      </dl>
    </div>
  );
}
