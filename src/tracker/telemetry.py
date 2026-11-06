"""Session Orchestrator and Non-Diluted Telemetry Accumulator for Bapode v3.0.

Reference: docs/specs/04-telemetry-and-schema.md & docs/ARCHITECTURE.md
Implements:
1. TelemetryBucketAggregator (5-minute non-diluted buckets):
   - Computes duty_cycle_any_violation
   - Computes p95_* from all measurable samples in bucket
   - Computes mean_violating_* ONLY from samples where V_i == 1 (free of upright dilution)
2. SessionOrchestrator:
   - Connects 3-axis state machine, 5 metrics, and SQLite WAL repository
   - Run-Length Encoding (RLE) for posture_state_intervals (flush on state change or every 15s)
   - Tracks alert_dismissal_sec when ACTIVE_OVERLAY extinguishes because V_i == 0
   - Computes observability_coverage, reference_adherence_ratio, and adherence_conservative_bound
"""

import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from db.manager import DatabaseManager, TelemetryBucketRecord
from tracker.calibration import NoiseFloorAdapter, SetupDriftDetector
from tracker.issue_tracker import IssueTrackerState, PostureEscalationController
from tracker.states import (
    IntervalSignature,
    OperatingMode,
    PostureViolationFlags,
    UIIntrusivenessLevel,
    VisibilityState,
)
from vision.landmarks import FaceLandmarkData, PoseLandmarkData
from vision.metrics import (
    CalibrationReference,
    calculate_posture_metrics,
    calculate_shoulder_geometry,
    calculate_yaw_compensated_eye_width,
)


def compute_percentile(values: Sequence[float], percentile: float) -> float:
    """Compute linear percentile for a sequence of values, matching numpy.percentile.

    Args:
        values: Sequence of numeric values
        percentile: Float between 0.0 and 100.0

    Returns:
        Interpolated percentile value, or 0.0 if values is empty.
    """
    if not values:
        return 0.0
    sorted_vals = sorted(values)
    n = len(sorted_vals)
    if n == 1:
        return sorted_vals[0]

    k = (n - 1) * (percentile / 100.0)
    floor_k = math.floor(k)
    ceil_k = math.ceil(k)
    if floor_k == ceil_k:
        return sorted_vals[int(k)]

    d0 = sorted_vals[int(floor_k)] * (ceil_k - k)
    d1 = sorted_vals[int(ceil_k)] * (k - floor_k)
    return d0 + d1


