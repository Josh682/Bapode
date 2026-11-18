"""Tests for Tahap 1 UI foundation, cohesive dark theme, and signal wiring."""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6 import QtWidgets

from db.manager import DatabaseManager
from db.worker import DatabaseWorker
from main import BapodeApp
from tracker.calibration import LOW_LIGHT_WARNING_MESSAGE
from tracker.states import PostureViolationFlags
from ui.dashboard import GuidedCalibrationDialog, SessionTrendDashboard
from ui.overlay import HUDOverlay
from ui.theme import ColorTokens, apply_app_theme
from ui.tray import SystemTrayManager
from vision.detector import SequentialLandmarkDetector
from vision.worker import VisionTrackerWorker

if TYPE_CHECKING:
    from pathlib import Path
    import queue as queue_mod
    from camera.reader import CameraFrame


class TestThemeFoundation:
    """Verify centralized dark QPalette/QSS and DPI-resilient dialog behavior."""

    def test_apply_app_theme_sets_palette_and_tokens(self, qapp: QtWidgets.QApplication) -> None:
        apply_app_theme(qapp)
        palette = qapp.palette()
        assert palette.color(palette.ColorRole.Window).name().upper() == ColorTokens.BG_CANVAS
        assert (
            palette.color(palette.ColorRole.WindowText).name().upper()
            == ColorTokens.TEXT_PRIMARY
        )
        assert bool(qapp.styleSheet())

    def test_calibration_dialog_dynamic_property_and_resizability(
        self, qapp: QtWidgets.QApplication
    ) -> None:
        del qapp
        dlg = GuidedCalibrationDialog()
        assert dlg.minimumWidth() >= 520
        assert dlg.minimumHeight() >= 520
        assert dlg.maximumWidth() > 520

        dlg.update_progress(4, 15, False, "Luruskan kepala")
        assert dlg.progress_bar.property("calibState") == "error"

        dlg.update_progress(5, 15, True, "Tahan posisi")
        assert dlg.progress_bar.property("calibState") == "ok"
        dlg.deleteLater()


