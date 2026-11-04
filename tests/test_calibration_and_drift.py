"""Unit tests for Calibration Engine, Plausibility Gate, Noise Floor Adapter, and Drift Detector."""

import math

import pytest

from tracker.calibration import (
    CalibrationEngine,
    CalibrationError,
    NoiseFloorAdapter,
    PlausibilityGateError,
    SetupDriftDetector,
)
from vision.landmarks import BoundingBox, FaceLandmarkData, Point2D, PoseLandmarkData
from vision.metrics import CalibrationReference


def make_landmarks(
    *,
    w_sh: float = 200.0,
    y_sh_mid: float = 400.0,
    theta_sh_deg: float = 0.0,
    neck_len_ort: float = 100.0,
    w_eye: float = 60.0,
    pitch_deg: float = 0.0,
    yaw_deg: float = 0.0,
    roll_deg: float = 0.0,
) -> tuple[PoseLandmarkData, FaceLandmarkData]:
    """Helper creating PoseLandmarkData and FaceLandmarkData with specified geometry."""
    x_center = 640.0

    # Shoulder tilt angle
    theta_rad = math.radians(theta_sh_deg)
    dx_sh = w_sh * math.cos(theta_rad)
    dy_sh = w_sh * math.sin(theta_rad)

    ls = Point2D(x_center + dx_sh / 2.0, y_sh_mid + dy_sh / 2.0)
    rs = Point2D(x_center - dx_sh / 2.0, y_sh_mid - dy_sh / 2.0)

    # Ears for orthogonal neck projection
    # Neck projection vector: L_neck_ort = -(dx)*sin(theta) - (dy)*cos(theta)
    y_ear_mid = y_sh_mid - neck_len_ort
    le = Point2D(x_center + 40.0, y_ear_mid)
    re = Point2D(x_center - 40.0, y_ear_mid)
    nose = Point2D(x_center, y_ear_mid + 20.0)

    pose = PoseLandmarkData(
        left_shoulder=ls,
        right_shoulder=rs,
        left_ear=le,
        right_ear=re,
        nose=nose,
    )

    # Eyes
    y_eye = y_ear_mid - 20.0
    half_eye = w_eye / 2.0
    eye_left = Point2D(x_center + half_eye, y_eye)
    eye_right = Point2D(x_center - half_eye, y_eye)

    face = FaceLandmarkData(
        bbox=BoundingBox(x_center - 60.0, y_eye - 40.0, x_center + 60.0, y_eye + 60.0),
        pitch_deg=pitch_deg,
        yaw_deg=yaw_deg,
        roll_deg=roll_deg,
        eye_outer_left=eye_left,
        eye_outer_right=eye_right,
    )
    return pose, face


