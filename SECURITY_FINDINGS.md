# Local security findings

Dependency and Terraform findings below include the 2026-11-05 remediation. npm audit is clean. Trivy findings were reviewed and left unchanged for infrastructure-owner approval. The Gitleaks investigation and approved fingerprint follow-up are recorded below. These are local results, not a GitHub-hosted result. Public advisory feeds change over time.

## Secret candidates ? investigation and current status

Investigated 2026-11-03 without printing matched values or calling any service.
All ten original `generic-api-key` findings are **confirmed synthetic fixtures**:
five distinct source occurrences, each reported once from history and once from
the working tree. The historical lines were read using captured `git show`
output and matched exactly to the original working-tree lines. No genuine
credential or uncertain candidate was identified; no service revocation/rotation
is indicated by this evidence. No live credential-validity check was performed.

The 2026-11-03 approval covered exactly five historical fingerprints. A
GitHub-hosted run on 2026-11-07, with `fetch-depth: 0`, found two further
historical `generic-api-key` candidates. Both were verified as synthetic
idempotency fixtures and added as G11 and G12. With those seven fingerprints,
history scanning still uses built-in rules and the working tree still has an
empty ignore file. The reproduction and clean result are in the 2026-11-07
section below. No credential rotation or history rewrite was required.

| ID | Original scope and location | Classification / potentially usable | Correction / final disposition |
| --- | --- | --- | --- |
| G1 | History `c191249913ecb8a91f10ed41d90dbd439dbaa8bd`, `tests/contracts/test_sprint3c_scoped_read.py:598` | Synthetic offline binding identifiers; no authentication capability | Historical evidence unchanged; exact fingerprint exception approved and active |
| G2 | History `c191249913ecb8a91f10ed41d90dbd439dbaa8bd`, `tests/contracts/test_sprint3c_scoped_read.py:600` | Synthetic copy of the same binding inputs; no authentication capability | Historical evidence unchanged; exact fingerprint exception approved and active |
| G3 | History `8f801061fa3cf5efc07ebfe7139fed4e09678abe`, `tests/contracts/test_master_specialist_runtime_contract.py:132` | Synthetic default idempotency identifier; no authentication capability | Historical evidence unchanged; exact fingerprint exception approved and active |
| G4 | History `9a003c2e54c88a11dcc0d9f753f1453dbff724cb`, `dashboard/src/api/operationalApi.test.ts:114` | Unsigned Base64 JSON pagination data with an airport field; not a credential | Historical evidence unchanged; exact fingerprint exception approved and active |
| G5 | History `9a003c2e54c88a11dcc0d9f753f1453dbff724cb`, `dashboard/src/api/operationalApi.test.ts:195` | Unsigned Base64 JSON pagination data with an offset field; not a credential | Historical evidence unchanged; exact fingerprint exception approved and active |
| G6 | Original working tree, `dashboard/src/api/operationalApi.test.ts:128` (same material as G4) | Synthetic pagination fixture; can represent pagination data, cannot grant authorization | Construct readable JSON and encode with `btoa(JSON.stringify(...))`; identical encoded bytes; resolved |
| G7 | Original working tree, `dashboard/src/api/operationalApi.test.ts:209` (same material as G5) | Synthetic pagination fixture; can represent pagination data, cannot grant authorization | Construct readable JSON and encode with `btoa(JSON.stringify(...))`; identical encoded bytes including escaping-sensitive characters; resolved |
| G8 | Original working tree, `tests/contracts/test_master_specialist_runtime_contract.py:132` (same material as G3) | Synthetic default idempotency identifier; no authentication capability | Short explicit test nonce; existing identity, replay and authorization assertions preserved; resolved |
| G9 | Original working tree, `tests/contracts/test_sprint3c_scoped_read.py:598` (same material as G1) | Synthetic offline binding identifiers; no authentication capability | Multiline argument formatting only; identical Python AST; resolved |
| G10 | Original working tree, `tests/contracts/test_sprint3c_scoped_read.py:600` (same material as G2) | Synthetic duplicate binding inputs; no authentication capability | Multiline argument formatting only; identical Python AST; resolved |

### Evidence and behavior preservation

- G1/G2/G9/G10 occur in
  `test_server_scope_wins_over_caller_region_and_caller_authorized`, using
  `bind_offline_aws_read_client`. These are fixed idempotency/invocation/correlation
  fixture inputs, not provider keys. The complete module AST is identical before
  and after formatting, so all security assertions and values are unchanged.
- G3/G8 occur in the `_envelope` helper used to construct
  `SpecialistRequestEnvelope`. The fixture literal had exactly one occurrence,
  no literal-coupled assertions, and is an idempotency field, not authorization.
  Only that nonce changed; envelope/identity/security assertions remain intact.
- G4/G5/G6/G7 decode to unsigned JSON with typed pagination fields, not an API
  key, signed token or authentication header. `stubFetch` supplies a `vi.fn`
  response and `clientWith` injects it as `fetchImpl`; no service receives the
  fixtures. The encoded strings are unchanged at runtime. Pagination data may
  be syntactically usable as a cursor but conveys no authentication authority.
- Captured historical source confirms the same helper/test context for every
  historical occurrence. No historical content was changed or deleted.

### Approved exact historical fingerprint exceptions

The user explicitly approved only these five confirmed synthetic findings.
Fingerprints below were extracted from the actual Gitleaks 8.30.1 JSON report
before enabling exceptions; each was checked against its complete commit,
file, `generic-api-key` rule and line. No fingerprint was inferred or invented.

