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

from PySide6 import QtCore, QtGui, QtWidgets

from db.manager import DatabaseManager


class GuidedCalibrationDialog(QtWidgets.QDialog):
    """Dialog guiding user through 3-second posture calibration with real-time feedback."""

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Kalibrasi Referensi Postur (3 Detik)")
        self.setFixedSize(480, 260)
        self._init_ui()

    def _init_ui(self) -> None:
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(16)

        title = QtWidgets.QLabel("Duduk Tegak & Nyaman Menatap Layar")
        title.setFont(QtGui.QFont("Arial", 14, QtGui.QFont.Weight.Bold))
        title.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)

        self.instruction_label = QtWidgets.QLabel(
            "Posisikan kepala dan bahu simetris di depan webcam..."
        )
        self.instruction_label.setWordWrap(True)
        self.instruction_label.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        self.instruction_label.setStyleSheet("color: #9CA3AF; font-size: 13px;")
        layout.addWidget(self.instruction_label)

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

    def __init__(
        self, db_manager: DatabaseManager, parent: QtWidgets.QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self.db_manager = db_manager
        self.setWindowTitle("Posture Guardian — Dashboard Sesi & Tren")
        self.resize(840, 580)
        self._init_ui()

        # Auto-refresh timer
        self.refresh_timer = QtCore.QTimer(self)
        self.refresh_timer.timeout.connect(self.refresh_data)
        self.refresh_timer.start(5000)

    def _init_ui(self) -> None:
        central = QtWidgets.QWidget()
        self.setCentralWidget(central)
        main_layout = QtWidgets.QVBoxLayout(central)
        main_layout.setContentsMargins(20, 20, 20, 20)
        main_layout.setSpacing(18)

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
        conn = self.db_manager.connect()
        cursor = conn.cursor()

        # Query latest session
        cursor.execute("SELECT * FROM monitoring_sessions ORDER BY session_id DESC LIMIT 1")
        row = cursor.fetchone()
        if row:
            ref_adh = row["reference_adherence_ratio"] * 100.0
            cons_bound = row["adherence_conservative_bound"] * 100.0
            cov = row["observability_coverage"] * 100.0
            alerts = row["total_alerts_shown"]

            self.card_ref_adh.value_label.setText(f"{ref_adh:.1f} %")  # type: ignore[attr-defined]
            self.card_cons_bound.value_label.setText(f"{cons_bound:.1f} %")  # type: ignore[attr-defined]
            self.card_coverage.value_label.setText(f"{cov:.1f} %")  # type: ignore[attr-defined]
            self.card_alerts.value_label.setText(f"{alerts} Kali")  # type: ignore[attr-defined]

            m_s = row["measurable_seconds"]
            occ_s = row["occluded_lower_seconds"] + row["occluded_upper_seconds"]
            abs_s = row["absent_seconds"]
            pau_s = row["paused_or_drift_seconds"]

            self.lbl_measurable.setText(f"Terukur: {m_s:.0f}s")
            self.lbl_occluded.setText(f"Oklusi: {occ_s:.0f}s")
            self.lbl_absent.setText(f"Meninggalkan Meja: {abs_s:.0f}s")
            self.lbl_paused.setText(f"Dijeda: {pau_s:.0f}s")

            # Query buckets
            session_id = row["session_id"]
            cursor.execute(
                """
                SELECT bucket_timestamp, measurable_sec_in_bucket, duty_cycle_any_violation,
                       mean_violating_fhp, p95_fhp_dev, mean_violating_neck, mean_violating_slump
                FROM session_telemetry_buckets
                WHERE session_id = ?
                ORDER BY bucket_timestamp DESC LIMIT 20
                """,
                (session_id,),
            )
            bucket_rows = cursor.fetchall()
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
