#!/usr/bin/env bash
# Posture Guardian v3.0 — Application Launcher
# Reference: docs/TROUBLESHOOTING_QT_COCOA.md

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Detect Python interpreter
if [ -f "$SCRIPT_DIR/.venv/bin/python" ]; then
    PYTHON_EXEC="$SCRIPT_DIR/.venv/bin/python"
elif [ -f "$SCRIPT_DIR/.venv312/bin/python" ]; then
    PYTHON_EXEC="$SCRIPT_DIR/.venv312/bin/python"
elif command -v python3 >/dev/null 2>&1; then
    PYTHON_EXEC="python3"
else
    PYTHON_EXEC="python"
fi

# Fix macOS hidden-flag issue on PySide6 .dylib files (UF_HIDDEN)
if [ "$(uname -s)" = "Darwin" ]; then
    PYSIDE_DIR="$("$PYTHON_EXEC" -c "import PySide6, os; print(os.path.dirname(PySide6.__file__))" 2>/dev/null || true)"
    if [ -n "$PYSIDE_DIR" ] && [ -d "$PYSIDE_DIR" ]; then
        chflags -R nohidden "$PYSIDE_DIR" 2>/dev/null || true
        export QT_PLUGIN_PATH="$PYSIDE_DIR/Qt/plugins"
        export QT_QPA_PLATFORM_PLUGIN_PATH="$PYSIDE_DIR/Qt/plugins/platforms"
    fi
fi

export PYTHONPATH="$SCRIPT_DIR/src:$PYTHONPATH"
exec "$PYTHON_EXEC" "$SCRIPT_DIR/src/main.py" "$@"
