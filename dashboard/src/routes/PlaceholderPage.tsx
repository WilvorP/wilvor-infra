import { Panel } from '@/components/Panel';
import { Notice } from '@/components/QueryState';
import type { RouteDefinition } from './routeDefinitions';

import styles from './PlaceholderPage.module.css';

export interface PlaceholderPageProps {
  route: RouteDefinition;
  /** Operational API routes this workflow will consume once implemented. */
  plannedEndpoints: readonly string[];
}

/**
 * Placeholder for a workflow that is registered but not yet built.
 *
 * Renders no data at all. Showing sample figures here would be
 * indistinguishable from a real but quiet operational picture, which is not an
 * acceptable failure mode on a decision-support surface.
 */
export function PlaceholderPage({
  route,
  plannedEndpoints,
}: PlaceholderPageProps) {
  return (
    <div className={styles.page}>
      <Panel title={route.title}>
        <div className={styles.content}>
          <Notice>
            This workflow is not implemented in the current milestone. No data
            is shown here, and none is simulated.
          </Notice>

          <p className={styles.description}>{route.description}</p>

          <div>
            <h3 className={styles.subheading}>Planned API dependencies</h3>
            <ul className={styles.endpoints}>
              {plannedEndpoints.map((endpoint) => (
                <li key={endpoint} className="wv-numeric">
                  GET {endpoint}
                </li>
              ))}
            </ul>
          </div>
        </div>
      </Panel>
    </div>
  );
}
