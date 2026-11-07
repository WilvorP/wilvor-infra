"""Regression checks for omissions, duplicate execution and unsafe workflow changes."""

from argparse import Namespace
from copy import deepcopy
import json
from pathlib import Path
import xml.etree.ElementTree as ET

import pytest
import yaml

from scripts.ci import clean_junit, dashboard_cases, gate, policy, validate_gate
from scripts.ci_groups import GROUPS, expected, inventory, owner

ROOT = Path(__file__).resolve().parents[3]
NODES = [
    "tests/unit/aircraft/test_x.py::test_a", "tests/unit/shared/test_x.py::test_a",
    "tests/unit/historical_query/test_x.py::test_a", "tests/unit/scripts/test_x.py::test_a",
    "tests/contracts/test_x.py::test_a", "tests/infrastructure/terraform/test_x.py::test_a",
    "tests/unit/ci/test_x.py::test_a", "tests/infrastructure/terraform/test_terraform_cli.py::test_a",
    "tests/infrastructure/aws/test_x.py::test_a", "tests/infrastructure/terraform/test_plan_safety.py::test_a",
    "tests/integration/aircraft/test_x.py::test_a",
]
NEEDS = {name: {"result": "success"} for name in ("discovery", "python", "terraform", "dashboard", "security")}


def results(data):
    reports = [{"group": g, "exit_code": 0, "cases": dict.fromkeys(expected(data, g), "passed")}
               for g in GROUPS if expected(data, g)]
    if data["profile"] == "full":
        reports.append({"group": "dashboard", "exit_code": 0, "cases": {"src/test.ts::test": "passed"}})
    return reports


def test_all_owners_are_disjoint_and_exclusions_are_explicit():
    data = inventory(NODES, "full")
    assert sum(map(len, data["groups"].values())) == 8
    assert sum(map(len, data["excluded"].values())) == 3
    assert validate_gate(data, results(data), "full", NEEDS) == 8


def test_new_cases_are_discovered_without_count_ceiling():
    data = inventory(NODES + ["tests/contracts/nested/test_new.py::test_new"], "full")
    assert len(expected(data, "copilot")) == 2
    listed = [{"file": "C:/work/dashboard/src/new.test.ts", "name": "suite > new test"}]
    raw = {"testResults": [{"name": "C:/work/dashboard/src/new.test.ts", "assertionResults": [
        {"ancestorTitles": ["suite"], "title": "new test", "fullName": "suite new test", "status": "passed"}
    ]}]}
    assert dashboard_cases(listed, raw) == {"src/new.test.ts::suite > new test": "passed"}
    with pytest.raises(ValueError, match="mismatch"):
        dashboard_cases(listed + [{"file": "src/missing.test.ts", "name": "missing"}], raw)


def test_unclassified_domains_fail_instead_of_disappearing():
    with pytest.raises(ValueError, match="Unclassified"):
        owner("tests/unit/new_domain/test_x.py::test_a")


def test_duplicate_collection_fails():
    with pytest.raises(ValueError, match="Duplicate"):
        inventory(NODES + NODES[:1], "full")


def test_pr_retains_all_contracts_safety_and_static_infrastructure():
    data = inventory(NODES, "pr")
    assert expected(data, "copilot") == [NODES[4]]
    assert expected(data, "script-safety") == [NODES[3]]
    assert expected(data, "infrastructure") == [NODES[5]]
    assert not expected(data, "pipelines")
    assert validate_gate(data, results(data), "pr", NEEDS) == 4


@pytest.mark.parametrize("status", ["failure", "cancelled", "skipped"])
def test_gate_rejects_unsuccessful_jobs(status):
    needs = deepcopy(NEEDS)
    needs["python"]["result"] = status
    data = inventory(NODES, "full")
    with pytest.raises(ValueError, match="required job"):
        validate_gate(data, results(data), "full", needs)


