import { useCallback, useEffect, useMemo, useRef } from 'react';

import 'maplibre-gl/dist/maplibre-gl.css';

import { describeApiError } from '@/api/errors';
import { useActiveHazards } from '@/hooks/useOperationalQueries';
import type { ActiveHazard, AircraftProjectionPoint } from '@/types/api';
import { formatCount } from '@/utils/format';

import {
  EMPTY_AIRPORT_COLLECTION,
  type AirportFeatureCollection,
} from './airportGeoJson';
import { EMPTY_AIRCRAFT_COLLECTION } from './aircraftGeoJson';
import { buildHazardGeoJson } from './hazardGeoJson';
import { useAircraftLayer } from './useAircraftLayer';
import { useAircraftLayerData } from './useAircraftLayerData';
import { useAirportLayer } from './useAirportLayer';
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
  /**
   * Investigation focus: draw only the selected aircraft. Hazard geometry and
   * the selected projection are unchanged.
   */
  isolateSelectedAircraft?: boolean;
  /**
   * Restrict the aircraft layer to these ids (loaded current-encounter
   * aircraft). `null` leaves the feed unfiltered. An empty list hides the
   * fleet rather than falling back to every tracked aircraft.
   */
  visibleAircraftIds?: readonly string[] | null;
  /** Hide the fleet when the surface is airport investigation. */
  showAircraft?: boolean;
  /** Loaded airport pages only. There is no unpaginated `/map/airports`. */
  airports?: AirportFeatureCollection | null;
  selectedAirportId?: string | null;
  onSelectAirport?: (airportId: string) => void;
}

/**
 * Live operations map.
 *
 * Layers:
 *   - active hazard geometry from `GET /hazards/active`
 *   - aircraft positions and heading from `GET /map/aircraft`
 *   - selected-aircraft short-term motion projection from `/aircraft/{id}`
 *   - optional airport markers from loaded `GET /airports` pages
 */
