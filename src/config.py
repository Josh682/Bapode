"""Configuration Loader for Posture Guardian v3.0.

Loads parameters from `config.toml` (or falls back to built-in defaults)
using standard library `tomllib`.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Default metric threshold mappings
DEFAULT_TAU_ENTER: dict[str, float] = {
    "fhp": 0.08,
    "neck": 0.08,
    "slump": 0.06,
    "pitch": 12.0,
    "tilt": 8.0,
}

DEFAULT_TAU_EXIT: dict[str, float] = {
    "fhp": 0.04,
    "neck": 0.04,
    "slump": 0.03,
    "pitch": 6.0,
    "tilt": 4.0,
}

DEFAULT_TAU_CAP: dict[str, float] = {
    "fhp": 0.18,
    "neck": 0.16,
    "slump": 0.14,
    "pitch": 22.0,
    "tilt": 16.0,
}


@dataclass(frozen=True, slots=True)
class VisionConfig:
    """Vision acquisition and model configuration."""

    camera_index: int = 0
    target_fps: float = 5.0
    frame_width: int = 1280
    frame_height: int = 720
    models_dir: str = "models"
    pose_model: str = "pose_landmarker_lite.task"
    face_model: str = "face_landmarker.task"


@dataclass(frozen=True, slots=True)
class MotionConfig:
    """Quasi-static motion gating configuration."""

    v_thresh: float = 0.15
    freeze_max_s: float = 3.0
    smoothing_alpha: float = 0.5


@dataclass(frozen=True, slots=True)
class MetricsThresholdsConfig:
    """Per-metric deviation thresholds."""

    tau_default_enter: dict[str, float] = field(default_factory=lambda: dict(DEFAULT_TAU_ENTER))
    tau_exit: dict[str, float] = field(default_factory=lambda: dict(DEFAULT_TAU_EXIT))
    tau_cap: dict[str, float] = field(default_factory=lambda: dict(DEFAULT_TAU_CAP))


@dataclass(frozen=True, slots=True)
class TrackerConfig:
    """IssueTracker and UI escalation timing parameters."""

    debounce_time_s: float = 15.0
    ambient_warn_threshold_s: float = 7.5
    step_down_floor_s: float = 9.0
    cooldown_s: float = 30.0
    microbreak_duration_s: float = 60.0
    tau_attack_s: float = 1.0
    tau_release_s: float = 0.3


@dataclass(frozen=True, slots=True)
class CalibrationConfig:
    """3-second guided calibration and drift parameters."""

    target_samples: int = 15
    min_samples: int = 15
    yaw_head_limit_deg: float = 8.0
    roll_head_limit_deg: float = 5.0
    shoulder_tilt_limit_deg: float = 6.0
    neck_ratio_min: float = 0.35
    neck_ratio_max: float = 0.75
    mad_rejection_factor: float = 0.80
    chair_drift_low: float = 0.78
    chair_drift_high: float = 1.30
    drift_sustain_time_s: float = 10.0


@dataclass(frozen=True, slots=True)
class AppConfig:
    """Top-level unified application configuration."""

    vision: VisionConfig = field(default_factory=VisionConfig)
    motion: MotionConfig = field(default_factory=MotionConfig)
    metrics: MetricsThresholdsConfig = field(default_factory=MetricsThresholdsConfig)
    tracker: TrackerConfig = field(default_factory=TrackerConfig)
    calibration: CalibrationConfig = field(default_factory=CalibrationConfig)


def load_config(config_path: str | Path = "config.toml") -> AppConfig:
    """Load AppConfig from TOML file, falling back to defaults if not found or invalid."""
    path = Path(config_path)
    if not path.is_file():
        # Try finding relative to project root
        repo_root = Path(__file__).resolve().parent.parent
        candidate = repo_root / config_path
        if candidate.is_file():
            path = candidate

    if not path.is_file():
        return AppConfig()

    try:
        with open(path, "rb") as f:
            data = tomllib.load(f)
    except Exception:
        return AppConfig()

    def get_sec(section: str) -> dict[str, Any]:
        val = data.get(section, {})
        return val if isinstance(val, dict) else {}

    v_data = get_sec("vision")
    vision_cfg = VisionConfig(
        camera_index=int(v_data.get("camera_index", 0)),
        target_fps=float(v_data.get("target_fps", 5.0)),
        frame_width=int(v_data.get("frame_width", 1280)),
        frame_height=int(v_data.get("frame_height", 720)),
        models_dir=str(v_data.get("models_dir", "models")),
        pose_model=str(v_data.get("pose_model", "pose_landmarker_lite.task")),
        face_model=str(v_data.get("face_model", "face_landmarker.task")),
    )

    m_data = get_sec("motion")
    motion_cfg = MotionConfig(
        v_thresh=float(m_data.get("v_thresh", 0.15)),
        freeze_max_s=float(m_data.get("freeze_max_s", 3.0)),
        smoothing_alpha=float(m_data.get("smoothing_alpha", 0.5)),
    )

    met_data = get_sec("metrics")
    tau_enter = dict(DEFAULT_TAU_ENTER)
    tau_exit = dict(DEFAULT_TAU_EXIT)
    tau_cap = dict(DEFAULT_TAU_CAP)

    if isinstance(met_data.get("tau_default_enter"), dict):
        for k, v in met_data["tau_default_enter"].items():
            tau_enter[k] = float(v)
    if isinstance(met_data.get("tau_exit"), dict):
        for k, v in met_data["tau_exit"].items():
            tau_exit[k] = float(v)
    if isinstance(met_data.get("tau_cap"), dict):
        for k, v in met_data["tau_cap"].items():
            tau_cap[k] = float(v)

    metrics_cfg = MetricsThresholdsConfig(
        tau_default_enter=tau_enter,
        tau_exit=tau_exit,
        tau_cap=tau_cap,
    )

    tr_data = get_sec("tracker")
    tracker_cfg = TrackerConfig(
        debounce_time_s=float(tr_data.get("debounce_time_s", 15.0)),
        ambient_warn_threshold_s=float(tr_data.get("ambient_warn_threshold_s", 7.5)),
        step_down_floor_s=float(tr_data.get("step_down_floor_s", 9.0)),
        cooldown_s=float(tr_data.get("cooldown_s", 30.0)),
        microbreak_duration_s=float(tr_data.get("microbreak_duration_s", 60.0)),
        tau_attack_s=float(tr_data.get("tau_attack_s", 1.0)),
        tau_release_s=float(tr_data.get("tau_release_s", 0.3)),
    )

    cal_data = get_sec("calibration")
    cal_cfg = CalibrationConfig(
        target_samples=int(cal_data.get("target_samples", 15)),
        min_samples=int(cal_data.get("min_samples", 15)),
        yaw_head_limit_deg=float(cal_data.get("yaw_head_limit_deg", 8.0)),
        roll_head_limit_deg=float(cal_data.get("roll_head_limit_deg", 5.0)),
        shoulder_tilt_limit_deg=float(cal_data.get("shoulder_tilt_limit_deg", 6.0)),
        neck_ratio_min=float(cal_data.get("neck_ratio_min", 0.35)),
        neck_ratio_max=float(cal_data.get("neck_ratio_max", 0.75)),
        mad_rejection_factor=float(cal_data.get("mad_rejection_factor", 0.80)),
        chair_drift_low=float(cal_data.get("chair_drift_low", 0.78)),
        chair_drift_high=float(cal_data.get("chair_drift_high", 1.30)),
        drift_sustain_time_s=float(cal_data.get("drift_sustain_time_s", 10.0)),
    )

    return AppConfig(
        vision=vision_cfg,
        motion=motion_cfg,
        metrics=metrics_cfg,
        tracker=tracker_cfg,
        calibration=cal_cfg,
    )
