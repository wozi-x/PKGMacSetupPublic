"""Offline public configuration validation and synthetic package decisions.

No provider commands, network, host inspection, approval, or execution here.
The optional observation dictionary is a test API, never an apply manifest.
"""
from copy import deepcopy
import re



class ConfigError(ValueError):
    """A safe, value-free configuration/observation diagnostic."""


PROVIDERS = ("formulae", "casks", "mas", "npm", "uv_tools", "uv_python")
MAX_BYTES = 1024 * 1024
IDS = {
    "formulae": r"[a-z0-9][a-z0-9+.-]*(?:@[0-9]+(?:\.[0-9]+)*)?",
    "casks": r"[a-z0-9][a-z0-9+.-]*",
    "mas": r"[1-9][0-9]*",
    "npm": r"(?:@[a-z0-9][a-z0-9._-]*/)?[a-z0-9][a-z0-9._-]*",
    "uv_tools": r"[a-z0-9]+(?:-[a-z0-9]+)*",
    "uv_python": r"3\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)",
}
NPM_VERSION = r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)(?:-[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?"
UV_VERSION = r"(?:[0-9]+!)?[0-9]+(?:\.[0-9]+)*(?:(?:a|b|rc)[0-9]+)?(?:\.post[0-9]+)?(?:\.dev[0-9]+)?(?:\+[a-z0-9]+(?:[.-][a-z0-9]+)*)?"


def fail(message):
    raise ConfigError(message)


def mapping(value, allowed=None):
    if type(value) is not dict or any(type(key) is not str for key in value):
        fail("Expected a mapping with quoted/string keys.")
    if allowed is not None and set(value) - set(allowed):
        fail("Unknown or unsupported field; public configuration has a closed schema.")
    return value


def boolean(value):
    if type(value) is not bool:
        fail("Expected a Boolean, not a string or number.")
    return value


def plain(value):
    if (type(value) is not str or not value or len(value) > 200
            or any(ord(char) < 32 or ord(char) == 127 for char in value)
            or any(token in value for token in ("{{", "}}", "{%", "%}"))):
        fail("Expected bounded literal text without control characters or templates.")
    return value


def identifier(provider, value):
    if not re.fullmatch(IDS[provider], plain(value)):
        fail("Invalid canonical package ID; paths, URLs, Git and alternate sources are unsupported.")
    return value


def unique_mapping(loader, node):
    result = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=True)
        if type(key) is not str:
            fail("YAML mapping keys must be strings; quote numeric app IDs.")
        if key in result:
            fail("Duplicate YAML key.")
        result[key] = loader.construct_object(value_node, deep=True)
    return result


def load_document(text):
    """Load one bounded, duplicate-free YAML document; never include other files."""
    import yaml

    class StrictLoader(yaml.SafeLoader):
        pass

    StrictLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, unique_mapping)
    if type(text) is not str:
        fail("Configuration must be UTF-8 text.")
    try:
        size = len(text.encode("utf-8"))
    except UnicodeError:
        fail("Configuration must be UTF-8 text.")
    if size > MAX_BYTES:
        fail("Configuration exceeds the text/size limit.")
    try:
        depth = count = 0
        for event in yaml.parse(text, Loader=yaml.SafeLoader):
            count += 1
            if isinstance(event, yaml.AliasEvent) or getattr(event, "anchor", None) or getattr(event, "tag", None):
                fail("YAML aliases, anchors and explicit tags are unsupported.")
            if isinstance(event, (yaml.MappingStartEvent, yaml.SequenceStartEvent)):
                depth += 1
            elif isinstance(event, (yaml.MappingEndEvent, yaml.SequenceEndEvent)):
                depth -= 1
            if depth > 12 or count > 15000:
                fail("Configuration exceeds nesting/entry limits.")
        value = yaml.load(text, Loader=StrictLoader)
    except yaml.YAMLError:
        fail("Invalid YAML syntax; source values are intentionally not echoed.")
    return mapping(value)


def load_config(text):
    return validate_config(load_document(text))


