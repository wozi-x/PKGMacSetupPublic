#!/usr/bin/env python3
"""Read-only entrypoint. Use an existing Python environment containing PyYAML."""
import sys
sys.dont_write_bytecode = True

import argparse
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    from planner import ConfigError, MAX_BYTES, build_plan, load_config
except ModuleNotFoundError:
    print("Use an existing Python environment with PyYAML; this planner installs nothing.", file=sys.stderr)
    sys.exit(2)


class SafeParser(argparse.ArgumentParser):
    def error(self, message):
        self.exit(2, "Invalid CLI options; use --help for the read-only interface.\n")


def main(argv=None):
    parser = SafeParser(description="Offline configuration-only plan; no installation or host probing.")
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--plan", required=True, action="store_true")
    parser.add_argument("--operation", choices=("setup", "update", "finish"), default="setup")
    args = parser.parse_args(argv)
    try:
        with args.config.open("rb") as source:
            raw = source.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES:
            raise ConfigError("Configuration exceeds the text/size limit.")
        config = load_config(raw.decode("utf-8"))
        plan = build_plan(config, operation=args.operation)
    except ConfigError as error:
        print("Configuration error: " + str(error), file=sys.stderr)
        return 2
    except (OSError, UnicodeError):
        print("Configuration could not be read safely as bounded UTF-8 text.", file=sys.stderr)
        return 2
    print(json.dumps(plan, indent=2, sort_keys=True))
    return 3 if plan["status"] in ("unresolved", "blocked") else 0


if __name__ == "__main__":
    sys.exit(main())