| Finding | Exact emitted fingerprint | Reviewed justification |
| --- | --- | --- |
| G1 | `c191249913ecb8a91f10ed41d90dbd439dbaa8bd:tests/contracts/test_sprint3c_scoped_read.py:generic-api-key:598` | Offline binding identifiers; source formatting correction preserves the entire module AST. |
| G2 | `c191249913ecb8a91f10ed41d90dbd439dbaa8bd:tests/contracts/test_sprint3c_scoped_read.py:generic-api-key:600` | Repeated synthetic binding inputs in the same offline authorization test. |
| G3 | `8f801061fa3cf5efc07ebfe7139fed4e09678abe:tests/contracts/test_master_specialist_runtime_contract.py:generic-api-key:132` | Default idempotency nonce in a test envelope; no authentication authority. |
| G4 | `9a003c2e54c88a11dcc0d9f753f1453dbff724cb:dashboard/src/api/operationalApi.test.ts:generic-api-key:114` | Unsigned airport pagination JSON injected only into a stubbed fetch client. |
| G5 | `9a003c2e54c88a11dcc0d9f753f1453dbff724cb:dashboard/src/api/operationalApi.test.ts:generic-api-key:195` | Unsigned offset pagination JSON injected only into a stubbed fetch client. |
| G11 | `972780efbcc51fb0005767bddbbd819cb4406d56:tests/contracts/test_sprint3d_auth_grants.py:generic-api-key:426` | Placeholder idempotency nonce in an offline grant envelope. Added 2026-11-07. |
| G12 | `972780efbcc51fb0005767bddbbd819cb4406d56:tests/contracts/test_sprint3d_offline_validation.py:generic-api-key:1493` | Synthetic policy idempotency nonce in an offline validation binding. Added 2026-11-07. |

`.gitleaksignore` contains exactly those seven entries, with individual comments.
G1-G5 are unchanged. `validate_gitleaksignore` requires the exact reviewed set
and cardinality: missing, altered, duplicate or extra entries fail policy and
scanner execution.
The validated entries are copied to an isolated Gitleaks ignore file for
**history only**; working-tree scanning receives an explicitly empty ignore file.
No whole-file, directory, commit or rule exclusion is configured.

All refs/history (`--log-opts=--all`), built-in rules (`useDefault = true`),
100% output redaction, disabled inline allow comments, stripped credential/config
environment variables and fail-on-findings exit handling remain intact. Reports
expose fingerprint metadata only, never matched fixture values.

### GitHub history findings, 2026-11-07

GitHub Actions run `42396968336` on PR 15 used Gitleaks 8.30.1 and failed the
secrets job. The sanitized report could not be downloaded without credentials
(the artifact API returned 401). The same `scripts/ci_security.py` command was
rerun locally after making the missing history reachable. Before G11 and G12
were added, that scan completed with **exit 1 and exactly two findings**, both
`generic-api-key`, both scope `history`, and both on commit
`972780efbcc51fb0005767bddbbd819cb4406d56`. The working tree was clean. The
scanner-emitted fingerprints are the G11 and G12 rows above. No other rule or
commit was reported, including commits through `c68742c` on
`ai-operations-copilot`.

The earlier local scan did not see these commits. `actions/checkout` with
`fetch-depth: 0` fetches every branch. This clone had no
`sprint3d-five-agent-integration` ref, whose tip is `972780e`, and
`origin/ai-operations-copilot` was stale at `a30cb31`. That stale tip does not
contain the Sprint 3D commits. `git log --all` cannot report a commit that no
local ref can reach. The files are not on `ci-step1`.

Neither matched value is printed here. Both are `idempotency_key` literals in
tests added by that commit:

- G11 is in `_issued_envelope_placeholder`. The literal is 19 characters,
  hyphenated, and made only of the same fixture prefix as the other
  idempotency keys in that file, an explicit placeholder word, and a sprint
  tag. Entropy is 3.54, just above the rule threshold. Shorter sibling keys
  in the same file stay below it. The field is an envelope nonce, not an
  authorization header or provider client secret.
- G12 is in `test_native_live_ops_historical_and_decision_contracts`. The
  literal is also 19 characters and hyphenated. Its short label is reused in
  that test for step, owner, attempt, grant-nonce, fingerprint, request, and
  planning identifiers. Entropy is 3.64. The call builds an offline server
  binding and later uses `FakeDynamoClient` and `LocalHarnessGrantIssuer`.
  It has no provider prefix and is not a signing key.

The same history contains `tests/contracts/_rsa_test_key.txt`. Gitleaks did
not report it. The file is three decimal integers used as a modulus, the
public exponent 65537, and a private exponent to sign offline JWTs for
`idp.wilvor.test`. The branch test `test_rsa_material_is_disposable_and_not_packaged`
asserts that this material stays in `tests/contracts` and is not packaged into
Lambda. It is not covered by an exception. It is not on the `ci-step1` tree.

With G11 and G12 present, the same Gitleaks 8.30.1 command completed **exit 0,
zero findings**, and recorded `approved_history_exceptions: 7`. An unrelated
synthetic canary at an unignored path then failed that command with exit 1:
one new working-tree `generic-api-key` finding, zero history findings, and no
matched value in the sanitized report. The canary file was removed, and the
following scan was clean again. The seven exceptions did not exempt it.

npm and Terraform were remediated or explicitly held for approval on
2026-11-05. See the sections below. Python dependency remediation is also
recorded below.

## Dashboard dependencies

npm audit 11.6.2: 22 affected package records (including transitive dependency effects). Fix suggestions are the registry response, not tested upgrade recommendations; do not run `npm audit fix --force` blindly. Upgrade compatible dependency families and regenerate/retest the lockfile, keeping Vitest and its coverage provider aligned.

