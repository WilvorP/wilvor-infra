import styles from './CloudWatchMarkdown.module.css';

export interface CloudWatchMarkdownProps {
  markdown: string;
}

/**
 * Safe rendering of CloudWatch text-widget markdown.
 *
 * CloudWatch text widgets in this account are headings plus a single
 * descriptive line. HTML in the source is shown as text, not executed.
 */
export function CloudWatchMarkdown({ markdown }: CloudWatchMarkdownProps) {
  const lines = markdown.split('\n');

  return (
    <div className={styles.markdown}>
      {lines.map((line, index) => {
        if (line.startsWith('# ')) {
          return <h3 key={index}>{line.slice(2)}</h3>;
        }

        if (line.startsWith('## ')) {
          return <h4 key={index}>{line.slice(3)}</h4>;
        }

        return <p key={index}>{line.length > 0 ? line : '\u00a0'}</p>;
      })}
    </div>
  );
}
