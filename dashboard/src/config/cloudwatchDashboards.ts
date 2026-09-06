/**
 * Catalog of every Wilvor CloudWatch dashboard defined in Terraform.
 *
 * Names match `aws_cloudwatch_dashboard.dashboard_name` for
 * `name_prefix = wilvor-dev`. Descriptions come from the widget titles in
 * each module's monitoring.tf. There is no embed/share URL: CloudWatch
 * dashboard sharing is not configured, and this catalog never invents one.
 */

export const CLOUDWATCH_REGION = 'us-west-1';

export const CLOUDWATCH_NAME_PREFIX = 'wilvor-dev';

export const CLOUDWATCH_CATEGORIES = [
  'all',
  'aircraft',
  'weather',
  'airports',
  'decision',
  'supporting',
] as const;

export type CloudWatchCategory = (typeof CLOUDWATCH_CATEGORIES)[number];

export interface CloudWatchDashboardEntry {
  readonly id: string;
  readonly name: string;
  readonly label: string;
  readonly category: Exclude<CloudWatchCategory, 'all'>;
  readonly description: string;
  /** Always null until a secure share exists. Never a public token. */
  readonly embedUrl: string | null;
}

export const CLOUDWATCH_CATEGORY_LABELS: Record<CloudWatchCategory, string> = {
  all: 'All',
  aircraft: 'Aircraft',
  weather: 'Weather',
  airports: 'Airports',
  decision: 'Decision Intelligence',
  supporting: 'Supporting Pipelines',
};

export const CLOUDWATCH_DASHBOARDS: readonly CloudWatchDashboardEntry[] = [
  {
    id: 'aircraft-pipeline',
    name: `${CLOUDWATCH_NAME_PREFIX}-aircraft-pipeline`,
    label: 'Aircraft Pipeline',
    category: 'aircraft',
    description:
      'OpenSky poller, Kinesis raw/clean streams, Lambda errors and AircraftCurrentState DynamoDB writes.',
    embedUrl: null,
  },
  {
    id: 'aircraft-hazard-encounter',
    name: `${CLOUDWATCH_NAME_PREFIX}-aircraft-hazard-encounter`,
    label: 'Aircraft-Hazard Encounter',
    category: 'aircraft',
    description:
      'AircraftHazardEncounter Lambda invocations, candidate counts and DynamoDB consumed capacity.',
    embedUrl: null,
  },
  {
    id: 'projection-pipeline',
    name: `${CLOUDWATCH_NAME_PREFIX}-projection-pipeline`,
    label: 'Projection Pipeline',
    category: 'aircraft',
    description:
      'Projection processor eligibility, impact matching, and projection / cells / points DynamoDB capacity and throttles.',
    embedUrl: null,
  },
  {
    id: 'sigmet-pipeline',
    name: `${CLOUDWATCH_NAME_PREFIX}-sigmet-pipeline`,
    label: 'SIGMET Pipeline',
    category: 'weather',
    description:
      'SIGMET poller and processor, raw Kinesis, Lambda health, and ActiveHazards / HazardCells / coordinates / impact-cell DynamoDB.',
    embedUrl: null,
  },
  {
    id: 'metar-pipeline',
    name: `${CLOUDWATCH_NAME_PREFIX}-metar-pipeline`,
    label: 'METAR Pipeline',
    category: 'weather',
    description:
      'METAR poller and processor, raw Kinesis, Lambda health, MetarLatest DynamoDB and EventBridge publish failures.',
    embedUrl: null,
  },
  {
    id: 'taf-pipeline',
    name: `${CLOUDWATCH_NAME_PREFIX}-taf-pipeline`,
    label: 'TAF Pipeline',
    category: 'weather',
    description:
      'TAF poller and processor, raw Kinesis, Lambda health, and TafLatest / TafForecastPeriods DynamoDB.',
    embedUrl: null,
  },
  {
    id: 'weather-events',
    name: `${CLOUDWATCH_NAME_PREFIX}-weather-events`,
    label: 'Weather Events',
    category: 'weather',
    description:
      'weather.changed EventBridge rule invocations and events by product type.',
    embedUrl: null,
  },
  {
    id: 'hazard-station-candidates',
    name: `${CLOUDWATCH_NAME_PREFIX}-hazard-station-candidates`,
    label: 'Hazard Station Candidates',
    category: 'weather',
    description:
      'Hazard-station candidate processor, DynamoDB capacity and EventBridge delivery.',
    embedUrl: null,
  },
  {
    id: 'airport-status',
    name: `${CLOUDWATCH_NAME_PREFIX}-airport-status`,
    label: 'Airport Status',
    category: 'airports',
    description:
      'Airport status materializer, AirportStatus DynamoDB and weather → AirportStatus EventBridge.',
    embedUrl: null,
  },
  {
    id: 'airport-assessment',
    name: `${CLOUDWATCH_NAME_PREFIX}-airport-assessment`,
    label: 'Airport Assessment',
    category: 'airports',
    description:
      'Airport assessment processor duration, AirportAssessment DynamoDB and risk → assessment EventBridge.',
    embedUrl: null,
  },
  {
    id: 'risk-pipeline',
    name: `${CLOUDWATCH_NAME_PREFIX}-risk-pipeline`,
    label: 'Risk Pipeline',
    category: 'decision',
    description:
      'Risk processor Lambda, risk evaluations, RiskResults DynamoDB and processor duration.',
    embedUrl: null,
  },
  {
    id: 'recommendations',
    name: `${CLOUDWATCH_NAME_PREFIX}-recommendations`,
    label: 'Recommendations',
    category: 'decision',
    description:
      'Recommendation processor invocations/errors/throttles and Recommendations DynamoDB capacity.',
    embedUrl: null,
  },
  {
    id: 'active-alerts',
    name: `${CLOUDWATCH_NAME_PREFIX}-active-alerts`,
    label: 'Active Alerts',
    category: 'decision',
    description:
      'Alert lifecycle processor invocations/errors/throttles and ActiveAlerts DynamoDB capacity.',
    embedUrl: null,
  },
  {
    id: 'runway-metadata',
    name: `${CLOUDWATCH_NAME_PREFIX}-runway-metadata`,
    label: 'Runway Metadata',
    category: 'supporting',
    description:
      'Runway load status, record results, loader Lambda and runway DynamoDB.',
    embedUrl: null,
  },
];

