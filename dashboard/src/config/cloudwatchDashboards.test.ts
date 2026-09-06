import { describe, expect, it } from 'vitest';

import {
  CLOUDWATCH_DASHBOARDS,
  CLOUDWATCH_NAME_PREFIX,
  CLOUDWATCH_REGION,
  DEFAULT_CLOUDWATCH_DASHBOARD_ID,
  cloudWatchConsoleUrl,
  dashboardById,
  filterDashboards,
  resolveDashboardSelection,
  resolveTimeRange,
} from './cloudwatchDashboards';

const EXPECTED_AWS_NAMES = [
  'wilvor-dev-aircraft-pipeline',
  'wilvor-dev-aircraft-hazard-encounter',
  'wilvor-dev-projection-pipeline',
  'wilvor-dev-sigmet-pipeline',
  'wilvor-dev-metar-pipeline',
  'wilvor-dev-taf-pipeline',
  'wilvor-dev-weather-events',
  'wilvor-dev-hazard-station-candidates',
  'wilvor-dev-airport-status',
  'wilvor-dev-airport-assessment',
  'wilvor-dev-risk-pipeline',
  'wilvor-dev-recommendations',
  'wilvor-dev-active-alerts',
  'wilvor-dev-runway-metadata',
] as const;

describe('CloudWatch dashboard catalog', () => {
  it('includes every discovered Wilvor dev dashboard exactly once', () => {
    const names = CLOUDWATCH_DASHBOARDS.map((entry) => entry.name);

    expect(names).toEqual([...EXPECTED_AWS_NAMES]);
    expect(new Set(names).size).toBe(14);
    expect(new Set(CLOUDWATCH_DASHBOARDS.map((entry) => entry.id)).size).toBe(
      14,
    );
  });

  it('uses the Terraform name prefix and us-west-1 console links', () => {
    expect(CLOUDWATCH_NAME_PREFIX).toBe('wilvor-dev');
    expect(CLOUDWATCH_REGION).toBe('us-west-1');
    expect(cloudWatchConsoleUrl('wilvor-dev-metar-pipeline')).toBe(
      'https://us-west-1.console.aws.amazon.com/cloudwatch/home?region=us-west-1#dashboards:name=wilvor-dev-metar-pipeline',
    );
  });

  it('groups dashboards into the System Health navigator categories', () => {
    const byCategory = Object.fromEntries(
      ['aircraft', 'weather', 'airports', 'decision', 'supporting'].map(
        (category) => [
          category,
          CLOUDWATCH_DASHBOARDS.filter((entry) => entry.category === category).map(
            (entry) => entry.id,
          ),
        ],
      ),
    );

    expect(byCategory).toEqual({
      aircraft: [
        'aircraft-pipeline',
        'aircraft-hazard-encounter',
        'projection-pipeline',
      ],
      weather: [
        'sigmet-pipeline',
        'metar-pipeline',
        'taf-pipeline',
        'weather-events',
        'hazard-station-candidates',
      ],
      airports: ['airport-status', 'airport-assessment'],
      decision: ['risk-pipeline', 'recommendations', 'active-alerts'],
      supporting: ['runway-metadata'],
    });
  });

  it('keeps the All filter as the complete catalog', () => {
    expect(filterDashboards(CLOUDWATCH_DASHBOARDS, 'all', '')).toHaveLength(14);
  });

  it('filters by label, AWS name, category and description', () => {
    expect(
      filterDashboards(CLOUDWATCH_DASHBOARDS, 'all', 'taf').map((entry) => entry.id),
    ).toEqual(['taf-pipeline']);
    expect(
      filterDashboards(CLOUDWATCH_DASHBOARDS, 'all', 'risk').map((entry) => entry.id),
    ).toEqual(['airport-assessment', 'risk-pipeline']);
    expect(
      filterDashboards(CLOUDWATCH_DASHBOARDS, 'all', 'risk-pipeline').map(
        (entry) => entry.id,
      ),
    ).toEqual(['risk-pipeline']);
    expect(
      filterDashboards(CLOUDWATCH_DASHBOARDS, 'all', 'airport').map(
        (entry) => entry.id,
      ),
    ).toEqual(['airport-status', 'airport-assessment']);
    expect(
      filterDashboards(CLOUDWATCH_DASHBOARDS, 'all', 'Kinesis').some(
        (entry) => entry.id === 'aircraft-pipeline',
      ),
    ).toBe(true);
    expect(
      filterDashboards(CLOUDWATCH_DASHBOARDS, 'weather', 'pipeline').map(
        (entry) => entry.id,
      ),
    ).toEqual(['sigmet-pipeline', 'metar-pipeline', 'taf-pipeline']);
  });

  it('defaults an unknown time range to 3h', () => {
    expect(resolveTimeRange(null)).toBe('3h');
    expect(resolveTimeRange('week')).toBe('3h');
    expect(resolveTimeRange('24h')).toBe('24h');
  });

  it('falls back safely for an unknown dashboard id', () => {
    expect(dashboardById('not-a-dashboard')).toBeNull();
    expect(resolveDashboardSelection('not-a-dashboard').id).toBe(
      DEFAULT_CLOUDWATCH_DASHBOARD_ID,
    );
    expect(resolveDashboardSelection(null).id).toBe(
      DEFAULT_CLOUDWATCH_DASHBOARD_ID,
    );
  });

  it('does not carry share tokens or AWS credentials', () => {
    const serialized = JSON.stringify(CLOUDWATCH_DASHBOARDS);

    expect(
      CLOUDWATCH_DASHBOARDS.every((entry) => entry.embedUrl === null),
    ).toBe(true);
    expect(serialized).not.toMatch(/AKIA|SECRET|SESSION_TOKEN|aws_access/i);
    expect(cloudWatchConsoleUrl('wilvor-dev-aircraft-pipeline')).not.toMatch(
      /auth|token|share/i,
    );
  });
});
