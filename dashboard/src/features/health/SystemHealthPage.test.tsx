import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter, Route, Routes, useLocation, useNavigate } from 'react-router-dom';
import { describe, expect, it, vi } from 'vitest';

import { ApiError } from '@/api/errors';
import {
  CLOUDWATCH_DASHBOARDS,
  cloudWatchConsoleUrl,
} from '@/config/cloudwatchDashboards';
import { AppRoutes } from '@/routes/AppRoutes';
import { renderWithProviders } from '@/test/renderWithProviders';
import type {
  FreshnessResponse,
  HealthResponse,
  SystemHealthResponse,
} from '@/types/api';

import { SystemHealthPage } from './SystemHealthPage';

const HEALTH: HealthResponse = {
  service: 'wilvor-operational-api',
  status: 'ok',
  requestId: 'req-health-1',
};

const FRESHNESS: FreshnessResponse = {
  generatedAt: '2026-09-06T08:00:00Z',
  mode: 'SOURCE_TABLE_LATEST_RECORD',
  sources: {
    opensky: {
      latestAt: '2026-09-06T07:53:43Z',
      ageSeconds: 377,
      status: 'STALE',
    },
    sigmet: {
      latestAt: '2026-09-06T07:04:18Z',
      ageSeconds: 3342,
      status: 'AVAILABLE',
      note: 'SIGMET table age reflects the newest materialized hazard product.',
    },
    metar: {
      latestAt: '2026-09-06T07:56:25Z',
      ageSeconds: 215,
      status: 'FRESH',
    },
    taf: {
      latestAt: '2026-09-06T07:45:02Z',
      ageSeconds: 898,
      status: 'FRESH',
    },
  },
};

const SYSTEM_HEALTH: SystemHealthResponse = {
  generatedAt: '2026-09-06T08:00:00Z',
  status: 'DEGRADED',
  lambda: {
    account: {
      concurrencyLimit: 1000,
      unreservedConcurrency: 900,
      reservedConcurrency: 100,
    },
    recent: {
      windowMinutes: 5,
      maxConcurrentExecutions: 4,
      concurrencyUtilizationPercent: 0.4,
    },
    operationalApi: {
      functionName: 'wilvor-dev-operational-api',
      throttlesLast5Minutes: 0,
    },
  },
  cloudWatch: {
    activeAlarmCount: 1,
    activeAlarms: [
      {
        alarmName: 'wilvor-dev-metar-errors',
        metricName: 'Errors',
        namespace: 'AWS/Lambda',
        state: 'ALARM',
      },
    ],
  },
  dataFreshness: {
    status: 'DEGRADED',
    problemSources: ['opensky'],
  },
};

function LocationProbe() {
  const location = useLocation();

  return (
    <div data-testid="location">{`${location.pathname}${location.search}`}</div>
  );
}

function HistoryControls() {
  const navigate = useNavigate();

  return (
    <button type="button" onClick={() => navigate(-1)}>
      Go back
    </button>
  );
}

function networkError(): ApiError {
  return new ApiError('boom', { kind: 'network' });
}

const DASHBOARD_VIEW = {
  id: 'aircraft-pipeline',
  name: 'Aircraft Pipeline',
  awsDashboardName: 'wilvor-dev-aircraft-pipeline',
  generatedAt: '2026-09-06T08:00:00Z',
  revision: 'rev-1',
  gridColumns: 24,
  widgets: [
    {
      id: 'widget-0',
      type: 'text',
      x: 0,
      y: 0,
      width: 24,
      height: 2,
      markdown: '# Wilvor Aircraft Pipeline\nOpenSky local poller',
      supported: true,
    },
    {
      id: 'widget-1',
      type: 'metric',
      x: 0,
      y: 2,
      width: 12,
      height: 6,
      title: 'OpenSky Poller - Local Producer',
      supported: true,
    },
  ],
};

