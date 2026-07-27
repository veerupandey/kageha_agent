#!/usr/bin/env bash
set -euo pipefail

DISPLAY="${DISPLAY:-:99}"
CDP_PORT="${CHROME_REMOTE_DEBUGGING_PORT:-9222}"
NOVNC_PORT="${NOVNC_PORT:-6080}"
NOVNC_PASSWORD="${NOVNC_PASSWORD:-kageha}"

# Xvfb
rm -f "/tmp/.X${DISPLAY#:}-lock" 2>/dev/null || true
Xvfb "$DISPLAY" -screen 0 1280x800x24 -ac +extension RANDR >/tmp/xvfb.log 2>&1 &
sleep 0.5

# VNC + noVNC
mkdir -p /tmp/vnc
x11vnc -storepasswd "$NOVNC_PASSWORD" /tmp/vnc/passwd >/dev/null 2>&1
x11vnc -display "$DISPLAY" -rfbauth /tmp/vnc/passwd -forever -shared -rfbport 5900 \
  -localhost >/tmp/x11vnc.log 2>&1 &
websockify --web=/usr/share/novnc/ "$NOVNC_PORT" localhost:5900 >/tmp/novnc.log 2>&1 &

# Chromium with CDP (no-sandbox required in many containers)
CHROME_BIN="$(command -v chromium || command -v chromium-browser || command -v google-chrome)"
"$CHROME_BIN" \
  --display="$DISPLAY" \
  --remote-debugging-address=0.0.0.0 \
  --remote-debugging-port="$CDP_PORT" \
  --no-first-run \
  --no-default-browser-check \
  --disable-gpu \
  --disable-dev-shm-usage \
  --no-sandbox \
  --user-data-dir=/tmp/chrome-profile \
  about:blank >/tmp/chrome.log 2>&1 &

# Keep container alive on chrome exit so logs are inspectable briefly
wait -n || true
tail -F /tmp/chrome.log /tmp/novnc.log
