"""Unit tests for Session Orchestrator, Non-Diluted Telemetry Aggregator, and SQLite Bounds."""

from datetime import datetime, timedelta

import pytest

from db.manager import CalibrationProfileRecord, DatabaseManager
from tracker.states import (
    OperatingMode,
    PostureViolationFlags,
    UIIntrusivenessLevel,
    VisibilityState,
)
from tracker.telemetry import (
    SessionOrchestrator,
    TelemetryBucketAggregator,
    compute_percentile,
)
from vision.landmarks import BoundingBox, FaceLandmarkData, Point2D, PoseLandmarkData
from vision.metrics import CalibrationReference


@pytest.fixture
def in_memory_db() -> DatabaseManager:
    """Fixture providing initialized SQLite in-memory database."""
    db = DatabaseManager(":memory:")
    db.initialize_schema()
    return db


@pytest.fixture
def baseline_ref() -> CalibrationReference:
    """Baseline calibration reference for test suite."""
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


@pytest.fixture
def profile_id(in_memory_db: DatabaseManager, baseline_ref: CalibrationReference) -> int:
    """Fixture saving a calibration profile to SQLite."""
    rec = CalibrationProfileRecord(
        profile_id=None,
        created_at=None,
        base_neck_ort_ratio=baseline_ref.base_neck_ort_ratio,
        base_eye_sh_ratio=baseline_ref.base_eye_sh_ratio,
        base_sh_width_px=baseline_ref.base_sh_width_px,
        base_sh_y_mid=baseline_ref.base_sh_y_mid,
        base_sh_angle_deg=baseline_ref.base_sh_angle_deg,
        base_head_pitch_deg=baseline_ref.base_head_pitch_deg,
        base_head_roll_deg=baseline_ref.base_head_roll_deg,
        sigma_fhp=0.01,
        sigma_neck=0.01,
        sigma_slump=0.01,
        sigma_pitch=1.0,
        sigma_tilt=0.8,
    )
    return in_memory_db.save_calibration_profile(rec)


def make_landmarks(
    *,
    w_sh: float = 200.0,
    y_sh_mid: float = 400.0,
    neck_len_ort: float = 100.0,
    w_eye: float = 60.0,
    pitch_deg: float = 0.0,
    yaw_deg: float = 0.0,
    roll_deg: float = 0.0,
) -> tuple[PoseLandmarkData, FaceLandmarkData]:
    """Helper creating PoseLandmarkData and FaceLandmarkData."""
    x_center = 640.0
    half_w = w_sh / 2.0

    ls = Point2D(x_center + half_w, y_sh_mid)
    rs = Point2D(x_center - half_w, y_sh_mid)

    y_ear = y_sh_mid - neck_len_ort
    le = Point2D(x_center + 40.0, y_ear)
    re = Point2D(x_center - 40.0, y_ear)
    nose = Point2D(x_center, y_ear + 20.0)

    pose = PoseLandmarkData(
        left_shoulder=ls,
        right_shoulder=rs,
        left_ear=le,
        right_ear=re,
        nose=nose,
    )

    y_eye = y_ear - 20.0
    half_eye = w_eye / 2.0
    face = FaceLandmarkData(
        bbox=BoundingBox(x_center - 60.0, y_eye - 40.0, x_center + 60.0, y_eye + 60.0),
        pitch_deg=pitch_deg,
        yaw_deg=yaw_deg,
        roll_deg=roll_deg,
        eye_outer_left=Point2D(x_center + half_eye, y_eye),
        eye_outer_right=Point2D(x_center - half_eye, y_eye),
    )
    return pose, face


class TestTelemetryPercentileAndBucket:
    """Test percentile interpolation and non-diluted 5-minute bucket aggregation."""

    def test_compute_percentile_known_distributions(self) -> None:
        """Verify linear percentile interpolation matches statistical definitions."""
        assert compute_percentile([], 95.0) == 0.0
        assert compute_percentile([42.0], 95.0) == 42.0

        data = list(range(101))  # 0, 1, ..., 100
        p95 = compute_percentile(data, 95.0)
        assert p95 == pytest.approx(95.0)

        p50 = compute_percentile(data, 50.0)
        assert p50 == pytest.approx(50.0)

    def test_telemetry_bucket_non_diluted_mean_violating_and_p95(self) -> None:
        """Verify mean_violating_* averages ONLY samples with V_i == 1 without upright dilution."""
        agg = TelemetryBucketAggregator(bucket_interval_s=300.0)

        # Feed 90 upright samples (V_fhp == 0) with small deviation 0.01
        for _ in range(90):
            agg.add_sample(
                dt=1.0,
                visibility=VisibilityState.MEASURABLE,
                deviations={"fhp": 0.01, "neck": 0.01, "slump": 0.005, "pitch": 0.5, "tilt": 0.2},
                flags=PostureViolationFlags(v_fhp=False),
            )

        # Feed 10 violating samples (V_fhp == 1) with deviations: [1.0, 1.1, ..., 1.9]
        violating_vals = [1.0 + 0.1 * i for i in range(10)]
        expected_violating_mean = sum(violating_vals) / len(violating_vals)

        for v in violating_vals:
            agg.add_sample(
                dt=1.0,
                visibility=VisibilityState.MEASURABLE,
                deviations={"fhp": v, "neck": 0.01, "slump": 0.005, "pitch": 0.5, "tilt": 0.2},
                flags=PostureViolationFlags(v_fhp=True),
            )

        rec = agg.flush_bucket(session_id=1, profile_id=1)

        assert rec.measurable_sec_in_bucket == pytest.approx(100.0)
        assert rec.duty_cycle_any_violation == pytest.approx(10.0 / 100.0)

        # mean_violating_fhp must equal 1.45 (NOT diluted by the 90 samples of 0.01!)
        assert rec.mean_violating_fhp is not None
        assert rec.mean_violating_fhp == pytest.approx(expected_violating_mean)

        # Neck compression was never violating in this bucket -> mean_violating_neck MUST be None!
        assert rec.mean_violating_neck is None

        # p95_fhp_dev covers all 100 samples
        assert rec.p95_fhp_dev > 1.0


