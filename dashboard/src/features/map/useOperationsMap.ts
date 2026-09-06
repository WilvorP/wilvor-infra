import maplibregl, { type Map as MapLibreMap } from 'maplibre-gl';
import { useEffect, useRef, useState, type RefObject } from 'react';

import { DARK_BASEMAP_STYLE, DEFAULT_VIEW } from './mapStyle';

export interface UseOperationsMapResult {
  /** The MapLibre instance once the style has finished loading. */
  map: MapLibreMap | null;
  /** True once the style is loaded and layers may be added. */
  ready: boolean;
  /** Non-fatal map failure, e.g. WebGL unavailable or tiles unreachable. */
  error: string | null;
}

/**
 * Create and own a MapLibre instance for the lifetime of a container element.
 *
 * The map is created once and never recreated on re-render; data layers are
 * mutated in place by separate hooks. Recreating the map per render would
 * discard the operator's pan and zoom on every poll.
 */
export function useOperationsMap(
  containerRef: RefObject<HTMLDivElement | null>,
  styleUrl: string | null,
): UseOperationsMapResult {
  const mapRef = useRef<MapLibreMap | null>(null);
  const [map, setMap] = useState<MapLibreMap | null>(null);
  const [ready, setReady] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const container = containerRef.current;

    if (container === null || mapRef.current !== null) {
      return;
    }

    let instance: MapLibreMap;

    try {
      instance = new maplibregl.Map({
        container,
        style: styleUrl ?? DARK_BASEMAP_STYLE,
        center: DEFAULT_VIEW.center,
        zoom: DEFAULT_VIEW.zoom,
        attributionControl: { compact: true },
        // Pitch and rotation add no operational value to a plan-view traffic
        // picture and make bearing interpretation harder.
        pitchWithRotate: false,
        dragRotate: false,
        touchZoomRotate: false,
        // Overlays are swapped wholesale on each poll; cross-fading them just
        // blurs the transition between two operational pictures.
        fadeDuration: 0,
      });
    } catch (cause) {
      setError(
        cause instanceof Error
          ? `Map could not be initialised: ${cause.message}`
          : 'Map could not be initialised.',
      );
      return;
    }

    mapRef.current = instance;

    instance.addControl(
      new maplibregl.NavigationControl({ showCompass: false }),
      'top-right',
    );

    instance.addControl(
      new maplibregl.ScaleControl({ unit: 'nautical' }),
      'bottom-left',
    );

    instance.on('error', (event) => {
      // MapLibre surfaces tile fetch failures here. They degrade the basemap
      // but must not blank the operational overlays, so this is reported
      // rather than thrown.
      const message = event.error?.message ?? 'Unknown map error.';
      setError(`Basemap issue: ${message}`);
    });

    const handleLoad = () => {
      setMap(instance);
      setReady(true);
    };

    if (instance.loaded()) {
      handleLoad();
    } else {
      instance.once('load', handleLoad);
    }

    const observer =
      typeof ResizeObserver === 'undefined'
        ? null
        : new ResizeObserver(() => {
            if (container.clientWidth > 0 && container.clientHeight > 0) {
              instance.resize();
            }
          });

    observer?.observe(container);

    return () => {
      observer?.disconnect();
      mapRef.current = null;
      setMap(null);
      setReady(false);
      instance.remove();
    };
  }, [containerRef, styleUrl]);

  return { map, ready, error };
}
