"""Label opt-in, mandatory scanner gates and value-free report regressions."""

from argparse import Namespace
import json
from pathlib import Path

import pytest
import yaml

from scripts import ci
from scripts.ci_groups import PR_EVENTS, inventory, profile_for_event
from scripts.ci_security import SCANS, VERSIONS, npm_findings, python_findings, secret_findings, terraform_findings, validate_security_reports

ROOT = Path(__file__).resolve().parents[3]


def workflow():
    return yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8"))


def clean_reports():
    return [{"schema": 1, "scan": scan, "version": VERSIONS[scan], "status": "passed", "exit_code": 0,
             "findings": [], "contexts": [{"status": "complete"}]} for scan in SCANS]


@pytest.mark.parametrize("event,labels,profile", [
    ("pull_request", [], "pr"), ("pull_request", ["ci-full"], "full"),
    ("pull_request", ["other", "ci-full"], "full"), ("pull_request", ["CI-FULL"], "full"),
    ("pull_request", ["ci-full-later"], "pr"), ("push", [], "full"), ("workflow_dispatch", [], "full"),
])
def test_profile_selection(event, labels, profile):
    assert profile_for_event(event, labels) == profile


@pytest.mark.parametrize("action", PR_EVENTS)
def test_pr_lifecycle_reevaluates_current_labels(action):
    assert action in workflow()["on"]["pull_request"]["types"]
    assert profile_for_event("pull_request", ["ci-full"]) == "full"
    assert profile_for_event("pull_request", []) == "pr"


@pytest.mark.parametrize("mutation", ["missing-labeled", "missing-unlabeled", "old-profile", "missing-scan", "conditional", "missing-execution", "fail-fast", "missing-gate-need"])
def test_security_and_label_workflow_policy(mutation):
    doc = workflow()
    if mutation.startswith("missing-lab") or mutation == "missing-unlabeled":
        doc["on"]["pull_request"]["types"].remove(mutation.removeprefix("missing-"))
    elif mutation == "old-profile":
        doc["env"]["CI_PROFILE"] = "${{ github.event_name == 'pull_request' && 'pr' || 'full' }}"
    elif mutation == "missing-scan":
        doc["jobs"]["security"]["strategy"]["matrix"]["scan"].pop()
    elif mutation == "conditional":
        doc["jobs"]["security"]["if"] = "env.CI_PROFILE == 'full'"
    elif mutation == "missing-execution":
        doc["jobs"]["security"]["steps"] = [step for step in doc["jobs"]["security"]["steps"] if not step.get("run", "").startswith("python scripts/ci_security.py run")]
    elif mutation == "fail-fast":
        doc["jobs"]["security"]["strategy"]["fail-fast"] = True
    else:
        doc["jobs"]["quality-gate"]["needs"].remove("security")
    with pytest.raises(ValueError):
        ci.policy(doc)


def test_clean_security_reports_pass():
    validate_security_reports(clean_reports())
    for change in ("shallow", "no-bootstrap"):
        doc = workflow()
        if change == "shallow":
            doc["jobs"]["security"]["steps"][0]["with"]["fetch-depth"] = 1
        else:
            doc["jobs"]["security"]["steps"] = [step for step in doc["jobs"]["security"]["steps"] if not step.get("run", "").startswith("python scripts/ci_security.py bootstrap")]
        with pytest.raises(ValueError):
            ci.policy(doc)


@pytest.mark.parametrize("mutation", ["missing", "duplicate", "findings", "error", "version", "empty-contexts", "incomplete-context", "exit", "schema"])
def test_security_findings_and_incomplete_scans_never_pass(mutation):
    reports = clean_reports()
    if mutation == "missing":
        reports.pop()
    elif mutation == "duplicate":
        reports.append(reports[0])
    elif mutation == "findings":
        reports[0]["findings"] = [{"rule": "example-finding"}]
    elif mutation == "error":
        reports[0]["status"] = "error"
    elif mutation == "version":
        reports[0]["version"] = "unreviewed"
    elif mutation == "empty-contexts":
        reports[0]["contexts"] = []
    elif mutation == "incomplete-context":
        reports[0]["contexts"][0]["status"] = "error"
    elif mutation == "exit":
        reports[0]["exit_code"] = 1
    else:
        reports[0]["schema"] = 0
    with pytest.raises(ValueError):
        validate_security_reports(reports)


