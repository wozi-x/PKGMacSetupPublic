"""Public package observations and fixed argv compilation. Never executes changes.

The injected reader is for tests only. The CLI always constructs ProductionReader.
Ansible, not this module, executes the freshly checked argv records.
"""
from copy import deepcopy
import hashlib
import inspect
import json
import os
from pathlib import Path
import platform
import pwd
import re
import shlex
import stat
import subprocess
import sys
import urllib.parse
import urllib.request

try:
    from ansible.module_utils import mac_setup_planner as planner
except ImportError:
    import mac_setup_planner as planner
ConfigError = planner.ConfigError
build_plan = planner.build_plan
validate_config = planner.validate_config


class EngineError(ValueError):
    """A value-free operational diagnostic safe for ordinary output."""


BREW = "/opt/homebrew/bin/brew" if platform.machine() == "arm64" else "/usr/local/bin/brew"
PREFIX = str(Path(BREW).parent.parent)
ROOT = Path(__file__).resolve().parent.parent
HOME = Path(pwd.getpwuid(os.getuid()).pw_dir)
PUBLIC = HOME / "Library/Application Support/MacSetup/public"
ENV = {
    "HOME": str(HOME), "USER": pwd.getpwuid(os.getuid()).pw_name,
    "LOGNAME": pwd.getpwuid(os.getuid()).pw_name,
    "PATH": PREFIX + "/bin:/usr/bin:/bin:/usr/sbin:/sbin",
    "LANG": "en_US.UTF-8", "LC_ALL": "en_US.UTF-8",
    "HOMEBREW_NO_AUTO_UPDATE": "1", "HOMEBREW_NO_ANALYTICS": "1",
    "HOMEBREW_NO_INSTALL_CLEANUP": "1", "HOMEBREW_NO_INSTALL_UPGRADE": "1",
    "HOMEBREW_NO_INSTALLED_DEPENDENTS_CHECK": "1",
    "HOMEBREW_NO_ENV_HINTS": "1", "HOMEBREW_NO_COLOR": "1",
    "npm_config_userconfig": "/dev/null", "npm_config_globalconfig": "/dev/null",
    "npm_config_registry": "https://registry.npmjs.org/", "npm_config_ignore_scripts": "true",
    "npm_config_audit": "false", "npm_config_fund": "false", "npm_config_logs_max": "0",
    "UV_NO_CONFIG": "1", "UV_NO_CACHE": "1", "UV_KEYRING_PROVIDER": "disabled",
    "UV_CREDENTIALS_DIR": "/var/empty", "NETRC": "/dev/null",
    "UV_DEFAULT_INDEX": "https://pypi.org/simple", "UV_PYTHON_DOWNLOADS": "never",
    "UV_TOOL_DIR": str(PUBLIC / "uv-tools"), "UV_TOOL_BIN_DIR": str(PUBLIC / "bin"),
    "UV_PYTHON_INSTALL_DIR": str(PUBLIC / "python"),
}
MUTATIONS = {"install", "change-version", "hold", "ensure-setting"}


class ProductionReader:
    def run(self, argv):
        try:
            result = subprocess.run(argv, env=ENV, cwd="/", stdin=subprocess.DEVNULL,
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                    text=True, timeout=90, check=False)
        except (OSError, subprocess.SubprocessError, UnicodeError):
            raise EngineError("Provider inspection failed; no package changes were requested.") from None
        if (result.returncode == 1 and argv[:3] == ["/usr/bin/defaults", "read", "com.apple.dock"]
                and "does not exist" in result.stderr):
            return "__MISSING__"
        if result.returncode or len(result.stdout) > 16 * 1024 * 1024:
            raise EngineError("Provider inspection failed; check required tools and local permissions.")
        return result.stdout

    def get_json(self, url):
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme != "https" or parsed.hostname not in (
                "formulae.brew.sh", "registry.npmjs.org", "pypi.org", "itunes.apple.com"):
            raise EngineError("Unsupported public metadata source.")
        # No environment proxy credentials, cookies, netrc, authentication or redirects.
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *args, **kwargs):
                return None
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
        try:
            with opener.open(urllib.request.Request(url, headers={"User-Agent": "MacSetup/1"}), timeout=30) as response:
                data = response.read(16 * 1024 * 1024 + 1)
            if len(data) > 16 * 1024 * 1024:
                raise ValueError()
            return json.loads(data)
        except Exception:
            raise EngineError("Public package metadata unavailable; no latest-version fallback is allowed.") from None


