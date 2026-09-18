"""Stable import facade; local and SSH use the same native module_utils engine."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent / "module_utils"))
from mac_setup_engine import *  # noqa: F401,F403
