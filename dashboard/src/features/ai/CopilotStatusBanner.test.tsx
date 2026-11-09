import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import {
  DISABLED_COPILOT_STANDING,
  HOST_CONTRACT_COPILOT_STANDING,
  INJECTED_COPILOT_STANDING,
  LOCAL_DEV_PREVIEW_STANDING,
} from '@/api/aiContracts';

import { CopilotStatusBanner } from './CopilotStatusBanner';

describe('CopilotStatusBanner', () => {
  it('states that production AI is not active and the route is disabled', () => {
    render(<CopilotStatusBanner standing={DISABLED_COPILOT_STANDING} />);

    expect(screen.getByText('POST /ai/query')).toBeInTheDocument();
    expect(screen.getByText('Not active')).toBeInTheDocument();
    expect(screen.getByText('No')).toBeInTheDocument();
    expect(
      screen.getByText(/This console does not call that route/i),
    ).toBeInTheDocument();
    expect(screen.queryByText(/production AI is active/i)).not.toBeInTheDocument();
  });

  it('labels injected fixture transport as test data, not a live AWS read', () => {
    render(<CopilotStatusBanner standing={INJECTED_COPILOT_STANDING} />);

    expect(
      screen.getByText(/fixture data, not a live AWS read/i),
    ).toBeInTheDocument();
    expect(screen.getByText('Not active')).toBeInTheDocument();
  });

  it('labels the loopback preview as not production', () => {
    render(<CopilotStatusBanner standing={LOCAL_DEV_PREVIEW_STANDING} />);

    expect(screen.getByText(/NOT PRODUCTION/i)).toBeInTheDocument();
    expect(screen.getByText(/operator-authenticated DEV evidence/i)).toBeInTheDocument();
    expect(screen.getByText('Not active')).toBeInTheDocument();
    expect(screen.getByText('No')).toBeInTheDocument();
  });

  it('labels host-contract transport as untrusted browser ids with the route disabled', () => {
    render(<CopilotStatusBanner standing={HOST_CONTRACT_COPILOT_STANDING} />);

    expect(
      screen.getByText(/Browser IDs are untrusted labels/i),
    ).toBeInTheDocument();
    expect(screen.getByText('Not active')).toBeInTheDocument();
  });
});
