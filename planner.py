"""Stable import facade; the native Ansible module_utils file is authoritative."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent / "module_utils"))
from mac_setup_planner import *  # noqa: F401,F403
