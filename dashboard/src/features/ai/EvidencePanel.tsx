import { Link } from 'react-router-dom';

import { DataField, DataFieldGrid } from '@/components/DataField';
import { Notice } from '@/components/QueryState';
import { StatusPill } from '@/components/StatusPill';
import type { CopilotQueryResult } from '@/api/aiContracts';
import { formatUtcDateTime, humaniseToken, NOT_REPORTED } from '@/utils/format';
import { presentFreshness } from '@/utils/status';

import {
  emptyEvidenceCaption,
  evidenceIdentity,
  evidenceInvestigationPath,
  presentLimitationCode,
  presentSourceAuthenticity,
} from './display';

import styles from './EvidencePanel.module.css';

export interface EvidencePanelProps {
  result: CopilotQueryResult | null;
}

/**
 * Display-only aircraft and hazard evidence.
 *
 * Empty pages stay empty. They are not a verified-zero, a LOW risk, or an
 * impacted-aircraft count. Missing source version and freshness stay
 * not-reported.
 */
export function EvidencePanel({ result }: EvidencePanelProps) {
  if (result === null) {
    return (
      <div className={styles.empty} data-testid="copilot-evidence-idle">
        <p className={styles.emptyTitle}>No evidence selected</p>
        <p className={styles.emptyDetail}>
          Evidence from the latest Copilot result appears here. Source
          authenticity stays not established until the integration owner
          admits it.
        </p>
      </div>
    );
  }

  const emptyCaption = emptyEvidenceCaption(result.pageEmpty);

  return (
    <div className={styles.panel} data-testid="copilot-evidence">
      <div className={styles.summary}>
        <StatusPill
          presentation={presentSourceAuthenticity()}
          prefix="Authenticity"
        />
        <p className={styles.authenticity}>
          Source authenticity: not established. Production authority: denied.
        </p>
        {result.truncated || result.hasMore ? (
          <Notice tone="warning">
            Evidence is truncated or has additional pages. A partial page is
            not complete coverage.
          </Notice>
        ) : null}
        {emptyCaption ? <Notice>{emptyCaption}</Notice> : null}
      </div>

      {result.evidence.length === 0 ? null : (
        <ul className={styles.list}>
          {result.evidence.map((item) => {
            const path = evidenceInvestigationPath(item);

            return (
              <li key={item.toolCallId} className={styles.item}>
                <header className={styles.itemHeader}>
                  <span className={styles.kind}>
                    {humaniseToken(item.entityKind)}
                  </span>
                  <span className={`${styles.identity} wv-numeric`}>
                    {evidenceIdentity(item)}
                  </span>
                  {item.freshnessBand ? (
                    <StatusPill
                      size="sm"
                      presentation={presentFreshness(item.freshnessBand)}
                    />
                  ) : (
                    <StatusPill
                      size="sm"
                      prefix="Freshness"
                      presentation={presentFreshness(null)}
                    />
                  )}
                </header>

                <DataFieldGrid columns={2}>
                  <DataField label="Entity id" value={item.entityId} numeric />
                  <DataField label="Callsign" value={item.callsign} numeric />
                  <DataField label="Aircraft" value={item.aircraftId} numeric />
                  <DataField label="Hazard" value={item.hazardId} numeric />
                  <DataField label="Encounter" value={item.encounterId} numeric />
                  <DataField label="Tool" value={item.toolName} numeric />
                  <DataField label="Source" value={item.sourceName} numeric />
                  <DataField
                    label="Source version"
                    value={item.sourceVersion}
                    numeric
                  />
                  <DataField
                    label="Observed"
                    value={formatUtcDateTime(item.observationTimeUtc)}
                    numeric
                  />
                  <DataField
                    label="Evaluated"
                    value={formatUtcDateTime(item.evaluationTimeUtc)}
                    numeric
                  />
                </DataFieldGrid>

                {item.limitationCodes.length > 0 ? (
                  <ul className={styles.itemLimits} aria-label="Evidence limitations">
                    {item.limitationCodes.map((code) => (
                      <li key={code}>{presentLimitationCode(code)}</li>
                    ))}
                  </ul>
                ) : null}

                {item.pageEmpty ? (
                  <Notice>
                    This evidence page is empty. That does not mint a
                    verified-zero claim.
                  </Notice>
                ) : null}

                {item.truncated || item.hasMore ? (
                  <p className={styles.pageNote}>
                    Pagination: truncated {item.truncated ? 'yes' : 'no'}, more
                    pages {item.hasMore ? 'yes' : 'no'}.
                  </p>
                ) : null}

                {path ? (
                  <Link className={styles.open} to={path}>
                    Open aircraft investigation
                  </Link>
                ) : null}
              </li>
            );
          })}
        </ul>
      )}

      <p className={styles.footer}>
        Unknown freshness stays {NOT_REPORTED}. Observation and evaluation
        clocks are displayed separately and are not merged.
      </p>
    </div>
  );
}
