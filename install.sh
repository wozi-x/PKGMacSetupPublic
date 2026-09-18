#!/bin/bash -p

set -Eeuo pipefail
unset BASH_ENV ENV OP_SERVICE_ACCOUNT_TOKEN OP_SERVICE_ACCOUNT_FILE OP_ENVIRONMENT_ID OP_CONNECT_HOST OP_CONNECT_TOKEN OP_SESSION
unset ANSIBLE_DIFF_ALWAYS ANSIBLE_DIFF_CONTEXT ANSIBLE_STDOUT_CALLBACK ANSIBLE_CALLBACKS_ENABLED ANSIBLE_CALLBACK_WHITELIST
unset ANSIBLE_CALLBACK_PLUGINS ANSIBLE_LOAD_CALLBACK_PLUGINS ANSIBLE_CALLBACK_RESULT_FORMAT ANSIBLE_CALLBACK_FORMAT_PRETTY
unset ANSIBLE_CONFIG ANSIBLE_DISPLAY_ARGS_TO_STDOUT ANSIBLE_DEBUG ANSIBLE_VERBOSITY ANSIBLE_LOG_PATH
for op_session_variable_name in "${!OP_SESSION_@}"; do
  unset "$op_session_variable_name"
done
unset op_session_variable_name

# Public first stage: install or update first-run essentials, authenticate
# GitHub locally, clone or update the private PKGMacSetup repository, and start
# its standard setup. Homebrew manages fresh installs so this script remains
# safe to run repeatedly. Standard setup never requests private SMB storage.
#
# Run with:
#   curl -fsSL https://raw.githubusercontent.com/wozi-x/PKGMacSetupPublic/main/install.sh | /bin/bash -p

readonly HOMEBREW_INSTALL_SCRIPT_URL="${HOMEBREW_INSTALL_SCRIPT_URL:-https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh}"
readonly HOMEBREW_INSTALL_SCRIPT_SHA256="${HOMEBREW_INSTALL_SCRIPT_SHA256:-}"
readonly PKGMACSETUP_REPOSITORY="${PKGMACSETUP_REPOSITORY:-wozi-x/PKGMacSetup}"
readonly PKGMACSETUP_CHECKOUT_DIR="${PKGMACSETUP_CHECKOUT_DIR:-$HOME/Developer/PKGMacSetup}"
readonly PKGMACSETUP_RUN_SETUP="${PKGMACSETUP_RUN_SETUP:-true}"
homebrew_installer_file=""

info() {
  printf '\n==> %s\n' "$1"
}

die() {
  printf 'Error: %s\n' "$1" >&2
  exit 1
}

cleanup_temp_files() {
  if [[ -n "$homebrew_installer_file" ]]; then
    rm -f -- "$homebrew_installer_file"
  fi
}

trap cleanup_temp_files EXIT
trap 'exit 1' HUP INT TERM

