"""System Tray and Quick Snooze Manager (M4.2).

Reference: docs/DESIGN_SYSTEM.md & docs/ARCHITECTURE.md
Implements:
1. Dynamic colored tray status icons (Green, Amber, Red, Orange, Blue, Violet, Grey).
2. Quick Snooze menu (15 / 30 / 60 minutes) and Resume option.
3. Guided recalibration trigger.
4. Dashboard opener and graceful quit.
"""

import sys

from PySide6 import QtCore, QtGui, QtWidgets

from tracker.states import OperatingMode, UIIntrusivenessLevel


class SystemTrayManager(QtCore.QObject):
    """Manages system tray icon, notifications, and menu actions."""

    recalibrate_requested = QtCore.Signal()
    snooze_requested = QtCore.Signal(int)  # minutes
    open_dashboard_requested = QtCore.Signal()
    quit_requested = QtCore.Signal()

    def __init__(self, parent: QtCore.QObject | None = None) -> None:
        super().__init__(parent)
        self.tray_icon = QtWidgets.QSystemTrayIcon(self)

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
        self.snooze_menu.addAction("Lanjutkan Pemantauan").triggered.connect(
            lambda: self.snooze_requested.emit(0)
        )

        self.menu.addSeparator()
        self._dashboard_action = self.menu.addAction("Buka Dashboard Sesi")
        self._dashboard_action.triggered.connect(self.open_dashboard_requested.emit)

        self.menu.addSeparator()
        self._quit_action = self.menu.addAction("Keluar")
        self._quit_action.triggered.connect(self.quit_requested.emit)

        self.tray_icon.setContextMenu(self.menu)

        # Initial icon
        self.set_color_indicator("#10B981", "Bapode: Aktif")
        self.tray_icon.show()

        # State transition tracking for native OS notifications
        self._prev_ui_level: UIIntrusivenessLevel = UIIntrusivenessLevel.SILENT_OK
        self._prev_op_mode: OperatingMode = OperatingMode.ACTIVE_MONITORING

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

    def set_color_indicator(self, hex_color: str, tooltip: str) -> None:
        """Generate and set a round status icon of given color."""
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
        self.tray_icon.setToolTip(tooltip)

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

        if op_mode == OperatingMode.PAUSED_MANUAL:
            self._status_action.setText("Status: Dijeda Manual (Snooze)")
            self.set_color_indicator("#6B7280", "Bapode: Dijeda Manual")
            return
        if op_mode == OperatingMode.PAUSED_MEETING_AUTO:
            self._status_action.setText("Status: Dijeda (Rapat Fullscreen)")
            self.set_color_indicator("#6B7280", "Bapode: Mode Rapat")
            return
        if op_mode == OperatingMode.CALIBRATING:
            self._status_action.setText("Status: Sedang Kalibrasi...")
            self.set_color_indicator("#8B5CF6", "Bapode: Kalibrasi")
            return
        if op_mode == OperatingMode.NEEDS_RECALIBRATION:
            self._status_action.setText("Status: Perlu Rekalibrasi")
            self.set_color_indicator("#8B5CF6", "Bapode: Setup Drift")
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
        if ui_level == UIIntrusivenessLevel.SILENT_OK:
            self._status_action.setText(f"Status: Baik ({vis_state_str})")
            self.set_color_indicator("#10B981", f"Postur Baik ({vis_state_str})")
        elif ui_level == UIIntrusivenessLevel.AMBIENT_WARN:
            self._status_action.setText("Status: Peringatan Halus")
            self.set_color_indicator("#F59E0B", "Peringatan Postur Ambien")
        elif ui_level == UIIntrusivenessLevel.ACTIVE_OVERLAY:
            self._status_action.setText("Status: Perlu Koreksi Postur")
            self.set_color_indicator("#EF4444", "Pelanggaran Postur Aktif")
        elif ui_level == UIIntrusivenessLevel.SUPPRESSED_COOLDOWN:
            self._status_action.setText("Status: Cooldown Alert")
            self.set_color_indicator("#F97316", "Peringatan Tertahan (Cooldown)")
        elif ui_level == UIIntrusivenessLevel.MICROBREAK_PROMPT:
            self._status_action.setText("Status: Waktu Istirahat (Microbreak)")
            self.set_color_indicator("#3B82F6", "Waktu Peregangan 60s")

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
