"""Application Entry Point and 4-Thread Coordinator for Posture Guardian v3.0.

Reference: docs/ARCHITECTURE.md & docs/DESIGN_SYSTEM.md
Coordinates:
- Thread 3: Camera Capture Worker (5 FPS)
- Worker Thread 1: Sequential Pose+Face & Tracker Engine
- Worker Thread 2: Asynchronous SQLite WAL Writer
- Main Thread: PySide6 GUI (Tray Icon, HUD Overlay, Dashboard)
"""

import argparse
import signal
import sys
from typing import Any

from camera.reader import CameraReader
from db.manager import DatabaseManager
from db.worker import DatabaseWorker
from ui.dashboard import GuidedCalibrationDialog, SessionTrendDashboard
from ui.overlay import HUDOverlay
from ui.platform_bridge import PlatformPowerListener
from ui.qt import QtCore, QtWidgets
from ui.tray import SystemTrayManager
from vision.detector import SequentialLandmarkDetector
from vision.worker import VisionTrackerWorker


class PostureGuardianApp(QtCore.QObject):
    """Main application lifecycle and cross-thread coordinator."""

    def __init__(
        self,
        db_path: str = "posture_guardian.db",
        camera_index: int = 0,
        mock_camera: bool = False,
        headless: bool = False,
        calibrate_thresholds: bool = False,
        show_dashboard: bool = False,
        start_calibration: bool = False,
        parent: QtCore.QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.db_path = db_path
        self.camera_index = camera_index
        self.mock_camera = mock_camera
        self.headless = headless
        self.calibrate_thresholds = calibrate_thresholds
        self.show_dashboard = show_dashboard
        self.start_calibration = start_calibration

        # 1. Initialize SQLite Database & Worker Thread 2
        self.db_manager = DatabaseManager(self.db_path)
        self.db_manager.initialize_schema()
        self.db_worker = DatabaseWorker(self.db_manager)

        # 2. Initialize Camera Capture Thread 3
        self.camera_reader = CameraReader(
            camera_index=self.camera_index,
            target_fps=5.0,
            mock_mode=self.mock_camera,
        )

        # 3. Initialize Vision & Tracker Worker Thread 1
        self.detector = SequentialLandmarkDetector(use_mock=self.mock_camera)
        self.vision_worker = VisionTrackerWorker(
            frame_queue=self.camera_reader.latest_frame_queue,
            db_worker=self.db_worker,
            detector=self.detector,
        )

        # 4. Initialize Platform Power Listener
        self.power_listener = PlatformPowerListener(self)
        self.power_listener.system_will_sleep.connect(self.vision_worker.on_os_sleep)
        self.power_listener.system_did_wake.connect(self.vision_worker.on_os_wake)

        # 5. Connect live telemetry for threshold tuning
        if self.calibrate_thresholds:
            self.vision_worker.live_telemetry.connect(self._render_live_terminal_telemetry)

        # 6. Initialize UI (Main Thread)
        self.hud_overlay: HUDOverlay | None = None
        self.tray_manager: SystemTrayManager | None = None
        self.dashboard_window: SessionTrendDashboard | None = None
        self.calib_dialog: GuidedCalibrationDialog | None = None

        if not self.headless:
            self._init_ui_components()

    def _init_ui_components(self) -> None:
        """Create and wire Main Thread UI widgets."""
        self.hud_overlay = HUDOverlay()
        self.tray_manager = SystemTrayManager(self)
        self.dashboard_window = SessionTrendDashboard(self.db_manager)
        self.calib_dialog = GuidedCalibrationDialog()

        # Connect Worker 1 signals to UI
        self.vision_worker.state_changed.connect(self.tray_manager.update_status)
        self.vision_worker.state_changed.connect(
            lambda op, vis, ui, flags: (
                self.hud_overlay.update_state(ui) if self.hud_overlay else None
            )
        )
        self.vision_worker.calibration_progress.connect(self.calib_dialog.update_progress)
        self.vision_worker.calibration_completed.connect(self._on_calibration_completed)
        self.vision_worker.live_telemetry.connect(self.dashboard_window.update_live_telemetry)
        self.vision_worker.frame_annotated.connect(self.dashboard_window.update_camera_frame)
        self.vision_worker.frame_annotated.connect(self.calib_dialog.update_camera_frame)

        # Connect Dashboard actions
        self.dashboard_window.recalibrate_requested.connect(self._start_calibration_flow)
        self.dashboard_window.snooze_requested.connect(self.vision_worker.set_snooze)
        self.dashboard_window.quit_requested.connect(self.shutdown)

        # Connect Calibration dialog actions
        self.calib_dialog.quit_requested.connect(self.shutdown)

        # Connect Tray actions
        self.tray_manager.recalibrate_requested.connect(self._start_calibration_flow)
        self.tray_manager.snooze_requested.connect(self.vision_worker.set_snooze)
        self.tray_manager.open_dashboard_requested.connect(self._open_dashboard)
        self.tray_manager.quit_requested.connect(self.shutdown)

        # Notify user that app is running in Menu Bar
        self.tray_manager.show_startup_notification()

    def _start_calibration_flow(self) -> None:
        """Trigger calibration and open guided dialog."""
        self.vision_worker.trigger_calibration()
        if self.calib_dialog is not None:
            self.calib_dialog.show()
            self.calib_dialog.raise_()

    def _on_calibration_completed(self, profile: Any) -> None:
        """Close dialog upon successful calibration."""
        if self.calib_dialog is not None:
            self.calib_dialog.accept()

    def _open_dashboard(self) -> None:
        """Open or focus dashboard window."""
        if self.dashboard_window is not None:
            self.dashboard_window.show()
            self.dashboard_window.raise_()
            self.dashboard_window.refresh_data()

    def start(self) -> None:
        """Start all background threads."""
        self.db_worker.start()
        self.camera_reader.start()
        self.vision_worker.start()

        if not self.headless and not self.calibrate_thresholds:
            print("\n" + "=" * 80)
            print(" Posture Guardian v3.0 Berjalan di Latar Belakang (Background Utility)")
            print("=" * 80)
            print(" • Status Icon : Periksa Menu Bar macOS di pojok kanan atas (lingkaran hijau)")
            print(" • Menu Aksi   : Klik ikon Menu Bar untuk:")
            print("                 - Buka Dashboard Sesi & Tren Historis")
            print("                 - Kalibrasi Ulang (3 Detik)")
            print("                 - Jeda Pemantauan (Snooze 15 / 30 / 60 Menit)")
            print("                 - Keluar Aplikasi")
            print(" • HUD Overlay : Otomatis muncul di layar jika postur melanggar (>= 15 detik)")
            print("=" * 80 + "\n")

            if self.show_dashboard:
                self._open_dashboard()
            elif self.start_calibration:
                self._start_calibration_flow()

    def shutdown(self) -> None:
        """Gracefully terminate all workers and flush data without deadlock."""
        if getattr(self, "_is_shutting_down", False):
            return
        self._is_shutting_down = True
        print("[Posture Guardian] Menghentikan seluruh background worker dan mem-flush database...")

        # 1. Stop camera reader
        self.camera_reader.stop()
        if self.camera_reader.is_alive():
            self.camera_reader.join(timeout=2.0)

        # 2. Stop vision worker
        self.vision_worker.stop()

        # 3. Stop DB worker (flushes active intervals)
        self.db_worker.stop()

        # 4. Close database
        self.db_manager.close()

        # 5. Quit Qt app
        app = QtWidgets.QApplication.instance()
        if app is not None:
            app.quit()
        print("[Posture Guardian] Shutdown selesai. Selamat beristirahat!")

    def _render_live_terminal_telemetry(self, data: dict[str, Any]) -> None:
        """Render ANSI live telemetry terminal screen for empirical threshold tuning (Bab 8)."""
        vis = data.get("vis_state", "UNKNOWN")
        mode = data.get("op_mode", "UNKNOWN")
        ui = data.get("ui_level", "SILENT_OK")
        moving = data.get("is_moving", False)
        v_norm = data.get("v_norm", 0.0)
        d_hat = data.get("d_hat", {})
        sigmas = data.get("sigmas", {})
        tau_enter = data.get("tau_enter", {})
        tau_exit = data.get("tau_exit", {})
        violations = data.get("violations", {})
        buckets = data.get("buckets", {})

        out = [
            "\033[H\033[2J",
            "=" * 80,
            " Posture Guardian v3.0 — Live Telemetry & Threshold Tuning (Bab 8)",
            f" Mode: {mode:<18} | Visibility: {vis:<14} | UI Level: {ui}",
            f" Gerak: {'MOVING' if moving else 'STATIONARY':<10} | v_norm: {v_norm:.3f}",
            "=" * 80,
            (
                f" {'Metrik':<12} {'d_hat':<10} {'sigma':<10} {'tau_in':<9} "
                f"{'tau_out':<9} {'Status':<9} {'Debounce (15s)':<14}"
            ),
            "-" * 80,
        ]

        metric_names = [
            ("fhp", "HEAD_FORWARD"),
            ("neck", "NECK_COMP"),
            ("slump", "SLUMP_DROP"),
            ("pitch", "GAZE_PITCH"),
            ("tilt", "CORONAL_TILT"),
        ]

        for key, label in metric_names:
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
            stat_str = "MELANGGAR" if is_v else "OK"

            fill_blocks = int(min(10, max(0, (b_s / 15.0) * 10)))
            bar_str = "[" + "#" * fill_blocks + "-" * (10 - fill_blocks) + f"] {b_s:4.1f}s"

            out.append(
                f" {label:<12} {val_str:<10} {sig_str:<10} {ent_str:<9} "
                f"{ex_str:<9} {stat_str:<9} {bar_str:<14}"
            )

        out.append("=" * 80)
        out.append(" Tekan Ctrl+C untuk keluar.")
        sys.stdout.write("\n".join(out) + "\n")
        sys.stdout.flush()


def main() -> int:
    """CLI bootstrap and entry point."""
    parser = argparse.ArgumentParser(description="Posture Guardian v3.0 Desktop Application")
    parser.add_argument(
        "--mock-camera", action="store_true", help="Run with synthetic camera frames"
    )
    parser.add_argument("--camera-index", type=int, default=0, help="UVC Camera device index")
    parser.add_argument(
        "--db-path", type=str, default="posture_guardian.db", help="SQLite database path"
    )
    parser.add_argument("--headless", action="store_true", help="Run headless without GUI")
    parser.add_argument(
        "--calibrate-thresholds",
        action="store_true",
        help="Display real-time live telemetry (d_hat, sigmas, visibility) for empirical tuning",
    )
    parser.add_argument(
        "--show-dashboard",
        action="store_true",
        help="Open dashboard window immediately upon application launch",
    )
    parser.add_argument(
        "--calibrate",
        action="store_true",
        help="Open 3-second guided calibration dialog upon launch",
    )
    args = parser.parse_args()

    app = QtCore.QCoreApplication.instance()
    if app is None:
        if args.headless or args.calibrate_thresholds:
            app = QtCore.QCoreApplication(sys.argv)
        else:
            app = QtWidgets.QApplication(sys.argv)

    # Don't quit when calibration dialog or dashboard closes
    if isinstance(app, QtWidgets.QApplication):
        app.setQuitOnLastWindowClosed(False)

    coordinator = PostureGuardianApp(
        db_path=args.db_path,
        camera_index=args.camera_index,
        mock_camera=args.mock_camera,
        headless=args.headless or args.calibrate_thresholds,
        calibrate_thresholds=args.calibrate_thresholds,
        show_dashboard=args.show_dashboard,
        start_calibration=args.calibrate,
    )
    coordinator.start()

    # Wire aboutToQuit for clean graceful shutdown
    app.aboutToQuit.connect(coordinator.shutdown)

    # Handle Ctrl+C (SIGINT) cleanly from terminal
    signal.signal(signal.SIGINT, lambda _sig, _frame: coordinator.shutdown())

    # Heartbeat timer so Python interpreter gets control to process OS signals
    heartbeat_timer = QtCore.QTimer()
    heartbeat_timer.timeout.connect(lambda: None)
    heartbeat_timer.start(500)

    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
