#!/bin/bash -p
# Standalone public entrypoint. No authentication or private continuation.
set -eu
unset BASH_ENV ENV CDPATH PYTHONPATH PYTHONHOME ANSIBLE_CONFIG ANSIBLE_CALLBACKS_ENABLED ANSIBLE_STDOUT_CALLBACK
export PATH=/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin
if [[ "${PKGMACSETUP_RUN_SETUP-true}" == false ]]; then
  printf '%s\n' 'Preparation only: no packages changed and no private repository accessed.'
  exit 0
fi
script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
if [[ -f "$script_dir/setup.sh" ]]; then
  exec /bin/bash -p "$script_dir/setup.sh" "$@"
fi
printf '%s\n' \
  'Download or clone the public PKGMacSetupPublic repository, then run its install.sh with --config and --plan or --apply.' \
  'An existing Ansible controller with PyYAML and Homebrew are prerequisites. No private checkout or GitHub authentication is needed.' >&2
exit 2