function renderPage(
  path = '/system-health',
  extra: {
    health?: ReturnType<typeof vi.fn>;
    freshness?: ReturnType<typeof vi.fn>;
    systemHealth?: ReturnType<typeof vi.fn>;
    getCloudWatchDashboard?: ReturnType<typeof vi.fn>;
    getCloudWatchWidgetImage?: ReturnType<typeof vi.fn>;
    withShell?: boolean;
  } = {},
) {
  const health = extra.health ?? vi.fn(async () => HEALTH);
  const freshness = extra.freshness ?? vi.fn(async () => FRESHNESS);
  const systemHealth = extra.systemHealth ?? vi.fn(async () => SYSTEM_HEALTH);
  const getCloudWatchDashboard =
    extra.getCloudWatchDashboard ??
    vi.fn(async (dashboardId: string) => ({
      ...DASHBOARD_VIEW,
      id: dashboardId,
      awsDashboardName: `wilvor-dev-${dashboardId}`,
    }));
  const getCloudWatchWidgetImage =
    extra.getCloudWatchWidgetImage ??
    vi.fn(async () => new Blob([new Uint8Array([1, 2, 3])], { type: 'image/png' }));

  const client = {
    health,
    freshness,
    systemHealth,
    getCloudWatchDashboard,
    getCloudWatchWidgetImage,
  };

  const view = extra.withShell
    ? renderWithProviders(
        <MemoryRouter initialEntries={[path]}>
          <AppRoutes mapStyleUrl={null} />
        </MemoryRouter>,
        { client },
      )
    : renderWithProviders(
        <MemoryRouter initialEntries={[path]}>
          <Routes>
            <Route
              path="/system-health"
              element={
                <>
                  <HistoryControls />
                  <SystemHealthPage />
                  <LocationProbe />
                </>
              }
            />
          </Routes>
        </MemoryRouter>,
        { client },
      );

  return {
    ...view,
    health,
    freshness,
    systemHealth,
    getCloudWatchDashboard,
    getCloudWatchWidgetImage,
  };
}