download_https() {
  local url="$1"
  local destination="$2"

  [[ "$url" == https://* ]] || die "Refusing non-HTTPS download URL: $url"
  curl --fail --silent --show-error --location \
    --proto '=https' \
    --proto-redir '=https' \
    --tlsv1.2 \
    --output "$destination" \
    "$url"
}

sha256_file() {
  local file="$1"

  if command -v shasum >/dev/null 2>&1; then
    shasum -a 256 "$file" | awk '{print $1}'
  elif command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$file" | awk '{print $1}'
  else
    die "Unable to verify SHA-256 checksum: shasum and sha256sum are unavailable."
  fi
}

install_homebrew() {
  local actual_sha
  local installer_rc=0

  homebrew_installer_file="$(mktemp "${TMPDIR:-/tmp}/homebrew-install.XXXXXX")"
  if ! download_https "$HOMEBREW_INSTALL_SCRIPT_URL" "$homebrew_installer_file"; then
    rm -f -- "$homebrew_installer_file"
    homebrew_installer_file=""
    die "Failed to download the Homebrew installer."
  fi

  [[ -s "$homebrew_installer_file" ]] || {
    rm -f -- "$homebrew_installer_file"
    homebrew_installer_file=""
    die "Downloaded Homebrew installer is empty."
  }

  if [[ -n "$HOMEBREW_INSTALL_SCRIPT_SHA256" ]]; then
    if ! actual_sha="$(sha256_file "$homebrew_installer_file")"; then
      rm -f -- "$homebrew_installer_file"
      homebrew_installer_file=""
      die "Unable to calculate the Homebrew installer SHA-256."
    fi
    if [[ "$actual_sha" != "$HOMEBREW_INSTALL_SCRIPT_SHA256" ]]; then
      rm -f -- "$homebrew_installer_file"
      homebrew_installer_file=""
      die "Homebrew installer SHA-256 mismatch."
    fi
  else
    info "Homebrew installer checksum is not pinned; relying on its HTTPS source"
  fi

  if ! /bin/bash -p -n "$homebrew_installer_file"; then
    rm -f -- "$homebrew_installer_file"
    homebrew_installer_file=""
    die "Downloaded Homebrew installer failed its shell syntax check."
  fi

  # The public curl pipeline leaves stdin as a pipe. Homebrew needs terminal
  # input to prompt for sudo instead of selecting non-interactive mode.
  if interactive_terminal_available; then
    /bin/bash -p "$homebrew_installer_file" </dev/tty || installer_rc=$?
  else
    /bin/bash -p "$homebrew_installer_file" || installer_rc=$?
  fi
  rm -f -- "$homebrew_installer_file"
  homebrew_installer_file=""
  [[ "$installer_rc" -eq 0 ]] || die "Homebrew installer failed with status $installer_rc."
}

install_or_update_cask() {
  local cask="$1"
  local label="$2"
  local app_name="$3"

  if brew list --cask "$cask" >/dev/null 2>&1; then
    if brew outdated --quiet --cask --greedy "$cask" | grep -Fxq "$cask"; then
      info "Updating $label"
      brew upgrade --cask --greedy "$cask"
    else
      info "$label is already up to date"
    fi
  elif [[ -d "/Applications/${app_name}.app" || -d "$HOME/Applications/${app_name}.app" ]]; then
    info "$label is already installed outside Homebrew; leaving it unchanged"
  else
    info "Installing $label"
    brew install --cask "$cask"
  fi
}

interactive_terminal_available() {
  # Device permissions alone do not prove this process has a controlling tty.
  ( : </dev/tty ) 2>/dev/null
}

authenticate_github() {
  if gh auth status --hostname github.com >/dev/null 2>&1; then
    info "GitHub CLI is already authenticated"
    return 0
  fi

  if ! interactive_terminal_available; then
    info "GitHub authentication needs an interactive terminal"
    return 1
  fi

  info "Authenticating GitHub in the browser"
  gh auth login --hostname github.com --git-protocol https --web </dev/tty
}

prepare_pkgmacsetup_checkout() {
  local origin_url=""

  if [[ -e "$PKGMACSETUP_CHECKOUT_DIR" ]]; then
    [[ -d "$PKGMACSETUP_CHECKOUT_DIR/.git" ]] \
      || die "Checkout path exists but is not a Git repository: $PKGMACSETUP_CHECKOUT_DIR"

    origin_url="$(git -C "$PKGMACSETUP_CHECKOUT_DIR" remote get-url origin 2>/dev/null || true)"
    case "$origin_url" in
      "https://github.com/${PKGMACSETUP_REPOSITORY}"|\
      "https://github.com/${PKGMACSETUP_REPOSITORY}.git"|\
      "git@github.com:${PKGMACSETUP_REPOSITORY}.git") ;;
      *) die "Refusing to update checkout with unexpected origin: ${origin_url:-missing}" ;;
    esac

    if [[ -n "$(git -C "$PKGMACSETUP_CHECKOUT_DIR" status --porcelain)" ]]; then
      die "PKGMacSetup checkout has local changes; review them before running setup."
    fi

    info "Updating PKGMacSetup"
    git -C "$PKGMACSETUP_CHECKOUT_DIR" pull --ff-only
    return 0
  fi

  mkdir -p "$(dirname "$PKGMACSETUP_CHECKOUT_DIR")"
  info "Cloning PKGMacSetup"
  gh repo clone "$PKGMACSETUP_REPOSITORY" "$PKGMACSETUP_CHECKOUT_DIR"
}