def test_secret_values_and_author_data_are_not_published():
    raw = [{"RuleID": "example", "File": "source.py", "StartLine": 3, "Commit": "abc",
            "Secret": "DO_NOT_PUBLISH", "Match": "DO_NOT_PUBLISH", "Author": "PRIVATE_AUTHOR"}]
    safe = json.dumps(secret_findings(raw, "history"))
    assert "DO_NOT_PUBLISH" not in safe
    assert "PRIVATE_AUTHOR" not in safe
    assert "source.py" in safe


def test_terraform_resolved_values_and_source_lines_are_not_published():
    raw = {"Results": [{"Target": "modules/example.tf", "Misconfigurations": [{
        "AVDID": "AVD-example", "Severity": "HIGH", "Title": "Example", "Resolution": "Enable protection",
        "Status": "FAIL", "CauseMetadata": {"Resource": "aws_example.test", "StartLine": 3,
        "ResolvedValue": "DO_NOT_PUBLISH", "Code": {"Lines": [{"Content": "DO_NOT_PUBLISH"}]}}
    }]}]}
    safe = json.dumps(terraform_findings(raw))
    assert "DO_NOT_PUBLISH" not in safe
    assert "aws_example.test" in safe


def test_dependency_findings_keep_exact_advisories_and_fixes():
    raw = {"dependencies": [{"name": "example", "version": "1.0", "vulns": [{
        "id": "GHSA-example", "aliases": ["CVE-example"], "fix_versions": ["1.1"], "description": "DO_NOT_PUBLISH"
    }]}]}
    findings = python_findings(raw, "tests/requirements-ci.txt")
    assert findings[0]["fix_versions"] == ["1.1"]
    assert "DO_NOT_PUBLISH" not in json.dumps(findings)
    raw["dependencies"][0]["skip_reason"] = "unsupported"
    with pytest.raises(RuntimeError):
        python_findings(raw, "example")


def test_npm_incomplete_advisory_responses_are_errors():
    with pytest.raises(RuntimeError):
        npm_findings({"error": {"summary": "DO_NOT_PUBLISH"}})


@pytest.mark.parametrize("scanner_status", ["passed", "findings", "missing"])
def test_real_gate_requires_security_evidence(tmp_path, monkeypatch, scanner_status):
    nodes = ["tests/contracts/test_example.py::test_example"]
    data = inventory(nodes, "pr")
    manifest = tmp_path / "inventory.json"
    manifest.write_text(json.dumps(data), encoding="utf-8")
    folder = tmp_path / "copilot"
    folder.mkdir()
    (folder / "results.json").write_text(json.dumps({"group": "copilot", "exit_code": 0, "cases": dict.fromkeys(nodes, "passed")}), encoding="utf-8")
    (folder / "junit.xml").write_text('<testsuite><testcase name="test_example"/></testsuite>', encoding="utf-8")
    reports = clean_reports()
    if scanner_status == "findings":
        reports[0]["status"] = "findings"
    if scanner_status == "missing":
        reports.pop()
    for report in reports:
        target = tmp_path / report["scan"]
        target.mkdir()
        (target / "scan.json").write_text(json.dumps(report), encoding="utf-8")
    needs = {name: {"result": "success"} for name in ("discovery", "python", "terraform", "dashboard", "security")}
    monkeypatch.setenv("NEEDS_JSON", json.dumps(needs))
    monkeypatch.setattr(ci, "REPORTS", tmp_path / "output")
    args = Namespace(inventory=manifest, artifacts=tmp_path, profile="pr")
    if scanner_status == "passed":
        assert ci.gate(args) == 0
    else:
        with pytest.raises(ValueError, match="security|Security"):
            ci.gate(args)
