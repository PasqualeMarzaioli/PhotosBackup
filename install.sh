#!/bin/bash
# --- install.sh ---------------------------------------------------------------
# Installs Python dependencies and activates the macOS LaunchAgent.
# Author: Pasquale Marzaioli
# Run with:  bash install.sh
# ------------------------------------------------------------------------------

set -euo pipefail

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
if [ ! -x "$SCRIPT_DIR/venv/bin/python" ]; then
    PYTHON_BIN="${PYTHON_BIN:-$(command -v python3.11 || command -v python3)}"
    "$PYTHON_BIN" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else "Python 3.10 or newer is required")'
    "$PYTHON_BIN" -m venv "$SCRIPT_DIR/venv"
fi
PYTHON_BIN="$SCRIPT_DIR/venv/bin/python"
"$PYTHON_BIN" -m pip install -r "$SCRIPT_DIR/requirements.txt"

if [ ! -d "/Applications/Google Chrome.app" ] && [ ! -d "$HOME/Applications/Google Chrome.app" ]; then
    echo "Google Chrome is required. Install Chrome before activating the backup."
    exit 1
fi
echo "  Dependencies installed in the project virtual environment."

# 2. Create tokens directory (with restricted permissions)
mkdir -p "$SCRIPT_DIR/.tokens"
chmod 700 "$SCRIPT_DIR/.tokens"
mkdir -p "$SCRIPT_DIR/.tmp_download"

# 3. Install/Update LaunchAgent
echo ""
echo "-> Configuring and Installing macOS LaunchAgent..."

# Serialize the plist so spaces and XML characters in project paths stay valid.
"$PYTHON_BIN" - "$SCRIPT_DIR" "$PLIST_NAME" <<'PY'
# Fill the LaunchAgent template using the project environment.
# Author: Pasquale Marzaioli
import pathlib
import plistlib
import sys

project = pathlib.Path(sys.argv[1])
template = plistlib.loads((project / (sys.argv[2] + '.example')).read_bytes())
for key in ('WorkingDirectory', 'StandardOutPath', 'StandardErrorPath'):
    template[key] = template[key].replace('__PROJECT_PATH__', str(project))
template['ProgramArguments'] = [value.replace('__PROJECT_PATH__', str(project)) for value in template['ProgramArguments']]
template['EnvironmentVariables'] = {'PYTHON_BIN': str(project / 'venv/bin/python')}
rendered = plistlib.dumps(template).decode('utf-8')
rendered = rendered.replace('?>', '?>\n<!-- Runs the monthly backup checker with the project environment. Author: Pasquale Marzaioli -->', 1)
(project / sys.argv[2]).write_text(rendered, encoding='utf-8')
PY

mkdir -p "$LAUNCH_AGENTS_DIR"
cp "$SCRIPT_DIR/$PLIST_NAME" "$LAUNCH_AGENTS_DIR/$PLIST_NAME"

# Reload if already present
launchctl unload "$LAUNCH_AGENTS_DIR/$PLIST_NAME" 2>/dev/null || true
launchctl load "$LAUNCH_AGENTS_DIR/$PLIST_NAME"

echo "  LaunchAgent installed and loaded: $PLIST_NAME"
echo "  The checker will run daily at 09:00 and start the monthly backup only when needed."

echo ""
echo "=============================================="
echo "  Next step: configure the APIs"
echo "=============================================="
echo ""
echo "  1. Edit: $SCRIPT_DIR/config.py"
echo "     (If copying for the first time: cp config.py.example config.py)"
echo "     Insert MICROSOFT_CLIENT_ID and optional Telegram settings."
echo ""
echo "  2. Then run the authentication setup:"
echo "     cd '$SCRIPT_DIR'"
echo "     venv/bin/python setup_auth.py"
echo ""
echo "  3. To start a manual backup right now:"
echo "     ./backup.sh"
echo ""
echo "  Read README.md for full details."
echo ""