def validate_config(value):
    mapping(value, ("schema_version", "profile", "packages", "runtimes", "settings"))
    if type(value.get("schema_version")) is not int or value["schema_version"] != 1:
        fail("schema_version must be integer 1.")
    if type(value.get("profile")) is not str or value["profile"] not in ("admin", "dev"):
        fail("profile must be admin or dev.")
    result = {"schema_version": 1, "profile": value["profile"], "packages": {}}
    packages = mapping(value.get("packages", {}), PROVIDERS[:-1])
    count = 0
    for provider in PROVIDERS[:-1]:
        result["packages"][provider] = {}
        for name, entry in mapping(packages.get(provider, {})).items():
            count += 1
            identifier(provider, name)
            allowed = ["enabled"]
            if provider in ("formulae", "casks"):
                allowed.append("hold")
            if provider in ("npm", "uv_tools"):
                allowed.append("version")
            mapping(entry, allowed)
            normalized = {"enabled": boolean(entry.get("enabled", True))}
            if "hold" in entry:
                normalized["hold"] = boolean(entry["hold"])
            if "version" in entry:
                version = plain(entry["version"])
                pattern = NPM_VERSION if provider == "npm" else UV_VERSION
                if not re.fullmatch(pattern, version):
                    fail("Expected a quoted exact tool version, not a range, tag or alternate source.")
                if provider == "npm" and "-" in version.split("+", 1)[0]:
                    prerelease = version.split("+", 1)[0].split("-", 1)[1]
                    if any(part.isdigit() and len(part) > 1 and part.startswith("0") for part in prerelease.split(".")):
                        fail("Invalid numeric semantic-version prerelease identifier.")
                normalized["version"] = version
            result["packages"][provider][name] = normalized
    if count > 1000:
        fail("Too many package records.")
    runtimes = mapping(value.get("runtimes", {}), ("uv_python",))
    versions = runtimes.get("uv_python", [])
    if type(versions) is not list or len(versions) > 20:
        fail("uv_python must be a bounded list of quoted exact Python versions.")
    for version in versions:
        identifier("uv_python", version)
    if len(set(versions)) != len(versions):
        fail("Duplicate Python runtime request.")
    result["runtimes"] = {"uv_python": list(versions)}
    settings = mapping(value.get("settings", {}), ("remote_login", "dock"))
    result["settings"] = {key: boolean(settings.get(key, False)) for key in ("remote_login", "dock")}
    return result


def validate_state(state):
    """Validate synthetic observations; omitted facts remain unknown."""
    if state is None:
        return {}
    mapping(state, ("installed", "availability", "capabilities", "bindings", "platform"))
    if "platform" in state and state["platform"] not in ("arm64", "x86_64"):
        fail("Unsupported synthetic platform.")
    for section in ("installed", "availability"):
        for provider, entries in mapping(state.get(section, {}), PROVIDERS).items():
            for name, entry in mapping(entries).items():
                identifier(provider, name)
                allowed = ("present", "version", "held") if section == "installed" else ("available", "versions", "candidate")
                mapping(entry, allowed)
                required = "present" if section == "installed" else "available"
                if required not in entry:
                    fail("Synthetic observations require an explicit present/available Boolean.")
                boolean(entry[required])
                if "held" in entry:
                    boolean(entry["held"])
                    if entry["held"] and not entry["present"]:
                        fail("An absent package cannot already be held.")
                for key in ("version", "candidate"):
                    if key in entry:
                        plain(entry[key])
                if "versions" in entry:
                    if type(entry["versions"]) is not list or len(entry["versions"]) > 10000:
                        fail("Synthetic versions must be a bounded list.")
                    for version in entry["versions"]:
                        plain(version)
    for value in mapping(state.get("capabilities", {}), ("cask_hold",)).values():
        boolean(value)
    for provider, entry in mapping(state.get("bindings", {}), ("npm", "uv_tools")).items():
        mapping(entry, ("runtime", "verified"))
        if set(entry) != {"runtime", "verified"}:
            fail("Synthetic binding requires runtime and verified fields.")
        identifier("formulae" if provider == "npm" else "uv_python", entry["runtime"])
        boolean(entry["verified"])
    return deepcopy(state)


def observation(state, section, provider, name):
    return state.get(section, {}).get(provider, {}).get(name)


def runtime_issue(config, state, provider):
    formulae = config["packages"]["formulae"]
    selected = {name for name, entry in formulae.items() if entry["enabled"]}
    if provider == "npm":
        choices = sorted(name for name in selected if name == "node" or name.startswith("node@"))
        if len(choices) != 1:
            return "blocked", "select-exactly-one-node-formula-for-npm"
        expected = choices[0]
    else:
        uv = observation(state, "installed", "formulae", "uv")
        if "uv" not in selected and not (uv and uv["present"]):
            return "blocked", "select-uv-or-provide-existing-uv-observation"
        if provider == "uv_python":
            return None
        choices = config["runtimes"]["uv_python"]
        if len(choices) != 1:
            return "blocked", "select-exactly-one-managed-python-for-uv-tools"
        expected = choices[0]
    binding = state.get("bindings", {}).get(provider)
    if binding is None:
        return "unresolved", "runtime-executable-binding-not-observed"
    if binding["runtime"] != expected or not binding["verified"]:
        return "blocked", "runtime-binding-conflicts-or-is-unverified"
    return None


