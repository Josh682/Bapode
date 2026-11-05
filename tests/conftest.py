"""Pytest configuration and global environment fixtures for Posture Guardian."""

from __future__ import annotations

import os

# Ensure offscreen Qt platform and writable matplotlib cache for test execution
os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ.setdefault("MPLCONFIGDIR", "/tmp/matplotlib")

from ui.qt import _fix_pyside6_macos_environment

_fix_pyside6_macos_environment()
