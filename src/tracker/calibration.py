"""Guided 3-Second Calibration, MAD Noise Floor, and Setup Drift Detector.

Reference: docs/specs/03-calibration-and-drift.md & docs/ARCHITECTURE.md
Implements:
1. CalibrationEngine & PlausibilityGate:
   - 15 consecutive samples (3.0s at 5 FPS)
   - Checks |yaw_head| <= 8.0 deg, |roll_head| <= 5.0 deg, |theta_sh| <= 6.0 deg,
     and 0.35 <= L_neck_ort / W_sh <= 0.75
   - Computes baseline medians
   - Computes sigma_i_0 = 1.4826 * MAD(d_i)
   - Rejects if 4 * sigma_i_0 > 0.80 * tau_default_enter
     ("Pencahayaan terlalu redup atau posisi belum stabil")
2. NoiseFloorAdapter (Running Quiet-Period Noise Floor):
   - Ring buffer (deque) collecting d_i ONLY when safe (V_i == 0 and moving == False)
   - Adapts tau_enter_i = min(tau_cap_i, max(tau_default_enter_i, 4 * sigma_i(t)))
   - Low-light warning flag when 4 * sigma_i(t) reaches tau_cap_i
3. SetupDriftDetector:
   - Detects chair distance shift (W_sh / W_sh_base not in [0.78, 1.30] with normal d_fhp)
   - Detects screen hinge drift (synchronous shifts in Y_sh_mid, Y_eye_mid, and pitch_head)
   - Transitions to NEEDS_RECALIBRATION after >= 10.0s sustained drift
     without silent auto-recalibration
"""

import statistics
from collections import deque
from collections.abc import Sequence
from dataclasses import dataclass

from db.manager import CalibrationProfileRecord
from vision.landmarks import FaceLandmarkData, PoseLandmarkData
from vision.metrics import (
    CalibrationReference,
    calculate_orthogonal_neck_length,
    calculate_posture_metrics,
    calculate_shoulder_geometry,
    calculate_yaw_compensated_eye_width,
)

# Standard default enter thresholds
DEFAULT_TAU_ENTER: dict[str, float] = {
    "fhp": 0.08,
    "neck": 0.08,
    "slump": 0.06,
    "pitch": 12.0,
    "tilt": 8.0,
}

# Standard default ceiling caps (tau_cap)
DEFAULT_TAU_CAP: dict[str, float] = {
    "fhp": 0.16,
    "neck": 0.16,
    "slump": 0.12,
    "pitch": 24.0,
    "tilt": 16.0,
}

LOW_LIGHT_WARNING_MESSAGE = "Kualitas Cahaya Kamera Buruk — Akurasi Menurun"
RECALIBRATION_REASON_NOISY = "Pencahayaan terlalu redup atau posisi belum stabil"


class CalibrationError(Exception):
    """Exception raised when calibration fails or is rejected."""


class PlausibilityGateError(CalibrationError):
    """Exception raised when posture violates anatomical or orientation plausibility gate."""


def compute_mad_sigma(values: Sequence[float]) -> float:
    """Compute robust standard deviation estimate using Median Absolute Deviation (MAD).

    Formula:
        sigma_hat = 1.4826 * median(|x_i - median(x)|)
    """
    if not values:
        return 0.0
    med = statistics.median(values)
    abs_deviations = [abs(x - med) for x in values]
    mad = statistics.median(abs_deviations)
    return 1.4826 * mad


@dataclass(frozen=True, slots=True)
class PlausibilityGateLimits:
    """Threshold limits for the Calibration Plausibility Gate."""

    max_abs_yaw_deg: float = 8.0
    max_abs_roll_deg: float = 5.0
    max_abs_theta_sh_deg: float = 6.0
    min_neck_ratio: float = 0.35
    max_neck_ratio: float = 0.75


@dataclass(frozen=True, slots=True)
class RawCalibrationSample:
    """Extracted geometric features from a single frame during calibration."""

    pose: PoseLandmarkData
    face: FaceLandmarkData
    w_sh_px: float
    theta_sh_deg: float
    l_neck_ort_px: float
    w_eye_corr_px: float
    y_sh_mid: float
    y_eye_mid: float
    neck_ratio: float
    eye_sh_ratio: float
    timestamp: float


