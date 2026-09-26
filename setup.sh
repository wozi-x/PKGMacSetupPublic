#!/bin/bash -p
# Standalone Base. No private discovery, authentication or sourced configuration.
set -Eeuo pipefail
unset BASH_ENV ENV CDPATH RUBYOPT RUBYLIB PYTHONPATH PYTHONHOME
export PATH=/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin
base_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
check=false
config_dir=''
scratch=''
drift=0
preference_failures=()
unresolved=()
finder=false keyboard=false trackpad=false dock=false
formulae=() casks=() mas_ids=() dotfiles=() declarations=() mas_declarations=()
die() { printf 'Error: %s\n' "$*" >&2; exit 2; }
note() { printf '%s\n' "$*"; }
pending() { note "Pending: $*"; drift=1; }
clean_run() {
  /usr/bin/env -i HOME="$HOME" USER="$base_user" LOGNAME="$base_user" PATH="$PATH" \
    SHELL=/bin/zsh TERM="${TERM-dumb}" \
    HOMEBREW_NO_AUTO_UPDATE=1 HOMEBREW_NO_ANALYTICS=1 \
    HOMEBREW_NO_INSTALL_CLEANUP=1 HOMEBREW_NO_INSTALL_UPGRADE=1 \
    HOMEBREW_NO_INSTALLED_DEPENDENTS_CHECK=1 \
    HOMEBREW_BUNDLE_NO_UPGRADE=1 "$@"
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
    --config-dir)
      [[ $# -ge 2 && -z "$config_dir" ]] || die 'Use --config-dir once, with a directory.'
      config_dir="$2"; shift 2 ;;
    -h|--help)
      note 'Usage: ./install.sh [--config-dir LOCAL_DIR] [--check]'
      note 'Apply the selected Base setup, or report drift without changing it.'
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
  [[ "$line" =~ ^(finder|keyboard|trackpad|dock)=(true|false)$ ]] || die 'preferences.conf accepts only finder/keyboard/trackpad/dock=true|false.'
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
note 'Base workstation setup'
note "Packages: $brewfile"
note "Preferences: $preferences"
note 'Homebrew may change dependencies while installing missing packages; no broad upgrade or cleanup is requested.'
note '[1/4] Homebrew packages'
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
    note 'Installing Homebrew using its official HTTPS installer; it may request administrator approval.'
    clean_run /bin/bash -p "$scratch/homebrew.sh"
    [[ -x "$brew_bin" ]] || die 'Homebrew remains unavailable.'
  fi
fi
# Known baseline app paths work offline, including externally installed copies.
external_app() {
  local name="$1" app='' metadata='' index=0 artifact='' found=false
  case "$name" in raycast) app=Raycast.app ;; zed) app=Zed.app ;; esac
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
      clean_run /usr/bin/env HOMEBREW_BUNDLE_CASK_SKIP="$skip_casks" \
        "$brew_bin" bundle install --file="$scratch/Brewfile" --no-upgrade
    ) || die 'Package installation stopped. Existing app conflicts are preserved; do not force adoption.'
  fi
