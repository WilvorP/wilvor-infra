import { useMemo, useRef, useState } from 'react';
import { useSearchParams } from 'react-router-dom';

import { useAiClient } from '@/api/aiClientContext';
import { CopilotContractError } from '@/api/aiContracts';
import { describeApiError } from '@/api/errors';
import { Panel } from '@/components/Panel';
import { ErrorState, Notice } from '@/components/QueryState';
import { DataField, DataFieldGrid } from '@/components/DataField';

import { CopilotComposer } from './CopilotComposer';
import { CopilotStatusBanner } from './CopilotStatusBanner';
import { ConversationTranscript, type CopilotHistoryTurn } from './ConversationTranscript';
import { EvidencePanel } from './EvidencePanel';
import {
  buildCopilotQueryRequest,
  createCopilotRequestIdentity,
  createCopilotSessionIdentity,
  selectorHintsFromSearch,
  toUtcTimestamp,
  type CopilotSessionIdentity,
} from './session';

import styles from './CopilotPage.module.css';

export interface CopilotPageProps {
  createSession?: () => CopilotSessionIdentity;
  now?: () => Date;
  createRequestId?: () => string;
}

function isAbortError(error: unknown): boolean {
  return (
    (error instanceof DOMException && error.name === 'AbortError') ||
    (error instanceof Error && error.name === 'AbortError')
  );
}

/**
 * Operations Copilot conversational surface.
 *
 * The default client never POSTs `/ai/query`. Production AI is not active.
 * Injected clients are for offline frontend tests only.
 */
