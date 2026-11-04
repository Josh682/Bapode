"""Unit tests for computer vision geometry, subject association, and 5 posture metrics."""

import math

import pytest

from tracker.states import VisibilityState
from vision.association import VisibilityEvaluator, associate_primary_subject
from vision.landmarks import BoundingBox, FaceLandmarkData, Point2D, PoseLandmarkData
from vision.metrics import (
    CalibrationReference,
    calculate_orthogonal_neck_length,
    calculate_posture_metrics,
    calculate_shoulder_geometry,
    calculate_yaw_compensated_eye_width,
)


@pytest.fixture
def base_calibration() -> CalibrationReference:
    """Standard calibrated baseline posture reference."""
    # Subject sitting upright at 720p:
    # shoulders: LS = (740, 400), RS = (540, 400) -> W_sh = 200, Y_mid = 400, angle = 0 deg
    # ears: LE = (680, 280), RE = (600, 280) -> Y_mid = 280, L_neck_ort = 400 - 280 = 120
    # eyes: left = (670, 260), right = (610, 260) -> W_eye = 60
    # ratio neck/sh = 120 / 200 = 0.60
    # ratio eye/sh = 60 / 200 = 0.30
    return CalibrationReference(
        base_neck_ort_ratio=0.60,
        base_eye_sh_ratio=0.30,
        base_sh_width_px=200.0,
        base_sh_y_mid=400.0,
        base_sh_angle_deg=0.0,
        base_head_pitch_deg=0.0,
        base_head_roll_deg=0.0,
        y_c=360.0,
    )


class TestSubjectAssociation:
    """Test multi-person face selection and Pose nose landmark spatial verification."""

    def test_single_face_matched_with_pose(self) -> None:
        face = FaceLandmarkData(
            bbox=BoundingBox(580, 200, 700, 320),
            pitch_deg=0.0,
            yaw_deg=0.0,
            roll_deg=0.0,
            eye_outer_left=Point2D(670, 260),
            eye_outer_right=Point2D(610, 260),
        )
        pose = PoseLandmarkData(
            left_shoulder=Point2D(740, 400),
            right_shoulder=Point2D(540, 400),
            left_ear=Point2D(680, 280),
            right_ear=Point2D(600, 280),
            nose=Point2D(640, 260),  # inside face bbox, center is (640, 260) -> dist = 0
        )
        chosen = associate_primary_subject([face], pose, x_calib_center=640.0)
        assert chosen == face

    def test_multi_person_picks_closest_largest_face(self) -> None:
        # User face: near center (640), area = 120 * 120 = 14400
        user_face = FaceLandmarkData(
            bbox=BoundingBox(580, 200, 700, 320),
            pitch_deg=0.0,
            yaw_deg=0.0,
            roll_deg=0.0,
            eye_outer_left=Point2D(670, 260),
            eye_outer_right=Point2D(610, 260),
        )
        # Background passerby face: off to the side (x=1100), smaller area
        passerby = FaceLandmarkData(
            bbox=BoundingBox(1050, 150, 1150, 250),
            pitch_deg=5.0,
            yaw_deg=10.0,
            roll_deg=0.0,
            eye_outer_left=Point2D(1130, 200),
            eye_outer_right=Point2D(1070, 200),
        )
        chosen = associate_primary_subject([passerby, user_face], None, x_calib_center=640.0)
        assert chosen == user_face

    def test_pose_nose_outside_face_bbox_rejects_frame(self) -> None:
        # Passerby face off to side, while pose is in center
        face = FaceLandmarkData(
            bbox=BoundingBox(1000, 200, 1120, 320),
            pitch_deg=0.0,
            yaw_deg=0.0,
            roll_deg=0.0,
            eye_outer_left=Point2D(1090, 260),
            eye_outer_right=Point2D(1030, 260),
        )
        pose = PoseLandmarkData(
            left_shoulder=Point2D(740, 400),
            right_shoulder=Point2D(540, 400),
            left_ear=Point2D(680, 280),
            right_ear=Point2D(600, 280),
            nose=Point2D(640, 260),  # Nose is at 640, way outside [1000, 1120]
        )
        chosen = associate_primary_subject([face], pose, x_calib_center=640.0)
        assert chosen is None

    def test_pose_nose_distance_exceeds_threshold_rejects_frame(self) -> None:
        # Face box contains nose at extreme corner, but dist >= 0.25 * W_sh (0.25 * 200 = 50px)
        face = FaceLandmarkData(
            bbox=BoundingBox(500, 100, 780, 420),  # large bbox, center is (640, 260)
            pitch_deg=0.0,
            yaw_deg=0.0,
            roll_deg=0.0,
            eye_outer_left=Point2D(670, 260),
            eye_outer_right=Point2D(610, 260),
        )
        pose = PoseLandmarkData(
            left_shoulder=Point2D(740, 400),
            right_shoulder=Point2D(540, 400),
            left_ear=Point2D(680, 280),
            right_ear=Point2D(600, 280),
            nose=Point2D(700, 260),  # dist to center (640, 260) is 60px > 50px (0.25 * 200)
        )
        chosen = associate_primary_subject([face], pose, x_calib_center=640.0)
        assert chosen is None


