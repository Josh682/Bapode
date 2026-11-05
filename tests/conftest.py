"""Pytest configuration and global environment fixtures for Posture Guardian."""

import os
import shutil
from pathlib import Path

# Ensure offscreen Qt platform and writable matplotlib cache for test execution
os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ.setdefault("MPLCONFIGDIR", "/tmp/matplotlib")

# Qt QFileSystemEngine skips plugin directories if any path component
# starts with '.' (such as '.venv').
# Workaround: copy platform plugins to /tmp/qt_plugins/platforms if needed.
try:
    import PySide6

    pyside_platforms = Path(PySide6.__file__).parent / "Qt" / "plugins" / "platforms"
    if pyside_platforms.exists():
        tmp_platforms = Path("/tmp/qt_plugins/platforms")
        tmp_platforms.mkdir(parents=True, exist_ok=True)
        for item in pyside_platforms.glob("*.dylib"):
            target = tmp_platforms / item.name
            if not target.exists() or target.stat().st_size != item.stat().st_size:
                shutil.copy2(item, target)
        os.environ["QT_QPA_PLATFORM_PLUGIN_PATH"] = str(tmp_platforms)
except Exception:
    pass
