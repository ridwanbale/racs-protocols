"""Pytest configuration — adds the repo root to sys.path for all tests."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