class TestVisibilityEvaluator:
    """Test 2x2 matrix and Extreme Close Proximity detection."""

    def test_2x2_visibility_matrix(self) -> None:
        evaluator = VisibilityEvaluator()

        # Both present
        st, prox = evaluator.evaluate(has_face=True, has_shoulders=True)
        assert st == VisibilityState.MEASURABLE
        assert prox is False

        # Face only (OCCLUDED_LOWER)
        st, prox = evaluator.evaluate(has_face=True, has_shoulders=False, w_eye_ratio=1.0)
        assert st == VisibilityState.OCCLUDED_LOWER
        assert prox is False

        # Shoulders only (OCCLUDED_UPPER)
        st, prox = evaluator.evaluate(has_face=False, has_shoulders=True)
        assert st == VisibilityState.OCCLUDED_UPPER
        assert prox is False

        # Neither (ABSENT)
        st, prox = evaluator.evaluate(has_face=False, has_shoulders=False)
        assert st == VisibilityState.ABSENT
        assert prox is False

    def test_extreme_close_proximity_sustained_15s(self) -> None:
        evaluator = VisibilityEvaluator(close_proximity_threshold=1.30, close_proximity_time_s=15.0)

        # 14 seconds of ratio = 1.35 (not yet 15s)
        for _ in range(70):  # 70 * 0.2s = 14.0s
            st, prox = evaluator.evaluate(
                has_face=True, has_shoulders=False, w_eye_ratio=1.35, dt=0.2
            )
            assert st == VisibilityState.OCCLUDED_LOWER
            assert prox is False

        st = VisibilityState.ABSENT
        prox = False
        # 5 more steps = 1.0s -> total 15.0s -> triggers proximity alert
        for _ in range(5):
            st, prox = evaluator.evaluate(
                has_face=True, has_shoulders=False, w_eye_ratio=1.35, dt=0.2
            )
        assert st == VisibilityState.OCCLUDED_LOWER
        assert prox is True


