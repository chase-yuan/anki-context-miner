#!/bin/bash
set -e

LABEL="com.user.anki_video_miner"
PLIST_PATH="$HOME/Library/LaunchAgents/${LABEL}.plist"
REPO_DIR="$(cd "$(dirname "$0")" && pwd)"
PYTHON_BIN="$(which python3)"
LOG_DIR="$HOME/Library/Logs"
LOG_FILE="$LOG_DIR/anki_video_miner.log"

ACTION="${1:-status}"

case "$ACTION" in
  install)
    echo "==> Configuring macOS launchd service for Anki Video Miner..."
    mkdir -p "$HOME/Library/LaunchAgents" "$LOG_DIR"

    cat <<EOF > "$PLIST_PATH"
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>${LABEL}</string>
    <key>ProgramArguments</key>
    <array>
        <string>${PYTHON_BIN}</string>
        <string>${REPO_DIR}/tg_bot.py</string>
    </array>
    <key>WorkingDirectory</key>
    <string>${REPO_DIR}</string>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>${LOG_FILE}</string>
    <key>StandardErrorPath</key>
    <string>${LOG_FILE}</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>/opt/homebrew/bin:/opt/homebrew/sbin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin</string>
    </dict>
</dict>
</plist>
EOF

    echo "==> Plist written to: $PLIST_PATH"
    # Unload if previously running
    launchctl unload "$PLIST_PATH" 2>/dev/null || true
    launchctl load -w "$PLIST_PATH"
    echo "==> Service installed and started successfully!"
    echo "    Check logs via: tail -f \"$LOG_FILE\""
    ;;

  uninstall)
    echo "==> Stopping and uninstalling service..."
    if [ -f "$PLIST_PATH" ]; then
        launchctl unload "$PLIST_PATH" 2>/dev/null || true
        rm -f "$PLIST_PATH"
        echo "==> Service uninstalled."
    else
        echo "==> Service was not installed."
    fi
    ;;

  restart)
    echo "==> Restarting service..."
    UID_NUM="$(id -u)"
    launchctl kickstart -k "gui/${UID_NUM}/${LABEL}" 2>/dev/null || {
        launchctl unload "$PLIST_PATH" 2>/dev/null || true
        launchctl load -w "$PLIST_PATH"
    }
    echo "==> Service restarted."
    ;;

  status)
    echo "==> Checking service status..."
    if [ -f "$PLIST_PATH" ]; then
        echo "Plist: $PLIST_PATH (Installed)"
    else
        echo "Plist: Not installed (Run: ./install_mac_service.sh install)"
        exit 0
    fi
    UID_NUM="$(id -u)"
    PID="$(launchctl list | grep "${LABEL}" | awk '{print $1}')"
    if [ -n "$PID" ] && [ "$PID" != "-" ]; then
        echo "Status: Running (PID: $PID)"
    else
        echo "Status: Stopped or waiting"
    fi
    if [ -f "$LOG_FILE" ]; then
        echo "--- Last 5 log entries ($LOG_FILE) ---"
        tail -n 5 "$LOG_FILE"
    fi
    ;;

  *)
    echo "Usage: $0 {install|uninstall|restart|status}"
    exit 1
    ;;
esac