class TestSessionOrchestratorIntegrations:
    """Test full integration of SessionOrchestrator with DB, drift, escalation, and bounds."""

    def test_chair_drift_transitions_to_needs_recalibration_without_false_alert(
        self,
        in_memory_db: DatabaseManager,
        profile_id: int,
        baseline_ref: CalibrationReference,
    ) -> None:
        """Chair distance shift triggers NEEDS_RECALIBRATION after 10s without false alert."""
        session_id = in_memory_db.create_session()
        orchestrator = SessionOrchestrator(
            db=in_memory_db,
            session_id=session_id,
            profile_id=profile_id,
            ref=baseline_ref,
        )

        # W_sh = 270px -> W_sh / W_sh_base = 270 / 200 = 1.35 (> 1.30)
        # d_fhp is normal (w_eye = 60 * 1.35 = 81 -> eye/sh ratio = 81 / 270 = 0.30 -> d_fhp = 0)
        pose, face = make_landmarks(w_sh=270.0, w_eye=81.0, neck_len_ort=135.0)

        t = datetime(2026, 11, 5, 10, 0, 0)
        for i in range(49):
            res = orchestrator.process_frame(
                pose=pose,
                face=face,
                visibility=VisibilityState.MEASURABLE,
                is_moving=False,
                dt=0.2,
                current_time_s=i * 0.2,
                now=t + timedelta(seconds=i * 0.2),
            )
            assert res.operating_mode == OperatingMode.ACTIVE_MONITORING
            assert res.ui_level != UIIntrusivenessLevel.ACTIVE_OVERLAY

        # Frame 50 -> reaches 10.0 seconds of sustained chair shift!
        res50 = orchestrator.process_frame(
            pose=pose,
            face=face,
            visibility=VisibilityState.MEASURABLE,
            is_moving=False,
            dt=0.2,
            current_time_s=50 * 0.2,
            now=t + timedelta(seconds=10.0),
        )

        assert res50.drift_detected
        assert res50.operating_mode == OperatingMode.NEEDS_RECALIBRATION
        assert orchestrator.operating_mode == OperatingMode.NEEDS_RECALIBRATION
        assert not res50.violation_flags.has_any_violation

    def test_alert_dismissal_sec_recorded_on_upright_recovery(
        self,
        in_memory_db: DatabaseManager,
        profile_id: int,
        baseline_ref: CalibrationReference,
    ) -> None:
        """When ACTIVE_OVERLAY extinguishes upon recovery, alert_dismissal_sec is saved."""
        session_id = in_memory_db.create_session()
        orchestrator = SessionOrchestrator(
            db=in_memory_db,
            session_id=session_id,
            profile_id=profile_id,
            ref=baseline_ref,
        )

        t0 = datetime(2026, 11, 5, 11, 0, 0)

        # 1. Trigger ACTIVE_OVERLAY by accumulating severe head pitch down for > 15s
        pose_bad, face_bad = make_landmarks(pitch_deg=-25.0)

        for i in range(80):
            orchestrator.process_frame(
                pose=pose_bad,
                face=face_bad,
                visibility=VisibilityState.MEASURABLE,
                is_moving=False,
                dt=0.2,
                current_time_s=i * 0.2,
                now=t0 + timedelta(seconds=i * 0.2),
            )

        assert orchestrator.ui_level == UIIntrusivenessLevel.ACTIVE_OVERLAY
        assert orchestrator.total_alerts_shown >= 1
        assert orchestrator.overlay_active_start is not None

        for i in range(80, 95):
            orchestrator.process_frame(
                pose=pose_bad,
                face=face_bad,
                visibility=VisibilityState.MEASURABLE,
                is_moving=False,
                dt=0.2,
                current_time_s=i * 0.2,
                now=t0 + timedelta(seconds=i * 0.2),
            )

        # 2. User returns to upright posture (pitch_deg = 0.0) -> recovery!
        pose_good, face_good = make_landmarks(pitch_deg=0.0)
        t_recovery = 95 * 0.2
        for k in range(8):
            t_curr = t_recovery + k * 0.2
            orchestrator.process_frame(
                pose=pose_good,
                face=face_good,
                visibility=VisibilityState.MEASURABLE,
                is_moving=False,
                dt=0.2,
                current_time_s=t_curr,
                now=t0 + timedelta(seconds=t_curr),
            )

        assert len(orchestrator.alert_dismissals) >= 1
        expected_latency = orchestrator.alert_dismissals[0]
        assert expected_latency >= 3.0

        orchestrator.close(end_time=t0 + timedelta(seconds=t_recovery + 2.0))
        intervals = in_memory_db.get_intervals_for_session(session_id)

        overlay_intervals = [
            it
            for it in intervals
            if it.ui_level == "ACTIVE_OVERLAY" and it.alert_dismissal_sec is not None
        ]
        assert len(overlay_intervals) >= 1
        assert overlay_intervals[0].alert_dismissal_sec == pytest.approx(
            expected_latency, abs=0.5
        )

    def test_database_persistence_and_adherence_bounds(
        self,
        in_memory_db: DatabaseManager,
        profile_id: int,
        baseline_ref: CalibrationReference,
    ) -> None:
        """Verify observability_coverage, reference_adherence_ratio, and conservative_bound."""
        session_id = in_memory_db.create_session()
        orchestrator = SessionOrchestrator(
            db=in_memory_db,
            session_id=session_id,
            profile_id=profile_id,
            ref=baseline_ref,
        )

        t0 = datetime(2026, 11, 5, 12, 0, 0)
        pose_upright, face_upright = make_landmarks()

        # Phase 1: 60s measurable upright (adherent)
        for i in range(300):
            orchestrator.process_frame(
                pose=pose_upright,
                face=face_upright,
                visibility=VisibilityState.MEASURABLE,
                is_moving=False,
                dt=0.2,
                current_time_s=i * 0.2,
                now=t0 + timedelta(seconds=i * 0.2),
            )

        # Phase 2: 20s measurable violating (slump)
        pose_slump, face_slump = make_landmarks(y_sh_mid=440.0)
        t_base = 60.0
        for i in range(100):
            orchestrator.process_frame(
                pose=pose_slump,
                face=face_slump,
                visibility=VisibilityState.MEASURABLE,
                is_moving=False,
                dt=0.2,
                current_time_s=t_base + i * 0.2,
                now=t0 + timedelta(seconds=t_base + i * 0.2),
            )

        # Phase 3: 20s occluded (10s lower, 10s upper)
        t_base = 80.0
        for i in range(50):
            orchestrator.process_frame(
                pose=None,
                face=face_upright,
                visibility=VisibilityState.OCCLUDED_LOWER,
                is_moving=False,
                dt=0.2,
                current_time_s=t_base + i * 0.2,
                now=t0 + timedelta(seconds=t_base + i * 0.2),
            )
        t_base = 90.0
        for i in range(50):
            orchestrator.process_frame(
                pose=pose_upright,
                face=None,
                visibility=VisibilityState.OCCLUDED_UPPER,
                is_moving=False,
                dt=0.2,
                current_time_s=t_base + i * 0.2,
                now=t0 + timedelta(seconds=t_base + i * 0.2),
            )

        orchestrator.close(end_time=t0 + timedelta(seconds=100.0))

        session = in_memory_db.get_session(session_id)
        assert session is not None

        # Durations: Measurable=80s, Occluded=20s, Total=100s, Adherent=60s
        assert session.measurable_seconds == pytest.approx(80.0, abs=0.5)
        assert session.occluded_lower_seconds == pytest.approx(10.0, abs=0.5)
        assert session.occluded_upper_seconds == pytest.approx(10.0, abs=0.5)
        assert session.adherent_seconds == pytest.approx(60.0, abs=0.5)

        # Bounds formulas:
        # observability_coverage = 80 / 100 = 0.80
        assert session.observability_coverage == pytest.approx(0.80, abs=0.01)
        # reference_adherence_ratio = 60 / 80 = 0.75
        assert session.reference_adherence_ratio == pytest.approx(0.75, abs=0.01)
        # adherence_conservative_bound = 60 / 100 = 0.60
        assert session.adherence_conservative_bound == pytest.approx(0.60, abs=0.01)

        buckets = in_memory_db.get_buckets_for_session(session_id)
        assert len(buckets) >= 1
        b = buckets[0]
        assert b.measurable_sec_in_bucket == pytest.approx(80.0, abs=0.5)
        assert b.occluded_sec_in_bucket == pytest.approx(20.0, abs=0.5)
