"""Temporary Terraform baseline: exact deferral, no silent acceptance."""

from argparse import Namespace
import json
from pathlib import Path

import pytest

from scripts import ci
from scripts import ci_security as security
from scripts.ci_groups import inventory


def reviewed():
    return [dict(item) for item in security.load_terraform_baseline()]


def bundle(terraform):
    reports = [security.passed_scan_report(name) for name in security.SCANS if name != "terraform-security"]
    reports.append(terraform)
    return reports


def terraform_report(findings, **changes):
    report = security.passed_scan_report("terraform-security")
    report["findings"] = findings
    report["status"] = "findings" if findings else "passed"
    report["exit_code"] = 1 if findings else 0
    report["policy"] = security.assess_terraform(findings)
    report.update(changes)
    return report


def test_exact_baseline_passes_and_records_raw_findings():
    findings = reviewed()
    policy = security.assess_terraform(findings)
    assert len(findings) == policy["baseline_entries"] == policy["raw_findings"] == 99
    assert policy["decision"] == "deferred" and not policy["unexpected"] and not policy["resolved"]
    assert policy["risk_acceptance"] is False
    summary = security.validate_security_reports(bundle(terraform_report(findings)))
    assert summary["raw_status"] == "findings" and summary["raw_exit"] == 1 and summary["raw_findings"] == 99
    assert summary["deferred"] == 99 and summary["resolved"] == 0


def test_resolved_finding_is_allowed_without_rewriting_the_baseline(tmp_path):
    before = security.TERRAFORM_BASELINE.read_bytes()
    findings = reviewed()[1:]
    policy = security.assess_terraform(findings)
    assert policy["decision"] == "deferred" and len(policy["resolved"]) == 1 and len(policy["deferred"]) == 98
    security.validate_security_reports(bundle(terraform_report(findings)))
    assert security.TERRAFORM_BASELINE.read_bytes() == before
    assert not any(path == security.TERRAFORM_BASELINE for path in tmp_path.rglob("*"))


def test_new_terraform_finding_fails():
    findings = reviewed() + [{"rule": "AWS-9999", "file": "modules/new.tf", "resource": "module.new", "line": 1, "severity": "LOW"}]
    assert security.assess_terraform(findings)["decision"] == "failed"
    with pytest.raises(ValueError, match="outside the temporary"):
        security.validate_security_reports(bundle(terraform_report(findings)))


def test_same_count_with_a_different_finding_fails():
    findings = reviewed()
    findings[-1] = {**findings[-1], "rule": "AWS-9999"}
    assert len(findings) == 99
    policy = security.assess_terraform(findings)
    assert policy["decision"] == "failed" and policy["unexpected"] and len(policy["resolved"]) == 1
    with pytest.raises(ValueError, match="outside the temporary"):
        security.validate_security_reports(bundle(terraform_report(findings)))


def test_increased_severity_fails():
    findings = reviewed()
    findings[0] = {**findings[0], "severity": "CRITICAL"}
    policy = security.assess_terraform(findings)
    assert policy["severity_increases"] and policy["decision"] == "failed"
    with pytest.raises(ValueError, match="outside the temporary"):
        security.validate_security_reports(bundle(terraform_report(findings)))


def test_duplicate_occurrence_fails():
    findings = reviewed()
    findings.append(dict(findings[0]))
    policy = security.assess_terraform(findings)
    assert policy["duplicates"] and policy["decision"] == "failed"
    with pytest.raises(ValueError, match="outside the temporary"):
        security.validate_security_reports(bundle(terraform_report(findings)))


def test_missing_baseline_fails(monkeypatch, tmp_path):
    monkeypatch.setattr(security, "TERRAFORM_BASELINE", tmp_path / "missing.json")
    with pytest.raises(ValueError, match="missing"):
        security.load_terraform_baseline()


@pytest.mark.parametrize("mutation", ["json", "entries", "duplicate", "acceptance", "note", "empty"])
def test_malformed_baseline_fails(monkeypatch, tmp_path, mutation):
    payload = json.loads(security.TERRAFORM_BASELINE.read_text(encoding="utf-8"))
    text = security.TERRAFORM_BASELINE.read_text(encoding="utf-8")
    if mutation == "json":
        text = "{not json"
    elif mutation == "entries":
        payload["entries"] = 98
        text = json.dumps(payload)
    elif mutation == "duplicate":
        payload["findings"].append(payload["findings"][0])
        payload["entries"] = len(payload["findings"])
        text = json.dumps(payload)
    elif mutation == "acceptance":
        payload["risk_acceptance"] = True
        text = json.dumps(payload)
    elif mutation == "note":
        payload["note"] = "approved"
        text = json.dumps(payload)
    else:
        payload["findings"] = []
        payload["entries"] = 0
        text = json.dumps(payload)
    path = tmp_path / "baseline.json"
    path.write_text(text, encoding="utf-8")
    monkeypatch.setattr(security, "TERRAFORM_BASELINE", path)
    with pytest.raises(ValueError, match="corrupted|duplicate"):
        security.load_terraform_baseline()


def test_missing_scan_report_fails():
    reports = bundle(terraform_report(reviewed()))
    reports = [report for report in reports if report["scan"] != "terraform-security"]
    with pytest.raises(ValueError, match="Missing/unexpected"):
        security.validate_security_reports(reports)


def test_unexpected_scanner_failure_fails():
    report = terraform_report([])
    report["status"] = "error"
    report["exit_code"] = 2
    with pytest.raises(ValueError, match="failed or did not finish"):
        security.validate_security_reports(bundle(report))


