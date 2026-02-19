#!/bin/bash

# Prompt for window name + agent inside tmux command mode.
# Agent defaults to claude; codex is available as an alternate.
tmux command-prompt -p "name:","agent:" -I "","claude" \
  "new-window -c '#{pane_current_path}' -n '%1 (%2)' 'case %2 in claude) claude --dangerously-skip-permissions;; codex) codex --full-auto;; *) %2;; esac'"
