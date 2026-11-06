"""Non-Activating & Screen-Share Safe HUD Overlay (M4.1).

Reference: docs/DESIGN_SYSTEM.md & docs/ARCHITECTURE.md
Implements:
1. Click-through, non-activating window flags preventing keyboard focus theft:
   - Qt.FramelessWindowHint
   - Qt.WindowStaysOnTopHint
   - Qt.Tool
   - Qt.WindowTransparentForInput
   - Qt.WindowDoesNotAcceptFocus
   - Qt.WA_ShowWithoutActivating = True
   - Qt.WA_TranslucentBackground = True
2. Screen-share safe protection via platform_bridge (NSWindowSharingNone / WDA_EXCLUDEFROMCAPTURE).
3. Visual states:
   - ACTIVE_OVERLAY (#EF4444 Red)
   - MICROBREAK_PROMPT (#3B82F6 Blue)
   - NEEDS_RECALIBRATION (#8B5CF6 Violet)
   - Hidden during SILENT_OK, AMBIENT_WARN, and SUPPRESSED_COOLDOWN.
"""

from PySide6 import QtCore, QtGui, QtWidgets

from tracker.states import UIIntrusivenessLevel
from ui.platform_bridge import set_screen_share_protected


class HUDOverlay(QtWidgets.QWidget):
    """Screen-safe non-activating posture guidance HUD overlay."""

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self._init_window_properties()
        self._current_level = UIIntrusivenessLevel.SILENT_OK
        self._badge_text = "Tegakkan Punggung & Leher"
        self._badge_color = QtGui.QColor("#EF4444")
        self._countdown_s: int = 60

        # Auto-dismiss timer for temporary notices (e.g. NEEDS_RECALIBRATION)
        self._auto_hide_timer = QtCore.QTimer(self)
        self._auto_hide_timer.setSingleShot(True)
        self._auto_hide_timer.timeout.connect(self.hide)

    def _init_window_properties(self) -> None:
        """Apply non-focus-stealing window flags and transparent attributes."""
        flags = (
            QtCore.Qt.WindowType.FramelessWindowHint
            | QtCore.Qt.WindowType.WindowStaysOnTopHint
            | QtCore.Qt.WindowType.Tool
            | QtCore.Qt.WindowType.WindowTransparentForInput
            | QtCore.Qt.WindowType.WindowDoesNotAcceptFocus
        )
        self.setWindowFlags(flags)
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_TranslucentBackground, True)

        # Set default size and center on screen top
        self.resize(460, 68)
        self._reposition_to_top_center()

    def _reposition_to_top_center(self) -> None:
        """Position overlay at the top-center of primary screen."""
        screen = QtGui.QGuiApplication.primaryScreen()
        if screen is not None:
            screen_geo = screen.geometry()
            x = (screen_geo.width() - self.width()) // 2
            y = screen_geo.top() + 24
            self.move(x, y)

    def showEvent(self, event: QtGui.QShowEvent) -> None:  # noqa: N802
        """Apply screen sharing privacy protection on display."""
        super().showEvent(event)
        set_screen_share_protected(self)

    def update_state(self, ui_level_str: str, message: str = "") -> None:
        """Update visual presentation based on UIIntrusivenessLevel."""
        try:
            level = UIIntrusivenessLevel(ui_level_str)
        except ValueError:
            level = UIIntrusivenessLevel.SILENT_OK

        self._current_level = level

        if level == UIIntrusivenessLevel.ACTIVE_OVERLAY:
            self._badge_color = QtGui.QColor("#EF4444")
            self._badge_text = message or "Perbaiki Postur: Kepala Maju / Bahu Merosot"
            self.show()
            self.raise_()
            set_screen_share_protected(self)
            self.update()
        elif level == UIIntrusivenessLevel.MICROBREAK_PROMPT:
            self._badge_color = QtGui.QColor("#3B82F6")
            self._badge_text = message or f"Waktu Peregangan ({self._countdown_s}s)"
            self.show()
            self.raise_()
            set_screen_share_protected(self)
            self.update()
        elif ui_level_str == "NEEDS_RECALIBRATION":
            self._badge_color = QtGui.QColor("#8B5CF6")
            self._badge_text = message or "Posisi Bergeser — Silakan Rekalibrasi"
            self.show()
            self.raise_()
            set_screen_share_protected(self)
            self.update()
            self._auto_hide_timer.start(5000)  # Auto-hide after 5s
        else:
            # SILENT_OK, AMBIENT_WARN, SUPPRESSED_COOLDOWN -> hidden
            self.hide()

    def paintEvent(self, event: QtGui.QPaintEvent) -> None:  # noqa: N802
        """Render pill badge notification."""
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)

        rect = self.rect().adjusted(4, 4, -4, -4)

        # Background pill with dark translucent backdrop
        bg_color = QtGui.QColor(18, 18, 24, 230)
        painter.setBrush(QtGui.QBrush(bg_color))
        painter.setPen(QtGui.QPen(self._badge_color, 2))
        painter.drawRoundedRect(rect, 16, 16)

        # Status indicator circle
        painter.setBrush(QtGui.QBrush(self._badge_color))
        painter.setPen(QtCore.Qt.PenStyle.NoPen)
        painter.drawEllipse(rect.left() + 20, rect.center().y() - 6, 12, 12)

        # Text
        painter.setPen(QtGui.QColor(255, 255, 255))
        font = painter.font()
        font.setPointSize(12)
        font.setBold(True)
        painter.setFont(font)

        text_rect = rect.adjusted(44, 0, -20, 0)
        painter.drawText(
            text_rect,
            QtCore.Qt.AlignmentFlag.AlignVCenter | QtCore.Qt.AlignmentFlag.AlignLeft,
            self._badge_text,
        )
        painter.end()