def parsed_json(text):
    try:
        return json.loads(text)
    except (ValueError, TypeError):
        raise EngineError("Provider returned invalid structured evidence.") from None


def version(value):
    if (type(value) is not str or not value or len(value) > 200
            or not re.fullmatch(r"[0-9A-Za-z.,+_!@:-]+", value)):
        raise EngineError("Provider returned an unsupported version value.")
    return value


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def source_digest():
    # Native Ansible packages these same canonical modules in its target zip.
    return digest([inspect.getsource(sys.modules[__name__]), inspect.getsource(planner)])


def checkout_digest():
    hashes = {}
    for pattern in ("*.py", "*.yml", "*.sh", "*.cfg", "library/*.py", "module_utils/*.py", "roles/mac_setup/**/*.yml"):
        for path in sorted(ROOT.glob(pattern)):
            if path.is_symlink():
                raise EngineError("Engine source symlinks are unsupported.")
            hashes[str(path.relative_to(ROOT))] = hashlib.sha256(path.read_bytes()).hexdigest()
    return digest(hashes)


def scope_fingerprint(config, operation="setup", observation=None):
    if observation is None:
        return observe(config, operation)["digest"]
    return digest({"config": validate_config(config), "operation": operation,
                   "target": [platform.system(), platform.machine(), platform.node(), os.getuid(), str(HOME)],
                   "source": source_digest(), "plan": observation["plan"],
                   "bindings": observation.get("bindings", {})})


def selected(config, provider):
    return {name: entry for name, entry in config["packages"][provider].items() if entry["enabled"]}


def validate_public_paths(config):
    """Do not follow a pre-existing redirect into another tool/private directory."""
    if not (selected(config, "npm") or selected(config, "uv_tools") or config["runtimes"]["uv_python"]):
        return
    paths = [PUBLIC, PUBLIC / "npm", PUBLIC / "uv-tools", PUBLIC / "bin", PUBLIC / "python"]
    paths.extend(PUBLIC / "npm" / name for name in selected(config, "formulae") if name == "node" or name.startswith("node@"))
    for path in paths:
        if not path.is_relative_to(HOME):
            raise EngineError("Public package storage must stay beneath the target user's home.")
        current = HOME
        for part in path.relative_to(HOME).parts:
            current = current / part
            try:
                info = current.lstat()
            except FileNotFoundError:
                break
            if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o022:
                raise EngineError("Public package storage has an unsafe existing directory; it was preserved for review.")


def shell_path_scope(config):
    if not selected(config, "npm") and not selected(config, "uv_tools"):
        return None
    path = HOME / ".zprofile"
    exists, mode = False, None
    try:
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o022 or info.st_size > 1024 * 1024:
            raise EngineError("Shell profile is not a safe owner-controlled regular file; preserve it and review PATH integration.")
        content = path.read_bytes()
        exists, mode = True, format(stat.S_IMODE(info.st_mode), "04o")
    except FileNotFoundError:
        content = b""
    directories = []
    if selected(config, "npm"):
        choices = [name for name in selected(config, "formulae") if name == "node" or name.startswith("node@")]
        if len(choices) == 1:
            directories.extend([PREFIX + "/opt/" + choices[0] + "/bin", str(PUBLIC / "npm" / choices[0] / "bin")])
    if selected(config, "uv_tools"):
        directories.append(str(PUBLIC / "bin"))
    block = "export PATH=" + shlex.quote(":".join(directories)) + ':"$PATH"'
    start, end = b"# BEGIN MACSETUP PUBLIC TOOLS", b"# END MACSETUP PUBLIC TOOLS"
    if content.count(start) != content.count(end) or content.count(start) > 1:
        raise EngineError("Shell profile has ambiguous public-owned markers; preserve it for review.")
    expected = start + b"\n" + block.encode() + b"\n" + end
    return {"path": str(path), "block": block, "checksum": hashlib.sha256(content).hexdigest(),
            "satisfied": expected in content, "exists": exists, "mode": mode, "uid": os.getuid()}


