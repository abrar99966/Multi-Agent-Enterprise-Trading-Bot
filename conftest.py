"""Pytest root config: make `import app...` resolve to backend/app."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "backend"))
