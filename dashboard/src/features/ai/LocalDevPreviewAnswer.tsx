import type {
  CopilotDeveloperPreview,
  CopilotDeveloperToolResult,
  CopilotQueryResult,
} from '@/api/aiContracts';

import styles from './ConversationTranscript.module.css';

/**
 * Display-only age between a stored position timestamp and the request clock.
 * This does not recompute freshness_status. That label is copied from the
 * stored aircraft row, where ingestion assigned it against the writer clock.
 */
export function describeObservationAge(
  positionTimeUtc: string | null,
  requestTimeUtc: string,
): string | null {
  if (!positionTimeUtc) {
    return null;
  }
  const observedMs = Date.parse(positionTimeUtc);
  const requestMs = Date.parse(requestTimeUtc);
  if (!Number.isFinite(observedMs) || !Number.isFinite(requestMs)) {
    return null;
  }

  const deltaSeconds = Math.floor(Math.abs(requestMs - observedMs) / 1000);
  const hours = Math.floor(deltaSeconds / 3600);
  const minutes = Math.floor((deltaSeconds % 3600) / 60);
  const seconds = deltaSeconds % 60;
  const elapsed = (hours > 0 ? String(hours) + 'h ' : '') +
    String(minutes) + 'm ' + String(seconds) + 's';

  if (observedMs > requestMs) {
    return 'Stored position time is ' + elapsed +
      ' after the request clock; clock alignment needs review.';
  }
  return 'Stored position time was ' + elapsed + ' before the request.';
}

const SPECIALIST_STOPPED_BEFORE_VERIFICATION =
  'Live operations stopped before verification. No operational facts are verified.';

function verificationCaption(text: string | null): string {
  if (!text) {
    return 'No completed specialist claim verification text was returned. No operational facts are treated as verified.';
  }
  if (text === SPECIALIST_STOPPED_BEFORE_VERIFICATION) {
    return 'Specialist execution stopped before verification. No operational facts are verified. The diagnostic text is in technical details and is not an admitted claim.';
  }
  return 'Deterministic specialist claim verification returned diagnostic text. It is in technical details, it is not the Copilot answer, and it does not admit a claim.';
}

function AircraftObservation({
  tool,
  requestTimeUtc,
}: {
  tool: CopilotDeveloperToolResult;
  requestTimeUtc: string;
}) {
  const age = describeObservationAge(tool.positionTimeUtc, requestTimeUtc);
  const observations = tool.observations ?? [];

  return (
    <section className={styles.previewEntity} aria-label="Stored aircraft observation">
      <p className={styles.previewEntityTitle}>
        Stored aircraft {tool.aircraftId ?? 'not reported'}
        {' — '}{tool.recordState.toLowerCase()} record
      </p>
      <p className={styles.previewDetail}>
        Callsign: {tool.callsign ?? 'not reported'}
      </p>
      <p className={styles.previewDetail}>
        Position timestamp: {tool.positionTimeUtc ?? 'not reported'}
      </p>
      {age ? (
        <>
          <p className={styles.previewDetail} data-testid="copilot-observation-age">
            {age}
          </p>
          <p className={styles.previewDetail}>
            This observation age uses the request clock only. It is not the
            evaluation clock, and it is not a source-freshness verdict.
          </p>
        </>
      ) : (
        <p className={styles.previewDetail}>
          Observation age is not calculated.
        </p>
      )}
      {observations.length > 0 ? (
        <dl className={styles.previewFields}>
          {observations.map((observation) => (
            <div key={observation.field} className={styles.previewField}>
              <dt>{observation.label}</dt>
              <dd>{observation.value}</dd>
            </div>
          ))}
        </dl>
      ) : (
        <p className={styles.previewDetail}>No observed scalar fields returned.</p>
      )}
      <p className={styles.previewCaution} data-testid="copilot-stored-freshness">
        Stored freshness label: {tool.freshnessStatus ?? 'not reported'}.
        This stored label is not an independent source-freshness check.
      </p>
      {tool.recordState === 'STALE' ? (
        <p className={styles.previewCaution}>
          Stored record state is stale. It is not current operational status.
        </p>
      ) : null}
      {tool.recordState === 'MISSING' ? (
        <p className={styles.previewCaution}>
          No stored aircraft row was returned. A missing row is not an
          inactivity finding and it is not a hazard assessment.
        </p>
      ) : null}
    </section>
  );
}

