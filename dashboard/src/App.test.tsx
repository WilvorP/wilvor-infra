import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { App } from './App';
import { resolveConfig } from './config/env';

describe('App configuration gate', () => {
  it('shows actionable setup guidance when the API base URL is missing', () => {
    // Without this gate the console would mount and then fail every query with
    // an opaque network error, which reads as an outage rather than a setup
    // problem.
    render(<App config={resolveConfig({})} />);

    expect(screen.getByRole('alert')).toBeInTheDocument();
    expect(screen.getByText('Configuration required')).toBeInTheDocument();
    expect(
      screen.getByText(/VITE_WILVOR_API_BASE_URL is not set/),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/terraform output -raw operational_api_endpoint/),
    ).toBeInTheDocument();
  });

  it('explains an invalid base URL rather than silently defaulting', () => {
    render(
      <App config={resolveConfig({ VITE_WILVOR_API_BASE_URL: 'not-a-url' })} />,
    );

    expect(screen.getByText(/not a valid absolute URL/)).toBeInTheDocument();
  });
});
