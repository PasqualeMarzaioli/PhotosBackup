#!/bin/bash
# --- install.sh ---------------------------------------------------------------
# Installs Python dependencies and activates the macOS LaunchAgent.
# Run with:  bash install.sh
# ------------------------------------------------------------------------------

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLIST_NAME="com.pasquale.photosbackup.plist"
LAUNCH_AGENTS_DIR="$HOME/Library/LaunchAgents"

echo ""
echo "=============================================="
echo "  Photos Backup -> OneDrive Installation"
echo "=============================================="

# 1. Install Python dependencies
echo ""
echo "-> Installing Python dependencies..."
PYTHON_BIN=$(which python3.11 || which python3 || echo "python3")

if ! "$PYTHON_BIN" -m pip install -r "$SCRIPT_DIR/requirements.txt" --quiet; then
    echo "  System Python is externally managed. Retrying with --break-system-packages --user..."
    "$PYTHON_BIN" -m pip install -r "$SCRIPT_DIR/requirements.txt" --break-system-packages --user --quiet
fi

echo "-> Installing browser for Playwright..."
"$PYTHON_BIN" -m playwright install chromium
echo "  Dependencies installed."

# 2. Create tokens directory (with restricted permissions)
mkdir -p "$SCRIPT_DIR/.tokens"
chmod 700 "$SCRIPT_DIR/.tokens"
mkdir -p "$SCRIPT_DIR/.tmp_download"

# 3. Install/Update LaunchAgent
echo ""
echo "-> Configuring and Installing macOS LaunchAgent..."

# Find the Python path used (preferring Python 3.11 if available)
PYTHON_PATH=$(which python3.11 || which python3 || echo "/usr/bin/python3")
echo "  Using Python path for LaunchAgent: $PYTHON_PATH"

# Generate the real plist from the example by replacing path and python placeholders
sed -e "s|__PROJECT_PATH__|$SCRIPT_DIR|g" -e "s|__PYTHON_PATH__|$PYTHON_PATH|g" "$SCRIPT_DIR/com.pasquale.photosbackup.plist.example" > "$SCRIPT_DIR/$PLIST_NAME"

mkdir -p "$LAUNCH_AGENTS_DIR"
cp "$SCRIPT_DIR/$PLIST_NAME" "$LAUNCH_AGENTS_DIR/$PLIST_NAME"

# Reload if already present
launchctl unload "$LAUNCH_AGENTS_DIR/$PLIST_NAME" 2>/dev/null || true
launchctl load "$LAUNCH_AGENTS_DIR/$PLIST_NAME"

echo "  LaunchAgent installed and loaded: $PLIST_NAME"
echo "  The script will start automatically every 2nd of the month at 09:00."

echo ""
echo "=============================================="
echo "  Next step: configure the APIs"
echo "=============================================="
echo ""
echo "  1. Edit: $SCRIPT_DIR/config.py"
echo "     (If copying for the first time: cp config.py.example config.py)"
echo "     Insert GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET,"
echo "     MICROSOFT_CLIENT_ID, TELEGRAM_BOT_TOKEN, etc."
echo ""
echo "  2. Then run the authentication setup:"
echo "     cd '$SCRIPT_DIR'"
echo "     python3 setup_auth.py"
echo ""
echo "  3. To start a manual backup right now:"
echo "     python3 backup.py"
echo ""
echo "  Read README.md for full details."
echo ""
