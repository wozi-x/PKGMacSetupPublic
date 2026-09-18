# PKGMacSetup Public Bootstrap

Public, reviewable first-stage bootstrap for a new Wozi macOS development
machine. It installs or updates the small set of tools needed before the
private machine configuration can continue:

- Homebrew
- Google Chrome
- 1Password
- ChatGPT for macOS
- GitHub CLI

After the public stage succeeds, the installer authenticates GitHub, clones or
updates the private `wozi-x/PKGMacSetup` checkout, and starts its standard setup.
The standard continuation deliberately excludes Mac App Store work, private SMB
storage, and DEVONthink database restore.

## Run on a new Mac

Review [`install.sh`](install.sh), then run:

```sh
curl -fsSL https://raw.githubusercontent.com/wozi-x/PKGMacSetupPublic/main/install.sh | /bin/bash -p
```

Run this in Terminal as your normal administrator user. Do not add `sudo` to
the command. The script requests administrator access only when Homebrew needs
it.

To install the public prerequisites and prepare the private checkout without
starting Ansible:

```sh
curl -fsSL https://raw.githubusercontent.com/wozi-x/PKGMacSetupPublic/main/install.sh |
  PKGMACSETUP_RUN_SETUP=false /bin/bash -p
```

## Safety boundary

This repository contains no private Ansible configuration, inventory, storage
details, credentials, signed helper artifacts, or private repository history.
GitHub authentication happens locally through GitHub CLI. The installer never
accepts or reads a 1Password service-account token.

The public stage is safe to rerun. Existing applications installed outside
Homebrew are left unchanged, and an existing private checkout is updated only
when it is clean and its `origin` matches `wozi-x/PKGMacSetup`.

## Validation

```sh
/bin/bash -p -n install.sh
```

## License

MIT. See [`LICENSE`](LICENSE).