export function CopilotPage({
  createSession = createCopilotSessionIdentity,
  now = () => new Date(),
  createRequestId,
}: CopilotPageProps) {
  const client = useAiClient();
  const [searchParams] = useSearchParams();
  const [session] = useState(createSession);
  const selectorHints = useMemo(
    () => selectorHintsFromSearch(searchParams),
    [searchParams],
  );

  const [draft, setDraft] = useState('');
  const [turns, setTurns] = useState<CopilotHistoryTurn[]>([]);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const abortRef = useRef<AbortController | null>(null);
  const turnSeq = useRef(0);

  const latestResult = [...turns]
    .reverse()
    .find((turn) => turn.result)?.result ?? null;

  async function submit() {
    const userText = draft.trim();

    if (userText.length === 0 || pending) {
      return;
    }

    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;

    const requestIds = createCopilotRequestIdentity(session, createRequestId);
    const request = buildCopilotQueryRequest({
      session,
      request: requestIds,
      userText,
      priorTurns: turns.map((turn) => ({ role: turn.role, text: turn.text })),
      applicationRequestTimeUtc: toUtcTimestamp(now()),
      selectorHints,
    });

    const userTurn: CopilotHistoryTurn = {
      id: `user-${request.masterRequestId}`,
      role: 'user',
      text: userText,
    };

    setDraft('');
    setError(null);
    setPending(true);
    setTurns((current) => [...current, userTurn]);

    try {
      const result = await client.query(request, { signal: controller.signal });
      const assistantTurn: CopilotHistoryTurn = {
        id: `assistant-${result.masterRequestId}-${turnSeq.current++}`,
        role: 'assistant',
        text: result.assistantText,
        result,
      };

      setTurns((current) => [...current, assistantTurn]);
    } catch (cause) {
      if (isAbortError(cause)) {
        return;
      }

      setError(cause);
    } finally {
      setPending(false);
    }
  }

  const retryable =
    error !== null &&
    !(error instanceof CopilotContractError) &&
    !(error instanceof Error && error.name === 'CopilotContractError');

  return (
    <div className={styles.page} data-testid="copilot-page">
      <header className={styles.header}>
        <div>
          <h1 className={styles.title}>Operations Copilot</h1>
          <p className={styles.lede}>
            {client.standing.transport === 'LOCAL_DEV_PREVIEW'
              ? 'NOT PRODUCTION. This preview sends the question to the loopback developer backend. Answers stay unadmitted and source authenticity stays not established.'
              : 'Conversational inspection of Copilot standing, limitations, and display-only aircraft or hazard evidence. This surface does not activate production AI.'}
          </p>
        </div>
        <CopilotStatusBanner standing={client.standing} />
        <DataFieldGrid columns={3}>
          <DataField
            label="Planning session"
            value={session.planningSessionId}
            numeric
          />
          <DataField
            label="Conversation"
            value={session.conversationId}
            numeric
          />
          <DataField
            label="Transport"
            value={client.standing.transport}
            numeric
          />
        </DataFieldGrid>
        {selectorHints ? (
          <Notice>
            Selector hints from the URL are untrusted text. They are not
            authorization and they do not execute a tool.
          </Notice>
        ) : null}
      </header>

      <div className={styles.body}>
        <Panel title="Conversation" flush className={styles.conversation}>
          <div className={styles.conversationBody}>
            <div className={styles.transcript}>
              <ConversationTranscript turns={turns} pending={pending} />
              {latestResult?.developerPreview ? (
                <div className={styles.preview} data-testid="copilot-developer-preview">
                  <p className={styles.previewNotice}>
                    {latestResult.developerPreview.notice}
                  </p>
                  <p className={styles.previewAnswer}>
                    {latestResult.developerPreview.answer}
                  </p>
                  {latestResult.developerPreview.modelErrors.length > 0 ? (
                    <p className={styles.previewMeta}>
                      Model errors: {latestResult.developerPreview.modelErrors.join(', ')}
                    </p>
                  ) : null}
                  {latestResult.developerPreview.toolErrors.length > 0 ? (
                    <p className={styles.previewMeta}>
                      Tool errors: {latestResult.developerPreview.toolErrors.join(', ')}
                    </p>
                  ) : null}
                  {latestResult.developerPreview.missingEvidence.length > 0 ? (
                    <p className={styles.previewMeta}>
                      Missing evidence: {latestResult.developerPreview.missingEvidence.join(', ')}
                    </p>
                  ) : null}
                  {latestResult.developerPreview.staleEvidence.length > 0 ? (
                    <p className={styles.previewMeta}>
                      Stale evidence: {latestResult.developerPreview.staleEvidence.join(', ')}
                    </p>
                  ) : null}
                  {latestResult.developerPreview.routeKinds &&
                  latestResult.developerPreview.routeKinds.length > 0 ? (
                    <p className={styles.previewMeta}>
                      Authorized route: {latestResult.developerPreview.routeKinds.join(', ')}
                    </p>
                  ) : null}
                  {latestResult.developerPreview.terminalDecision ? (
                    <p className={styles.previewMeta}>
                      Planner terminal decision: {latestResult.developerPreview.terminalDecision}
                    </p>
                  ) : null}
                  {latestResult.developerPreview.toolResults.some(
                    (tool) => (tool.observations?.length ?? 0) > 0,
                  ) ? (
                    <ul className={styles.previewList}>
                      {latestResult.developerPreview.toolResults.flatMap((tool) =>
                        (tool.observations ?? []).map((observation) => (
                          <li key={`${tool.toolCallId}-${observation.field}`}>
                            {observation.label}: {observation.value}
                          </li>
                        )),
                      )}
                    </ul>
                  ) : null}
                  {latestResult.developerPreview.awsOperations.length > 0 ? (
                    <ul className={styles.previewList}>
                      {latestResult.developerPreview.awsOperations.map((operation) => (
                        <li key={`${operation.operation}-${operation.table}`}>
                          {operation.provenance} {operation.operation} {operation.table}
                        </li>
                      ))}
                    </ul>
                  ) : null}
                </div>
              ) : null}
              {error ? (
                <div className={styles.error}>
                  <ErrorState
                    error={error}
                    subject="the Copilot response"
                    onRetry={
                      retryable
                        ? () => {
                            setError(null);
                          }
                        : undefined
                    }
                  />
                  {error instanceof CopilotContractError ? (
                    <p className={styles.errorCode}>
                      {describeApiError(error)}
                    </p>
                  ) : null}
                </div>
              ) : null}
            </div>
            <div className={styles.composer}>
              <CopilotComposer
                value={draft}
                pending={pending}
                onChange={setDraft}
                onSubmit={() => {
                  void submit();
                }}
              />
            </div>
          </div>
        </Panel>

        <Panel
          title="Evidence"
          meta={latestResult ? latestResult.state : 'idle'}
          className={styles.evidence}
        >
          <EvidencePanel result={latestResult} />
        </Panel>
      </div>
    </div>
  );
}
