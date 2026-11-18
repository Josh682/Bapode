"""Centralized UI Design Tokens, QPalette, and Global Stylesheet for Bapode.

Establishes a cohesive dark-neutral "Calm Ergonomic Instrument" visual system
for all PySide6 windows, dialogs, menus, and status components, eliminating
light/dark contrast mismatches across Windows and macOS.
"""

from __future__ import annotations

import sys

from ui.qt import QtCore, QtGui, QtWidgets


class ColorTokens:
    """Semantic color palette aligned with docs/DESIGN_SYSTEM.md."""

    # Layered neutral surfaces
    BG_CANVAS = "#0B0F17"
    BG_SURFACE = "#111827"
    BG_ELEVATED = "#161E2E"
    BG_INTERACTIVE = "#1E293B"
    BG_INTERACTIVE_HOVER = "#283548"
    BG_INTERACTIVE_PRESSED = "#334155"

    # Borders
    BORDER_SUBTLE = "#232F43"
    BORDER_STRONG = "#334155"
    BORDER_FOCUS = "#3B82F6"

    # Typography (>= 4.5:1 contrast against BG_CANVAS / BG_SURFACE / BG_ELEVATED)
    TEXT_PRIMARY = "#F1F5F9"
    TEXT_SECONDARY = "#94A3B8"
    TEXT_TERTIARY = "#A1B0C4"
    TEXT_ON_ACCENT = "#FFFFFF"

    # Semantic posture & system states
    STATUS_OK = "#10B981"
    STATUS_OK_HOVER = "#059669"
    STATUS_WARN = "#F59E0B"
    STATUS_ALERT = "#EF4444"
    STATUS_ALERT_HOVER = "#DC2626"
    STATUS_COOLDOWN = "#F97316"
    STATUS_INFO = "#3B82F6"
    STATUS_INFO_HOVER = "#2563EB"
    STATUS_CALIBRATION = "#8B5CF6"
    STATUS_PAUSED = "#6B7280"

    # Subtle status surface tints
    TINT_OK = "rgba(16, 185, 129, 0.14)"
    TINT_WARN = "rgba(245, 158, 11, 0.15)"
    TINT_ALERT = "rgba(239, 68, 68, 0.16)"
    TINT_INFO = "rgba(59, 130, 246, 0.15)"
    TINT_CALIBRATION = "rgba(139, 92, 246, 0.16)"
    TINT_PAUSED = "rgba(107, 114, 128, 0.16)"


_THEME_APPLIED_PROP = "_bapode_theme_applied"


def app_font_family() -> str:
    """Return platform-idiomatic sans-serif UI font family."""
    if sys.platform == "win32":
        return "Segoe UI"
    if sys.platform == "darwin":
        return "SF Pro Text"
    return "Inter"


def mono_font_family() -> str:
    """Return platform-idiomatic monospace font family for tabular telemetry."""
    if sys.platform == "win32":
        return "Cascadia Mono"
    if sys.platform == "darwin":
        return "SF Mono"
    return "JetBrains Mono"


