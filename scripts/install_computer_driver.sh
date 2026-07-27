#!/usr/bin/env bash
# Install cua-driver sidecar for Kageha computer-use (macOS).
# Works around a packaging bug in the upstream installer that expects
# cua-cursor-theme inside the tarball.
set -euo pipefail

VERSION="${CUA_DRIVER_RS_VERSION:-0.12.6}"
BIN_DIR="${CUA_DRIVER_BIN_DIR:-$HOME/.local/bin}"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "error: computer driver install is macOS-only" >&2
  exit 1
fi

ARCH_TAG="darwin-universal"
URL="https://github.com/trycua/cua/releases/download/cua-driver-rs-v${VERSION}/cua-driver-rs-${VERSION}-${ARCH_TAG}.tar.gz"

echo "==> downloading $URL"
curl -fsSL -o "$TMP/cua.tgz" "$URL"
mkdir -p "$TMP/extract" "$BIN_DIR"
tar -xzf "$TMP/cua.tgz" -C "$TMP/extract"
SRC="$(find "$TMP/extract" -type f -name cua-driver | head -1)"
APP_SRC="$(find "$TMP/extract" -type d -name CuaDriver.app | head -1)"
if [[ -z "$SRC" || -z "$APP_SRC" ]]; then
  echo "error: tarball missing cua-driver or CuaDriver.app" >&2
  exit 1
fi

echo "==> installing CuaDriver.app → /Applications"
rm -rf /Applications/CuaDriver.app
cp -R "$APP_SRC" /Applications/CuaDriver.app

echo "==> installing CLI → $BIN_DIR/cua-driver"
cp "$SRC" "$BIN_DIR/cua-driver"
chmod +x "$BIN_DIR/cua-driver"

echo "==> starting daemon (LaunchServices identity for TCC)"
open -n -g -a CuaDriver --args serve || true
sleep 1

export PATH="$BIN_DIR:$PATH"
echo "==> version: $(cua-driver --version)"
cua-driver status || true
echo
echo "Next: grant permissions (dialogs attribute to CuaDriver.app):"
echo "  cua-driver permissions grant"
echo "  # or System Settings → Privacy & Security → Accessibility + Screen Recording"
echo
echo "Then enable the pack:"
echo "  export KAGEHA_TOOL_PACKS=computer"
echo "  uv sync --extra computer"
