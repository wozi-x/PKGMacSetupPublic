"""Review a local public scope and invoke its native Ansible role.

Offline --plan never calls an observer, bootstrapper, authentication helper or Ansible.
No user-supplied state/digest/approval file or command is accepted.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
from planner import ConfigError, MAX_BYTES, build_plan, load_config
from engine import EngineError, ENV, ROOT, MUTATIONS, observe, prerequisite_scope, checkout_digest


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise EngineError("Invalid options; use --help for supported public setup commands.")


def read_config(path):
    if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_BYTES:
        raise EngineError("Select one regular bounded YAML file, not a symlink.")
    raw = path.read_bytes()
    return load_config(raw.decode("utf-8")), hashlib.sha256(raw).hexdigest()


def inspect_inventory(path):
    """Native static inventory only; never source or invent an inventory parser."""
    if path.is_symlink() or not path.is_file() or os.access(path, os.X_OK):
        raise EngineError("Select one regular non-executable static inventory file.")
    executable = Path(sys.executable).parent / "ansible-inventory"
    if not executable.is_file():
        raise EngineError("Ansible inventory support is a prerequisite.")
    env = dict(ENV, ANSIBLE_CONFIG=str(ROOT / "ansible.cfg"), ANSIBLE_INVENTORY_ENABLED="ini,yaml")
    result = subprocess.run([str(executable), "-i", str(path.absolute()), "--playbook-dir", str(ROOT), "--list"],
                            env=env, cwd=ROOT, stdin=subprocess.DEVNULL, capture_output=True, text=True, check=False)
    try:
        value = json.loads(result.stdout)
        names = {name for group, data in value.items() if group != "_meta" for name in data.get("hosts", [])}
        if result.returncode or len(names) != 1:
            raise ValueError()
        name = names.pop()
        host = value.get("_meta", {}).get("hostvars", {}).get(name, {})
        allowed = {"ansible_connection", "ansible_host", "ansible_user", "ansible_port", "ansible_python_interpreter"}
        if set(host) - allowed:
            raise ValueError()
        if host.get("ansible_connection", "ssh") not in ("local", "ssh", "smart"):
            raise ValueError()
        for key in ("ansible_user", "ansible_host", "ansible_python_interpreter"):
            if key not in host or type(host[key]) is not str or not host[key] or any(char in host[key] for char in "\r\n{}%"):
                raise ValueError()
        if host["ansible_user"] == "root" or not host["ansible_python_interpreter"].startswith("/"):
            raise ValueError()
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_-]*", host["ansible_user"]):
            raise ValueError()
        if not re.fullmatch(r"[A-Za-z0-9_.:\[\]-]+", host["ansible_host"]):
            raise ValueError()
        if not re.fullmatch(r"/[A-Za-z0-9_./+@-]+", host["ansible_python_interpreter"]):
            raise ValueError()
        if "ansible_port" in host and (str(host["ansible_port"]).isdigit() is False or not 1 <= int(host["ansible_port"]) <= 65535):
            raise ValueError()
        if host["ansible_connection"] == "local" and host["ansible_user"] != ENV["USER"]:
            raise ValueError()
        if host["ansible_connection"] == "local" and host["ansible_host"] not in ("localhost", "127.0.0.1", "::1"):
            raise ValueError()
        if host["ansible_python_interpreter"] == "/usr/bin/python3":
            raise ValueError()
        if host.get("ansible_become", False) not in (False, "false", "False"):
            raise ValueError()
        return {"name": name, "host": host["ansible_host"], "user": host["ansible_user"], "connection": host.get("ansible_connection", "ssh")}
    except (ValueError, TypeError, KeyError, AttributeError):
        raise EngineError("Inventory must select one non-root target with explicit host/user/safe Python and no ambient become.") from None


def inventory_fingerprint(path):
    sources = {path.absolute()}
    for root in (path.absolute().parent, ROOT):
        for name in ("host_vars", "group_vars"):
            folder = root / name
            if folder.is_symlink():
                raise EngineError("Inventory variables cannot be symlink redirects.")
            if folder.exists():
                sources.update(folder.rglob("*"))
    hashes = {}
    for source in sorted(sources):
        if source.is_symlink():
            raise EngineError("Inventory variables cannot be symlink redirects.")
        if source.is_file():
            if source.stat().st_size > MAX_BYTES or len(hashes) > 1000:
                raise EngineError("Inventory variables exceed the bounded static selection scope.")
            hashes[str(source)] = hashlib.sha256(source.read_bytes()).hexdigest()
    return hashlib.sha256(json.dumps(hashes, sort_keys=True).encode()).hexdigest()


def run_native(config, operation, review_digest="", *, inventory=None, mode="apply"):
    executable = Path(sys.executable).parent / "ansible-playbook"
    if not executable.is_file():
        raise EngineError("Install an isolated Ansible/PyYAML controller first; this command does not bootstrap tools.")
    # Temporary controller files contain public config only; never a durable approval.
    with tempfile.TemporaryDirectory(prefix="macsetup-public-") as scratch:
        directory = Path(scratch)
        cfg = directory / "ansible.cfg"
        cfg.write_text("[defaults]\nstdout_callback=default\nretry_files_enabled=False\nhost_key_checking=True\n"
                       + "library=" + str(ROOT / "library") + "\nmodule_utils=" + str(ROOT / "module_utils") + "\n"
                       + "[inventory]\nenable_plugins=ini,yaml,host_list\n")
        variables = directory / "scope.json"
        values = {"mac_setup_config": config, "mac_setup_operation": operation,
                  "mac_setup_review_digest": review_digest,
                  "ansible_ssh_common_args": "-o BatchMode=yes -o StrictHostKeyChecking=yes"}
        if inventory is None:
            values.update(ansible_connection="local", ansible_python_interpreter=sys.executable)
        variables.write_text(json.dumps(values))
        env = dict(ENV, ANSIBLE_CONFIG=str(cfg), ANSIBLE_NOCOLOR="1")
        argv = [str(executable), str(ROOT / ("observe.yml" if mode == "observe" else "general.yml")),
                "-i", str(inventory.absolute()) if inventory else "localhost,", "-e", "@" + str(variables)]
        if config["settings"]["remote_login"] and mode == "apply":
            argv.append("--ask-become-pass")
        if os.environ.get("SSH_AUTH_SOCK"):
            env["SSH_AUTH_SOCK"] = os.environ["SSH_AUTH_SOCK"]
        result = subprocess.run(argv, env=env, cwd=ROOT, check=False,
                                capture_output=mode == "observe", text=True)
        if result.returncode:
            raise EngineError("Native setup stopped; completed actions remain, unrelated state is preserved. Re-plan before resuming.")
        if mode == "observe":
            messages = re.findall(r'"msg":\s*("(?:\\.|[^"\\])*")', result.stdout)
            scopes = [json.loads(item).removeprefix("MAC_SETUP_SCOPE=") for item in messages if json.loads(item).startswith("MAC_SETUP_SCOPE=")]
            if len(scopes) != 1:
                raise EngineError("Native target did not return exactly one public observation.")
            return json.loads(scopes[0])


def main():
    parser = Parser(description="Public standalone YAML package setup; no private discovery or downloads.")
    parser.add_argument("--config", required=True, type=Path)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--plan", action="store_true", help="offline, no host/provider checks or changes")
    mode.add_argument("--apply", action="store_true", help="observe and interactively review selected package effects")
    parser.add_argument("--operation", choices=("setup", "update", "finish"), default="setup")
    parser.add_argument("--non-interactive", action="store_true")
    parser.add_argument("-i", "--inventory", type=Path, help="advanced: one explicit target; requires existing safe target Python")
    try:
        args = parser.parse_args()
        config, checksum = read_config(args.config)
        if args.plan:
            print(json.dumps(build_plan(config, operation=args.operation), indent=2, sort_keys=True))
            return 3
        if args.non_interactive or not sys.stdin.isatty() or not sys.stdout.isatty():
            raise EngineError("Needs human: reviewed apply requires an interactive terminal; --plan is available unattended.")
        inventory_checksum = None
        if args.inventory:
            target = inspect_inventory(args.inventory)
            inventory_checksum = inventory_fingerprint(args.inventory)
            print("Observe the explicitly selected SSH/local target: " + json.dumps(target, sort_keys=True))
            print("SSH authentication and first-contact host-key verification may need your terminal; failed verification is never bypassed.")
        def observe_scope(scope):
            return run_native(scope, args.operation, inventory=args.inventory, mode="observe") if args.inventory else observe(scope, args.operation)
        # A finite prerequisite sequence, not an extensible workflow engine.
        for stage_number in range(4):
            observation = observe_scope(config)
            stage = prerequisite_scope(config, observation)
            scope = stage or config
            if stage:
                print("Selected tap/runtime prerequisites need a separate scope; packages and tools will be reobserved afterward.")
                observation = observe_scope(stage)
            print(json.dumps(observation["plan"], indent=2, sort_keys=True))
            if observation["plan"]["status"] != "planned":
                raise EngineError("Scope is unresolved or blocked; no changes made in this stage.")
            if not any(item["action"] in MUTATIONS for item in observation["plan"]["actions"]):
                print("Selected package state is already satisfied; no changes requested.")
                return 0
            print("Package installation can run upstream package scripts and change dependencies. No private configuration is managed.")
            source_before = checkout_digest()
            if input("Apply exactly the scope above? Type apply: ").strip() != "apply":
                raise EngineError("Canceled without starting this scope.")
            if read_config(args.config)[1] != checksum:
                raise EngineError("Chosen YAML changed after review; start a new plan.")
            if checkout_digest() != source_before:
                raise EngineError("Engine source changed after review; start a new plan.")
            if args.inventory and (inventory_fingerprint(args.inventory) != inventory_checksum or inspect_inventory(args.inventory) != target):
                raise EngineError("Inventory changed after target selection; review again.")
            run_native(scope, args.operation, observation["digest"], inventory=args.inventory)
            if not stage:
                print("Native package scope completed. This is not application/CLI/login/private-runtime readiness.")
                return 0
        raise EngineError("Runtime prerequisites changed unexpectedly; re-plan the remaining scope.")
    except (ConfigError, EngineError) as exc:
        print(str(exc), file=sys.stderr)
    except (OSError, UnicodeError, EOFError, KeyboardInterrupt):
        print("Stopped safely; configuration/input or prerequisites need attention.", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
