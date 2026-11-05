"""Qt abstraction and macOS environment self-healing.

Reference: docs/TROUBLESHOOTING_QT_COCOA.md
Fixes:
- macOS UF_HIDDEN flag on PySide6 .dylib platform plugins.
- Automatic discovery of QT_PLUGIN_PATH and QT_QPA_PLATFORM_PLUGIN_PATH.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def _fix_pyside6_macos_environment() -> None:
    """Fix macOS UF_HIDDEN flag on PySide6 .dylib files.

    pip install PySide6 on macOS marks .dylib plugin files with the hidden flag (UF_HIDDEN).
    Qt's QFactoryLoader uses QDir with standard filters which skip hidden files,
    so the cocoa platform plugin is invisible despite being physically present.
    We clear the flag recursively and export the plugin directories.
    """
    if sys.platform != "darwin":
        return

    try:
        import PySide6

        pyside_dir = Path(PySide6.__file__).parent
        if pyside_dir.is_dir():
            subprocess.run(
                ["chflags", "-R", "nohidden", str(pyside_dir)],
                capture_output=True,
                timeout=5,
            )
            plugins_dir = pyside_dir / "Qt" / "plugins"
            if plugins_dir.is_dir():
                os.environ.setdefault("QT_PLUGIN_PATH", str(plugins_dir))
                os.environ.setdefault("QT_QPA_PLATFORM_PLUGIN_PATH", str(plugins_dir / "platforms"))
    except Exception:
        pass


_fix_pyside6_macos_environment()

from PySide6 import QtCore, QtGui, QtWidgets  # noqa: E402

__all__ = [
    "QtCore",
    "QtGui",
    "QtWidgets",
    "_fix_pyside6_macos_environment",
]
