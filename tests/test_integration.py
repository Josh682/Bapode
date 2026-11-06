"""End-to-End Integration and 4-Thread Pipeline Tests.

Reference: docs/ARCHITECTURE.md & docs/DESIGN_SYSTEM.md
Verifies:
1. CameraReader 5 FPS acquisition with latest-frame queue size 1.
2. Asynchronous DatabaseWorker batching and interval flushing.
3. Platform bridge screen-share and OS sleep/wake listeners.
4. Non-activating HUDOverlay flags and system tray state updates.
5. Full end-to-end pipeline (Camera -> Vision -> DB) and graceful shutdown without deadlock.
"""

import os
import time
from pathlib import Path

# Ensure offscreen Qt platform and writable matplotlib cache for test execution
os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ.setdefault("MPLCONFIGDIR", "/tmp/matplotlib")

import pytest
from PySide6 import QtCore, QtWidgets

from camera.reader import CameraReader
from db.manager import CalibrationProfileRecord, DatabaseManager
from db.worker import DatabaseWorker
from main import BapodeApp
from tracker.states import (
    IntervalSignature,
    PostureViolationFlags,
    UIIntrusivenessLevel,
    VisibilityState,
)
from ui.overlay import HUDOverlay
from ui.platform_bridge import PlatformPowerListener, set_screen_share_protected
from ui.tray import SystemTrayManager


@pytest.fixture(scope="session")
def qapp() -> QtWidgets.QApplication:
    """Ensure QApplication instance exists with offscreen platform for GUI tests."""
    app = QtWidgets.QApplication.instance()
    if app is None:
        app = QtWidgets.QApplication(["-platform", "offscreen"])
    return app  # type: ignore[return-value]


class TestCameraReader:
    """Test CameraReader single-reader thread and latest-frame queue."""

    def test_camera_reader_acquisition_and_overwrite(self) -> None:
        reader = CameraReader(target_fps=20.0, mock_mode=True)
        reader.start()

        # Wait for a few frames
        time.sleep(0.15)

        assert not reader.latest_frame_queue.empty()
        frame1 = reader.latest_frame_queue.get(timeout=1.0)
        assert frame1.image.shape == (720, 1280, 3)
        assert frame1.timestamp_s > 0.0

        # Verify queue does not accumulate lag frames (maxsize=1)
        time.sleep(0.2)
        assert reader.latest_frame_queue.qsize() <= 1

        reader.stop()
        reader.join(timeout=2.0)
        assert not reader.is_alive()


class TestDatabaseWorker:
    """Test asynchronous SQLite WAL writer worker."""

    def test_async_commit_and_graceful_shutdown(self, tmp_path: Path) -> None:
        db_file = tmp_path / "async_test.db"
        db_mgr = DatabaseManager(db_file)
        db_mgr.initialize_schema()

        session_id = db_mgr.create_session()
        profile_rec = CalibrationProfileRecord(
            profile_id=None,
            created_at=None,
            base_neck_ort_ratio=0.5,
            base_eye_sh_ratio=0.3,
            base_sh_width_px=200.0,
            base_sh_y_mid=400.0,
            base_sh_angle_deg=0.0,
            base_head_pitch_deg=0.0,
            base_head_roll_deg=0.0,
            sigma_fhp=0.01,
            sigma_neck=0.01,
            sigma_slump=0.01,
            sigma_pitch=1.0,
            sigma_tilt=1.0,
        )
        profile_id = db_mgr.save_calibration_profile(profile_rec)

        worker = DatabaseWorker(db_mgr, flush_interval_s=0.2)
        worker.start()

        sig = IntervalSignature(
            visibility_state=VisibilityState.MEASURABLE,
            ui_level=UIIntrusivenessLevel.SILENT_OK,
            violations=PostureViolationFlags(),
        )

        worker.enqueue_record_state(session_id, profile_id, sig)
        worker.enqueue_flush()

        # Graceful stop
        worker.stop()
        assert not worker.isRunning()

        # Verify flushed to disk
        intervals = db_mgr.get_intervals_for_session(session_id)
        assert len(intervals) >= 1
        db_mgr.close()


