"""Tests for Camera View and Frame Annotation (GUI)."""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from pathlib import Path

from tracker.states import PostureViolationFlags, VisibilityState
from ui.camera_view import CameraPreviewWidget
from ui.qt import QtGui
from vision.annotator import FrameAnnotator
from vision.landmarks import BoundingBox, FaceLandmarkData, Point2D, PoseLandmarkData


class TestCameraAnnotation:
    """Verify FrameAnnotator renders landmarks, HUD banner, and calibration overlays."""

    def test_annotate_empty_frame(self) -> None:
        raw = np.zeros((480, 640, 3), dtype=np.uint8)
        qimg = FrameAnnotator.annotate(
            image_bgr=raw,
            pose=None,
            face=None,
            vis_state=VisibilityState.ABSENT,
        )
        assert isinstance(qimg, QtGui.QImage)
        assert not qimg.isNull()
        assert qimg.width() == 640
        assert qimg.height() == 480

    def test_annotate_with_pose_and_face(self) -> None:
        raw = np.full((720, 1280, 3), 50, dtype=np.uint8)
        pose = PoseLandmarkData(
            left_shoulder=Point2D(400.0, 450.0),
            right_shoulder=Point2D(800.0, 450.0),
            left_ear=Point2D(500.0, 250.0),
            right_ear=Point2D(700.0, 250.0),
            nose=Point2D(600.0, 270.0),
        )
        face = FaceLandmarkData(
            bbox=BoundingBox(520.0, 180.0, 680.0, 340.0),
            pitch_deg=5.0,
            yaw_deg=-2.0,
            roll_deg=1.0,
            eye_outer_left=Point2D(560.0, 240.0),
            eye_outer_right=Point2D(640.0, 240.0),
        )
        violations = PostureViolationFlags(v_slump=True)

        qimg = FrameAnnotator.annotate(
            image_bgr=raw,
            pose=pose,
            face=face,
            vis_state=VisibilityState.MEASURABLE,
            violations=violations,
        )
        assert not qimg.isNull()
        assert qimg.width() == 1280
        assert qimg.height() == 720

    def test_annotate_calibration_overlay(self) -> None:
        raw = np.zeros((720, 1280, 3), dtype=np.uint8)
        qimg = FrameAnnotator.annotate(
            image_bgr=raw,
            is_calibrating=True,
            calib_current=8,
            calib_total=15,
            calib_plausible=True,
            calib_feedback="Pertahankan posisi tegak lurus...",
        )
        assert not qimg.isNull()


class TestCameraPreviewWidget:
    """Verify CameraPreviewWidget updates pixmap."""

    def test_widget_frame_update(self, qapp: QtGui.QGuiApplication) -> None:
        widget = CameraPreviewWidget()
        assert widget.video_label.text() == "📷 Menunggu Feed Webcam..."

        # Update with valid image
        img = QtGui.QImage(640, 480, QtGui.QImage.Format.Format_RGB888)
        img.fill(QtGui.QColor(0, 255, 0))
        widget.update_frame(img)

        assert widget._current_pixmap is not None
        assert not widget._current_pixmap.isNull()


class TestDashboardAndCalibrationQuit:
    """Verify Cmd+Q and quit signals in Dashboard and Calibration Dialog."""

    def test_dashboard_quit_requested_signal(
        self, tmp_path: Path, qapp: QtGui.QGuiApplication
    ) -> None:
        from db.manager import DatabaseManager
        from ui.dashboard import GuidedCalibrationDialog, SessionTrendDashboard

        db_path = str(tmp_path / "test_dash_quit.db")
        mgr = DatabaseManager(db_path)
        mgr.initialize_schema()

        dashboard = SessionTrendDashboard(mgr)
        quit_called = []
        dashboard.quit_requested.connect(lambda: quit_called.append(True))

        # Click quit button
        dashboard.btn_quit.click()
        assert len(quit_called) == 1

        # Test calibration dialog quit shortcut signal
        calib = GuidedCalibrationDialog()
        calib_quit_called = []
        calib.quit_requested.connect(lambda: calib_quit_called.append(True))
        calib.quit_shortcut.activated.emit()
        assert len(calib_quit_called) == 1

        dashboard.deleteLater()
        calib.deleteLater()
        mgr.close()
