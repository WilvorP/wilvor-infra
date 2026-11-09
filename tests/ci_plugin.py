"""Explicitly loaded pytest collection and execution accounting for CI."""

import json
from pathlib import Path

import pytest

from scripts.ci_groups import expected, inventory


def pytest_addoption(parser):
    parser.addoption("--ci-inventory")
    parser.addoption("--ci-profile", default="full")
    parser.addoption("--ci-group")
    parser.addoption("--ci-expected")
    parser.addoption("--ci-results")


def pytest_collection_finish(session):
    config = session.config
    nodeids = [item.nodeid for item in session.items]
    path = config.getoption("--ci-inventory")
    if path:
        try:
            data = inventory(nodeids, config.getoption("--ci-profile"))
        except ValueError as error:
            raise pytest.UsageError(str(error)) from error
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(data, indent=2), encoding="utf-8")
    group = config.getoption("--ci-group")
    if group:
        data = json.loads(Path(config.getoption("--ci-expected")).read_text(encoding="utf-8"))
        wanted = set(expected(data, group))
        if len(nodeids) != len(set(nodeids)) or set(nodeids) != wanted:
            raise pytest.UsageError(
                f"{group} discovery mismatch: missing={sorted(wanted-set(nodeids))}, unexpected={sorted(set(nodeids)-wanted)}"
            )
    for item in session.items:
        item.user_properties.append(("ci_nodeid", item.nodeid))


_outcomes = {}


def pytest_runtest_logreport(report):
    if report.failed:
        _outcomes[report.nodeid] = "failed"
    elif report.skipped:
        _outcomes[report.nodeid] = "skipped"
    elif report.when == "call" and report.nodeid not in _outcomes:
        _outcomes[report.nodeid] = "passed"


def pytest_sessionfinish(session, exitstatus):
    path = session.config.getoption("--ci-results")
    if path:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps({
            "group": session.config.getoption("--ci-group"),
            "exit_code": int(exitstatus), "cases": _outcomes,
        }, indent=2), encoding="utf-8")
