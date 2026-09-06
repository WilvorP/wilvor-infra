import styles from './ConfigurationError.module.css';

export interface ConfigurationErrorProps {
  errors: readonly string[];
}

/**
 * Terminal configuration failure.
 *
 * Shown instead of the console when the API base URL is missing or invalid.
 * Without it the application would mount and then fail every query with an
 * opaque network error, which reads as an outage rather than a setup problem.
 */
export function ConfigurationError({ errors }: ConfigurationErrorProps) {
  return (
    <div className={styles.wrapper} role="alert">
      <div className={styles.card}>
        <p className={styles.eyebrow}>Wilvor Operations</p>
        <h1 className={styles.title}>Configuration required</h1>

        <p className={styles.intro}>
          The console cannot start because its environment configuration is
          incomplete.
        </p>

        <ul className={styles.list}>
          {errors.map((error) => (
            <li key={error} className={styles.item}>
              {error}
            </li>
          ))}
        </ul>

        <div className={styles.help}>
          <p className={styles.helpTitle}>To resolve</p>
          <pre className={styles.code}>
            {[
              'cp dashboard/.env.example dashboard/.env',
              '',
              '# then set VITE_WILVOR_API_BASE_URL from Terraform:',
              'cd envs/dev',
              'terraform output -raw operational_api_endpoint',
            ].join('\n')}
          </pre>
        </div>
      </div>
    </div>
  );
}
