#!/bin/bash -p
# Standalone Base. No private discovery, authentication or sourced configuration.
set -Eeuo pipefail
unset BASH_ENV ENV CDPATH RUBYOPT RUBYLIB PYTHONPATH PYTHONHOME
export PATH=/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin
base_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
check=false
migrate_ohmyzsh=true
config_dir=''
scratch=''
drift=0
preference_failures=()
unresolved=()
deferred=()
# Per-invocation handoff from the shared launcher; never an account-login claim.
onepassword_installed_now="${PKGMACSETUP_1PASSWORD_JUST_INSTALLED:-false}"
finder=false keyboard=false trackpad=false dock=false textedit=false wallpaper=false
general=false screenshots=false input=false dock_layout=false applications=false restart_on_freeze=false
formulae=() casks=() mas_ids=() dotfiles=() declarations=() mas_declarations=()
format_output() {
  local columns="${COLUMNS:-80}" terminal_size
  # Preserve the terminal before command substitution redirects stdout.
  if [[ -t 1 ]] && { terminal_size="$(/bin/stty size <&3 2>/dev/null)"; } 3>&1; then
    columns="${terminal_size##* }"
  fi
  [[ "$columns" =~ ^[1-9][0-9]*$ ]] || columns=80
  /usr/bin/awk -v width="$columns" '
    BEGIN { width = width < 24 ? 24 : (width > 96 ? 96 : width) }
    {
      text = $0
      sub(/\r$/, "", text)
      prefix = "    "
      indent = prefix
      if (match(text, /^ +/)) {
        prefix = substr(text, 1, RLENGTH)
        text = substr(text, RLENGTH + 1)
        indent = prefix
        if (match(text, /^(→|·|✓|!|×|-) /)) {
          prefix = prefix substr(text, 1, RLENGTH)
          text = substr(text, RLENGTH + 1)
          indent = indent "  "
          if (match(text, /^(Warning|Error): /)) {
            prefix = prefix substr(text, 1, RLENGTH)
            indent = indent sprintf("%*s", RLENGTH, "")
            text = substr(text, RLENGTH + 1)
          }
        }
      }
      count = split(text, words, /[[:space:]]+/)
      line = prefix
      has_word = 0
      for (i = 1; i <= count; i++) {
        if (words[i] == "") continue
        if (has_word && length(line) + length(words[i]) + 1 > width) {
          print line
          line = indent
          has_word = 0
        }
        line = line (has_word ? " " : "") words[i]
        has_word = 1
      }
      if (has_word) print line
      else print ""
      fflush()
    }
  '
}
# Format only displayed output, never data consumed by inventory checks.
# pipefail retains the provider status; stdin remains attached for prompts.
run_visible() { "$@" 2>&1 | format_output; }
die() { printf '  × Error: %s\n' "$*" | format_output >&2; exit 2; }
note() { printf '  · %s\n' "$*" | format_output; }
ready() { printf '  ✓ %s\n' "$*" | format_output; }
heading() {
  if [[ -t 1 && "${TERM:-}" != dumb && -z "${NO_COLOR+x}" ]]; then
    printf '\n  \033[1m→ %s\033[0m\n' "$1"
  else
    printf '\n  → %s\n' "$1"
  fi
}
pending() { note "Pending: $*"; drift=1; }
clean_run() {
  /usr/bin/env -i HOME="$HOME" USER="$base_user" LOGNAME="$base_user" PATH="$PATH" \
    SHELL=/bin/zsh TERM="${TERM-dumb}" \
    HOMEBREW_NO_AUTO_UPDATE=1 HOMEBREW_NO_ANALYTICS=1 \
    HOMEBREW_NO_INSTALL_CLEANUP=1 HOMEBREW_NO_INSTALL_UPGRADE=1 \
    HOMEBREW_NO_INSTALLED_DEPENDENTS_CHECK=1 \
    HOMEBREW_BUNDLE_NO_UPGRADE=1 "$@"
}
account_ready() {
  local app="$1" answer=''
  [[ -t 0 ]] || return 0
  note "Sign in to or unlock $app, then continue. No time limit."
  clean_run /usr/bin/open -a "$app" || note "Open $app manually to continue."
  while true; do
    if ! read -r -p "  Return to continue · s skip · q quit > " answer; then
      exit 130
    fi
    case "$answer" in
      '') return 0 ;;
      s|S) return 1 ;;
      q|Q) exit 130 ;;
      *) note 'Choose Return, s, or q.' ;;
    esac
  done
}
prepare_app_store_authorization() {
  [[ -t 0 ]] || return 0
  note 'App Store installs may also need your Mac login password.'
  clean_run /usr/bin/sudo -n -v 2>/dev/null && return 0
  clean_run /usr/bin/sudo -v -p '  Mac login password (authorizes App Store installation): '
}
cleanup() {
  if [[ -n "$scratch" && "$scratch" == /private/tmp/macsetup-base.* ]]; then
    /bin/rm -rf -- "$scratch"
  fi
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM HUP
while [[ $# -gt 0 ]]; do
  case "$1" in
    --check) check=true; shift ;;
    --skip-oh-my-zsh-migration) migrate_ohmyzsh=false; shift ;;
    --config-dir)
      [[ $# -ge 2 && -z "$config_dir" ]] || die 'Use --config-dir once, with a directory.'
      config_dir="$2"; shift 2 ;;
    -h|--help)
      cat <<'HELP'
Usage: ./install.sh [options]

Set up everyday apps, macOS preferences and shell configuration.

  --config-dir DIR           Use a local configuration directory
  --check                    Report pending changes without applying them
  --skip-oh-my-zsh-migration Skip Oh My Zsh startup migration
  -h, --help                 Show help
HELP
      exit 0 ;;
    *) die "Unknown option: $1" ;;
  esac