| Package | Severity | Affected range | Advisories/dependency causes | Suggested fix |
| --- | --- | --- | --- | --- |
| `@eslint-community/eslint-utils` | moderate | `*` | eslint | `{"isSemVerMajor": true, "name": "typescript-eslint", "version": "8.14.0"}` |
| `@eslint/config-array` | moderate | `*` | minimatch | `{"isSemVerMajor": true, "name": "typescript-eslint", "version": "8.14.0"}` |
| `@eslint/eslintrc` | moderate | `0.0.1 || >=0.1.1` | minimatch | `{"isSemVerMajor": true, "name": "typescript-eslint", "version": "8.14.0"}` |
| `@typescript-eslint/eslint-plugin` | moderate | `*` | @typescript-eslint/parser, @typescript-eslint/type-utils, @typescript-eslint/utils, eslint | `{"isSemVerMajor": true, "name": "typescript-eslint", "version": "8.14.0"}` |
| `@typescript-eslint/parser` | moderate | `*` | @typescript-eslint/typescript-estree, eslint | `{"isSemVerMajor": true, "name": "typescript-eslint", "version": "8.14.0"}` |
| `@typescript-eslint/type-utils` | moderate | `>=5.9.2-alpha.0` | @typescript-eslint/typescript-estree, @typescript-eslint/utils, eslint | `{"isSemVerMajor": true, "name": "typescript-eslint", "version": "8.14.0"}` |
| `@typescript-eslint/typescript-estree` | moderate | `>=6.16.0` | minimatch | `{"isSemVerMajor": true, "name": "typescript-eslint", "version": "8.14.0"}` |
| `@typescript-eslint/utils` | moderate | `*` | @eslint-community/eslint-utils, @typescript-eslint/typescript-estree, eslint | `{"isSemVerMajor": true, "name": "typescript-eslint", "version": "8.14.0"}` |
| `@vitest/coverage-v8` | moderate | `<=4.1.10` | test-exclude, vitest | `{"isSemVerMajor": true, "name": "@vitest/coverage-v8", "version": "5.0.5"}` |
| `@vitest/mocker` | moderate | `<=4.1.11` | https://github.com/advisories/GHSA-82fw-gwwq-j7x9, https://github.com/advisories/GHSA-qxg6-89mg-rqv8 | `{"isSemVerMajor": true, "name": "vitest", "version": "5.0.5"}` |
| `brace-expansion` | high | `<=1.1.20 || 4.0.0 - 5.0.11` | https://github.com/advisories/GHSA-6j4f-fj2g-mc7p, https://github.com/advisories/GHSA-q2hr-2g5m-vwhr, https://github.com/advisories/GHSA-qhr7-859c-m2p7 | `true` |
| `eslint` | moderate | `0.7.1 - 2.0.0-rc.1 || >=4.1.0` | @eslint-community/eslint-utils, @eslint/config-array, @eslint/eslintrc, minimatch | `{"isSemVerMajor": true, "name": "typescript-eslint", "version": "8.14.0"}` |
| `eslint-plugin-react-refresh` | moderate | `*` | eslint | `false` |
| `glob` | moderate | `3.0.0 - 14.0.1` | https://github.com/advisories/GHSA-3g3m-2fjr-6pw4, minimatch | `{"isSemVerMajor": true, "name": "@vitest/coverage-v8", "version": "5.0.5"}` |
| `maplibre-gl` | critical | `<=6.4.0` | https://github.com/advisories/GHSA-jrc7-96c5-q579 | `{"isSemVerMajor": true, "name": "maplibre-gl", "version": "6.16.0"}` |
| `minimatch` | moderate | `<11.0.1` | https://github.com/advisories/GHSA-g3hv-75wg-m249 | `{"isSemVerMajor": true, "name": "typescript-eslint", "version": "8.14.0"}` |
| `source-map-js` | high | `1.0.0 - 1.2.1` | https://github.com/advisories/GHSA-68fv-2mgg-jv7q | `true` |
| `test-exclude` | moderate | `4.2.2 || >=5.0.0` | glob, minimatch | `{"isSemVerMajor": true, "name": "@vitest/coverage-v8", "version": "5.0.5"}` |
| `tinypool` | critical | `<=2.1.1` | https://github.com/advisories/GHSA-5gmw-xhrv-c9v3, https://github.com/advisories/GHSA-85c8-ppgw-ccpr | `{"isSemVerMajor": true, "name": "vitest", "version": "5.0.5"}` |
| `typescript-eslint` | moderate | `*` | @typescript-eslint/eslint-plugin, @typescript-eslint/parser, @typescript-eslint/typescript-estree, @typescript-eslint/utils, eslint | `{"isSemVerMajor": true, "name": "typescript-eslint", "version": "8.14.0"}` |
| `vite` | high | `<=6.4.3` | https://github.com/advisories/GHSA-587h-7wmj-h5pm, https://github.com/advisories/GHSA-698v-4r3x-3vcv, https://github.com/advisories/GHSA-9jrq-w75r-8gcw, https://github.com/advisories/GHSA-r825-w8mm-5pxw, https://github.com/advisories/GHSA-rq7h-c2jc-7f22 | `true` |
| `vitest` | critical | `0.0.95 - 4.1.10` | @vitest/mocker, https://github.com/advisories/GHSA-82fw-gwwq-j7x9, tinypool | `{"isSemVerMajor": true, "name": "vitest", "version": "5.0.5"}` |

`true` means npm reports a fix available; `false` means it supplied none. Object fixes identify the package/version npm proposes, sometimes a major change.

### Remediated 2026-11-05

A fresh CI scan of the previous lockfile, `python scripts/ci_security.py run --scan npm-dependencies` with npm 11.6.2, reported **17** affected records. Five eslint-family records from the 2026-11-01 snapshot were no longer in the advisory feed. The same command after the changes below reports **zero findings, exit 0**.

| Package | Scope | Before | After | Reason |
| --- | --- | --- | --- | --- |
| `maplibre-gl` | production | 5.24.0 | 6.4.1 | GHSA-jrc7-96c5-q579, critical XSS in `DOM.sanitize()`. 6.4.1 is the first patched release. v5 has no fix. |
| `vitest`, `@vitest/coverage-v8` | development tests | 3.2.7 / 3.2.7 | 4.1.12 / 4.1.12 | GHSA-82fw-gwwq-j7x9 is fixed in 4.1.11. GHSA-qxg6-89mg-rqv8 is fixed in 4.1.12. Vitest 4 removes `tinypool`, which clears both critical tinypool advisories. Vitest 5 was not required. The two packages stay on the same version. |
| `vite` | development build | 6.4.3 | 6.4.5 | High `launch-editor` and `server.fs` advisories. 6.4.4 is itself affected by GHSA-8g5g-mxqh-grgj. 6.4.5 is the newest Vite 6 release. |
| `brace-expansion` | development transitive | 1.1.18 and 5.x below 5.0.12 | 1.1.21 and 5.0.12 | High brace-expansion DoS advisories. Updated inside existing parent ranges. |
| `source-map-js` | development transitive | 1.2.1 | 1.2.2 | GHSA-68fv-2mgg-jv7q. Updated inside the existing parent range. |
| `minimatch` under `@typescript-eslint/typescript-estree` | development lint | 10.2.6 | 11.0.4 | GHSA-g3hv-75wg-m249 affects `>=7.0.0 <11.0.1`. typescript-eslint 8.72.1 still depends on `minimatch ^10.2.2`, so no compatible upstream release exists. A package override applies only under that parent. ESLint's `minimatch` 3.1.5 is outside the advisory range and was not overridden. |

