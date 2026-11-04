"""Unit tests for SQLite v3.0 WAL database layer and telemetry schema."""

import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from db.manager import (
    CalibrationProfileRecord,
    DatabaseManager,
    TelemetryBucketRecord,
)
from tracker.states import (
    IntervalSignature,
    PostureViolationFlags,
    UIIntrusivenessLevel,
    VisibilityState,
)


@pytest.fixture
def in_memory_db() -> DatabaseManager:
    """Fixture providing an initialized in-memory database."""
    db = DatabaseManager(":memory:")
    db.initialize_schema()
    return db


@pytest.fixture
def sample_profile(in_memory_db: DatabaseManager) -> int:
    """Fixture creating a base calibration profile."""
    rec = CalibrationProfileRecord(
        profile_id=None,
        created_at=None,
        base_neck_ort_ratio=0.45,
        base_eye_sh_ratio=0.32,
        base_sh_width_px=220.0,
        base_sh_y_mid=340.0,
        base_sh_angle_deg=1.2,
        base_head_pitch_deg=-3.4,
        base_head_roll_deg=0.5,
        sigma_fhp=0.015,
        sigma_neck=0.018,
        sigma_slump=0.012,
        sigma_pitch=1.8,
        sigma_tilt=1.2,
    )
    return in_memory_db.save_calibration_profile(rec)


