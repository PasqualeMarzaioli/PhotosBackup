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
pip3 install -r "$SCRIPT_DIR/requirements.txt" --quiet
echo "-> Installing browser for Playwright..."
playwright install chromium
echo "  Dependencies installed."

# 2. Create tokens directory (with restricted permissions)
mkdir -p "$SCRIPT_DIR/.tokens"
chmod 700 "$SCRIPT_DIR/.tokens"
mkdir -p "$SCRIPT_DIR/.tmp_download"

# 3. Install/Update LaunchAgent
echo ""
echo "-> Configuring and Installing macOS LaunchAgent..."

# Generate the real plist from the example by replacing the path placeholder
sed "s|__PROJECT_PATH__|$SCRIPT_DIR|g" "$SCRIPT_DIR/com.pasquale.photosbackup.plist.example" > "$SCRIPT_DIR/$PLIST_NAME"

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