@dataclass(slots=True)
class TelemetryBucketAggregator:
    """Aggregates 5-minute telemetry samples free of upright dilution."""

    bucket_interval_s: float = 300.0
    measurable_sec: float = 0.0
    occluded_sec: float = 0.0
    violating_sec: float = 0.0

    # Collections per metric for all measurable samples vs violating samples
    samples_fhp: list[float] | None = None
    samples_neck: list[float] | None = None
    samples_slump: list[float] | None = None
    samples_pitch: list[float] | None = None
    samples_tilt: list[float] | None = None

    violating_fhp: list[float] | None = None
    violating_neck: list[float] | None = None
    violating_slump: list[float] | None = None
    violating_pitch: list[float] | None = None
    violating_tilt: list[float] | None = None

    def __post_init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        """Clear bucket accumulator state."""
        self.measurable_sec = 0.0
        self.occluded_sec = 0.0
        self.violating_sec = 0.0
        self.samples_fhp = []
        self.samples_neck = []
        self.samples_slump = []
        self.samples_pitch = []
        self.samples_tilt = []

        self.violating_fhp = []
        self.violating_neck = []
        self.violating_slump = []
        self.violating_pitch = []
        self.violating_tilt = []

    @property
    def total_observed_seconds(self) -> float:
        """Total time accumulated in current bucket."""
        return self.measurable_sec + self.occluded_sec

    @property
    def is_bucket_ready(self) -> bool:
        """Return True if bucket interval threshold is reached."""
        return self.total_observed_seconds >= self.bucket_interval_s

    def add_sample(
        self,
        dt: float,
        visibility: VisibilityState,
        deviations: dict[str, float] | None = None,
        flags: PostureViolationFlags | None = None,
    ) -> None:
        """Add sample to current bucket.

        Args:
            dt: Delta time of this sample
            visibility: Current visibility state
            deviations: Posture deviation dict (if measurable)
            flags: Binary violation flags (if measurable)
        """
        dt_safe = max(1e-4, dt)
        if visibility == VisibilityState.MEASURABLE:
            self.measurable_sec += dt_safe
            if deviations is not None and flags is not None:
                if flags.has_any_violation:
                    self.violating_sec += dt_safe

                # Append all measurable deviations
                assert self.samples_fhp is not None
                assert self.samples_neck is not None
                assert self.samples_slump is not None
                assert self.samples_pitch is not None
                assert self.samples_tilt is not None
                self.samples_fhp.append(deviations.get("fhp", 0.0))
                self.samples_neck.append(deviations.get("neck", 0.0))
                self.samples_slump.append(deviations.get("slump", 0.0))
                self.samples_pitch.append(deviations.get("pitch", 0.0))
                self.samples_tilt.append(deviations.get("tilt", 0.0))

                # Append ONLY violating samples to violating lists (no upright dilution!)
                assert self.violating_fhp is not None
                assert self.violating_neck is not None
                assert self.violating_slump is not None
                assert self.violating_pitch is not None
                assert self.violating_tilt is not None
                if flags.v_fhp:
                    self.violating_fhp.append(deviations.get("fhp", 0.0))
                if flags.v_neck_comp:
                    self.violating_neck.append(deviations.get("neck", 0.0))
                if flags.v_slump:
                    self.violating_slump.append(deviations.get("slump", 0.0))
                if flags.v_gaze_pitch:
                    self.violating_pitch.append(deviations.get("pitch", 0.0))
                if flags.v_tilt:
                    self.violating_tilt.append(deviations.get("tilt", 0.0))
        elif visibility in (VisibilityState.OCCLUDED_LOWER, VisibilityState.OCCLUDED_UPPER):
            self.occluded_sec += dt_safe

    def flush_bucket(
        self,
        session_id: int,
        profile_id: int,
        timestamp: datetime | None = None,
    ) -> TelemetryBucketRecord:
        """Compute aggregated statistics and return TelemetryBucketRecord."""
        ts_str = (timestamp or datetime.now()).strftime("%Y-%m-%d %H:%M:%S")

        duty_cycle = (
            (self.violating_sec / self.measurable_sec) if self.measurable_sec > 0.0 else 0.0
        )
        duty_cycle_clamped = min(1.0, max(0.0, duty_cycle))

        # Helper to compute mean only when violating samples exist
        def calc_mean_violating(items: list[float] | None) -> float | None:
            if not items:
                return None
            return sum(items) / len(items)

        assert self.samples_fhp is not None
        assert self.samples_neck is not None
        assert self.samples_slump is not None
        assert self.samples_pitch is not None
        assert self.samples_tilt is not None

        record = TelemetryBucketRecord(
            bucket_id=None,
            session_id=session_id,
            profile_id=profile_id,
            bucket_timestamp=ts_str,
            measurable_sec_in_bucket=self.measurable_sec,
            occluded_sec_in_bucket=self.occluded_sec,
            duty_cycle_any_violation=duty_cycle_clamped,
            mean_violating_fhp=calc_mean_violating(self.violating_fhp),
            p95_fhp_dev=compute_percentile(self.samples_fhp, 95.0),
            mean_violating_neck=calc_mean_violating(self.violating_neck),
            p95_neck_dev=compute_percentile(self.samples_neck, 95.0),
            mean_violating_slump=calc_mean_violating(self.violating_slump),
            p95_slump_dev=compute_percentile(self.samples_slump, 95.0),
            mean_violating_pitch_deg=calc_mean_violating(self.violating_pitch),
            p95_pitch_deg=compute_percentile(self.samples_pitch, 95.0),
            mean_violating_tilt_deg=calc_mean_violating(self.violating_tilt),
            p95_tilt_deg=compute_percentile(self.samples_tilt, 95.0),
        )

        self.reset()
        return record


@dataclass(frozen=True, slots=True)
class SessionTickResult:
    """Snapshot result of a single orchestrator process tick."""

    operating_mode: OperatingMode
    visibility_state: VisibilityState
    ui_level: UIIntrusivenessLevel
    violation_flags: PostureViolationFlags
    tracker_states: dict[str, IssueTrackerState] | None
    low_light_warning: bool
    drift_detected: bool
    drift_reason: str | None


