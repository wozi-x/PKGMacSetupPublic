"""Internal native-Ansible bridge. No synthetic state or apply-manifest input."""
import argparse
import json
from pathlib import Path
import sys

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
from planner import ConfigError, MAX_BYTES, load_config
from engine import EngineError, compile_operations, observe


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise EngineError("Invalid engine arguments; use the public setup entrypoint.")


def main():
    parser = Parser()
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--config", type=Path)
    source.add_argument("--stdin-config", action="store_true")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--observe", action="store_true")
    mode.add_argument("--commands", action="store_true")
    parser.add_argument("--operation", choices=("setup", "update", "finish"), default="setup")
    parser.add_argument("--review-digest")
    try:
        args = parser.parse_args()
        if args.stdin_config:
            text = sys.stdin.read(MAX_BYTES + 1)
        else:
            with args.config.open(encoding="utf-8") as stream:
                text = stream.read(MAX_BYTES + 1)
        config = load_config(text)
        result = (compile_operations(config, args.operation, args.review_digest) if args.commands
                  else observe(config, args.operation))
        print(json.dumps(result, sort_keys=True))
        return 0
    except (ConfigError, EngineError) as exc:
        print(str(exc), file=sys.stderr)
    except (OSError, UnicodeError):
        print("Unable to read configuration safely.", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
