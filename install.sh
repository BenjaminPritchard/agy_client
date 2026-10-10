#!/usr/bin/env bash
set -e

# ==============================================================================
# agy-client: Installer & Setup Script
# ==============================================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BIN_DEST="$HOME/.local/bin"
TARGET_LINK="$BIN_DEST/agy_client"

echo "=================================================="
echo "Installing agy-client: Antigravity Pipe Plugin"
echo "=================================================="

# 1. Check Python 3
if ! command -v python3 >/dev/null 2>&1; then
    echo "❌ Error: python3 is required but not found."
    exit 1
fi
echo "✔ Python 3 found: $(python3 --version)"

# 2. Check agy CLI
AGY_BIN="${ANTIGRAVITY_AGENTAPI_EXE:-$HOME/.local/bin/agy}"
if [ ! -x "$AGY_BIN" ] && ! command -v agy >/dev/null 2>&1; then
    echo "⚠️ Warning: 'agy' CLI not found at default location ($AGY_BIN)."
    echo "  Ensure agy is installed on your PATH."
else
    echo "✔ Antigravity CLI found: $AGY_BIN"
fi

# 3. Ensure executable permissions
chmod +x "$SCRIPT_DIR/bin/"*
chmod +x "$SCRIPT_DIR/scripts/session_sync.py"
echo "✔ Set executable permissions on scripts."

# 4. Link and enable plugin in Antigravity
PLUGIN_DEST="$HOME/.gemini/config/plugins/agy-client"
mkdir -p "$(dirname "$PLUGIN_DEST")"
rm -rf "$PLUGIN_DEST"
ln -s "$SCRIPT_DIR" "$PLUGIN_DEST"
echo "✔ Symlinked plugin -> $PLUGIN_DEST"

if command -v agy >/dev/null 2>&1 || [ -x "$AGY_BIN" ]; then
    CLI="${AGY_BIN:-agy}"
    echo "Validating plugin..."
    "$CLI" plugin validate "$PLUGIN_DEST"
    echo "Enabling plugin in Antigravity config..."
    "$CLI" plugin enable agy-client
fi

# 5. Create symlinks in ~/.local/bin
mkdir -p "$BIN_DEST"
for CMD_NAME in "agy_client" "agy-screenshot" "agy_screenshot"; do
    SRC="$SCRIPT_DIR/bin/$CMD_NAME"
    if [ ! -f "$SRC" ] && [ "$CMD_NAME" = "agy_screenshot" ]; then
        SRC="$SCRIPT_DIR/bin/agy-screenshot"
    fi
    DEST="$BIN_DEST/$CMD_NAME"
    rm -f "$DEST"
    ln -s "$SRC" "$DEST"
    echo "✔ Symlinked $CMD_NAME -> $DEST"
done

# 6. Check PATH
case ":$PATH:" in
    *":$BIN_DEST:"*) ;;
    *)
        echo "⚠️ Note: $BIN_DEST is not in your current PATH."
        echo "  Add it to your shell config (e.g. ~/.zshrc):"
        echo "    export PATH=\"\$HOME/.local/bin:\$PATH\""
        ;;
esac

# 7. Seed current session if available
if [ -n "$ANTIGRAVITY_CONVERSATION_ID" ]; then
    echo "Active Antigravity session detected ($ANTIGRAVITY_CONVERSATION_ID)."
    echo "{}" | "$SCRIPT_DIR/scripts/session_sync.py" >/dev/null 2>&1 || true
fi

echo ""
echo "=================================================="
echo "🎉 Installation Complete!"
echo "=================================================="
echo "You can now pipe context from any terminal into your running Antigravity session:"
echo ""
echo "  ls | agy_client"
echo "  git diff | agy_client \"Please review these changes\""
echo "  pytest 2>&1 | agy_client \"Why did this test fail?\""
echo "  agy_client --list"
echo "=================================================="
