import sys
from pathlib import Path


AI_DIR = (
    Path(__file__).resolve().parents[3]
    / "functions"
    / "ai_copilot"
)

if str(AI_DIR) not in sys.path:
    sys.path.insert(0, str(AI_DIR))
