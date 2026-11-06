"""Worker Thread 1: Vision & Tracker Engine.

Reference: docs/ARCHITECTURE.md, docs/specs/01-vision-and-metrics.md,
           docs/specs/02-signal-and-tracker.md
Implements:
1. Frame consumer from CameraReader queue at 5 FPS.
2. Sequential Pose Lite and Face Landmarker inference.
3. End-to-end integration with SessionOrchestrator, CalibrationEngine, and 5x IssueTracker.
4. Pure Qt Signals emitted to Main Thread (GUI & System Tray).
5. Non-blocking asynchronous coordination with DatabaseWorker.
"""

import queue
import time
from datetime import datetime

import numpy as np

from camera.reader import CameraFrame
from db.manager import CalibrationProfileRecord
from db.worker import DatabaseWorker
from tracker.calibration import CalibrationEngine
from tracker.filter import NormalizedVelocityDetector
from tracker.states import (
    OperatingMode,
    UIIntrusivenessLevel,
    VisibilityState,
)
from tracker.telemetry import SessionOrchestrator
from ui.qt import QtCore, QtGui
from vision.annotator import FrameAnnotator
from vision.association import VisibilityEvaluator, associate_primary_subject
from vision.detector import SequentialLandmarkDetector
from vision.landmarks import FaceLandmarkData, PoseLandmarkData
from vision.metrics import (
    CalibrationReference,
    calculate_posture_metrics,
    calculate_yaw_compensated_eye_width,
)


