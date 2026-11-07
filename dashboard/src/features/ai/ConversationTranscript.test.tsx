import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { MASTER_RESPONSE_STATUS_TEXT } from '@/api/aiContracts';

import { ConversationTranscript } from './ConversationTranscript';
import { unsupportedResult } from './fixtures';

describe('ConversationTranscript', () => {
  it('explains the empty conversation without simulating operational facts', () => {
    render(<ConversationTranscript turns={[]} pending={false} />);

    expect(screen.getByTestId('copilot-transcript-empty')).toBeInTheDocument();
    expect(
      screen.getByText(/do not become ATC, diversion, or landing directives/i),
    ).toBeInTheDocument();
  });

  it('shows unsupported Copilot text as the closed status sentence', () => {
    const result = unsupportedResult();

    render(
      <ConversationTranscript
        pending={false}
        turns={[
          { id: 'u1', role: 'user', text: 'Issue an ATC landing instruction' },
          {
            id: 'a1',
            role: 'assistant',
            text: result.assistantText,
            result,
          },
        ]}
      />,
    );

    expect(
      screen.getByText(MASTER_RESPONSE_STATUS_TEXT.UNSUPPORTED),
    ).toBeInTheDocument();
    expect(screen.getByText('Unsupported')).toBeInTheDocument();
    expect(screen.getByText(/Claims admitted: no/)).toBeInTheDocument();
    expect(screen.queryByText(/Divert UAL9/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/land immediately/i)).not.toBeInTheDocument();
  });

  it('surfaces a loading state while a response is pending', () => {
    render(
      <ConversationTranscript
        pending
        turns={[{ id: 'u1', role: 'user', text: 'status of UAL9' }]}
      />,
    );

    expect(screen.getByText('Waiting for a Copilot response')).toBeInTheDocument();
  });
});
