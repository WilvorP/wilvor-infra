import '@testing-library/jest-dom/vitest';

import { afterEach } from 'vitest';
import { cleanup } from '@testing-library/react';

// maplibre-gl registers its web worker at module import time via
// URL.createObjectURL, which jsdom does not implement. Any test that
// transitively imports the map would otherwise fail during import, before it
// gets a chance to assert anything. This only satisfies the import; it does
// not make WebGL available, so the map itself still reports its unsupported
// state under jsdom.
if (typeof window.URL.createObjectURL !== 'function') {
  window.URL.createObjectURL = () => 'blob:wilvor-test-stub';
}

if (typeof window.URL.revokeObjectURL !== 'function') {
  window.URL.revokeObjectURL = () => {};
}

afterEach(() => {
  cleanup();
});