describe('SystemHealthPage', () => {
  it('renders native health from /health, /freshness and /system-health', async () => {
    const { health, freshness, systemHealth } = renderPage();

    await waitFor(() => {
      expect(screen.getByText('DEGRADED')).toBeInTheDocument();
    });

    expect(health).toHaveBeenCalledTimes(1);
    expect(freshness).toHaveBeenCalledTimes(1);
    expect(systemHealth).toHaveBeenCalledTimes(1);

    expect(screen.getByText('OK')).toBeInTheDocument();
    expect(screen.getByText('wilvor-operational-api')).toBeInTheDocument();
    expect(screen.getByText('1 problem source')).toBeInTheDocument();
    expect(screen.getByText('wilvor-dev-metar-errors')).toBeInTheDocument();

    const aircraft = screen.getByTestId('source-opensky');
    expect(within(aircraft).getByText('Stale')).toBeInTheDocument();
    expect(within(aircraft).getByText('6m 17s')).toBeInTheDocument();

    const sigmet = screen.getByTestId('source-sigmet');
    expect(within(sigmet).getByText('Available')).toBeInTheDocument();
    expect(within(sigmet).getByText('55m 42s')).toBeInTheDocument();

    const metar = screen.getByTestId('source-metar');
    expect(within(metar).getByText('Fresh')).toBeInTheDocument();
    expect(within(metar).getByText('3m 35s')).toBeInTheDocument();

    const taf = screen.getByTestId('source-taf');
    expect(within(taf).getByText('Fresh')).toBeInTheDocument();
    expect(within(taf).getByText('14m 58s')).toBeInTheDocument();
  });

  it('labels a missing freshness source Unavailable, not Healthy', async () => {
    renderPage('/system-health', {
      freshness: vi.fn(async () => ({
        generatedAt: '2026-09-06T08:00:00Z',
        sources: {},
      })),
      systemHealth: vi.fn(async () => ({
        generatedAt: '2026-09-06T08:00:00Z',
      })),
    });

    await waitFor(() => {
      expect(screen.getByTestId('source-opensky')).toBeInTheDocument();
    });

    for (const key of ['opensky', 'sigmet', 'metar', 'taf']) {
      expect(
        within(screen.getByTestId(`source-${key}`)).getByText('Unavailable'),
      ).toBeInTheDocument();
    }

    const health = screen.getByTestId('wilvor-health');
    expect(within(health).queryByText('Healthy')).not.toBeInTheDocument();
    expect(within(health).getByText('Unknown')).toBeInTheDocument();
  });

  it('isolates a freshness failure from API liveness and CloudWatch', async () => {
    renderPage('/system-health', {
      freshness: vi.fn(async () => {
        throw networkError();
      }),
    });

    await waitFor(() => {
      expect(screen.getByText(/Source freshness is unavailable/)).toBeInTheDocument();
    });

    expect(screen.getByText('OK')).toBeInTheDocument();
    expect(screen.getByText('DEGRADED')).toBeInTheDocument();
    expect(screen.getByTestId('cloudwatch-viewer')).toBeInTheDocument();
    expect(
      screen.getByRole('button', { name: /Aircraft Pipeline/ }),
    ).toBeInTheDocument();
  });

  it('keeps CloudWatch observability when native health endpoints fail', async () => {
    renderPage('/system-health', {
      health: vi.fn(async () => {
        throw networkError();
      }),
      freshness: vi.fn(async () => {
        throw networkError();
      }),
      systemHealth: vi.fn(async () => {
        throw networkError();
      }),
    });

    await waitFor(() => {
      expect(
        screen.getByText(/API liveness \(\/health\) is unavailable/),
      ).toBeInTheDocument();
    });

    expect(screen.getByText(/Source freshness is unavailable/)).toBeInTheDocument();
    expect(
      screen.getByText(/Platform health \(\/system-health\) is unavailable/),
    ).toBeInTheDocument();
    expect(screen.getByTestId('dashboard-navigator')).toBeInTheDocument();
    expect(screen.getByTestId('cloudwatch-viewer')).toBeInTheDocument();
    expect(
      screen.getByRole('button', { name: /Aircraft Pipeline/ }),
    ).toBeInTheDocument();

    const health = screen.getByTestId('wilvor-health');
    expect(within(health).queryByText('Healthy')).not.toBeInTheDocument();
  });

  it('renders the CloudWatch layout without an iframe', async () => {
    renderPage();

    await waitFor(() => {
      expect(
        screen.getByAltText('OpenSky Poller - Local Producer'),
      ).toBeInTheDocument();
    });

    expect(screen.queryByTestId('cloudwatch-iframe')).not.toBeInTheDocument();
    expect(screen.getByText('Wilvor Aircraft Pipeline')).toBeInTheDocument();
    expect(screen.getByTestId('wilvor-health')).toBeInTheDocument();
    expect(screen.getByText('OK')).toBeInTheDocument();
  });

  it('changes the selected time range in the URL without resetting health', async () => {
    renderPage();

    await waitFor(() => {
      expect(screen.getByRole('button', { name: '1h' })).toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole('button', { name: '24h' }));

    await waitFor(() => {
      expect(screen.getByTestId('location')).toHaveTextContent('range=24h');
    });

    expect(screen.getByText('DEGRADED')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '24h' })).toHaveAttribute(
      'aria-pressed',
      'true',
    );
  });

  it('lists every discovered dashboard under All and groups by category', async () => {
    renderPage();

    await waitFor(() => {
      expect(screen.getByTestId('dashboard-navigator')).toBeInTheDocument();
    });

    const navigator = screen.getByTestId('dashboard-navigator');
    for (const entry of CLOUDWATCH_DASHBOARDS) {
      expect(within(navigator).getByText(entry.name)).toBeInTheDocument();
    }

    fireEvent.click(screen.getByRole('button', { name: 'Weather' }));

    expect(screen.getByRole('button', { name: 'Weather' })).toHaveAttribute(
      'aria-pressed',
      'true',
    );
    expect(
      screen.getByRole('button', { name: /METAR Pipeline/ }),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole('button', { name: /Risk Pipeline/ }),
    ).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'All' }));

    expect(
      screen.getByRole('button', { name: /Risk Pipeline/ }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole('button', { name: /Runway Metadata/ }),
    ).toBeInTheDocument();
  });

  it('filters the navigator by search without loading another viewer', async () => {
    renderPage();

    await waitFor(() => {
      expect(screen.getByLabelText('Search dashboards')).toBeInTheDocument();
    });

    fireEvent.change(screen.getByLabelText('Search dashboards'), {
      target: { value: 'taf' },
    });

    expect(
      screen.getByRole('button', { name: /TAF Pipeline/ }),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole('button', { name: /METAR Pipeline/ }),
    ).not.toBeInTheDocument();

    fireEvent.change(screen.getByLabelText('Search dashboards'), {
      target: { value: 'airport' },
    });

    expect(
      screen.getByRole('button', { name: /Airport Status/ }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole('button', { name: /Airport Assessment/ }),
    ).toBeInTheDocument();

    expect(screen.getAllByTestId('cloudwatch-viewer')).toHaveLength(1);
  });

  it('binds the selected dashboard to a stable URL id', async () => {
    renderPage();

    await waitFor(() => {
      expect(screen.getByTestId('location')).toHaveTextContent(
        '/system-health?dashboard=aircraft-pipeline&range=3h',
      );
    });

    fireEvent.click(screen.getByRole('button', { name: /METAR Pipeline/ }));

    await waitFor(() => {
      expect(screen.getByTestId('location')).toHaveTextContent(
        '/system-health?dashboard=metar-pipeline&range=3h',
      );
    });

    expect(screen.getByRole('heading', { name: 'METAR Pipeline' })).toBeInTheDocument();
    expect(
      within(screen.getByTestId('cloudwatch-viewer')).getByText(
        'wilvor-dev-metar-pipeline',
      ),
    ).toBeInTheDocument();
    expect(screen.getAllByTestId('cloudwatch-viewer')).toHaveLength(1);
    expect(
      screen.getByTestId('cloudwatch-viewer'),
    ).toHaveAttribute('data-dashboard-id', 'metar-pipeline');
    expect(
      screen.getAllByRole('link', {
        name: 'Open wilvor-dev-metar-pipeline in CloudWatch',
      })[0],
    ).toHaveAttribute(
      'href',
      cloudWatchConsoleUrl('wilvor-dev-metar-pipeline'),
    );

    fireEvent.click(screen.getByRole('button', { name: 'Go back' }));

    await waitFor(() => {
      expect(screen.getByTestId('location')).toHaveTextContent(
        '/system-health?dashboard=aircraft-pipeline&range=3h',
      );
    });
    expect(
      screen.getByRole('heading', { name: 'Aircraft Pipeline' }),
    ).toBeInTheDocument();
  });

  it('falls back safely when the URL dashboard id is unknown', async () => {
    renderPage('/system-health?dashboard=not-a-dashboard');

    await waitFor(() => {
      expect(
        screen.getByText(/Unknown dashboard "not-a-dashboard"/),
      ).toBeInTheDocument();
    });

    expect(
      screen.getByRole('heading', { name: 'Aircraft Pipeline' }),
    ).toBeInTheDocument();
    expect(screen.getByTestId('location')).toHaveTextContent(
      'dashboard=not-a-dashboard',
    );
    expect(screen.getByTestId('location').textContent).not.toMatch(
      /token|share|AKIA/i,
    );
  });

  it('preserves navigator search while health queries settle', async () => {
    renderPage();

    await waitFor(() => {
      expect(screen.getByLabelText('Search dashboards')).toBeInTheDocument();
    });

    fireEvent.change(screen.getByLabelText('Search dashboards'), {
      target: { value: 'alerts' },
    });

    await waitFor(() => {
      expect(screen.getByText('DEGRADED')).toBeInTheDocument();
    });

    expect(screen.getByLabelText('Search dashboards')).toHaveValue('alerts');
    expect(
      screen.getByRole('button', { name: /Active Alerts/ }),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole('button', { name: /Aircraft Pipeline/ }),
    ).not.toBeInTheDocument();
    expect(
      screen.getByRole('heading', { name: 'Aircraft Pipeline' }),
    ).toBeInTheDocument();
  });

  it('redirects /health to /system-health without a request storm', async () => {
    const { health, freshness, systemHealth } = renderPage('/health', {
      withShell: true,
    });

    await waitFor(() => {
      expect(screen.getByTestId('system-health-page')).toBeInTheDocument();
    });

    expect(health).toHaveBeenCalledTimes(1);
    expect(freshness).toHaveBeenCalledTimes(1);
    expect(systemHealth).toHaveBeenCalledTimes(1);
    expect(screen.getByRole('link', { name: 'System Health' })).toHaveAttribute(
      'aria-current',
      'page',
    );
  });
});
