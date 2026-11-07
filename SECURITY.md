# Step 1 security checks

The required `security` matrix runs on ordinary PRs, `ci-full` PRs, main pushes
and manual full runs. It uses `contents: read`, no persisted checkout credentials,
cloud authentication, OIDC, production services or deployment. Public package,
advisory and Terraform policy downloads are permitted. Findings and incomplete
scans fail the job and the aggregate gate. No severity threshold, blanket
suppression, ignored exit code or advisory allowlist is configured.

| Scanner | Pinned version | Scope |
| --- | --- | --- |
| Gitleaks | 8.30.1 | All available Git history (`fetch-depth: 0`) and tracked/nonignored source; built-in rules, inline allow comments disabled |
| pip-audit | 2.10.1 | Every test/CI/infrastructure/live-validation requirements manifest, all Lambda requirements, root developer snapshot |
| npm audit | npm 11.6.2 on Node 22 | Complete dashboard lockfile including development, peer and optional dependencies |
| Trivy config | 0.76.1 | All `envs/**/*.tf` and `modules/**/*.tf`; every severity; no state, tfvars, provider initialization or AWS calls |

Gitleaks history scanning uses exactly five reviewed `.gitleaksignore`
fingerprints for confirmed synthetic fixtures (G1-G5 in `SECURITY_FINDINGS.md`).
The full commit/file/rule/line entries were extracted from Gitleaks 8.30.1.
Both workflow policy and scanner execution reject missing, duplicate, altered
or additional entries. Current-tree scanning uses an empty ignore file, so
these exceptions cannot exempt newly introduced working-tree material. Built-in
rules, all-history scope and disabled inline allow comments remain in force.

Gitleaks and Trivy Windows ZIPs have SHA-256 checks against official release
checksums in `scripts/ci_security.py`. pip-audit and npm are installed at exact
versions from their public registries, in isolated ignored tool directories.
Update versions/checksums deliberately and rerun scanner/policy regressions.

Python manifests resolve independently with transitive dependencies (different
Lambda runtimes have conflicting pins). Comment-only manifests are explicitly
reported as standard-library-only. The root UTF-16 developer freeze targets a
newer Python than CI: its fully pinned declared versions are queried directly
without installing or executing them. It is not claimed as transitive resolution;
the CI, Lambda and live-validation manifests receive full resolution separately.
Unresolved or unauditable dependencies are errors, never clean scans.

The root snapshot is a security-maintained developer/notebook reference, not
the Python 3.11 CI or Lambda installation entry point. It already contains
NumPy requiring Python >=3.12; CI installs `tests/requirements-ci.txt`, while
Lambda builds use their separate manifests. Retain snapshot auditing when
maintaining it; retirement or replacement requires explicit ownership review.
The current audit reports all twelve manifests independently. Existing owners
cannot disappear or become empty without review; only the historical transform
manifest is approved as stdlib-only. New supported requirements files are
automatically audited, and unclassified Python packaging manifests fail closed.

Run locally from the repository root with Python 3.11 and Node 22 on PATH:

```powershell
foreach ($scan in @('secrets','python-dependencies','npm-dependencies','terraform-security')) {
    python scripts/ci_security.py bootstrap --scan $scan
    if ($LASTEXITCODE -ne 0) { throw "Scanner setup failed: $scan" }
    python scripts/ci_security.py run --scan $scan
    # Exit 0 = clean, 1 = findings, 2 = incomplete/setup error. Inspect each result.
}
python scripts/run_offline_tests.py tests/unit/ci -q
python scripts/ci.py policy
```

Raw scanner stdout/stderr and raw reports remain in disposable local storage and
are not printed or uploaded. Published `ci-security-*` artifacts contain only
allowlisted identifiers, package versions, advisories/fixes and file/rule locations.
Secret matches, source lines, Terraform resolved values and Git author data are
excluded. Scanner credentials are removed from child environments. Artifacts
expire after seven days; summaries give status and finding counts only.

Review sanitized `scan.json` findings to remediate dependency advisories and
Terraform risks. Secret candidates require private verification and, if real,
revocation/rotation; deleting a value from the current tree does not remove it
from history. Never paste matched values into issues, logs or tracked reports.
Do not change production security behavior or blanket-ignore findings to obtain
a green CI. A reviewed remediation is required before the security gate can pass.
The local finding inventory is recorded in [SECURITY_FINDINGS.md](SECURITY_FINDINGS.md),
with verification evidence in [CI_SECURITY_VALIDATION.md](CI_SECURITY_VALIDATION.md).

The existing offline test guards and AWS/plan-dependent Step 2 exclusions remain
unchanged. Future AWS integration must use a separate authorized job family.
