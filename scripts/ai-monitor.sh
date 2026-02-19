#!/usr/bin/env bash
# ai-monitor.sh — Background daemon that tags AI windows as idle/active
# by comparing pane content snapshots. If the content changed since last
# poll, the window is active. If unchanged for IDLE_POLLS consecutive
# polls, it's idle. Sets @ai_status on each AI window.

# Kill any other running instance
PIDFILE="/tmp/tmux-ai-monitor.pid"
if [[ -f "$PIDFILE" ]]; then
    old_pid=$(cat "$PIDFILE")
    if kill -0 "$old_pid" 2>/dev/null; then
        kill "$old_pid" 2>/dev/null
        sleep 0.2
    fi
fi
echo $$ > "$PIDFILE"

STATEDIR="/tmp/tmux-ai-monitor-state"
rm -rf "$STATEDIR"
mkdir -p "$STATEDIR"

# Only clean up if we're still the active instance (prevents race
# where the old instance's trap deletes the new instance's state dir).
cleanup() {
    if [[ -f "$PIDFILE" ]] && [[ "$(cat "$PIDFILE")" == "$$" ]]; then
        rm -f "$PIDFILE"
        rm -rf "$STATEDIR"
    fi
}
trap 'cleanup; exit 0' TERM
trap 'cleanup' EXIT

POLL_INTERVAL="${TMUX_AI_POLL_SECONDS:-3}"
IDLE_POLLS="${TMUX_AI_IDLE_POLLS:-2}"
AUTONOMOUS_CMD_REGEX='(^|[[:space:]])([^[:space:]]*/)?(codex[[:space:]]+exec|claude([[:space:]]+exec|[[:space:]]+(-p|--print|--prompt)))([[:space:]]|$)'

is_ai_window() {
    local window_name="$1"
    [[ "$window_name" == *"(claude)"* || "$window_name" == *"(codex)"* ]]
}

pane_has_autonomous_agent() {
    local pane_tty="$1"
    [[ -n "$pane_tty" ]] || return 1
    ps -o command= -t "$pane_tty" 2>/dev/null | grep -Eiq "$AUTONOMOUS_CMD_REGEX"
}

while true; do
    tmux has-session 2>/dev/null || { sleep 5; continue; }

    while IFS=$'\t' read -r target name pane pane_tty; do
        if is_ai_window "$name"; then
            # Use pane id (minus the %) as a safe filename
            pane_key="${pane#%}"

            snapshot=$(tmux capture-pane -t "$pane" -p 2>/dev/null)
            hash=$(printf '%s' "$snapshot" | md5 -q 2>/dev/null || printf '%s' "$snapshot" | md5sum | cut -d' ' -f1)

            prev=""
            [[ -f "$STATEDIR/$pane_key.hash" ]] && prev=$(cat "$STATEDIR/$pane_key.hash")

            count=0
            [[ -f "$STATEDIR/$pane_key.count" ]] && count=$(cat "$STATEDIR/$pane_key.count")

            if [[ "$prev" == "$hash" ]]; then
                count=$((count + 1))
            else
                count=0
            fi

            echo "$hash" > "$STATEDIR/$pane_key.hash"

            if pane_has_autonomous_agent "$pane_tty"; then
                # Autonomous one-shot runs can stay quiet for long stretches.
                # Keep them active while the command is still present on the pane TTY.
                count=0
                echo "$count" > "$STATEDIR/$pane_key.count"
                tmux set-option -wq -t "$target" @ai_status "active" 2>/dev/null
            elif [[ $count -ge $IDLE_POLLS ]]; then
                echo "$count" > "$STATEDIR/$pane_key.count"
                tmux set-option -wq -t "$target" @ai_status "idle" 2>/dev/null
            else
                echo "$count" > "$STATEDIR/$pane_key.count"
                tmux set-option -wq -t "$target" @ai_status "active" 2>/dev/null
            fi
        fi
    done < <(tmux list-windows -a -F "#{session_name}:#{window_index}	#{window_name}	#{pane_id}	#{pane_tty}" 2>/dev/null)

    # Build status-line summary (agent counts) for the status bar
    active=0
    idle=0
    while IFS=$'\t' read -r _t n s; do
        if is_ai_window "$n"; then
            if [[ "$s" == "active" ]]; then
                ((active++))
            else
                ((idle++))
            fi
        fi
    done < <(tmux list-windows -a -F "#{session_name}:#{window_index}	#{window_name}	#{@ai_status}" 2>/dev/null)
    summary=""
    if (( idle > 0 && active > 0 )); then
        summary="$idle 🤖 | $active ⚡"
    elif (( idle > 0 )); then
        summary="$idle 🤖"
    elif (( active > 0 )); then
        summary="$active ⚡"
    fi
    tmux set -gq @ai_status_line "$summary"

    sleep "$POLL_INTERVAL"
done
