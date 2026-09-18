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
APPLICATIONS = Path("/Applications")
ENV = {
    "HOME": str(HOME), "USER": pwd.getpwuid(os.getuid()).pw_name,
    "LOGNAME": pwd.getpwuid(os.getuid()).pw_name,
    "PATH": PREFIX + "/bin:/usr/bin:/bin:/usr/sbin:/sbin",
    "LANG": "en_US.UTF-8", "LC_ALL": "en_US.UTF-8",
    "HOMEBREW_NO_AUTO_UPDATE": "1", "HOMEBREW_NO_ANALYTICS": "1",
    "HOMEBREW_NO_INSTALL_CLEANUP": "1", "HOMEBREW_NO_INSTALL_UPGRADE": "1",
    "HOMEBREW_NO_INSTALLED_DEPENDENTS_CHECK": "1",
    "HOMEBREW_NO_ENV_HINTS": "1", "HOMEBREW_NO_COLOR": "1",
    "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_SYSTEM": "/dev/null",
    "GIT_TERMINAL_PROMPT": "0", "GIT_ASKPASS": "/usr/bin/false",
    "GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "credential.helper", "GIT_CONFIG_VALUE_0": "",
    "npm_config_userconfig": "/dev/null", "npm_config_globalconfig": str(PUBLIC / ".npm-empty-global-config"),
    "npm_config_registry": "https://registry.npmjs.org/", "npm_config_ignore_scripts": "true",
    "npm_config_audit": "false", "npm_config_fund": "false", "npm_config_logs_max": "0",
    "UV_NO_CONFIG": "1", "UV_NO_CACHE": "1", "UV_KEYRING_PROVIDER": "disabled",
    "UV_CREDENTIALS_DIR": "/var/empty", "NETRC": "/dev/null",
    "UV_DEFAULT_INDEX": "https://pypi.org/simple", "UV_PYTHON_DOWNLOADS": "never",
    "UV_TOOL_DIR": str(PUBLIC / "uv-tools"), "UV_TOOL_BIN_DIR": str(PUBLIC / "bin"),
    "UV_PYTHON_INSTALL_DIR": str(PUBLIC / "python"),
}
MUTATIONS = {"install", "change-version", "hold", "ensure-setting", "prepare-tap"}