export const DEFAULT_CLOUDWATCH_DASHBOARD_ID = 'aircraft-pipeline';

export const CLOUDWATCH_TIME_RANGES = [
  '1h',
  '3h',
  '6h',
  '12h',
  '24h',
] as const;

export type CloudWatchTimeRange = (typeof CLOUDWATCH_TIME_RANGES)[number];

export const DEFAULT_CLOUDWATCH_TIME_RANGE: CloudWatchTimeRange = '3h';

export function resolveTimeRange(
  value: string | null,
): CloudWatchTimeRange {
  return CLOUDWATCH_TIME_RANGES.find((range) => range === value) ??
    DEFAULT_CLOUDWATCH_TIME_RANGE;
}

/**
 * AWS console deep-link for a CloudWatch dashboard in this account/region.
 *
 * Pattern: regional CloudWatch home + `#dashboards:name=…`. This is a
 * console navigation URL. It does not grant access.
 */
export function cloudWatchConsoleUrl(dashboardName: string): string {
  const region = encodeURIComponent(CLOUDWATCH_REGION);
  const name = encodeURIComponent(dashboardName);

  return `https://${CLOUDWATCH_REGION}.console.aws.amazon.com/cloudwatch/home?region=${region}#dashboards:name=${name}`;
}

export function dashboardById(
  id: string | null,
): CloudWatchDashboardEntry | null {
  if (id === null || id.length === 0) {
    return null;
  }

  return CLOUDWATCH_DASHBOARDS.find((entry) => entry.id === id) ?? null;
}

export function resolveDashboardSelection(
  id: string | null,
): CloudWatchDashboardEntry {
  return dashboardById(id) ?? CLOUDWATCH_DASHBOARDS[0]!;
}

export function filterDashboards(
  items: readonly CloudWatchDashboardEntry[],
  category: CloudWatchCategory,
  search: string,
): CloudWatchDashboardEntry[] {
  const needle = search.trim().toLowerCase();

  return items.filter((entry) => {
    if (category !== 'all' && entry.category !== category) {
      return false;
    }

    if (needle.length === 0) {
      return true;
    }

    return (
      entry.id.toLowerCase().includes(needle) ||
      entry.name.toLowerCase().includes(needle) ||
      entry.label.toLowerCase().includes(needle) ||
      entry.category.toLowerCase().includes(needle) ||
      CLOUDWATCH_CATEGORY_LABELS[entry.category].toLowerCase().includes(needle) ||
      entry.description.toLowerCase().includes(needle)
    );
  });
}