def decide(provider, name, entry, state, operation):
    installed = observation(state, "installed", provider, name)
    available = observation(state, "availability", provider, name)
    desired = name if provider == "uv_python" else entry.get("version")
    if installed is None:
        return "unresolved", "installed-state-not-observed"
    if entry.get("hold"):
        if not installed["present"]:
            return "blocked", "cannot-hold-an-absent-package"
        if provider == "casks":
            capability = state.get("capabilities", {}).get("cask_hold")
            if capability is None:
                return "unresolved", "cask-hold-capability-not-observed"
            if not capability:
                return "blocked", "cask-hold-not-supported"
        return ("noop", "existing-hold-preserved") if installed.get("held") else ("hold", "hold-current-installed-version-not-an-exact-pin")
    if not installed["present"] and operation == "update":
        return "preserve", "updates-do-not-install-missing-packages"
    if installed["present"]:
        if desired:
            if "version" not in installed:
                return "unresolved", "installed-version-not-observed"
            if installed["version"] == desired:
                return "noop", "exact-version-already-present"
            if installed.get("held"):
                return "blocked", "exact-version-conflicts-with-existing-hold"
        elif operation != "update" or installed.get("held"):
            return "noop", "installed-package-preserved"
        elif provider in ("formulae", "casks") and "held" not in installed:
            return "unresolved", "native-pin-state-not-observed"
    if available is None:
        return "unresolved", "package-availability-not-observed"
    if not available["available"]:
        return "blocked", "requested-package-or-platform-unavailable"
    if desired and provider != "uv_python":
        if "versions" not in available:
            return "unresolved", "exact-version-availability-not-observed"
        if desired not in available["versions"]:
            return "blocked", "requested-exact-version-unavailable"
    if desired:
        return ("change-version", "exact-version-change-requires-review") if installed["present"] else ("install", "install-requested-exact-version")
    if "candidate" not in available:
        return "unresolved", "current-candidate-version-not-observed"
    if installed["present"]:
        if installed.get("version") == available["candidate"]:
            return "noop", "current-candidate-already-present"
        return "change-version", "provider-candidate-change-direction-unverified-requires-review"
    return "install", "install-current-provider-candidate"


def build_plan(config, *, operation="setup", state=None):
    """Return deterministic proposed decisions from data, never actual readiness."""
    config = validate_config(config)
    synthetic = state is not None
    state = validate_state(state)
    if operation not in ("setup", "update", "finish"):
        fail("Unsupported planning operation.")
    actions = []
    selections = deepcopy(config["packages"])
    selections["uv_python"] = {version: {"enabled": True} for version in config["runtimes"]["uv_python"]}
    # Only known explicit ownership collisions; not a general alias/dependency solver.
    owners = {}
    for provider in ("formulae", "casks", "npm", "uv_tools"):
        for name, entry in selections[provider].items():
            if entry["enabled"]:
                owners.setdefault(name, []).append(provider)
    collisions = {name for name, providers in owners.items() if len(providers) > 1}
    for provider in PROVIDERS:
        for name, entry in sorted(selections[provider].items()):
            observed = observation(state, "installed", provider, name)
            if not entry["enabled"]:
                decision = ("preserve", "disabled-entry-is-not-uninstall")
            elif name in collisions and provider != "mas":
                decision = ("blocked", "same-application-selected-through-multiple-managers")
            elif operation == "finish" and provider != "mas":
                decision = ("deferred", "app-store-operation-only")
            elif provider == "mas" and operation != "finish":
                decision = ("deferred", "app-store-work-requires-separate-finish-operation")
            elif operation == "update" and observed is not None and not observed["present"] and not entry.get("hold"):
                decision = ("preserve", "updates-do-not-install-missing-packages")
            else:
                issue = runtime_issue(config, state, provider) if provider in ("npm", "uv_tools", "uv_python") else None
                decision = issue or decide(provider, name, entry, state, operation)
            action = {"provider": provider, "id": name, "action": decision[0], "reason": decision[1]}
            if observed and observed["present"] and "version" in observed:
                action["current_version"] = observed["version"]
            if provider == "uv_python" or "version" in entry:
                action["requested_version"] = name if provider == "uv_python" else entry["version"]
            elif decision[0] in ("install", "change-version"):
                candidate = observation(state, "availability", provider, name)
                if candidate and "candidate" in candidate:
                    action["requested_version"] = candidate["candidate"]
            actions.append(action)
    kinds = {action["action"] for action in actions}
    status = "blocked" if "blocked" in kinds else ("unresolved" if "unresolved" in kinds or not synthetic else "planned")
    return {"schema_version": 1, "profile": config["profile"], "operation": operation,
            "evidence": "synthetic" if synthetic else "configuration-only", "status": status,
            "executable": False, "platform": state.get("platform", "unobserved"),
            "settings": config["settings"], "actions": actions,
            "warnings": ["No host, provider, authentication or availability checks were performed.",
                         "These decisions are not approval, an apply manifest or installed readiness.",
                         "Homebrew dependencies/dependents may change; no automatic unpin or transaction rollback.",
                         "Offline settings selections are not target observations or approval."]}
