"""System Tray and Quick Snooze Manager (M4.2).

Reference: docs/DESIGN_SYSTEM.md & docs/ARCHITECTURE.md
Implements:
1. Dynamic colored tray status icons (Green, Amber, Red, Orange, Blue, Violet, Grey).
2. Quick Snooze menu (15 / 30 / 60 minutes) and live countdown status.
3. Guided recalibration trigger and environment warning indicators.
4. Dashboard opener (including left-click trigger) and graceful quit.
"""

from __future__ import annotations

import sys

from PySide6 import QtCore, QtGui, QtWidgets

from tracker.states import OperatingMode, UIIntrusivenessLevel
from ui.theme import ColorTokens


class SystemTrayManager(QtCore.QObject):
    """Manages system tray icon, notifications, and menu actions."""

    recalibrate_requested = QtCore.Signal()
    snooze_requested = QtCore.Signal(int)  # minutes
    open_dashboard_requested = QtCore.Signal()
    quit_requested = QtCore.Signal()

    def __init__(self, parent: QtCore.QObject | None = None) -> None:
        super().__init__(parent)
        self.tray_icon = QtWidgets.QSystemTrayIcon(self)
        self.tray_icon.activated.connect(self._on_tray_activated)

        # Context menu
        self.menu = QtWidgets.QMenu()
        self._status_action = self.menu.addAction("Status: Memulai...")
        self._status_action.setEnabled(False)

        self.menu.addSeparator()
        self._preview_action = self.menu.addAction("📷 Pratinjau Kamera (Live View)")
        self._preview_action.triggered.connect(self.open_dashboard_requested.emit)
        self._calib_action = self.menu.addAction("🎯 Kalibrasi Ulang (3 dtk)")
        self._calib_action.triggered.connect(self.recalibrate_requested.emit)

        # Snooze submenu
        self.snooze_menu = self.menu.addMenu("Jeda Pemantauan (Snooze)")
        self.snooze_menu.addAction("Jeda 15 Menit").triggered.connect(
            lambda: self.snooze_requested.emit(15)
        )
        self.snooze_menu.addAction("Jeda 30 Menit").triggered.connect(
            lambda: self.snooze_requested.emit(30)
        )
        self.snooze_menu.addAction("Jeda 60 Menit").triggered.connect(
            lambda: self.snooze_requested.emit(60)
        )
        self.snooze_menu.addSeparator()
        self._resume_action = self.snooze_menu.addAction("Lanjutkan Pemantauan")
        self._resume_action.setEnabled(False)
        self._resume_action.triggered.connect(lambda: self.snooze_requested.emit(0))

        self.menu.addSeparator()
        self._dashboard_action = self.menu.addAction("Buka Dashboard Sesi")
        self._dashboard_action.triggered.connect(self.open_dashboard_requested.emit)

        self.menu.addSeparator()
        self._quit_action = self.menu.addAction("Keluar")
        self._quit_action.triggered.connect(self.quit_requested.emit)

        self.tray_icon.setContextMenu(self.menu)

        # State tracking for notifications, snooze, and environment alerts
        self._prev_ui_level: UIIntrusivenessLevel = UIIntrusivenessLevel.SILENT_OK
        self._prev_op_mode: OperatingMode = OperatingMode.ACTIVE_MONITORING
        self._is_snoozed: bool = False
        self._snooze_remaining_s: float = 0.0
        self._low_light_warning: bool = False
        self._extreme_close_proximity: bool = False
        self._base_tooltip: str = "Bapode: Aktif"

        # Initial icon
        self.set_color_indicator(ColorTokens.STATUS_OK, self._base_tooltip)
        self.tray_icon.show()

    def _on_tray_activated(
        self, reason: QtWidgets.QSystemTrayIcon.ActivationReason
    ) -> None:
        """Open Dashboard on primary left-click or double-click."""
        if reason in (
            QtWidgets.QSystemTrayIcon.ActivationReason.Trigger,
            QtWidgets.QSystemTrayIcon.ActivationReason.DoubleClick,
        ):
            self.open_dashboard_requested.emit()

    def show_startup_notification(self) -> None:
        """Display native OS notification banner confirming app is running in tray."""
        if self.tray_icon.isSystemTrayAvailable():
            if sys.platform == "win32":
                msg = (
                    "Aplikasi aktif memantau di System Tray (kanan bawah). "
                    "Klik kanan ikon untuk opsi."
                )
            else:
                msg = "Aplikasi aktif memantau di Menu Bar (kanan atas). Klik ikon untuk opsi."
            self.tray_icon.showMessage(
                "Bapode v3.0",
                msg,
                QtWidgets.QSystemTrayIcon.MessageIcon.Information,
                3000,
            )

    def _compose_tooltip(self, base_tooltip: str) -> str:
        """Append active environment hints onto the tray tooltip."""
        notes: list[str] = []
        if self._extreme_close_proximity:
            notes.append("Terlalu Dekat")
        if self._low_light_warning:
            notes.append("Cahaya Redup")
        if not notes:
            return base_tooltip
        return f"{base_tooltip} | {' • '.join(notes)}"

    def set_color_indicator(self, hex_color: str, tooltip: str) -> None:
        """Generate and set a round status icon of given color."""
        self._base_tooltip = tooltip
        pixmap = QtGui.QPixmap(64, 64)
        pixmap.fill(QtCore.Qt.GlobalColor.transparent)

        painter = QtGui.QPainter(pixmap)
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)

        color = QtGui.QColor(hex_color)
        painter.setBrush(QtGui.QBrush(color))
        painter.setPen(QtGui.QPen(QtGui.QColor(0, 0, 0, 80), 3))
        painter.drawEllipse(6, 6, 52, 52)
        painter.end()

        icon = QtGui.QIcon(pixmap)
        self.tray_icon.setIcon(icon)
        self.tray_icon.setToolTip(self._compose_tooltip(tooltip))

    @QtCore.Slot(bool, float)
    def update_snooze_status(self, is_snoozed: bool, remaining_s: float) -> None:
        """Update remaining snooze countdown in tray status item and tooltip."""
        self._is_snoozed = is_snoozed
        self._snooze_remaining_s = max(0.0, remaining_s)
        self._resume_action.setEnabled(is_snoozed)

        if is_snoozed:
            mins, secs = divmod(int(self._snooze_remaining_s), 60)
            self._status_action.setText(f"Status: Dijeda ({mins:02d}:{secs:02d} tersisa)")
            self.tray_icon.setToolTip(
                self._compose_tooltip(f"Bapode: Dijeda ({mins:02d}:{secs:02d})")
            )

    @QtCore.Slot(bool, bool, str)
    def update_environment_status(
        self,
        low_light: bool,
        extreme_close: bool,
        message: str = "",
    ) -> None:
        """Track low-light and extreme close proximity cues in tray tooltip."""
        del message
        self._low_light_warning = low_light
        self._extreme_close_proximity = extreme_close
        self.tray_icon.setToolTip(self._compose_tooltip(self._base_tooltip))

    def update_status(self, op_mode_str: str, vis_state_str: str, ui_level_str: str) -> None:
        """Update tray status icon and text reflecting the 3-axis state."""
        try:
            op_mode = OperatingMode(op_mode_str)
        except ValueError:
            op_mode = OperatingMode.ACTIVE_MONITORING

        try:
            ui_level = UIIntrusivenessLevel(ui_level_str)
        except ValueError:
            ui_level = UIIntrusivenessLevel.SILENT_OK

        self._resume_action.setEnabled(op_mode == OperatingMode.PAUSED_MANUAL)

        if op_mode == OperatingMode.PAUSED_MANUAL:
            if self._is_snoozed and self._snooze_remaining_s > 0:
                mins, secs = divmod(int(self._snooze_remaining_s), 60)
                self._status_action.setText(f"Status: Dijeda ({mins:02d}:{secs:02d} tersisa)")
                self.set_color_indicator(
                    ColorTokens.STATUS_PAUSED, f"Bapode: Dijeda ({mins:02d}:{secs:02d})"
                )
            else:
                self._status_action.setText("Status: Dijeda Manual (Snooze)")
                self.set_color_indicator(ColorTokens.STATUS_PAUSED, "Bapode: Dijeda Manual")
            self._prev_op_mode = op_mode
            return
        if op_mode == OperatingMode.PAUSED_MEETING_AUTO:
            self._status_action.setText("Status: Dijeda (Rapat Fullscreen)")
            self.set_color_indicator(ColorTokens.STATUS_PAUSED, "Bapode: Mode Rapat")
            self._prev_op_mode = op_mode
            return
        if op_mode == OperatingMode.CALIBRATING:
            self._status_action.setText("Status: Sedang Kalibrasi...")
            self.set_color_indicator(ColorTokens.STATUS_CALIBRATION, "Bapode: Kalibrasi")
            self._prev_op_mode = op_mode
            return
        if op_mode == OperatingMode.NEEDS_RECALIBRATION:
            self._status_action.setText("Status: Perlu Rekalibrasi")
            self.set_color_indicator(ColorTokens.STATUS_CALIBRATION, "Bapode: Setup Drift")
            if self._prev_op_mode != OperatingMode.NEEDS_RECALIBRATION:
                self.tray_icon.showMessage(
                    "Bapode: Perlu Kalibrasi Ulang",
                    "Perubahan posisi duduk atau layar terdeteksi. Kalibrasi ulang 3 detik.",
                    QtWidgets.QSystemTrayIcon.MessageIcon.Warning,
                    4000,
                )
            self._prev_op_mode = op_mode
            return

        # Active Monitoring
        if self._extreme_close_proximity and ui_level == UIIntrusivenessLevel.SILENT_OK:
            self._status_action.setText("Status: Jarak Wajah Terlalu Dekat")
            self.set_color_indicator(
                ColorTokens.STATUS_ALERT, "Jarak Wajah Terlalu Dekat ke Layar"
            )
        elif ui_level == UIIntrusivenessLevel.SILENT_OK:
            suffix = " • Cahaya Redup" if self._low_light_warning else ""
            self._status_action.setText(f"Status: Baik ({vis_state_str}){suffix}")
            self.set_color_indicator(ColorTokens.STATUS_OK, f"Postur Baik ({vis_state_str})")
        elif ui_level == UIIntrusivenessLevel.AMBIENT_WARN:
            self._status_action.setText("Status: Peringatan Halus")
            self.set_color_indicator(ColorTokens.STATUS_WARN, "Peringatan Postur Ambien")
        elif ui_level == UIIntrusivenessLevel.ACTIVE_OVERLAY:
            self._status_action.setText("Status: Perlu Koreksi Postur")
            self.set_color_indicator(ColorTokens.STATUS_ALERT, "Pelanggaran Postur Aktif")
        elif ui_level == UIIntrusivenessLevel.SUPPRESSED_COOLDOWN:
            self._status_action.setText("Status: Cooldown Alert")
            self.set_color_indicator(
                ColorTokens.STATUS_COOLDOWN, "Peringatan Tertahan (Cooldown)"
            )
        elif ui_level == UIIntrusivenessLevel.MICROBREAK_PROMPT:
            self._status_action.setText("Status: Waktu Istirahat (Microbreak)")
            self.set_color_indicator(ColorTokens.STATUS_INFO, "Waktu Peregangan 60s")

        # Native OS banner notification on critical state transitions
        if ui_level != self._prev_ui_level:
            if ui_level == UIIntrusivenessLevel.ACTIVE_OVERLAY:
                self.tray_icon.showMessage(
                    "Bapode: Peringatan Postur",
                    "Postur membungkuk (>= 15 detik). Tegakkan kembali posisi duduk Anda.",
                    QtWidgets.QSystemTrayIcon.MessageIcon.Warning,
                    4000,
                )
            elif ui_level == UIIntrusivenessLevel.MICROBREAK_PROMPT:
                self.tray_icon.showMessage(
                    "Bapode: Waktu Peregangan",
                    "Beban statis berulang terdeteksi. Istirahat sejenak 60 detik.",
                    QtWidgets.QSystemTrayIcon.MessageIcon.Information,
                    5000,
                )

        self._prev_ui_level = ui_level
        self._prev_op_mode = op_mode
