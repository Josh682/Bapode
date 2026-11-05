"""Application Entry Point and 4-Thread Coordinator for Posture Guardian v3.0.

Reference: docs/ARCHITECTURE.md & docs/DESIGN_SYSTEM.md
Coordinates:
- Thread 3: Camera Capture Worker (5 FPS)
- Worker Thread 1: Sequential Pose+Face & Tracker Engine
- Worker Thread 2: Asynchronous SQLite WAL Writer
- Main Thread: PySide6 GUI (Tray Icon, HUD Overlay, Dashboard)
"""

import argparse
import sys
from typing import Any

from PySide6 import QtCore, QtWidgets

from camera.reader import CameraReader
from db.manager import DatabaseManager
from db.worker import DatabaseWorker
from ui.dashboard import GuidedCalibrationDialog, SessionTrendDashboard
from ui.overlay import HUDOverlay
from ui.platform_bridge import PlatformPowerListener
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
        parent: QtCore.QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.db_path = db_path
        self.camera_index = camera_index
        self.mock_camera = mock_camera
        self.headless = headless

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

        # 5. Initialize UI (Main Thread)
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

        # Connect Tray actions
        self.tray_manager.recalibrate_requested.connect(self._start_calibration_flow)
        self.tray_manager.snooze_requested.connect(self.vision_worker.set_snooze)
        self.tray_manager.open_dashboard_requested.connect(self._open_dashboard)
        self.tray_manager.quit_requested.connect(self.shutdown)

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

    def shutdown(self) -> None:
        """Gracefully terminate all workers and flush data without deadlock."""
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
    args = parser.parse_args()

    app = QtWidgets.QApplication.instance()
    if app is None:
        app = QtWidgets.QApplication(sys.argv)

    # Don't quit when calibration dialog or dashboard closes
    if isinstance(app, QtWidgets.QApplication):
        app.setQuitOnLastWindowClosed(False)

    coordinator = PostureGuardianApp(
        db_path=args.db_path,
        camera_index=args.camera_index,
        mock_camera=args.mock_camera,
        headless=args.headless,
    )
    coordinator.start()

    # Wire aboutToQuit for clean graceful shutdown
    app.aboutToQuit.connect(coordinator.shutdown)

    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
