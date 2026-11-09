import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { MASTER_RESPONSE_STATUS_TEXT } from '@/api/aiContracts';

import { ConversationTranscript } from './ConversationTranscript';
import {
  developerTool,
  encounterTool,
  localPreviewResult,
  unsupportedResult,
} from './fixtures';

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

  it('shows local aircraft observations without replacing the closed status sentence', () => {
    const result = localPreviewResult(undefined, {
      toolResults: [developerTool(), encounterTool()],
    });

    render(
      <ConversationTranscript
        pending={false}
        turns={[
          { id: 'u1', role: 'user', text: 'status of aircraft 407d6c' },
          {
            id: 'a1',
            role: 'assistant',
            text: result.assistantText,
            result,
          },
        ]}
      />,
    );

    const assistant = document.querySelector('[data-role="assistant"]');
    expect(assistant?.querySelector('p')?.textContent).toBe(
      MASTER_RESPONSE_STATUS_TEXT.INSUFFICIENT_EVIDENCE,
    );
    expect(screen.getByTestId('copilot-developer-preview')).toBeInTheDocument();
    expect(screen.getByText(/Stored aircraft 407d6c/)).toBeInTheDocument();
    expect(screen.getByText('Latitude')).toBeInTheDocument();
    expect(screen.getByText('37.62')).toBeInTheDocument();
    const details = screen
      .getByText('Specialist verification and technical details')
      .closest('details');
    expect(details).not.toHaveAttribute('open');
    expect(details).toHaveTextContent('DIAGNOSTIC_NARRATIVE_ONLY');
  });

  it('does not render developer observations for a non-preview result', () => {
    const result = unsupportedResult();

    render(
      <ConversationTranscript
        pending={false}
        turns={[
          {
            id: 'a1',
            role: 'assistant',
            text: result.assistantText,
            result,
          },
        ]}
      />,
    );

    expect(screen.queryByTestId('copilot-developer-preview')).not.toBeInTheDocument();
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