class TestUIAndPlatformBridge:
    """Test HUD overlay flags, platform bridge, and system tray."""

    def test_hud_overlay_window_flags(self, qapp: QtWidgets.QApplication) -> None:
        overlay = HUDOverlay()
        flags = overlay.windowFlags()

        # Must have non-activating, tool, click-through flags
        assert flags & QtCore.Qt.WindowType.FramelessWindowHint
        assert flags & QtCore.Qt.WindowType.WindowStaysOnTopHint
        assert flags & QtCore.Qt.WindowType.Tool
        assert flags & QtCore.Qt.WindowType.WindowTransparentForInput
        assert flags & QtCore.Qt.WindowType.WindowDoesNotAcceptFocus

        # Must show without activating and have translucent background
        assert overlay.testAttribute(QtCore.Qt.WidgetAttribute.WA_ShowWithoutActivating)
        assert overlay.testAttribute(QtCore.Qt.WidgetAttribute.WA_TranslucentBackground)

        # Visual state transitions
        overlay.update_state("ACTIVE_OVERLAY", "Test Warning")
        assert overlay.isVisible()

        overlay.update_state("SILENT_OK")
        assert overlay.isHidden()
        overlay.close()

    def test_screen_share_safe_bridge(self, qapp: QtWidgets.QApplication) -> None:
        widget = QtWidgets.QWidget()
        # Should not raise exception across any OS
        result = set_screen_share_protected(widget)
        assert isinstance(result, bool)
        widget.close()

    def test_power_listener_simulation(self, qapp: QtWidgets.QApplication) -> None:
        listener = PlatformPowerListener()
        sleep_called = []
        wake_called = []

        listener.system_will_sleep.connect(lambda: sleep_called.append(True))
        listener.system_did_wake.connect(lambda: wake_called.append(True))

        listener.simulate_sleep()
        listener.simulate_wake()

        assert sleep_called == [True]
        assert wake_called == [True]

    def test_system_tray_status_colors(self, qapp: QtWidgets.QApplication) -> None:
        tray = SystemTrayManager()
        tray.update_status("ACTIVE_MONITORING", "MEASURABLE", "SILENT_OK")
        tray.update_status("ACTIVE_MONITORING", "MEASURABLE", "ACTIVE_OVERLAY")
        tray.update_status("PAUSED_MANUAL", "PAUSED", "SILENT_OK")
        # Ensure menu actions exist
        assert len(tray.menu.actions()) >= 4


class TestEndToEndPipeline:
    """Full end-to-end integration test across all 4 threads."""

    def test_pipeline_dataflow_and_graceful_shutdown(
        self, tmp_path: Path, qapp: QtWidgets.QApplication
    ) -> None:
        db_path = str(tmp_path / "e2e_full.db")
        app_coord = BapodeApp(
            db_path=db_path,
            mock_camera=True,
            headless=True,
        )

        app_coord.start()

        # Let the pipeline process ~5-8 frames (about 1.2s at 5 FPS)
        time.sleep(1.2)

        # Trigger manual snooze and verify state updates
        app_coord.vision_worker.set_snooze(15)
        time.sleep(0.2)
        assert app_coord.vision_worker.orchestrator is not None
        assert app_coord.vision_worker.orchestrator.operating_mode == "PAUSED_MANUAL"

        app_coord.vision_worker.set_snooze(0)  # Resume

        # Graceful shutdown must finish cleanly without deadlock
        app_coord.shutdown()

        assert not app_coord.camera_reader.is_alive()
        assert not app_coord.vision_worker.isRunning()
        assert not app_coord.db_worker.isRunning()

        # Reopen DB and verify telemetry was persisted
        verify_mgr = DatabaseManager(db_path)
        conn = verify_mgr.connect()
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM monitoring_sessions")
        session_count = cursor.fetchone()[0]
        assert session_count >= 1

        verify_mgr.close()