class TestHudAndTraySignalPlumbing:
    """Verify full 4-tuple state, proximity, low-light, and snooze signal delivery."""

    def test_hud_receives_flags_and_recalibration_transition_once(
        self, qapp: QtWidgets.QApplication
    ) -> None:
        del qapp
        overlay = HUDOverlay()

        overlay.on_state_changed(
            "ACTIVE_MONITORING",
            "MEASURABLE",
            "ACTIVE_OVERLAY",
            PostureViolationFlags(v_fhp=True),
        )
        assert overlay.isVisible()
        assert "Tarik kepala ke belakang" in overlay._badge_text
        assert overlay._badge_color.name().upper() == ColorTokens.STATUS_ALERT

        # Transition into NEEDS_RECALIBRATION triggers violet 5s passive notice once
        overlay.on_state_changed(
            "NEEDS_RECALIBRATION",
            "NEEDS_RECALIBRATION",
            "SILENT_OK",
            PostureViolationFlags(),
        )
        assert overlay.isVisible()
        assert overlay._badge_color.name().upper() == ColorTokens.STATUS_CALIBRATION
        assert overlay._auto_hide_timer.isActive()

        # Simulate 5s expiry, then verify subsequent 200ms ticks do not re-pop the badge
        overlay._auto_hide_timer.timeout.emit()
        assert overlay.isHidden()

        overlay.on_state_changed(
            "NEEDS_RECALIBRATION",
            "NEEDS_RECALIBRATION",
            "SILENT_OK",
            PostureViolationFlags(),
        )
        assert overlay.isHidden()

        # Paused mode must immediately dismiss an active posture overlay
        overlay.on_state_changed(
            "ACTIVE_MONITORING",
            "MEASURABLE",
            "ACTIVE_OVERLAY",
            PostureViolationFlags(v_slump=True),
        )
        assert overlay.isVisible()
        overlay.on_state_changed(
            "PAUSED_MANUAL",
            "PAUSED",
            "SILENT_OK",
            PostureViolationFlags(),
        )
        assert overlay.isHidden()
        overlay.close()

    def test_hud_extreme_close_proximity_alert_and_recovery(
        self, qapp: QtWidgets.QApplication
    ) -> None:
        del qapp
        overlay = HUDOverlay()
        overlay.on_state_changed(
            "ACTIVE_MONITORING",
            "OCCLUDED_LOWER",
            "SILENT_OK",
            PostureViolationFlags(),
        )
        overlay.update_environment_status(False, True, "")
        assert overlay.isVisible()
        assert "Terlalu Dekat" in overlay._badge_text
        assert overlay._badge_color.name().upper() == ColorTokens.STATUS_ALERT

        overlay.update_environment_status(False, False, "")
        assert overlay.isVisible()
        assert overlay._badge_text == "Postur Kembali Ideal"
        assert overlay._badge_color.name().upper() == ColorTokens.STATUS_OK
        assert overlay._auto_hide_timer.isActive()
        overlay.close()

    def test_tray_snooze_countdown_env_tooltip_and_recalib_transition(
        self, qapp: QtWidgets.QApplication
    ) -> None:
        del qapp
        tray = SystemTrayManager()
        messages: list[tuple[str, str]] = []
        tray.tray_icon.showMessage = (  # type: ignore[method-assign]
            lambda title, msg, *args: messages.append((title, msg))
        )

        tray.update_snooze_status(True, 125.0)
        assert "02:05" in tray._status_action.text()
        assert "02:05" in tray.tray_icon.toolTip()
        assert tray._resume_action.isEnabled()

        # Transition NEEDS_RECALIBRATION -> PAUSED_MANUAL -> NEEDS_RECALIBRATION must notify twice
        tray.update_status("NEEDS_RECALIBRATION", "NEEDS_RECALIBRATION", "SILENT_OK")
        tray.update_status("PAUSED_MANUAL", "PAUSED", "SILENT_OK")
        tray.update_status("NEEDS_RECALIBRATION", "NEEDS_RECALIBRATION", "SILENT_OK")
        assert len(messages) == 2

        tray.update_environment_status(True, False, LOW_LIGHT_WARNING_MESSAGE)
        assert "Cahaya Redup" in tray.tray_icon.toolTip()


class TestDashboardBannersAndWiring:
    """Verify Dashboard status strip, environment/drift banners, timer lifecycle, and wiring."""

    def test_dashboard_status_banners_and_timer_lifecycle(
        self, tmp_path: Path, qapp: QtWidgets.QApplication
    ) -> None:
        del qapp
        mgr = DatabaseManager(str(tmp_path / "dash_phase1.db"))
        mgr.initialize_schema()
        dash = SessionTrendDashboard(mgr)
        assert dash.tabs.tabText(1) == "Sesi && Tren Historis"

        assert not dash.refresh_timer.isActive()
        dash.show()
        assert dash.refresh_timer.isActive()

        dash.update_system_status("PAUSED_MANUAL", "PAUSED", "SILENT_OK")
        dash.update_snooze_status(True, 90.0)
        assert "01:30" in dash.system_status_label.text()
        assert not dash.btn_resume_snooze.isHidden()

        dash.update_system_status("ACTIVE_MONITORING", "MEASURABLE", "SILENT_OK")
        assert dash.btn_resume_snooze.isHidden()

        dash.update_environment_status(True, False, LOW_LIGHT_WARNING_MESSAGE)
        assert not dash.env_banner.isHidden()
        assert LOW_LIGHT_WARNING_MESSAGE in dash.env_banner_label.text()
        assert dash.env_banner.property("severity") == "warning"

        dash.on_drift_detected("Sudut engsel layar berubah")
        assert not dash.env_banner.isHidden()
        assert dash.env_banner.property("severity") == "calibration"
        assert not dash.btn_banner_recalibrate.isHidden()

        dash.hide()
        assert not dash.refresh_timer.isActive()
        dash.deleteLater()
        mgr.close()

    def test_worker_environment_signal_deduplication(
        self, tmp_path: Path, qapp: QtWidgets.QApplication
    ) -> None:
        import queue

        del qapp
        mgr = DatabaseManager(str(tmp_path / "worker_env.db"))
        mgr.initialize_schema()
        db_worker = DatabaseWorker(mgr)
        frame_q: queue_mod.Queue[CameraFrame] = queue.Queue(maxsize=1)
        worker = VisionTrackerWorker(
            frame_queue=frame_q,
            db_worker=db_worker,
            detector=SequentialLandmarkDetector(use_mock=True),
        )

        emitted: list[tuple[bool, bool, str]] = []
        worker.environment_alerts_changed.connect(
            lambda low, close, msg: emitted.append((low, close, msg))
        )

        worker._emit_environment_alerts(False, False)
        assert emitted == []

        worker._emit_environment_alerts(True, False)
        worker._emit_environment_alerts(True, False)
        assert emitted == [(True, False, LOW_LIGHT_WARNING_MESSAGE)]

        worker._emit_environment_alerts(False, True)
        assert emitted[-1] == (False, True, "")
        mgr.close()

    def test_bapode_app_gui_wiring_smoke(
        self, tmp_path: Path, qapp: QtWidgets.QApplication
    ) -> None:
        del qapp
        app_coord = BapodeApp(
            db_path=str(tmp_path / "app_gui_smoke.db"),
            mock_camera=True,
            headless=False,
        )
        assert app_coord.hud_overlay is not None
        assert app_coord.tray_manager is not None
        assert app_coord.dashboard_window is not None
        assert app_coord.calib_dialog is not None
        app_coord.shutdown()