class CalibrationEngine:
    """3-second guided calibration collector and Plausibility Gate evaluator."""

    def __init__(
        self,
        target_samples: int = 15,
        limits: PlausibilityGateLimits | None = None,
        tau_enter_defaults: dict[str, float] | None = None,
    ) -> None:
        self.target_samples = target_samples
        self.limits = limits or PlausibilityGateLimits()
        self.tau_enter_defaults = tau_enter_defaults or DEFAULT_TAU_ENTER
        self.samples: list[RawCalibrationSample] = []

    def reset(self) -> None:
        """Clear collected calibration samples."""
        self.samples.clear()

    @property
    def sample_count(self) -> int:
        """Number of valid samples collected so far."""
        return len(self.samples)

    @property
    def is_complete(self) -> bool:
        """Return True if target sample count has been reached."""
        return len(self.samples) >= self.target_samples

    def validate_sample_plausibility(
        self,
        pose: PoseLandmarkData,
        face: FaceLandmarkData,
    ) -> tuple[bool, str | None, RawCalibrationSample]:
        """Verify if a single frame passes orientation and anatomical limits."""
        _, _, theta_sh_deg, w_sh_px = calculate_shoulder_geometry(pose)
        w_sh_safe = max(1.0, w_sh_px)
        l_neck_ort_px = calculate_orthogonal_neck_length(pose, theta_sh_deg)
        w_eye_corr_px = calculate_yaw_compensated_eye_width(face.w_eye, face.yaw_deg)

        neck_ratio = l_neck_ort_px / w_sh_safe
        eye_sh_ratio = w_eye_corr_px / w_sh_safe
        y_sh_mid = pose.shoulder_midpoint.y
        y_eye_mid = (face.eye_outer_left.y + face.eye_outer_right.y) / 2.0

        sample = RawCalibrationSample(
            pose=pose,
            face=face,
            w_sh_px=w_sh_px,
            theta_sh_deg=theta_sh_deg,
            l_neck_ort_px=l_neck_ort_px,
            w_eye_corr_px=w_eye_corr_px,
            y_sh_mid=y_sh_mid,
            y_eye_mid=y_eye_mid,
            neck_ratio=neck_ratio,
            eye_sh_ratio=eye_sh_ratio,
            timestamp=0.0,
        )

        # 1. Orientation bounds
        if abs(face.yaw_deg) > self.limits.max_abs_yaw_deg:
            msg = f"Head yaw ({face.yaw_deg:.1f}°) exceeds {self.limits.max_abs_yaw_deg}° limit"
            return False, msg, sample

        if abs(face.roll_deg) > self.limits.max_abs_roll_deg:
            msg = f"Head roll ({face.roll_deg:.1f}°) exceeds {self.limits.max_abs_roll_deg}° limit"
            return False, msg, sample

        if abs(theta_sh_deg) > self.limits.max_abs_theta_sh_deg:
            limit = self.limits.max_abs_theta_sh_deg
            msg = f"Shoulder tilt ({theta_sh_deg:.1f}°) exceeds {limit}° limit"
            return False, msg, sample

        # 2. Anatomical 2D projection ratio
        if not (self.limits.min_neck_ratio <= neck_ratio <= self.limits.max_neck_ratio):
            msg = (
                f"Neck/shoulder ratio ({neck_ratio:.2f}) outside plausible range "
                f"[{self.limits.min_neck_ratio}, {self.limits.max_neck_ratio}]"
            )
            return False, msg, sample

        return True, None, sample

    def add_sample(
        self,
        pose: PoseLandmarkData,
        face: FaceLandmarkData,
        timestamp: float = 0.0,
        strict: bool = False,
    ) -> bool:
        """Add a frame sample during calibration.

        Args:
            pose: PoseLandmarkData
            face: FaceLandmarkData
            timestamp: Time of sample
            strict: If True, raises PlausibilityGateError on invalid sample;
                    if False, resets collected samples on invalid sample.

        Returns:
            True if sample was accepted, False if rejected (when strict=False).
        """
        is_valid, reason, sample = self.validate_sample_plausibility(pose, face)
        if not is_valid:
            if strict:
                raise PlausibilityGateError(reason or "Sample failed plausibility gate")
            self.reset()
            return False

        valid_sample = RawCalibrationSample(
            pose=sample.pose,
            face=sample.face,
            w_sh_px=sample.w_sh_px,
            theta_sh_deg=sample.theta_sh_deg,
            l_neck_ort_px=sample.l_neck_ort_px,
            w_eye_corr_px=sample.w_eye_corr_px,
            y_sh_mid=sample.y_sh_mid,
            y_eye_mid=sample.y_eye_mid,
            neck_ratio=sample.neck_ratio,
            eye_sh_ratio=sample.eye_sh_ratio,
            timestamp=timestamp,
        )
        self.samples.append(valid_sample)
        return True

    def finalize(
        self,
        tau_enter_defaults: dict[str, float] | None = None,
    ) -> tuple[CalibrationReference, CalibrationProfileRecord]:
        """Validate aggregate plausibility, compute baseline, and evaluate initial noise.

        Returns:
            (CalibrationReference, CalibrationProfileRecord)

        Raises:
            CalibrationError: If sample count < 15 or initial noise MAD is too high.
            PlausibilityGateError: If aggregate median violates plausibility limits.
        """
        if len(self.samples) < self.target_samples:
            raise CalibrationError(
                f"Sampel kalibrasi belum mencukupi (memerlukan {self.target_samples} sampel, "
                f"saat ini {len(self.samples)})"
            )

        defaults = tau_enter_defaults or self.tau_enter_defaults

        # 1. Compute aggregate median baseline values
        base_neck_ort_ratio = statistics.median([s.neck_ratio for s in self.samples])
        base_eye_sh_ratio = statistics.median([s.eye_sh_ratio for s in self.samples])
        base_sh_width_px = statistics.median([s.w_sh_px for s in self.samples])
        base_sh_y_mid = statistics.median([s.y_sh_mid for s in self.samples])
        base_sh_angle_deg = statistics.median([s.theta_sh_deg for s in self.samples])
        base_head_pitch_deg = statistics.median([s.face.pitch_deg for s in self.samples])
        base_head_roll_deg = statistics.median([s.face.roll_deg for s in self.samples])
        base_head_yaw_deg = statistics.median([s.face.yaw_deg for s in self.samples])

        # 2. Check aggregate plausibility gate
        if abs(base_head_yaw_deg) > self.limits.max_abs_yaw_deg:
            limit = self.limits.max_abs_yaw_deg
            msg = f"Median head yaw ({base_head_yaw_deg:.1f}°) exceeds {limit}°"
            raise PlausibilityGateError(msg)
        if abs(base_head_roll_deg) > self.limits.max_abs_roll_deg:
            limit = self.limits.max_abs_roll_deg
            raise PlausibilityGateError(
                f"Median head roll ({base_head_roll_deg:.1f}°) exceeds {limit}°"
            )
        if abs(base_sh_angle_deg) > self.limits.max_abs_theta_sh_deg:
            limit = self.limits.max_abs_theta_sh_deg
            raise PlausibilityGateError(
                f"Median shoulder tilt ({base_sh_angle_deg:.1f}°) exceeds {limit}°"
            )
        if not (self.limits.min_neck_ratio <= base_neck_ort_ratio <= self.limits.max_neck_ratio):
            min_r = self.limits.min_neck_ratio
            max_r = self.limits.max_neck_ratio
            raise PlausibilityGateError(
                f"Median neck ratio ({base_neck_ort_ratio:.2f}) outside [{min_r}, {max_r}]"
            )

        ref = CalibrationReference(
            base_neck_ort_ratio=base_neck_ort_ratio,
            base_eye_sh_ratio=base_eye_sh_ratio,
            base_sh_width_px=base_sh_width_px,
            base_sh_y_mid=base_sh_y_mid,
            base_sh_angle_deg=base_sh_angle_deg,
            base_head_pitch_deg=base_head_pitch_deg,
            base_head_roll_deg=base_head_roll_deg,
        )

        # 3. Compute deviations d_i on all 15 calibration samples against new baseline
        d_fhp_list: list[float] = []
        d_neck_list: list[float] = []
        d_slump_list: list[float] = []
        d_pitch_list: list[float] = []
        d_tilt_list: list[float] = []

        for sample in self.samples:
            metrics = calculate_posture_metrics(sample.pose, sample.face, ref)
            d_fhp_list.append(metrics.d_fhp)
            d_neck_list.append(metrics.d_neck)
            d_slump_list.append(metrics.d_slump)
            d_pitch_list.append(metrics.d_pitch)
            d_tilt_list.append(metrics.d_tilt)

        # 4. Compute MAD standard deviation estimates: sigma_i_0 = 1.4826 * MAD(d_i)
        sigmas: dict[str, float] = {
            "fhp": compute_mad_sigma(d_fhp_list),
            "neck": compute_mad_sigma(d_neck_list),
            "slump": compute_mad_sigma(d_slump_list),
            "pitch": compute_mad_sigma(d_pitch_list),
            "tilt": compute_mad_sigma(d_tilt_list),
        }

        # 5. Check Light Quality & Stability Gate:
        # If 4 * sigma_i_0 > 0.80 * tau_default_enter, reject!
        for metric_name, sigma_val in sigmas.items():
            tau_enter = defaults.get(metric_name, DEFAULT_TAU_ENTER[metric_name])
            if 4.0 * sigma_val > 0.80 * tau_enter:
                raise CalibrationError(RECALIBRATION_REASON_NOISY)

        profile = CalibrationProfileRecord(
            profile_id=None,
            created_at=None,
            base_neck_ort_ratio=base_neck_ort_ratio,
            base_eye_sh_ratio=base_eye_sh_ratio,
            base_sh_width_px=base_sh_width_px,
            base_sh_y_mid=base_sh_y_mid,
            base_sh_angle_deg=base_sh_angle_deg,
            base_head_pitch_deg=base_head_pitch_deg,
            base_head_roll_deg=base_head_roll_deg,
            sigma_fhp=sigmas["fhp"],
            sigma_neck=sigmas["neck"],
            sigma_slump=sigmas["slump"],
            sigma_pitch=sigmas["pitch"],
            sigma_tilt=sigmas["tilt"],
        )

        return ref, profile