fi
note '[2/4] macOS preferences'
preference_failed() {
  preference_failures+=("$1 $2: $3")
  printf 'Warning: preference not applied: %s %s (%s). Continuing setup.\n' "$1" "$2" "$3" >&2
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
  if [[ "$current" != "$desired" ]]; then
    if [[ "$check" == true ]]; then
      pending "Preference: $domain $key -> $desired"
    else
      note "Set preference: $domain $key -> $desired"
      if /usr/bin/defaults write "$domain" "$key" "-$kind" "$write_value"; then
        if current="$(/usr/bin/defaults read "$domain" "$key")"; then
          if [[ "$current" != "$desired" ]]; then
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
# Only these two fixed shortcuts are managed; dict-add preserves other hotkeys.
hotkey_matches() {
  local hotkey="$1" snapshot enabled parameters kind
  snapshot="$(/usr/bin/defaults export com.apple.symbolichotkeys - 2>/dev/null)" || return 1
  enabled="$(printf '%s' "$snapshot" | /usr/bin/plutil -extract "AppleSymbolicHotKeys.$hotkey.enabled" raw -o - - 2>/dev/null)" || return 1
  if [[ "$hotkey" == 64 ]]; then [[ "$enabled" == false ]]; return; fi
  [[ "$enabled" == true ]] || return 1
  parameters="$(printf '%s' "$snapshot" | /usr/bin/plutil -extract AppleSymbolicHotKeys.61.value.parameters json -o - - 2>/dev/null)" || return 1
  kind="$(printf '%s' "$snapshot" | /usr/bin/plutil -extract AppleSymbolicHotKeys.61.value.type raw -o - - 2>/dev/null)" || return 1
  [[ "$parameters" == '[32,49,1048576]' && "$kind" == standard ]]
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
    elif ! hotkey_matches "$hotkey"; then
      preference_failed com.apple.symbolichotkeys "$hotkey" 'shortcut did not persist'
    fi
  done
}
if [[ "$finder" == true ]]; then
  preference NSGlobalDomain AppleShowAllExtensions bool 1
  preference com.apple.finder ShowPathbar bool 1
  preference com.apple.finder ShowStatusBar bool 1
  preference com.apple.finder FXPreferredViewStyle string clmv
  preference com.apple.finder FXDefaultSearchScope string SCcf
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
fi
note '[3/4] Shell configuration'
if [[ ! -d "$ohmyzsh_dir" ]]; then
  if [[ "$check" == true ]]; then
    pending 'Oh My Zsh is not installed.'
  else
    [[ -n "$scratch" ]] || scratch="$(/usr/bin/mktemp -d /private/tmp/macsetup-base.XXXXXX)"
    clean_run /usr/bin/git clone --depth 1 --branch master -- \
      https://github.com/ohmyzsh/ohmyzsh.git "$scratch/oh-my-zsh" \
      || die 'Oh My Zsh download failed; rerun Base.'
    [[ -f "$scratch/oh-my-zsh/oh-my-zsh.sh" && ! -L "$scratch/oh-my-zsh/oh-my-zsh.sh" ]] \
      || die 'Oh My Zsh download is incomplete.'
    safe_destination "$ohmyzsh_dir"
    [[ ! -e "$ohmyzsh_dir" ]] || die 'Oh My Zsh destination appeared during setup; preserved.'
    /bin/mv -n -- "$scratch/oh-my-zsh" "$ohmyzsh_dir"
    note 'Installed Oh My Zsh. Existing installations are never updated by Base.'
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
        note "Updated managed dotfile: $name"
      fi
    fi
    if [[ "$included" == false ]]; then
      if [[ "$check" == true ]]; then pending "Dotfile include: $name"
      else
        printf '\n%s\n' "$include" >> "$destination"
        note "Added dotfile include: $name"
      fi
    fi
  elif [[ -e "$destination" || -L "$destination" ]]; then
    note "Preserved existing dotfile: $name"
    if [[ -L "$destination" || ! -f "$destination" ]] || ! /usr/bin/cmp -s -- "$source_file" "$destination"; then
      pending "Unmanaged dotfile differs from the common default: $name"
      unresolved+=("$name: existing configuration preserved; select reviewed dotfile fragments with --config-dir to manage common settings.")
    fi
  elif [[ "$check" == true ]]; then
    pending "Default dotfile: $name"
  else
    /bin/cp -n -- "$source_file" "$destination"
    note "Created default dotfile: $name"
  fi
done
note '[4/4] App Store applications'
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
  if [[ "$check" == false && "$mas_pending" == true ]]; then
    printf '%s\n' "${mas_declarations[@]}" > "$scratch/AppStore.Brewfile"
    if (cd -- "$scratch" && clean_run "$brew_bin" bundle install --file="$scratch/AppStore.Brewfile" --no-upgrade); then
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
  [[ "$drift" == 0 ]] || { note 'Base has pending changes.'; exit 1; }
  note '[OK] Base is ready for the selected configuration.'
else
  if [[ ${#preference_failures[@]} -gt 0 || ${#unresolved[@]} -gt 0 ]]; then
    note 'Base setup incomplete: common setup finished with items needing attention:'
    if [[ ${#preference_failures[@]} -gt 0 ]]; then
      printf '  - %s\n' "${preference_failures[@]}"
      note 'Review the defaults errors above. Rerun as the same ordinary user after resolving the write or verification failure.'
    fi
    [[ ${#unresolved[@]} == 0 ]] || printf '  - %s\n' "${unresolved[@]}"
    note 'Use --check to report remaining changes. No preference permissions or management policies were overridden.'
    exit 3
  fi
  note '[OK] Base setup completed. Open a new shell; preferences may require an app restart or logout.'
  note 'Raycast permissions, application sign-in and App Store authentication remain interactive.'
fi