def brew_observations(config, reader, state, details):
    requested = {provider: selected(config, provider) for provider in ("formulae", "casks")}
    # Inspect only selected public IDs, never enumerate private applications/configuration.
    for provider, entries in requested.items():
        flag = "--formula" if provider == "formulae" else "--cask"
        for name, entry in entries.items():
            info = parsed_json(reader.run([BREW, "info", "--json=v2", flag, name]))
            records = info.get("formulae" if provider == "formulae" else "casks", [])
            if len(records) != 1:
                raise EngineError("Homebrew did not identify exactly one selected package.")
            record = records[0]
            installed = record.get("installed", [])
            if provider == "formulae":
                versions = [version(item["version"]) for item in installed]
                present = bool(versions)
                current = versions[-1] if versions else None
            else:
                present = bool(installed)
                current = version(installed if isinstance(installed, str) else installed[-1]) if present else None
            held = record.get("pinned")
            if type(held) is not bool:
                # Current brew JSON must disclose pin state; never infer false.
                raise EngineError("Homebrew pin evidence is unavailable; update Homebrew separately.")
            state["installed"][provider][name] = {"present": present, "held": held}
            if current:
                state["installed"][provider][name]["version"] = current
            remote = reader.get_json("https://formulae.brew.sh/api/" + ("formula/" if provider == "formulae" else "cask/") + name + ".json")
            candidate = version(remote["versions"]["stable"] if provider == "formulae" else remote["version"])
            local_candidate = version(record["versions"]["stable"] if provider == "formulae" else record["version"])
            if local_candidate != candidate:
                raise EngineError("Homebrew cached metadata differs from the public candidate; refresh metadata separately and review again.")
            available = not remote.get("disabled", False)
            state["availability"][provider][name] = {"available": available, "candidate": candidate}
            details[name] = {"dependencies": remote.get("dependencies", []), "candidate": candidate}
    if requested["casks"]:
        state["capabilities"]["cask_hold"] = "--cask" in reader.run([BREW, "pin", "--help"])


def node_binding(config, reader, state, bindings):
    choices = [name for name in selected(config, "formulae") if name == "node" or name.startswith("node@")]
    if len(choices) != 1:
        return
    name = choices[0]
    if not state["installed"]["formulae"].get(name, {}).get("present"):
        return
    prefix = reader.run([BREW, "--prefix", name]).strip()
    expected = PREFIX + "/opt/" + name
    if prefix != expected:
        raise EngineError("Selected Node runtime has an unexpected prefix.")
    node = prefix + "/bin/node"
    npm = prefix + "/lib/node_modules/npm/bin/npm-cli.js"
    observed = reader.run([node, "--version"]).strip().removeprefix("v")
    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", observed):
        raise EngineError("Selected Node executable version is invalid.")
    if "@" in name and not observed.startswith(name.split("@", 1)[1] + "."):
        raise EngineError("Selected Node executable does not match its requested series.")
    state["bindings"]["npm"] = {"runtime": name, "verified": True}
    bindings["npm"] = {"node": node, "npm": npm, "version": observed,
                       "prefix": str(PUBLIC / "npm" / name)}