`glob` and `test-exclude` left the tree because `@vitest/coverage-v8` 4.1.12 no longer depends on them. MapLibre 6 required a namespace import, a Vite worker URL, and `zoomLevelsToOverscale: undefined` so vector-tile placement stays on the v5 behavior. Vitest 4 mocks are constructable, so the test API stub type accepts them. Operational API contracts were not changed.

Node 22.23.4 and its bundled npm 10.9.9 completed `npm ci`, ESLint (the existing Fast Refresh warning only), TypeScript, **342 passed** dashboard tests, and the production build. Measured Vitest 4 V8 coverage is 81.80% lines and statements, 72.26% branches, and 83.75% functions. The earlier 85.68% / 84.69% / 86.94% figures used Vitest 3's less accurate remapper.

## Terraform static configuration

Trivy 0.76.1: 99 findings (72 low, 25 medium, two high). Findings require infrastructure-owner review and scoped Terraform remediation; this CI change does not alter resource semantics. Static findings do not prove exploitability or the state of a deployed environment.

### Reviewed 2026-11-05, not modified

The CI command was rerun on the current tree. The result is unchanged: **99 findings, 2 high, 25 medium, 72 low, exit 1**. No Terraform source, provider lock, IAM policy, encryption setting, retention setting, log configuration, or network exposure was changed. No Trivy ignore was added. Each rule changes something this task is not allowed to change automatically:

| Rule | Severity | Count | Why it was not changed |
| --- | --- | --- | --- |
| AWS-0132 | HIGH | 2 | `aircraft_archive` and `historical_facts` already use SSE-S3 (`AES256`) and block public access. The check requires a customer managed KMS key. That changes encryption and the IAM rights of every reader and writer. Existing contracts require `AES256`. |
| AWS-0024 | MEDIUM | 18 | Point-in-time recovery is set to false, or to `enable_point_in_time_recovery` whose default is false. Turning it on changes backup retention, cost, and how long TTL-expired items can be restored. |
| AWS-0090 | MEDIUM | 6 | Versioning changes object deletion and retention cost. Static contracts require versioning to be absent on several of these buckets. |
| AWS-0001 | MEDIUM | 1 | API Gateway stage access logging adds a log destination, an IAM write path, and cost. |
| AWS-0017 | LOW | 24 | Customer managed keys for CloudWatch Logs change key management. |
| AWS-0025 | LOW | 18 | Customer managed keys for DynamoDB change key management and table access. |
| AWS-0066 | LOW | 22 | X-Ray is not configured. Enabling it adds Lambda tracing permissions and cost. |
| AWS-0089 | LOW | 7 | S3 server access logging needs a log target, a bucket policy, and a retention decision. |
| AWS-0098 | LOW | 1 | A customer managed key for Secrets Manager changes key management. |

These remain blockers for a fully green Trivy gate until an infrastructure owner approves the specific IAM, encryption, retention, logging, and cost changes. They are not silent risk acceptances and they are not scanner suppressions. Six other buckets also set `sse_algorithm = "AES256"` and were not in this Trivy result; they were left on SSE-S3 as well.

### Owner decision requested 2026-11-07

No Terraform resource, Trivy ignore, or severity threshold was changed. GitHub
run `42396968336` again failed `terraform-security`. The 2026-11-05 inventory
is still the current reviewed result: **99 findings, 2 high, 25 medium, 72 low**.
The rows below are the decision the infrastructure owner needs to make. They
separate baseline debt already present on `ci-step1` from any later regression.

