import { useCallback, useMemo, useRef } from 'react';

import 'maplibre-gl/dist/maplibre-gl.css';

import { describeApiError } from '@/api/errors';
import { useActiveHazards } from '@/hooks/useOperationalQueries';
import type { ActiveHazard, AircraftProjectionPoint } from '@/types/api';
import { formatCount } from '@/utils/format';

import { buildHazardGeoJson } from './hazardGeoJson';
import { useAircraftLayer } from './useAircraftLayer';
import { useAircraftLayerData } from './useAircraftLayerData';
import { useHazardLayer } from './useHazardLayer';
import { useOperationsMap } from './useOperationsMap';
import { useTrajectoryLayer } from './useTrajectoryLayer';
import styles from './OperationsMap.module.css';

const NO_EMPHASIZED_HAZARDS: readonly string[] = [];

export interface OperationsMapProps {
  styleUrl: string | null;
  selectedHazardId: string | null;
  onSelectHazard: (hazard: ActiveHazard | null) => void;
  selectedAircraftId: string | null;
  onSelectAircraft: (aircraftId: string) => void;
  /** Projection points for the selected aircraft only; never a fleet overlay. */
  projectionPoints?: readonly AircraftProjectionPoint[] | null;
  /** Hazard ids referenced by the selected aircraft's returned encounters. */
  emphasizedHazardIds?: readonly string[];
}

/**
 * Live operations map.
 *
 * Layers:
 *   - active hazard geometry from `GET /hazards/active`
 *   - aircraft positions and heading from `GET /map/aircraft`
 *   - selected-aircraft short-term motion projection from `/aircraft/{id}`
 *
 * Airports are not yet drawn. `AirportStatus` does carry coordinates, so that
 * needs no backend work — only pagination handling for the `/airports` scan.
 */
