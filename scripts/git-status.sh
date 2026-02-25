#!/usr/bin/env bash
# Git status indicator for tmux status bar.
# Called via #(~/.tmux/scripts/git-status.sh #{pane_current_path})
# Outputs: 🚧 (has work) | ✅ (clean) | nothing (main/master or non-repo)

dir="${1:-.}"
cd "$dir" 2>/dev/null || exit 0

# Must be inside a git repo
git rev-parse --git-dir &>/dev/null || exit 0

# Silent on detached HEAD
branch=$(git symbolic-ref --short HEAD 2>/dev/null) || exit 0

# Silent on main/master
[[ "$branch" == "main" || "$branch" == "master" ]] && exit 0

# Check for commits ahead of main
ahead=0
if git rev-parse --verify main &>/dev/null; then
  ahead=$(git rev-list main..HEAD --count 2>/dev/null || echo 0)
fi

# Check for dirty working tree (modified/staged/untracked)
dirty=$(git status --porcelain 2>/dev/null)

if [[ $ahead -gt 0 || -n "$dirty" ]]; then
  printf '🚧'
else
  printf '✅'
fi