| Control | Rule / severity | Resources | Actual risk | Existing protection | Recommended remediation | Impact | Architectural approval | Narrow acceptance defensible? |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Customer-managed S3 keys | AWS-0132 / HIGH | 2 (`aircraft_archive`, `historical_facts`) | Objects are encrypted, but a bucket-wide AWS-managed key cannot be denied per application through a customer key policy. This is not public plaintext exposure. | SSE-S3 (`AES256`) and all four public-access-block settings. `historical_facts` is also versioned, with bucket-owner enforcement and lifecycle expiry. `aircraft_archive` has lifecycle expiry. Contracts require `AES256`. | Introduce a customer-managed key only with matching IAM for every reader and writer, then change the contracts that require SSE-S3. | Encryption, IAM, and KMS request cost. A bad key policy can cause availability loss. Retention is unchanged. | Yes | Yes, as explicit baseline debt while SSE-S3 and public-access blocks remain. Not acceptable for a new bucket that lacks encryption or a public-access block. |
| DynamoDB point-in-time recovery | AWS-0024 / MEDIUM | 18 tables | Accidental overwrite or delete cannot be restored to an earlier point. Enabling recovery also keeps a restore path for TTL-expired items. | `enable_point_in_time_recovery` defaults to false, or the resource sets recovery false. Tables still have the service's default encryption. | Turn recovery on per table after deciding which tables are systems of record and which are rebuildable. | Backup cost and a longer effective retention window. Restore improves availability of data. IAM and encryption are unchanged. | Yes | Yes for rebuildable operational tables. A system-of-record table needs its own decision, not this group acceptance. |
| S3 versioning | AWS-0090 / MEDIUM | 6 buckets | Current-object delete or overwrite is permanent on those buckets. | Public access is blocked and SSE-S3 is set. `historical_facts` already has versioning and is not in this count. Static contracts require versioning to be absent on several of these buckets. | Enable versioning together with noncurrent-object expiration so retention stays bounded. | Retention and storage cost. Old versions remain available. IAM and encryption are unchanged. | Yes, where a contract forbids versioning | Yes only where the bucket is a replayable archive and the contract still forbids versioning. Do not accept a missing public-access block under this rule. |
| API Gateway stage access logs | AWS-0001 / MEDIUM | 1 stage | The HTTP stage has no access log, so request metadata is harder to investigate. This does not by itself open the API. | The stage sets throttling and auto-deploy. The module also has a Lambda log group. This repository does not define CloudTrail. | Add a log destination, a documented format, an IAM write path, and a retention period. | Logging, retention, cost, and IAM. Encryption of that log group becomes the AWS-0017 decision. | Yes | A short deferral is defensible while Lambda logs exist. It should name an end date. It is a weak long-term acceptance. |
| CloudWatch Logs customer-managed keys | AWS-0017 / LOW | 24 log groups | Logs use default service encryption. A customer key would allow an additional deny on decrypt. | Log groups already exist. Some modules set retention as low as 3 days. | One customer-managed key, a logs-service key policy, and `kms_key_id` on each group. | Encryption, IAM, key cost, and availability if the key is disabled. Retention is a separate choice. | Yes | Yes, as baseline debt. Default encryption remains. A new unencrypted-by-policy log group should still fail review. |
| DynamoDB customer-managed keys | AWS-0025 / LOW | 18 tables | Tables use default encryption rather than a key this application controls. | Server-side encryption blocks are present without a customer key. | Customer-managed keys and IAM grants for table access. | Encryption, IAM, and cost. Key loss can make tables unavailable. | Yes | Yes, as baseline debt alongside the S3 SSE-S3 decision. Not acceptable if encryption is removed. |
| Lambda X-Ray | AWS-0066 / LOW | 22 functions | No distributed traces. This is a visibility gap, not a data-exposure finding. | Functions run without `tracing_config`. | Enable tracing and add the Lambda X-Ray permissions. | Cost, IAM, and a small latency and availability effect. No encryption or retention change. | Yes | Yes. Tracing is not a confidentiality control. |
| S3 server access logs | AWS-0089 / LOW | 7 buckets | Credentialed object access is not recorded by a bucket log. Public access is already blocked. | Public-access blocks and SSE-S3. No `logging` block. No CloudTrail resource is defined in this repository. | A log bucket, a bucket policy, and a retention lifecycle. | Logging, retention, cost, and bucket-policy IAM. | Yes | Only if the owner confirms an account CloudTrail data-event trail that covers these buckets. This repository does not show that trail. |
| Secrets Manager customer-managed key | AWS-0098 / LOW | 1 secret (`opensky_credentials`) | The secret uses the service default key. `recovery_window_in_days = 0` means deletion is immediate; that is a separate availability choice and was not the Trivy finding. | The secret exists for OpenSky credentials and is not public. | A customer-managed key and key-policy access for the readers that already use the secret. | Encryption, IAM, and cost. Disabling the key can stop ingestion. | Yes | Yes for the key-management finding alone. Do not treat immediate recovery-window deletion as accepted by this row. |

**Baseline debt versus a future regression.** The 99 findings above are the candidate baseline. They describe controls that are absent or left at service defaults on the current `ci-step1` tree. A later change is a regression when it adds a resource to one of these rules, raises a finding's severity, removes SSE-S3 or a public-access block, or introduces a rule that is not in this table. An owner acceptance of this table does not accept that later change.

**How to keep the gate blocking after approval.** Do not lower Trivy's severity threshold and do not add rule-wide ignores. Owner approval, if it is given later, should replace this development deferral with an explicit acceptance record. Until then, the temporary baseline below is only a CI scheduling deferral.

### Temporary development deferral, 2026-11-07

**Warning:** `security/terraform-security-baseline.json` is not a permanent replacement for infrastructure hardening. It does not accept security risk, does not clear production, and does not mean an infrastructure owner has reviewed these controls. The 99 findings remain unresolved: 2 high, 25 medium, and 72 low. They must be remediated or formally accepted after AI Copilot infrastructure development is finished.

Trivy 0.76.1 still scans every `envs/**/*.tf` and `modules/**/*.tf` file at every severity, with built-in rules and an empty ignore file. Its raw result stays in `scan.json` with status `findings`, exit 1, and all 99 finding records. The CI policy then compares those records to the baseline. A match lets the security job and the quality gate continue. The gate repeats that comparison itself and rejects a report whose policy block does not match the findings.

Each baseline entry is the rule, repository-relative Terraform path, Trivy resource, source line, and severity. The line distinguishes repeated findings that share one module resource. The file has `entries: 99` and `risk_acceptance: false`. CI only reads it. A run cannot regenerate it.

The policy fails when a finding is not an exact entry, when severity rises at the same location, when the same location appears twice, when the baseline is missing or malformed, when the scanner errors, or when the scanned Terraform file list does not match the repository. A finding that disappears may pass and stays listed as resolved. Python, npm, and Gitleaks scans still require zero unapproved findings.

To remove an entry after the resource is actually changed:

1. Change the Terraform resource in a reviewed infrastructure change. Do not do that as part of this CI deferral.
2. Run `python scripts/ci_security.py run --scan terraform-security`.
3. Confirm the raw finding is gone and the policy lists that identity under `resolved`.
4. Delete that one object from `security/terraform-security-baseline.json` and set `entries` to the new list length.
5. Update the regression that records the reviewed count and severity totals.
6. Do not replace the file with a fresh scanner dump, and do not delete it to force a green result. An empty or missing baseline fails CI.

| Rule | Count | Severity | Finding / required remediation |
| --- | --- | --- | --- |
| AWS-0001 | 1 | MEDIUM | API Gateway stages for V1 and V2 should have access logging enabled; Enable logging for API Gateway stages |
| AWS-0017 | 24 | LOW | CloudWatch log groups should be encrypted using CMK; Enable CMK encryption of CloudWatch Log Groups |
| AWS-0024 | 18 | MEDIUM | Point in time recovery should be enabled to protect DynamoDB table; Enable point in time recovery |
| AWS-0025 | 18 | LOW | DynamoDB tables should use at rest encryption with a Customer Managed Key; Enable server side encryption with a customer managed key |
| AWS-0066 | 22 | LOW | Lambda functions should have X-Ray tracing enabled; Enable tracing |
| AWS-0089 | 7 | LOW | S3 Bucket Logging; Add a logging block to the resource to enable access logging |
| AWS-0090 | 6 | MEDIUM | S3 Data should be versioned; Enable versioning to protect against accidental/malicious removal or modification |
| AWS-0098 | 1 | LOW | Secrets Manager should use customer managed keys; Use customer managed keys |
| AWS-0132 | 2 | HIGH | S3 encryption should use Customer Managed Keys; Use SSE-KMS with a customer managed key (CMK) |