class VisionTrackerWorker(QtCore.QThread):
    """Worker Thread 1: Sequential Pose+Face inference and posture tracking engine."""

    # Qt Signals emitted to Main Thread (GUI)
    state_changed = QtCore.Signal(str, str, str, object)  # op_mode, vis_state, ui_level, flags
    metrics_calculated = QtCore.Signal(object, float)  # PostureMetricsResult, timestamp
    alert_triggered = QtCore.Signal(str, float)  # issue_key, severity
    microbreak_triggered = QtCore.Signal(float)  # microbreak duration
    calibration_progress = QtCore.Signal(int, int, bool, str)  # count, total, is_plausible, prompt
    calibration_completed = QtCore.Signal(object)  # CalibrationProfileRecord
    drift_detected = QtCore.Signal(str)  # drift description
    snooze_status_changed = QtCore.Signal(bool, float)  # is_snoozed, remaining_s
    live_telemetry = QtCore.Signal(dict)  # Live telemetry for threshold tuning
    frame_annotated = QtCore.Signal(QtGui.QImage)  # Live annotated camera feed for GUI

    def __init__(
        self,
        frame_queue: queue.Queue[CameraFrame],
        db_worker: DatabaseWorker,
        detector: SequentialLandmarkDetector | None = None,
        parent: QtCore.QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.frame_queue = frame_queue
        self.db_worker = db_worker
        self.detector = detector or SequentialLandmarkDetector()

        # Calibration & Orchestrator
        self.calibration_engine = CalibrationEngine()
        self.visibility_evaluator = VisibilityEvaluator()
        self.velocity_detector = NormalizedVelocityDetector()
        self.orchestrator: SessionOrchestrator | None = None
        self.active_profile: CalibrationProfileRecord | None = None

        # Thread lifecycle
        self._running = True
        self._snooze_until: float = 0.0
        self._manual_calibrating = False

    def stop(self) -> None:
        """Stop worker loop."""
        self._running = False
        self.wait(5000)
        self.detector.close()

    def set_active_profile(self, profile: CalibrationProfileRecord) -> None:
        """Set or update active calibration reference."""
        self.active_profile = profile
        ref = CalibrationReference(
            base_neck_ort_ratio=profile.base_neck_ort_ratio,
            base_eye_sh_ratio=profile.base_eye_sh_ratio,
            base_sh_width_px=profile.base_sh_width_px,
            base_sh_y_mid=profile.base_sh_y_mid,
            base_sh_angle_deg=profile.base_sh_angle_deg,
            base_head_pitch_deg=profile.base_head_pitch_deg,
            base_head_roll_deg=profile.base_head_roll_deg,
        )
        if self.orchestrator is not None:
            self.orchestrator.ref = ref
            self.orchestrator.profile_id = profile.profile_id or 1
            self.orchestrator.drift_detector.ref = ref
            self.orchestrator.noise_adapter.current_sigmas.update(
                {
                    "fhp": profile.sigma_fhp,
                    "neck": profile.sigma_neck,
                    "slump": profile.sigma_slump,
                    "pitch": profile.sigma_pitch,
                    "tilt": profile.sigma_tilt,
                }
            )

    def trigger_calibration(self) -> None:
        """Start 3-second guided calibration sequence."""
        self._manual_calibrating = True
        self.calibration_engine.reset()
        if self.orchestrator is not None:
            self.orchestrator.operating_mode = OperatingMode.CALIBRATING
            self._emit_current_state()

    def set_snooze(self, minutes: int) -> None:
        """Set manual snooze duration in minutes."""
        if minutes <= 0:
            self._snooze_until = 0.0
            if self.orchestrator is not None:
                self.orchestrator.operating_mode = OperatingMode.ACTIVE_MONITORING
            self.snooze_status_changed.emit(False, 0.0)
        else:
            self._snooze_until = time.monotonic() + (minutes * 60.0)
            if self.orchestrator is not None:
                self.orchestrator.operating_mode = OperatingMode.PAUSED_MANUAL
            self.snooze_status_changed.emit(True, float(minutes * 60))
        self._emit_current_state()

    def set_meeting_auto_pause(self, in_meeting: bool) -> None:
        """Handle fullscreen meeting auto-pause."""
        if self.orchestrator is None:
            return
        if in_meeting:
            if self.orchestrator.operating_mode == OperatingMode.ACTIVE_MONITORING:
                self.orchestrator.operating_mode = OperatingMode.PAUSED_MEETING_AUTO
                self._emit_current_state()
        else:
            if self.orchestrator.operating_mode == OperatingMode.PAUSED_MEETING_AUTO:
                self.orchestrator.operating_mode = OperatingMode.ACTIVE_MONITORING
                self._emit_current_state()

    def on_os_sleep(self) -> None:
        """Handle OS Sleep notification: flush DB and freeze session."""
        if self.orchestrator is not None:
            self.db_worker.enqueue_flush(now=datetime.now())

    def on_os_wake(self) -> None:
        """Handle OS Wake notification: resume monitoring."""
        if self.orchestrator is not None:
            self.orchestrator.operating_mode = OperatingMode.ACTIVE_MONITORING
            self._emit_current_state()

    def run(self) -> None:
        """Main loop: consume frames, run CV inference, evaluate posture."""
        # Initialize default session in SQLite if needed
        session_id = self.db_worker.db_manager.create_session()

        # Ensure at least one base calibration profile exists for FK constraints
        profile = self.db_worker.db_manager.get_calibration_profile(1)
        if profile is None:
            default_rec = CalibrationProfileRecord(
                profile_id=None,
                created_at=None,
                base_neck_ort_ratio=0.60,
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
            profile_id = self.db_worker.db_manager.save_calibration_profile(default_rec)
        else:
            profile_id = profile.profile_id or 1

        ref = CalibrationReference(
            base_neck_ort_ratio=0.60,
            base_eye_sh_ratio=0.30,
            base_sh_width_px=200.0,
            base_sh_y_mid=400.0,
            base_sh_angle_deg=0.0,
            base_head_pitch_deg=0.0,
            base_head_roll_deg=0.0,
        )

        self.orchestrator = SessionOrchestrator(
            db=self.db_worker.db_manager,
            session_id=session_id,
            profile_id=profile_id,
            ref=ref,
        )

        prev_timestamp = time.monotonic()

        while self._running:
            try:
                frame = self.frame_queue.get(timeout=0.25)
            except queue.Empty:
                continue

            current_mono = frame.timestamp_s
            dt = max(0.05, current_mono - prev_timestamp)
            prev_timestamp = current_mono

            # Check snooze expiry
            if self._snooze_until > 0.0:
                remaining = self._snooze_until - current_mono
                if remaining <= 0.0:
                    self.set_snooze(0)
                else:
                    self.snooze_status_changed.emit(True, remaining)

            # 1. Sequential Inference: Pose Lite followed by Face
            pose_data, detected_faces = self.detector.detect(frame.image, current_mono)

            # 2. Calibration Mode Processing
            if self._manual_calibrating or (
                self.orchestrator is not None
                and self.orchestrator.operating_mode == OperatingMode.CALIBRATING
            ):
                self._process_calibration_tick(frame.image, pose_data, detected_faces)
                continue

            # 3. Active Monitoring Processing
            if self.orchestrator is None:
                continue

            img_w = (
                frame.image.shape[1]
                if hasattr(frame.image, "shape") and len(frame.image.shape) >= 2
                else 640.0
            )
            x_calib_center = float(img_w) / 2.0

            primary_face = associate_primary_subject(detected_faces, pose_data, x_calib_center)
            has_face = primary_face is not None
            has_shoulders = pose_data is not None and pose_data.shoulder_width > 1e-4

            w_eye_ratio: float | None = None
            if has_face and primary_face is not None:
                w_eye_corr = calculate_yaw_compensated_eye_width(
                    primary_face.w_eye, primary_face.yaw_deg
                )
                base_eye = (
                    self.orchestrator.ref.base_eye_sh_ratio * self.orchestrator.ref.base_sh_width_px
                )
                if base_eye > 1e-4:
                    w_eye_ratio = w_eye_corr / base_eye

            vis_state, _ = self.visibility_evaluator.evaluate(
                has_face=has_face,
                has_shoulders=has_shoulders,
                w_eye_ratio=w_eye_ratio,
                dt=dt,
            )

            is_moving = False
            v_norm = 0.0
            if has_shoulders and pose_data is not None:
                v_norm, is_moving = self.velocity_detector.update(
                    pose_data.shoulder_midpoint.x,
                    pose_data.shoulder_midpoint.y,
                    pose_data.shoulder_width,
                    dt,
                )

            snapshot = self.orchestrator.process_frame(
                pose=pose_data if vis_state == VisibilityState.MEASURABLE else None,
                face=primary_face
                if vis_state in (VisibilityState.MEASURABLE, VisibilityState.OCCLUDED_LOWER)
                else None,
                visibility=vis_state,
                is_moving=is_moving,
                dt=dt,
                current_time_s=current_mono,
                now=datetime.now(),
            )

            # Emit state changes
            self.state_changed.emit(
                snapshot.operating_mode.value,
                snapshot.visibility_state.value,
                snapshot.ui_level.value,
                snapshot.violation_flags,
            )

            if (
                vis_state == VisibilityState.MEASURABLE
                and pose_data is not None
                and primary_face is not None
            ):
                metrics = calculate_posture_metrics(
                    pose_data, primary_face, self.orchestrator.ref, dt=dt
                )
                self.metrics_calculated.emit(metrics, current_mono)

            # Emit live telemetry for calibration and empirical tuning (Bab 8)
            d_hats: dict[str, float] = {}
            buckets: dict[str, float] = {}
            if snapshot.tracker_states:
                d_hats = {k: st.filtered_deviation for k, st in snapshot.tracker_states.items()}
                buckets = {k: st.bucket_s for k, st in snapshot.tracker_states.items()}

            telemetry_data = {
                "timestamp": current_mono,
                "vis_state": snapshot.visibility_state.value,
                "op_mode": snapshot.operating_mode.value,
                "ui_level": snapshot.ui_level.value,
                "is_moving": is_moving,
                "v_norm": v_norm,
                "d_hat": d_hats,
                "sigmas": dict(self.orchestrator.noise_adapter.current_sigmas),
                "tau_enter": dict(self.orchestrator.noise_adapter.effective_thresholds),
                "tau_exit": {
                    k: tracker.tau_exit
                    for k, tracker in self.orchestrator.escalation_controller.trackers.items()
                },
                "violations": {
                    "fhp": snapshot.violation_flags.v_fhp,
                    "neck": snapshot.violation_flags.v_neck_comp,
                    "slump": snapshot.violation_flags.v_slump,
                    "pitch": snapshot.violation_flags.v_gaze_pitch,
                    "tilt": snapshot.violation_flags.v_tilt,
                },
                "buckets": buckets,
            }
            self.live_telemetry.emit(telemetry_data)

            # Emit live annotated frame for GUI Camera View
            annotated = FrameAnnotator.annotate(
                image_bgr=frame.image,
                pose=pose_data,
                face=primary_face,
                vis_state=vis_state,
                violations=snapshot.violation_flags,
                is_moving=is_moving,
                is_calibrating=False,
            )
            self.frame_annotated.emit(annotated)

            # Check Alert and Microbreak triggers
            if snapshot.ui_level == UIIntrusivenessLevel.ACTIVE_OVERLAY:
                self.alert_triggered.emit("ACTIVE_OVERLAY", 15.0)
            elif snapshot.ui_level == UIIntrusivenessLevel.MICROBREAK_PROMPT:
                self.microbreak_triggered.emit(60.0)

            # Check setup drift
            if snapshot.operating_mode == OperatingMode.NEEDS_RECALIBRATION:
                self.drift_detected.emit(
                    "Setup drift terdeteksi (posisi kursi atau engsel layar berubah)."
                )

    def _process_calibration_tick(
        self,
        image: np.ndarray,
        pose: PoseLandmarkData | None,
        faces: list[FaceLandmarkData],
    ) -> None:
        """Step guided 3-second calibration sequence."""
        target = self.calibration_engine.target_samples

        if pose is None or not faces:
            prompt = "Posisikan wajah dan bahu di depan kamera."
            self.calibration_progress.emit(
                len(self.calibration_engine.samples),
                target,
                False,
                prompt,
            )
            self.frame_annotated.emit(
                FrameAnnotator.annotate(
                    image_bgr=image,
                    pose=pose,
                    face=faces[0] if faces else None,
                    is_calibrating=True,
                    calib_current=len(self.calibration_engine.samples),
                    calib_total=target,
                    calib_plausible=False,
                    calib_feedback=prompt,
                )
            )
            return

        face = faces[0]
        is_valid, reason, _ = self.calibration_engine.validate_sample_plausibility(pose, face)
        if not is_valid:
            prompt = reason or "Luruskan posisi tubuh..."
            self.calibration_progress.emit(
                len(self.calibration_engine.samples),
                target,
                False,
                prompt,
            )
            self.frame_annotated.emit(
                FrameAnnotator.annotate(
                    image_bgr=image,
                    pose=pose,
                    face=face,
                    is_calibrating=True,
                    calib_current=len(self.calibration_engine.samples),
                    calib_total=target,
                    calib_plausible=False,
                    calib_feedback=prompt,
                )
            )
            return

        self.calibration_engine.add_sample(pose, face)
        count = len(self.calibration_engine.samples)
        prompt = "Pertahankan posisi tegak lurus..."

        self.calibration_progress.emit(count, target, True, prompt)
        self.frame_annotated.emit(
            FrameAnnotator.annotate(
                image_bgr=image,
                pose=pose,
                face=face,
                is_calibrating=True,
                calib_current=count,
                calib_total=target,
                calib_plausible=True,
                calib_feedback=prompt,
            )
        )

        if count >= target:
            try:
                _ref, rec = self.calibration_engine.finalize()
                self._manual_calibrating = False
                profile_id = self.db_worker.db_manager.save_calibration_profile(rec)
                updated_rec = CalibrationProfileRecord(
                    profile_id=profile_id,
                    created_at=rec.created_at,
                    base_neck_ort_ratio=rec.base_neck_ort_ratio,
                    base_eye_sh_ratio=rec.base_eye_sh_ratio,
                    base_sh_width_px=rec.base_sh_width_px,
                    base_sh_y_mid=rec.base_sh_y_mid,
                    base_sh_angle_deg=rec.base_sh_angle_deg,
                    base_head_pitch_deg=rec.base_head_pitch_deg,
                    base_head_roll_deg=rec.base_head_roll_deg,
                    sigma_fhp=rec.sigma_fhp,
                    sigma_neck=rec.sigma_neck,
                    sigma_slump=rec.sigma_slump,
                    sigma_pitch=rec.sigma_pitch,
                    sigma_tilt=rec.sigma_tilt,
                )
                self.set_active_profile(updated_rec)
                if self.orchestrator is not None:
                    self.orchestrator.operating_mode = OperatingMode.ACTIVE_MONITORING
                self.calibration_completed.emit(updated_rec)
                self._emit_current_state()
            except Exception as e:
                self.calibration_engine.reset()
                self.calibration_progress.emit(0, target, False, str(e))

    def _emit_current_state(self) -> None:
        """Emit state change with current orchestrator snapshot."""
        if self.orchestrator is not None:
            self.state_changed.emit(
                self.orchestrator.operating_mode.value,
                self.orchestrator.visibility_state.value,
                self.orchestrator.ui_level.value,
                self.orchestrator.violation_flags,
            )
