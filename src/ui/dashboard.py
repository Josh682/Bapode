"""Session & Trend Dashboard and Guided Calibration Dialog (M4.3).

Reference: docs/DESIGN_SYSTEM.md & docs/specs/04-telemetry-and-schema.md
Implements:
1. Honest occlusion reporting:
   - Reference Adherence Ratio (adherent / measurable)
   - Adherence Conservative Bound (adherent / (measurable + occluded))
   - Observability Coverage percentage
2. Non-diluted telemetry bucket table displaying mean_violating_* and p95_*.
3. Guided 3-second calibration dialog with live feedback and cohesive dark theme.
4. Real-time system, snooze countdown, drift, low-light, and proximity status banner.
"""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING, Any

from tracker.states import OperatingMode, UIIntrusivenessLevel
from ui.camera_view import CameraPreviewWidget
from ui.platform_bridge import set_application_activation_policy
from ui.qt import QtCore, QtGui, QtWidgets
from ui.theme import ColorTokens, ensure_theme_applied, refresh_widget_style

if TYPE_CHECKING:
    from db.manager import DatabaseManager


class GuidedCalibrationDialog(QtWidgets.QDialog):
    """Dialog guiding user through 3-second posture calibration with real-time feedback."""

    quit_requested = QtCore.Signal()

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        ensure_theme_applied()
        self.setWindowTitle("Kalibrasi Referensi Postur (3 Detik)")
        self.setMinimumSize(560, 560)
        self.resize(620, 600)
        self._last_plausible_state: bool | None = None
        self._init_ui()

        # Keyboard shortcuts: Ctrl+Q (Windows) / Cmd+Q (macOS) quits application
        if sys.platform == "win32":
            quit_key: QtGui.QKeySequence | QtGui.QKeySequence.StandardKey = QtGui.QKeySequence(
                "Ctrl+Q"
            )
        else:
            quit_key = QtGui.QKeySequence.StandardKey.Quit
        self.quit_shortcut = QtGui.QShortcut(quit_key, self)
        self.quit_shortcut.activated.connect(self.quit_requested.emit)

    def _init_ui(self) -> None:
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(12)

        title = QtWidgets.QLabel("Duduk Tegak & Nyaman Menatap Layar")
        title.setProperty("role", "dialogTitle")
        title.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)

        self.instruction_label = QtWidgets.QLabel(
            "Tatap tanda (+) di tengah video dan tahan posisi duduk tegak selama 3 detik."
        )
        self.instruction_label.setProperty("role", "mutedText")
        self.instruction_label.setWordWrap(True)
        self.instruction_label.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.instruction_label)

        # 3-step live readiness chips above preview
        chips_row = QtWidgets.QHBoxLayout()
        chips_row.setSpacing(8)
        self.chip_visibility = self._make_step_chip("1. Wajah & Bahu")
        self.chip_alignment = self._make_step_chip("2. Posisi Lurus")
        self.chip_stability = self._make_step_chip("3. Tahan 3 Detik")
        chips_row.addWidget(self.chip_visibility)
        chips_row.addWidget(self.chip_alignment)
        chips_row.addWidget(self.chip_stability)
        layout.addLayout(chips_row)

        # Live Camera View with Silhouette Guide
        self.preview = CameraPreviewWidget(self)
        self.preview.setMinimumSize(480, 270)
        layout.addWidget(self.preview, stretch=1)

        self.progress_bar = QtWidgets.QProgressBar()
        self.progress_bar.setRange(0, 15)
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(True)
        self.progress_bar.setFormat("%v / 15 Sampel Stabil")
        self.progress_bar.setProperty("calibState", "ok")
        layout.addWidget(self.progress_bar)

        self.feedback_label = QtWidgets.QLabel("Menyiapkan kamera...")
        self.feedback_label.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        self.feedback_label.setWordWrap(True)
        self.feedback_label.setStyleSheet(
            f"font-weight: 600; font-size: 13px; color: {ColorTokens.STATUS_WARN};"
        )
        layout.addWidget(self.feedback_label)

        button_box = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.StandardButton.Cancel)
        cancel_btn = button_box.button(QtWidgets.QDialogButtonBox.StandardButton.Cancel)
        if cancel_btn is not None:
            cancel_btn.setText("Batal")
        button_box.rejected.connect(self.reject)
        layout.addWidget(button_box)

    @staticmethod
    def _make_step_chip(text: str) -> QtWidgets.QLabel:
        chip = QtWidgets.QLabel(text)
        chip.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        chip.setStyleSheet(
            f"background-color: {ColorTokens.BG_ELEVATED}; "
            f"border: 1px solid {ColorTokens.BORDER_SUBTLE}; "
            f"border-radius: 6px; padding: 5px 8px; font-size: 12px; font-weight: 600; "
            f"color: {ColorTokens.TEXT_SECONDARY};"
        )
        return chip

    @staticmethod
    def _set_chip_state(chip: QtWidgets.QLabel, state: str) -> None:
        if state == "ok":
            bg, border, fg = ColorTokens.TINT_OK, ColorTokens.STATUS_OK, ColorTokens.TEXT_PRIMARY
        elif state == "progress":
            bg, border, fg = (
                ColorTokens.TINT_INFO,
                ColorTokens.STATUS_INFO,
                ColorTokens.TEXT_PRIMARY,
            )
        elif state == "error":
            bg, border, fg = (
                ColorTokens.TINT_WARN,
                ColorTokens.STATUS_WARN,
                ColorTokens.TEXT_PRIMARY,
            )
        else:
            bg, border, fg = (
                ColorTokens.BG_ELEVATED,
                ColorTokens.BORDER_SUBTLE,
                ColorTokens.TEXT_SECONDARY,
            )
        chip.setStyleSheet(
            f"background-color: {bg}; border: 1px solid {border}; border-radius: 6px; "
            f"padding: 5px 8px; font-size: 12px; font-weight: 600; color: {fg};"
        )

    def showEvent(self, event: QtGui.QShowEvent) -> None:  # noqa: N802
        """Reset visual indicators cleanly whenever the dialog opens."""
        super().showEvent(event)
        self._last_plausible_state = None
        self.progress_bar.setValue(0)
        self.progress_bar.setProperty("calibState", "ok")
        refresh_widget_style(self.progress_bar)
        self.feedback_label.setText("Menyiapkan kamera... Duduk tegak & tatap titik tengah.")
        self.feedback_label.setStyleSheet(
            f"font-weight: 600; font-size: 13px; color: {ColorTokens.STATUS_WARN};"
        )
        self._set_chip_state(self.chip_visibility, "idle")
        self._set_chip_state(self.chip_alignment, "idle")
        self._set_chip_state(self.chip_stability, "idle")

    def update_camera_frame(self, image: QtGui.QImage) -> None:
        """Render incoming live calibration frame."""
        if hasattr(self, "preview") and self.isVisible():
            self.preview.update_frame(image)

    def update_progress(self, current: int, total: int, is_plausible: bool, feedback: str) -> None:
        """Update live progress, readiness chips, and guidance text."""
        if total > 0 and self.progress_bar.maximum() != total:
            self.progress_bar.setRange(0, total)
        self.progress_bar.setValue(current)
        self.feedback_label.setText(feedback)

        lower_fb = feedback.lower()
        missing_vis = any(
            kw in lower_fb
            for kw in ("bahu tidak terlihat", "wajah tidak terdeteksi", "belum terlihat")
        )
        noise_fail = any(
            kw in lower_fb for kw in ("belum stabil", "terlalu redup", "jitter")
        )
        if is_plausible:
            self._set_chip_state(self.chip_visibility, "ok")
            self._set_chip_state(self.chip_alignment, "ok")
            step3_state = "ok" if current >= total else ("progress" if current > 0 else "idle")
            self._set_chip_state(self.chip_stability, step3_state)
        elif missing_vis:
            self._set_chip_state(self.chip_visibility, "error")
            self._set_chip_state(self.chip_alignment, "idle")
            self._set_chip_state(self.chip_stability, "idle")
        elif noise_fail:
            self._set_chip_state(self.chip_visibility, "ok")
            self._set_chip_state(self.chip_alignment, "ok")
            self._set_chip_state(self.chip_stability, "error")
        else:
            self._set_chip_state(self.chip_visibility, "ok")
            self._set_chip_state(self.chip_alignment, "error")
            self._set_chip_state(self.chip_stability, "idle")

        if is_plausible != self._last_plausible_state:
            self._last_plausible_state = is_plausible
            if is_plausible:
                self.feedback_label.setStyleSheet(
                    f"font-weight: 600; font-size: 13px; color: {ColorTokens.STATUS_OK};"
                )
                self.progress_bar.setProperty("calibState", "ok")
            else:
                self.feedback_label.setStyleSheet(
                    f"font-weight: 600; font-size: 13px; color: {ColorTokens.STATUS_ALERT};"
                )
                self.progress_bar.setProperty("calibState", "error")
            refresh_widget_style(self.progress_bar)