done
[[ "$(/usr/bin/uname -s)" == Darwin ]] || die 'Base requires macOS.'
base_uid="$(/usr/bin/id -u)"
base_user="$(/usr/bin/id -un)"
[[ "$base_uid" != 0 ]] || die 'Run as your ordinary user, without sudo.'
[[ "$HOME" == /* && ! "$HOME" =~ [[:cntrl:]] && -d "$HOME" && ! -L "$HOME" ]] || die 'HOME must be a regular absolute directory.'
[[ "$(/usr/bin/stat -f %u "$HOME")" == "$base_uid" ]] || die 'HOME must belong to the current user.'
brewfile="$base_dir/Brewfile"
preferences="$base_dir/preferences.conf"
dot_source="$base_dir/dotfiles"
local_dots=false
if [[ -n "$config_dir" ]]; then
  [[ ! "$config_dir" =~ [[:cntrl:]] && -d "$config_dir" && ! -L "$config_dir" ]] || die 'Select a regular local directory without control characters.'
  config_dir="$(cd -- "$config_dir" && pwd -P)"
  [[ ! -e "$config_dir/Brewfile" && ! -L "$config_dir/Brewfile" ]] || brewfile="$config_dir/Brewfile"
  [[ ! -e "$config_dir/preferences.conf" && ! -L "$config_dir/preferences.conf" ]] || preferences="$config_dir/preferences.conf"
  if [[ -e "$config_dir/dotfiles" || -L "$config_dir/dotfiles" ]]; then
    dot_source="$config_dir/dotfiles"
    local_dots=true
  fi
fi
for source_file in "$brewfile" "$preferences"; do
  [[ -f "$source_file" && ! -L "$source_file" ]] || die "Configuration must be a regular file: $source_file"
done
# Data-only Brewfile declarations: no arbitrary Ruby or package hooks.
package_pattern='^[[:space:]]*(brew|cask)[[:space:]]+"([a-z0-9][a-z0-9+_.@-]*)"[[:space:]]*(#.*)?$'
mas_pattern='^[[:space:]]*mas[[:space:]]+"([A-Za-z0-9 ._()+-]+)",[[:space:]]*id:[[:space:]]*([0-9]+)[[:space:]]*(#.*)?$'
line_number=0
while IFS= read -r line || [[ -n "$line" ]]; do
  line_number=$((line_number + 1))
  [[ "$line" =~ ^[[:space:]]*(#.*)?$ ]] && continue
  if [[ "$line" =~ $package_pattern ]]; then
    declarations+=("${BASH_REMATCH[1]} \"${BASH_REMATCH[2]}\"")
    if [[ "${BASH_REMATCH[1]}" == brew ]]; then
      formulae+=("${BASH_REMATCH[2]}")
    else
      casks+=("${BASH_REMATCH[2]}")
    fi
  elif [[ "$line" =~ $mas_pattern ]]; then
    mas_ids+=("${BASH_REMATCH[2]}")
    mas_declarations+=("mas \"${BASH_REMATCH[1]}\", id: ${BASH_REMATCH[2]}")
  else
    die "Unsupported Brewfile declaration on line $line_number; use plain brew, cask or mas entries."
  fi
done < "$brewfile"
if [[ ${#mas_ids[@]} -gt 0 ]]; then
  /usr/bin/grep -Fxq mas <(printf '%s\n' ${formulae+"${formulae[@]}"}) \
    || die 'App Store entries require brew "mas" in the selected Brewfile.'
fi
seen_preferences=' '
while IFS= read -r line || [[ -n "$line" ]]; do
  [[ "$line" =~ ^[[:space:]]*(#.*)?$ ]] && continue
  [[ "$line" =~ ^(finder|keyboard|trackpad|dock|textedit|wallpaper|general|screenshots|input|dock_layout|applications|restart_on_freeze)=(true|false)$ ]] || die 'preferences.conf accepts only documented preference groups set to true|false.'
  key="${BASH_REMATCH[1]}" value="${BASH_REMATCH[2]}"
  [[ "$seen_preferences" != *" $key "* ]] || die "Duplicate preference group: $key"
  seen_preferences+="$key "
  printf -v "$key" '%s' "$value"
done < "$preferences"
# Validate destinations before package or preference mutation.
safe_destination() {
  local destination="$1" current="$HOME" component permissions
  [[ "$destination" == "$HOME/"* && ! "$destination" =~ [[:cntrl:]] ]] || die 'Invalid destination.'
  local relative="${destination#"$HOME/"}"
  local parts=()
  IFS=/ read -r -a parts <<< "$relative"
  for component in "${parts[@]}"; do
    [[ "$component" != . && "$component" != .. && -n "$component" ]] || die 'Invalid destination component.'
    current="$current/$component"
    [[ ! -L "$current" ]] || die "Destination is a symlink; preserved: $current"
    if [[ -e "$current" ]]; then
      [[ "$(/usr/bin/stat -f %u "$current")" == "$base_uid" ]] || die "Destination belongs to another user: $current"
      permissions="$(/usr/bin/stat -f %Lp "$current")"
      (( (8#$permissions & 022) == 0 )) || die "Destination is writable by another user: $current"
      if [[ "$current" != "$destination" ]]; then
        [[ -d "$current" ]] || die "Destination ancestor is not a directory: $current"
      fi
    fi
  done
}
ohmyzsh_dir="$HOME/.oh-my-zsh"
role_receipt="$HOME/Library/Application Support/PKGMacSetup/role"
safe_destination "$role_receipt"
[[ ! -e "$role_receipt" || -f "$role_receipt" ]] || die 'Setup role receipt is not a regular file; preserved.'
safe_destination "$ohmyzsh_dir"
if [[ -e "$ohmyzsh_dir" ]]; then
  [[ -d "$ohmyzsh_dir" && -f "$ohmyzsh_dir/oh-my-zsh.sh" && ! -L "$ohmyzsh_dir/oh-my-zsh.sh" ]] \
    || die 'Existing .oh-my-zsh is incomplete or unsafe; preserved for repair.'
fi
if [[ "$keyboard" == true ]]; then
  hotkey_plist="$HOME/Library/Preferences/com.apple.symbolichotkeys.plist"
  safe_destination "$hotkey_plist"
  if [[ -e "$hotkey_plist" ]]; then
    [[ -f "$hotkey_plist" ]] && /usr/bin/plutil -lint "$hotkey_plist" >/dev/null \
      || die 'Keyboard shortcut preferences are not a valid regular plist; preserved for repair.'
  fi
fi
if [[ "$finder" == true ]]; then
  safe_destination "$HOME/Library"
  finder_plist="$HOME/Library/Preferences/com.apple.finder.plist"
  safe_destination "$finder_plist"
  if [[ -e "$finder_plist" ]]; then
    [[ -f "$finder_plist" ]] && /usr/bin/plutil -lint "$finder_plist" >/dev/null \
      || die 'Finder preferences are not a valid regular plist; preserved for repair.'
  fi
fi
for preference_group in dock input applications; do
  [[ "${!preference_group}" == true ]] || continue
  case "$preference_group" in
    dock) preference_domain=com.apple.dock ;;
    input) preference_domain=com.apple.HIToolbox ;;
    applications) preference_domain=com.apple.messageshelper.MessageController ;;
  esac
  safe_destination "$HOME/Library/Preferences/$preference_domain.plist"
  if [[ "$preference_group" == input ]]; then
    safe_destination "$HOME/Library/Preferences/ByHost"
    for preference_file in "$HOME/Library/Preferences/ByHost/com.apple.HIToolbox."*.plist; do
      [[ -e "$preference_file" || -L "$preference_file" ]] || continue
      safe_destination "$preference_file"
    done
  fi
done
[[ -d "$dot_source" && ! -L "$dot_source" ]] || die 'dotfiles must be a regular directory.'
shopt -s nullglob dotglob
for source_file in "$dot_source"/*; do
  name="${source_file##*/}"
  case "$name" in .zprofile|.zshrc|.tmux.conf|.gitconfig) ;; *) die "Unsupported dotfile: $name" ;; esac
  [[ -f "$source_file" && ! -L "$source_file" ]] || die "Dotfile must be a regular file: $name"
  dotfiles+=("$name")
  if [[ "$local_dots" == true ]]; then
    safe_destination "$HOME/.config/macsetup/base/${name#.}"
    safe_destination "$HOME/$name"
    [[ ! -e "$HOME/$name" || -f "$HOME/$name" ]] || die "Home dotfile is not a regular file: $name"
    [[ ! -e "$HOME/.config/macsetup/base/${name#.}" || -f "$HOME/.config/macsetup/base/${name#.}" ]] || die "Managed fragment is not a regular file: $name"
  elif [[ ! -e "$HOME/$name" && ! -L "$HOME/$name" ]]; then
    safe_destination "$HOME/$name"
  fi
