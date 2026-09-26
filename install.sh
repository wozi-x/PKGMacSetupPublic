#!/bin/bash -p
# Standalone Base entrypoint. No authentication or private continuation.
set -eu
unset BASH_ENV ENV CDPATH PYTHONPATH PYTHONHOME ANSIBLE_CONFIG ANSIBLE_CALLBACKS_ENABLED ANSIBLE_STDOUT_CALLBACK
export PATH=/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin
script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
if [[ -f "$script_dir/setup.sh" ]]; then
  exec "$script_dir/setup.sh" "$@"
fi
printf '%s\n' \
  'Download or clone the public PKGMacSetupPublic repository, then run ./install.sh [--config-dir LOCAL_DIR] [--check].' \
  'Base needs no Ansible controller, private checkout or GitHub authentication.' >&2
exit 2
