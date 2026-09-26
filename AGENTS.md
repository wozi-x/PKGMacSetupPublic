# Public Base maintenance

This repository owns the standalone Base shell + Brewfile setup. Keep the
default path small: install.sh -> setup.sh. Do not add a profile engine,
approval database, arbitrary hooks or dependency solver. Public source never
discovers, authenticates to, downloads or continues into private repositories.

- Keep tracked files public-safe; no private identity, infrastructure, credentials,
  certificate material, personal AI configuration or private fixtures.
- Accept only the documented data-only Brewfile and preference syntax.
  Validate selected configuration and managed destination ancestors before writes.
- Preserve unrelated packages and existing apps. Never broad-upgrade, uninstall,
  force-adopt or cleanup. Disclose Homebrew dependency effects.
- Default dotfiles are create-only. Explicit local fragments update only the
  managed files, retaining existing home-file bytes around their stable includes.
- Keep protected Bash startup and fixed provider argv. Never source local
  configuration during setup. Check mode must not install, persist preferences,
  create dotfiles or authenticate.
- Live provisioning, publication and pushing require explicit user authorization.
  Synthetic tests must use temporary HOME and fake providers.
- Work on the current branch, preserve unrelated changes, and use conventional
  commits. Run Base tests, the retained legacy synthetic suite, shell syntax,
  legacy Ansible syntax and whitespace checks before committing.

The old Python/Ansible modules and role remain an explicit legacy implementation.
Their authoritative code stays in module_utils with root import facades; do not
duplicate or silently route Base into that engine. Keep its existing tests and
boundaries intact while maintaining legacy-setup.sh separately.
