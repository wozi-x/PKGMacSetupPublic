# AGENTS.md

Guidance for agents working on the public PKGMacSetup bootstrap.

## Purpose

This repository is the public, reviewable first stage of the Wozi macOS setup.
It installs the minimal public prerequisites needed to authenticate GitHub and
continue into the private `wozi-x/PKGMacSetup` repository.

## Public safety boundary

- Keep every tracked file safe for unrestricted public disclosure.
- Never add credentials, tokens, account identifiers, private inventories,
  storage addresses, host-specific configuration, signed private artifacts, or
  content copied from the private repository.
- Public GitHub repository names and the private repository slug
  `wozi-x/PKGMacSetup` may be referenced, but do not expose its contents or
  history.
- Never accept, read, print, persist, or transport a 1Password service-account
  token or other automation credential.
- Keep private SMB, DEVONthink restore, Mac App Store, signing, release, and
  Wozi service operations outside this repository.

## Installer contract

- `install.sh` must remain safe to rerun as a normal macOS administrator user.
- Do not require the installer to run as root or tell users to pipe it through
  `sudo`.
- Preserve protected Bash startup (`#!/bin/bash -p`) and the early scrubbing of
  inherited credential and Ansible display variables.
- Require HTTPS for every download. Download scripts completely, verify they
  are non-empty, run a syntax check, and only then execute them.
- Preserve existing applications installed outside Homebrew.
- Authenticate GitHub locally with GitHub CLI before accessing the private
  continuation repository.
- Update an existing private checkout only when its working tree is clean and
  its `origin` exactly matches `wozi-x/PKGMacSetup`.
- The automatic continuation may run only the standard private setup. It must
  not request private storage or trigger account-dependent follow-up stages.
- Keep `PKGMACSETUP_RUN_SETUP=false` working so users can stop after preparing
  the private checkout.

## Scope

Keep this repository intentionally small. The expected tracked files are:

- `install.sh` — public first-stage installer and private handoff
- `README.md` — public usage and safety documentation
- `LICENSE` — repository license
- `AGENTS.md` — maintenance rules

Do not turn this repository into a copy of the private Ansible project. New
machine configuration belongs in private `PKGMacSetup` unless it is both
essential to the first-stage handoff and safe for public disclosure.

## Editing and verification

- Make focused changes and preserve rerun safety.
- Review the complete diff for public-disclosure risk before committing.
- Run:

  ```sh
  /bin/bash -p -n install.sh
  git diff --check
  ```

- When installer behavior changes, also run the bootstrap tests maintained in
  the private `PKGMacSetup` repository and update its tracked public mirror.
- Use conventional commit messages. Do not force-push or rewrite published
  history.