class TestCalibrationPlausibilityGate:
    """Test 3-second guided calibration and Plausibility Gate rejection rules."""

    def test_plausibility_head_roll_exceeded(self) -> None:
        """Reject calibration when head roll exceeds 5.0 deg."""
        engine = CalibrationEngine()
        pose, face = make_landmarks(roll_deg=5.5)

        with pytest.raises(PlausibilityGateError, match=r"Head roll .* exceeds 5.0° limit"):
            engine.add_sample(pose, face, strict=True)

    def test_plausibility_head_yaw_exceeded(self) -> None:
        """Reject calibration when head yaw exceeds 8.0 deg."""
        engine = CalibrationEngine()
        pose, face = make_landmarks(yaw_deg=8.5)

        with pytest.raises(PlausibilityGateError, match=r"Head yaw .* exceeds 8.0° limit"):
            engine.add_sample(pose, face, strict=True)

    def test_plausibility_shoulder_tilt_exceeded(self) -> None:
        """Reject calibration when shoulder tilt exceeds 6.0 deg."""
        engine = CalibrationEngine()
        pose, face = make_landmarks(theta_sh_deg=6.5)

        with pytest.raises(PlausibilityGateError, match=r"Shoulder tilt .* exceeds 6.0° limit"):
            engine.add_sample(pose, face, strict=True)

    def test_plausibility_neck_ratio_unnatural(self) -> None:
        """Reject calibration when neck/shoulder ratio is outside [0.35, 0.75]."""
        engine = CalibrationEngine()

        # Ratio too small: 50 / 200 = 0.25 < 0.35
        pose_small, face_small = make_landmarks(w_sh=200.0, neck_len_ort=50.0)
        with pytest.raises(PlausibilityGateError, match=r"Neck/shoulder ratio .* outside"):
            engine.add_sample(pose_small, face_small, strict=True)

        # Ratio too large: 160 / 200 = 0.80 > 0.75
        pose_large, face_large = make_landmarks(w_sh=200.0, neck_len_ort=160.0)
        with pytest.raises(PlausibilityGateError, match=r"Neck/shoulder ratio .* outside"):
            engine.add_sample(pose_large, face_large, strict=True)

    def test_valid_15_samples_calibration_success(self) -> None:
        """Successful 15-sample calibration with normal noise produces reference and profile."""
        engine = CalibrationEngine(target_samples=15)

        # Collect 15 clean samples with negligible jitter
        for i in range(15):
            jitter = (i % 3 - 1) * 0.2
            pose, face = make_landmarks(
                w_sh=200.0 + jitter,
                neck_len_ort=100.0 + jitter,
                pitch_deg=1.0,
                yaw_deg=0.5,
                roll_deg=-0.5,
            )
            accepted = engine.add_sample(pose, face, timestamp=i * 0.2)
            assert accepted

        assert engine.is_complete
        ref, profile = engine.finalize()

        assert ref.base_sh_width_px == pytest.approx(200.0, abs=1.0)
        assert ref.base_neck_ort_ratio == pytest.approx(0.50, abs=0.05)
        assert profile.sigma_fhp >= 0.0
        assert profile.sigma_pitch >= 0.0

    def test_calibration_rejected_when_mad_noise_floor_too_high(self) -> None:
        """Reject calibration when 4 * sigma_0 > 0.80 * tau_default_enter."""
        engine = CalibrationEngine(target_samples=15)

        # Generate varied heavy eye width jitter (fhp noise) across the 15 samples
        for i in range(15):
            jitter_eye = ((-1) ** i) * (10.0 + i * 2.0)
            pose, face = make_landmarks(
                w_sh=200.0,
                neck_len_ort=100.0,
                w_eye=60.0 + jitter_eye,
            )
            engine.add_sample(pose, face, timestamp=i * 0.2)

        with pytest.raises(
            CalibrationError, match="Pencahayaan terlalu redup atau posisi belum stabil"
        ):
            engine.finalize()

    def test_insufficient_samples_raises_error(self) -> None:
        """Attempting to finalize before 15 samples raises CalibrationError."""
        engine = CalibrationEngine(target_samples=15)
        pose, face = make_landmarks()
        engine.add_sample(pose, face)
        with pytest.raises(CalibrationError, match="Sampel kalibrasi belum mencukupi"):
            engine.finalize()


class TestNoiseFloorAdapter:
    """Test Running Quiet-Period Noise Floor Adapter."""

    def test_noise_buffer_only_accumulates_in_quiet_period(self) -> None:
        """Ring buffer collects samples ONLY when is_safe_zone=True AND is_moving=False."""
        adapter = NoiseFloorAdapter(min_samples_to_adapt=5)
        devs = {"fhp": 0.02, "neck": 0.02, "slump": 0.01, "pitch": 1.0, "tilt": 1.0}

        # Case 1: Moving is True -> should NOT collect
        adapter.update(devs, is_safe_zone=True, is_moving=True)
        assert len(adapter.buffers["fhp"]) == 0

        # Case 2: In violation zone (is_safe_zone=False) -> should NOT collect
        adapter.update(devs, is_safe_zone=False, is_moving=False)
        assert len(adapter.buffers["fhp"]) == 0

        # Case 3: Quiet period (is_safe_zone=True and is_moving=False) -> accumulates!
        adapter.update(devs, is_safe_zone=True, is_moving=False)
        assert len(adapter.buffers["fhp"]) == 1

    def test_one_way_threshold_adaptation(self) -> None:
        """tau_enter can only increase above default; never drops below default enter."""
        adapter = NoiseFloorAdapter(
            tau_enter_defaults={"fhp": 0.08},
            tau_caps={"fhp": 0.16},
            window_size=15,
            min_samples_to_adapt=5,
        )

        # 1. Very clean data (sigma near 0.0) -> tau_enter stays at default 0.08
        for _ in range(15):
            adapter.update({"fhp": 0.01}, is_safe_zone=True, is_moving=False)
        assert adapter.effective_thresholds["fhp"] == pytest.approx(0.08)

        # 2. Elevated jitter: values oscillating with high MAD
        for i in range(15):
            val = 0.02 + 0.025 * (i % 5)
            adapter.update({"fhp": val}, is_safe_zone=True, is_moving=False)

        assert adapter.effective_thresholds["fhp"] > 0.08
        assert adapter.effective_thresholds["fhp"] <= 0.16

    def test_low_light_warning_triggered_at_tau_cap(self) -> None:
        """When 4 * sigma_i touches or exceeds tau_cap_i, LOW_LIGHT_WARNING is activated."""
        adapter = NoiseFloorAdapter(
            tau_enter_defaults={"fhp": 0.08},
            tau_caps={"fhp": 0.16},
            min_samples_to_adapt=5,
        )

        assert not adapter.low_light_warning

        # Inject extreme noise such that 4 * sigma >= 0.16
        for i in range(15):
            val = 0.02 + 0.06 * (i % 5)
            adapter.update({"fhp": val}, is_safe_zone=True, is_moving=False)

        assert adapter.effective_thresholds["fhp"] == pytest.approx(0.16)
        assert adapter.low_light_warning


