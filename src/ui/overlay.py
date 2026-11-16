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
   - ACTIVE_OVERLAY (#EF4444 Red) with issue-specific cues from PostureViolationFlags
   - MICROBREAK_PROMPT (#3B82F6 Blue)
   - NEEDS_RECALIBRATION (#8B5CF6 Violet, 5s passive notice on transition)
   - Hidden during SILENT_OK, AMBIENT_WARN, SUPPRESSED_COOLDOWN, and PAUSED modes.
"""

from __future__ import annotations

import math

from config import load_config
from tracker.states import OperatingMode, PostureViolationFlags, UIIntrusivenessLevel
from ui.platform_bridge import set_screen_share_protected, show_window_non_activating
from ui.qt import QtCore, QtGui, QtWidgets
from ui.theme import ColorTokens, app_font_family


def _format_violation_message(flags: PostureViolationFlags | None) -> str:
    """Build a concise user-facing posture correction cue from active flags."""
    if flags is None or not flags.has_any_violation:
        return "Perbaiki Postur: Kepala Maju / Bahu Merosot"

    cues: list[str] = []
    if flags.v_fhp:
        cues.append("Tarik kepala ke belakang")
    if flags.v_slump:
        cues.append("Tegakkan bahu & punggung")
    if flags.v_neck_comp:
        cues.append("Panjangkan leher")
    if flags.v_gaze_pitch:
        cues.append("Angkat pandangan")
    if flags.v_tilt:
        cues.append("Sejajarkan bahu/kepala")

    if len(cues) == 1:
        return f"Perbaiki Postur: {cues[0]}"
    if len(cues) == 2:
        return f"Perbaiki Postur: {cues[0]} & {cues[1].lower()}"
    return f"Perbaiki Postur: {cues[0]} (+{len(cues) - 1} lainnya)"


class HUDOverlay(QtWidgets.QWidget):
    """Screen-safe non-activating posture guidance HUD overlay."""

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self._current_level = UIIntrusivenessLevel.SILENT_OK
        self._current_op_mode = OperatingMode.ACTIVE_MONITORING
        self._badge_text = "Tegakkan Punggung & Leher"
        self._badge_color = QtGui.QColor(ColorTokens.STATUS_ALERT)
        self._default_microbreak_s: int = max(
            1, int(round(load_config().tracker.microbreak_duration_s))
        )
        self._countdown_s: int = self._default_microbreak_s
        self._microbreak_active: bool = False
        self._microbreak_custom_msg: str = ""
        self._microbreak_just_finished: bool = False
        self._extreme_close_active: bool = False
        self._showing_proximity_alert: bool = False
        self._recalib_notice_shown_for_episode: bool = False

        # Auto-dismiss timer for temporary notices (e.g. NEEDS_RECALIBRATION, ideal recovery)
        self._auto_hide_timer = QtCore.QTimer(self)
        self._auto_hide_timer.setSingleShot(True)
        self._auto_hide_timer.timeout.connect(self.hide)

        # 1-second countdown timer for MICROBREAK_PROMPT
        self._microbreak_timer = QtCore.QTimer(self)
        self._microbreak_timer.setInterval(1000)
        self._microbreak_timer.timeout.connect(self._on_microbreak_tick)

        self._init_window_properties()

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

        # Initialize native window handle and OS screen-share protection
        set_screen_share_protected(self)

    def _reposition_to_top_center(self) -> None:
        """Position overlay at the top-center of currently active screen."""
        cursor_pos = QtGui.QCursor.pos()
        screen = QtGui.QGuiApplication.screenAt(cursor_pos) or QtGui.QGuiApplication.primaryScreen()
        if screen is not None:
            screen_geo = screen.geometry()
            x = screen_geo.left() + (screen_geo.width() - self.width()) // 2
            y = screen_geo.top() + 24
            self.move(x, y)

    def _fit_badge_width(self, text: str) -> None:
        """Adjust overlay width to avoid clipping longer posture guidance cues."""
        font = QtGui.QFont(app_font_family(), 12, QtGui.QFont.Weight.Bold)
        metrics = QtGui.QFontMetrics(font)
        needed = metrics.horizontalAdvance(text) + 84
        target_w = max(460, min(680, needed))
        if target_w != self.width():
            self.resize(target_w, 68)

    def showEvent(self, event: QtGui.QShowEvent) -> None:  # noqa: N802
        """Apply screen sharing privacy protection on display."""
        super().showEvent(event)
        set_screen_share_protected(self)

    @QtCore.Slot(str, str, str, object)
    def on_state_changed(
        self,
        op_mode_str: str,
        vis_state_str: str,
        ui_level_str: str,
        flags: object = None,
    ) -> None:
        """Receive 4-tuple state_changed signal from VisionTrackerWorker."""
        parsed_flags = flags if isinstance(flags, PostureViolationFlags) else None
        self.update_state(
            ui_level_str,
            op_mode_str=op_mode_str,
            vis_state_str=vis_state_str,
            flags=parsed_flags,
        )

    @QtCore.Slot(bool, bool, str)
    def update_environment_status(
        self,
        low_light: bool,
        extreme_close: bool,
        message: str = "",
    ) -> None:
        """Respond to sustained extreme close proximity during lower-body occlusion."""
        del low_light, message  # Low-light cues live non-intrusively in Tray & Dashboard
        was_close = self._extreme_close_active
        self._extreme_close_active = extreme_close

        if self._current_op_mode != OperatingMode.ACTIVE_MONITORING:
            self._showing_proximity_alert = False
            return

        if extreme_close and self._current_level not in (
            UIIntrusivenessLevel.ACTIVE_OVERLAY,
            UIIntrusivenessLevel.MICROBREAK_PROMPT,
        ):
            self._showing_proximity_alert = True
            self._auto_hide_timer.stop()
            self._badge_color = QtGui.QColor(ColorTokens.STATUS_ALERT)
            self._badge_text = "Jarak Wajah Terlalu Dekat — Mundurkan Posisi"
            self._fit_badge_width(self._badge_text)
            self._reposition_to_top_center()
            self.update()
            show_window_non_activating(self)
        elif was_close and not extreme_close and self._showing_proximity_alert:
            self._showing_proximity_alert = False
            if self.isVisible() and self._current_level not in (
                UIIntrusivenessLevel.ACTIVE_OVERLAY,
                UIIntrusivenessLevel.MICROBREAK_PROMPT,
            ):
                self._show_recovery_badge()

    def _show_recovery_badge(
        self,
        text: str = "Postur Kembali Ideal",
        duration_ms: int = 5000,
    ) -> None:
        """Show positive green confirmation, then auto-hide."""
        self._badge_color = QtGui.QColor(ColorTokens.STATUS_OK)
        self._badge_text = text
        self._fit_badge_width(self._badge_text)
        self._reposition_to_top_center()
        self.update()
        show_window_non_activating(self)
        self._auto_hide_timer.start(duration_ms)

    def _stop_microbreak_timer(self) -> None:
        """Stop microbreak countdown timer and clear active microbreak flag."""
        if self._microbreak_timer.isActive():
            self._microbreak_timer.stop()
        self._microbreak_active = False
        self._microbreak_custom_msg = ""

    def _on_microbreak_tick(self) -> None:
        """Decrement microbreak countdown every second and auto-dismiss at 0s."""
        if not self._microbreak_active:
            self._microbreak_timer.stop()
            return

        self._countdown_s = max(0, self._countdown_s - 1)
        if self._countdown_s <= 0:
            self._stop_microbreak_timer()
            self._microbreak_just_finished = True
            self._show_recovery_badge(
                text="Peregangan Selesai — Lanjutkan Bekerja",
                duration_ms=3000,
            )
            return

        if not self._microbreak_custom_msg:
            self._badge_text = f"Waktu Peregangan ({self._countdown_s}s)"
            self._fit_badge_width(self._badge_text)
            self.update()

    @QtCore.Slot(float)
    def sync_microbreak_remaining(self, remaining_s: float) -> None:
        """Synchronize HUD microbreak countdown with backend elapsed timer."""
        if not self._microbreak_active:
            return
        if remaining_s <= 0.0:
            self._countdown_s = 0
            self._on_microbreak_tick()
            return
        target_s = max(1, int(math.ceil(remaining_s)))
        if abs(target_s - self._countdown_s) >= 2:
            self._countdown_s = target_s
            if not self._microbreak_custom_msg:
                self._badge_text = f"Waktu Peregangan ({self._countdown_s}s)"
                self._fit_badge_width(self._badge_text)
                self.update()

    def update_state(
        self,
        ui_level_str: str,
        message: str = "",
        *,
        op_mode_str: str = "",
        vis_state_str: str = "",
        flags: PostureViolationFlags | None = None,
    ) -> None:
        """Update visual presentation based on OperatingMode, UIIntrusivenessLevel, and flags."""
        del vis_state_str
        try:
            level = UIIntrusivenessLevel(ui_level_str)
        except ValueError:
            level = UIIntrusivenessLevel.SILENT_OK

        if op_mode_str:
            try:
                op_mode = OperatingMode(op_mode_str)
            except ValueError:
                op_mode = OperatingMode.ACTIVE_MONITORING
        else:
            op_mode = (
                OperatingMode.NEEDS_RECALIBRATION
                if ui_level_str == "NEEDS_RECALIBRATION"
                else OperatingMode.ACTIVE_MONITORING
            )

        prev_level = self._current_level
        self._current_level = level
        self._current_op_mode = op_mode

        if op_mode != OperatingMode.NEEDS_RECALIBRATION:
            self._recalib_notice_shown_for_episode = False

        # Immediately dismiss HUD during paused or calibrating modes
        if op_mode in (
            OperatingMode.PAUSED_MANUAL,
            OperatingMode.PAUSED_MEETING_AUTO,
            OperatingMode.CALIBRATING,
        ):
            self._stop_microbreak_timer()
            self._microbreak_just_finished = False
            self._showing_proximity_alert = False
            self._auto_hide_timer.stop()
            self.hide()
            return

        # Setup drift / recalibration needed: show 5s passive violet notice once per episode
        if op_mode == OperatingMode.NEEDS_RECALIBRATION or ui_level_str == "NEEDS_RECALIBRATION":
            self._stop_microbreak_timer()
            self._microbreak_just_finished = False
            self._showing_proximity_alert = False
            if not self._recalib_notice_shown_for_episode:
                self._recalib_notice_shown_for_episode = True
                self._auto_hide_timer.stop()
                self._badge_color = QtGui.QColor(ColorTokens.STATUS_CALIBRATION)
                self._badge_text = message or "Posisi Bergeser — Silakan Rekalibrasi"
                self._fit_badge_width(self._badge_text)
                self._reposition_to_top_center()
                self.update()
                show_window_non_activating(self)
                self._auto_hide_timer.start(5000)
            return

        if level == UIIntrusivenessLevel.ACTIVE_OVERLAY:
            self._stop_microbreak_timer()
            self._microbreak_just_finished = False
            self._showing_proximity_alert = False
            self._auto_hide_timer.stop()
            self._badge_color = QtGui.QColor(ColorTokens.STATUS_ALERT)
            self._badge_text = message or _format_violation_message(flags)
            self._fit_badge_width(self._badge_text)
            self._reposition_to_top_center()
            self.update()
            show_window_non_activating(self)
        elif level == UIIntrusivenessLevel.MICROBREAK_PROMPT:
            if self._microbreak_just_finished:
                return
            self._showing_proximity_alert = False
            self._auto_hide_timer.stop()
            if not self._microbreak_active:
                self._microbreak_active = True
                self._countdown_s = self._default_microbreak_s
                self._microbreak_custom_msg = message
                self._microbreak_timer.start()
            self._badge_color = QtGui.QColor(ColorTokens.STATUS_INFO)
            self._badge_text = (
                self._microbreak_custom_msg or f"Waktu Peregangan ({self._countdown_s}s)"
            )
            self._fit_badge_width(self._badge_text)
            self._reposition_to_top_center()
            self.update()
            show_window_non_activating(self)
        elif self._extreme_close_active:
            # Keep extreme close proximity warning active during OCCLUDED_LOWER
            self._stop_microbreak_timer()
            self._microbreak_just_finished = False
            self._showing_proximity_alert = True
            self._auto_hide_timer.stop()
            self._badge_color = QtGui.QColor(ColorTokens.STATUS_ALERT)
            self._badge_text = message or "Jarak Wajah Terlalu Dekat — Mundurkan Posisi"
            self._fit_badge_width(self._badge_text)
            self._reposition_to_top_center()
            self.update()
            show_window_non_activating(self)
        else:
            # Posture is now ideal (SILENT_OK, AMBIENT_WARN, SUPPRESSED_COOLDOWN)
            self._stop_microbreak_timer()
            if self._microbreak_just_finished:
                self._microbreak_just_finished = False
                return
            was_showing_alert = self.isVisible() and (
                prev_level
                in (
                    UIIntrusivenessLevel.ACTIVE_OVERLAY,
                    UIIntrusivenessLevel.MICROBREAK_PROMPT,
                )
                or self._showing_proximity_alert
            )
            self._showing_proximity_alert = False
            if was_showing_alert:
                self._show_recovery_badge()
            elif not self._auto_hide_timer.isActive():
                self.hide()

    def paintEvent(self, event: QtGui.QPaintEvent) -> None:  # noqa: N802
        """Render pill badge notification."""
        del event
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)

        rect = self.rect().adjusted(4, 4, -4, -4)

        # Background pill with dark translucent backdrop
        bg_color = QtGui.QColor(11, 15, 23, 238)
        painter.setBrush(QtGui.QBrush(bg_color))
        painter.setPen(QtGui.QPen(self._badge_color, 1.5))
        painter.drawRoundedRect(rect, 16, 16)

        # Status indicator circle
        painter.setBrush(QtGui.QBrush(self._badge_color))
        painter.setPen(QtCore.Qt.PenStyle.NoPen)
        painter.drawEllipse(rect.left() + 20, rect.center().y() - 6, 12, 12)

        # Text
        painter.setPen(QtGui.QColor(ColorTokens.TEXT_PRIMARY))
        font = QtGui.QFont(app_font_family(), 12, QtGui.QFont.Weight.Bold)
        painter.setFont(font)

        text_rect = rect.adjusted(44, 0, -20, 0)
        painter.drawText(
            text_rect,
            QtCore.Qt.AlignmentFlag.AlignVCenter | QtCore.Qt.AlignmentFlag.AlignLeft,
            self._badge_text,
        )
        painter.end()
