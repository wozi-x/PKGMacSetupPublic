# Mac Setup Base

A small, reusable macOS workstation setup: Homebrew packages, familiar Finder,
keyboard, trackpad and Dock preferences, and conservative shell defaults.
It runs independently of any private setup repository and needs no Ansible.

From this checkout, run:

~~~sh
./install.sh
./install.sh --check
~~~

Run as your ordinary user, without an outer sudo. A fresh Mac may need Apple's
Command Line Tools dialog and Homebrew's administrator prompt. Complete a pending
Apple installation and rerun. Existing Homebrew is reused. Application login and
Raycast permissions remain interactive.

The default packages are git, gh, jq, mas, tmux, zsh-autosuggestions,
zsh-syntax-highlighting, Raycast, Zed and Amphetamine. Amphetamine is installed
from the Mac App Store after common packages, preferences and shell setup finish.
Use the same existing App Store account on Base, Development and Administration
Macs. If sign-in or installation is unavailable, common setup finishes and Base
reports the App Store work as pending; sign in and rerun. No language runtimes, cloud tooling,
iOS tooling, credentials, personal agent configuration or private services are
installed. Projects and the device owner choose their additional requirements.

## Keep local configuration local

Copy examples/local to a folder on the Mac or in its owner's repository, edit it,
and select that folder explicitly:

~~~sh
./install.sh --config-dir "$HOME/mac-setup"
./install.sh --config-dir "$HOME/mac-setup" --check
~~~

The runner never searches for, downloads or publishes your local configuration.

| File | Meaning |
| --- | --- |
| Brewfile | Complete replacement for the default package list; missing file uses the default. |
| preferences.conf | Only finder, keyboard, trackpad and dock, each set to true or false. Omitted groups preserve existing settings. Missing file uses public preferences. |
| dotfiles/.zprofile, .zshrc, .tmux.conf, .gitconfig | Optional explicitly selected fragments. Only these filenames are accepted. |

Brewfiles accept only blank lines, comments, and these plain declarations:

~~~ruby
brew "git"
brew "awscli"
cask "zed"
brew "mas"
mas "Numbers", id: 361304891
~~~

Only core formula/cask names, including version suffixes, are supported.
Arbitrary Ruby, taps, hooks, package options, includes and interpolation are
rejected before changes. A selected local list can be empty. App Store entries
require mas and an existing App Store sign-in; setup does not purchase apps,
sign in, import accounts or transfer licenses.

Explicit local dotfiles are stored under ~/.config/macsetup/base. Setup adds
one include to the corresponding home file while retaining its existing bytes.
Rerunning updates the managed fragments from the selected local source.
Missing fragments preserve earlier installed fragments and includes. Shell
fragments execute in future shells, never during setup. Only use reviewed
configuration you trust. Symlink destinations or unsafe destination ancestors
are rejected without repair.

Without local dotfiles, the small public defaults create only missing
.zprofile, .zshrc and .tmux.conf files. Existing files and symlinks are skipped;
there is no Git identity default. Homebrew path, shell plugins and tmux aliases
are available in newly created shell files. Existing shell customization stays
with its current owner.

Existing files that differ from these defaults are reported as unmanaged and
pending, in both setup and `--check`; they are preserved and do not count as a
verified common experience. To deliberately adopt the public defaults on an
existing Mac, review `dotfiles/` and select this checkout as the configuration:

~~~sh
./install.sh --config-dir "$PWD"
./install.sh --config-dir "$PWD" --check
~~~

This uses the same managed-fragment mechanism, preserving existing home-file
bytes and appending includes. Keep using that configuration selection on reruns.
Check verifies managed fragment contents and includes; later user settings can
still override them. It does not execute shell configuration to certify runtime
behavior. Common tmux settings use Ctrl-A, 50,000 lines of history, mouse support,
vi selection keys, h/j/k/l pane navigation, and current-directory splits.

## Preferences and repeat behavior

The enabled default groups set:

- Finder: extensions, path/status bars, column view and current-folder search.
- Keyboard: repeat rate 2, initial delay 15, ordinary key repeat, and disabled
  smart quotes, dashes and auto-correction. Command-Space selects the next input
  source; the conflicting Spotlight shortcut is disabled. Other shortcuts are
  preserved. Log out and back in if macOS has not yet picked up the shortcut.
- Trackpad: tap to click, two-finger right-click, three-finger drag and light click.
- Dock: autohide, size 48 and no recent-app section.

False groups preserve existing values. Preferences are compared before writing.
No Dock icons are cleared, applications killed, Remote Login enabled, Mac name
changed or security permissions overridden. A logout or later app restart may
be needed for macOS to display some changes.

A failed preference write or read-back verification is reported with its domain
and key. Base continues with the remaining preferences and shell configuration,
then exits 3 with an incomplete-setup summary instead of reporting success.
Review the original `defaults` error in the client Mac terminal, resolve the
reported failure, and rerun as the same ordinary user. Base does not change
preference-file ownership or override macOS permissions or management policies.
Use `--check` to see what remains pending. Homebrew package failures still stop
setup before preferences and shell configuration. App Store failures happen
after those stages and return 3 with a pending summary. Reruns check installed
App Store apps before requesting installation and do not change accounts.

Package installs use Homebrew Bundle with --no-upgrade and no cleanup.
Unselected packages remain installed. Homebrew may still change dependencies
to install missing packages; this is not a version lock or rollback system.
Ordinary package maintenance remains separate.

Existing Raycast/Zed bundles are recognized locally. Other missing casks can
use the public Homebrew JSON API to recognize simple external .app bundles in
/Applications or ~/Applications. Those copies are skipped without adoption.
Complex installer conflicts stop with guidance; setup never forces replacement.
App presence does not certify its version, login or optional command-line tools.

Setup returns 0 when complete, 3 when completed with items needing attention,
and 2 for invalid configuration or a prerequisite/package failure (unexpected
provider failures may retain their original nonzero status). `--check` returns
0 when the selected package/preference/dotfile state is satisfied and 1 for
pending changes; invalid configuration and prerequisite errors still return 2.
It does not install, write preferences/dotfiles or authenticate. It may read
public cask metadata to identify an external app. It does not prove application
login, license activation or a particular project build.

## Development and legacy compatibility

Tests use a disposable HOME and fake package/preferences providers:

~~~sh
python3 -m unittest discover -s tests
for script in setup.sh install.sh legacy-setup.sh bootstrap.sh; do
  /bin/bash -n "$script" || exit
done
git diff --check
~~~

The previous Python/Ansible engine is available only through
[legacy-setup.sh](legacy-setup.sh); its advanced schema and limitations are in
[LEGACY.md](LEGACY.md). Neither install.sh nor setup.sh falls back to it.
Legacy controller and native Ansible test requirements remain in requirements.txt.