class TestSetupDriftDetector:
    """Test setup drift detection for chair distance and screen hinge tilt."""

    @pytest.fixture
    def baseline_ref(self) -> CalibrationReference:
        return CalibrationReference(
            base_neck_ort_ratio=0.50,
            base_eye_sh_ratio=0.30,
            base_sh_width_px=200.0,
            base_sh_y_mid=400.0,
            base_sh_angle_deg=0.0,
            base_head_pitch_deg=0.0,
            base_head_roll_deg=0.0,
            y_c=360.0,
        )

    def test_normal_posture_no_drift(self, baseline_ref: CalibrationReference) -> None:
        """Normal variations within bounds do not accumulate drift."""
        detector = SetupDriftDetector(ref=baseline_ref, drift_duration_threshold_s=10.0)

        # 10 seconds of normal upright posture
        for _ in range(50):
            detected, reason = detector.update(
                w_sh=205.0,  # 205 / 200 = 1.025 in [0.78, 1.30]
                y_sh_mid=402.0,
                y_eye_mid=300.0,
                pitch_head=1.0,
                d_fhp=0.02,
                dt=0.2,
            )
            assert not detected
            assert reason is None
        assert detector.drift_elapsed_s == 0.0

    def test_chair_distance_drift_triggers_after_10s(
        self, baseline_ref: CalibrationReference
    ) -> None:
        """Chair distance shift (W_sh / W_sh_base > 1.30 or < 0.78) triggers drift after 10.0s."""
        detector = SetupDriftDetector(ref=baseline_ref, drift_duration_threshold_s=10.0)

        # W_sh = 270.0 -> ratio = 270 / 200 = 1.35 > 1.30, while d_fhp = 0.02 is normal
        # 49 ticks at dt = 0.2s -> 9.8s (not yet triggered)
        for _ in range(49):
            detected, reason = detector.update(
                w_sh=270.0,
                y_sh_mid=400.0,
                y_eye_mid=300.0,
                pitch_head=0.0,
                d_fhp=0.02,
                dt=0.2,
            )
            assert not detected
            assert reason is None

        assert detector.drift_elapsed_s == pytest.approx(9.8, abs=0.01)

        # 50th tick -> reaches 10.0s!
        detected, reason = detector.update(
            w_sh=270.0,
            y_sh_mid=400.0,
            y_eye_mid=300.0,
            pitch_head=0.0,
            d_fhp=0.02,
            dt=0.2,
        )
        assert detected
        assert reason is not None
        assert "kursi" in reason.lower()

    def test_screen_hinge_drift_triggers_after_10s(
        self, baseline_ref: CalibrationReference
    ) -> None:
        """Synchronous vertical shift of shoulders, eyes, and pitch triggers screen hinge drift."""
        detector = SetupDriftDetector(
            ref=baseline_ref,
            base_eye_y_mid=300.0,
            drift_duration_threshold_s=10.0,
        )

        # Screen tilted back: both shoulders and eyes moved DOWN by 30px, pitch shifted by +8 deg
        # 50 ticks of 0.2s = 10.0s
        for i in range(50):
            detected, reason = detector.update(
                w_sh=200.0,
                y_sh_mid=430.0,  # delta_y_sh = +30.0
                y_eye_mid=330.0,  # delta_y_eye = +30.0
                pitch_head=8.0,  # delta_pitch = +8.0
                d_fhp=0.02,
                dt=0.2,
            )
            if i < 49:
                assert not detected
            else:
                assert detected
                assert reason is not None
                assert "engsel" in reason.lower()

    def test_temporary_movement_resets_drift_timer(
        self, baseline_ref: CalibrationReference
    ) -> None:
        """Intermittent movement that stops before 10.0s resets the drift timer to zero."""
        detector = SetupDriftDetector(ref=baseline_ref, drift_duration_threshold_s=10.0)

        # Drift active for 6 seconds (30 ticks)
        for _ in range(30):
            detector.update(
                w_sh=270.0,
                y_sh_mid=400.0,
                y_eye_mid=300.0,
                pitch_head=0.0,
                d_fhp=0.02,
                dt=0.2,
            )
        assert detector.drift_elapsed_s == pytest.approx(6.0, abs=0.01)

        # Return to normal
        detected, reason = detector.update(
            w_sh=200.0,
            y_sh_mid=400.0,
            y_eye_mid=300.0,
            pitch_head=0.0,
            d_fhp=0.02,
            dt=0.2,
        )
        assert not detected
        assert reason is None
        assert detector.drift_elapsed_s == 0.0
