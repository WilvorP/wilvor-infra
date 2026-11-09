"""Sprint 4 Live Ops modules stay free of live AWS, models, and Terraform."""

from __future__ import annotations

import ast
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
SHARED = REPO_ROOT / "functions" / "shared" / "wilvor_ai"
MODULES = (
    SHARED / "sprint4_live_ops_authorization.py",
    SHARED / "sprint4_live_ops_source.py",
    SHARED / "sprint4_live_ops_tools.py",
)
FORBIDDEN_IMPORTS = {
    "boto3",
    "botocore",
    "anthropic",
    "openai",
    "langgraph",
}
FORBIDDEN_TEXT = (
    "boto3.client",
    "boto3.resource",
    "os.environ",
    "datetime.now",
    "time.time",
    "terraform",
)


def test_sprint4_live_ops_modules_are_offline():
    for path in MODULES:
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        imported = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.append(node.module)
        for name in FORBIDDEN_IMPORTS:
            assert name not in imported, path.name
        for fragment in FORBIDDEN_TEXT:
            assert fragment not in source, f"{path.name}:{fragment}"
        assert "PRODUCTION_LIVE_OPS_AWS_READ_ENABLED = False" in source or path.name != (
            "sprint4_live_ops_authorization.py"
        )
