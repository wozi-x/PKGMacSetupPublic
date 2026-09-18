# Public Mac Setup maintenance

This repository owns the standalone general macOS setup engine. Keep every
tracked file public-safe. Use native Ansible tasks and the one strict public YAML
schema; do not add another planner, arbitrary shell hooks or a dependency solver.

## Boundaries

- No private repository discovery, authentication, checkout or continuation.
- No personal skills, agent config, plugins, identities, storage endpoints,
  signed private artifacts, credentials, private migrations or cleanup.
- Install apps without replacing global agent configurations. Omitted/disabled
  packages and false settings mean preserve, never uninstall or disable.
- No broad package upgrade, unpin, force overwrite, project traversal or venv
  replacement. Preserve native pins and existing application data.
- Public tools use only the public-owned destination subtree. Refuse unsafe
  symlink/ownership redirection instead of repairing or adopting it.
- `--plan` is offline and non-mutating. Apply requires target observation and
  actual scoped human review. Configuration and digests are not consent.
- Keep protected Bash startup, fixed argv and clean provider environments. Do
  not run real setup or package commands for tests; use synthetic fixtures.
- Only explicit Remote Login selection may request its privileged setting;
  never change sudoers, bypass SSH host keys or macOS permission prompts.

## Ownership and checks

`module_utils/mac_setup_planner.py` and `mac_setup_engine.py` are authoritative;
root import facades do not duplicate implementations. Ansible bundles the same
modules on local/SSH targets. `roles/mac_setup` is the one general executor.
The private project may explicitly compose this pinned role with private tasks;
the standalone public entrypoint never loads that private context.

Run the synthetic unittest suite, native fake-provider fixtures, Bash syntax,
Ansible syntax and whitespace checks. Inspect the whole export for privacy.
Never copy private Git history, config, assets, logs or fixtures into this repo.
Do not run live provisioning, publish, push or rewrite history without explicit
user authorization. Use conventional commits and preserve unrelated changes.