def uv_observations(config, reader, state, bindings):
    if not config["runtimes"]["uv_python"] and not selected(config, "uv_tools"):
        return
    if not state["installed"]["formulae"].get("uv", {}).get("present"):
        return
    uv = PREFIX + "/bin/uv"
    installed_records = parsed_json(reader.run([uv, "--no-config", "python", "list", "--only-installed", "--managed-python", "--all-versions", "--output-format", "json"]))
    downloadable_records = parsed_json(reader.run([uv, "--no-config", "python", "list", "--only-downloads", "--all-versions", "--output-format", "json"]))
    if type(installed_records) is not list or type(downloadable_records) is not list:
        raise EngineError("uv returned invalid managed Python observations.")
    records = installed_records + downloadable_records
    if type(records) is not list:
        raise EngineError("uv returned invalid managed Python observations.")
    paths = {}
    for wanted in config["runtimes"]["uv_python"]:
        matches = [item for item in records if str(item.get("version")) == wanted
                   and item.get("implementation") == "cpython"
                   and item.get("variant", "default") == "default"
                   and item.get("os") == "macos"
                   and (item.get("arch") in ("aarch64", "arm64") if platform.machine() == "arm64"
                        else str(item.get("arch", "")).split("_v", 1)[0] == "x86_64")]
        installed = [item for item in matches if item.get("path")]
        state["installed"]["uv_python"][wanted] = {"present": bool(installed)}
        if installed:
            raw_path = installed[0]["path"]
            path = Path(str(HOME) + raw_path[1:] if raw_path.startswith("~/") else raw_path)
            if ".." in path.parts or not path.resolve().is_relative_to((PUBLIC / "python").resolve()):
                raise EngineError("uv interpreter is outside the public-owned runtime directory.")
            result = reader.run([str(path), "--version"]).strip()
            if result != "Python " + wanted:
                raise EngineError("uv interpreter does not match the exact requested version.")
            paths[wanted] = str(path)
            state["installed"]["uv_python"][wanted]["version"] = wanted
        state["availability"]["uv_python"][wanted] = {"available": bool(matches), "candidate": wanted}
    if len(config["runtimes"]["uv_python"]) == 1:
        wanted = config["runtimes"]["uv_python"][0]
        if wanted in paths:
            state["bindings"]["uv_tools"] = {"runtime": wanted, "verified": True}
            bindings["uv_tools"] = {"uv": uv, "python": paths[wanted]}
    listing = reader.run([uv, "--no-config", "tool", "list"])
    installed_tools = {}
    for line in listing.splitlines():
        match = re.fullmatch(r"([a-z0-9][a-z0-9-]*) v([^ ]+)", line)
        if match:
            installed_tools[match[1]] = version(match[2])
    for name, entry in selected(config, "uv_tools").items():
        state["installed"]["uv_tools"][name] = {"present": name in installed_tools}
        if name in installed_tools:
            state["installed"]["uv_tools"][name]["version"] = installed_tools[name]
        url = "https://pypi.org/pypi/" + name + ("/" + entry["version"] if "version" in entry else "") + "/json"
        record = reader.get_json(url)
        candidate = version(record["info"]["version"])
        state["availability"]["uv_tools"][name] = {"available": bool(record.get("urls")), "candidate": candidate, "versions": [candidate]}


def npm_observations(config, reader, state, bindings):
    if "npm" not in bindings:
        return
    binding = bindings["npm"]
    result = parsed_json(reader.run([binding["node"], binding["npm"], "list", "--global", "--prefix", binding["prefix"], "--depth=0", "--json"]))
    dependencies = result.get("dependencies", {})
    for name, entry in selected(config, "npm").items():
        state["installed"]["npm"][name] = {"present": name in dependencies}
        if name in dependencies:
            state["installed"]["npm"][name]["version"] = version(dependencies[name]["version"])
        url = "https://registry.npmjs.org/" + urllib.parse.quote(name, safe="") + "/" + entry.get("version", "latest")
        record = reader.get_json(url)
        candidate = version(record["version"])
        state["availability"]["npm"][name] = {"available": True, "candidate": candidate, "versions": [candidate]}
        if set(record.get("scripts", {})) & {"preinstall", "install", "postinstall"}:
            bindings.setdefault("npm_lifecycle_blocked", []).append(name)