class SessionTrendDashboard(QtWidgets.QMainWindow):
    """Session telemetry dashboard with honest occlusion bounds and 5-min buckets."""

    recalibrate_requested = QtCore.Signal()
    snooze_requested = QtCore.Signal(int)
    quit_requested = QtCore.Signal()

    def __init__(
        self, db_manager: DatabaseManager, parent: QtWidgets.QWidget | None = None
    ) -> None:
        super().__init__(parent)
        ensure_theme_applied()
        self.db_manager = db_manager
        self.setWindowTitle("Bapode — Dashboard Sesi & Tren")
        self.resize(920, 680)
        self.setMinimumSize(760, 560)

        self._op_mode = OperatingMode.ACTIVE_MONITORING
        self._vis_state_str = "MEASURABLE"
        self._ui_level = UIIntrusivenessLevel.SILENT_OK
        self._is_snoozed = False
        self._snooze_remaining_s = 0.0
        self._low_light_warning = False
        self._low_light_message = ""
        self._extreme_close_proximity = False
        self._drift_reason = ""

        self._init_ui()

        # Keyboard shortcut: Ctrl+Q (Windows) / Cmd+Q (macOS) quits application
        if sys.platform == "win32":
            quit_seq: QtGui.QKeySequence | QtGui.QKeySequence.StandardKey = QtGui.QKeySequence(
                "Ctrl+Q"
            )
        else:
            quit_seq = QtGui.QKeySequence.StandardKey.Quit
        self.quit_shortcut = QtGui.QShortcut(quit_seq, self)
        self.quit_shortcut.setContext(QtCore.Qt.ShortcutContext.ApplicationShortcut)
        self.quit_shortcut.activated.connect(self.quit_requested.emit)

        # Native Menu Bar with Quit action
        menu_bar = self.menuBar()
        app_menu = menu_bar.addMenu("Aplikasi")
        quit_action = app_menu.addAction("Keluar Bapode")
        quit_action.setShortcut(quit_seq)
        quit_action.triggered.connect(self.quit_requested.emit)

        # Auto-refresh timer (active only while dashboard is visible)
        self.refresh_timer = QtCore.QTimer(self)
        self.refresh_timer.setInterval(5000)
        self.refresh_timer.timeout.connect(self.refresh_data)

    def _init_ui(self) -> None:
        central = QtWidgets.QWidget()
        self.setCentralWidget(central)
        root_layout = QtWidgets.QVBoxLayout(central)
        root_layout.setContentsMargins(16, 14, 16, 16)
        root_layout.setSpacing(12)

        # Top real-time system status strip
        self.status_strip = QtWidgets.QFrame()
        self.status_strip.setProperty("role", "statusStrip")
        strip_layout = QtWidgets.QHBoxLayout(self.status_strip)
        strip_layout.setContentsMargins(14, 10, 14, 10)
        strip_layout.setSpacing(10)

        self.status_dot = QtWidgets.QLabel()
        self.status_dot.setFixedSize(10, 10)
        self._set_dot_color(ColorTokens.STATUS_OK)
        strip_layout.addWidget(self.status_dot)

        self.system_status_label = QtWidgets.QLabel("Status: Memulai pemantauan...")
        self.system_status_label.setStyleSheet("font-weight: 600; font-size: 13px;")
        strip_layout.addWidget(self.system_status_label, stretch=1)

        self.btn_resume_snooze = QtWidgets.QPushButton("Lanjutkan Pemantauan")
        self.btn_resume_snooze.setProperty("variant", "primary")
        self.btn_resume_snooze.setVisible(False)
        self.btn_resume_snooze.clicked.connect(lambda: self.snooze_requested.emit(0))
        strip_layout.addWidget(self.btn_resume_snooze)

        root_layout.addWidget(self.status_strip)

        # Environment / Drift alert banner (hidden unless an actionable condition exists)
        self.env_banner = QtWidgets.QFrame()
        self.env_banner.setProperty("role", "envBanner")
        self.env_banner.setProperty("severity", "warning")
        self.env_banner.setVisible(False)
        banner_layout = QtWidgets.QHBoxLayout(self.env_banner)
        banner_layout.setContentsMargins(14, 9, 14, 9)
        banner_layout.setSpacing(10)

        self.env_banner_label = QtWidgets.QLabel("")
        self.env_banner_label.setWordWrap(True)
        self.env_banner_label.setStyleSheet(
            f"font-weight: 600; font-size: 13px; color: {ColorTokens.TEXT_PRIMARY};"
        )
        banner_layout.addWidget(self.env_banner_label, stretch=1)

        self.btn_banner_recalibrate = QtWidgets.QPushButton("Kalibrasi Ulang (3 dtk)")
        self.btn_banner_recalibrate.setProperty("variant", "accentInline")
        self.btn_banner_recalibrate.setVisible(False)
        self.btn_banner_recalibrate.clicked.connect(self.recalibrate_requested.emit)
        banner_layout.addWidget(self.btn_banner_recalibrate)

        root_layout.addWidget(self.env_banner)

        self.tabs = QtWidgets.QTabWidget()
        root_layout.addWidget(self.tabs, stretch=1)

        # Tab 1: Pratinjau Kamera (Live View)
        tab_camera = QtWidgets.QWidget()
        cam_layout = QtWidgets.QVBoxLayout(tab_camera)
        cam_layout.setContentsMargins(16, 16, 16, 16)
        cam_layout.setSpacing(12)

        self.camera_preview = CameraPreviewWidget(tab_camera)
        cam_layout.addWidget(self.camera_preview, stretch=1)

        ctrl_bar = QtWidgets.QHBoxLayout()
        ctrl_bar.setSpacing(10)

        self.btn_recalibrate = QtWidgets.QPushButton("Kalibrasi Ulang (3 Detik)")
        self.btn_recalibrate.setProperty("variant", "primary")
        self.btn_recalibrate.clicked.connect(self.recalibrate_requested.emit)

        self.btn_snooze = QtWidgets.QPushButton("Jeda Pemantauan (15m)")
        self.btn_snooze.clicked.connect(lambda: self.snooze_requested.emit(15))

        self.btn_quit = QtWidgets.QPushButton("Keluar Aplikasi")
        self.btn_quit.setProperty("variant", "danger")
        self.btn_quit.clicked.connect(self.quit_requested.emit)

        ctrl_bar.addWidget(self.btn_recalibrate)
        ctrl_bar.addWidget(self.btn_snooze)
        ctrl_bar.addStretch()
        ctrl_bar.addWidget(self.btn_quit)
        cam_layout.addLayout(ctrl_bar)
        self.tabs.addTab(tab_camera, "Pratinjau Kamera (Live)")

        # Tab 2: Sesi & Tren Historis
        tab_history = QtWidgets.QWidget()
        main_layout = QtWidgets.QVBoxLayout(tab_history)
        main_layout.setContentsMargins(16, 16, 16, 16)
        main_layout.setSpacing(16)

        # 1. Header Cards: Adherence & Bounds
        cards_layout = QtWidgets.QHBoxLayout()
        cards_layout.setSpacing(12)

        self.card_ref_adh = self._create_metric_card(
            "Kepatuhan Terukur", "— %", ColorTokens.STATUS_OK
        )
        self.card_cons_bound = self._create_metric_card(
            "Batas Konservatif", "— %", ColorTokens.STATUS_WARN
        )
        self.card_coverage = self._create_metric_card(
            "Cakupan Observasi", "— %", ColorTokens.STATUS_INFO
        )
        self.card_alerts = self._create_metric_card(
            "Peringatan Tampil", "0 Kali", ColorTokens.STATUS_ALERT
        )

        cards_layout.addWidget(self.card_ref_adh)
        cards_layout.addWidget(self.card_cons_bound)
        cards_layout.addWidget(self.card_coverage)
        cards_layout.addWidget(self.card_alerts)
        main_layout.addLayout(cards_layout)

        # 2. Detailed Session Durations
        durations_box = QtWidgets.QGroupBox("Ringkasan Distribusi Waktu Sesi")
        dur_layout = QtWidgets.QHBoxLayout(durations_box)
        dur_layout.setSpacing(16)
        self.lbl_measurable = QtWidgets.QLabel("Terukur: 0s")
        self.lbl_occluded = QtWidgets.QLabel("Oklusi: 0s")
        self.lbl_absent = QtWidgets.QLabel("Meninggalkan Meja: 0s")
        self.lbl_paused = QtWidgets.QLabel("Dijeda: 0s")

        for lbl in (self.lbl_measurable, self.lbl_occluded, self.lbl_absent, self.lbl_paused):
            lbl.setProperty("role", "durationItem")
            dur_layout.addWidget(lbl)
        main_layout.addWidget(durations_box)

        # 3. 5-Minute Telemetry Buckets Table (Free of Upright Dilution)
        buckets_box = QtWidgets.QGroupBox("Agregasi Telemetri 5-Menit (Bebas Dilusi Periode Tegak)")
        table_layout = QtWidgets.QVBoxLayout(buckets_box)

        self.table = QtWidgets.QTableWidget()
        self.table.setAlternatingRowColors(True)
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setColumnCount(7)
        self.table.setHorizontalHeaderLabels(
            [
                "Waktu Bucket",
                "Waktu Terukur",
                "Duty Cycle (%)",
                "Mean FHP Melanggar",
                "P95 FHP",
                "Mean Neck Melanggar",
                "Mean Slump Melanggar",
            ]
        )
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.horizontalHeader().setSectionResizeMode(
            QtWidgets.QHeaderView.ResizeMode.ResizeToContents
        )
        table_layout.addWidget(self.table)
        main_layout.addWidget(buckets_box, stretch=1)
        self.tabs.addTab(tab_history, "Sesi && Tren Historis")

        # Tab 3: Live Telemetry & Tuning Ambang Batas (Bab 8)
        tab_live = QtWidgets.QWidget()
        live_layout = QtWidgets.QVBoxLayout(tab_live)
        live_layout.setContentsMargins(16, 16, 16, 16)
        live_layout.setSpacing(14)

        self.live_status_label = QtWidgets.QLabel(
            "Visibilitas: MENUNGGU SINYAL | Mode: — | Status: DIAM"
        )
        self.live_status_label.setProperty("role", "liveStatus")
        live_layout.addWidget(self.live_status_label)

        self.live_table = QtWidgets.QTableWidget()
        self.live_table.setAlternatingRowColors(True)
        self.live_table.setEditTriggers(QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
        self.live_table.setSelectionBehavior(
            QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows
        )
        self.live_table.setColumnCount(7)
        self.live_table.setHorizontalHeaderLabels(
            [
                "Metrik Postur",
                "d_hat(t)",
                "sigma(t) MAD",
                "tau_enter(t)",
                "tau_exit",
                "Status",
                "Ember Debounce (15s)",
            ]
        )
        self.live_table.setRowCount(5)
        self.live_table.horizontalHeader().setStretchLastSection(True)
        self.live_table.horizontalHeader().setSectionResizeMode(
            QtWidgets.QHeaderView.ResizeMode.ResizeToContents
        )
        live_layout.addWidget(self.live_table, stretch=1)
        self.tabs.addTab(tab_live, "Live Telemetry (Tuning Bab 8)")

    def _set_dot_color(self, hex_color: str) -> None:
        self.status_dot.setStyleSheet(
            f"background-color: {hex_color}; border-radius: 5px;"
        )

    def _create_metric_card(
        self, title: str, initial_value: str, accent_hex: str
    ) -> QtWidgets.QFrame:
        card = QtWidgets.QFrame()
        card.setProperty("role", "metricCard")
        layout = QtWidgets.QVBoxLayout(card)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(6)

        header_row = QtWidgets.QHBoxLayout()
        header_row.setSpacing(6)
        dot = QtWidgets.QLabel()
        dot.setFixedSize(8, 8)
        dot.setStyleSheet(f"background-color: {accent_hex}; border-radius: 4px;")
        lbl_title = QtWidgets.QLabel(title)
        lbl_title.setProperty("role", "metricTitle")
        header_row.addWidget(dot)
        header_row.addWidget(lbl_title)
        header_row.addStretch()

        lbl_val = QtWidgets.QLabel(initial_value)
        lbl_val.setStyleSheet(
            f"color: {accent_hex}; font-size: 22px; font-weight: 700;"
        )

        layout.addLayout(header_row)
        layout.addWidget(lbl_val)
        card.value_label = lbl_val  # type: ignore[attr-defined]
        return card

    def showEvent(self, event: QtGui.QShowEvent) -> None:  # noqa: N802
        """Start periodic DB refresh and update immediately when window becomes visible."""
        super().showEvent(event)
        self.refresh_data()
        if not self.refresh_timer.isActive():
            self.refresh_timer.start()

    def hideEvent(self, event: QtGui.QHideEvent) -> None:  # noqa: N802
        """Pause periodic DB polling while window is hidden in tray."""
        if self.refresh_timer.isActive():
            self.refresh_timer.stop()
        super().hideEvent(event)

    @QtCore.Slot(str, str, str, object)
    def update_system_status(
        self,
        op_mode_str: str,
        vis_state_str: str,
        ui_level_str: str,
        flags: object = None,
    ) -> None:
        """Update top status strip from 3-axis state_changed signal."""
        del flags
        try:
            self._op_mode = OperatingMode(op_mode_str)
        except ValueError:
            self._op_mode = OperatingMode.ACTIVE_MONITORING
        try:
            self._ui_level = UIIntrusivenessLevel(ui_level_str)
        except ValueError:
            self._ui_level = UIIntrusivenessLevel.SILENT_OK
        self._vis_state_str = vis_state_str

        if self._op_mode != OperatingMode.NEEDS_RECALIBRATION:
            self._drift_reason = ""
        if self._op_mode != OperatingMode.PAUSED_MANUAL:
            self._is_snoozed = False
            self._snooze_remaining_s = 0.0

        self._render_status_banners()

    @QtCore.Slot(bool, float)
    def update_snooze_status(self, is_snoozed: bool, remaining_s: float) -> None:
        """Update live snooze countdown inside status strip."""
        self._is_snoozed = is_snoozed
        self._snooze_remaining_s = max(0.0, remaining_s)
        self._render_status_banners()

    @QtCore.Slot(bool, bool, str)
    def update_environment_status(
        self,
        low_light: bool,
        extreme_close: bool,
        message: str = "",
    ) -> None:
        """Surface low-light accuracy warning or extreme close proximity alert."""
        self._low_light_warning = low_light
        self._low_light_message = message
        self._extreme_close_proximity = extreme_close
        self._render_status_banners()

    @QtCore.Slot(str)
    def on_drift_detected(self, reason: str) -> None:
        """Display setup drift explanation banner."""
        self._drift_reason = reason
        self._op_mode = OperatingMode.NEEDS_RECALIBRATION
        self._render_status_banners()

    def _render_status_banners(self) -> None:
        """Synchronize top status strip and environment/drift banner."""
        self.btn_resume_snooze.setVisible(self._op_mode == OperatingMode.PAUSED_MANUAL)

        if self._op_mode == OperatingMode.PAUSED_MANUAL:
            self._set_dot_color(ColorTokens.STATUS_PAUSED)
            if self._is_snoozed and self._snooze_remaining_s > 0:
                mins, secs = divmod(int(self._snooze_remaining_s), 60)
                self.system_status_label.setText(
                    f"Pemantauan Dijeda (Snooze) — {mins:02d}:{secs:02d} tersisa"
                )
            else:
                self.system_status_label.setText("Pemantauan Dijeda Manual (Snooze)")
        elif self._op_mode == OperatingMode.PAUSED_MEETING_AUTO:
            self._set_dot_color(ColorTokens.STATUS_PAUSED)
            self.system_status_label.setText("Pemantauan Dijeda Otomatis (Mode Rapat Fullscreen)")
        elif self._op_mode == OperatingMode.CALIBRATING:
            self._set_dot_color(ColorTokens.STATUS_CALIBRATION)
            self.system_status_label.setText("Sedang Kalibrasi Referensi Postur (3 Detik)...")
        elif self._op_mode == OperatingMode.NEEDS_RECALIBRATION:
            self._set_dot_color(ColorTokens.STATUS_CALIBRATION)
            self.system_status_label.setText(
                "Perlu Kalibrasi Ulang — Pemantauan ditangguhkan sementara"
            )
        else:
            dot_map = {
                UIIntrusivenessLevel.SILENT_OK: ColorTokens.STATUS_OK,
                UIIntrusivenessLevel.AMBIENT_WARN: ColorTokens.STATUS_WARN,
                UIIntrusivenessLevel.ACTIVE_OVERLAY: ColorTokens.STATUS_ALERT,
                UIIntrusivenessLevel.SUPPRESSED_COOLDOWN: ColorTokens.STATUS_COOLDOWN,
                UIIntrusivenessLevel.MICROBREAK_PROMPT: ColorTokens.STATUS_INFO,
            }
            self._set_dot_color(dot_map.get(self._ui_level, ColorTokens.STATUS_OK))
            self.system_status_label.setText(
                f"Pemantauan Aktif • Visibilitas: {self._vis_state_str} • "
                f"Level: {self._ui_level.value}"
            )

        # Priority for actionable banner: Setup Drift > Extreme Close Proximity > Low Light
        severity = ""
        banner_text = ""
        show_recalib_btn = False
        if self._op_mode == OperatingMode.NEEDS_RECALIBRATION:
            severity = "calibration"
            banner_text = (
                self._drift_reason
                or "Posisi duduk atau sudut layar bergeser. Lakukan kalibrasi ulang 3 detik."
            )
            show_recalib_btn = True
        elif self._extreme_close_proximity and self._op_mode == OperatingMode.ACTIVE_MONITORING:
            severity = "alert"
            banner_text = (
                "Jarak wajah terlalu dekat ke layar (bahu di luar bingkai >= 15 dtk). "
                "Mundurkan posisi duduk Anda."
            )
        elif self._low_light_warning and self._op_mode == OperatingMode.ACTIVE_MONITORING:
            severity = "warning"
            banner_text = (
                self._low_light_message or "Kualitas Cahaya Kamera Buruk — Akurasi Menurun"
            )

        if banner_text:
            self.env_banner_label.setText(banner_text)
            self.btn_banner_recalibrate.setVisible(show_recalib_btn)
            if self.env_banner.property("severity") != severity:
                self.env_banner.setProperty("severity", severity)
                refresh_widget_style(self.env_banner)
            self.env_banner.setVisible(True)
        else:
            self.env_banner.setVisible(False)
            self.btn_banner_recalibrate.setVisible(False)

    def refresh_data(self) -> None:
        """Query SQLite WAL and update all views."""
        latest_session = self.db_manager.get_latest_session()
        if latest_session:
            ref_adh = latest_session.reference_adherence_ratio * 100.0
            cons_bound = latest_session.adherence_conservative_bound * 100.0
            cov = latest_session.observability_coverage * 100.0
            alerts = latest_session.total_alerts_shown

            self.card_ref_adh.value_label.setText(f"{ref_adh:.1f} %")  # type: ignore[attr-defined]
            self.card_cons_bound.value_label.setText(f"{cons_bound:.1f} %")  # type: ignore[attr-defined]
            self.card_coverage.value_label.setText(f"{cov:.1f} %")  # type: ignore[attr-defined]
            self.card_alerts.value_label.setText(f"{alerts} Kali")  # type: ignore[attr-defined]

            m_s = latest_session.measurable_seconds
            occ_s = latest_session.occluded_lower_seconds + latest_session.occluded_upper_seconds
            abs_s = latest_session.absent_seconds
            pau_s = latest_session.paused_or_drift_seconds

            self.lbl_measurable.setText(f"Terukur: {m_s:.0f}s")
            self.lbl_occluded.setText(f"Oklusi: {occ_s:.0f}s")
            self.lbl_absent.setText(f"Meninggalkan Meja: {abs_s:.0f}s")
            self.lbl_paused.setText(f"Dijeda: {pau_s:.0f}s")

            # Query buckets
            if latest_session.session_id is not None:
                bucket_rows = self.db_manager.get_recent_buckets(
                    latest_session.session_id, limit=20
                )
                self.table.setRowCount(len(bucket_rows))

                for r_idx, b in enumerate(bucket_rows):
                    self.table.setItem(r_idx, 0, QtWidgets.QTableWidgetItem(str(b[0])))
                    self.table.setItem(r_idx, 1, QtWidgets.QTableWidgetItem(f"{b[1]:.0f}s"))
                    self.table.setItem(
                        r_idx, 2, QtWidgets.QTableWidgetItem(f"{b[2] * 100.0:.1f}%")
                    )
                    fhp_str = f"{b[3]:.3f}" if b[3] is not None else "—"
                    self.table.setItem(r_idx, 3, QtWidgets.QTableWidgetItem(fhp_str))
                    self.table.setItem(r_idx, 4, QtWidgets.QTableWidgetItem(f"{b[4]:.3f}"))
                    neck_str = f"{b[5]:.3f}" if b[5] is not None else "—"
                    self.table.setItem(r_idx, 5, QtWidgets.QTableWidgetItem(neck_str))
                    slump_str = f"{b[6]:.3f}" if b[6] is not None else "—"
                    self.table.setItem(r_idx, 6, QtWidgets.QTableWidgetItem(slump_str))

    def update_live_telemetry(self, data: dict[str, Any]) -> None:
        """Update live telemetry tab with real-time deviations and thresholds."""
        if not hasattr(self, "live_table"):
            return

        vis = data.get("vis_state", "UNKNOWN")
        mode = data.get("op_mode", "UNKNOWN")
        moving = data.get("is_moving", False)
        v_norm = data.get("v_norm", 0.0)
        low_light = bool(data.get("low_light_warning", False))
        extreme_close = bool(data.get("extreme_close_proximity", False))

        mov_str = f"BERGERAK (v={v_norm:.3f})" if moving else f"DIAM (v={v_norm:.3f})"
        extras: list[str] = []
        if low_light:
            extras.append("CAHAYA REDUP")
        if extreme_close:
            extras.append("TERLALU DEKAT")
        extra_str = f" | Peringatan: {', '.join(extras)}" if extras else ""

        self.live_status_label.setText(
            f"Visibilitas: {vis} | Mode: {mode} | Status: {mov_str}{extra_str}"
        )

        d_hat = data.get("d_hat", {})
        sigmas = data.get("sigmas", {})
        tau_enter = data.get("tau_enter", {})
        tau_exit = data.get("tau_exit", {})
        violations = data.get("violations", {})
        buckets = data.get("buckets", {})

        metrics = [
            ("fhp", "HEAD_FORWARD"),
            ("neck", "TORSO_NECK_COMP"),
            ("slump", "SHOULDER_SLUMP"),
            ("pitch", "GAZE_DOWN_PITCH"),
            ("tilt", "CORONAL_ASYMMETRY"),
        ]

        for r_idx, (key, label) in enumerate(metrics):
            val = d_hat.get(key, 0.0)
            sig = sigmas.get(key, 0.0)
            t_ent = tau_enter.get(key, 0.0)
            t_ex = tau_exit.get(key, 0.0)
            is_v = violations.get(key, False)
            b_s = buckets.get(key, 0.0)

            is_deg = key in ("pitch", "tilt")
            val_str = f"{val:+.1f}°" if is_deg else f"{val:+.4f}"
            sig_str = f"{sig:.2f}°" if is_deg else f"{sig:.4f}"
            ent_str = f"{t_ent:.1f}°" if is_deg else f"{t_ent:.3f}"
            ex_str = f"{t_ex:.1f}°" if is_deg else f"{t_ex:.3f}"
            status_str = "MELANGGAR" if is_v else "OK"
            bar_fill = int(min(10, max(0, (b_s / 15.0) * 10)))
            b_str = f"[{'#' * bar_fill}{'-' * (10 - bar_fill)}] {b_s:4.1f}s"

            self.live_table.setItem(r_idx, 0, QtWidgets.QTableWidgetItem(label))
            self.live_table.setItem(r_idx, 1, QtWidgets.QTableWidgetItem(val_str))
            self.live_table.setItem(r_idx, 2, QtWidgets.QTableWidgetItem(sig_str))
            self.live_table.setItem(r_idx, 3, QtWidgets.QTableWidgetItem(ent_str))
            self.live_table.setItem(r_idx, 4, QtWidgets.QTableWidgetItem(ex_str))

            stat_item = QtWidgets.QTableWidgetItem(status_str)
            color_hex = ColorTokens.STATUS_ALERT if is_v else ColorTokens.STATUS_OK
            stat_item.setForeground(QtGui.QBrush(QtGui.QColor(color_hex)))
            self.live_table.setItem(r_idx, 5, stat_item)
            self.live_table.setItem(r_idx, 6, QtWidgets.QTableWidgetItem(b_str))

    def update_camera_frame(self, image: QtGui.QImage) -> None:
        """Receive live annotated frame from VisionTrackerWorker."""
        if hasattr(self, "camera_preview") and self.isVisible():
            self.camera_preview.update_frame(image)

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:  # noqa: N802
        """Handle window close event without terminating background tracking."""
        set_application_activation_policy(is_accessory=True)
        if sys.platform == "win32":
            print(
                "\n[Bapode] Jendela Dashboard ditutup. "
                "Aplikasi tetap memantau di latar belakang (System Tray kanan bawah).\n"
                "  • Untuk keluar aplikasi: Tekan Ctrl+Q, klik kanan ikon System Tray "
                "lalu pilih 'Keluar', atau tekan Ctrl+C di terminal.\n"
            )
        else:
            print(
                "\n[Bapode] Jendela Dashboard ditutup. "
                "Aplikasi tetap memantau di latar belakang (Menu Bar macOS kanan atas).\n"
                "  • Untuk keluar aplikasi: Tekan Cmd+Q, klik tombol 'Keluar' pada Menu Bar, "
                "atau tekan Ctrl+C di terminal.\n"
            )
        super().closeEvent(event)