class TestDatabaseSchemaAndConstraints:
    """Test table creation, WAL mode, foreign keys, and CHECK constraints."""

    def test_schema_initialization_tables_and_indices(self, in_memory_db: DatabaseManager) -> None:
        conn = in_memory_db.connect()
        cursor = conn.cursor()

        # Check tables
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = {row[0] for row in cursor.fetchall()}
        expected_tables = {
            "calibration_profiles",
            "monitoring_sessions",
            "posture_state_intervals",
            "session_telemetry_buckets",
        }
        assert expected_tables.issubset(tables)

        # Check indices
        cursor.execute("SELECT name FROM sqlite_master WHERE type='index'")
        indices = {row[0] for row in cursor.fetchall()}
        assert "idx_intervals_session" in indices
        assert "idx_buckets_session" in indices

    def test_wal_mode_on_disk(self, tmp_path: Path) -> None:
        db_file = tmp_path / "posture_test.db"
        db = DatabaseManager(db_file)
        db.initialize_schema()

        conn = db.connect()
        cursor = conn.cursor()
        cursor.execute("PRAGMA journal_mode;")
        mode = cursor.fetchone()[0]
        assert mode.lower() == "wal"
        db.close()

    def test_foreign_key_enforcement(
        self, in_memory_db: DatabaseManager, sample_profile: int
    ) -> None:
        conn = in_memory_db.connect()
        cursor = conn.cursor()

        # Attempt to insert interval with non-existent session_id -> should fail
        with pytest.raises(sqlite3.IntegrityError):
            cursor.execute(
                """
                INSERT INTO posture_state_intervals (
                    session_id, profile_id, visibility_state, ui_level,
                    started_at, ended_at, duration_seconds
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    9999,
                    sample_profile,
                    "MEASURABLE",
                    "SILENT_OK",
                    "2026-11-05 08:00:00",
                    "2026-11-05 08:00:15",
                    15.0,
                ),
            )

    def test_foreign_key_cascade_deletion(
        self, in_memory_db: DatabaseManager, sample_profile: int
    ) -> None:
        session_id = in_memory_db.create_session()
        sig = IntervalSignature(
            visibility_state=VisibilityState.MEASURABLE,
            ui_level=UIIntrusivenessLevel.SILENT_OK,
            violations=PostureViolationFlags(),
        )
        in_memory_db.record_state(session_id, sample_profile, sig)
        in_memory_db.flush_current_interval()

        # Verify interval exists
        intervals = in_memory_db.get_intervals_for_session(session_id)
        assert len(intervals) == 1

        # Delete session
        with in_memory_db.transaction() as cursor:
            cursor.execute("DELETE FROM monitoring_sessions WHERE session_id = ?", (session_id,))

        # Verify interval was deleted via cascade
        intervals_after = in_memory_db.get_intervals_for_session(session_id)
        assert len(intervals_after) == 0

    def test_check_constraint_visibility_state(
        self, in_memory_db: DatabaseManager, sample_profile: int
    ) -> None:
        session_id = in_memory_db.create_session()
        conn = in_memory_db.connect()
        cursor = conn.cursor()

        valid_states = [
            "MEASURABLE",
            "OCCLUDED_LOWER",
            "OCCLUDED_UPPER",
            "ABSENT",
            "PAUSED",
            "NEEDS_RECALIBRATION",
        ]
        for st in valid_states:
            cursor.execute(
                """
                INSERT INTO posture_state_intervals (
                    session_id, profile_id, visibility_state, ui_level,
                    started_at, ended_at, duration_seconds
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session_id,
                    sample_profile,
                    st,
                    "SILENT_OK",
                    "2026-11-05 08:00:00",
                    "2026-11-05 08:00:05",
                    5.0,
                ),
            )

        # Invalid visibility state must raise IntegrityError
        with pytest.raises(sqlite3.IntegrityError):
            cursor.execute(
                """
                INSERT INTO posture_state_intervals (
                    session_id, profile_id, visibility_state, ui_level,
                    started_at, ended_at, duration_seconds
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session_id,
                    sample_profile,
                    "INVALID_STATE",
                    "SILENT_OK",
                    "2026-11-05 08:00:00",
                    "2026-11-05 08:00:05",
                    5.0,
                ),
            )

    def test_check_constraint_ui_level(
        self, in_memory_db: DatabaseManager, sample_profile: int
    ) -> None:
        session_id = in_memory_db.create_session()
        conn = in_memory_db.connect()
        cursor = conn.cursor()

        valid_levels = [
            "SILENT_OK",
            "AMBIENT_WARN",
            "ACTIVE_OVERLAY",
            "SUPPRESSED_COOLDOWN",
            "MICROBREAK_PROMPT",
        ]
        for lvl in valid_levels:
            cursor.execute(
                """
                INSERT INTO posture_state_intervals (
                    session_id, profile_id, visibility_state, ui_level,
                    started_at, ended_at, duration_seconds
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session_id,
                    sample_profile,
                    "MEASURABLE",
                    lvl,
                    "2026-11-05 08:00:00",
                    "2026-11-05 08:00:05",
                    5.0,
                ),
            )

        # Invalid UI level must raise IntegrityError
        with pytest.raises(sqlite3.IntegrityError):
            cursor.execute(
                """
                INSERT INTO posture_state_intervals (
                    session_id, profile_id, visibility_state, ui_level,
                    started_at, ended_at, duration_seconds
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session_id,
                    sample_profile,
                    "MEASURABLE",
                    "MAX_ALERT_POPUP",
                    "2026-11-05 08:00:00",
                    "2026-11-05 08:00:05",
                    5.0,
                ),
            )


class TestCalibrationAndSessionMetrics:
    """Test profile storage and session bounds mathematical formulas."""

    def test_calibration_profile_roundtrip(self, in_memory_db: DatabaseManager) -> None:
        rec = CalibrationProfileRecord(
            profile_id=None,
            created_at=None,
            base_neck_ort_ratio=0.55,
            base_eye_sh_ratio=0.38,
            base_sh_width_px=245.5,
            base_sh_y_mid=312.0,
            base_sh_angle_deg=-0.8,
            base_head_pitch_deg=2.1,
            base_head_roll_deg=0.1,
            sigma_fhp=0.02,
            sigma_neck=0.03,
            sigma_slump=0.025,
            sigma_pitch=2.0,
            sigma_tilt=1.5,
        )
        pid = in_memory_db.save_calibration_profile(rec)
        fetched = in_memory_db.get_calibration_profile(pid)
        assert fetched is not None
        assert fetched.profile_id == pid
        assert fetched.base_neck_ort_ratio == 0.55
        assert fetched.base_sh_width_px == 245.5
        assert fetched.sigma_tilt == 1.5

    def test_session_bounds_computation(self) -> None:
        # Case 1: Pure measurable and adherent
        cov, ref_adh, cons_b = DatabaseManager.compute_session_bounds(
            measurable_seconds=100.0,
            adherent_seconds=100.0,
            occluded_lower_seconds=0.0,
            occluded_upper_seconds=0.0,
        )
        assert cov == 1.0
        assert ref_adh == 1.0
        assert cons_b == 1.0

        # Case 2: 80s measurable (60s adherent), 20s occluded (10 lower, 10 upper)
        cov, ref_adh, cons_b = DatabaseManager.compute_session_bounds(
            measurable_seconds=80.0,
            adherent_seconds=60.0,
            occluded_lower_seconds=10.0,
            occluded_upper_seconds=10.0,
        )
        # Total observed = 80 + 20 = 100
        assert cov == pytest.approx(0.80)
        assert ref_adh == pytest.approx(60.0 / 80.0)  # 0.75
        assert cons_b == pytest.approx(60.0 / 100.0)  # 0.60

        # Case 3: Zero observed time edge case
        cov, ref_adh, cons_b = DatabaseManager.compute_session_bounds(0.0, 0.0, 0.0, 0.0)
        assert cov == 1.0
        assert ref_adh == 1.0
        assert cons_b == 1.0

    def test_update_session_metrics(self, in_memory_db: DatabaseManager) -> None:
        session_id = in_memory_db.create_session()
        in_memory_db.update_session_metrics(
            session_id,
            measurable_seconds=80.0,
            adherent_seconds=60.0,
            occluded_lower_seconds=10.0,
            occluded_upper_seconds=10.0,
            absent_seconds=50.0,
            paused_or_drift_seconds=30.0,
            fhp_violation_seconds=15.0,
            total_alerts_shown=2,
            total_microbreaks_prompted=1,
            mean_alert_dismissal_seconds=3.5,
        )
        session = in_memory_db.get_session(session_id)
        assert session is not None
        assert session.observability_coverage == pytest.approx(0.80)
        assert session.reference_adherence_ratio == pytest.approx(0.75)
        assert session.adherence_conservative_bound == pytest.approx(0.60)
        assert session.total_alerts_shown == 2
        assert session.mean_alert_dismissal_seconds == 3.5


class TestRLEIntervalRecording:
    """Test Run-Length Encoding logic for state intervals."""

    def test_steady_state_extends_interval(
        self, in_memory_db: DatabaseManager, sample_profile: int
    ) -> None:
        session_id = in_memory_db.create_session()
        t0 = datetime(2026, 11, 5, 9, 0, 0)
        sig = IntervalSignature(
            visibility_state=VisibilityState.MEASURABLE,
            ui_level=UIIntrusivenessLevel.SILENT_OK,
            violations=PostureViolationFlags(),
        )

        # First tick at t0
        in_memory_db.record_state(session_id, sample_profile, sig, now=t0)
        # Second tick at t0 + 5s (same signature)
        in_memory_db.record_state(session_id, sample_profile, sig, now=t0 + timedelta(seconds=5))
        # Third tick at t0 + 15s (same signature - 15s flush)
        in_memory_db.record_state(session_id, sample_profile, sig, now=t0 + timedelta(seconds=15))

        # Explicit flush to finish session
        in_memory_db.flush_current_interval(flush_time=t0 + timedelta(seconds=15))

        intervals = in_memory_db.get_intervals_for_session(session_id)
        # Must only have 1 single RLE interval row!
        assert len(intervals) == 1
        assert intervals[0].duration_seconds == pytest.approx(15.0)
        assert intervals[0].started_at == "2026-11-05 09:00:00"
        assert intervals[0].ended_at == "2026-11-05 09:00:15"
        assert intervals[0].visibility_state == "MEASURABLE"
        assert intervals[0].ui_level == "SILENT_OK"

    def test_state_change_creates_new_interval(
        self, in_memory_db: DatabaseManager, sample_profile: int
    ) -> None:
        session_id = in_memory_db.create_session()
        t0 = datetime(2026, 11, 5, 9, 0, 0)

        sig_ok = IntervalSignature(
            visibility_state=VisibilityState.MEASURABLE,
            ui_level=UIIntrusivenessLevel.SILENT_OK,
            violations=PostureViolationFlags(),
        )
        sig_violating = IntervalSignature(
            visibility_state=VisibilityState.MEASURABLE,
            ui_level=UIIntrusivenessLevel.AMBIENT_WARN,
            violations=PostureViolationFlags(v_fhp=True),
        )

        # 0s - 10s: OK
        in_memory_db.record_state(session_id, sample_profile, sig_ok, now=t0)
        t_10 = t0 + timedelta(seconds=10)
        in_memory_db.record_state(session_id, sample_profile, sig_ok, now=t_10)

        # 10s - 25s: Violating
        in_memory_db.record_state(session_id, sample_profile, sig_violating, now=t_10)
        t_25 = t0 + timedelta(seconds=25)
        in_memory_db.record_state(session_id, sample_profile, sig_violating, now=t_25)

        in_memory_db.close_session(session_id, end_time=t_25)

        intervals = in_memory_db.get_intervals_for_session(session_id)
        assert len(intervals) == 2

        # First interval
        assert intervals[0].duration_seconds == pytest.approx(10.0)
        assert intervals[0].ui_level == "SILENT_OK"
        assert intervals[0].v_fhp == 0

        # Second interval
        assert intervals[1].duration_seconds == pytest.approx(15.0)
        assert intervals[1].ui_level == "AMBIENT_WARN"
        assert intervals[1].v_fhp == 1


class TestTelemetryBuckets:
    """Test 5-minute bucket aggregation insertion and retrieval."""

    def test_telemetry_bucket_with_and_without_violations(
        self, in_memory_db: DatabaseManager, sample_profile: int
    ) -> None:
        session_id = in_memory_db.create_session()

        # Bucket 1: Melanggar FHP dan Neck Compression
        bucket1 = TelemetryBucketRecord(
            bucket_id=None,
            session_id=session_id,
            profile_id=sample_profile,
            bucket_timestamp="2026-11-05 09:05:00",
            measurable_sec_in_bucket=280.0,
            occluded_sec_in_bucket=20.0,
            duty_cycle_any_violation=0.35,
            mean_violating_fhp=1.42,
            p95_fhp_dev=1.55,
            mean_violating_neck=1.28,
            p95_neck_dev=1.35,
            mean_violating_slump=None,  # Tidak ada pelanggaran slump
            p95_slump_dev=0.10,
            mean_violating_pitch_deg=None,
            p95_pitch_deg=2.5,
            mean_violating_tilt_deg=None,
            p95_tilt_deg=1.1,
        )
        b1_id = in_memory_db.record_telemetry_bucket(bucket1)
        assert b1_id > 0

        buckets = in_memory_db.get_buckets_for_session(session_id)
        assert len(buckets) == 1
        b = buckets[0]
        assert b.duty_cycle_any_violation == 0.35
        assert b.mean_violating_fhp == 1.42
        assert b.mean_violating_slump is None
        assert b.p95_fhp_dev == 1.55
