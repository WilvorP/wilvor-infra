import { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';

import { describeApiError } from '@/api/errors';
import { LoadingState } from '@/components/QueryState';
import {
  cloudWatchConsoleUrl,
  type CloudWatchDashboardEntry,
} from '@/config/cloudwatchDashboards';
import { useCloudWatchWidgetImage } from '@/hooks/useOperationalQueries';
import type {
  CloudWatchDashboardWidget,
  CloudWatchViewerRange,
} from '@/types/api';
import { asString } from '@/utils/coerce';

import { CloudWatchMarkdown } from './CloudWatchMarkdown';
import styles from './CloudWatchWidgetCell.module.css';

const MAX_IMAGE_PIXELS = 2000;
const MIN_IMAGE_WIDTH = 120;
const MIN_IMAGE_HEIGHT = 80;

export function snapWidgetPixels(
  width: number,
  height: number,
): { width: number; height: number } | null {
  if (width < 8 || height < 8) {
    return null;
  }

  return {
    width: Math.max(
      MIN_IMAGE_WIDTH,
      Math.min(MAX_IMAGE_PIXELS, Math.round(width)),
    ),
    height: Math.max(
      MIN_IMAGE_HEIGHT,
      Math.min(MAX_IMAGE_PIXELS, Math.round(height)),
    ),
  };
}

export interface CloudWatchWidgetCellProps {
  catalog: CloudWatchDashboardEntry;
  widget: CloudWatchDashboardWidget;
  range: CloudWatchViewerRange;
  revision: string;
}

export function CloudWatchWidgetCell({
  catalog,
  widget,
  range,
  revision,
}: CloudWatchWidgetCellProps) {
  const widgetId = asString(widget.id) ?? '';
  const widgetType = asString(widget.type) ?? 'unknown';
  const isMetric = widgetType === 'metric';
  const isText = widgetType === 'text';
  const supported = widget.supported !== false && (isMetric || isText);
  const cellRef = useRef<HTMLElement>(null);
  const [pixelSize, setPixelSize] = useState<{
    width: number;
    height: number;
  } | null>(null);
  const [cellReady, setCellReady] = useState(!isMetric);

  const image = useCloudWatchWidgetImage(
    catalog.id,
    widgetId,
    range,
    revision,
    isMetric && widgetId.length > 0 && cellReady,
    pixelSize,
  );

  const objectUrl = useMemo(() => {
    if (!image.data) {
      return null;
    }

    return URL.createObjectURL(image.data);
  }, [image.data]);

  useEffect(() => {
    return () => {
      if (objectUrl) {
        URL.revokeObjectURL(objectUrl);
      }
    };
  }, [objectUrl]);

  const column = (widget.x ?? 0) + 1;
  const row = (widget.y ?? 0) + 1;
  const width = widget.width ?? 24;
  const height = widget.height ?? 2;

  useLayoutEffect(() => {
    if (!isMetric) {
      return;
    }

    const el = cellRef.current;

    if (el === null) {
      return;
    }

    const apply = (next: { width: number; height: number } | null) => {
      setPixelSize((previous) => {
        if (
          previous?.width === next?.width &&
          previous?.height === next?.height
        ) {
          return previous;
        }

        return next;
      });
    };

    apply(snapWidgetPixels(el.clientWidth, el.clientHeight));
    setCellReady(true);

    if (typeof ResizeObserver === 'undefined') {
      return;
    }

    const observer = new ResizeObserver((entries) => {
      const box = entries[0]?.contentRect;

      if (!box) {
        return;
      }

      apply(snapWidgetPixels(box.width, box.height));
    });

    observer.observe(el);

    return () => {
      observer.disconnect();
    };
  }, [isMetric, width, height]);

  return (
    <article
      ref={cellRef}
      className={styles.cell}
      style={{
        gridColumn: `${column} / span ${width}`,
        gridRow: `${row} / span ${height}`,
      }}
      data-testid={`cloudwatch-widget-${widgetId}`}
      data-widget-type={widgetType}
    >
      {isText ? (
        <CloudWatchMarkdown markdown={asString(widget.markdown) ?? ''} />
      ) : null}

      {isMetric && image.isPending && image.data === undefined ? (
        <LoadingState label="Loading CloudWatch widget" />
      ) : null}

      {isMetric && objectUrl ? (
        <img
          className={styles.image}
          src={objectUrl}
          alt={asString(widget.title) ?? catalog.label}
        />
      ) : null}

      {isMetric && image.isError && image.data === undefined ? (
        <WidgetUnavailable
          title="Unable to render this CloudWatch widget"
          detail={describeApiError(image.error)}
          consoleUrl={cloudWatchConsoleUrl(catalog.name)}
          dashboardName={catalog.name}
          onRetry={() => {
            void image.refetch();
          }}
        />
      ) : null}

      {!supported ? (
        <WidgetUnavailable
          title="This CloudWatch widget type cannot currently be rendered inside Wilvor."
          detail={`Widget type: ${widgetType}`}
          consoleUrl={cloudWatchConsoleUrl(catalog.name)}
          dashboardName={catalog.name}
        />
      ) : null}
    </article>
  );
}

function WidgetUnavailable({
  title,
  detail,
  consoleUrl,
  dashboardName,
  onRetry,
}: {
  title: string;
  detail: string;
  consoleUrl: string;
  dashboardName: string;
  onRetry?: () => void;
}) {
  return (
    <div className={styles.unavailable}>
      <p>{title}</p>
      <p className={styles.detail}>{detail}</p>
      <div className={styles.actions}>
        {onRetry ? (
          <button type="button" onClick={onRetry}>
            Retry
          </button>
        ) : null}
        <a href={consoleUrl} target="_blank" rel="noreferrer">
          Open in CloudWatch ↗
        </a>
        <span className="wv-visually-hidden">{dashboardName}</span>
      </div>
    </div>
  );
}
