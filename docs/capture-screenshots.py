#!/usr/bin/env python3
"""Capture tmux screenshots by launching views in a temporary window,
capturing the pane content with ANSI codes, and rendering to SVG via rich."""

import subprocess
import sys
import time
import os

from rich.console import Console
from rich.text import Text

DOCS = os.path.dirname(os.path.abspath(__file__))
SCREENSHOTS = os.path.join(DOCS, "screenshots")
SCRIPTS = os.path.expanduser("~/.tmux/scripts")
SESSION = "tmux"  # session to work in


def tmux(*args):
    result = subprocess.run(["tmux"] + list(args), capture_output=True, text=True)
    return result.stdout.strip()


def capture_pane(target, width=160, height=45):
    """Capture pane content with ANSI escape codes."""
    # Resize pane to consistent dimensions for screenshots
    tmux("resize-window", "-t", target, "-x", str(width), "-y", str(height))
    time.sleep(0.3)
    return tmux("capture-pane", "-e", "-p", "-t", target)


def render_svg(content, output_path, title="tmux", width=160):
    """Render ANSI content to SVG using rich."""
    console = Console(record=True, width=width, force_terminal=True)
    console.print(Text.from_ansi(content))
    svg = console.export_svg(title=title)
    with open(output_path, "w") as f:
        f.write(svg)
    print(f"  -> {os.path.basename(output_path)} ({len(svg):,} bytes)")


def capture_curses_view(cmd_args, output_name, title, wait=2.0):
    """Launch a curses app in a temp window, capture it, render to SVG."""
    temp_window = f"{SESSION}:99"

    # Create a temp window running the command
    full_cmd = " ".join(cmd_args)
    tmux("new-window", "-t", f"{SESSION}", "-n", "screenshot", full_cmd)

    # Wait for curses to render
    time.sleep(wait)

    # Find the window we just created
    windows = tmux("list-windows", "-t", SESSION, "-F", "#{window_index}:#{window_name}")
    target = None
    for line in windows.splitlines():
        if ":screenshot" in line:
            idx = line.split(":")[0]
            target = f"{SESSION}:{idx}"
            break

    if not target:
        print(f"  !! Could not find screenshot window for {output_name}")
        return

    content = capture_pane(target)

    # Kill the temp window
    tmux("kill-window", "-t", target)

    # Render
    svg_path = os.path.join(SCREENSHOTS, f"{output_name}.svg")
    render_svg(content, svg_path, title=title)


def capture_status_bar():
    """Capture a regular pane that shows the status bar context."""
    print("Capturing status bar...")
    # Capture a pane in the tmux session - status bar is visible in the terminal
    # but not in capture-pane. Instead, we'll capture the pane content
    # and add a status-bar representation.
    content = tmux("capture-pane", "-e", "-p", "-t", f"{SESSION}:0")

    # Also grab the status bar values
    status_left = tmux("display-message", "-t", SESSION, "-p",
                       "#{status-left}")
    status_right = tmux("display-message", "-t", SESSION, "-p",
                        "#{E:status-right}")
    current_win = tmux("display-message", "-t", SESSION, "-p",
                       "#I:#W")

    # Build a synthetic status bar line
    session_name = tmux("display-message", "-t", SESSION, "-p", "#S")
    ai_status = tmux("display-message", "-t", SESSION, "-p", "#{@ai_status_line}")

    # Take the last ~5 lines of pane content + a status bar
    lines = content.split("\n")
    # Keep last portion to show context
    visible = "\n".join(lines[-8:]) if len(lines) > 8 else content

    # Build a colored status bar
    bar = f"\033[1;48;2;38;38;38m\033[38;2;75;175;255m {session_name} \033[0m"
    bar += f"\033[48;2;38;38;38m{'':>40}\033[0m"
    bar += f"\033[1;48;2;51;51;255;38;2;255;255;255m {current_win} \033[0m"
    if ai_status:
        bar += f"\033[48;2;38;38;38m{'':>10}\033[0m"
        bar += f"\033[1;48;2;51;51;255;38;2;255;255;255m {ai_status} \033[0m"
    bar += f"\033[48;2;38;38;38m{'':>20}\033[0m"

    full = visible + "\n" + bar

    svg_path = os.path.join(SCREENSHOTS, "status-bar.svg")
    render_svg(full, svg_path, title="tmux — status bar", width=120)


def main():
    os.makedirs(SCREENSHOTS, exist_ok=True)

    wm = os.path.join(SCRIPTS, "window-manager")

    # 1. Status bar
    capture_status_bar()

    # 2. Window manager - tree view (Option+E)
    print("Capturing window manager tree view...")
    capture_curses_view([wm], "window-manager-tree", "Window Manager — Tree View")

    # 3. Window manager - session-focused (Option+W)
    print("Capturing window manager session view...")
    capture_curses_view([wm, "--session"], "window-manager-session",
                        "Window Manager — Session View")

    # 4. Window manager - sessions list (Option+S)
    print("Capturing window manager sessions list...")
    capture_curses_view([wm, "--sessions"], "window-manager-sessions",
                        "Window Manager — Sessions List")

    # 5. Window manager - robots view (Option+R)
    print("Capturing robots view...")
    capture_curses_view([wm, "--robots"], "robots-view",
                        "Window Manager — Robots View")

    # 6. Choose-tree with AI status
    print("Capturing choose-tree...")
    # choose-tree is a tmux built-in, need to trigger it via keys
    # We'll capture an existing AI pane instead as a representative shot
    # Find an AI window
    windows = tmux("list-windows", "-a", "-F", "#{session_name}:#{window_index} #{window_name}")
    ai_target = None
    for line in windows.splitlines():
        if "(claude)" in line or "(codex)" in line:
            ai_target = line.split()[0]
            break

    if ai_target:
        print(f"  Capturing AI agent window: {ai_target}")
        content = capture_pane(ai_target)
        svg_path = os.path.join(SCREENSHOTS, "agent-running.svg")
        render_svg(content, svg_path, title=f"AI Agent — {ai_target}")

    print("\nDone! SVGs saved to docs/screenshots/")
    print("\nScreenshots that need manual capture (interactive fzf popups):")
    print("  - session-picker.png   (Option+A → session list)")
    print("  - agent-picker.png     (Option+A → agent selection)")
    print("  - task-picker.png      (Option+A → todo selection)")
    print("  - merge-prompt.png     (after agent completes)")
    print("  - choose-tree-ai.png   (Prefix+w)")
    print("  - summarizer.png       (Option+Q)")
    print("  - summarizer-qa.png    (Option+Q → follow-up)")
    print("  - session-reorder.png  (Prefix+S)")
    print("  - file-explorer.png    (Prefix+t)")


if __name__ == "__main__":
    main()
