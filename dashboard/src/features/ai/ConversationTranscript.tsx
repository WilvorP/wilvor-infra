import { LoadingState } from '@/components/QueryState';
import { StatusPill } from '@/components/StatusPill';
import type { CopilotQueryResult } from '@/api/aiContracts';
import { presentLimitationCode, presentCopilotState } from './display';

import styles from './ConversationTranscript.module.css';

export interface CopilotHistoryTurn {
  readonly id: string;
  readonly role: 'user' | 'assistant';
  readonly text: string;
  readonly result?: CopilotQueryResult;
}

export interface ConversationTranscriptProps {
  turns: readonly CopilotHistoryTurn[];
  pending: boolean;
}

export function ConversationTranscript({
  turns,
  pending,
}: ConversationTranscriptProps) {
  if (turns.length === 0 && !pending) {
    return (
      <div className={styles.empty} data-testid="copilot-transcript-empty">
        <p className={styles.emptyTitle}>No conversation yet</p>
        <p className={styles.emptyDetail}>
          Ask about current aircraft or hazard context. Answers stay advisory
          and do not become ATC, diversion, or landing directives.
        </p>
      </div>
    );
  }

  return (
    <ol className={styles.list} data-testid="copilot-transcript">
      {turns.map((turn) => (
        <li
          key={turn.id}
          className={`${styles.turn} ${styles[turn.role]}`}
          data-role={turn.role}
        >
          <header className={styles.turnHeader}>
            <span className={styles.role}>
              {turn.role === 'user' ? 'Operator' : 'Copilot'}
            </span>
            {turn.result ? (
              <StatusPill presentation={presentCopilotState(turn.result.state)} />
            ) : null}
          </header>
          <p className={styles.text}>{turn.text}</p>
          {turn.result ? <AssistantMeta result={turn.result} /> : null}
        </li>
      ))}
      {pending ? (
        <li className={styles.pending}>
          <LoadingState label="Waiting for a Copilot response" />
        </li>
      ) : null}
    </ol>
  );
}

function AssistantMeta({ result }: { result: CopilotQueryResult }) {
  return (
    <div className={styles.meta} data-testid="copilot-assistant-meta">
      <p className={styles.claims}>
        Claims admitted: no. Source authenticity is not established.
      </p>
      <ul className={styles.limitations} aria-label="Response limitations">
        {result.limitationCodes.map((code) => (
          <li key={code}>{presentLimitationCode(code)}</li>
        ))}
      </ul>
      <p className={styles.notice}>{result.completenessNotice}</p>
    </div>
  );
}