done
shopt -u nullglob dotglob
if [[ "$check" == true ]]; then heading 'Base setup · check only'
else heading 'Base setup'; fi
[[ -z "$config_dir" ]] || note "Configuration: $config_dir"
note 'Homebrew may update dependencies when installing missing packages.'
note 'No broad upgrade or cleanup.'
heading 'Homebrew packages'
brew_bin=/opt/homebrew/bin/brew
[[ "$(/usr/bin/uname -m)" == arm64 ]] || brew_bin=/usr/local/bin/brew
if [[ ! -x "$brew_bin" ]]; then
  if [[ "$check" == true ]]; then
    pending 'Homebrew is not installed.'
  else
    if ! /usr/bin/xcode-select -p >/dev/null 2>&1; then
      /usr/bin/xcode-select --install
      die 'Complete Apple Command Line Tools installation, then rerun Base.'
    fi
    scratch="$(/usr/bin/mktemp -d /private/tmp/macsetup-base.XXXXXX)"
    clean_run /usr/bin/curl -q --fail --silent --show-error --location --proto '=https' --proto-redir '=https' \
      --output "$scratch/homebrew.sh" https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh
    [[ -s "$scratch/homebrew.sh" ]] || die 'Homebrew installer download was empty.'
    /bin/bash -p -n "$scratch/homebrew.sh"
    note 'Installing Homebrew · administrator approval may be required'
    clean_run /bin/bash -p "$scratch/homebrew.sh"
    [[ -x "$brew_bin" ]] || die 'Homebrew remains unavailable.'
  fi