function StatusLayers({
  result,
  preview,
}: {
  result: CopilotQueryResult;
  preview: CopilotDeveloperPreview;
}) {
  const routeKinds = preview.routeKinds ?? [];
  const toolOutcomes = preview.toolResults.map(
    (tool) =>
      tool.toolName + ' ' + tool.status + ', ' + tool.recordState.toLowerCase() + ' record',
  );
  const execution = [
    preview.specialists.length > 0
      ? 'Specialists named on the plan: ' + preview.specialists.join(', ') + '.'
      : 'No specialist was named on this plan.',
    toolOutcomes.length > 0
      ? 'Tool outcomes: ' + toolOutcomes.join('; ') + '.'
      : 'No specialist tool outcome was returned.',
    ...preview.blockedSpecialists.map(
      (blocked) => blocked.specialist + ' not executed: ' + blocked.reason,
    ),
    preview.modelErrors.length > 0
      ? 'Model diagnostics: ' + preview.modelErrors.join(', ') + '.'
      : '',
    preview.toolErrors.length > 0
      ? 'Tool diagnostics: ' + preview.toolErrors.join(', ') + '.'
      : '',
    'Execution status is not production evidence admission.',
  ].filter((sentence) => sentence.length > 0).join(' ');

  return (
    <dl className={styles.previewLayers}>
      <div>
        <dt>Master route authorization</dt>
        <dd>
          {preview.routeStatus}
          {routeKinds.length > 0 ? ' (' + routeKinds.join(', ') + ')' : ''}.
          {' '}Terminal decision: {preview.terminalDecision ?? 'not reported'}.
          {' '}Route authorization is not production evidence admission.
        </dd>
      </div>
      <div>
        <dt>Specialist execution</dt>
        <dd>{execution}</dd>
      </div>
      <div>
        <dt>Deterministic specialist claim verification</dt>
        <dd>{verificationCaption(preview.specialistStatusText)}</dd>
      </div>
      <div>
        <dt>Production evidence admission</dt>
        <dd>
          Production evidence admission remains closed.
          Claims admitted: no.
          Source authenticity: {result.sourceAuthenticity}.
          Production authority: {result.productionAuthority}.
          Production AI active: no.
        </dd>
      </div>
    </dl>
  );
}

/**
 * Human-readable local observations, not a second authoritative answer.
 * Facts shown here come from the validated local preview envelope.
 * assistantText, claims admission, and production authority stay unchanged.
 */
export function LocalDevPreviewAnswer({ result }: { result: CopilotQueryResult }) {
  const preview = result.developerPreview;
  if (result.standing.transport !== 'LOCAL_DEV_PREVIEW' || !preview) {
    return null;
  }

  const aircraft = preview.toolResults.filter(
    (tool) => tool.toolName === 'get_aircraft_operational_context',
  );
  const encounters = preview.toolResults.filter(
    (tool) => tool.toolName === 'search_current_encounters',
  );
  const other = preview.toolResults.filter(
    (tool) =>
      tool.toolName !== 'get_aircraft_operational_context' &&
      tool.toolName !== 'search_current_encounters',
  );

  return (
    <section className={styles.previewWrap} data-testid="copilot-developer-preview">
      <p className={styles.previewHeading}>Local developer observations</p>
      <p className={styles.previewCaution}>
        NOT PRODUCTION. UNADMITTED DEV EVIDENCE. These are operator-authenticated
        DEV reads, not admitted operational claims. Source authenticity is not
        established.
      </p>
      {preview.toolResults.length === 0 ? (
        <p className={styles.previewDetail}>
          No specialist tool observations were returned. No aircraft or hazard
          status is inferred.
        </p>
      ) : null}
      {aircraft.map((tool) => (
        <AircraftObservation
          key={tool.toolCallId}
          tool={tool}
          requestTimeUtc={result.applicationRequestTimeUtc}
        />
      ))}
      {encounters.map((tool) => (
        <p key={tool.toolCallId} className={styles.previewDetail}>
          Encounter query for aircraft {tool.aircraftId ?? 'not reported'}:
          {' '}{tool.encounterCandidateCount === null || tool.encounterCandidateCount === undefined
            ? 'count not reported'
            : String(tool.encounterCandidateCount) + ' returned candidate rows'}.
          {' '}A returned count, including zero, does not mean the aircraft is
          free from hazards.
        </p>
      ))}
      {other.map((tool) => (
        <p key={tool.toolCallId} className={styles.previewDetail}>
          Tool {tool.toolName}: {tool.status} ({tool.recordState}).
          No additional operational conclusion is inferred.
        </p>
      ))}
      <StatusLayers result={result} preview={preview} />
      <details className={styles.previewTechnical}>
        <summary>Specialist verification and technical details</summary>
        <p>{preview.notice}</p>
        {preview.specialistStatusText ? (
          <p>
            Deterministic specialist claim verification (local DEV diagnostic,
            not the Copilot answer): {preview.specialistStatusText}
          </p>
        ) : null}
        <p>Developer diagnostic narrative: {preview.answer}</p>
        <p>Model calls recorded: {preview.modelOperations.length}</p>
        {preview.missingEvidence.length > 0 ? (
          <p>Missing evidence: {preview.missingEvidence.join(', ')}</p>
        ) : null}
        {preview.staleEvidence.length > 0 ? (
          <p>Stale evidence: {preview.staleEvidence.join(', ')}</p>
        ) : null}
        {preview.partialEvidence.length > 0 ? (
          <p>Partial evidence: {preview.partialEvidence.join(', ')}</p>
        ) : null}
        {preview.awsOperations.length > 0 ? (
          <ul>
            {preview.awsOperations.map((operation, index) => (
              <li key={operation.operation + '-' + operation.table + '-' + String(index)}>
                {operation.provenance} {operation.operation} {operation.table}
              </li>
            ))}
          </ul>
        ) : null}
      </details>
    </section>
  );
}