run_pkgmacsetup_interactive() {
  (
    cd "$PKGMACSETUP_CHECKOUT_DIR"
    ./setup.sh </dev/tty
  )
}

run_pkgmacsetup() {
  case "$PKGMACSETUP_RUN_SETUP" in
    true) ;;
    false)
      info "PKGMacSetup is ready at $PKGMACSETUP_CHECKOUT_DIR"
      return 0
      ;;
    *) die "PKGMACSETUP_RUN_SETUP must be true or false." ;;
  esac

  if ! interactive_terminal_available; then
    info "PKGMacSetup is ready; run ./setup.sh from $PKGMACSETUP_CHECKOUT_DIR"
    return 0
  fi

  info "Starting the standard PKGMacSetup run (private storage is excluded)"
  run_pkgmacsetup_interactive
}

[[ "$(uname -s)" == "Darwin" ]] || die "This installer supports macOS only."
[[ "${EUID:-$(id -u)}" -ne 0 ]] || die "Run this installer as your normal user, not with sudo."
command -v curl >/dev/null 2>&1 || die "curl is required."

brew_was_on_path=false
brew_bin=""
if command -v brew >/dev/null 2>&1; then
  brew_bin="$(command -v brew)"
  brew_was_on_path=true
  info "Homebrew is already installed"
else
  for candidate in /opt/homebrew/bin/brew /usr/local/bin/brew; do
    if [[ -x "$candidate" ]]; then
      brew_bin="$candidate"
      info "Homebrew is already installed at $brew_bin"
      break
    fi
  done

  if [[ -z "$brew_bin" ]]; then
    info "Installing Homebrew"
    install_homebrew

    for candidate in /opt/homebrew/bin/brew /usr/local/bin/brew; do
      if [[ -x "$candidate" ]]; then
        brew_bin="$candidate"
        break
      fi
    done
    [[ -n "$brew_bin" ]] || die "Homebrew was installed, but its brew command could not be found."
  fi
fi

eval "$("$brew_bin" shellenv)"

if [[ "$brew_was_on_path" == false ]]; then
  case "${SHELL:-}" in
    */zsh) brew_profile="$HOME/.zprofile" ;;
    */bash) brew_profile="$HOME/.bash_profile" ;;
    *) brew_profile="$HOME/.profile" ;;
  esac

  brew_shellenv_line='eval "$('"$brew_bin"' shellenv)"'
  if [[ ! -f "$brew_profile" ]] || ! grep -F "$brew_bin shellenv" "$brew_profile" >/dev/null 2>&1; then
    {
      printf '\n# Homebrew\n'
      printf '%s\n' "$brew_shellenv_line"
    } >>"$brew_profile"
    info "Added Homebrew to future shells via $brew_profile"
  else
    info "Homebrew is already configured in $brew_profile"
  fi
fi

info "Updating Homebrew package data"
brew update

install_or_update_cask google-chrome "Google Chrome" "Google Chrome"
install_or_update_cask 1password "1Password" "1Password"
install_or_update_cask chatgpt "ChatGPT desktop app with Codex" "ChatGPT"

if [[ -d /Applications/1Password.app || -d "$HOME/Applications/1Password.app" ]]; then
  info "Opening 1Password so sign-in can continue while setup runs"
  /usr/bin/open -a 1Password >/dev/null 2>&1 || true
fi

if brew list --formula gh >/dev/null 2>&1; then
  if brew outdated --quiet --formula gh | grep -Fxq gh; then
    info "Updating GitHub CLI"
    brew upgrade gh
  else
    info "GitHub CLI is already up to date"
  fi
else
  info "Installing GitHub CLI"
  brew install gh
fi

info "Installed versions"
gh --version | sed -n '1p'

if authenticate_github; then
  prepare_pkgmacsetup_checkout
  run_pkgmacsetup
else
  printf '\nEssentials are installed. Run `gh auth login --web`, then clone and run PKGMacSetup.\n'
fi
