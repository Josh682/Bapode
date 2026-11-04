"""One-way geometric posture metrics calculator.

Reference: docs/specs/01-vision-and-metrics.md
Implements:
1. Shoulder geometry (theta_sh, W_sh, midpoint).
2. Orthogonal neck projection (L_neck_ort) with roll compensation.
3. Yaw-compensated biocular eye width (W_eye_corr).
4. Yaw and Torso rotation gating.
5. Five one-way deviation metrics:
   - HEAD_FORWARD_SCALE (d_fhp)
   - TORSO_NECK_COMPRESSION (d_neck)
   - SHOULDER_VERTICAL_DROP (d_slump) with optical perspective compensation
   - GAZE_DOWN_PITCH (d_pitch)
   - CORONAL_ASYMMETRY (d_tilt)
"""

import math
from dataclasses import dataclass

from vision.landmarks import FaceLandmarkData, PoseLandmarkData


@dataclass(frozen=True, slots=True)
class CalibrationReference:
    """Baseline calibration reference values recorded during 3s calibration."""

    base_neck_ort_ratio: float
    base_eye_sh_ratio: float
    base_sh_width_px: float
    base_sh_y_mid: float
    base_sh_angle_deg: float
    base_head_pitch_deg: float
    base_head_roll_deg: float
    y_c: float = 360.0  # Default vertical optical center (e.g. 720p / 2)


@dataclass(frozen=True, slots=True)
class PostureMetricsResult:
    """Calculated raw geometric values and one-way deviations."""

    d_fhp: float
    d_neck: float
    d_slump: float
    d_pitch: float
    d_tilt: float
    theta_sh_deg: float
    w_sh_px: float
    l_neck_ort_px: float
    w_eye_corr_px: float
    yaw_gate_active: bool
    torso_gate_active: bool


def calculate_shoulder_geometry(pose: PoseLandmarkData) -> tuple[float, float, float, float]:
    """Compute shoulder delta X, delta Y, angle in degrees, and width in pixels.

    Returns:
        (delta_x_sh, delta_y_sh, theta_sh_deg, w_sh_px)
    """
    # Note: On front-facing camera, anatomical left shoulder is on image right (X_LS > X_RS)
    delta_x = pose.left_shoulder.x - pose.right_shoulder.x
    delta_y = pose.left_shoulder.y - pose.right_shoulder.y
    theta_rad = math.atan2(delta_y, delta_x)
    theta_deg = math.degrees(theta_rad)
    w_sh = math.hypot(delta_x, delta_y)
    return delta_x, delta_y, theta_deg, w_sh


def calculate_orthogonal_neck_length(pose: PoseLandmarkData, theta_deg: float) -> float:
    """Calculate orthogonal neck projection length compensated for body roll.

    Formula:
        L_neck_ort = -(X_ear_mid - X_sh_mid)*sin(theta) - (Y_ear_mid - Y_sh_mid)*cos(theta)
    """
    sh_mid = pose.shoulder_midpoint
    ear_mid = pose.ear_midpoint

    dx = ear_mid.x - sh_mid.x
    dy = ear_mid.y - sh_mid.y

    theta_rad = math.radians(theta_deg)
    return -dx * math.sin(theta_rad) - dy * math.cos(theta_rad)


def calculate_yaw_compensated_eye_width(w_eye: float, yaw_deg: float) -> float:
    """Calculate biocular eye width compensated for head yaw rotation.

    Formula:
        W_eye_corr = W_eye / cos(yaw_head)
    """
    yaw_rad = math.radians(yaw_deg)
    cos_val = math.cos(yaw_rad)
    # Clamp cos_val to avoid division by zero or negative flip at extreme angles
    cos_clamped = max(0.1, cos_val)
    return w_eye / cos_clamped


def calculate_posture_metrics(
    pose: PoseLandmarkData,
    face: FaceLandmarkData,
    ref: CalibrationReference,
    prev_w_sh: float | None = None,
    dt: float = 0.2,
    yaw_threshold_deg: float = 15.0,
    torso_rate_threshold: float = 0.25,  # shoulder-widths per second
) -> PostureMetricsResult:
    """Compute the 5 one-way geometric posture metrics with gates and compensations."""
    # 1. Shoulder Geometry
    _, _, theta_sh_deg, w_sh = calculate_shoulder_geometry(pose)
    w_sh_safe = max(1.0, w_sh)

    # 2. Orthogonal Neck Length
    l_neck_ort = calculate_orthogonal_neck_length(pose, theta_sh_deg)

    # 3. Biocular Eye Width with Yaw Compensation
    w_eye_corr = calculate_yaw_compensated_eye_width(face.w_eye, face.yaw_deg)

    # 4. Gating Checks
    yaw_gate_active = abs(face.yaw_deg) > yaw_threshold_deg

    torso_gate_active = False
    if prev_w_sh is not None and dt > 1e-4 and ref.base_sh_width_px > 1.0:
        dw_dt = abs(w_sh - prev_w_sh) / dt
        # Normalized rate of change of shoulder width
        dw_norm = dw_dt / ref.base_sh_width_px
        if dw_norm > torso_rate_threshold:
            torso_gate_active = True

    # 5. One-way Metrics:
    # 5a. HEAD_FORWARD_SCALE (d_fhp)
    cur_eye_sh_ratio = w_eye_corr / w_sh_safe
    base_eye_sh_ratio = max(1e-4, ref.base_eye_sh_ratio)
    d_fhp_raw = (cur_eye_sh_ratio / base_eye_sh_ratio) - 1.0
    # If yaw or torso gate is active, hold d_fhp at 0.0 (or suspended)
    d_fhp = 0.0 if (yaw_gate_active or torso_gate_active) else d_fhp_raw

    # 5b. TORSO_NECK_COMPRESSION (d_neck)
    cur_neck_ort_ratio = l_neck_ort / w_sh_safe
    base_neck_ort_ratio = max(1e-4, ref.base_neck_ort_ratio)
    d_neck = 1.0 - (cur_neck_ort_ratio / base_neck_ort_ratio)

    # 5c. SHOULDER_VERTICAL_DROP (d_slump) with Perspective Optical Compensation
    s_w = w_sh / max(1.0, ref.base_sh_width_px)
    y_exp = ref.y_c + s_w * (ref.base_sh_y_mid - ref.y_c)
    y_sh_mid = pose.shoulder_midpoint.y
    d_slump = (y_sh_mid - y_exp) / w_sh_safe

    # 5d. GAZE_DOWN_PITCH (d_pitch)
    d_pitch = max(0.0, ref.base_head_pitch_deg - face.pitch_deg)

    # 5e. CORONAL_ASYMMETRY (d_tilt)
    delta_sh_angle = abs(theta_sh_deg - ref.base_sh_angle_deg)
    delta_head_roll = abs(face.roll_deg - ref.base_head_roll_deg)
    d_tilt = max(delta_sh_angle, delta_head_roll)

    return PostureMetricsResult(
        d_fhp=d_fhp,
        d_neck=d_neck,
        d_slump=d_slump,
        d_pitch=d_pitch,
        d_tilt=d_tilt,
        theta_sh_deg=theta_sh_deg,
        w_sh_px=w_sh,
        l_neck_ort_px=l_neck_ort,
        w_eye_corr_px=w_eye_corr,
        yaw_gate_active=yaw_gate_active,
        torso_gate_active=torso_gate_active,
    )