def observe(config, operation="setup", reader=None):
    config = validate_config(config)
    validate_public_paths(config)
    if operation not in ("setup", "update", "finish"):
        raise EngineError("Unsupported operation.")
    if reader is None:
        if platform.system() != "Darwin" or os.getuid() == 0:
            raise EngineError("Apply requires the intended non-root macOS user.")
        reader = ProductionReader()
    state = {"installed": {p: {} for p in ("formulae", "casks", "mas", "npm", "uv_tools", "uv_python")},
             "availability": {p: {} for p in ("formulae", "casks", "mas", "npm", "uv_tools", "uv_python")},
             "capabilities": {}, "bindings": {}, "platform": platform.machine()}
    bindings, details = {}, {}
    try:
        if operation != "finish":
            brew_observations(config, reader, state, details)
            if selected(config, "npm"):
                node_binding(config, reader, state, bindings)
                npm_observations(config, reader, state, bindings)
            uv_observations(config, reader, state, bindings)
        elif selected(config, "mas"):
            listing = reader.run([PREFIX + "/bin/mas", "list"])
            installed = {}
            for line in listing.splitlines():
                match = re.fullmatch(r"([0-9]+)\s+.+\s+\(([^()]+)\)", line)
                if match:
                    installed[match[1]] = version(match[2])
            for name in selected(config, "mas"):
                state["installed"]["mas"][name] = {"present": name in installed}
                if name in installed:
                    state["installed"]["mas"][name]["version"] = installed[name]
                records = reader.get_json("https://itunes.apple.com/lookup?id=" + name + "&entity=macSoftware").get("results", [])
                state["availability"]["mas"][name] = {"available": bool(records)}
                if records:
                    state["availability"]["mas"][name]["candidate"] = version(records[0]["version"])
        plan = build_plan(config, operation=operation, state=state)
        plan["evidence"] = "provider-observation"
        plan["warnings"] = ["Package scripts/downloads and dependency changes occur only during reviewed apply; no transaction rollback.",
                            "Homebrew may affect dependencies; active controller/runtime dependencies are blocked.",
                            "Public tools use a separate public-owned directory; no private config, project environments or credentials are managed."]
        if operation == "setup":
            shell_scope = shell_path_scope(config)
            if shell_scope:
                bindings["shell_path"] = shell_scope
                plan["actions"].append({"provider": "settings", "id": "public-path", "action": "noop" if shell_scope["satisfied"] else "ensure-setting",
                                        "reason": "owned-zprofile-block-exposes-public-tools-and-selected-node-in-new-login-shell"})
        for action in plan["actions"]:
            if action["provider"] == "npm" and action["id"] in bindings.get("npm_lifecycle_blocked", []) and action["action"] in MUTATIONS:
                action.update(action="blocked", reason="npm-install-lifecycle-needs-separate-reviewed-provider-support")
                plan["status"] = "blocked"
        if operation == "setup" and config["settings"]["dock"]:
            for key, desired in (("autohide", "1"), ("show-recents", "0")):
                current = reader.run(["/usr/bin/defaults", "read", "com.apple.dock", key]).strip()
                if current not in ("0", "1", "__MISSING__"):
                    raise EngineError("Managed Dock key contains an unexpected value; preserved for review.")
                plan["actions"].append({"provider": "settings", "id": "dock-" + key,
                                        "action": "noop" if current == desired else "ensure-setting",
                                        "reason": "neutral-owned-dock-key-no-icon-clearing",
                                        "current_version": current, "requested_version": desired})
        if operation == "setup" and config["settings"]["remote_login"]:
            plan["actions"].append({"provider": "settings", "id": "remote-login",
                                    "action": "ensure-setting", "reason": "explicit-admin-read-then-enable-ssh-os-permission-may-be-required"})
        # Never replace the running interpreter/Ansible or request their dependency upgrades.
        protected = {"ansible", "ansible-core"}
        for part in Path(sys.executable).resolve().parts:
            if part.startswith("python@"):
                protected.add(part)
        for action in plan["actions"]:
            if action["provider"] in ("formulae", "casks") and action["action"] in {"install", "change-version"}:
                flag = "--formula" if action["provider"] == "formulae" else "--cask"
                dependencies = reader.run([BREW, "deps", "--union", "--include-build", flag, action["id"]]).splitlines()
                affected = set(dependencies)
                if action["provider"] == "formulae":
                    affected.add(action["id"])
                for dependency in sorted(affected):
                    affected.update(reader.run([BREW, "uses", "--installed", "--recursive", dependency]).splitlines())
                if affected & protected:
                    action.update(action="blocked", reason="would-replace-active-controller-or-its-runtime")
                    plan["status"] = "blocked"
        result = {"state": state, "plan": plan, "bindings": bindings}
        result["digest"] = scope_fingerprint(config, operation, result)
        return result
    except (KeyError, TypeError, IndexError, AttributeError, ConfigError, OSError):
        raise EngineError("Provider evidence is incomplete or incompatible; no mutations are authorized.") from None


