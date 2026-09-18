# Public Mac Setup

One editable YAML selection, two preference profiles (`admin` and `dev`), and one
native Ansible engine for local or single-target SSH setup. Public setup never
authenticates GitHub, discovers/downloads a private repository, installs personal
skills, edits global agent configuration, or cleans up private installed assets.

Source and synthetic test acceptance are separate from installed-Mac readiness.
Publication and live provisioning require their own explicit authorization.

## Everyday use

From the public checkout, with prerequisites already installed:

```sh
./install.sh --config examples/admin.yml --plan
./install.sh --config examples/web-dev.yml --apply
./install.sh --config examples/web-dev.yml --apply --operation update
./install.sh --config examples/ios-dev.yml --apply --operation finish
./install.sh --config my-mac.yml --apply -i examples/inventory.remote
# Fresh Mac: separately review the prerequisite scope first.
./install.sh --prepare
```

Copy one example and edit its complete package list. Purpose filenames do not
create extra profiles. No package is silently inherited from a hidden catalog.
Profiles are preferences, not credential or automation permissions.

`--plan` validates configuration **offline**: no host inspection, provider/network
requests, authentication, prompts, installation or persistence. JSON says
`configuration-only`, `executable: false`, and `unresolved`; exit 3 is expected.
It cannot certify target prerequisites or availability. Invalid input exits 2.

`--apply` observes the actual target and public package metadata, shows the
concrete scope, and requires `apply` at a real interactive terminal. Changes to
configuration, engine, target, versions or bindings invalidate the reviewed
scope. Node/uv/Python prerequisites are explicit selected-only stages; a later
scope is reviewed after the real executables exist. No per-package approval
database, saved approval, automatic force/unpin or fake installed-state file.
`--non-interactive --apply` returns needs-human; unattended plan remains usable.

## Prerequisites and SSH