Exact source locations:

| Rule | File:line | Resource |
| --- | --- | --- |
| AWS-0017 | `modules/active_alerts/processor.tf:79` | `module.active_alerts` |
| AWS-0066 | `modules/active_alerts/processor.tf:85` | `module.active_alerts` |
| AWS-0024 | `modules/active_alerts/tables.tf:58` | `module.active_alerts` |
| AWS-0025 | `modules/active_alerts/tables.tf:61` | `module.active_alerts` |
| AWS-0017 | `modules/aircraft_foundation/aircraft_current_state_writer.tf:12` | `module.aircraft_foundation` |
| AWS-0066 | `modules/aircraft_foundation/aircraft_current_state_writer.tf:85` | `module.aircraft_foundation` |
| AWS-0017 | `modules/aircraft_foundation/aircraft_raw_processor.tf:14` | `module.aircraft_foundation` |
| AWS-0066 | `modules/aircraft_foundation/aircraft_raw_processor.tf:100` | `module.aircraft_foundation` |
| AWS-0024 | `modules/aircraft_foundation/main.tf:173` | `module.aircraft_foundation` |
| AWS-0025 | `modules/aircraft_foundation/main.tf:127` | `module.aircraft_foundation` |
| AWS-0089 | `modules/aircraft_foundation/main.tf:43` | `module.aircraft_foundation` |
| AWS-0090 | `modules/aircraft_foundation/main.tf:43` | `module.aircraft_foundation` |
| AWS-0132 | `modules/aircraft_foundation/main.tf:62` | `module.aircraft_foundation` |
| AWS-0017 | `modules/aircraft_foundation/opensky_poller.tf:24` | `module.aircraft_foundation` |
| AWS-0066 | `modules/aircraft_foundation/opensky_poller.tf:95` | `module.aircraft_foundation` |
| AWS-0098 | `modules/aircraft_foundation/secrets.tf:1` | `module.aircraft_foundation` |
| AWS-0017 | `modules/airport_assessment/processor.tf:122` | `module.airport_assessment` |
| AWS-0066 | `modules/airport_assessment/processor.tf:132` | `module.airport_assessment` |
| AWS-0024 | `modules/airport_assessment/tables.tf:47` | `module.airport_assessment` |
| AWS-0025 | `modules/airport_assessment/tables.tf:50` | `module.airport_assessment` |
| AWS-0017 | `modules/airport_status/processor.tf:28` | `module.airport_status` |
| AWS-0066 | `modules/airport_status/processor.tf:104` | `module.airport_status` |
| AWS-0024 | `modules/airport_status/tables.tf:59` | `module.airport_status` |
| AWS-0025 | `modules/airport_status/tables.tf:62` | `module.airport_status` |
| AWS-0017 | `modules/encounter/processor.tf:179` | `module.encounter` |
| AWS-0066 | `modules/encounter/processor.tf:187` | `module.encounter` |
| AWS-0024 | `modules/encounter/tables.tf:64` | `module.encounter` |
| AWS-0025 | `modules/encounter/tables.tf:1` | `module.encounter` |
| AWS-0017 | `modules/historical_analytics/monitoring.tf:1` | `module.historical_analytics` |
| AWS-0089 | `modules/historical_analytics/results_bucket.tf:1` | `module.historical_analytics` |
| AWS-0090 | `modules/historical_analytics/results_bucket.tf:1` | `module.historical_analytics` |
| AWS-0017 | `modules/historical_facts/coverage.tf:1` | `module.historical_facts` |
| AWS-0017 | `modules/historical_facts/coverage.tf:203` | `module.historical_facts` |
| AWS-0066 | `modules/historical_facts/coverage.tf:139` | `module.historical_facts` |
| AWS-0066 | `modules/historical_facts/coverage.tf:280` | `module.historical_facts` |
| AWS-0017 | `modules/historical_facts/firehose.tf:8` | `module.historical_facts` |
| AWS-0017 | `modules/historical_facts/transform.tf:1` | `module.historical_facts` |
| AWS-0066 | `modules/historical_facts/transform.tf:12` | `module.historical_facts` |
| AWS-0089 | `modules/historical_facts_data/bucket.tf:1` | `module.historical_facts_data` |
| AWS-0132 | `modules/historical_facts_data/bucket.tf:27` | `module.historical_facts_data` |
| AWS-0089 | `modules/metar/archive.tf:5` | `module.metar` |
| AWS-0090 | `modules/metar/archive.tf:5` | `module.metar` |
| AWS-0017 | `modules/metar/poller.tf:24` | `module.metar` |
| AWS-0066 | `modules/metar/poller.tf:97` | `module.metar` |
| AWS-0017 | `modules/metar/processor.tf:24` | `module.metar` |
| AWS-0066 | `modules/metar/processor.tf:114` | `module.metar` |
| AWS-0024 | `modules/metar/tables.tf:35` | `module.metar` |
| AWS-0025 | `modules/metar/tables.tf:1` | `module.metar` |
| AWS-0001 | `modules/operational_api/api.tf:479` | `module.operational_api` |
| AWS-0017 | `modules/operational_api/api.tf:207` | `module.operational_api` |
| AWS-0066 | `modules/operational_api/api.tf:310` | `module.operational_api` |
| AWS-0017 | `modules/projection/processor.tf:218` | `module.projection` |
| AWS-0066 | `modules/projection/processor.tf:235` | `module.projection` |
| AWS-0024 | `modules/projection/tables.tf:40` | `module.projection` |
| AWS-0024 | `modules/projection/tables.tf:101` | `module.projection` |
| AWS-0024 | `modules/projection/tables.tf:142` | `module.projection` |
| AWS-0025 | `modules/projection/tables.tf:1` | `module.projection` |
| AWS-0025 | `modules/projection/tables.tf:50` | `module.projection` |
| AWS-0025 | `modules/projection/tables.tf:112` | `module.projection` |
| AWS-0017 | `modules/recommendations/processor.tf:68` | `module.recommendations` |
| AWS-0066 | `modules/recommendations/processor.tf:74` | `module.recommendations` |
| AWS-0024 | `modules/recommendations/tables.tf:64` | `module.recommendations` |
| AWS-0025 | `modules/recommendations/tables.tf:67` | `module.recommendations` |
| AWS-0017 | `modules/risk/processor.tf:129` | `module.risk` |
| AWS-0066 | `modules/risk/processor.tf:142` | `module.risk` |
| AWS-0024 | `modules/risk/tables.tf:51` | `module.risk` |
| AWS-0025 | `modules/risk/tables.tf:1` | `module.risk` |
| AWS-0089 | `modules/runway_metadata/archive.tf:7` | `module.runway_metadata` |
| AWS-0090 | `modules/runway_metadata/archive.tf:7` | `module.runway_metadata` |
| AWS-0017 | `modules/runway_metadata/loader.tf:41` | `module.runway_metadata` |
| AWS-0066 | `modules/runway_metadata/loader.tf:133` | `module.runway_metadata` |
| AWS-0024 | `modules/runway_metadata/tables.tf:21` | `module.runway_metadata` |
| AWS-0025 | `modules/runway_metadata/tables.tf:24` | `module.runway_metadata` |
| AWS-0089 | `modules/sigmet/archive.tf:5` | `module.sigmet` |
| AWS-0090 | `modules/sigmet/archive.tf:5` | `module.sigmet` |
| AWS-0017 | `modules/sigmet/hazard_coordinates_processor.tf:24` | `module.sigmet` |
| AWS-0066 | `modules/sigmet/hazard_coordinates_processor.tf:114` | `module.sigmet` |
| AWS-0017 | `modules/sigmet/hazard_station_candidates_processor.tf:24` | `module.sigmet` |
| AWS-0066 | `modules/sigmet/hazard_station_candidates_processor.tf:120` | `module.sigmet` |
| AWS-0017 | `modules/sigmet/poller.tf:24` | `module.sigmet` |
| AWS-0066 | `modules/sigmet/poller.tf:83` | `module.sigmet` |
| AWS-0017 | `modules/sigmet/processor.tf:24` | `module.sigmet` |
| AWS-0066 | `modules/sigmet/processor.tf:151` | `module.sigmet` |
| AWS-0024 | `modules/sigmet/tables.tf:52` | `module.sigmet` |
| AWS-0024 | `modules/sigmet/tables.tf:92` | `module.sigmet` |
| AWS-0024 | `modules/sigmet/tables.tf:132` | `module.sigmet` |
| AWS-0024 | `modules/sigmet/tables.tf:166` | `module.sigmet` |
| AWS-0024 | `modules/sigmet/tables.tf:206` | `module.sigmet` |
| AWS-0025 | `modules/sigmet/tables.tf:1` | `module.sigmet` |
| AWS-0025 | `modules/sigmet/tables.tf:62` | `module.sigmet` |
| AWS-0025 | `modules/sigmet/tables.tf:102` | `module.sigmet` |
| AWS-0025 | `modules/sigmet/tables.tf:143` | `module.sigmet` |
| AWS-0025 | `modules/sigmet/tables.tf:176` | `module.sigmet` |
| AWS-0089 | `modules/station_reference/archive.tf:7` | `module.station_reference` |
| AWS-0090 | `modules/station_reference/archive.tf:7` | `module.station_reference` |
| AWS-0017 | `modules/station_reference/loader.tf:31` | `module.station_reference` |
| AWS-0066 | `modules/station_reference/loader.tf:105` | `module.station_reference` |
| AWS-0024 | `modules/station_reference/tables.tf:43` | `module.station_reference` |
| AWS-0025 | `modules/station_reference/tables.tf:46` | `module.station_reference` |