class SessionOrchestrator:
    """Coordinates state machines, trackers, noise adaptation, and SQLite WAL storage."""

    def __init__(
        self,
        db: DatabaseManager,
        session_id: int,
        profile_id: int,
        ref: CalibrationReference,
        escalation_controller: PostureEscalationController | None = None,
        noise_adapter: NoiseFloorAdapter | None = None,
        drift_detector: SetupDriftDetector | None = None,
        bucket_aggregator: TelemetryBucketAggregator | None = None,
        flush_interval_s: float = 15.0,
    ) -> None:
        self.db = db
        self.session_id = session_id
        self.profile_id = profile_id
        self.ref = ref

        self.escalation_controller = escalation_controller or PostureEscalationController()
        self.noise_adapter = noise_adapter or NoiseFloorAdapter()
        self.drift_detector = drift_detector or SetupDriftDetector(ref=ref)
        self.bucket_aggregator = bucket_aggregator or TelemetryBucketAggregator()
        self.flush_interval_s = flush_interval_s

        # 3-Axis state representation
        self.operating_mode: OperatingMode = OperatingMode.ACTIVE_MONITORING
        self.visibility_state: VisibilityState = VisibilityState.MEASURABLE
        self.ui_level: UIIntrusivenessLevel = UIIntrusivenessLevel.SILENT_OK
        self.violation_flags: PostureViolationFlags = PostureViolationFlags()

        # Cumulative session duration accumulators
        self.measurable_seconds: float = 0.0
        self.adherent_seconds: float = 0.0
        self.occluded_lower_seconds: float = 0.0
        self.occluded_upper_seconds: float = 0.0
        self.absent_seconds: float = 0.0
        self.paused_or_drift_seconds: float = 0.0

        self.fhp_violation_seconds: float = 0.0
        self.neck_comp_violation_seconds: float = 0.0
        self.slump_violation_seconds: float = 0.0
        self.gaze_pitch_violation_seconds: float = 0.0
        self.tilt_violation_seconds: float = 0.0

        # Alert dismissal tracking
        self.total_alerts_shown: int = 0
        self.total_microbreaks_prompted: int = 0
        self.overlay_active_start: float | None = None
        self.alert_dismissals: list[float] = []

        self.last_flush_time: float = 0.0
        self.session_elapsed_time: float = 0.0

    @property
    def mean_alert_dismissal_seconds(self) -> float | None:
        """Mean dismissal latency across all dismissed alerts."""
        if not self.alert_dismissals:
            return None
        return sum(self.alert_dismissals) / len(self.alert_dismissals)

    def process_frame(
        self,
        pose: PoseLandmarkData | None,
        face: FaceLandmarkData | None,
        visibility: VisibilityState,
        is_moving: bool,
        dt: float,
        current_time_s: float | None = None,
        now: datetime | None = None,
    ) -> SessionTickResult:
        """Process a single frame tick through all pipelines and update telemetry.

        Args:
            pose: PoseLandmarkData or None if occluded/absent
            face: FaceLandmarkData or None if occluded/absent
            visibility: Evaluated VisibilityState
            is_moving: Quasi-static motion detector boolean
            dt: Delta time in seconds
            current_time_s: Monotonic clock timestamp (seconds)
            now: Calendar datetime for database timestamps
        """
        dt_safe = max(1e-4, dt)
        t_sec = (
            current_time_s if current_time_s is not None else (self.session_elapsed_time + dt_safe)
        )
        self.session_elapsed_time = t_sec
        now_dt = now or datetime.now()

        tracker_states: dict[str, IssueTrackerState] | None = None
        drift_detected = False
        drift_reason: str | None = None
        last_dismissal_sec: float | None = None

        # 1. Check Drift if currently measurable
        if (
            self.operating_mode == OperatingMode.ACTIVE_MONITORING
            and visibility == VisibilityState.MEASURABLE
            and pose is not None
            and face is not None
        ):
            _, _, _, w_sh = calculate_shoulder_geometry(pose)
            y_sh = pose.shoulder_midpoint.y
            y_eye = (face.eye_outer_left.y + face.eye_outer_right.y) / 2.0
            # Pre-evaluate d_fhp for chair drift gating
            w_eye_corr = calculate_yaw_compensated_eye_width(face.w_eye, face.yaw_deg)
            cur_ratio = w_eye_corr / max(1.0, w_sh)
            d_fhp_raw = (cur_ratio / max(1e-4, self.ref.base_eye_sh_ratio)) - 1.0

            drift_detected, drift_reason = self.drift_detector.update(
                w_sh=w_sh,
                y_sh_mid=y_sh,
                y_eye_mid=y_eye,
                pitch_head=face.pitch_deg,
                d_fhp=d_fhp_raw,
                dt=dt_safe,
            )
            if drift_detected:
                self.operating_mode = OperatingMode.NEEDS_RECALIBRATION

        # 2. Operating Mode Routing
        if self.operating_mode == OperatingMode.NEEDS_RECALIBRATION:
            self.paused_or_drift_seconds += dt_safe
            self.visibility_state = VisibilityState.NEEDS_RECALIBRATION
            self.ui_level = UIIntrusivenessLevel.SILENT_OK
            self.violation_flags = PostureViolationFlags()

        elif self.operating_mode.is_paused:
            self.paused_or_drift_seconds += dt_safe
            self.visibility_state = VisibilityState.PAUSED
            self.ui_level = UIIntrusivenessLevel.SILENT_OK
            self.violation_flags = PostureViolationFlags()

        else:
            # Active monitoring mode
            self.visibility_state = visibility

            if visibility == VisibilityState.ABSENT:
                self.absent_seconds += dt_safe
                self.ui_level = UIIntrusivenessLevel.SILENT_OK
                self.violation_flags = PostureViolationFlags()
                self.bucket_aggregator.add_sample(dt_safe, visibility)

            elif visibility == VisibilityState.OCCLUDED_LOWER:
                self.occluded_lower_seconds += dt_safe
                self.ui_level = UIIntrusivenessLevel.SILENT_OK
                self.violation_flags = PostureViolationFlags()
                self.bucket_aggregator.add_sample(dt_safe, visibility)

            elif visibility == VisibilityState.OCCLUDED_UPPER:
                self.occluded_upper_seconds += dt_safe
                self.ui_level = UIIntrusivenessLevel.SILENT_OK
                self.violation_flags = PostureViolationFlags()
                self.bucket_aggregator.add_sample(dt_safe, visibility)

            elif visibility == VisibilityState.MEASURABLE:
                assert pose is not None
                assert face is not None

                self.measurable_seconds += dt_safe

                # Calculate 5 pure geometric posture metrics
                metrics = calculate_posture_metrics(pose, face, self.ref, dt=dt_safe)
                devs = {
                    "fhp": metrics.d_fhp,
                    "neck": metrics.d_neck,
                    "slump": metrics.d_slump,
                    "pitch": metrics.d_pitch,
                    "tilt": metrics.d_tilt,
                }

                # Update Running Quiet-Period Noise Floor
                # Quiet period: safe zone (all V_i == 0 previously) AND not moving
                is_safe_zone = not self.violation_flags.has_any_violation
                adapted_taus = self.noise_adapter.update(
                    devs, is_safe_zone=is_safe_zone, is_moving=is_moving
                )

                # Set adapted tau_enter in IssueTrackers
                for metric_name, tau_val in adapted_taus.items():
                    self.escalation_controller.trackers[metric_name].tau_enter = tau_val

                # Update IssueTrackers and UI Escalation Controller
                prev_ui_level = self.ui_level
                new_ui_level, flags, tracker_states = self.escalation_controller.update(
                    d_fhp=metrics.d_fhp,
                    d_neck=metrics.d_neck,
                    d_slump=metrics.d_slump,
                    d_pitch=metrics.d_pitch,
                    d_tilt=metrics.d_tilt,
                    dt=dt_safe,
                    is_moving=is_moving,
                    current_time=t_sec,
                )
                self.ui_level = new_ui_level
                self.violation_flags = flags

                # Time duration accounting
                if not flags.has_any_violation:
                    self.adherent_seconds += dt_safe
                if flags.v_fhp:
                    self.fhp_violation_seconds += dt_safe
                if flags.v_neck_comp:
                    self.neck_comp_violation_seconds += dt_safe
                if flags.v_slump:
                    self.slump_violation_seconds += dt_safe
                if flags.v_gaze_pitch:
                    self.gaze_pitch_violation_seconds += dt_safe
                if flags.v_tilt:
                    self.tilt_violation_seconds += dt_safe

                # Alert dismissal tracking
                if self.ui_level == UIIntrusivenessLevel.ACTIVE_OVERLAY:
                    if self.overlay_active_start is None:
                        self.overlay_active_start = t_sec
                        self.total_alerts_shown += 1
                elif self.overlay_active_start is not None:
                    # Overlay extinguished! Record latency if recovered (all V_i == 0)
                    if not flags.has_any_violation:
                        dismissal_sec = max(0.0, float(t_sec - self.overlay_active_start))
                        self.alert_dismissals.append(dismissal_sec)
                        last_dismissal_sec = dismissal_sec
                    self.overlay_active_start = None

                # Microbreak escalation tracking
                if (
                    self.ui_level == UIIntrusivenessLevel.MICROBREAK_PROMPT
                    and prev_ui_level != UIIntrusivenessLevel.MICROBREAK_PROMPT
                ):
                    self.total_microbreaks_prompted += 1

                # Feed sample into 5-minute bucket aggregator
                self.bucket_aggregator.add_sample(dt_safe, visibility, devs, flags)
                if self.bucket_aggregator.is_bucket_ready:
                    bucket_record = self.bucket_aggregator.flush_bucket(
                        session_id=self.session_id,
                        profile_id=self.profile_id,
                        timestamp=now_dt,
                    )
                    self.db.record_telemetry_bucket(bucket_record)

        # 3. RLE Interval Management:
        # Flush/record state when signature changes or every 15 seconds
        sig = IntervalSignature(
            visibility_state=self.visibility_state,
            ui_level=self.ui_level,
            violations=self.violation_flags,
        )

        time_since_flush = t_sec - self.last_flush_time
        should_flush = time_since_flush >= self.flush_interval_s

        self.db.record_state(
            session_id=self.session_id,
            profile_id=self.profile_id,
            signature=sig,
            now=now_dt,
            alert_dismissal_sec=last_dismissal_sec,
        )

        if should_flush:
            self.last_flush_time = t_sec
            self.sync_session_metrics_to_db()

        return SessionTickResult(
            operating_mode=self.operating_mode,
            visibility_state=self.visibility_state,
            ui_level=self.ui_level,
            violation_flags=self.violation_flags,
            tracker_states=tracker_states,
            low_light_warning=self.noise_adapter.low_light_warning,
            drift_detected=drift_detected,
            drift_reason=drift_reason,
        )

    def sync_session_metrics_to_db(self) -> None:
        """Write current session metrics and calculated bounds to SQLite."""
        self.db.update_session_metrics(
            session_id=self.session_id,
            measurable_seconds=self.measurable_seconds,
            adherent_seconds=self.adherent_seconds,
            occluded_lower_seconds=self.occluded_lower_seconds,
            occluded_upper_seconds=self.occluded_upper_seconds,
            absent_seconds=self.absent_seconds,
            paused_or_drift_seconds=self.paused_or_drift_seconds,
            fhp_violation_seconds=self.fhp_violation_seconds,
            neck_comp_violation_seconds=self.neck_comp_violation_seconds,
            slump_violation_seconds=self.slump_violation_seconds,
            gaze_pitch_violation_seconds=self.gaze_pitch_violation_seconds,
            tilt_violation_seconds=self.tilt_violation_seconds,
            total_alerts_shown=self.total_alerts_shown,
            total_microbreaks_prompted=self.total_microbreaks_prompted,
            mean_alert_dismissal_seconds=self.mean_alert_dismissal_seconds,
        )

    def close(self, end_time: datetime | None = None) -> None:
        """Flush active intervals, flush remaining bucket data, and close session."""
        now_dt = end_time or datetime.now()

        # Flush any remaining telemetry bucket data
        if self.bucket_aggregator.total_observed_seconds > 0.0:
            bucket_rec = self.bucket_aggregator.flush_bucket(
                session_id=self.session_id,
                profile_id=self.profile_id,
                timestamp=now_dt,
            )
            self.db.record_telemetry_bucket(bucket_rec)

        # Flush interval and sync session
        self.sync_session_metrics_to_db()
        self.db.close_session(session_id=self.session_id, end_time=now_dt)