class TestPostureMetrics:
    """Test 5 one-way posture metrics and geometric compensations."""

    def test_baseline_posture_yields_zero_deviations(
        self, base_calibration: CalibrationReference
    ) -> None:
        pose = PoseLandmarkData(
            left_shoulder=Point2D(740, 400),
            right_shoulder=Point2D(540, 400),
            left_ear=Point2D(680, 280),
            right_ear=Point2D(600, 280),
            nose=Point2D(640, 260),
        )
        face = FaceLandmarkData(
            bbox=BoundingBox(580, 200, 700, 320),
            pitch_deg=0.0,
            yaw_deg=0.0,
            roll_deg=0.0,
            eye_outer_left=Point2D(670, 260),
            eye_outer_right=Point2D(610, 260),
        )
        res = calculate_posture_metrics(pose, face, base_calibration)
        assert res.d_fhp == pytest.approx(0.0, abs=1e-3)
        assert res.d_neck == pytest.approx(0.0, abs=1e-3)
        assert res.d_slump == pytest.approx(0.0, abs=1e-3)
        assert res.d_pitch == pytest.approx(0.0, abs=1e-3)
        assert res.d_tilt == pytest.approx(0.0, abs=1e-3)

    def test_orthogonal_neck_length_with_shoulder_roll(self) -> None:
        # Tilt body by 30 degrees counter-clockwise
        theta_deg = 30.0
        theta_rad = math.radians(theta_deg)
        w_sh = 200.0
        mid = Point2D(640, 400)

        # Shoulders rotated by theta
        dx = (w_sh / 2.0) * math.cos(theta_rad)
        dy = (w_sh / 2.0) * math.sin(theta_rad)
        pose = PoseLandmarkData(
            left_shoulder=Point2D(mid.x + dx, mid.y + dy),
            right_shoulder=Point2D(mid.x - dx, mid.y - dy),
            # Neck vector perpendicular to shoulder line at length 120px
            # Normal vector: (-sin(theta), -cos(theta))
            left_ear=Point2D(
                mid.x - 120.0 * math.sin(theta_rad) + 40.0 * math.cos(theta_rad),
                mid.y - 120.0 * math.cos(theta_rad) + 40.0 * math.sin(theta_rad),
            ),
            right_ear=Point2D(
                mid.x - 120.0 * math.sin(theta_rad) - 40.0 * math.cos(theta_rad),
                mid.y - 120.0 * math.cos(theta_rad) - 40.0 * math.sin(theta_rad),
            ),
            nose=Point2D(mid.x, mid.y - 120.0),
        )
        _dx, _dy, calc_theta, calc_w = calculate_shoulder_geometry(pose)
        assert calc_theta == pytest.approx(30.0, abs=1e-2)
        assert calc_w == pytest.approx(200.0, abs=1e-2)

        l_neck = calculate_orthogonal_neck_length(pose, calc_theta)
        # Expected orthogonal projection is exactly 120.0px!
        assert l_neck == pytest.approx(120.0, abs=1e-2)

    def test_yaw_compensated_eye_width(self) -> None:
        # At 0 yaw: cos(0) = 1.0 -> W_eye_corr = W_eye
        assert calculate_yaw_compensated_eye_width(60.0, 0.0) == pytest.approx(60.0)

        # At 30 yaw: cos(30 deg) = 0.8660 -> W_eye_corr = 60 / 0.8660 = 69.28
        w_corr = calculate_yaw_compensated_eye_width(60.0, 30.0)
        assert w_corr == pytest.approx(60.0 / math.cos(math.radians(30.0)))

    def test_slump_perspective_optical_invariance_when_leaning_forward(
        self, base_calibration: CalibrationReference
    ) -> None:
        """CRITICAL: Leaning forward closer to camera must NOT trigger slump false alarm.

        Optical perspective model:
        S_w = W_sh / W_sh_base (e.g. 1.25x closer)
        Y_exp = Y_c + S_w * (Y_sh_base - Y_c)
        When leaning forward without slumping:
        Y_sh_mid drops naturally to Y_exp, so d_slump == 0.0!
        """
        s_w = 1.25  # Moved 25% closer to screen
        w_sh_new = 200.0 * s_w  # 250px
        # Y_c = 360, base Y_sh = 400.
        # Expected Y_sh = 360 + 1.25 * (400 - 360) = 360 + 50 = 410.0
        y_sh_new = 360.0 + s_w * (400.0 - 360.0)

        pose_closer = PoseLandmarkData(
            left_shoulder=Point2D(640 + w_sh_new / 2.0, y_sh_new),
            right_shoulder=Point2D(640 - w_sh_new / 2.0, y_sh_new),
            # Neck scaled by s_w
            left_ear=Point2D(640 + 40.0 * s_w, y_sh_new - 120.0 * s_w),
            right_ear=Point2D(640 - 40.0 * s_w, y_sh_new - 120.0 * s_w),
            nose=Point2D(640, y_sh_new - 140.0 * s_w),
        )
        face_closer = FaceLandmarkData(
            bbox=BoundingBox(550, 180, 730, 360),
            pitch_deg=0.0,
            yaw_deg=0.0,
            roll_deg=0.0,
            # Eyes scaled by s_w
            eye_outer_left=Point2D(640 + 30.0 * s_w, 260),
            eye_outer_right=Point2D(640 - 30.0 * s_w, 260),
        )

        res = calculate_posture_metrics(pose_closer, face_closer, base_calibration)
        # Even though shoulder appeared lower in pixels (410 vs 400),
        # perspective compensation correctly deduces d_slump = 0!
        assert res.d_slump == pytest.approx(0.0, abs=1e-3)

    def test_actual_slump_triggers_positive_deviation(
        self, base_calibration: CalibrationReference
    ) -> None:
        # Same distance (W_sh = 200), but shoulders slumped down from 400 to 450
        pose_slump = PoseLandmarkData(
            left_shoulder=Point2D(740, 450),
            right_shoulder=Point2D(540, 450),
            left_ear=Point2D(680, 330),
            right_ear=Point2D(600, 330),
            nose=Point2D(640, 310),
        )
        face = FaceLandmarkData(
            bbox=BoundingBox(580, 250, 700, 370),
            pitch_deg=0.0,
            yaw_deg=0.0,
            roll_deg=0.0,
            eye_outer_left=Point2D(670, 310),
            eye_outer_right=Point2D(610, 310),
        )
        res = calculate_posture_metrics(pose_slump, face, base_calibration)
        # d_slump = (450 - 400) / 200 = 0.25 > 0
        assert res.d_slump == pytest.approx(0.25, abs=1e-3)

    def test_yaw_gate_suspends_fhp_when_turning_head(
        self, base_calibration: CalibrationReference
    ) -> None:
        pose = PoseLandmarkData(
            left_shoulder=Point2D(740, 400),
            right_shoulder=Point2D(540, 400),
            left_ear=Point2D(680, 280),
            right_ear=Point2D(600, 280),
            nose=Point2D(640, 260),
        )
        # Head turned 25 degrees (|yaw| > 15 deg)
        face_turned = FaceLandmarkData(
            bbox=BoundingBox(580, 200, 700, 320),
            pitch_deg=0.0,
            yaw_deg=25.0,
            roll_deg=0.0,
            eye_outer_left=Point2D(670, 260),
            eye_outer_right=Point2D(610, 260),
        )
        res = calculate_posture_metrics(pose, face_turned, base_calibration)
        assert res.yaw_gate_active is True
        assert res.d_fhp == 0.0  # Suspended!