## Python dependencies - remediated 2026-11-05

Actual CI command `python scripts/ci_security.py run --scan python-dependencies`,
using pip-audit **2.10.1**, completed **all 12 contexts, zero findings, exit 0**.
All 14 previous records / 13 unique advisories are resolved. The original feed
repeated one certifi record; no advisory was excluded or suppressed.

### Dependency scope and versions

| Package | Previous root pin / original local CI venv | New root pin / local CI venv | Logical parents and use |
| --- | --- | --- | --- |
| certifi | 2026.6.17 / 2026.6.17 | 2026.10.18 / 2026.10.18 | TLS trust data; transitive of Requests and HTTPX; used by local development, CI HTTP clients and operator-only Anthropic tooling |
| tornado | 6.5.7 / not installed | 6.6 / not installed | Notebook/kernel tooling via ipykernel and jupyter_client; no application import, CI requirement or Lambda package requirement |
| urllib3 | 2.7.0 / 2.7.0 | 2.8.0 / 2.8.0 | HTTP transport; transitive of Requests and botocore; local development and CI SDK dependencies |

All three are syntactically direct pins in root `requirements.txt`, but their
logical role in this freeze is transitive library support. The repository does
not directly import them in application code. Lambda packaging manifests contain
only their own h3 pins (plus one explicit stdlib-only historical transform).
Those boundaries, SDK/provider interfaces and production code were not changed.
The deployed AWS runtime's supplied SDK is outside this repository snapshot and
was not inspected or changed through AWS operations.

Root `requirements.txt` is retained as a security-maintained developer/notebook
reference snapshot. Evidence: its 44 fully pinned packages include ipykernel,
Jupyter, NumPy and pandas; root h3 differs from the Lambda-specific pins; CI and
packaging scripts use their own manifests. No tracked notebook was found.
It is not the authoritative Python 3.11 CI or Lambda installation manifest.
NumPy 2.5.0 already requires Python >=3.12, while only Python 3.11 is installed
here. Regenerating the full snapshot on 3.11 would require unrelated changes.
Only the three vulnerable pins were upgraded; the snapshot was neither retired
nor removed from scanning. UTF-16LE BOM, all 44 CRLF lines and every other byte
are preserved, verified against the original Git blob.

