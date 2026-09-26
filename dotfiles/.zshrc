# Base shell conveniences. Existing shell files are never replaced by defaults.
# BEGIN BASE MANAGED OH MY ZSH
if (( ! $+functions[omz] )); then
  export ZSH="$HOME/.oh-my-zsh"
  ZSH_THEME="${ZSH_THEME-robbyrussell}"
  (( ${+plugins} )) || plugins=(git)
  source "$ZSH/oh-my-zsh.sh"
fi
typeset -g _PKGMACSETUP_OMZ_LOADED=1
# END BASE MANAGED OH MY ZSH

alias ta='tmux attach -t'
alias tl='tmux ls'
alias tn='tmux new -s'
for base_prefix in /opt/homebrew /usr/local; do
  [[ ! -r "$base_prefix/share/zsh-autosuggestions/zsh-autosuggestions.zsh" ]] || source "$base_prefix/share/zsh-autosuggestions/zsh-autosuggestions.zsh"
  [[ ! -r "$base_prefix/share/zsh-syntax-highlighting/zsh-syntax-highlighting.zsh" ]] || source "$base_prefix/share/zsh-syntax-highlighting/zsh-syntax-highlighting.zsh"
done
unset base_prefix