def compile_operations(config, operation, expected_digest, reader=None):
    observed = observe(config, operation, reader)
    if observed["plan"]["status"] != "planned":
        raise EngineError("Scope is blocked or unresolved; no provider operation can be emitted.")
    if prerequisite_scope(config, observed) is not None:
        raise EngineError("Selected runtimes need their own prerequisite scope before tool effects can be reviewed.")
    if not isinstance(expected_digest, str) or observed["digest"] != expected_digest:
        raise EngineError("Target, source, configuration or material package effects changed; review again.")
    operations = []
    for action in sorted(observed["plan"]["actions"], key=lambda item: item["action"] != "hold"):
        if action["action"] not in MUTATIONS:
            continue
        provider, name = action["provider"], action["id"]
        wanted = action.get("requested_version")
        env = dict(ENV)
        if provider in ("formulae", "casks"):
            verb = "pin" if action["action"] == "hold" else ("install" if action["action"] == "install" else "upgrade")
            argv = [BREW, verb, "--formula" if provider == "formulae" else "--cask", name]
        elif provider == "npm":
            bound = observed["bindings"]["npm"]
            argv = [bound["node"], bound["npm"], "install", "--global", "--ignore-scripts", "--prefix", bound["prefix"], name + "@" + wanted]
        elif provider == "uv_python":
            argv = [PREFIX + "/bin/uv", "--no-config", "python", "install", name]
            env["UV_PYTHON_DOWNLOADS"] = "manual"
        elif provider == "uv_tools":
            bound = observed["bindings"]["uv_tools"]
            argv = [bound["uv"], "--no-config", "tool", "install", "--python", bound["python"], name + "==" + wanted]
        elif provider == "mas":
            argv = [PREFIX + "/bin/mas", "install" if action["action"] == "install" else "upgrade", name]
        elif provider == "settings":
            if name == "remote-login":
                argv = ["/usr/sbin/systemsetup", "-setremotelogin", "on"]
            elif name == "public-path":
                argv = []  # Native blockinfile owns this one explicitly reviewed block.
            else:
                argv = ["/usr/bin/defaults", "write", "com.apple.dock", name.removeprefix("dock-"), "-bool", "true" if wanted == "1" else "false"]
        else:
            raise EngineError("No executor exists for a selected provider.")
        operations.append({"provider": provider, "id": name, "argv": argv, "environment": env,
                           "become": provider == "settings" and name == "remote-login"})
    return {"operations": operations, "digest": observed["digest"], "plan": observed["plan"],
            "shell_path": observed["bindings"].get("shell_path")}


def prerequisite_scope(config, observed):
    """A small explicit selected-runtime stage, never hidden dependencies or approval."""
    config = validate_config(config)
    # Genuine hard blockers must not be bypassed with partial work.
    if any(item["action"] == "blocked" for item in observed["plan"]["actions"]):
        return None
    stage = {"schema_version": 1, "profile": config["profile"], "packages": {"formulae": {}}}
    def with_holds(scope):
        for provider in ("formulae", "casks"):
            for name, entry in selected(config, provider).items():
                if entry.get("hold"):
                    scope["packages"].setdefault(provider, {})[name] = entry
        return validate_config(scope)
    mutating = {item["id"] for item in observed["plan"]["actions"]
                if item["provider"] == "formulae" and item["action"] in ("install", "change-version")}
    for name, entry in selected(config, "formulae").items():
        needed = ((name == "node" or name.startswith("node@")) and selected(config, "npm")) or (
            name == "uv" and (selected(config, "uv_tools") or config["runtimes"]["uv_python"]))
        if needed:
            if name in mutating:
                stage["packages"]["formulae"][name] = entry
    if stage["packages"]["formulae"]:
        return with_holds(stage)
    missing = [name for name in config["runtimes"]["uv_python"]
               if not observed["state"]["installed"]["uv_python"].get(name, {}).get("present")]
    if missing and selected(config, "uv_tools") and "uv" in selected(config, "formulae") and observed["plan"]["operation"] != "update":
        stage["packages"]["formulae"] = {"uv": config["packages"]["formulae"]["uv"]}
        stage["runtimes"] = {"uv_python": missing}
        return with_holds(stage)
    return None
