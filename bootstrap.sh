#!/bin/bash -p
# Reviewed public controller prerequisites only. Never a private handoff.
set -Eeuo pipefail
unset BASH_ENV ENV CDPATH PYTHONPATH PYTHONHOME
export PATH=/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin
bootstrap_dir="$(cd -- "$(dirname -- "$0")" && pwd -P)"
bootstrap_user="$(/usr/bin/id -un)"
bootstrap_uid="$(/usr/bin/id -u)"
bootstrap_root="$HOME/Library/Application Support/MacSetup"
controller="$bootstrap_root/controller"
runtime_root="$bootstrap_root/bootstrap-python"
bootstrap_temp=''
controller_stage=''
die() { printf '%s\n' "$1" >&2; exit 2; }
clean_run() {
  /usr/bin/env -i HOME="$HOME" USER="$bootstrap_user" LOGNAME="$bootstrap_user" PATH="$PATH" \
    SHELL=/bin/zsh TERM="${TERM-dumb}" \
    HOMEBREW_NO_AUTO_UPDATE=1 HOMEBREW_NO_ANALYTICS=1 HOMEBREW_NO_INSTALL_CLEANUP=1 \
    HOMEBREW_NO_INSTALL_UPGRADE=1 HOMEBREW_NO_INSTALLED_DEPENDENTS_CHECK=1 \
    UV_NO_CONFIG=1 UV_KEYRING_PROVIDER=disabled UV_CREDENTIALS_DIR=/var/empty NETRC=/dev/null UV_DEFAULT_INDEX=https://pypi.org/simple \
    UV_PYTHON_INSTALL_DIR="$runtime_root" "$@"
}
cleanup() {
  # Only mktemp-owned scratch is removed; no inherited path is accepted.
  if [[ -n "$bootstrap_temp" && "$bootstrap_temp" == /private/tmp/macsetup-prerequisites.* ]]; then
    /bin/rm -f -- "$bootstrap_temp/homebrew.sh"
    /bin/rmdir -- "$bootstrap_temp" 2>/dev/null || true
  fi
  if [[ -n "$controller_stage" ]]; then
    printf '%s\n' "Incomplete controller staging was preserved for inspection: $controller_stage" >&2
  fi
}
trap cleanup EXIT
trap 'exit 2' INT TERM HUP

[[ "$(/usr/bin/uname -s)" == Darwin && "$bootstrap_uid" != 0 ]] || die 'Prepare must run as the intended non-root macOS user.'
[[ -t 0 && -t 1 ]] || die 'Needs human: prerequisite preparation requires an interactive terminal.'
printf '%s\n' \
  'Public prerequisite scope (configuration/package availability is not resolved yet):' \
  '- Request Apple Command Line Tools only if absent; its OS dialog must finish before rerun.' \
  '- Install Homebrew only if absent, using the official HTTPS installer (not checksum-pinned).' \
  '- Install uv only if absent; Homebrew dependencies may be installed.' \
  '- Install isolated CPython 3.14.7 and a new controller with tested requirements.txt.' \
  '- Existing controllers/runtimes, accounts, dotfiles and private repositories are not upgraded or managed.'
read -r -p 'Prepare exactly these prerequisites? Type prepare: ' bootstrap_answer
[[ "$bootstrap_answer" == prepare ]] || die 'Canceled before prerequisite changes.'

if ! /usr/bin/xcode-select -p >/dev/null 2>&1; then
  /usr/bin/xcode-select --install
  die 'Needs human: finish the Apple developer-tools dialog, then rerun preparation.'
fi
brew_bin=/opt/homebrew/bin/brew
[[ "$(/usr/bin/uname -m)" == arm64 ]] || brew_bin=/usr/local/bin/brew
if [[ ! -x "$brew_bin" ]]; then
  bootstrap_temp="$(/usr/bin/mktemp -d /private/tmp/macsetup-prerequisites.XXXXXX)"
  clean_run /usr/bin/curl -q --fail --silent --show-error --location --proto '=https' --proto-redir '=https' --tlsv1.2 \
    --output "$bootstrap_temp/homebrew.sh" https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh
  [[ -s "$bootstrap_temp/homebrew.sh" ]] || die 'Homebrew installer download was empty.'
  /bin/bash -p -n "$bootstrap_temp/homebrew.sh" || die 'Homebrew installer failed its syntax check.'
  clean_run /bin/bash -p "$bootstrap_temp/homebrew.sh"
  [[ -x "$brew_bin" ]] || die 'Homebrew is still unavailable; resolve its prerequisite error before rerunning.'
fi

# Refuse adoption/redirection of any existing controller storage ancestor.
current="$HOME"
for part in Library 'Application Support' MacSetup; do
  current="$current/$part"
  [[ ! -L "$current" ]] || die 'Controller storage contains a symlink; existing state was preserved.'
  if [[ -e "$current" ]]; then
    [[ -d "$current" && "$(/usr/bin/stat -f %u "$current")" == "$bootstrap_uid" ]] || die 'Controller storage ownership is unsafe.'
    permissions="$(/usr/bin/stat -f %Lp "$current")"
    (( (8#$permissions & 022) == 0 )) || die 'Controller storage is writable by another account.'
  fi
done
[[ ! -L "$controller" && ! -L "$runtime_root" ]] || die 'Controller/runtime redirects are unsupported.'
for current in "$controller" "$runtime_root"; do
  if [[ -e "$current" ]]; then
    [[ -d "$current" && "$(/usr/bin/stat -f %u "$current")" == "$bootstrap_uid" ]] || die 'Controller/runtime ownership is unsafe.'
    permissions="$(/usr/bin/stat -f %Lp "$current")"
    (( (8#$permissions & 022) == 0 )) || die 'Controller/runtime directory is writable by another account.'
  fi
done
if [[ -e "$controller" ]]; then
  [[ -x "$controller/bin/python" ]] || die 'Existing incomplete controller was preserved; inspect it before manual repair.'
  printf '%s\n' 'Existing controller preserved without package or runtime upgrades.'
  exit 0
fi
uv_bin="$(dirname "$brew_bin")/uv"
if [[ ! -x "$uv_bin" ]]; then
  clean_run "$brew_bin" install --formula uv
fi
[[ -x "$uv_bin" ]] || die 'uv prerequisite is unavailable.'
/bin/mkdir -p -- "$bootstrap_root"
clean_run "$uv_bin" --no-config python install 3.14.7
runtime="$(clean_run "$uv_bin" --no-config python find --managed-python --no-python-downloads 3.14.7)"
[[ "$runtime" == "$runtime_root/"* && -x "$runtime" ]] || die 'Managed controller interpreter binding could not be verified.'
controller_stage="$(/usr/bin/mktemp -d "$bootstrap_root/controller-stage.XXXXXX")"
clean_run "$uv_bin" --no-config venv --relocatable --python "$runtime" "$controller_stage"
clean_run "$uv_bin" --no-config pip install --python "$controller_stage/bin/python" --index-url https://pypi.org/simple \
  --requirement "$bootstrap_dir/requirements.txt"
clean_run "$controller_stage/bin/python" -I -B -c 'import ansible, yaml'
[[ ! -e "$controller" && ! -L "$controller" ]] || die 'Controller destination appeared during preparation; staging preserved.'
# uv's relocatable mode keeps console entrypoints valid after the atomic rename.
/bin/mv -- "$controller_stage" "$controller"
controller_stage=''
clean_run "$controller/bin/ansible-playbook" --version >/dev/null
printf '%s\n' 'Controller prepared. Re-run with your chosen YAML for a newly observed package scope.'