`--prepare` offers a separate explicit prerequisite review. It requests missing
CLT (then pauses for Apple's dialog), installs missing Homebrew using its complete
HTTPS official installer after a nonempty/syntax check, installs missing uv, and
creates an isolated CPython 3.14.7 controller with tested `requirements.txt` pins.
The Homebrew installer is not checksum-pinned; that upstream source and possible
dependency effects are disclosed before confirmation. Existing controller
environments are preserved, not upgraded. An unavailable exact runtime stops
without fallback. The controller is staged in relocatable form then renamed only
after successful dependency installation; failed staging is preserved for review.

The shell launcher also recognizes an existing Homebrew Ansible environment, or
you may invoke `python -I -B setup.py ...` from another known isolated controller.
It never probes Apple's `/usr/bin/python3` shim. Offline `--plan` never prepares
prerequisites. A missing runtime reports unresolved instead. Piped installer use
without a complete public checkout gives public download/checkout guidance; it
does not authenticate or clone any private continuation.

An isolated interpreter avoids replacing the active controller during package
changes. The engine blocks selected/dependent changes that can affect the active
Ansible/Python runtime; resolve such a conflict in a separately reviewed
prerequisite maintenance step, never by disabling the guard.

SSH is advanced and explicit: `-i` must be a regular non-executable static native
Ansible inventory with exactly one non-root target, explicit host/user, and an
existing safe Python path. Arbitrary inventory scripts are unsupported. Native
Ansible transports the same public observer to the target; observations are
never fabricated from the controller. Missing remote Python/CLT/Homebrew is an
actionable prerequisite boundary, not a silent passwordless-sudo bootstrap.
SSH uses existing key/agent authentication with BatchMode and strict known-host
checking; first contact or missing authentication returns needs-human for a
separate ordinary SSH trust/authentication step, never bypassing that check.
No inventory is rewritten, selected automatically or used as approval.

## Small configuration schema

```yaml
schema_version: 1
profile: dev
packages:
  formulae:
    git: {}
    node@22: {}       # Maintained series, not an exact patch pin.
    uv: {}
  casks:
    visual-studio-code: {}
  npm:
    prettier: {version: "3.6.2"}
  uv_tools:
    ruff: {version: "0.6.0"}
runtimes:
  uv_python: ["3.12.3"]
settings:
  remote_login: false
  dock: false
```

Versions above illustrate syntax, not current security recommendations.

- Required `schema_version: 1` and `profile: admin|dev`. One selected file owns
  the complete software selection. No includes, templates, aliases, duplicate
  keys, arbitrary hooks or private extension fields.
- Package maps: `formulae`, `casks`, `mas`, `npm`, `uv_tools`. `{}` selects an
  entry. `enabled: false`, removing an entry or changing purpose preserves its
  installed state; none is an uninstall request.
- Formula/cask `hold: true` holds an **already installed** version. An absent
  package cannot be held. Existing native pins never silently release. A cask
  hold cannot control an app's own updater. Homebrew versioned formula names
  select a maintained series, not an exact patch.
- npm/uv tools accept quoted exact `version`. Unsupported ranges, alternate
  sources, URL/Git/path IDs and unsupported pin types fail instead of using latest.
  Exact changes, including downgrades, are displayed for review. npm lifecycle
  installation scripts are disabled by default. The only reviewed exception is
  `"@posthog/cli": {version: "0.18.3", allow_lifecycle_scripts: true}`.
  Its reviewed install may run package **and dependency** lifecycle scripts as
  your user, with access to your files; the isolated prefix is not a security
  sandbox. Other packages/versions cannot enable scripts. Metadata version,
  script definitions and distribution integrity metadata are bound to review;
  transitive dependencies and vendor downloads are not a whole-machine lockfile.
  Default-off dependency hooks remain suppressed; installation does not certify
  CLI health. Additional installer exceptions require source review.
- `runtimes.uv_python` accepts stable exact CPython `3.minor.patch` requests.
  npm binds the selected Node executable and its npm CLI directly. uv tools bind
  one actual selected managed Python. No incidental PATH runtime or force-link.
  Homebrew npm discovery accepts exactly one regular CLI file in the selected
  formula's `lib/node_modules` or `libexec/lib/node_modules` layout; missing,
  ambiguous or redirected-outside-formula layouts stop for review.
  npm reads `/dev/null` as user config and a distinct reserved, verified-absent
  public-owned global-config path. Existing files/links there are preserved and
  rejected without reading them; no ambient npmrc or authentication is loaded.
  A genuinely absent verified public npm prefix is observed as empty without
  invoking npm or creating directories. Reviewed installation creates only that
  compiled prefix; an existing-prefix inspection failure is never treated as empty.
- Supported IDs are core formula tokens (numeric `@series` allowed), canonical
  `owner/repo/formula` names from already prepared public taps, plain cask tokens
  or named channels such as `@beta`, quoted MAS IDs, canonical npm names and
  normalized uv names. A qualified formula requires a clean committed tap with
  its default GitHub HTTPS remote and existing item/tap trust before formula
  metadata is evaluated. `trust: true` enables a separately reviewed prerequisite
  scope only for `getsentry/tools/sentry-cli`, `resend/cli/resend`, and
  `mobile-dev-inc/tap/maestro`. It downloads a missing canonical public GitHub
  tap and grants only the selected formula's trust, never whole-tap trust.
  Other missing/untrusted items stop for separately reviewed support. No formula
  Ruby is loaded before this stage; after preparation, packages are reobserved
  and reviewed separately. The scope binds tap revision and formula checksum.
  Selection flags and a previously completed run are not human consent.
  A core formula's confirmed alias/old name may resolve to its validated current
  core name for public metadata; this does not authorize alternate sources.
  Arbitrary sources and alternate Python implementations remain unsupported.
- Cask `accept_external: true` preserves an existing safe, simple application
  artifact under `/Applications` without adopting, quitting, replacing, updating
  or claiming its version. Its metadata is bound to review. This cannot combine
  with a Homebrew hold. When the artifact is absent, ordinary installation applies.
- `dock: true` manages only Dock autohide=true and show-recents=false; it never
  clears icons or restarts applications. `remote_login: true` explicitly reads
  then enables SSH with normal sudo/OS approval. False touches neither setting.
  Hostnames, accounts, passwords, shell/git identity and global agent files are
  preserved; there are no hardware/profile-derived hostname defaults.

Setup does not request updates of satisfied unversioned direct entries. Update
requests only selected installed eligible IDs, never bare upgrade-all commands.
For already installed Homebrew entries, setup retains installed-version, pin,
canonical identity and selected tap/source checks, but does not fetch or compare
an unused latest package candidate. Missing installs and update observations
retain strict availability/candidate checks. Runtime executable bindings and
protected dependency checks for other selected changes still apply.
`finish` is the separate App Store scope; it may need the owner's existing
App Store session. Missing Store prerequisites stop, never trigger account setup.

Homebrew can change dependencies; this is not a whole-machine lockfile. Known
controller conflicts/native holds stop the run. Protection follows the actual
raw/resolved interpreter and environment-prefix formula roots, including a
Homebrew-hosted Ansible environment; an unrelated installed Homebrew Ansible
does not become the active isolated controller. This evidence is rechecked with
the package scope, without a general safety opt-out. There is no guarantee about
unseen private constraints, transitive reproducibility, or transactional rollback.
Failures stop subsequent operations; already completed changes remain, and a
rerun observes them before deciding what is still needed.

## Ownership and repeat safety

Public npm/uv tools and managed Python live under
`~/Library/Application Support/MacSetup/public`, separate from existing global
tool/project environments. The preview names this scope. Selected public tools
also propose one `MACSETUP PUBLIC TOOLS` block in `.zprofile`, exposing their bins
and the explicitly selected Node runtime in a **new login shell**. Its deliberate
PATH precedence is part of the reviewed scope; project version managers remain
separately owned. Unrelated shell bytes are preserved, and a symlinked/unsafe
profile stops for review. Existing unsafe storage redirects are likewise refused
without repair. No project repositories, venvs, secrets, enrollment,
credential helpers, private plugins or prior-copy/backup directories are touched.

App installation is not application login/readiness verification. The engine
does not use `--force` to adopt existing external app bundles or CLI collisions;
provider conflicts stop for review. Exact availability known at preflight is
checked; late download/build/platform failures stop without a fallback version.

The private repository may explicitly compose its own guards and private tasks
around this pinned public role in one native play. Only that private caller loads
private settings; standalone public setup has no private loading mechanism.

## Implementation and tests

`module_utils/mac_setup_planner.py` is the pure schema/decision module.
`mac_setup_engine.py` adds target observation and fixed argv compilation only.
`library/mac_setup_scope.py` runs those same modules on native Ansible targets;
`roles/mac_setup` is the sole executor. Root `planner.py`/`engine.py` are import
facades, not copies. No synthetic observation file can be passed to apply.

The role accepts public `mac_setup_config`, `mac_setup_operation`, mode
`observe|apply` and an ephemeral review digest; an observation is not consent.
Direct-role callers must perform the same human scope review. Reserved internal
result variables are rejected before execution. Public-role configuration is
closed even when invoked directly from another playbook.

```sh
"$PYTHON" -I -B -m unittest discover -s tests
```

Tests use synthetic metadata and temporary fake executable providers, including
the actual native module zip and Ansible command-dispatch path. They never
provision a Mac, authenticate, invoke a real manager, or prove installed-host
readiness. A separate authorized real-host trial is required before publication.
