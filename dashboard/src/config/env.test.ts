import { describe, expect, it } from 'vitest';

import { resolveConfig } from './env';

describe('resolveConfig', () => {
  it('accepts a valid base URL and applies defaults', () => {
    const result = resolveConfig({
      VITE_WILVOR_API_BASE_URL: 'https://abc.execute-api.us-west-1.amazonaws.com',
    });

    expect(result.errors).toHaveLength(0);
    expect(result.config?.apiBaseUrl).toBe(
      'https://abc.execute-api.us-west-1.amazonaws.com',
    );
    expect(result.config?.apiTimeoutMs).toBe(20_000);
    expect(result.config?.mapStyleUrl).toBeNull();
  });

  it('strips trailing slashes so route joining stays predictable', () => {
    const result = resolveConfig({
      VITE_WILVOR_API_BASE_URL: 'https://api.example.test///',
    });

    expect(result.config?.apiBaseUrl).toBe('https://api.example.test');
  });

  it('reports a missing base URL rather than returning a config', () => {
    const result = resolveConfig({});

    expect(result.config).toBeNull();
    expect(result.errors[0]).toContain('VITE_WILVOR_API_BASE_URL');
  });

  it('treats a blank base URL as missing', () => {
    const result = resolveConfig({ VITE_WILVOR_API_BASE_URL: '   ' });

    expect(result.config).toBeNull();
  });

  it('rejects a non-absolute base URL', () => {
    const result = resolveConfig({ VITE_WILVOR_API_BASE_URL: '/api' });

    expect(result.config).toBeNull();
    expect(result.errors[0]).toContain('not a valid absolute URL');
  });

  it('rejects a non-http protocol', () => {
    const result = resolveConfig({
      VITE_WILVOR_API_BASE_URL: 'ftp://api.example.test',
    });

    expect(result.config).toBeNull();
  });

  it('falls back to the default timeout when the value is unusable', () => {
    const nonNumeric = resolveConfig({
      VITE_WILVOR_API_BASE_URL: 'https://api.example.test',
      VITE_WILVOR_API_TIMEOUT_MS: 'soon',
    });

    expect(nonNumeric.config?.apiTimeoutMs).toBe(20_000);
    expect(nonNumeric.errors[0]).toContain('VITE_WILVOR_API_TIMEOUT_MS');

    const outOfRange = resolveConfig({
      VITE_WILVOR_API_BASE_URL: 'https://api.example.test',
      VITE_WILVOR_API_TIMEOUT_MS: '999999',
    });

    expect(outOfRange.config?.apiTimeoutMs).toBe(20_000);
    expect(outOfRange.errors).toHaveLength(1);
  });

  it('accepts an in-range timeout override', () => {
    const result = resolveConfig({
      VITE_WILVOR_API_BASE_URL: 'https://api.example.test',
      VITE_WILVOR_API_TIMEOUT_MS: '8000',
    });

    expect(result.config?.apiTimeoutMs).toBe(8_000);
    expect(result.errors).toHaveLength(0);
  });
});