export function OperationsMap({
  styleUrl,
  selectedHazardId,
  onSelectHazard,
  selectedAircraftId,
  onSelectAircraft,
  projectionPoints = null,
  emphasizedHazardIds = NO_EMPHASIZED_HAZARDS,
  isolateSelectedAircraft = false,
  visibleAircraftIds = null,
  showAircraft = true,
  airports = null,
  selectedAirportId = null,
  onSelectAirport,
}: OperationsMapProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const { map, ready, error: mapError } = useOperationsMap(
    containerRef,
    styleUrl,
  );

  const hazards = useActiveHazards();
  const aircraft = useAircraftLayerData({ enabled: showAircraft });

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

  const visibleAircraftIdSet = useMemo(
    () => (visibleAircraftIds == null ? null : new Set(visibleAircraftIds)),
    [visibleAircraftIds],
  );

  const aircraftCollection = useMemo(() => {
    if (!showAircraft) {
      return EMPTY_AIRCRAFT_COLLECTION;
    }

    const collection = aircraft.result.collection;
    const scoped =
      visibleAircraftIdSet == null
        ? collection
        : {
            type: 'FeatureCollection' as const,
            features: collection.features.filter((feature) =>
              visibleAircraftIdSet.has(feature.properties.aircraftId),
            ),
          };

    if (!isolateSelectedAircraft || selectedAircraftId === null) {
      return scoped;
    }

    return {
      type: 'FeatureCollection' as const,
      features: scoped.features.filter(
        (feature) => feature.properties.aircraftId === selectedAircraftId,
      ),
    };
  }, [
    aircraft.result.collection,
    isolateSelectedAircraft,
    selectedAircraftId,
    showAircraft,
    visibleAircraftIdSet,
  ]);

  const airportCollection = airports ?? EMPTY_AIRPORT_COLLECTION;

  const handleSelectHazard = useCallback(
    (hazardId: string) => {
      const hazard = hazardItems.find(
        (candidate) => candidate.hazard_id === hazardId,
      );

      onSelectHazard(hazard ?? null);
    },
    [hazardItems, onSelectHazard],
  );

  // Layer order: hazards, airports, trajectory, then aircraft.
  useHazardLayer(map, ready, geoJson.collection, handleSelectHazard);

  useAirportLayer(
    map,
    ready,
    airportCollection,
    selectedAirportId,
    onSelectAirport ?? null,
  );

  useTrajectoryLayer(
    map,
    ready,
    selectedAircraftId !== null,
    selectedAircraftId !== null ? projectionPoints : null,
  );

  useAircraftLayer(
    map,
    ready,
    aircraftCollection,
    selectedAircraftId,
    onSelectAircraft,
  );

  const focusedAircraftIdRef = useRef<string | null>(null);

  useEffect(() => {
    if (map === null || !ready) {
      return;
    }

    if (selectedAircraftId === null) {
      focusedAircraftIdRef.current = null;
      return;
    }

    if (focusedAircraftIdRef.current === selectedAircraftId) {
      return;
    }

    const selected = aircraft.result.aircraftById.get(selectedAircraftId);

    if (selected === undefined) {
      return;
    }

    focusedAircraftIdRef.current = selectedAircraftId;
    map.easeTo({
      center: [selected.longitude, selected.latitude],
      zoom: Math.max(map.getZoom(), 5),
      duration: 700,
    });
  }, [map, ready, selectedAircraftId, aircraft.result.aircraftById]);

  const focusedAirportIdRef = useRef<string | null>(null);

  useEffect(() => {
    if (map === null || !ready) {
      return;
    }

    if (selectedAirportId === null) {
      focusedAirportIdRef.current = null;
      return;
    }

    if (focusedAirportIdRef.current === selectedAirportId) {
      return;
    }

    const selected = airportCollection.features.find(
      (feature) => feature.properties.airportId === selectedAirportId,
    );

    if (selected === undefined) {
      return;
    }

    focusedAirportIdRef.current = selectedAirportId;
    map.easeTo({
      center: selected.geometry.coordinates,
      zoom: Math.max(map.getZoom(), 5),
      duration: 700,
    });
  }, [map, ready, selectedAirportId, airportCollection]);

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

  if (showAircraft && aircraftResult.truncated) {
    warnings.push(
      'The aircraft feed reported that it was truncated; the map shows a ' +
        'partial traffic picture.',
    );
  }

  if (showAircraft && aircraftResult.droppedCount > 0) {
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
              {!showAircraft
                ? 'hidden'
                : aircraft.isPending
                  ? '…'
                  : formatCount(
                      isolateSelectedAircraft || visibleAircraftIdSet != null
                        ? aircraftCollection.features.length
                        : aircraftResult.renderedCount,
                    )}
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

          <li
            className={`${styles.legendItem} ${airports == null ? styles.legendPending : ''}`}
          >
            <span
              className={`${styles.swatch} ${airports == null ? styles.swatchPending : styles.swatchAirport}`}
              aria-hidden="true"
            />
            <span>Airports</span>
            <span className={`${styles.legendCount} wv-numeric`}>
              {airports == null
                ? 'pending'
                : formatCount(airportCollection.features.length)}
            </span>
          </li>
        </ul>

        {aircraftResult.withoutHeadingCount > 0 ? (
          <p className={styles.legendNote}>
            {formatCount(aircraftResult.withoutHeadingCount)} aircraft shown as
            dots: heading not reported.
          </p>
        ) : null}

        {isolateSelectedAircraft && selectedAircraftId !== null ? (
          <p className={styles.legendNote}>
            Investigation focus. Other aircraft are hidden.
          </p>
        ) : visibleAircraftIdSet != null ? (
          <p className={styles.legendNote}>
            Aircraft from loaded current encounters only, not the full fleet.
          </p>
        ) : selectedAirportId !== null ? (
          <p className={styles.legendNote}>Airport selected.</p>
        ) : selectedHazardId !== null || selectedAircraftId !== null ? (
          <p className={styles.legendNote}>
            {selectedAircraftId !== null ? 'Aircraft' : 'Hazard'} selected.
            Details in the investigation panel below.
          </p>
        ) : airports != null ? (
          <p className={styles.legendNote}>
            Airport markers are the loaded pages only, not a full-network
            feed.
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

        {showAircraft && aircraftResult.contractError !== null ? (
          <div className={styles.overlayError} role="alert">
            <span aria-hidden="true">⚠ </span>
            Aircraft layer disabled. {aircraftResult.contractError} No aircraft
            are drawn, because reading rows against an unexpected column layout
            could place traffic at wrong positions.
          </div>
        ) : null}

        {showAircraft && aircraft.isError ? (
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