def build_qpalette() -> QtGui.QPalette:
    """Construct a cohesive dark QPalette so native Qt widgets match design tokens."""
    palette = QtGui.QPalette()

    bg_canvas = QtGui.QColor(ColorTokens.BG_CANVAS)
    bg_surface = QtGui.QColor(ColorTokens.BG_SURFACE)
    bg_elevated = QtGui.QColor(ColorTokens.BG_ELEVATED)
    bg_interactive = QtGui.QColor(ColorTokens.BG_INTERACTIVE)
    text_primary = QtGui.QColor(ColorTokens.TEXT_PRIMARY)
    text_secondary = QtGui.QColor(ColorTokens.TEXT_SECONDARY)
    accent = QtGui.QColor(ColorTokens.STATUS_INFO)
    disabled_text = QtGui.QColor("#64748B")

    palette.setColor(QtGui.QPalette.ColorRole.Window, bg_canvas)
    palette.setColor(QtGui.QPalette.ColorRole.WindowText, text_primary)
    palette.setColor(QtGui.QPalette.ColorRole.Base, bg_surface)
    palette.setColor(QtGui.QPalette.ColorRole.AlternateBase, bg_elevated)
    palette.setColor(QtGui.QPalette.ColorRole.ToolTipBase, bg_elevated)
    palette.setColor(QtGui.QPalette.ColorRole.ToolTipText, text_primary)
    palette.setColor(QtGui.QPalette.ColorRole.Text, text_primary)
    palette.setColor(QtGui.QPalette.ColorRole.Button, bg_interactive)
    palette.setColor(QtGui.QPalette.ColorRole.ButtonText, text_primary)
    palette.setColor(QtGui.QPalette.ColorRole.BrightText, QtGui.QColor("#FFFFFF"))
    palette.setColor(QtGui.QPalette.ColorRole.Link, accent)
    palette.setColor(QtGui.QPalette.ColorRole.Highlight, accent)
    palette.setColor(QtGui.QPalette.ColorRole.HighlightedText, QtGui.QColor("#FFFFFF"))
    palette.setColor(QtGui.QPalette.ColorRole.PlaceholderText, text_secondary)

    palette.setColor(
        QtGui.QPalette.ColorGroup.Disabled,
        QtGui.QPalette.ColorRole.WindowText,
        disabled_text,
    )
    palette.setColor(
        QtGui.QPalette.ColorGroup.Disabled,
        QtGui.QPalette.ColorRole.Text,
        disabled_text,
    )
    palette.setColor(
        QtGui.QPalette.ColorGroup.Disabled,
        QtGui.QPalette.ColorRole.ButtonText,
        disabled_text,
    )
    return palette


