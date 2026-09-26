#!/bin/bash -p
# Fixed Base migration; never evaluates configuration in this Bash process.
set -Eeuo pipefail
export PATH=/usr/bin:/bin:/usr/sbin:/sbin
unset BASH_ENV ENV CDPATH ZDOTDIR
check="${1:-false}"
[[ "$check" == true || "$check" == false ]] || exit 2
target="$HOME/.zshrc"
begin='# BEGIN BASE MANAGED OH MY ZSH'
end='# END BASE MANAGED OH MY ZSH'
scratch='' candidate='' child='' backup=''
installed_candidate=false
valid=false
cleanup() {
  if [[ -n "$child" ]]; then
    kill -TERM "$child" 2>/dev/null || true
    kill -KILL "$child" 2>/dev/null || true
    wait "$child" 2>/dev/null || true
  fi
  if [[ "$installed_candidate" == true && "$valid" != true ]] && /usr/bin/cmp -s "$target" "$scratch/expected"; then
    candidate="$(/usr/bin/mktemp "$HOME/.zshrc.base-candidate.XXXXXXXX")"
    /bin/cp -p -- "$backup" "$candidate"
    /bin/chmod "$permissions" "$candidate"
    /bin/mv -f -- "$candidate" "$target"
    candidate=''
    printf 'Restored original .zshrc after interrupted migration.\n' >&2
  fi
  [[ -z "$candidate" ]] || /bin/rm -f -- "$candidate"
  [[ -z "$scratch" ]] || /bin/rm -rf -- "$scratch"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM HUP
block() {
  cat <<'ZSH'
# BEGIN BASE MANAGED OH MY ZSH
if (( ! $+functions[omz] )); then
  export ZSH="$HOME/.oh-my-zsh"
  ZSH_THEME="${ZSH_THEME-robbyrussell}"
  (( ${+plugins} )) || plugins=(git)
  source "$ZSH/oh-my-zsh.sh"
fi
typeset -g _PKGMACSETUP_OMZ_LOADED=1
# END BASE MANAGED OH MY ZSH
ZSH
}
fail() { printf 'Oh My Zsh migration needs attention: %s\n' "$*" >&2; exit 1; }
[[ "$HOME" == /* && -d "$HOME" && ! -L "$HOME" ]] || fail 'HOME is not a regular directory.'
[[ -f "$target" && ! -L "$target" ]] || fail '.zshrc must be a regular file; existing links are preserved.'
[[ "$(/usr/bin/stat -f %u "$target")" == "$(/usr/bin/id -u)" ]] || fail '.zshrc belongs to another user.'
permissions="$(/usr/bin/stat -f %Lp "$target")"
(( (8#$permissions & 022) == 0 )) || fail '.zshrc is writable by another user.'
if /usr/bin/grep -Eiv '^[[:space:]]*(#|$)' "$target" | /usr/bin/grep -Ei '(zprezto|prezto/init|zinit|zplug|antidote|antigen|zgen|zimfw|zsh4humans)' >/dev/null; then
  fail 'another shell framework or plugin manager is referenced; .zshrc was preserved. Use --skip-oh-my-zsh-migration to retain it.'
fi
managed=false
if /usr/bin/grep -Fq -- "$begin" "$target" || /usr/bin/grep -Fq -- "$end" "$target"; then
  [[ "$(/usr/bin/grep -Fxc -- "$begin" "$target")" == 1 && "$(/usr/bin/grep -Fxc -- "$end" "$target")" == 1 ]] \
    || fail 'managed block markers are duplicated or incomplete; .zshrc was preserved.'
  /usr/bin/cmp -s <(block) <(/usr/bin/sed -n '/^# BEGIN BASE MANAGED OH MY ZSH$/,/^# END BASE MANAGED OH MY ZSH$/p' "$target") \
    || fail 'the managed block was edited; .zshrc was preserved.'
  managed=true
fi
if [[ "$check" == true ]]; then
  [[ "$managed" == true ]] || fail 'startup migration is pending (no shell configuration was executed).'
  printf 'Oh My Zsh startup block is present; runtime loading is not tested in check mode.\n'
  exit 0
fi
scratch="$(/usr/bin/mktemp -d /private/tmp/macsetup-omz.XXXXXXXX)"
if [[ "$managed" == true ]]; then
  /usr/bin/env -i HOME="$scratch" ZDOTDIR="$scratch" PATH="$PATH" /bin/zsh -dfn "$target" \
    >"$scratch/syntax.log" 2>&1 || fail 'existing .zshrc has a syntax error; it was preserved.'
fi
if [[ "$managed" == false ]]; then
  candidate="$(/usr/bin/mktemp "$HOME/.zshrc.base-candidate.XXXXXXXX")"
  /bin/cp -p -- "$target" "$candidate"
  printf '\n' >> "$candidate"
  block >> "$candidate"
  /usr/bin/env -i HOME="$scratch" ZDOTDIR="$scratch" PATH="$PATH" /bin/zsh -dfn "$candidate" \
    >"$scratch/syntax.log" 2>&1 || fail 'the candidate .zshrc has a syntax error; the original is unchanged.'
  backup="$(/usr/bin/mktemp "$HOME/.zshrc.pre-base-ohmyzsh.$(/bin/date +%Y%m%d-%H%M%S).XXXXXXXX")"
  /bin/cp -p -- "$target" "$backup"
  /bin/chmod 600 "$backup"
  /bin/cp -- "$candidate" "$scratch/expected"
  /usr/bin/cmp -s "$target" "$backup" || fail '.zshrc changed during migration; it was preserved.'
  /bin/mv -f -- "$candidate" "$target"
  installed_candidate=true
  candidate=''
  printf 'Oh My Zsh startup backup: %s\n' "$backup"
fi
# Run the actual startup file in a separate, non-login interactive shell.
# No input or inherited credentials; suppress updater/compdump writes where supported.
/usr/bin/env -i HOME="$HOME" USER="$(/usr/bin/id -un)" LOGNAME="$(/usr/bin/id -un)" \
  PATH=/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin SHELL=/bin/zsh TERM=dumb \
  DISABLE_AUTO_UPDATE=true ZSH_COMPDUMP="$scratch/zcompdump" \
  /bin/zsh -ic '(( $+functions[omz] && ${_PKGMACSETUP_OMZ_LOADED:-0} == 1 )) || exit 1; print -r -- BASE_OMZ_READY' \
  </dev/null >"$scratch/startup.log" 2>&1 &
child=$!
for ((attempt=0; attempt<50; attempt++)); do
  kill -0 "$child" 2>/dev/null || break
  /bin/sleep 0.1
done
valid=false
if ! kill -0 "$child" 2>/dev/null; then
  if wait "$child" && /usr/bin/grep -Fxq BASE_OMZ_READY "$scratch/startup.log"; then valid=true; fi
else
  kill -TERM "$child" 2>/dev/null || true
  /bin/sleep 0.2
  kill -KILL "$child" 2>/dev/null || true
  wait "$child" 2>/dev/null || true
fi
child=''
if [[ "$valid" != true ]]; then
  if [[ -n "$backup" ]]; then
    if /usr/bin/cmp -s "$target" "$scratch/expected"; then
      candidate="$(/usr/bin/mktemp "$HOME/.zshrc.base-candidate.XXXXXXXX")"
      /bin/cp -p -- "$backup" "$candidate"
      /bin/chmod "$permissions" "$candidate"
      /bin/mv -f -- "$candidate" "$target"
      candidate=''
      installed_candidate=false
      fail "fresh Zsh did not load Oh My Zsh within five seconds; original .zshrc restored. Backup: $backup"
    fi
    fail "startup changed .zshrc; preserved the newer file. Original backup: $backup"
  fi
  fail 'fresh Zsh did not load Oh My Zsh within five seconds; existing managed file was preserved.'
fi
printf 'Verified Oh My Zsh in a fresh interactive Zsh session.\n'
