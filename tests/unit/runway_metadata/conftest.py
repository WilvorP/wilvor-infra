from __future__ import annotations

import sys
from pathlib import Path

LOADER_DIR = (
    Path(__file__).resolve().parents[3]
    / "functions"
    / "runway_metadata"
    / "loader"
)
sys.path.insert(0, str(LOADER_DIR))
