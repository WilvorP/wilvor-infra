import { fireEvent, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { ApiError } from '@/api/errors';
import {
  CLOUDWATCH_DASHBOARDS,
  cloudWatchConsoleUrl,
} from '@/config/cloudwatchDashboards';
import { renderWithProviders } from '@/test/renderWithProviders';

import { CloudWatchViewer } from './CloudWatchViewer';

const BASE = CLOUDWATCH_DASHBOARDS[0]!;

const VIEW = {
  id: BASE.id,
  name: BASE.label,
  awsDashboardName: BASE.name,
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
    {
      id: 'widget-2',
      type: 'log',
      x: 12,
      y: 2,
      width: 12,
      height: 6,
      title: 'Unsupported',
      supported: false,
    },
  ],
};

function renderViewer(
  extra: {
    getCloudWatchDashboard?: ReturnType<typeof vi.fn>;
    getCloudWatchWidgetImage?: ReturnType<typeof vi.fn>;
    range?: '1h' | '3h' | '6h' | '12h' | '24h';
    invalidRequestedId?: string | null;
    onRangeChange?: (range: '1h' | '3h' | '6h' | '12h' | '24h') => void;
  } = {},
) {
  const getCloudWatchDashboard =
    extra.getCloudWatchDashboard ?? vi.fn(async () => VIEW);
  const getCloudWatchWidgetImage =
    extra.getCloudWatchWidgetImage ??
    vi.fn(async () => new Blob([new Uint8Array([9])], { type: 'image/png' }));
  const onRangeChange = extra.onRangeChange ?? vi.fn();

  const view = renderWithProviders(
    <CloudWatchViewer
      dashboard={BASE}
      range={extra.range ?? '3h'}
      invalidRequestedId={extra.invalidRequestedId ?? null}
      onRangeChange={onRangeChange}
    />,
    {
      client: {
        getCloudWatchDashboard,
        getCloudWatchWidgetImage,
      },
    },
  );

  return { ...view, getCloudWatchDashboard, getCloudWatchWidgetImage, onRangeChange };
}

describe('CloudWatchViewer', () => {
  it('renders CloudWatch widgets in grid order without an iframe', async () => {
    renderViewer();

    await waitFor(() => {
      expect(
        screen.getByAltText('OpenSky Poller - Local Producer'),
      ).toBeInTheDocument();
    });

    expect(screen.queryByTestId('cloudwatch-iframe')).not.toBeInTheDocument();
    expect(screen.getByText('Wilvor Aircraft Pipeline')).toBeInTheDocument();
    expect(screen.getByText('OpenSky local poller')).toBeInTheDocument();
    expect(
      screen.getByText(/widget type cannot currently be rendered/i),
    ).toBeInTheDocument();
    expect(screen.getByText(/Widget type: log/)).toBeInTheDocument();
    expect(
      screen.getAllByRole('link', { name: `Open ${BASE.name} in CloudWatch` })
        .length,
    ).toBeGreaterThan(0);
  });

  it('keeps one viewer when the selected dashboard changes', async () => {
    const { rerender } = renderViewer();

    await waitFor(() => {
      expect(screen.getByTestId('cloudwatch-grid')).toBeInTheDocument();
    });

    rerender(
      <CloudWatchViewer
        dashboard={CLOUDWATCH_DASHBOARDS[4]!}
        range="3h"
        onRangeChange={vi.fn()}
      />,
    );

    expect(screen.getAllByTestId('cloudwatch-viewer')).toHaveLength(1);
    expect(screen.getByRole('heading', { name: 'METAR Pipeline' })).toBeInTheDocument();
  });

  it('reports a partial widget failure without hiding the dashboard', async () => {
    renderViewer({
      getCloudWatchWidgetImage: vi.fn(async () => {
        throw new ApiError('widget failed', { kind: 'server', status: 500 });
      }),
    });

    await waitFor(() => {
      expect(
        screen.getByText('Unable to render this CloudWatch widget'),
      ).toBeInTheDocument();
    });

    expect(screen.getByText('Wilvor Aircraft Pipeline')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Retry' })).toBeInTheDocument();
  });

  it('exposes time range, refresh and Open in CloudWatch', async () => {
    const { getCloudWatchDashboard, onRangeChange } = renderViewer();

    await waitFor(() => {
      expect(screen.getByRole('button', { name: '3h' })).toHaveAttribute(
        'aria-pressed',
        'true',
      );
    });

    fireEvent.click(screen.getByRole('button', { name: '6h' }));
    expect(onRangeChange).toHaveBeenCalledWith('6h');

    fireEvent.click(screen.getByRole('button', { name: 'Refresh' }));
    await waitFor(() => {
      expect(getCloudWatchDashboard.mock.calls.length).toBeGreaterThan(1);
    });

    expect(
      screen.getAllByRole('link', { name: `Open ${BASE.name} in CloudWatch` })[0],
    ).toHaveAttribute('href', cloudWatchConsoleUrl(BASE.name));
  });

  it('explains an invalid requested id without changing the console target', async () => {
    renderViewer({ invalidRequestedId: 'not-real' });

    await waitFor(() => {
      expect(screen.getByText(/Unknown dashboard "not-real"/)).toBeInTheDocument();
    });

    expect(
      screen.getAllByRole('link', { name: `Open ${BASE.name} in CloudWatch` })[0],
    ).toHaveAttribute('href', cloudWatchConsoleUrl(BASE.name));
  });
});
