"""Importing the Live Ops harness must stay offline."""

from __future__ import annotations

import ast
import os
import subprocess
import sys
import textwrap
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "validate_live_ops_specialist_anthropic_live.py"


def _function_local(path: Path, predicate) -> None:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in tree.body:
        if predicate(node):
            raise AssertionError("module-level import is not allowed")


def _imports_anthropic(node: ast.AST) -> bool:
    if isinstance(node, ast.Import):
        return any(alias.name == "anthropic" or alias.name.startswith("anthropic.") for alias in node.names)
    if isinstance(node, ast.ImportFrom):
        return node.module == "anthropic" or (
            node.module is not None and node.module.startswith("anthropic.")
        )
    return False


def _reads_environ(node: ast.AST) -> bool:
    if not isinstance(node, ast.Attribute) or node.attr != "environ":
        return False
    return isinstance(node.value, ast.Name) and node.value.id == "os"


def test_sdk_import_and_key_read_are_function_local() -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(SCRIPT))
    _function_local(SCRIPT, _imports_anthropic)
    for node in tree.body:
        for child in ast.walk(node):
            if _reads_environ(child) and not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                raise AssertionError("os.environ is read at module scope")
    assert "import anthropic" in source
    assert "ANTHROPIC_API_KEY" in source


def test_importing_the_harness_does_not_start_live_work() -> None:
    script = textwrap.dedent(
        f"""
        import os
        import socket
        import sys
        from pathlib import Path

        hits = []
        real = os.environ

        class EnvironProxy:
            def get(self, key, default=None):
                if key == "ANTHROPIC_API_KEY":
                    hits.append(key)
                return real.get(key, default)
            def __getitem__(self, key):
                if key == "ANTHROPIC_API_KEY":
                    hits.append(key)
                return real[key]
            def __contains__(self, key):
                if key == "ANTHROPIC_API_KEY":
                    hits.append(key)
                return key in real
            def __iter__(self):
                return iter(real)
            def __setitem__(self, key, value):
                real[key] = value
            def __delitem__(self, key):
                del real[key]
            def pop(self, key, *args):
                if key == "ANTHROPIC_API_KEY":
                    hits.append(key)
                return real.pop(key, *args)
            def setdefault(self, key, default=None):
                if key == "ANTHROPIC_API_KEY":
                    hits.append(key)
                return real.setdefault(key, default)
            def copy(self):
                return real.copy()
            def items(self):
                return real.items()
            def keys(self):
                return real.keys()
            def values(self):
                return real.values()
            def update(self, *args, **kwargs):
                return real.update(*args, **kwargs)

        os.environ = EnvironProxy()
        connections = []
        original_connect = socket.create_connection

        def blocked(*args, **kwargs):
            connections.append(args)
            raise AssertionError("network")

        socket.create_connection = blocked
        import boto3
        clients = []

        def client(*args, **kwargs):
            clients.append(("client", args))
            raise AssertionError("boto3.client")

        def resource(*args, **kwargs):
            clients.append(("resource", args))
            raise AssertionError("boto3.resource")

        boto3.client = client
        boto3.resource = resource
        import importlib.util
        path = Path({str(SCRIPT)!r})
        spec = importlib.util.spec_from_file_location("live_ops_harness_isolation", path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        assert "anthropic" not in sys.modules
        assert module.SDK_IMPORTED is False
        assert hits == []
        assert connections == []
        assert clients == []
        assert not hasattr(module, "Anthropic")
        print("offline")
        """
    )
    env = os.environ.copy()
    env["ANTHROPIC_API_KEY"] = "sk-ant-isolation-sentinel"
    env["WILVOR_RUN_LIVE_LIVE_OPS_ANTHROPIC"] = "1"
    completed = subprocess.run(
        [sys.executable, "-B", "-c", script],
        cwd=REPO_ROOT,
        env=env,
        check=False,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert completed.returncode == 0, completed.stderr + completed.stdout
    assert "offline" in completed.stdout
    assert "sk-ant-isolation-sentinel" not in completed.stdout
    assert "sk-ant-isolation-sentinel" not in completed.stderr
