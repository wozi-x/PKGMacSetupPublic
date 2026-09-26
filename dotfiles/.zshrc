# Base shell conveniences. Existing shell files are never replaced by defaults.
alias ta='tmux attach -t'
alias tl='tmux ls'
alias tn='tmux new -s'
for base_prefix in /opt/homebrew /usr/local; do
  [[ ! -r "$base_prefix/share/zsh-autosuggestions/zsh-autosuggestions.zsh" ]] || source "$base_prefix/share/zsh-autosuggestions/zsh-autosuggestions.zsh"
  [[ ! -r "$base_prefix/share/zsh-syntax-highlighting/zsh-syntax-highlighting.zsh" ]] || source "$base_prefix/share/zsh-syntax-highlighting/zsh-syntax-highlighting.zsh"
done
unset base_prefix