Published package metadata confirms the patched certifi supports Python >=3.7,
urllib3 >=3.10 and tornado >=3.11. A public-registry resolver dry run succeeds
with unchanged Requests 2.34.2, botocore 1.43.36, ipykernel 7.3.0 and
jupyter_client 8.9.1 plus all three patched versions. Their relevant constraints
are certifi >=2023.5.7, urllib3 <3 (botocore also excludes 2.2.0), and tornado
>=6.4.1. No full Python >=3.12 notebook environment installation is claimed.
Release metadata: [certifi](https://pypi.org/project/certifi/2026.10.18/),
[tornado](https://pypi.org/project/tornado/6.6/),
[urllib3](https://pypi.org/project/urllib3/2.8.0/).

### Each original advisory

| Previous package/version | Advisory | Aliases | Fix versions originally reported | Installed/pinned remediation | Outcome |
| --- | --- | --- | --- | --- | --- |
| certifi 2026.6.17 | PYSEC-2026-4320 | GHSA-6q48-6c7w-9gw2 | 2026.10.18 | certifi 2026.10.18 | Resolved in actual audit |
| tornado 6.5.7 | PYSEC-2026-3928 | CVE-2026-82397, GHSA-mpf4-983q-p7j4 | 6.5.8 | tornado 6.6 | Resolved in actual audit |
| tornado 6.5.7 | GHSA-chx6-46f5-w4vp | - | 6.5.9 | tornado 6.6 | Resolved in actual audit |
| tornado 6.5.7 | GHSA-c2m8-h5v5-343r | - | 6.5.9 | tornado 6.6 | Resolved in actual audit |
| tornado 6.5.7 | GHSA-3hv7-mjh2-fv65 | - | 6.5.9 | tornado 6.6 | Resolved in actual audit |
| tornado 6.5.7 | GHSA-wwv5-g3v4-889x | - | 6.5.8 | tornado 6.6 | Resolved in actual audit |
| tornado 6.5.7 | GHSA-8423-8fgw-73vq | - | 6.5.8 | tornado 6.6 | Resolved in actual audit |
| tornado 6.5.7 | CVE-2026-116577 | GHSA-pcg2-g6cj-q92c | 6.6 | tornado 6.6 | Resolved in actual audit |
| tornado 6.5.7 | CVE-2026-115927 | GHSA-r4j5-9xpp-3985 | 6.6 | tornado 6.6 | Resolved in actual audit |
| tornado 6.5.7 | CVE-2026-116578 | GHSA-pw9p-v7r6-gx4p | 6.6 | tornado 6.6 | Resolved in actual audit |
| urllib3 2.7.0 | PYSEC-2026-4177 | CVE-2026-97689, GHSA-vxq7-64xx-v4gw | 2.8.0 | urllib3 2.8.0 | Resolved in actual audit |
| urllib3 2.7.0 | PYSEC-2026-4176 | CVE-2026-97688, GHSA-gh4c-6fx4-qh6g | 2.8.0 | urllib3 2.8.0 | Resolved in actual audit |
| urllib3 2.7.0 | PYSEC-2026-4175 | CVE-2026-97687, GHSA-8988-9cw3-xx77 | 2.8.0 | urllib3 2.8.0 | Resolved in actual audit |

### Audit coverage and bypass protection

Each test/CI/infrastructure/live-validation manifest is now audited independently,
including the two previously covered only through `-r` includes. All Lambda
manifests and the root snapshot remain audited. Twelve contexts replace the ten
previous report contexts; no original owner was removed. Existing owners are
mandatory. New requirements manifests in supported locations are discovered
without a fixed ceiling; unknown packaging formats/locations fail for review.
Empty requirements cannot be reclassified as stdlib-only except for the already
reviewed historical-transform path. Root snapshot mode is fixed to the exact root
path and requires nonempty, fully pinned requirements. Unsafe options, unclassified
includes, unresolved/skipped dependencies, empty advisory responses and service
errors fail the scan. No vulnerability IDs, severity levels or package findings
are ignored.

Audited contexts (resolved contexts include transitive dependencies; snapshot
mode queries its 44 declared pins without installing the Python >=3.12 freeze):

| Manifest | Mode | Dependencies | Status |
| --- | --- | --- | --- |
| `functions/aircraft_raw_processor/requirements.txt` | resolved-transitive | 1 | complete |
| `functions/encounter/processor/requirements.txt` | resolved-transitive | 1 | complete |
| `functions/historical_facts/transform/requirements.txt` | stdlib-only | 0 | complete |
| `functions/projection/processor/requirements.txt` | resolved-transitive | 1 | complete |
| `functions/station_reference/loader/requirements.txt` | resolved-transitive | 1 | complete |
| `functions/weather/sigmet/hazard_station_candidates_processor/requirements.txt` | resolved-transitive | 1 | complete |
| `functions/weather/sigmet/processor/requirements.txt` | resolved-transitive | 1 | complete |
| `requirements.txt` | declared-pinned-snapshot | 44 | complete |
| `tests/requirements-ci.txt` | resolved-transitive | 56 | complete |
| `tests/requirements-infrastructure.txt` | resolved-transitive | 56 | complete |
| `tests/requirements-live-validation.txt` | resolved-transitive | 15 | complete |
| `tests/requirements-test.txt` | resolved-transitive | 53 | complete |

### Verification and remaining Python blockers

A fresh isolated Python 3.11 environment installed `tests/requirements-ci.txt`
with patched certifi/urllib3 and passed `pip check` and **1,352 offline unit
cases**. The existing local test venv was also patched only for those two
installed packages, passed `pip check`, and passed **111 CI regressions**.
Tornado is absent from the CI environments; its root pin/parent constraints were
validated with the public resolver. All Gitleaks exceptions and safety tests
remain intact.

A temporary requirements file containing known-vulnerable certifi 2026.6.17 was
resolved/audited by the real pip-audit binary. It exited 1 and its parsed findings
were rejected by the actual CI security-report validator, with the other scanner
reports represented only by isolated test controls. No vulnerable package was
installed and no repository manifest was contaminated. Separate regressions
verify scan exit/status propagation and manifest reclassification failures.

**Remaining Python dependency blockers: none.** Full installation of the reference
notebook freeze requires a newer Python and was not attempted. npm and Terraform
findings above remain unchanged and their scanners were not rerun in this phase.