fi
# Known baseline app paths work offline, including externally installed copies.
external_app() {
  local name="$1" app='' metadata='' index=0 artifact='' found=false
  case "$name" in raycast) app=Raycast.app ;; zed) app=Zed.app ;; 1password) app=1Password.app ;; esac
  if [[ -n "$app" ]]; then
    [[ -d "/Applications/$app" && ! -L "/Applications/$app" ]] \
      || [[ -d "$HOME/Applications/$app" && ! -L "$HOME/Applications/$app" ]]
    return
  fi
  # Public JSON only, no cask Ruby evaluation or persistent metadata cache.
  metadata="$(clean_run /usr/bin/curl -q --fail --silent --show-error --location \
    --proto '=https' --proto-redir '=https' --connect-timeout 10 --max-time 30 \
    "https://formulae.brew.sh/api/cask/$name.json")" || die "Cannot inspect external application artifacts for $name."
  printf '%s' "$metadata" | /usr/bin/plutil -extract artifacts json -o /dev/null - 2>/dev/null \
    || die "Invalid public cask metadata for $name."
  while artifact="$(printf '%s' "$metadata" | /usr/bin/plutil -extract "artifacts.$index" json -o - - 2>/dev/null)"; do
    app="$(printf '%s' "$artifact" | /usr/bin/plutil -extract app.0 raw -o - - 2>/dev/null || true)"
    if [[ -n "$app" ]]; then
      [[ "$app" != */* && "$app" != *\\* && ! "$app" =~ [[:cntrl:]] && "$app" == *.app ]] || return 1
      [[ -d "/Applications/$app" && ! -L "/Applications/$app" ]] \
        || [[ -d "$HOME/Applications/$app" && ! -L "$HOME/Applications/$app" ]] || return 1
      found=true
    fi
    index=$((index + 1))
  done
  [[ "$found" == true ]]
}
skip_casks=''
# Prepare the account app before the rest of the selected packages. Local
# Brewfiles remain authoritative: do not add it when they omit 1Password.
if [[ "$check" == false && -x "$brew_bin" ]]; then
  for package in ${casks+"${casks[@]}"}; do
    if [[ "$package" == 1password ]]; then
      if ! clean_run "$brew_bin" list --cask -1 | /usr/bin/grep -Fxq 1password && ! external_app 1password; then
        [[ -n "$scratch" ]] || scratch="$(/usr/bin/mktemp -d /private/tmp/macsetup-base.XXXXXX)"
        printf '%s\n' 'cask "1password"' > "$scratch/Prerequisites.Brewfile"
        (cd -- "$scratch" && run_visible clean_run "$brew_bin" bundle install --file="$scratch/Prerequisites.Brewfile" --no-upgrade) \
          || die '1Password prerequisite installation failed.'
        onepassword_installed_now=true
      fi
      if [[ "$onepassword_installed_now" == true ]]; then
        if ! account_ready 1Password; then deferred+=('1Password sign-in'); fi
      fi
      break
    fi
  done
fi
if [[ -x "$brew_bin" ]]; then
  installed_formulae="$(clean_run "$brew_bin" list --formula -1)"
  installed_casks="$(clean_run "$brew_bin" list --cask -1)"
  for package in ${formulae+"${formulae[@]}"}; do
    /usr/bin/grep -Fxq -- "$package" <<< "$installed_formulae" || pending "Formula: $package"
  done
  for package in ${casks+"${casks[@]}"}; do
    if /usr/bin/grep -Fxq -- "$package" <<< "$installed_casks"; then
      continue
    elif external_app "$package"; then
      note "Preserved external app: $package"
      skip_casks+=" $package"
    else
      pending "Cask: $package"
    fi
  done
  if [[ "$check" == false ]]; then
    [[ -n "$scratch" ]] || scratch="$(/usr/bin/mktemp -d /private/tmp/macsetup-base.XXXXXX)"
    # Emit only validated declarations; never execute the original Ruby file.
    printf '%s\n' ${declarations+"${declarations[@]}"} > "$scratch/Brewfile"
    (
      cd -- "$scratch"
      run_visible clean_run /usr/bin/env HOMEBREW_BUNDLE_CASK_SKIP="$skip_casks" \
        "$brew_bin" bundle install --file="$scratch/Brewfile" --no-upgrade
    ) || die 'Package installation stopped. Existing app conflicts are preserved; do not force adoption.'
  fi
fi
heading 'macOS settings'
if [[ "$wallpaper" == true ]]; then
  wallpaper_mode=--apply
  [[ "$check" == false ]] || wallpaper_mode=--check
  if wallpaper_result="$(clean_run /usr/bin/swift "$base_dir/set-desktop-wallpaper.swift" \
      '/System/Library/Desktop Pictures/Solid Colors/Stone.png' "$wallpaper_mode")"; then
    case "$wallpaper_result" in
      'wallpaper: unchanged') ;;
      'wallpaper: would-change ('*) pending 'Desktop wallpaper: Stone' ;;
      'wallpaper: changed ('*) ready 'Desktop wallpaper: Stone' ;;
      'wallpaper: skipped ('*) note "$wallpaper_result" ;;
      *) pending 'Desktop wallpaper could not be verified.'
         unresolved+=('Desktop wallpaper returned an unexpected result; rerun Base.') ;;
    esac
  else
    pending 'Desktop wallpaper could not be applied or verified.'
    unresolved+=('Desktop wallpaper failed; review the error above, then rerun Base.')
  fi
fi
preference_failed() {
  preference_failures+=("$1 $2: $3")
  printf '  ! Warning: preference not applied: %s %s\n             %s. Continuing setup.\n' "$1" "$2" "$3" | format_output >&2
  if [[ "$1" == com.apple.TextEdit ]]; then
    note "TextEdit permission: allow the app running setup (Terminal, iTerm, or your editor) to access other apps' data / Full Disk Access in System Settings > Privacy & Security, then quit and reopen it."
    note 'Or set TextEdit > Settings > New Document > Plain text manually. If access is already allowed, review the write or verification error above.'
  elif [[ "$1" == com.apple.universalaccess ]]; then
    note 'Review System Settings > Accessibility > Display > Reduce transparency.'
  fi
}
preference() {
  local domain="$1" key="$2" kind="$3" desired="$4" current='' status write_value="$4"
  # defaults reads booleans as 1/0, but its documented write syntax uses words.
  if [[ "$kind" == bool ]]; then
    case "$desired" in
      1) write_value=true ;;
      0) write_value=false ;;
      *) die "Invalid boolean preference: $domain $key" ;;
    esac
  fi
  current="$(/usr/bin/defaults read "$domain" "$key" 2>/dev/null || true)"
  if ! preference_matches "$kind" "$current" "$desired"; then
    if [[ "$check" == true ]]; then
      pending "Preference: $domain $key -> $desired"
    else
      note "Set preference: $domain $key -> $desired"
      if /usr/bin/defaults write "$domain" "$key" "-$kind" "$write_value"; then
        if current="$(/usr/bin/defaults read "$domain" "$key")"; then
          if ! preference_matches "$kind" "$current" "$desired"; then
            preference_failed "$domain" "$key" 'value did not persist'
          fi
        else
          status=$?
          preference_failed "$domain" "$key" "verification read failed with exit $status"
        fi
      else
        status=$?
        preference_failed "$domain" "$key" "defaults write failed with exit $status"
      fi
    fi
  fi
}
preference_matches() {
  if [[ "$1" == int || "$1" == float ]]; then
    [[ "$2" =~ ^-?[0-9]+([.][0-9]+)?$ ]] || return 1
    /usr/bin/awk -v current="$2" -v desired="$3" 'BEGIN { exit !(current + 0 == desired + 0) }'
  else
    [[ "$2" == "$3" ]]
  fi
}
finder_views() {
  local section key kind desired current path
  for section in DesktopViewSettings FK_StandardViewSettings StandardViewSettings; do
    for key in arrangeBy iconSize; do
      kind=string desired=grid
      [[ "$key" != iconSize ]] || { kind=integer; desired=64; }
      path=":$section:IconViewSettings:$key"
      current="$(/usr/libexec/PlistBuddy -c "Print $path" "$finder_plist" 2>/dev/null || true)"
      if [[ "$key" == iconSize ]]; then
        preference_matches int "$current" "$desired" && continue
      else
        [[ "$current" != "$desired" ]] || continue
      fi
      if [[ "$check" == true ]]; then pending "Finder: $section $key -> $desired"; continue; fi
      /usr/libexec/PlistBuddy -c "Add :$section dict" "$finder_plist" 2>/dev/null || true
      /usr/libexec/PlistBuddy -c "Add :$section:IconViewSettings dict" "$finder_plist" 2>/dev/null || true
      if ! /usr/libexec/PlistBuddy -c "Set $path $desired" "$finder_plist" 2>/dev/null \
          && ! /usr/libexec/PlistBuddy -c "Add $path $kind $desired" "$finder_plist"; then
        preference_failed com.apple.finder "$path" 'icon view write failed'
        continue
      fi
      current="$(/usr/libexec/PlistBuddy -c "Print $path" "$finder_plist" 2>/dev/null || true)"
      if [[ "$key" == iconSize ]]; then
        preference_matches int "$current" "$desired" \
          || preference_failed com.apple.finder "$path" 'icon size did not persist'
      elif [[ "$current" != "$desired" ]]; then
        preference_failed com.apple.finder "$path" 'icon arrangement did not persist'
      fi
    done
  done
}
show_library() {
  local flags
  [[ -d "$HOME/Library" ]] || return 0
  if ! flags="$(/usr/bin/stat -f %Sf "$HOME/Library")"; then
    pending 'Library folder visibility could not be checked.'
    unresolved+=('Library folder visibility could not be checked.')
    return
  fi
  [[ ",$flags," == *,hidden,* ]] || return 0
  if [[ "$check" == true ]]; then pending 'Show the Library folder'; return; fi
  if run_visible /usr/bin/chflags nohidden "$HOME/Library" \
      && flags="$(/usr/bin/stat -f %Sf "$HOME/Library")" && [[ ",$flags," != *,hidden,* ]]; then
    ready 'Library folder is visible'
  else
    preference_failed com.apple.finder Library 'could not make Library visible'
  fi
}
input_source_present() {
  local key="$1" kind="$2"
  { /usr/bin/defaults read com.apple.HIToolbox "$key" 2>/dev/null || true
    /usr/bin/defaults -currentHost read com.apple.HIToolbox "$key" 2>/dev/null || true
  } | /usr/bin/awk -v kind="$kind" '
    BEGIN { found = 0; RS = "}"; }
    /"Bundle ID" = "com.apple.inputmethod.SCIM"/ {
      if (kind == "keyboard" && /InputSourceKind = "Keyboard Input Method"/) found = 1;
      if (kind == "pinyin" && /"Input Mode" = "com.apple.inputmethod.SCIM.ITABC"/) found = 1;
    }
    END { exit(found ? 0 : 1); }'
}
pinyin_sources() {
  local key kind entry
  for key in AppleEnabledInputSources AppleSelectedInputSources; do
    for kind in keyboard pinyin; do
      input_source_present "$key" "$kind" && continue
      if [[ "$check" == true ]]; then pending "Pinyin: $key $kind"; continue; fi
      if [[ "$key" == AppleSelectedInputSources ]] \
          && ! /usr/bin/defaults read com.apple.HIToolbox "$key" >/dev/null 2>&1 \
          && ! /usr/bin/defaults -currentHost read com.apple.HIToolbox "$key" >/dev/null 2>&1; then
        /usr/bin/defaults write com.apple.HIToolbox "$key" -array-add \
          '{ InputSourceKind = "Keyboard Layout"; "KeyboardLayout ID" = 252; "KeyboardLayout Name" = "ABC"; }' \
          || preference_failed com.apple.HIToolbox "$key" 'initial ABC input source write failed'
      fi
      if [[ "$kind" == keyboard ]]; then
        entry='{ InputSourceKind = "Keyboard Input Method"; "Bundle ID" = "com.apple.inputmethod.SCIM"; }'
      else
        entry='{ InputSourceKind = "Input Mode"; "Bundle ID" = "com.apple.inputmethod.SCIM"; "Input Mode" = "com.apple.inputmethod.SCIM.ITABC"; }'
      fi
      if ! /usr/bin/defaults write com.apple.HIToolbox "$key" -array-add "$entry"; then
        preference_failed com.apple.HIToolbox "$key" 'Pinyin input source write failed'
      elif ! input_source_present "$key" "$kind"; then
        # Match the Dev/Admin fallback for preferences resolved from ByHost.
        if ! /usr/bin/defaults -currentHost write com.apple.HIToolbox "$key" -array-add "$entry" \
            || ! input_source_present "$key" "$kind"; then
          preference_failed com.apple.HIToolbox "$key" 'Pinyin input source did not persist'
        fi
      fi
    done
  done
}
clear_dock_layout() {
  local key snapshot current
  for key in persistent-apps persistent-others; do
    snapshot="$(/usr/bin/defaults export com.apple.dock - 2>/dev/null)" || {
      pending 'Dock layout could not be inspected.'
      unresolved+=('Dock layout could not be inspected; existing icons were preserved.')
      return
    }
    # An absent key is already empty. Preserve any other Dock preference.
    current="$(printf '%s' "$snapshot" | /usr/bin/plutil -extract "$key" json -o - - 2>/dev/null || true)"
    [[ -n "$current" && "$current" != '[]' ]] || continue
    if [[ "$check" == true ]]; then pending "Clear Dock icons: $key"; continue; fi
    note "Clear Dock icons: $key"
    if ! /usr/bin/defaults write com.apple.dock "$key" -array; then
      preference_failed com.apple.dock "$key" 'Dock icon removal failed'
      continue
    fi
    snapshot="$(/usr/bin/defaults export com.apple.dock - 2>/dev/null || true)"
    current="$(printf '%s' "$snapshot" | /usr/bin/plutil -extract "$key" json -o - - 2>/dev/null || true)"
    [[ "$current" == '[]' ]] || preference_failed com.apple.dock "$key" 'Dock icon removal did not persist'
  done
}
messages_preferences() {
  local key snapshot current
  for key in automaticQuoteSubstitutionEnabled continuousSpellCheckingEnabled; do
    snapshot="$(/usr/bin/defaults export com.apple.messageshelper.MessageController - 2>/dev/null || true)"
    current="$(printf '%s' "$snapshot" | /usr/bin/plutil -extract "SOInputLineSettings.$key" raw -o - - 2>/dev/null || true)"
    [[ "$current" != false && "$current" != 0 ]] || continue
    if [[ "$check" == true ]]; then pending "Messages: $key -> false"; continue; fi
    if ! /usr/bin/defaults write com.apple.messageshelper.MessageController SOInputLineSettings -dict-add "$key" '<false/>'; then
      preference_failed com.apple.messageshelper.MessageController "$key" 'Messages preference write failed'
      continue
    fi
    snapshot="$(/usr/bin/defaults export com.apple.messageshelper.MessageController - 2>/dev/null || true)"
    current="$(printf '%s' "$snapshot" | /usr/bin/plutil -extract "SOInputLineSettings.$key" raw -o - - 2>/dev/null || true)"
    [[ "$current" == false || "$current" == 0 ]] \
      || preference_failed com.apple.messageshelper.MessageController "$key" 'Messages preference did not persist'
  done
}
restart_freeze_setting() {
  local current diagnostic status=0
  current="$(clean_run /usr/bin/sudo -n /usr/sbin/systemsetup -getrestartfreeze 2>/dev/null || true)"
  [[ "$current" != 'Restart After Freeze: On' ]] || return 0
  if [[ "$check" == true ]]; then
    pending 'Restart after freeze: enable or verify with administrator access'
    return
  fi
  note 'Enable restart after freeze · administrator approval may be required'
  # Keep the password prompt on the terminal before capturing native diagnostics.
  if ! clean_run /usr/bin/sudo -v -p '  Mac login password (authorizes restart setting): '; then
    preference_failed systemsetup restartfreeze 'administrator authorization was not completed'
    return
  fi
  diagnostic="$(clean_run /usr/bin/sudo -n /usr/sbin/systemsetup -setrestartfreeze on 2>&1)" || status=$?
  current="$(clean_run /usr/bin/sudo -n /usr/sbin/systemsetup -getrestartfreeze 2>/dev/null || true)"
  if [[ "$status" == 0 && "$current" == 'Restart After Freeze: On' ]]; then
    ready 'Restart after freeze is enabled'
  else
    [[ -z "$diagnostic" ]] || printf '%s\n' "$diagnostic" | format_output >&2
    preference_failed systemsetup restartfreeze 'restart after freeze could not be enabled or verified'
  fi
}
# Only these two fixed shortcuts are managed; dict-add preserves other hotkeys.
hotkey_matches() {
  local hotkey="$1" snapshot enabled parameters kind
  hotkey_observation='preference export unavailable'
  snapshot="$(/usr/bin/defaults export com.apple.symbolichotkeys - 2>/dev/null)" || return 1
  hotkey_observation='enabled flag unavailable'
  enabled="$(printf '%s' "$snapshot" | /usr/bin/plutil -extract "AppleSymbolicHotKeys.$hotkey.enabled" raw -o - - 2>/dev/null)" || return 1
  case "$enabled" in
    false|0) hotkey_observation='enabled flag is off' ;;
    true|1) hotkey_observation='enabled flag is on' ;;
    *) hotkey_observation='enabled flag has an unsupported value' ;;
  esac
  # macOS preferences can export CFBoolean values or numeric 0/1 values.
  if [[ "$hotkey" == 64 ]]; then [[ "$enabled" == false || "$enabled" == 0 ]]; return; fi
  [[ "$enabled" == true || "$enabled" == 1 ]] || return 1
  hotkey_observation='shortcut parameters unavailable or different'
  parameters="$(printf '%s' "$snapshot" | /usr/bin/plutil -extract AppleSymbolicHotKeys.61.value.parameters json -o - - 2>/dev/null)" || return 1
  kind="$(printf '%s' "$snapshot" | /usr/bin/plutil -extract AppleSymbolicHotKeys.61.value.type raw -o - - 2>/dev/null)" || return 1
  [[ "$parameters" == '[32,49,1048576]' && "$kind" == standard ]]
}
hotkey_persisted() {
  local attempt
  for attempt in 1 2 3; do
    hotkey_matches "$1" && return 0
    [[ "$attempt" == 3 ]] || /bin/sleep 0.2
  done
  return 1
}
keyboard_shortcuts() {
  local hotkey payload
  for hotkey in 64 61; do
    hotkey_matches "$hotkey" && continue
    if [[ "$check" == true ]]; then
      pending "Keyboard shortcut: $hotkey (Command-Space switches input source)"
      continue
    fi
    if [[ "$hotkey" == 64 ]]; then
      payload='<dict><key>enabled</key><false/></dict>'
    else
      payload='<dict><key>enabled</key><true/><key>value</key><dict><key>type</key><string>standard</string><key>parameters</key><array><integer>32</integer><integer>49</integer><integer>1048576</integer></array></dict></dict>'
    fi
    note "Set keyboard shortcut: $hotkey (Command-Space switches input source)"
    if ! /usr/bin/defaults write com.apple.symbolichotkeys AppleSymbolicHotKeys -dict-add "$hotkey" "$payload"; then
      preference_failed com.apple.symbolichotkeys "$hotkey" 'shortcut write failed'
    elif ! hotkey_persisted "$hotkey"; then
      preference_failed com.apple.symbolichotkeys "$hotkey" "shortcut did not persist ($hotkey_observation)"
    fi
  done
}
if [[ "$finder" == true ]]; then
  preference NSGlobalDomain AppleShowAllExtensions bool 1
  preference com.apple.finder ShowPathbar bool 1
  preference com.apple.finder ShowStatusBar bool 1
  preference com.apple.finder FXPreferredViewStyle string clmv
  preference com.apple.finder FXDefaultSearchScope string SCcf
  preference com.apple.finder NewWindowTarget string PfDe
  preference com.apple.finder ShowHardDrivesOnDesktop bool 1
  preference com.apple.finder ShowExternalHardDrivesOnDesktop bool 1
  preference com.apple.finder ShowMountedServersOnDesktop bool 1
  preference com.apple.finder ShowRemovableMediaOnDesktop bool 1
  preference com.apple.finder QLEnableTextSelection bool 1
  preference com.apple.finder _FXShowPosixPathInTitle bool 1
  preference com.apple.finder FXEnableExtensionChangeWarning bool 0
  preference NSGlobalDomain com.apple.springing.enabled bool 1
  preference NSGlobalDomain com.apple.springing.delay float 0.1
  preference com.apple.desktopservices DSDontWriteNetworkStores bool 1
  finder_views
  show_library
fi
if [[ "$general" == true ]]; then
  preference NSGlobalDomain NSNavPanelExpandedStateForSaveMode bool 1
  preference NSGlobalDomain PMPrintingExpandedStateForPrint bool 1
  preference NSGlobalDomain NSDocumentSaveNewDocumentsToCloud bool 0
  preference com.apple.print.PrintingPrefs 'Quit When Finished' bool 1
fi
if [[ "$screenshots" == true ]]; then
  preference com.apple.screencapture location string "$HOME/Downloads"
  preference com.apple.screencapture type string png
  preference com.apple.screencapture disable-shadow bool 1
fi
if [[ "$keyboard" == true ]]; then
  preference NSGlobalDomain KeyRepeat int 2
  preference NSGlobalDomain InitialKeyRepeat int 15
  preference NSGlobalDomain ApplePressAndHoldEnabled bool 0
  preference NSGlobalDomain NSAutomaticQuoteSubstitutionEnabled bool 0
  preference NSGlobalDomain NSAutomaticDashSubstitutionEnabled bool 0
  preference NSGlobalDomain NSAutomaticSpellingCorrectionEnabled bool 0
  keyboard_shortcuts
fi
if [[ "$input" == true ]]; then
  pinyin_sources
  preference com.apple.TextInputMenu visible bool 1
fi
if [[ "$trackpad" == true ]]; then
  for domain in com.apple.AppleMultitouchTrackpad com.apple.driver.AppleBluetoothMultitouch.trackpad; do
    preference "$domain" Clicking int 1
    preference "$domain" TrackpadRightClick bool 1
    preference "$domain" TrackpadThreeFingerDrag bool 1
  done
  preference com.apple.AppleMultitouchTrackpad FirstClickThreshold int 0
fi
if [[ "$dock" == true ]]; then
  preference com.apple.dock autohide bool 1
  preference com.apple.dock tilesize int 48
  preference com.apple.dock show-recents bool 0
  preference com.apple.dock expose-animation-duration float 0.15
  preference com.apple.dock showhidden bool 1
  preference com.apple.universalaccess reduceTransparency bool 1
  preference com.apple.dock wvous-br-corner int 2
  preference com.apple.dock wvous-tr-corner int 10
  preference com.apple.dock wvous-bl-corner int 4
  [[ "$dock_layout" == false ]] || clear_dock_layout
fi
if [[ "$applications" == true ]]; then
  preference com.apple.appstore InAppReviewEnabled int 0
  preference com.apple.ActivityMonitor OpenMainWindow bool 1
  preference com.apple.ActivityMonitor ShowCategory int 0
  messages_preferences
fi
[[ "$restart_on_freeze" == false ]] || restart_freeze_setting
if [[ "$textedit" == true ]]; then
  textedit_preferences="$HOME/Library/Containers/com.apple.TextEdit/Data/Library/Preferences"
  # Probe existing containers without printing file names or changing access.
  # A missing container is allowed: defaults may create the preference normally.
  if [[ -d "$textedit_preferences" ]] && ! /bin/ls "$textedit_preferences" >/dev/null 2>&1; then
    if [[ "$check" == true ]]; then
      pending 'TextEdit preferences are inaccessible; permission is required to verify plain-text format.'
    else
      preference_failed com.apple.TextEdit RichText 'missing access to protected preferences'
    fi
  else
    preference com.apple.TextEdit RichText int 0
  fi
fi
heading 'Shell configuration'
if [[ ! -d "$ohmyzsh_dir" ]]; then
  if [[ "$check" == true ]]; then
    pending 'Oh My Zsh is not installed.'
  else
    [[ -n "$scratch" ]] || scratch="$(/usr/bin/mktemp -d /private/tmp/macsetup-base.XXXXXX)"
    run_visible clean_run /usr/bin/git clone --depth 1 --branch master -- \
      https://github.com/ohmyzsh/ohmyzsh.git "$scratch/oh-my-zsh" \
      || die 'Oh My Zsh download failed; rerun Base.'
    [[ -f "$scratch/oh-my-zsh/oh-my-zsh.sh" && ! -L "$scratch/oh-my-zsh/oh-my-zsh.sh" ]] \
      || die 'Oh My Zsh download is incomplete.'
    safe_destination "$ohmyzsh_dir"
    [[ ! -e "$ohmyzsh_dir" ]] || die 'Oh My Zsh destination appeared during setup; preserved.'
    /bin/mv -n -- "$scratch/oh-my-zsh" "$ohmyzsh_dir"
    ready 'Installed Oh My Zsh'
  fi
else
  note 'Preserved existing Oh My Zsh installation.'
fi
# Shell fragments are never evaluated here.
for name in ${dotfiles+"${dotfiles[@]}"}; do
  source_file="$dot_source/$name"
  destination="$HOME/$name"
  if [[ "$local_dots" == true ]]; then
    fragment="$HOME/.config/macsetup/base/${name#.}"
    case "$name" in
      .zprofile) include='source "$HOME/.config/macsetup/base/zprofile"' ;;
      .zshrc) include='source "$HOME/.config/macsetup/base/zshrc"' ;;
      .tmux.conf|.gitconfig)
        escaped_fragment="${fragment//\\/\\\\}"
        escaped_fragment="${escaped_fragment//\"/\\\"}"
        if [[ "$name" == .tmux.conf ]]; then
          escaped_fragment="${escaped_fragment//\$/\\$}"
          include="source-file \"$escaped_fragment\""
        else
          include="$(printf '[include]\n\tpath = \"%s\"' "$escaped_fragment")"
        fi ;;
    esac
    included=false
    if [[ -f "$destination" ]]; then
      if [[ "$name" == .gitconfig ]]; then
        # Compare the literal adjacent two-line block, with no awk string escapes.
        previous=''
        while IFS= read -r dot_line || [[ -n "$dot_line" ]]; do
          if [[ "$previous"$'\n'"$dot_line" == "$include" ]]; then included=true; break; fi
          previous="$dot_line"
        done < "$destination"
      else
        /usr/bin/grep -Fxq -- "$include" "$destination" && included=true
      fi
    fi
    if ! /usr/bin/cmp -s -- "$source_file" "$fragment"; then
      if [[ "$check" == true ]]; then pending "Managed dotfile: $name"
      else
        /bin/mkdir -p -- "$HOME/.config/macsetup/base"
        /bin/cp -- "$source_file" "$fragment"
        /bin/chmod 600 "$fragment"
        ready "Updated managed dotfile: $name"
      fi
    fi
    if [[ "$included" == false ]]; then
      if [[ "$check" == true ]]; then pending "Dotfile include: $name"
      else
        printf '\n%s\n' "$include" >> "$destination"
        ready "Added dotfile include: $name"
      fi
    fi
  elif [[ -e "$destination" || -L "$destination" ]]; then
    note "Preserved existing dotfile: $name"
  elif [[ "$check" == true ]]; then
    pending "Default dotfile: $name"
  else
    /bin/cp -n -- "$source_file" "$destination"
    ready "Created default dotfile: $name"
  fi
done
if [[ "$migrate_ohmyzsh" == true && "$local_dots" == false ]]; then
  if ! run_visible clean_run /bin/bash -p "$base_dir/migrate-ohmyzsh.sh" "$check"; then
    pending 'Oh My Zsh startup migration requires attention.'
    unresolved+=('Oh My Zsh startup was not verified; review the migration message above.')
  fi
fi
heading 'App Store apps'
if [[ ${#mas_ids[@]} -gt 0 ]]; then
  mas_bin="$(dirname "$brew_bin")/mas"
  mas_pending=false
  installed_mas=''
  if [[ ! -x "$mas_bin" ]] || ! installed_mas="$(clean_run "$mas_bin" list)"; then
    pending 'App Store inventory is unavailable.'
    mas_pending=true
  else
    for package in ${mas_ids+"${mas_ids[@]}"}; do
      if ! /usr/bin/grep -Eq "^[[:space:]]*${package}[[:space:]]" <<< "$installed_mas"; then
        pending "App Store app: $package"
        mas_pending=true
      fi
    done
  fi
  mas_ready=true
  if [[ "$check" == false && "$mas_pending" == true ]]; then
    if ! account_ready 'App Store'; then
      mas_ready=false
      deferred+=('App Store apps')
    fi
  fi
  if [[ "$check" == false && "$mas_pending" == true && "$mas_ready" == true ]]; then
    if ! prepare_app_store_authorization; then
      mas_ready=false
      unresolved+=('Mac administrator authorization was not completed; App Store apps were not installed.')
    fi
  fi
  if [[ "$check" == false && "$mas_pending" == true && "$mas_ready" == true ]]; then
    note 'Apple may request your Apple Account password or Touch ID for downloads.'
    printf '%s\n' "${mas_declarations[@]}" > "$scratch/AppStore.Brewfile"
    if (cd -- "$scratch" && run_visible clean_run "$brew_bin" bundle install --file="$scratch/AppStore.Brewfile" --no-upgrade); then
      if ! installed_mas="$(clean_run "$mas_bin" list)"; then
        unresolved+=('App Store inventory could not be verified after installation.')
      else
        for package in "${mas_ids[@]}"; do
          /usr/bin/grep -Eq "^[[:space:]]*${package}[[:space:]]" <<< "$installed_mas" \
            || unresolved+=("App Store app remains missing: $package")
        done
      fi
    else
      unresolved+=('App Store apps remain pending. Sign into the App Store using your existing account, then rerun Base.')
    fi
  fi
fi
if [[ "$check" == true ]]; then
  [[ "$drift" == 0 ]] || { printf '\n  ! Base has pending changes.\n'; exit 1; }
  printf '\n'
  ready 'Base is ready for the selected configuration.'
else
  printf '\n'
  # A small data-only receipt lets the shared starter report the last selected
  # role. --check never writes it; pending accounts remain visible below.
  safe_destination "$role_receipt"
  mkdir -p "${role_receipt%/*}"
  chmod 700 "${role_receipt%/*}"
  receipt_tmp="$(mktemp "${role_receipt%/*}/.role.XXXXXX")"
  printf 'Base\n' > "$receipt_tmp"
  chmod 600 "$receipt_tmp"
  mv -f "$receipt_tmp" "$role_receipt"
  if [[ ${#preference_failures[@]} -gt 0 || ${#unresolved[@]} -gt 0 ]]; then
    printf '  ! Base needs attention\n'
    if [[ ${#preference_failures[@]} -gt 0 ]]; then
      printf '    - %s\n' "${preference_failures[@]}" | format_output
      note 'Resolve the preference errors above, then rerun as the same user.'
    fi
    [[ ${#unresolved[@]} == 0 ]] || printf '    - %s\n' "${unresolved[@]}" | format_output
    [[ ${#deferred[@]} == 0 ]] || printf '  · Deferred by request: %s\n' "${deferred[@]}" | format_output
    note 'Rerun Base after resolving these items; --check lists pending changes.'
    exit 3
  fi
  ready 'Base setup complete'
  if [[ ${#deferred[@]} -gt 0 ]]; then
    printf '  · Deferred by request: %s\n' "${deferred[@]}" | format_output
    note 'Rerun Base when ready.'
  fi
  note 'Open a new shell. Some settings need an app restart or logout.'
  note 'Finish app sign-in and permissions when prompted.'
fi
