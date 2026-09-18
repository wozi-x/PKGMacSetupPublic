#!/bin/bash -p
# No shell startup, credential-helper probe or private discovery.
set -eu
unset BASH_ENV ENV CDPATH PYTHONPATH PYTHONHOME ANSIBLE_CONFIG ANSIBLE_CALLBACKS_ENABLED ANSIBLE_STDOUT_CALLBACK
export PATH=/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin
script_dir="$(cd -- "$(dirname -- "$0")" && pwd -P)"
mode=''
noninteractive=false
inventory_selected=false
config_file=''
arguments=("$@")
while [[ $# -gt 0 ]]; do
  case "$1" in
    --plan|--apply|--prepare)
      [[ -z "$mode" ]] || { printf '%s\n' 'Choose one mode.' >&2; exit 2; }
      mode="$1"; shift ;;
    --config|--operation|-i|--inventory)
      [[ $# -gt 1 ]] || { printf '%s\n' 'Missing option value.' >&2; exit 2; }
      if [[ "$1" == --config ]]; then
        [[ -z "$config_file" && -f "$2" && ! -L "$2" ]] || { printf '%s\n' 'Select one regular YAML configuration.' >&2; exit 2; }
        config_file="$2"
      fi
      if [[ "$1" == --operation ]]; then
        case "$2" in setup|update|finish) ;; *) printf '%s\n' 'Unsupported operation.' >&2; exit 2 ;; esac
      fi
      if [[ "$1" == -i || "$1" == --inventory ]]; then
        [[ -f "$2" && ! -L "$2" && ! -x "$2" ]] || { printf '%s\n' 'Select one regular static inventory.' >&2; exit 2; }
      fi
      [[ "$1" != -i && "$1" != --inventory ]] || inventory_selected=true
      shift 2 ;;
    --non-interactive) noninteractive=true; shift ;;
    -h|--help) printf '%s\n' 'Use --config FILE --plan|--apply [--operation setup|update|finish] [-i INVENTORY], or --prepare.'; exit 0 ;;
    *) printf '%s\n' 'Unsupported option; use --help.' >&2; exit 2 ;;
  esac
done
[[ -n "$mode" ]] || { printf '%s\n' 'Choose --plan, --apply or --prepare.' >&2; exit 2; }
if [[ "$mode" != --plan && "$noninteractive" == true ]]; then
  printf '%s\n' 'Needs human: apply/prepare requires an interactive reviewed scope.' >&2
  exit 2
fi
if [[ "$mode" == --prepare ]]; then
  [[ ${#arguments[@]} == 1 ]] || { printf '%s\n' '--prepare is a separate prerequisite operation.' >&2; exit 2; }
  exec /bin/bash -p "$script_dir/bootstrap.sh"
fi
[[ -n "$config_file" ]] || { printf '%s\n' '--config is required before any package or prerequisite work.' >&2; exit 2; }
controller=''
for candidate in "$HOME/Library/Application Support/MacSetup/controller/bin/python" /opt/homebrew/opt/ansible/libexec/bin/python /usr/local/opt/ansible/libexec/bin/python; do
  if [[ -x "$candidate" ]]; then controller="$candidate"; break; fi
done
if [[ "$mode" == --apply && ( -z "$controller" || ( "$inventory_selected" == false && ! -x /opt/homebrew/bin/brew && ! -x /usr/local/bin/brew ) ) ]]; then
  /bin/bash -p "$script_dir/bootstrap.sh"
  controller="$HOME/Library/Application Support/MacSetup/controller/bin/python"
fi
if [[ -z "$controller" ]]; then
  printf '%s\n' 'Unresolved prerequisites: no existing Ansible/PyYAML controller. --plan made no changes; use --prepare for a separately reviewed prerequisite scope.' >&2
  exit 3
fi
exec /usr/bin/env -i PATH="$PATH" SSH_AUTH_SOCK="${SSH_AUTH_SOCK-}" "$controller" -I -B "$script_dir/setup.py" "${arguments[@]}"
