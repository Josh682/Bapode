"""Session & Trend Dashboard and Guided Calibration Dialog (M4.3).

Reference: docs/DESIGN_SYSTEM.md & docs/specs/04-telemetry-and-schema.md
Implements:
1. Honest occlusion reporting:
   - Reference Adherence Ratio (adherent / measurable)
   - Adherence Conservative Bound (adherent / (measurable + occluded))
   - Observability Coverage percentage
2. Non-diluted telemetry bucket table displaying mean_violating_* and p95_*.
3. Guided 3-second calibration dialog with live feedback.
"""

import sys
from typing import Any

from db.manager import DatabaseManager
from ui.camera_view import CameraPreviewWidget
from ui.platform_bridge import set_application_activation_policy
from ui.qt import QtCore, QtGui, QtWidgets


class GuidedCalibrationDialog(QtWidgets.QDialog):
    """Dialog guiding user through 3-second posture calibration with real-time feedback."""

    quit_requested = QtCore.Signal()

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Kalibrasi Referensi Postur (3 Detik)")
        self.setFixedSize(520, 500)
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
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(12)

        title = QtWidgets.QLabel("Duduk Tegak & Nyaman Menatap Layar")
        title.setFont(QtGui.QFont("Arial", 14, QtGui.QFont.Weight.Bold))
        title.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)

        self.instruction_label = QtWidgets.QLabel(
            "Posisikan kepala dan bahu simetris di dalam siluet pemandu..."
        )
        self.instruction_label.setWordWrap(True)
        self.instruction_label.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        self.instruction_label.setStyleSheet("color: #9CA3AF; font-size: 13px;")
        layout.addWidget(self.instruction_label)

        # Live Camera View with Silhouette Guide
        self.preview = CameraPreviewWidget(self)
        self.preview.setMinimumSize(480, 270)
        layout.addWidget(self.preview)

        self.progress_bar = QtWidgets.QProgressBar()
        self.progress_bar.setRange(0, 15)
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(True)
        self.progress_bar.setFormat("%v / 15 Sampel Stabil")
        self.progress_bar.setStyleSheet(
            """
            QProgressBar {
                border: 1px solid #374151;
                border-radius: 8px;
                background-color: #1F2937;
                height: 24px;
                text-align: center;
                color: #FFFFFF;
            }
            QProgressBar::chunk {
                background-color: #10B981;
                border-radius: 7px;
            }
            """
        )
        layout.addWidget(self.progress_bar)

        self.feedback_label = QtWidgets.QLabel("")
        self.feedback_label.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        self.feedback_label.setStyleSheet("font-weight: bold; font-size: 13px; color: #F59E0B;")
        layout.addWidget(self.feedback_label)

        button_box = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.StandardButton.Cancel)
        button_box.rejected.connect(self.reject)
        layout.addWidget(button_box)

    def update_camera_frame(self, image: QtGui.QImage) -> None:
        """Render incoming live calibration frame."""
        if hasattr(self, "preview") and self.isVisible():
            self.preview.update_frame(image)

    def update_progress(self, current: int, total: int, is_plausible: bool, feedback: str) -> None:
        """Update live progress and guidance text."""
        self.progress_bar.setValue(current)
        if not is_plausible:
            self.feedback_label.setStyleSheet("font-weight: bold; font-size: 13px; color: #EF4444;")
            self.feedback_label.setText(feedback)
            self.progress_bar.setStyleSheet(
                self.progress_bar.styleSheet().replace("#10B981", "#EF4444")
            )
        else:
            self.feedback_label.setStyleSheet("font-weight: bold; font-size: 13px; color: #10B981;")
            self.feedback_label.setText(feedback)
            self.progress_bar.setStyleSheet(
                self.progress_bar.styleSheet().replace("#EF4444", "#10B981")
            )


