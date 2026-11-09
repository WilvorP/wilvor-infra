"""Required Step 1 scanners. Public advisory feeds only; no cloud/runtime calls.

Raw scanner output is captured in disposable directories, never echoed or
uploaded. Reports contain allowlisted identifiers/locations, not secret values,
source lines, Terraform resolved values, author data or provider payloads.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]
SCANS = ("secrets", "python-dependencies", "npm-dependencies", "terraform-security")
VERSIONS = {"secrets": "8.30.1", "python-dependencies": "2.10.1", "npm-dependencies": "11.6.2", "terraform-security": "0.76.1"}
TERRAFORM_SCOPE = "all repository envs/modules .tf source, no state/tfvars or AWS access"
TERRAFORM_BASELINE = ROOT / "security" / "terraform-security-baseline.json"
TERRAFORM_DEFERRAL = (
    "Temporary development CI deferral. Not an infrastructure-owner risk acceptance and not production security clearance."
)
SEVERITY_RANK = {"UNKNOWN": 0, "LOW": 1, "MEDIUM": 2, "HIGH": 3, "CRITICAL": 4}
TOOLS = ROOT / "test-results" / "security-tools"
REPORTS = ROOT / "test-results" / "ci" / "security"
# Emitted by Gitleaks 8.30.1, verified against commit/file/rule/line. These
# historical synthetic fixtures alone are approved; additions require review.
APPROVED_GITLEAKS_FINGERPRINTS = frozenset({
    "c191249913ecb8a91f10ed41d90dbd439dbaa8bd:tests/contracts/test_sprint3c_scoped_read.py:generic-api-key:598",
    "c191249913ecb8a91f10ed41d90dbd439dbaa8bd:tests/contracts/test_sprint3c_scoped_read.py:generic-api-key:600",
    "8f801061fa3cf5efc07ebfe7139fed4e09678abe:tests/contracts/test_master_specialist_runtime_contract.py:generic-api-key:132",
    "9a003c2e54c88a11dcc0d9f753f1453dbff724cb:dashboard/src/api/operationalApi.test.ts:generic-api-key:114",
    "9a003c2e54c88a11dcc0d9f753f1453dbff724cb:dashboard/src/api/operationalApi.test.ts:generic-api-key:195",
    "972780efbcc51fb0005767bddbbd819cb4406d56:tests/contracts/test_sprint3d_auth_grants.py:generic-api-key:426",
    "972780efbcc51fb0005767bddbbd819cb4406d56:tests/contracts/test_sprint3d_offline_validation.py:generic-api-key:1493",
})
BINARIES = {
    "secrets": ("gitleaks", "https://github.com/gitleaks/gitleaks/releases/download/v8.30.1/gitleaks_8.30.1_windows_x64.zip",
                "d29144deff3a68aa93ced33dddf84b7fdc26070add4aa0f4513094c8332afc4e"),
    "terraform-security": ("trivy", "https://github.com/aquasecurity/trivy/releases/download/v0.76.1/trivy_0.76.1_windows-64bit.zip",
                           "1051f49cb22d5284f43425f72aad9cda63ca12ceb92e7caa06903490c82dc9bf"),
}


def environment():
    result = os.environ.copy()
    for key in list(result):
        if key.startswith(("AWS_", "WILVOR_RUN_LIVE", "GITLEAKS_", "TRIVY_", "PIP_", "NPM_CONFIG_", "npm_config_")) or key in {
            "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GITHUB_TOKEN", "GH_TOKEN", "NPM_TOKEN", "NODE_AUTH_TOKEN", "NODE_OPTIONS", "PYTHONPATH"
        }:
            result.pop(key)
    result.update(AWS_CONFIG_FILE=os.devnull, AWS_SHARED_CREDENTIALS_FILE=os.devnull,
                  AWS_EC2_METADATA_DISABLED="true", CHECKPOINT_DISABLE="1", PIP_CONFIG_FILE=os.devnull,
                  PIP_INDEX_URL="https://pypi.org/simple", PIP_ONLY_BINARY=":all:", PIP_DISABLE_PIP_VERSION_CHECK="1")
    return result


def invoke(command, *, cwd=ROOT, env=None):
    return subprocess.run([str(part) for part in command], cwd=cwd, env=env or environment(),
                          capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=1200)


def audit_python():
    return TOOLS / "pip-audit" / "Scripts" / "python.exe"


def bootstrap(scan):
    TOOLS.mkdir(parents=True, exist_ok=True)
    if scan in BINARIES:
        name, url, checksum = BINARIES[scan]
        folder = TOOLS / name
        folder.mkdir(exist_ok=True)
        archive = folder / "download.zip"
        if not archive.exists() or hashlib.sha256(archive.read_bytes()).hexdigest() != checksum:
            urllib.request.urlretrieve(url, archive)
        if hashlib.sha256(archive.read_bytes()).hexdigest() != checksum:
            raise RuntimeError("Scanner download checksum mismatch")
        with zipfile.ZipFile(archive) as zipped:
            (folder / f"{name}.exe").write_bytes(zipped.read(f"{name}.exe"))
    elif scan == "python-dependencies":
        if not audit_python().exists():
            result = invoke([sys.executable, "-m", "venv", TOOLS / "pip-audit"])
            if result.returncode:
                raise RuntimeError("Cannot create isolated pip-audit environment")
        result = invoke([audit_python(), "-m", "pip", "install", f"pip-audit=={VERSIONS[scan]}"])
        if result.returncode:
            raise RuntimeError("Pinned pip-audit installation failed (public PyPI)")
    else:
        result = invoke(["npm.cmd", "install", "--prefix", TOOLS / "npm", "--ignore-scripts", "--no-audit", "--no-fund", f"npm@{VERSIONS[scan]}"])
        if result.returncode:
            raise RuntimeError("Pinned npm scanner installation failed (public npm registry)")
    print(f"Prepared {scan}, pinned version {VERSIONS[scan]}")


def tracked_files():
    env = environment()
    # Process-scoped ownership exception only; never change Git configuration.
    env.update(GIT_CONFIG_COUNT="1", GIT_CONFIG_KEY_0="safe.directory", GIT_CONFIG_VALUE_0=str(ROOT))
    result = invoke(["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"], env=env)
    if result.returncode:
        raise RuntimeError("Cannot enumerate repository source files")
    files = []
    for name in sorted(set(result.stdout.split("\0")) - {""}):
        path = Path(name)
        if path.is_absolute() or ".." in path.parts or (ROOT / path).is_symlink():
            raise RuntimeError("Repository export contains an unsafe path/symlink")
        if (ROOT / path).is_file():
            files.append(path)
    return files


def secret_findings(entries, scope):
    return [{"scope": scope, "rule": item.get("RuleID"), "file": item.get("File"),
             "line": item.get("StartLine"), "commit": item.get("Commit", ""),
             "fingerprint": item.get("Fingerprint", "")}
            for item in entries]


def validate_gitleaksignore(path=None):
    path = Path(path) if path is not None else ROOT / ".gitleaksignore"
    if path.is_symlink():
        raise ValueError("Gitleaks exceptions must be a regular reviewed file")
    entries = tuple(line.strip() for line in path.read_text(encoding="utf-8").splitlines()
                    if line.strip() and not line.lstrip().startswith("#"))
    if len(entries) != 7 or set(entries) != APPROVED_GITLEAKS_FINGERPRINTS:
        raise ValueError("Gitleaks exceptions must equal the seven reviewed historical fingerprints exactly")
    return entries


def scan_secrets(temp):
    approved = validate_gitleaksignore()
    exe = TOOLS / "gitleaks" / "gitleaks.exe"
    env = environment()
    env.update(GIT_CONFIG_COUNT="1", GIT_CONFIG_KEY_0="safe.directory", GIT_CONFIG_VALUE_0=str(ROOT))
    shallow = invoke(["git", "rev-parse", "--is-shallow-repository"], env=env)
    if shallow.returncode or shallow.stdout.strip() != "false":
        raise RuntimeError("Complete Git history is required; checkout fetch-depth must be 0")
    source = temp / "source"
    source.mkdir()
    for relative in tracked_files():
        target = source / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / relative, target)
    config = temp / "gitleaks.toml"
    config.write_text("[extend]\nuseDefault = true\n", encoding="utf-8")
    ignored = temp / "empty-ignore"
    ignored.write_text("", encoding="utf-8")
    history_ignored = temp / "approved.gitleaksignore"
    history_ignored.write_text("\n".join(approved) + "\n", encoding="utf-8")
    findings = []
    for scope, arguments in (("history", ["git", str(ROOT), "--log-opts=--all"]), ("working-tree", ["dir", str(source)])):
        report = temp / f"gitleaks-{scope}.json"
        result = invoke([exe, *arguments, "--redact=100", "--no-banner", "--ignore-gitleaks-allow", "--config", config,
                         "--gitleaks-ignore-path", history_ignored if scope == "history" else ignored,
                         "--report-format=json", f"--report-path={report}", "--exit-code=1"], env=env)
        if result.returncode not in (0, 1) or not report.exists():
            raise RuntimeError(f"Gitleaks {scope} scan did not complete")
        entries = json.loads(report.read_text(encoding="utf-8")) or []
        if bool(entries) != bool(result.returncode):
            raise RuntimeError("Gitleaks exit/report mismatch")
        safe = secret_findings(entries, scope)
        for item in safe:
            if scope == "working-tree":
                item["file"] = Path(item["file"]).relative_to(source).as_posix()
        findings.extend(safe)
    return findings, [{"scope": "all Git history + tracked/nonignored source", "status": "complete",
                       "approved_history_exceptions": len(approved)}]


def requirement_lines(path):
    content = path.read_bytes()
    text = content.decode("utf-16" if content.startswith((b"\xff\xfe", b"\xfe\xff")) else "utf-8-sig")
    return [line.strip() for line in text.splitlines() if line.strip() and not line.lstrip().startswith("#")]


def python_findings(payload, context):
    findings = []
    for dependency in payload["dependencies"]:
        if dependency.get("skip_reason"):
            raise RuntimeError(f"Unaudited Python dependency in {context}")
        for vulnerability in dependency.get("vulns", []):
            findings.append({"context": context, "package": dependency["name"], "version": dependency["version"],
                             "advisory": vulnerability["id"], "aliases": vulnerability.get("aliases", []),
                             "fix_versions": vulnerability.get("fix_versions", [])})
    return findings


REQUIRED_PYTHON_MANIFESTS = frozenset({
    "requirements.txt", "tests/requirements-ci.txt", "tests/requirements-test.txt",
    "tests/requirements-infrastructure.txt", "tests/requirements-live-validation.txt",
    "functions/aircraft_raw_processor/requirements.txt", "functions/encounter/processor/requirements.txt",
    "functions/historical_facts/transform/requirements.txt", "functions/projection/processor/requirements.txt",
    "functions/station_reference/loader/requirements.txt",
    "functions/weather/sigmet/hazard_station_candidates_processor/requirements.txt",
    "functions/weather/sigmet/processor/requirements.txt",
})
STDLIB_PYTHON_MANIFESTS = frozenset({"functions/historical_facts/transform/requirements.txt"})


def python_manifest_paths():
    """Discover new requirements, but never silently reclassify/retire an owner."""
    files = tracked_files()
    manifests = []
    for path in files:
        name = path.name.casefold()
        if name.startswith("requirements") and name.endswith(".txt"):
            if not (len(path.parts) == 1 or (path.parts[0] == "tests" and len(path.parts) == 2)
                    or (path.parts[0] == "functions" and path.name == "requirements.txt")):
                raise RuntimeError("Unclassified Python manifest; review audit ownership before proceeding")
            manifests.append(path)
        elif name in {"pyproject.toml", "pipfile", "pipfile.lock", "poetry.lock", "uv.lock", "setup.py", "setup.cfg"}:
            raise RuntimeError("Unclassified Python packaging manifest; review audit ownership before proceeding")
    if not REQUIRED_PYTHON_MANIFESTS <= {path.as_posix() for path in manifests}:
        raise RuntimeError("A required Python manifest is missing; retirement/reclassification requires review")
    return sorted(set(manifests), key=lambda path: path.as_posix())


def scan_python(temp):
    # Audit each runtime independently: conflicting h3 pins must not be merged.
    paths = python_manifest_paths()
    findings, contexts = [], []
    for index, relative in enumerate(paths):
        lines = requirement_lines(ROOT / relative)
        context = relative.as_posix()
        print(f"Auditing Python manifest: {context}", flush=True)
        if not lines:
            if context not in STDLIB_PYTHON_MANIFESTS:
                raise RuntimeError("Empty Python manifest cannot be reclassified as stdlib-only without review")
            contexts.append({"manifest": context, "mode": "stdlib-only", "status": "complete", "dependencies": 0})
            continue
        if any(re.search(r"https?://|git\+|--index|--extra-index|--trusted-host", line, re.I) for line in lines):
            raise RuntimeError(f"Non-public dependency source requires explicit review: {context}")
        for line in lines:
            if line.startswith("-"):
                include = re.fullmatch(r"(?:-r\s*|--requirement\s+)([^\s]+)", line)
                if not include or (ROOT / relative.parent / include[1]).resolve() not in {(ROOT / path).resolve() for path in paths}:
                    raise RuntimeError("Python manifest options/includes require classified repository requirements")
        target = ROOT / relative
        extra = []
        mode = "resolved-transitive"
        if relative == Path("requirements.txt"):
            # This UTF-16 developer freeze is fully pinned, but targets a newer
            # Python than CI. Query its declared versions without executing it;
            # transitive CI/Lambda/live dependencies are audited above separately.
            if not all(re.fullmatch(r"[\w.\[\]-]+==[\w.+!-]+", line) for line in lines):
                raise RuntimeError("Developer snapshot is no longer fully pinned")
            target = temp / "developer-snapshot.txt"
            target.write_text("\n".join(lines), encoding="utf-8")
            extra = ["--no-deps", "--disable-pip"]
            mode = "declared-pinned-snapshot"
        report = temp / f"python-{index}.json"
        result = invoke([audit_python(), "-m", "pip_audit", "-r", target, "--strict", "--format=json", "--desc=off",
                         "--aliases=on", "--progress-spinner=off", "--output", report, *extra])
        if result.returncode not in (0, 1) or not report.exists():
            contexts.append({"manifest": context, "mode": mode, "status": "error", "reason": "Dependency resolution or advisory service failed"})
            continue
        payload = json.loads(report.read_text(encoding="utf-8"))
        if not payload.get("dependencies"):
            contexts.append({"manifest": context, "mode": mode, "status": "error", "reason": "Audit returned no dependencies for a nonempty manifest"})
            continue
        try:
            found = python_findings(payload, context)
        except RuntimeError:
            contexts.append({"manifest": context, "mode": mode, "status": "error", "reason": "An unaudited dependency was reported"})
            continue
        if not found and result.returncode:
            contexts.append({"manifest": context, "mode": mode, "status": "error", "reason": "Audit exit/report mismatch"})
            continue
        findings.extend(found)
        contexts.append({"manifest": context, "mode": mode, "status": "complete", "dependencies": len(payload["dependencies"])})
    return findings, contexts


def npm_findings(payload):
    if payload.get("error") or "vulnerabilities" not in payload or not payload.get("metadata", {}).get("dependencies", {}).get("total"):
        raise RuntimeError("npm advisory response is incomplete")
    findings = []
    for name, entry in sorted(payload["vulnerabilities"].items()):
        via = [{"advisory": value.get("url"), "title": value.get("title"), "range": value.get("range")}
               if isinstance(value, dict) else {"dependency": value} for value in entry.get("via", [])]
        fix = entry.get("fixAvailable")
        if isinstance(fix, dict):
            fix = {key: fix.get(key) for key in ("name", "version", "isSemVerMajor")}
        findings.append({"package": name, "severity": entry.get("severity"), "range": entry.get("range"),
                         "direct": entry.get("isDirect"), "via": via, "fix": fix})
    return findings


def scan_npm(temp):
    for name in ("package.json", "package-lock.json"):
        shutil.copyfile(ROOT / "dashboard" / name, temp / name)
    user_config, global_config = temp / "user.npmrc", temp / "global.npmrc"
    user_config.write_text("", encoding="utf-8")
    global_config.write_text("", encoding="utf-8")
    env = environment() | {"NPM_CONFIG_USERCONFIG": str(user_config), "NPM_CONFIG_GLOBALCONFIG": str(global_config),
                           "NPM_CONFIG_REGISTRY": "https://registry.npmjs.org/", "NODE_ENV": "development"}
    npm = TOOLS / "npm" / "node_modules" / "npm" / "bin" / "npm-cli.js"
    result = invoke(["node", npm, "audit", "--json", "--package-lock-only", "--ignore-scripts", "--include=dev", "--include=optional", "--include=peer"], cwd=temp, env=env)
    payload = json.loads(result.stdout)
    findings = npm_findings(payload)
    if result.returncode not in (0, 1) or bool(findings) != bool(result.returncode):
        raise RuntimeError("npm audit exit/report mismatch")
    return findings, [{"scope": "dashboard/package-lock.json (production + development + optional + peer)", "status": "complete"}]


def strict_json(text, label):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"Duplicate JSON key in {label}")
            result[key] = value
        return result
    try:
        return json.loads(text, object_pairs_hook=unique)
    except json.JSONDecodeError as error:
        raise ValueError(f"{label} is corrupted") from error


def terraform_sources():
    files = [path.as_posix() for path in tracked_files() if path.suffix == ".tf" and path.parts[0] in {"envs", "modules"}]
    if not files:
        raise RuntimeError("Terraform scan coverage is empty")
    return tuple(files)


def terraform_identity(item):
    if not isinstance(item, dict):
        raise ValueError("Terraform finding identity is incomplete")
    rule, file, resource, line, severity = (item.get("rule"), item.get("file"), item.get("resource"), item.get("line"), item.get("severity"))
    path = Path(file) if isinstance(file, str) else None
    if (not isinstance(rule, str) or not rule or not isinstance(resource, str) or not resource or severity not in SEVERITY_RANK
            or not isinstance(line, int) or isinstance(line, bool) or line < 1 or path is None or path.is_absolute()
            or ".." in path.parts or path.suffix != ".tf" or path.parts[0] not in {"envs", "modules"}):
        raise ValueError("Terraform finding identity is incomplete")
    return {"rule": rule, "file": path.as_posix(), "resource": resource, "line": line, "severity": severity}


def load_terraform_baseline(path=None):
    """Read the reviewed baseline. This never creates or rewrites it."""
    path = Path(path) if path is not None else TERRAFORM_BASELINE
    if path.is_symlink() or not path.is_file():
        raise ValueError("Terraform baseline is missing")
    payload = strict_json(path.read_text(encoding="utf-8"), "Terraform baseline")
    required = {"schema", "scanner", "version", "purpose", "risk_acceptance", "note", "entries", "findings"}
    findings = payload.get("findings") if isinstance(payload, dict) else None
    if (not isinstance(payload, dict) or set(payload) != required or payload.get("schema") != 1 or payload.get("scanner") != "trivy"
            or payload.get("version") != VERSIONS["terraform-security"] or payload.get("purpose") != "temporary-development-deferral"
            or payload.get("risk_acceptance") is not False or payload.get("note") != TERRAFORM_DEFERRAL
            or not isinstance(findings, list) or not findings or payload.get("entries") != len(findings)):
        raise ValueError("Terraform baseline is corrupted")
    identities, seen = [], set()
    for item in findings:
        if not isinstance(item, dict) or set(item) != {"rule", "file", "resource", "line", "severity"}:
            raise ValueError("Terraform baseline is corrupted")
        identity = terraform_identity(item)
        key = (identity["rule"], identity["file"], identity["resource"], identity["line"])
        if key in seen:
            raise ValueError("Terraform baseline contains a duplicate finding")
        seen.add(key)
        identities.append(identity)
    return tuple(identities)


def assess_terraform(findings, baseline=None):
    approved = tuple(baseline) if baseline is not None else load_terraform_baseline()
    if not isinstance(findings, list):
        raise ValueError("Terraform findings are missing")
    by_location = {(item["rule"], item["file"], item["resource"], item["line"]): item for item in approved}
    deferred, resolved, unexpected, increases, duplicates, seen = [], [], [], [], [], set()
    for raw in findings:
        item = terraform_identity(raw)
        location = (item["rule"], item["file"], item["resource"], item["line"])
        if location in seen:
            duplicates.append(item)
            continue
        seen.add(location)
        match = by_location.get(location)
        if match and match["severity"] == item["severity"]:
            deferred.append(item)
        elif match and SEVERITY_RANK[item["severity"]] > SEVERITY_RANK[match["severity"]]:
            increases.append(item)
        else:
            unexpected.append(item)
    for item in approved:
        if (item["rule"], item["file"], item["resource"], item["line"]) not in seen:
            resolved.append(item)
    order = lambda item: (item["file"], item["line"], item["rule"], item["resource"], item["severity"])
    return {
        "name": "temporary-development-deferral",
        "risk_acceptance": False,
        "baseline_entries": len(approved),
        "raw_findings": len(findings),
        "deferred": sorted(deferred, key=order),
        "resolved": sorted(resolved, key=order),
        "unexpected": sorted(unexpected, key=order),
        "severity_increases": sorted(increases, key=order),
        "duplicates": sorted(duplicates, key=order),
        "decision": "deferred" if not unexpected and not increases and not duplicates else "failed",
    }


def passed_scan_report(scan):
    report = {"schema": 1, "scan": scan, "version": VERSIONS[scan], "status": "passed", "exit_code": 0,
              "findings": [], "contexts": [{"status": "complete"}]}
    if scan == "terraform-security":
        report["contexts"] = [{"scope": TERRAFORM_SCOPE, "status": "complete", "scanned_files": ["modules/example.tf"]}]
        report["policy"] = assess_terraform([])
    return report


def terraform_finding_path(target, source):
    path = Path(str(target))
    if path.is_absolute():
        path = path.resolve().relative_to(source.resolve())
    elif path.parts and path.parts[0] == source.name:
        path = Path(*path.parts[1:])
    if path.is_absolute() or ".." in path.parts:
        raise RuntimeError("Terraform finding path is unsafe")
    return path.as_posix()


def terraform_findings(payload):
    findings = []
    for result in payload.get("Results", []):
        for item in result.get("Misconfigurations", []):
            if item.get("Status") not in (None, "FAIL"):
                continue
            cause = item.get("CauseMetadata", {})
            findings.append({"file": result.get("Target"), "rule": item.get("AVDID") or item.get("ID"),
                             "title": item.get("Title"), "severity": item.get("Severity"),
                             "resource": cause.get("Resource"), "line": cause.get("StartLine"),
                             "remediation": item.get("Resolution"), "reference": item.get("PrimaryURL")})
    return findings


def scan_terraform(temp):
    # The reviewed baseline is read during policy comparison and is never rewritten here.
    source = temp / "source"
    source.mkdir()
    sources = []
    for relative in tracked_files():
        if relative.suffix != ".tf" or relative.parts[0] not in {"envs", "modules"}:
            continue
        text = (ROOT / relative).read_text(encoding="utf-8-sig")
        if re.search(r"(?:tfsec|trivy):ignore|checkov:skip", text):
            raise RuntimeError(f"Inline security suppressions require review: {relative.as_posix()}")
        target = source / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / relative, target)
        sources.append(relative.as_posix())
    if not sources:
        raise RuntimeError("Terraform scan coverage is empty")
    ignored = temp / "empty-ignore"
    ignored.write_text("", encoding="utf-8")
    report = temp / "trivy.json"
    # Execute outside the repository so local trivy.yaml cannot suppress checks.
    result = invoke([TOOLS / "trivy" / "trivy.exe", "config", "--format=json", "--output", report,
                     "--exit-code=1", "--ignorefile", ignored, "--severity=UNKNOWN,LOW,MEDIUM,HIGH,CRITICAL", source], cwd=temp)
    if result.returncode not in (0, 1) or not report.exists():
        raise RuntimeError("Trivy Terraform scan did not complete (policy DB/download or parser error)")
    payload = json.loads(report.read_text(encoding="utf-8"))
    if "Results" not in payload:
        raise RuntimeError("Trivy report is incomplete")
    findings = terraform_findings(payload)
    for item in findings:
        item["file"] = terraform_finding_path(item["file"], source)
    if bool(findings) != bool(result.returncode):
        raise RuntimeError("Trivy exit/report mismatch")
    return findings, [{"scope": TERRAFORM_SCOPE, "status": "complete", "scanned_files": sources}]


def validate_terraform_coverage(context, expected_sources):
    files = context.get("scanned_files")
    if context.get("scope") != TERRAFORM_SCOPE or context.get("status") != "complete" or not isinstance(files, list) or not files or len(files) != len(set(files)):
        raise ValueError("Missing or unexpected Terraform scan coverage")
    for item in files:
        path = Path(item) if isinstance(item, str) else Path()
        if not isinstance(item, str) or path.is_absolute() or ".." in path.parts or path.suffix != ".tf" or not path.parts or path.parts[0] not in {"envs", "modules"}:
            raise ValueError("Missing or unexpected Terraform scan coverage")
    if expected_sources is not None and tuple(files) != tuple(expected_sources):
        raise ValueError("Missing or unexpected Terraform scan coverage")


def validate_security_reports(reports, expected_sources=None):
    by_scan = {}
    for report in reports:
        name = report["scan"]
        if name in by_scan:
            raise ValueError(f"Duplicate security report: {name}")
        by_scan[name] = report
    if set(by_scan) != set(SCANS):
        raise ValueError("Missing/unexpected required security reports")
    terraform_summary = None
    for name, report in by_scan.items():
        if report.get("schema") != 1 or report.get("version") != VERSIONS[name]:
            raise ValueError(f"Security scan is not clean: {name}")
        contexts = report.get("contexts")
        if not contexts or any(context.get("status") != "complete" for context in contexts):
            raise ValueError(f"Incomplete security scan: {name}")
        if name != "terraform-security":
            if report.get("status") != "passed" or report.get("exit_code") != 0 or report.get("findings"):
                raise ValueError(f"Security scan is not clean: {name}")
            continue
        if len(contexts) != 1:
            raise ValueError("Missing or unexpected Terraform scan coverage")
        validate_terraform_coverage(contexts[0], expected_sources)
        findings = report.get("findings")
        if report.get("status") == "error" or report.get("exit_code") not in (0, 1) or not isinstance(findings, list):
            raise ValueError("Terraform scanner failed or did not finish")
        if bool(findings) != (report.get("status") == "findings" and report.get("exit_code") == 1):
            raise ValueError("Terraform raw result was tampered")
        policy = assess_terraform(findings)
        if policy["decision"] != "deferred":
            raise ValueError("Terraform finding is outside the temporary development baseline")
        if report.get("policy") != policy:
            raise ValueError("Terraform policy result does not match the baseline comparison")
        terraform_summary = {"raw_status": report["status"], "raw_exit": report["exit_code"], "raw_findings": len(findings),
                             "decision": policy["decision"], "deferred": len(policy["deferred"]), "resolved": len(policy["resolved"])}
    return terraform_summary


def run(scan):
    report = {"schema": 1, "scan": scan, "version": VERSIONS[scan], "status": "error", "exit_code": 2, "findings": [], "contexts": []}
    try:
        with tempfile.TemporaryDirectory(prefix="wilvor-security-") as directory:
            functions = {"secrets": scan_secrets, "python-dependencies": scan_python, "npm-dependencies": scan_npm, "terraform-security": scan_terraform}
            report["findings"], report["contexts"] = functions[scan](Path(directory))
            if scan == "terraform-security":
                report["policy"] = assess_terraform(report["findings"])
        failed_context = any(context["status"] != "complete" for context in report["contexts"])
        report["status"] = "error" if failed_context else "findings" if report["findings"] else "passed"
        report["exit_code"] = 2 if failed_context else 1 if report["findings"] else 0
    except RuntimeError as error:
        report["contexts"].append({"status": "error", "reason": str(error)})
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
        # Never echo raw scanner diagnostics: they can contain matched values.
        report["contexts"].append({"status": "error", "reason": "Scanner could not complete; verify pinned tool, public feed access and input compatibility"})
    destination = REPORTS / scan / "scan.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(report, indent=2), encoding="utf-8")
    if scan == "terraform-security" and "policy" in report:
        policy = report["policy"]
        message = (
            f"### Security: terraform-security\nTrivy {VERSIONS[scan]} raw status: {report['status']}; "
            f"raw exit {report['exit_code']}; raw findings: {len(report['findings'])}.\n"
            "CI policy is a temporary development deferral, not risk acceptance. "
            f"Decision: {policy['decision']}; deferred {len(policy['deferred'])}; resolved {len(policy['resolved'])}; "
            f"unexpected {len(policy['unexpected'])}; severity increases {len(policy['severity_increases'])}; "
            f"duplicates {len(policy['duplicates'])}.\n"
        )
        for item in report["findings"]:
            message += f"- {item['severity']} {item['rule']} {item['file']}:{item['line']} {item['resource']}\n"
        message += "Identifiers/locations only are in the scan artifact; raw values and scanner output are not published.\n"
        code = 2 if report["status"] == "error" else 0 if policy["decision"] == "deferred" else 1
    else:
        message = f"### Security: {scan}\nVersion {VERSIONS[scan]}; status: {report['status']}; findings: {len(report['findings'])}.\n"
        message += "Identifiers/locations only are in the scan artifact; raw values and scanner output are not published.\n"
        code = report["exit_code"]
    print(message)
    if os.getenv("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as stream:
            stream.write(message)
    return code


if __name__ == "__main__":
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument("command", choices=("bootstrap", "run"))
    parser.add_argument("--scan", choices=SCANS, required=True)
    args = parser.parse_args()
    if args.command == "run":
        raise SystemExit(run(args.scan))
    try:
        bootstrap(args.scan)
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError):
        print(f"Pinned scanner setup failed: {args.scan}; check public download access/integrity")
        raise SystemExit(2)