class NoiseFloorAdapter:
    """Running Quiet-Period Noise Floor Adapter.

    Updates noise floor sigma_i(t) and threshold tau_enter_i(t) in one direction
    (never below default). Monitors low-light condition when 4 * sigma_i(t) reaches tau_cap_i.
    """

    def __init__(
        self,
        initial_sigmas: dict[str, float] | None = None,
        tau_enter_defaults: dict[str, float] | None = None,
        tau_caps: dict[str, float] | None = None,
        window_size: int = 60,
        min_samples_to_adapt: int = 10,
    ) -> None:
        self.tau_enter_defaults = dict(tau_enter_defaults or DEFAULT_TAU_ENTER)
        self.tau_caps = dict(tau_caps or DEFAULT_TAU_CAP)
        self.window_size = window_size
        self.min_samples_to_adapt = min_samples_to_adapt

        self.buffers: dict[str, deque[float]] = {
            k: deque(maxlen=window_size) for k in self.tau_enter_defaults
        }

        self.current_sigmas: dict[str, float] = dict(initial_sigmas or {})
        for k in self.tau_enter_defaults:
            if k not in self.current_sigmas:
                self.current_sigmas[k] = 0.0

        self.effective_thresholds: dict[str, float] = dict(self.tau_enter_defaults)
        self.low_light_warning: bool = False

    def update(
        self,
        deviations: dict[str, float],
        is_safe_zone: bool,
        is_moving: bool,
    ) -> dict[str, float]:
        """Update noise buffers if in quiet period and compute adapted thresholds.

        Args:
            deviations: Current deviation d_i per metric
            is_safe_zone: True if all V_i == 0
            is_moving: True if quasi-static motion detector detects movement

        Returns:
            Dict of effective tau_enter_i thresholds
        """
        # Collect samples ONLY during quiet period (safe zone AND not moving)
        if is_safe_zone and not is_moving:
            for metric, val in deviations.items():
                if metric in self.buffers:
                    self.buffers[metric].append(val)

        warning_triggered = False

        # Progressively update sigma and adapted threshold per metric
        for metric, buf in self.buffers.items():
            if len(buf) >= self.min_samples_to_adapt:
                sigma_t = compute_mad_sigma(list(buf))
                self.current_sigmas[metric] = sigma_t
            else:
                sigma_t = self.current_sigmas[metric]

            default_enter = self.tau_enter_defaults[metric]
            cap = self.tau_caps[metric]

            # tau_enter_i(t) = min(tau_cap_i, max(tau_default_enter_i, 4 * sigma_i(t)))
            adapted_tau = max(default_enter, 4.0 * sigma_t)
            effective_tau = min(cap, adapted_tau)
            self.effective_thresholds[metric] = effective_tau

            # If 4 * sigma_i(t) touches or exceeds tau_cap_i, trigger LOW_LIGHT_WARNING
            if 4.0 * sigma_t >= cap:
                warning_triggered = True

        self.low_light_warning = warning_triggered
        return dict(self.effective_thresholds)


