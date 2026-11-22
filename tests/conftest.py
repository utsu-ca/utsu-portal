"""Tests share helper modules (test_portal, test_agm, test_sap, ...) by plain
module name, so every test folder must be importable from every other."""
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
for _dir in sorted(p for p in _HERE.iterdir() if p.is_dir() and p.name != "__pycache__"):
    if str(_dir) not in sys.path:
        sys.path.append(str(_dir))