class SessionTrendDashboard(QtWidgets.QMainWindow):
    """Session telemetry dashboard with honest occlusion bounds and 5-min buckets."""

    recalibrate_requested = QtCore.Signal()
    snooze_requested = QtCore.Signal(int)
    quit_requested = QtCore.Signal()

    def __init__(
        self, db_manager: DatabaseManager, parent: QtWidgets.QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self.db_manager = db_manager
        self.setWindowTitle("Bapode — Dashboard Sesi & Tren")
        self.resize(880, 640)
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

        # Auto-refresh timer
        self.refresh_timer = QtCore.QTimer(self)
        self.refresh_timer.timeout.connect(self.refresh_data)
        self.refresh_timer.start(5000)

    def _init_ui(self) -> None:
        central = QtWidgets.QWidget()
        self.setCentralWidget(central)
        root_layout = QtWidgets.QVBoxLayout(central)
        root_layout.setContentsMargins(12, 12, 12, 12)

        self.tabs = QtWidgets.QTabWidget()
        root_layout.addWidget(self.tabs)

        # Tab 1: Pratinjau Kamera (Live View)
        tab_camera = QtWidgets.QWidget()
        cam_layout = QtWidgets.QVBoxLayout(tab_camera)
        cam_layout.setContentsMargins(16, 16, 16, 16)
        cam_layout.setSpacing(12)

        self.camera_preview = CameraPreviewWidget(tab_camera)
        cam_layout.addWidget(self.camera_preview, stretch=1)

        ctrl_bar = QtWidgets.QHBoxLayout()
        ctrl_bar.setSpacing(12)

        self.btn_recalibrate = QtWidgets.QPushButton("🎯 Kalibrasi Ulang (3 Detik)")
        self.btn_recalibrate.setStyleSheet(
            """
            QPushButton {
                background-color: #10B981;
                color: #FFFFFF;
                font-weight: bold;
                padding: 10px 18px;
                border-radius: 8px;
                font-size: 13px;
            }
            QPushButton:hover { background-color: #059669; }
            """
        )
        self.btn_recalibrate.clicked.connect(self.recalibrate_requested.emit)

        self.btn_snooze = QtWidgets.QPushButton("☕ Jeda Pemantauan (Snooze 15m)")
        self.btn_snooze.setStyleSheet(
            """
            QPushButton {
                background-color: #374151;
                color: #F9FAFB;
                font-weight: bold;
                padding: 10px 18px;
                border-radius: 8px;
                font-size: 13px;
            }
            QPushButton:hover { background-color: #4B5563; }
            """
        )
        self.btn_snooze.clicked.connect(lambda: self.snooze_requested.emit(15))

        self.btn_quit = QtWidgets.QPushButton("🚪 Keluar Aplikasi")
        self.btn_quit.setStyleSheet(
            """
            QPushButton {
                background-color: #4B1D1D;
                color: #F87171;
                font-weight: bold;
                padding: 10px 18px;
                border-radius: 8px;
                font-size: 13px;
                border: 1px solid #7F1D1D;
            }
            QPushButton:hover {
                background-color: #7F1D1D;
                color: #FFFFFF;
            }
            """
        )
        self.btn_quit.clicked.connect(self.quit_requested.emit)

        ctrl_bar.addWidget(self.btn_recalibrate)
        ctrl_bar.addWidget(self.btn_snooze)
        ctrl_bar.addWidget(self.btn_quit)
        ctrl_bar.addStretch()
        cam_layout.addLayout(ctrl_bar)
        self.tabs.addTab(tab_camera, "📷 Pratinjau Kamera (Live)")

        # Tab 2: Sesi & Tren Historis
        tab_history = QtWidgets.QWidget()
        main_layout = QtWidgets.QVBoxLayout(tab_history)
        main_layout.setContentsMargins(16, 16, 16, 16)
        main_layout.setSpacing(16)

        # 1. Header Card: Adherence & Bounds
        cards_layout = QtWidgets.QHBoxLayout()
        cards_layout.setSpacing(12)

        self.card_ref_adh = self._create_metric_card("Kepatuhan Terukur", "— %", "#10B981")
        self.card_cons_bound = self._create_metric_card("Batas Konservatif", "— %", "#F59E0B")
        self.card_coverage = self._create_metric_card("Cakupan Observasi", "— %", "#3B82F6")
        self.card_alerts = self._create_metric_card("Peringatan Tampil", "0 Kali", "#EF4444")

        cards_layout.addWidget(self.card_ref_adh)
        cards_layout.addWidget(self.card_cons_bound)
        cards_layout.addWidget(self.card_coverage)
        cards_layout.addWidget(self.card_alerts)
        main_layout.addLayout(cards_layout)

        # 2. Detailed Session Durations
        durations_box = QtWidgets.QGroupBox("Ringkasan Distribusi Waktu Sesi")
        dur_layout = QtWidgets.QHBoxLayout(durations_box)
        self.lbl_measurable = QtWidgets.QLabel("Terukur: 0s")
        self.lbl_occluded = QtWidgets.QLabel("Oklusi: 0s")
        self.lbl_absent = QtWidgets.QLabel("Meninggalkan Meja: 0s")
        self.lbl_paused = QtWidgets.QLabel("Dijeda: 0s")

        for lbl in (self.lbl_measurable, self.lbl_occluded, self.lbl_absent, self.lbl_paused):
            lbl.setStyleSheet("font-size: 13px; color: #D1D5DB;")
            dur_layout.addWidget(lbl)
        main_layout.addWidget(durations_box)

        # 3. 5-Minute Telemetry Buckets Table (Free of Upright Dilution)
        buckets_box = QtWidgets.QGroupBox("Agregasi Telemetri 5-Menit (Bebas Dilusi Periode Tegak)")
        table_layout = QtWidgets.QVBoxLayout(buckets_box)

        self.table = QtWidgets.QTableWidget()
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
        self.table.setStyleSheet(
            """
            QTableWidget {
                background-color: #111827;
                alternate-background-color: #1F2937;
                gridline-color: #374151;
                color: #F9FAFB;
            }
            QHeaderView::section {
                background-color: #1F2937;
                color: #9CA3AF;
                font-weight: bold;
                padding: 4px;
            }
            """
        )
        table_layout.addWidget(self.table)
        main_layout.addWidget(buckets_box)
        self.tabs.addTab(tab_history, "Sesi & Tren Historis")

        # Tab 2: Live Telemetry & Tuning Ambang Batas (Bab 8)
        tab_live = QtWidgets.QWidget()
        live_layout = QtWidgets.QVBoxLayout(tab_live)
        live_layout.setContentsMargins(16, 16, 16, 16)
        live_layout.setSpacing(14)

        self.live_status_label = QtWidgets.QLabel(
            "Visibilitas: MENUNGGU SINYAL | Mode: — | Status: DIAM"
        )
        self.live_status_label.setStyleSheet(
            "font-size: 13px; font-weight: bold; color: #60A5FA; padding: 4px;"
        )
        live_layout.addWidget(self.live_status_label)

        self.live_table = QtWidgets.QTableWidget()
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
        self.live_table.setStyleSheet(self.table.styleSheet())
        live_layout.addWidget(self.live_table)
        self.tabs.addTab(tab_live, "Live Telemetry (Tuning Bab 8)")

    def _create_metric_card(
        self, title: str, initial_value: str, accent_hex: str
    ) -> QtWidgets.QFrame:
        card = QtWidgets.QFrame()
        card.setStyleSheet(
            f"""
            QFrame {{
                background-color: #1F2937;
                border-radius: 10px;
                border-left: 4px solid {accent_hex};
                padding: 8px;
            }}
            """
        )
        layout = QtWidgets.QVBoxLayout(card)
        layout.setContentsMargins(8, 8, 8, 8)

        lbl_title = QtWidgets.QLabel(title)
        lbl_title.setStyleSheet("color: #9CA3AF; font-size: 11px; font-weight: bold;")
        lbl_val = QtWidgets.QLabel(initial_value)
        lbl_val.setStyleSheet(f"color: {accent_hex}; font-size: 20px; font-weight: bold;")

        layout.addWidget(lbl_title)
        layout.addWidget(lbl_val)
        card.value_label = lbl_val  # type: ignore[attr-defined]
        return card

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
                    self.table.setItem(r_idx, 2, QtWidgets.QTableWidgetItem(f"{b[2] * 100.0:.1f}%"))
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

        mov_str = f"BERGERAK (v={v_norm:.3f})" if moving else f"DIAM (v={v_norm:.3f})"
        self.live_status_label.setText(f"Visibilitas: {vis} | Mode: {mode} | Status: {mov_str}")

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
            if is_v:
                stat_item.setForeground(QtGui.QBrush(QtGui.QColor("#EF4444")))
            else:
                stat_item.setForeground(QtGui.QBrush(QtGui.QColor("#10B981")))
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