class SetupDriftDetector:
    """Setup Drift Detector for chair distance changes and laptop screen hinge tilts.

    Triggers transition to NEEDS_RECALIBRATION after >= 10.0s sustained drift.
    DILARANG melakukan silent auto-recalibration.
    """

    def __init__(
        self,
        ref: CalibrationReference,
        base_eye_y_mid: float | None = None,
        drift_duration_threshold_s: float = 10.0,
        tau_enter_fhp: float = 0.08,
    ) -> None:
        self.ref = ref
        # If eye y mid is not explicitly supplied, estimate near eye level relative to shoulder
        self.base_eye_y_mid = (
            base_eye_y_mid
            if base_eye_y_mid is not None
            else (ref.base_sh_y_mid - ref.base_neck_ort_ratio * ref.base_sh_width_px)
        )
        self.drift_duration_threshold_s = drift_duration_threshold_s
        self.tau_enter_fhp = tau_enter_fhp

        self.drift_elapsed_s: float = 0.0
        self.is_drift_detected: bool = False
        self.last_drift_reason: str | None = None

    def reset(self) -> None:
        """Reset drift timer and state."""
        self.drift_elapsed_s = 0.0
        self.is_drift_detected = False
        self.last_drift_reason = None

    def update(
        self,
        w_sh: float,
        y_sh_mid: float,
        y_eye_mid: float,
        pitch_head: float,
        d_fhp: float,
        dt: float,
    ) -> tuple[bool, str | None]:
        """Evaluate setup drift conditions.

        Args:
            w_sh: Current shoulder width in pixels
            y_sh_mid: Current shoulder midpoint Y coordinate
            y_eye_mid: Current outer eye midpoint Y coordinate
            pitch_head: Current head pitch in degrees
            d_fhp: Current head forward scale deviation
            dt: Time elapsed since last frame in seconds

        Returns:
            (is_drift_sustained, drift_reason)
        """
        w_sh_base = max(1.0, self.ref.base_sh_width_px)
        s_w = w_sh / w_sh_base

        # Condition A: Chair Distance Shift
        # Shoulder ratio not in [0.78, 1.30] while d_fhp is normal (user did not lean forward)
        chair_drift = (s_w < 0.78 or s_w > 1.30) and (d_fhp <= self.tau_enter_fhp)

        # Condition B: Laptop Screen Hinge Drift
        # Synchronous vertical shift of both shoulder and eyes with apparent head pitch change
        delta_y_sh = y_sh_mid - self.ref.base_sh_y_mid
        delta_y_eye = y_eye_mid - self.base_eye_y_mid
        delta_pitch = pitch_head - self.ref.base_head_pitch_deg

        # Vertical shifts must have the same sign (both moved down or both moved up in frame)
        same_direction = (delta_y_sh * delta_y_eye) > 0.0
        y_threshold = max(12.0, 0.06 * w_sh_base)
        pitch_threshold = 4.0

        hinge_drift = (
            same_direction
            and abs(delta_y_sh) >= y_threshold
            and abs(delta_y_eye) >= y_threshold
            and abs(delta_pitch) >= pitch_threshold
        )

        dt_safe = max(1e-4, dt)
        if chair_drift or hinge_drift:
            self.drift_elapsed_s += dt_safe
            reason = (
                "Pergeseran jarak kursi permanen"
                if chair_drift
                else "Perubahan sudut engsel layar laptop"
            )
            self.last_drift_reason = reason
        else:
            self.drift_elapsed_s = 0.0
            self.last_drift_reason = None

        if self.drift_elapsed_s >= (self.drift_duration_threshold_s - 1e-4):
            self.is_drift_detected = True
            return True, self.last_drift_reason

        return False, None