@pytest.mark.parametrize("coverage", ["empty", "escape", "mismatch", "scope"])
def test_missing_or_unexpected_terraform_coverage_fails(coverage):
    report = terraform_report([])
    if coverage == "empty":
        report["contexts"][0]["scanned_files"] = []
    elif coverage == "escape":
        report["contexts"][0]["scanned_files"] = ["../modules/evil.tf"]
    elif coverage == "scope":
        report["contexts"][0]["scope"] = "modules/only"
    else:
        report["contexts"][0]["scanned_files"] = ["modules/example.tf"]
    kwargs = {"expected_sources": ("modules/other.tf",)} if coverage == "mismatch" else {}
    with pytest.raises(ValueError, match="coverage"):
        security.validate_security_reports(bundle(report), **kwargs)


def test_tampered_clean_status_and_policy_fail():
    findings = reviewed()
    report = terraform_report(findings)
    report["status"] = "passed"
    report["exit_code"] = 0
    with pytest.raises(ValueError, match="tampered"):
        security.validate_security_reports(bundle(report))
    lied = terraform_report(findings)
    lied["policy"] = security.assess_terraform([])
    with pytest.raises(ValueError, match="does not match"):
        security.validate_security_reports(bundle(lied))
    incomplete = terraform_report([])
    incomplete["contexts"] = []
    with pytest.raises(ValueError, match="Incomplete"):
        security.validate_security_reports(bundle(incomplete))


def test_policy_deferral_does_not_hide_or_rewrite_the_raw_scan(tmp_path, monkeypatch):
    findings = reviewed()
    context = [{"scope": security.TERRAFORM_SCOPE, "status": "complete", "scanned_files": ["modules/example.tf"]}]
    monkeypatch.setattr(security, "scan_terraform", lambda temp: (findings, context))
    monkeypatch.setattr(security, "REPORTS", tmp_path)
    before = security.TERRAFORM_BASELINE.read_bytes()
    assert security.run("terraform-security") == 0
    report = json.loads((tmp_path / "terraform-security" / "scan.json").read_text(encoding="utf-8"))
    assert report["status"] == "findings" and report["exit_code"] == 1 and len(report["findings"]) == 99
    assert report["policy"]["decision"] == "deferred" and report["policy"]["raw_findings"] == 99
    assert security.TERRAFORM_BASELINE.read_bytes() == before
    findings.append({"rule": "AWS-9999", "file": "modules/new.tf", "resource": "module.new", "line": 1, "severity": "HIGH"})
    assert security.run("terraform-security") == 1
    failed = json.loads((tmp_path / "terraform-security" / "scan.json").read_text(encoding="utf-8"))
    assert failed["exit_code"] == 1 and len(failed["findings"]) == 100 and failed["policy"]["unexpected"]


def test_new_secret_candidate_fails_and_dependency_audits_stay_closed():
    reports = [security.passed_scan_report(name) for name in security.SCANS]
    reports[0]["findings"] = [{"rule": "generic-api-key", "file": "canary.txt", "line": 1}]
    reports[0]["status"] = "findings"
    reports[0]["exit_code"] = 1
    with pytest.raises(ValueError, match="not clean"):
        security.validate_security_reports(reports)
    for scan in ("python-dependencies", "npm-dependencies"):
        reports = [security.passed_scan_report(name) for name in security.SCANS]
        target = next(item for item in reports if item["scan"] == scan)
        target["findings"] = [{"package": "example", "advisory": "GHSA-example"}]
        with pytest.raises(ValueError, match="not clean"):
            security.validate_security_reports(reports)


def test_aggregate_gate_recomputes_the_baseline(tmp_path, monkeypatch):
    nodes = ["tests/contracts/test_example.py::test_example"]
    data = inventory(nodes, "pr")
    (tmp_path / "inventory.json").write_text(json.dumps(data), encoding="utf-8")
    folder = tmp_path / "copilot"
    folder.mkdir()
    (folder / "results.json").write_text(json.dumps({"group": "copilot", "exit_code": 0, "cases": dict.fromkeys(nodes, "passed")}), encoding="utf-8")
    (folder / "junit.xml").write_text('<testsuite><testcase name="test_example"/></testsuite>', encoding="utf-8")
    sources = list(security.terraform_sources())
    report = terraform_report(reviewed())
    report["contexts"][0]["scanned_files"] = sources
    for item in bundle(report):
        target = tmp_path / item["scan"]
        target.mkdir()
        (target / "scan.json").write_text(json.dumps(item), encoding="utf-8")
    monkeypatch.setenv("NEEDS_JSON", json.dumps({name: {"result": "success"} for name in ("discovery", "python", "terraform", "dashboard", "security")}))
    monkeypatch.setattr(ci, "REPORTS", tmp_path / "output")
    args = Namespace(inventory=tmp_path / "inventory.json", artifacts=tmp_path, profile="pr")
    assert ci.gate(args) == 0
    stored = json.loads((tmp_path / "terraform-security" / "scan.json").read_text(encoding="utf-8"))
    stored["findings"].append({"rule": "AWS-9999", "file": "modules/new.tf", "resource": "module.new", "line": 1, "severity": "LOW"})
    stored["policy"] = security.assess_terraform(stored["findings"])
    (tmp_path / "terraform-security" / "scan.json").write_text(json.dumps(stored), encoding="utf-8")
    with pytest.raises(ValueError, match="outside the temporary"):
        ci.gate(args)
