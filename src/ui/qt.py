"""Qt abstraction for Bapode Windows Edition.

Reference: docs/DESIGN_SYSTEM.md & docs/ARCHITECTURE.md
Provides PySide6 QtCore, QtGui, and QtWidgets bindings.
"""

from __future__ import annotations

from PySide6 import QtCore, QtGui, QtWidgets


def _fix_pyside6_macos_environment() -> None:
    """Safe no-op stub for backward compatibility."""
    pass


__all__ = [
    "QtCore",
    "QtGui",
    "QtWidgets",
    "_fix_pyside6_macos_environment",
]
