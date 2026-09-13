"""Import isolation for wilvor_historical_query (Phase 2B.1 / 2B.2)."""

from __future__ import annotations

import ast
import inspect
import os
import subprocess
import sys
from pathlib import Path

import wilvor_historical_query
from wilvor_historical_query import executor, query_registry, query_sql


REPO_ROOT = Path(__file__).resolve().parents[3]
SHARED_DIR = REPO_ROOT / "functions" / "shared"
PACKAGE_DIR = SHARED_DIR / "wilvor_historical_query"
HISTORICAL_DIR = SHARED_DIR / "wilvor_historical"


def _run_isolated(script: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    pythonpath = str(SHARED_DIR)
    existing = env.get("PYTHONPATH", "")
    if existing:
        pythonpath = pythonpath + os.pathsep + existing
    env["PYTHONPATH"] = pythonpath
    return subprocess.run(
        [sys.executable, "-c", script],
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )


def _imported_roots(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".", 1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".", 1)[0])
    return imported


def test_query_package_source_has_no_aws_or_ai_imports():
    forbidden = {
        "boto3",
        "botocore",
        "wilvor_ai",
        "athena",
        "pyathena",
    }
    for path in PACKAGE_DIR.glob("*.py"):
        imported = _imported_roots(path)
        assert imported.isdisjoint(forbidden), path
    assert "wilvor_historical" in _imported_roots(PACKAGE_DIR / "query_sql.py")
    assert "wilvor_historical" in _imported_roots(PACKAGE_DIR / "query_registry.py")
    assert "wilvor_historical" in _imported_roots(PACKAGE_DIR / "coverage_store.py")
    assert "wilvor_historical" in _imported_roots(PACKAGE_DIR / "coverage_gate.py")
    assert "wilvor_historical" not in _imported_roots(PACKAGE_DIR / "executor.py")
    assert "wilvor_historical" not in _imported_roots(PACKAGE_DIR / "errors.py")
    assert "executor" not in _imported_roots(PACKAGE_DIR / "coverage_store.py")
    assert "executor" not in _imported_roots(PACKAGE_DIR / "coverage_gate.py")


def test_historical_package_still_does_not_import_query_runtime():
    forbidden = {
        "boto3",
        "botocore",
        "wilvor_ai",
        "wilvor_historical_query",
        "athena",
        "pyathena",
    }
    for path in HISTORICAL_DIR.glob("*.py"):
        assert _imported_roots(path).isdisjoint(forbidden), path


def test_import_wilvor_historical_query_is_isolated():
    script = """
import sys
import wilvor_historical_query
assert 'wilvor_historical' in sys.modules
assert 'boto3' not in sys.modules
assert 'botocore' not in sys.modules
assert 'wilvor_ai' not in sys.modules
assert 'athena' not in sys.modules
assert hasattr(wilvor_historical_query, 'render_historical_operation')
assert hasattr(wilvor_historical_query, 'AthenaExecutor')
assert hasattr(wilvor_historical_query, 'InternalQueryId')
assert hasattr(wilvor_historical_query, 'render_fixed_query')
assert hasattr(wilvor_historical_query, 'CoverageGate')
assert hasattr(wilvor_historical_query, 'CoverageStore')
assert not hasattr(wilvor_historical_query, 'execute_sql')
assert not hasattr(wilvor_historical_query.AthenaExecutor, 'execute')
assert not hasattr(wilvor_historical_query, '_execute_rendered')
assert not hasattr(wilvor_historical_query, 'run_sql')
assert not hasattr(wilvor_historical_query, 'query_table')
assert not hasattr(wilvor_historical_query, 'register_query')
"""
    completed = _run_isolated(script)
    assert completed.returncode == 0, completed.stderr + completed.stdout


def test_no_generic_sql_api_symbols_exist():
    forbidden = {
        "execute_sql",
        "run_sql",
        "query_table",
        "register_query",
        "execute_query",
        "execute_athena",
    }
    for module in (wilvor_historical_query, query_registry, query_sql, executor):
        names = set(dir(module))
        assert names.isdisjoint(forbidden)
        source = inspect.getsource(module)
        assert "def execute_sql" not in source
        assert "def run_sql" not in source
        assert "def query_table" not in source
        assert "def register_query" not in source
        assert "boto3" not in source
        assert "time.time(" not in source
        assert "datetime.now(" not in source
        assert "evaluate_collection_window" not in source
        assert "is_verified_zero" not in source
    for module in (wilvor_historical_query, query_registry, query_sql):
        source = inspect.getsource(module)
        assert "StartQueryExecution" not in source


def test_executor_has_no_raw_sql_or_coverage_hooks():
    source = inspect.getsource(executor)
    assert "def execute_sql" not in source
    assert "def run_sql" not in source
    assert "evaluate_collection_window" not in source
    assert "is_verified_zero" not in source
    assert "boto3" not in source
    assert "client(" not in source
    assert hasattr(executor.AthenaExecutor, "execute_fixed")
    assert not hasattr(executor.AthenaExecutor, "execute")
    assert not hasattr(executor.AthenaExecutor, "execute_sql")
    assert not hasattr(executor.AthenaExecutor, "execute_query_string")
    signature = inspect.signature(executor.AthenaExecutor.execute_fixed)
    assert list(signature.parameters) == ["self", "query_id", "request"]


def test_coverage_modules_do_not_import_or_call_the_executor():
    from wilvor_historical_query import coverage_gate, coverage_store

    for module in (coverage_gate, coverage_store):
        source = inspect.getsource(module)
        assert "AthenaExecutor" not in source
        assert "execute_fixed" not in source
        assert "render_fixed_query" not in source
        assert "datetime.now" not in source
        assert "datetime.utcnow" not in source
        assert "time.time(" not in source
        assert "is_verified_zero" not in source
    assert "evaluate_collection_window" in inspect.getsource(coverage_gate)
    assert "evaluate_collection_window" not in inspect.getsource(coverage_store)
    assert "evaluate_collection_window" not in inspect.getsource(executor)