export function OperationsMap({
  styleUrl,
  selectedHazardId,
  onSelectHazard,
  selectedAircraftId,
  onSelectAircraft,
  projectionPoints = null,
  emphasizedHazardIds = NO_EMPHASIZED_HAZARDS,
}: OperationsMapProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const { map, ready, error: mapError } = useOperationsMap(
    containerRef,
    styleUrl,
  );

  const hazards = useActiveHazards();
  const aircraft = useAircraftLayerData();

  const hazardItems = useMemo(
    () => hazards.data?.items ?? [],
    [hazards.data?.items],
  );

  const emphasizedHazardIdSet = useMemo(
    () => new Set(emphasizedHazardIds),
    [emphasizedHazardIds],
  );

  const geoJson = useMemo(
    () => buildHazardGeoJson(hazardItems, emphasizedHazardIdSet),
    [hazardItems, emphasizedHazardIdSet],
  );

  const handleSelectHazard = useCallback(
    (hazardId: string) => {
      const hazard = hazardItems.find(
        (candidate) => candidate.hazard_id === hazardId,
      );

      onSelectHazard(hazard ?? null);
    },
    [hazardItems, onSelectHazard],
  );

  // Layer order: hazards, then the selected trajectory, then aircraft so
  // traffic symbols stay on top of fills and the projection line.
  useHazardLayer(map, ready, geoJson.collection, handleSelectHazard);

  useTrajectoryLayer(
    map,
    ready,
    selectedAircraftId !== null,
    selectedAircraftId !== null ? projectionPoints : null,
  );

  useAircraftLayer(
    map,
    ready,
    aircraft.result.collection,
    selectedAircraftId,
    onSelectAircraft,
  );

  const hazardsTruncated = hazards.data?.nextToken != null;
  const aircraftResult = aircraft.result;

  const warnings: string[] = [];

  if (geoJson.withoutGeometryCount > 0) {
    warnings.push(
      `${formatCount(geoJson.withoutGeometryCount)} active hazard` +
        `${geoJson.withoutGeometryCount === 1 ? '' : 's'} could not be drawn ` +
        `(geometry unavailable).`,
    );
  }

  if (hazardsTruncated) {
    warnings.push(
      'More hazards exist beyond the first page; the map shows a partial ' +
        'hazard picture.',
    );
  }

  if (aircraftResult.truncated) {
    warnings.push(
      'The aircraft feed reported that it was truncated; the map shows a ' +
        'partial traffic picture.',
    );
  }

  if (aircraftResult.droppedCount > 0) {
    warnings.push(
      `${formatCount(aircraftResult.droppedCount)} aircraft record` +
        `${aircraftResult.droppedCount === 1 ? '' : 's'} could not be placed ` +
        `(position unusable).`,
    );
  }

  return (
    <div className={styles.wrapper}>
      <div
        ref={containerRef}
        className={styles.canvas}
        role="application"
        aria-label="Live operations map"
      />

      {!ready && mapError === null ? (
        <div className={styles.overlayCentre} role="status">
          Initialising map…
        </div>
      ) : null}

      <div className={styles.legend}>
        <p className={styles.legendTitle}>Layers</p>

        <ul className={styles.legendList}>
          <li className={styles.legendItem}>
            <span
              className={`${styles.swatch} ${styles.swatchAircraft}`}
              aria-hidden="true"
            />
            <span>Aircraft</span>
            <span className={`${styles.legendCount} wv-numeric`}>
              {aircraft.isPending
                ? '…'
                : formatCount(aircraftResult.renderedCount)}
            </span>
          </li>

          <li className={styles.legendItem}>
            <span
              className={`${styles.swatch} ${styles.swatchHazard}`}
              aria-hidden="true"
            />
            <span>Active hazards</span>
            <span className={`${styles.legendCount} wv-numeric`}>
              {hazards.isPending && hazards.data === undefined
                ? '…'
                : formatCount(geoJson.renderedCount)}
            </span>
          </li>

          {selectedAircraftId !== null ? (
            <li className={styles.legendItem}>
              <span
                className={`${styles.swatch} ${styles.swatchTrajectory}`}
                aria-hidden="true"
              />
              <span>Selected projection</span>
            </li>
          ) : null}

          <li className={`${styles.legendItem} ${styles.legendPending}`}>
            <span
              className={`${styles.swatch} ${styles.swatchPending}`}
              aria-hidden="true"
            />
            <span>Airports</span>
            <span className={styles.legendCount}>pending</span>
          </li>
        </ul>

        {aircraftResult.withoutHeadingCount > 0 ? (
          <p className={styles.legendNote}>
            {formatCount(aircraftResult.withoutHeadingCount)} aircraft shown as
            dots: heading not reported.
          </p>
        ) : null}

        {selectedHazardId !== null || selectedAircraftId !== null ? (
          <p className={styles.legendNote}>
            {selectedAircraftId !== null ? 'Aircraft' : 'Hazard'} selected.
            Details in the investigation panel below.
          </p>
        ) : null}
      </div>

      <div className={styles.overlayStack}>
        {mapError !== null ? (
          <div className={styles.overlayError} role="alert">
            <span aria-hidden="true">⚠ </span>
            {mapError}
          </div>
        ) : null}

        {aircraftResult.contractError !== null ? (
          <div className={styles.overlayError} role="alert">
            <span aria-hidden="true">⚠ </span>
            Aircraft layer disabled. {aircraftResult.contractError} No aircraft
            are drawn, because reading rows against an unexpected column layout
            could place traffic at wrong positions.
          </div>
        ) : null}

        {aircraft.isError ? (
          <div className={styles.overlayError} role="alert">
            <span aria-hidden="true">⚠ </span>
            Aircraft layer unavailable. {describeApiError(aircraft.error)}
          </div>
        ) : null}

        {hazards.isError ? (
          <div className={styles.overlayError} role="alert">
            <span aria-hidden="true">⚠ </span>
            Hazard layer unavailable. {describeApiError(hazards.error)}
          </div>
        ) : null}

        {warnings.length > 0 ? (
          <div className={styles.overlayWarning} role="status">
            <span aria-hidden="true">⚠ </span>
            {warnings.join(' ')}
          </div>
        ) : null}
      </div>
    </div>
  );
}