@pytest.mark.parametrize("mutation", ["missing", "duplicate", "unexpected", "skip", "failure", "missing-case", "extra-case"])
def test_gate_rejects_incomplete_or_invalid_execution(mutation):
    data = inventory(NODES, "full")
    reports = results(data)
    if mutation == "missing":
        reports.pop()
    elif mutation == "duplicate":
        reports.append(reports[0])
    elif mutation == "unexpected":
        reports.append({"group": "extra", "exit_code": 0, "cases": {"x": "passed"}})
    elif mutation == "skip":
        reports[0]["cases"][NODES[0]] = "skipped"
    elif mutation == "failure":
        reports[0]["exit_code"] = 1
    elif mutation == "missing-case":
        reports[0]["cases"].clear()
    else:
        reports[0]["cases"]["unexpected"] = "passed"
    with pytest.raises(ValueError):
        validate_gate(data, reports, "full", NEEDS)


def test_gate_requires_report_files(tmp_path, monkeypatch):
    data = inventory(NODES, "pr")
    inventory_path = tmp_path / "inventory.json"
    inventory_path.write_text(json.dumps(data), encoding="utf-8")
    for report in results(data):
        directory = tmp_path / report["group"]
        directory.mkdir()
        (directory / "results.json").write_text(json.dumps(report), encoding="utf-8")
    monkeypatch.setenv("NEEDS_JSON", json.dumps(NEEDS))
    with pytest.raises(ValueError, match="Missing JUnit"):
        gate(Namespace(inventory=inventory_path, artifacts=tmp_path, profile="pr"))


def workflow():
    return yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8"))


def test_real_workflow_policy():
    policy(workflow())


@pytest.mark.parametrize("mutation", ["push", "permissions", "secret", "pin", "checkout", "shell-expression", "suppression", "gate"])
def test_workflow_policy_rejects_security_regressions(mutation):
    document = workflow()
    steps = document["jobs"]["discovery"]["steps"]
    if mutation == "push":
        document["on"]["push"]["branches"] = ["**"]
    elif mutation == "permissions":
        document["permissions"]["id-token"] = "write"
    elif mutation == "secret":
        document["env"]["KEY"] = "${{ secrets.KEY }}"
    elif mutation == "pin":
        steps[0]["uses"] = "actions/checkout@main"
    elif mutation == "checkout":
        steps[0]["with"]["persist-credentials"] = True
    elif mutation == "shell-expression":
        steps.append({"run": "echo '${{ github.event.pull_request.title }}'"})
    elif mutation == "suppression":
        steps[0]["continue-on-error"] = True
    else:
        for change in ("missing-needs", "conditional-gate", "wrong-profile"):
            candidate = workflow()
            if change == "missing-needs":
                candidate["jobs"]["quality-gate"]["needs"].remove("dashboard")
            elif change == "conditional-gate":
                candidate["jobs"]["quality-gate"]["if"] = "always() && false"
            else:
                candidate["env"]["CI_PROFILE"] = "pr"
            with pytest.raises(ValueError):
                policy(candidate)
        return
    with pytest.raises(ValueError):
        policy(document)
    if mutation == "shell-expression":
        candidate = workflow()
        for step in candidate["jobs"]["dashboard"]["steps"]:
            if step.get("name") == "Vitest with coverage and reports":
                step.pop("env")
        with pytest.raises(ValueError, match="offline guard"):
            policy(candidate)


def test_junit_upload_removes_captured_output_and_redacts_tokens(tmp_path):
    path = tmp_path / "junit.xml"
    path.write_text('<testsuite><testcase><failure message="sk-ant-synthetic-token">sk-ant-synthetic-token</failure><system-out>private payload</system-out></testcase></testsuite>', encoding="utf-8")
    clean_junit(path)
    tree = ET.parse(path)
    assert tree.find(".//system-out") is None
    assert "sk-ant-" not in path.read_text(encoding="utf-8")
