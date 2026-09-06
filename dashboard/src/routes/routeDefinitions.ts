/**
 * Information architecture for the operations console.
 *
 * All seven workflows are registered so navigation reflects the real product
 * shape, but only those marked `implemented` render live data. The rest render
 * an explicit not-yet-built placeholder rather than mock content, which would
 * be indistinguishable from a real but empty operational picture.
 */
export interface RouteDefinition {
  readonly path: string;
  readonly navLabel: string;
  readonly title: string;
  readonly description: string;
  readonly implemented: boolean;
}

export const ROUTES: readonly RouteDefinition[] = [
  {
    path: '/',
    navLabel: 'Overview',
    title: 'Operations Overview',
    description:
      'Network-wide operational posture: active aircraft, hazards, encounters, recommendations and alerts.',
    implemented: true,
  },
  {
    path: '/aircraft',
    navLabel: 'Aircraft',
    title: 'Aircraft Investigation',
    description:
      'Per-aircraft current state, projected trajectory, hazard encounters, risk evaluation and advisory recommendation.',
    implemented: false,
  },
  {
    path: '/airports',
    navLabel: 'Airports',
    title: 'Airport Intelligence',
    description:
      'Airport operational status, current METAR, TAF forecast periods and diversion assessments.',
    implemented: false,
  },
  {
    path: '/encounters',
    navLabel: 'Encounters',
    title: 'Active Encounters',
    description:
      'Aircraft/hazard encounters currently detected or monitored, with their latest risk evaluation.',
    implemented: false,
  },
  {
    path: '/recommendations',
    navLabel: 'Recommendations',
    title: 'Recommendations',
    description:
      'Active advisory recommendations with candidate airports and supporting rationale.',
    implemented: false,
  },
  {
    path: '/alerts',
    navLabel: 'Alerts',
    title: 'Active Alerts',
    description:
      'Active alert lifecycle feed across new, escalated, updated and monitored states.',
    implemented: false,
  },
  {
    path: '/health',
    navLabel: 'System Health',
    title: 'Data & System Health',
    description:
      'Source freshness, pipeline health, Lambda capacity and active CloudWatch alarms.',
    implemented: false,
  },
] as const;