def build_global_stylesheet() -> str:
    """Build unified QSS stylesheet covering windows, tabs, tables, menus, and controls."""
    c = ColorTokens
    font_ui = app_font_family()
    return f"""
        QMainWindow, QDialog {{
            background-color: {c.BG_CANVAS};
            color: {c.TEXT_PRIMARY};
            font-family: "{font_ui}", "Segoe UI", sans-serif;
            font-size: 13px;
        }}
        QWidget {{
            color: {c.TEXT_PRIMARY};
            font-family: "{font_ui}", "Segoe UI", sans-serif;
        }}
        QLabel {{
            background-color: transparent;
            color: {c.TEXT_PRIMARY};
        }}
        QLabel[role="dialogTitle"] {{
            font-size: 17px;
            font-weight: 700;
            color: {c.TEXT_PRIMARY};
        }}
        QLabel[role="mutedText"] {{
            font-size: 13px;
            color: {c.TEXT_SECONDARY};
        }}
        QLabel[role="metricTitle"] {{
            font-size: 12px;
            font-weight: 600;
            color: {c.TEXT_SECONDARY};
        }}
        QLabel[role="durationItem"] {{
            font-size: 13px;
            color: {c.TEXT_PRIMARY};
            padding: 2px 6px;
        }}
        QLabel[role="liveStatus"] {{
            font-size: 13px;
            font-weight: 600;
            color: {c.TEXT_PRIMARY};
            background-color: {c.BG_ELEVATED};
            border: 1px solid {c.BORDER_SUBTLE};
            border-radius: 8px;
            padding: 8px 12px;
        }}
        QFrame[role="metricCard"] {{
            background-color: {c.BG_ELEVATED};
            border: 1px solid {c.BORDER_SUBTLE};
            border-radius: 10px;
        }}
        QFrame[role="statusStrip"] {{
            background-color: {c.BG_ELEVATED};
            border: 1px solid {c.BORDER_SUBTLE};
            border-radius: 10px;
        }}
        QFrame[role="envBanner"] {{
            background-color: {c.TINT_WARN};
            border: 1px solid {c.STATUS_WARN};
            border-radius: 8px;
        }}
        QFrame[role="envBanner"][severity="alert"] {{
            background-color: {c.TINT_ALERT};
            border: 1px solid {c.STATUS_ALERT};
        }}
        QFrame[role="envBanner"][severity="calibration"] {{
            background-color: {c.TINT_CALIBRATION};
            border: 1px solid {c.STATUS_CALIBRATION};
        }}

        /* Menu Bar & Tray Context Menu */
        QMenuBar {{
            background-color: {c.BG_SURFACE};
            color: {c.TEXT_PRIMARY};
            border-bottom: 1px solid {c.BORDER_SUBTLE};
            padding: 2px 6px;
        }}
        QMenuBar::item {{
            background: transparent;
            padding: 5px 10px;
            border-radius: 4px;
        }}
        QMenuBar::item:selected {{
            background-color: {c.BG_INTERACTIVE};
        }}
        QMenu {{
            background-color: {c.BG_ELEVATED};
            color: {c.TEXT_PRIMARY};
            border: 1px solid {c.BORDER_STRONG};
            border-radius: 8px;
            padding: 6px 4px;
        }}
        QMenu::item {{
            padding: 6px 18px;
            border-radius: 5px;
        }}
        QMenu::item:selected {{
            background-color: {c.BG_INTERACTIVE_HOVER};
        }}
        QMenu::item:disabled {{
            color: {c.TEXT_SECONDARY};
        }}
        QMenu::separator {{
            height: 1px;
            background-color: {c.BORDER_SUBTLE};
            margin: 4px 8px;
        }}

        /* Tabs */
        QTabWidget::pane {{
            background-color: {c.BG_SURFACE};
            border: 1px solid {c.BORDER_SUBTLE};
            border-radius: 10px;
            top: -1px;
        }}
        QTabBar::tab {{
            background-color: {c.BG_CANVAS};
            color: {c.TEXT_SECONDARY};
            border: 1px solid transparent;
            border-top-left-radius: 8px;
            border-top-right-radius: 8px;
            padding: 8px 16px;
            margin-right: 4px;
            font-weight: 600;
        }}
        QTabBar::tab:hover {{
            background-color: {c.BG_ELEVATED};
            color: {c.TEXT_PRIMARY};
        }}
        QTabBar::tab:selected {{
            background-color: {c.BG_SURFACE};
            color: {c.TEXT_PRIMARY};
            border-color: {c.BORDER_SUBTLE};
            border-bottom-color: {c.BG_SURFACE};
        }}

        /* Group Boxes */
        QGroupBox {{
            background-color: {c.BG_ELEVATED};
            border: 1px solid {c.BORDER_SUBTLE};
            border-radius: 10px;
            margin-top: 14px;
            padding: 14px 12px 10px 12px;
            font-weight: 600;
            color: {c.TEXT_PRIMARY};
        }}
        QGroupBox::title {{
            subcontrol-origin: margin;
            subcontrol-position: top left;
            left: 12px;
            padding: 0 6px;
            color: {c.TEXT_SECONDARY};
            background-color: {c.BG_SURFACE};
        }}

        /* Buttons */
        QPushButton {{
            background-color: {c.BG_INTERACTIVE};
            color: {c.TEXT_PRIMARY};
            border: 1px solid {c.BORDER_STRONG};
            border-radius: 8px;
            padding: 8px 16px;
            font-weight: 600;
            font-size: 13px;
        }}
        QPushButton:hover {{
            background-color: {c.BG_INTERACTIVE_HOVER};
        }}
        QPushButton:pressed {{
            background-color: {c.BG_INTERACTIVE_PRESSED};
        }}
        QPushButton:focus {{
            border: 1px solid {c.BORDER_FOCUS};
        }}
        QPushButton:disabled {{
            background-color: {c.BG_SURFACE};
            color: #64748B;
            border-color: {c.BORDER_SUBTLE};
        }}
        QPushButton[variant="primary"] {{
            background-color: {c.STATUS_OK};
            color: {c.TEXT_ON_ACCENT};
            border: 1px solid {c.STATUS_OK};
        }}
        QPushButton[variant="primary"]:hover {{
            background-color: {c.STATUS_OK_HOVER};
            border-color: {c.STATUS_OK_HOVER};
        }}
        QPushButton[variant="danger"] {{
            background-color: rgba(239, 68, 68, 0.14);
            color: #FCA5A5;
            border: 1px solid rgba(239, 68, 68, 0.45);
        }}
        QPushButton[variant="danger"]:hover {{
            background-color: {c.STATUS_ALERT_HOVER};
            color: {c.TEXT_ON_ACCENT};
            border-color: {c.STATUS_ALERT_HOVER};
        }}
        QPushButton[variant="accentInline"] {{
            background-color: {c.STATUS_CALIBRATION};
            color: {c.TEXT_ON_ACCENT};
            border: 1px solid {c.STATUS_CALIBRATION};
            padding: 5px 12px;
            font-size: 12px;
        }}

        /* Progress Bar */
        QProgressBar {{
            border: 1px solid {c.BORDER_STRONG};
            border-radius: 8px;
            background-color: {c.BG_ELEVATED};
            min-height: 24px;
            text-align: center;
            color: {c.TEXT_PRIMARY};
            font-weight: 600;
        }}
        QProgressBar::chunk {{
            background-color: {c.STATUS_OK};
            border-radius: 7px;
        }}
        QProgressBar[calibState="error"]::chunk {{
            background-color: {c.STATUS_ALERT};
        }}

        /* Tables */
        QTableWidget {{
            background-color: {c.BG_SURFACE};
            alternate-background-color: {c.BG_ELEVATED};
            gridline-color: {c.BORDER_SUBTLE};
            color: {c.TEXT_PRIMARY};
            border: 1px solid {c.BORDER_SUBTLE};
            border-radius: 8px;
            selection-background-color: {c.BG_INTERACTIVE_HOVER};
            selection-color: {c.TEXT_PRIMARY};
        }}
        QHeaderView::section {{
            background-color: {c.BG_ELEVATED};
            color: {c.TEXT_SECONDARY};
            font-weight: 600;
            padding: 7px 8px;
            border: none;
            border-right: 1px solid {c.BORDER_SUBTLE};
            border-bottom: 1px solid {c.BORDER_SUBTLE};
        }}
        QTableCornerButton::section {{
            background-color: {c.BG_ELEVATED};
            border: none;
        }}

        /* Scrollbars & Tooltips */
        QScrollBar:vertical {{
            background-color: {c.BG_SURFACE};
            width: 10px;
            margin: 0;
        }}
        QScrollBar::handle:vertical {{
            background-color: {c.BORDER_STRONG};
            min-height: 24px;
            border-radius: 5px;
        }}
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
            height: 0;
        }}
        QScrollBar:horizontal {{
            background-color: {c.BG_SURFACE};
            height: 10px;
            margin: 0;
        }}
        QScrollBar::handle:horizontal {{
            background-color: {c.BORDER_STRONG};
            min-width: 24px;
            border-radius: 5px;
        }}
        QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{
            width: 0;
        }}
        QToolTip {{
            background-color: {c.BG_ELEVATED};
            color: {c.TEXT_PRIMARY};
            border: 1px solid {c.BORDER_STRONG};
            border-radius: 6px;
            padding: 5px 8px;
        }}
    """


def apply_app_theme(app: QtWidgets.QApplication) -> None:
    """Apply Fusion style, dark QPalette, font, and global QSS to QApplication."""
    if app.property(_THEME_APPLIED_PROP):
        return

    fusion_style = QtWidgets.QStyleFactory.create("Fusion")
    if fusion_style is not None:
        app.setStyle(fusion_style)

    app.setPalette(build_qpalette())
    font = QtGui.QFont(app_font_family(), 10)
    app.setFont(font)
    app.setStyleSheet(build_global_stylesheet())
    app.setProperty(_THEME_APPLIED_PROP, True)


def ensure_theme_applied() -> None:
    """Ensure the global theme is applied whenever GUI widgets are constructed."""
    app = QtCore.QCoreApplication.instance()
    if isinstance(app, QtWidgets.QApplication):
        apply_app_theme(app)


def refresh_widget_style(widget: QtWidgets.QWidget) -> None:
    """Re-polish a widget after updating a dynamic Qt property used in QSS."""
    style = widget.style()
    if style is not None:
        style.unpolish(widget)
        style.polish(widget)
    widget.update()