class ProductionReader:
    def has_directory(self, path):
        try:
            metadata = Path(path).lstat()
        except FileNotFoundError:
            return False
        if not stat.S_ISDIR(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
            raise EngineError("Selected public tool prefix is not a regular directory.")
        return True

    def has_file(self, path, root):
        candidate = Path(path)
        if not candidate.exists() and not candidate.is_symlink():
            return False
        if not candidate.is_file() or not candidate.resolve().is_relative_to(Path(root).resolve()):
            raise EngineError("Selected runtime tool path is not a regular file within its formula.")
        return True

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


def active_runtime_evidence():
    """Protect formula roots actually hosting this interpreter/controller.

    An unrelated installed Homebrew Ansible is not the active isolated runtime.
    On SSH targets these paths describe the target's native module interpreter.
    """
    paths = {sys.executable, sys.prefix, sys.base_prefix}
    expanded = set()
    for path in paths:
        if not isinstance(path, str) or not path or not Path(path).is_absolute():
            raise EngineError("Active runtime location cannot be identified safely.")
        expanded.update((path, str(Path(path).resolve())))
    protected = set()
    for path in expanded:
        for base in (PREFIX + "/Cellar/", PREFIX + "/opt/"):
            if path.startswith(base):
                name = path[len(base):].split("/", 1)[0]
                if not re.fullmatch(planner.IDS["formulae"], name) or "/" in name:
                    raise EngineError("Active Homebrew runtime formula cannot be identified safely.")
                protected.add(name)
    return {"paths": sorted(expanded), "formulae": sorted(protected)}


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
    if selected(config, "npm"):
        try:
            (PUBLIC / ".npm-empty-global-config").lstat()
        except FileNotFoundError:
            pass
        else:
            raise EngineError("Reserved empty npm configuration path already exists; preserved without reading it.")


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


def tap_observations(config, reader, bindings):
    """Read names/trust/Git identity, never load unapproved formula Ruby."""
    names = [name for name in selected(config, "formulae") if "/" in name]
    if not names:
        return []
    installed = set(reader.run([BREW, "tap"]).splitlines())
    trust = parsed_json(reader.run([BREW, "trust", "--json=v1"]))
    if any(type(trust.get(key)) is not list or any(type(x) is not str for x in trust[key])
           for key in ("taps", "formulae")):
        raise EngineError("Selected-item Homebrew trust evidence is unavailable.")
    preparation, sources = [], {}
    for name in names:
        owner, repository, _ = name.split("/")
        tap = owner + "/" + repository
        missing = tap not in installed
        trusted = name in trust["formulae"] or tap in trust["taps"]
        if not missing:
            root = PREFIX if PREFIX == "/opt/homebrew" else PREFIX + "/Homebrew"
            path = root + "/Library/Taps/" + owner + "/homebrew-" + repository
            git = ["/usr/bin/git", "-c", "core.fsmonitor=false", "-c", "core.hooksPath=/dev/null", "-C", path]
            remote = reader.run(git + ["remote", "get-url", "origin"]).strip()
            expected = "https://github.com/" + owner + "/homebrew-" + repository
            if remote.removesuffix(".git") != expected:
                raise EngineError("Selected tap has a noncanonical remote; preserve it for separate review.")
            head = reader.run(git + ["rev-parse", "HEAD"]).strip()
            dirty = reader.run(git + ["status", "--porcelain", "--untracked-files=all"]).strip()
            if not re.fullmatch(r"[a-f0-9]{40}", head) or dirty:
                raise EngineError("Selected tap must have a clean committed source before review.")
            sources[tap] = head
        if missing or not trusted:
            if not config["packages"]["formulae"][name].get("trust"):
                raise EngineError("Selected public tap/item trust needs supported formula trust selection and scope review; no formula code loaded.")
            preparation.append({"id": name, "tap": tap, "missing": missing, "trust_missing": not trusted})
    bindings["tap_sources"] = sources
    if preparation:
        bindings["tap_preparation"] = preparation
    return preparation


def brew_observations(config, reader, state, details, operation="setup"):
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
            if provider == "casks" and entry.get("accept_external") and not record.get("installed"):
                apps = [artifact["app"] for artifact in record.get("artifacts", []) if type(artifact) is dict and "app" in artifact]
                if len(apps) != 1 or type(apps[0]) is not list or len(apps[0]) != 1:
                    raise EngineError("External-app preservation requires one simple cask application artifact.")
                app = apps[0][0]
                if type(app) is not str or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 .+_-]*\.app", app):
                    raise EngineError("External application artifact is not a safe application basename.")
                path = APPLICATIONS / app
                if path.exists() or path.is_symlink():
                    item = path.lstat()
                    parent = path.parent.lstat()
                    if (stat.S_ISLNK(item.st_mode) or not stat.S_ISDIR(item.st_mode)
                            or stat.S_ISLNK(parent.st_mode) or item.st_uid not in (0, os.getuid())
                            or item.st_mode & stat.S_IWOTH):
                        raise EngineError("Existing external application metadata is unsafe; preserve it for manual review.")
                    state["installed"][provider][name] = {"present": True, "held": False}
                    details[name] = {"external_app": {"path": str(path), "inode": item.st_ino, "device": item.st_dev,
                                                   "uid": item.st_uid, "mode": item.st_mode}}
                    continue
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
            if "/" in name:
                if record.get("full_name") != name or record.get("tap") != name.rsplit("/", 1)[0]:
                    raise EngineError("Selected tap formula identity differs from its requested source.")
                checksum = record.get("ruby_source_checksum", {}).get("sha256", "")
                if not re.fullmatch(r"[a-f0-9]{64}", checksum):
                    raise EngineError("Selected tap formula source checksum is unavailable.")
                remote = record  # Approved local tap revision, not a fictitious core API entry.
                details[name] = {"source_sha256": checksum}
                if operation == "setup" and present:
                    continue
            else:
                canonical = record.get("name", name) if provider == "formulae" else name
                if provider == "formulae" and canonical != name:
                    aliases, oldnames = record.get("aliases", []), record.get("oldnames", [])
                    if (type(canonical) is not str or "/" in canonical
                            or record.get("tap") != "homebrew/core"
                            or record.get("full_name") != canonical
                            or type(aliases) is not list or type(oldnames) is not list
                            or any(type(item) is not str for item in aliases + oldnames)
                            or name not in aliases + oldnames):
                        raise EngineError("Homebrew core rename lacks matching canonical alias evidence.")
                    planner.identifier("formulae", canonical)
                if provider == "formulae":
                    details[name] = {"canonical_core_name": canonical}
                # Presence-only setup does not select a newer recipe. Keep
                # installed/pin/source identity evidence, without inventing
                # availability or requiring an unused remote candidate match.
                if operation == "setup" and present:
                    continue
                remote = reader.get_json("https://formulae.brew.sh/api/" + ("formula/" if provider == "formulae" else "cask/") + canonical + ".json")
                if provider == "formulae" and remote.get("name", canonical) != canonical:
                    raise EngineError("Public core metadata does not match the observed canonical formula.")
            candidate = version(remote["versions"]["stable"] if provider == "formulae" else remote["version"])
            local_candidate = version(record["versions"]["stable"] if provider == "formulae" else record["version"])
            if local_candidate != candidate:
                raise EngineError("Homebrew cached metadata differs from the public candidate; refresh metadata separately and review again.")
            available = not remote.get("disabled", False)
            state["availability"][provider][name] = {"available": available, "candidate": candidate}
            details.setdefault(name, {}).update(dependencies=remote.get("dependencies", []), candidate=candidate)
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
    candidates = [prefix + suffix for suffix in (
        "/lib/node_modules/npm/bin/npm-cli.js", "/libexec/lib/node_modules/npm/bin/npm-cli.js")]
    available = [path for path in candidates if reader.has_file(path, prefix)]
    if len(available) != 1:
        raise EngineError("Selected Node formula must contain exactly one supported npm CLI layout; no PATH fallback is allowed.")
    npm = available[0]
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
    # An absent verified setup-owned prefix is genuinely empty, not a failed
    # npm command. Never swallow an error from an existing installation.
    dependencies = {}
    if reader.has_directory(binding["prefix"]):
        result = parsed_json(reader.run([binding["node"], binding["npm"], "list", "--global", "--prefix", binding["prefix"], "--depth=0", "--json"]))
        dependencies = result.get("dependencies", {})
    for name, entry in selected(config, "npm").items():
        state["installed"]["npm"][name] = {"present": name in dependencies}
        if name in dependencies:
            state["installed"]["npm"][name]["version"] = version(dependencies[name]["version"])
        url = "https://registry.npmjs.org/" + urllib.parse.quote(name, safe="") + "/" + entry.get("version", "latest")
        record = reader.get_json(url)
        candidate = version(record["version"])
        if entry.get("version") and candidate != entry["version"]:
            raise EngineError("npm metadata does not match the selected exact version.")
        state["availability"]["npm"][name] = {"available": True, "candidate": candidate, "versions": [candidate]}
        bindings.setdefault("npm_metadata", {})[name] = digest({"version": candidate, "scripts": record.get("scripts", {}), "dist": record.get("dist", {})})
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
        bindings["active_runtime"] = active_runtime_evidence()
        if operation != "finish":
            pending_taps = tap_observations(config, reader, bindings)
            if pending_taps:
                if operation != "setup":
                    raise EngineError("Missing public tap/item trust must be prepared in a separately reviewed setup scope, not during update.")
                plan = {"schema_version": 1, "profile": config["profile"], "operation": operation,
                        "evidence": "provider-observation", "status": "planned", "executable": False,
                        "actions": [{"provider": "formulae", "id": item["id"], "action": "prepare-tap",
                                     "reason": "review-public-tap-download-and-selected-formula-trust-before-loading-code"}
                                    for item in pending_taps],
                        "warnings": ["Only supported public tap preparation and selected-item trust will run. Formula code and package effects require fresh observation and review afterward."]}
                result = {"state": state, "plan": plan, "bindings": bindings}
                result["digest"] = scope_fingerprint(config, operation, result)
                return result
            brew_observations(config, reader, state, details, operation=operation)
            bindings["brew_sources"] = details
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
        core_names = [item["canonical_core_name"] for item in details.values() if "canonical_core_name" in item]
        for action in plan["actions"]:
            canonical = details.get(action["id"], {}).get("canonical_core_name")
            if action["provider"] == "formulae" and canonical and core_names.count(canonical) > 1:
                action.update(action="blocked", reason="multiple-selected-names-resolve-to-one-core-formula")
            if action["provider"] == "casks" and details.get(action["id"], {}).get("external_app"):
                action.update(action="preserve", reason="external-app-present-no-adoption-version-claim-or-update")
        kinds = {action["action"] for action in plan["actions"]}
        plan["status"] = "blocked" if "blocked" in kinds else ("unresolved" if "unresolved" in kinds else "planned")
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
            if action["provider"] != "npm" or action["action"] not in MUTATIONS:
                continue
            if config["packages"]["npm"][action["id"]].get("allow_lifecycle_scripts"):
                action["reason"] = "exact-npm-install-allows-package-and-transitive-dependency-lifecycle-scripts-not-sandboxed"
                plan["warnings"].append("Selected npm lifecycle scripts run as your user and can access user files; an isolated install prefix is not a security sandbox.")
            elif action["id"] in bindings.get("npm_lifecycle_blocked", []):
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
        protected = set(bindings["active_runtime"]["formulae"])
        protected.update(name for name, entry in selected(config, "formulae").items()
                         if entry.get("hold") or state["installed"]["formulae"].get(name, {}).get("held"))
        bindings["protected_formulae"] = sorted(protected)
        changing = {item["id"] for item in plan["actions"] if item["provider"] == "formulae" and item["action"] in {"install", "change-version"}}
        for action in plan["actions"]:
            if action["provider"] in ("formulae", "casks") and action["action"] in {"install", "change-version"}:
                flag = "--formula" if action["provider"] == "formulae" else "--cask"
                dependencies = [name.strip() for name in reader.run([BREW, "deps", "--union", "--include-build", flag, action["id"]]).splitlines() if name.strip()]
                affected = set(dependencies)
                if action["provider"] == "formulae":
                    affected.add(action["id"])
                conflict = False
                for dependency in sorted(affected):
                    planner.identifier("formulae", dependency)
                    impact = {dependency} | {name.strip() for name in reader.run([BREW, "uses", "--installed", "--recursive", dependency]).splitlines() if name.strip()}
                    if not impact & protected:
                        continue
                    if dependency in changing:
                        conflict = True
                        break
                    # A shared dependency already at its installed recipe version
                    # does not imply mutation of every reverse-dependent runtime.
                    # Only prove this narrow no-change case; uncertainty blocks.
                    evidence = parsed_json(reader.run([BREW, "info", "--json=v2", "--formula", dependency])).get("formulae", [])
                    if len(evidence) != 1:
                        raise EngineError("Protected runtime dependency evidence is ambiguous.")
                    item = evidence[0]
                    revision = item.get("revision", 0)
                    if type(revision) is not int or revision < 0:
                        raise EngineError("Protected runtime dependency revision is invalid.")
                    candidate = version(item["versions"]["stable"]) + ("_" + str(revision) if revision else "")
                    installed_versions = sorted(version(record["version"]) for record in item.get("installed", []))
                    unchanged = item.get("outdated") is False and candidate in installed_versions
                    bindings.setdefault("protected_dependency_checks", {})[dependency] = {
                        "candidate": candidate, "installed": installed_versions,
                        "unchanged": unchanged, "protected_dependents": sorted(impact & protected)}
                    if not unchanged:
                        conflict = True
                        break
                if conflict:
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
    tap_stage = observed["bindings"].get("tap_preparation")
    if prerequisite_scope(config, observed) is not None and not tap_stage:
        raise EngineError("Selected runtimes need their own prerequisite scope before tool effects can be reviewed.")
    if not isinstance(expected_digest, str) or observed["digest"] != expected_digest:
        raise EngineError("Target, source, configuration or material package effects changed; review again.")
    operations = []
    if tap_stage:
        prepared = set()
        for item in tap_stage:
            if item["missing"] and item["tap"] not in prepared:
                operations.append({"provider": "formulae", "id": item["id"], "argv": [BREW, "tap", item["tap"]], "environment": dict(ENV), "become": False})
                prepared.add(item["tap"])
            if item["trust_missing"]:
                operations.append({"provider": "formulae", "id": item["id"], "argv": [BREW, "trust", "--formula", item["id"]], "environment": dict(ENV), "become": False})
        return {"operations": operations, "digest": observed["digest"], "plan": observed["plan"], "shell_path": None}
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
            scripts = config["packages"]["npm"][name].get("allow_lifecycle_scripts", False)
            env["npm_config_ignore_scripts"] = "false" if scripts else "true"
            env["PATH"] = str(Path(bound["node"]).parent) + ":" + env["PATH"]
            argv = [bound["node"], bound["npm"], "install", "--global", "--ignore-scripts=false" if scripts else "--ignore-scripts", "--prefix", bound["prefix"], name + "@" + wanted]
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
        if provider == "npm":
            operations[-1]["directory"] = observed["bindings"]["npm"]["prefix"]
    return {"operations": operations, "digest": observed["digest"], "plan": observed["plan"],
            "shell_path": observed["bindings"].get("shell_path")}


def prerequisite_scope(config, observed):
    """A small explicit selected-runtime stage, never hidden dependencies or approval."""
    config = validate_config(config)
    if observed["bindings"].get("tap_preparation"):
        return config
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