class TestGuidedCalibrationFixes:
    """Verify calibration streak reset, Indonesian cues, cancel recovery, and persistence."""

    def test_calibration_dialog_resets_on_reopen_and_updates_chips(
        self, qapp: QtWidgets.QApplication
    ) -> None:
        del qapp
        dlg = GuidedCalibrationDialog()
        dlg.show()
        dlg.update_progress(10, 15, True, "Posisi pas!")
        assert dlg.progress_bar.value() == 10

        dlg.hide()
        dlg.show()
        assert dlg.progress_bar.value() == 0
        assert "Menyiapkan kamera" in dlg.feedback_label.text()

        dlg.update_progress(0, 15, False, "Bahu tidak terlihat — mundur sedikit.")
        assert "Bahu tidak terlihat" in dlg.feedback_label.text()

        # Noise / MAD failure must highlight step 3 (stability), not step 2 (alignment)
        dlg.update_progress(
            0,
            15,
            False,
            "Pencahayaan terlalu redup atau posisi belum stabil (jitter Stabilitas bahu)",
        )
        assert (
            "245, 158, 11" in dlg.chip_stability.styleSheet()
            or "#F59E0B" in dlg.chip_stability.styleSheet()
        )
        assert "#F59E0B" not in dlg.chip_alignment.styleSheet()
        dlg.deleteLater()

    def test_worker_calibration_cancel_streak_and_indonesian_guidance(
        self, tmp_path: Path, qapp: QtWidgets.QApplication
    ) -> None:
        import queue

        import numpy as np

        from db.manager import CalibrationProfileRecord
        from tracker.states import OperatingMode
        from tracker.telemetry import SessionOrchestrator
        from vision.landmarks import BoundingBox, FaceLandmarkData, Point2D, PoseLandmarkData
        from vision.metrics import CalibrationReference

        del qapp
        mgr = DatabaseManager(str(tmp_path / "calib_fix.db"))
        mgr.initialize_schema()
        sid = mgr.create_session()
        pid = mgr.save_calibration_profile(
            CalibrationProfileRecord(
                profile_id=None,
                created_at=None,
                base_neck_ort_ratio=0.55,
                base_eye_sh_ratio=0.30,
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
        )
        db_worker = DatabaseWorker(mgr)
        frame_q: queue_mod.Queue[CameraFrame] = queue.Queue(maxsize=1)
        worker = VisionTrackerWorker(
            frame_queue=frame_q,
            db_worker=db_worker,
            detector=SequentialLandmarkDetector(use_mock=True),
        )
        worker.orchestrator = SessionOrchestrator(
            db=mgr,
            session_id=sid,
            profile_id=pid,
            ref=CalibrationReference(0.55, 0.30, 200.0, 400.0, 0.0, 0.0, 0.0),
        )

        progress_events: list[tuple[int, int, bool, str]] = []
        worker.calibration_progress.connect(
            lambda cur, tot, ok, msg: progress_events.append((cur, tot, ok, msg))
        )

        worker.trigger_calibration()
        assert worker.orchestrator.operating_mode == OperatingMode.CALIBRATING
        worker.cancel_calibration()
        assert worker.orchestrator.operating_mode == OperatingMode.ACTIVE_MONITORING
        assert not worker._manual_calibrating

        # Start calibration again and verify streak + Indonesian feedback
        worker.trigger_calibration()
        img = np.zeros((720, 1280, 3), dtype=np.uint8)
        valid_pose = PoseLandmarkData(
            left_shoulder=Point2D(740.0, 440.0),
            right_shoulder=Point2D(540.0, 440.0),
            left_ear=Point2D(680.0, 330.0),
            right_ear=Point2D(600.0, 330.0),
            nose=Point2D(640.0, 340.0),
        )
        valid_face = FaceLandmarkData(
            bbox=BoundingBox(580.0, 280.0, 700.0, 400.0),
            pitch_deg=0.0,
            yaw_deg=0.0,
            roll_deg=0.0,
            eye_outer_left=Point2D(670.0, 330.0),
            eye_outer_right=Point2D(610.0, 330.0),
        )

        # Collect 4 valid samples
        for _ in range(4):
            worker._process_calibration_tick(img, valid_pose, [valid_face])
        assert worker.calibration_engine.sample_count == 4

        # Missing pose with visible face -> specific shoulder warning; 3 ticks resets streak
        for _ in range(3):
            worker._process_calibration_tick(img, None, [valid_face])
        assert "Bahu tidak terlihat" in progress_events[-1][3]
        assert worker.calibration_engine.sample_count == 0

        # Yaw exceeded -> actionable Indonesian cue
        bad_yaw_face = FaceLandmarkData(
            bbox=valid_face.bbox,
            pitch_deg=0.0,
            yaw_deg=11.5,
            roll_deg=0.0,
            eye_outer_left=valid_face.eye_outer_left,
            eye_outer_right=valid_face.eye_outer_right,
        )
        worker._process_calibration_tick(img, valid_pose, [bad_yaw_face])
        assert "Hadapkan wajah lurus" in progress_events[-1][3]

        # Complete 15 clean consecutive ticks -> saves profile and returns to ACTIVE_MONITORING
        for _ in range(15):
            worker._process_calibration_tick(img, valid_pose, [valid_face])
        assert worker.orchestrator.operating_mode == OperatingMode.ACTIVE_MONITORING
        latest = mgr.get_latest_calibration_profile()
        assert latest is not None
        assert (latest.profile_id or 0) > pid
        mgr.close()

    def test_worker_calibration_noise_diagnostic_and_cooldown(
        self, tmp_path: Path, qapp: QtWidgets.QApplication
    ) -> None:
        import queue

        import numpy as np

        from tracker.states import OperatingMode
        from tracker.telemetry import SessionOrchestrator
        from vision.landmarks import BoundingBox, FaceLandmarkData, Point2D, PoseLandmarkData
        from vision.metrics import CalibrationReference

        del qapp
        mgr = DatabaseManager(str(tmp_path / "calib_noise.db"))
        mgr.initialize_schema()
        sid = mgr.create_session()
        db_worker = DatabaseWorker(mgr)
        frame_q: queue_mod.Queue[CameraFrame] = queue.Queue(maxsize=1)
        worker = VisionTrackerWorker(
            frame_queue=frame_q,
            db_worker=db_worker,
            detector=SequentialLandmarkDetector(use_mock=True),
        )
        worker.orchestrator = SessionOrchestrator(
            db=mgr,
            session_id=sid,
            profile_id=1,
            ref=CalibrationReference(0.55, 0.30, 200.0, 400.0, 0.0, 0.0, 0.0),
        )

        progress_events: list[tuple[int, int, bool, str]] = []
        worker.calibration_progress.connect(
            lambda cur, tot, ok, msg: progress_events.append((cur, tot, ok, msg))
        )

        worker.trigger_calibration()
        img = np.zeros((720, 1280, 3), dtype=np.uint8)

        # Feed 15 valid-alignment frames with multi-level vertical shoulder jitter (slump noise)
        for i in range(15):
            dy = ((i % 5) - 2) * 6.0
            pose = PoseLandmarkData(
                left_shoulder=Point2D(740.0, 440.0 + dy),
                right_shoulder=Point2D(540.0, 440.0 + dy),
                left_ear=Point2D(680.0, 330.0),
                right_ear=Point2D(600.0, 330.0),
                nose=Point2D(640.0, 340.0),
            )
            face = FaceLandmarkData(
                bbox=BoundingBox(580.0, 280.0, 700.0, 400.0),
                pitch_deg=0.0,
                yaw_deg=0.0,
                roll_deg=0.0,
                eye_outer_left=Point2D(670.0, 330.0),
                eye_outer_right=Point2D(610.0, 330.0),
            )
            worker._process_calibration_tick(img, pose, [face])

        assert worker.orchestrator.operating_mode == OperatingMode.CALIBRATING
        assert progress_events[-1][0] == 0
        assert not progress_events[-1][2]
        assert "jitter" in progress_events[-1][3].lower()

        # Next frame stays in readable error cooldown at 0/15 instead of jumping to 1/15
        steady_pose = PoseLandmarkData(
            left_shoulder=Point2D(740.0, 440.0),
            right_shoulder=Point2D(540.0, 440.0),
            left_ear=Point2D(680.0, 330.0),
            right_ear=Point2D(600.0, 330.0),
            nose=Point2D(640.0, 340.0),
        )
        steady_face = FaceLandmarkData(
            bbox=BoundingBox(580.0, 280.0, 700.0, 400.0),
            pitch_deg=0.0,
            yaw_deg=0.0,
            roll_deg=0.0,
            eye_outer_left=Point2D(670.0, 330.0),
            eye_outer_right=Point2D(610.0, 330.0),
        )
        worker._process_calibration_tick(img, steady_pose, [steady_face])
        assert progress_events[-1][0] == 0
        mgr.close()

    def test_hud_microbreak_countdown_and_auto_dismiss(
        self, qapp: QtWidgets.QApplication
    ) -> None:
        del qapp
        overlay = HUDOverlay()
        overlay.on_state_changed(
            "ACTIVE_MONITORING",
            "MEASURABLE",
            "MICROBREAK_PROMPT",
            PostureViolationFlags(),
        )
        assert overlay.isVisible()
        assert overlay._microbreak_timer.isActive()
        assert "60s" in overlay._badge_text
        assert overlay._badge_color.name().upper() == ColorTokens.STATUS_INFO

        # Sync backend remaining seconds when drift >= 2s
        overlay.sync_microbreak_remaining(45.0)
        assert "45s" in overlay._badge_text

        # Simulate remaining 44 seconds ticking down to 1s
        for _ in range(44):
            overlay._on_microbreak_tick()
        assert "1s" in overlay._badge_text
        assert overlay._microbreak_active is True

        # Final tick hits 0s -> stops countdown, shows green completion, auto-hides in 3s
        overlay._on_microbreak_tick()
        assert not overlay._microbreak_timer.isActive()
        assert overlay._microbreak_active is False
        assert "Peregangan Selesai" in overlay._badge_text
        assert overlay._badge_color.name().upper() == ColorTokens.STATUS_OK
        assert overlay._auto_hide_timer.isActive()

        # Backend transitioning to SILENT_OK does not break active auto-hide
        overlay.on_state_changed(
            "ACTIVE_MONITORING",
            "MEASURABLE",
            "SILENT_OK",
            PostureViolationFlags(),
        )
        assert overlay._auto_hide_timer.isActive()

        overlay._auto_hide_timer.timeout.emit()
        assert overlay.isHidden()
        overlay.close()
