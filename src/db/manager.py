"""SQLite Database Manager and RLE Interval Storage for Bapode v3.0.

Reference: docs/specs/04-telemetry-and-schema.md & docs/ARCHITECTURE.md
Enforces:
- PRAGMA journal_mode = WAL;
- PRAGMA foreign_keys = ON;
- Run-Length Encoding (RLE) for posture state intervals.
- Non-diluted session adherence metrics computation.
"""

import sqlite3
import threading
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from tracker.states import IntervalSignature

try:
    import numpy as np

    sqlite3.register_adapter(np.int64, int)
    sqlite3.register_adapter(np.int32, int)
    sqlite3.register_adapter(np.float64, float)
    sqlite3.register_adapter(np.float32, float)
    sqlite3.register_adapter(np.bool_, bool)
except ImportError:
    pass


@dataclass(frozen=True, slots=True)
class CalibrationProfileRecord:
    """Record representing a calibration profile."""

    profile_id: int | None
    created_at: str | None
    base_neck_ort_ratio: float
    base_eye_sh_ratio: float
    base_sh_width_px: float
    base_sh_y_mid: float
    base_sh_angle_deg: float
    base_head_pitch_deg: float
    base_head_roll_deg: float
    sigma_fhp: float
    sigma_neck: float
    sigma_slump: float
    sigma_pitch: float
    sigma_tilt: float


@dataclass(frozen=True, slots=True)
class MonitoringSessionRecord:
    """Record representing a daily monitoring session."""

    session_id: int | None
    start_time: str
    end_time: str | None = None
    measurable_seconds: float = 0.0
    adherent_seconds: float = 0.0
    occluded_lower_seconds: float = 0.0
    occluded_upper_seconds: float = 0.0
    absent_seconds: float = 0.0
    paused_or_drift_seconds: float = 0.0
    fhp_violation_seconds: float = 0.0
    neck_comp_violation_seconds: float = 0.0
    slump_violation_seconds: float = 0.0
    gaze_pitch_violation_seconds: float = 0.0
    tilt_violation_seconds: float = 0.0
    observability_coverage: float = 1.0
    reference_adherence_ratio: float = 1.0
    adherence_conservative_bound: float = 1.0
    total_alerts_shown: int = 0
    total_microbreaks_prompted: int = 0
    mean_alert_dismissal_seconds: float | None = None


@dataclass(frozen=True, slots=True)
class PostureStateIntervalRecord:
    """Record representing a single Run-Length Encoded posture state interval."""

    interval_id: int | None
    session_id: int
    profile_id: int
    visibility_state: str
    ui_level: str
    started_at: str
    ended_at: str
    duration_seconds: float
    v_fhp: int = 0
    v_neck_comp: int = 0
    v_slump: int = 0
    v_gaze_pitch: int = 0
    v_tilt: int = 0
    alert_dismissal_sec: float | None = None


@dataclass(frozen=True, slots=True)
class TelemetryBucketRecord:
    """Record representing 5-minute telemetry aggregation without upright dilution."""

    bucket_id: int | None
    session_id: int
    profile_id: int
    bucket_timestamp: str
    measurable_sec_in_bucket: float
    occluded_sec_in_bucket: float
    duty_cycle_any_violation: float
    mean_violating_fhp: float | None
    p95_fhp_dev: float
    mean_violating_neck: float | None
    p95_neck_dev: float
    mean_violating_slump: float | None
    p95_slump_dev: float
    mean_violating_pitch_deg: float | None
    p95_pitch_deg: float
    mean_violating_tilt_deg: float | None
    p95_tilt_deg: float


class DatabaseManager:
    """SQLite WAL database manager for Bapode telemetry."""

    def __init__(self, db_path: str | Path = ":memory:") -> None:
        self.db_path = str(db_path)
        self._connection: sqlite3.Connection | None = None
        self._lock = threading.RLock()
        self._current_interval_id: int | None = None
        self._current_signature: IntervalSignature | None = None
        self._current_session_id: int | None = None
        self._current_profile_id: int | None = None
        self._current_interval_start: datetime | None = None

    def connect(self) -> sqlite3.Connection:
        """Create or return existing connection with WAL mode and foreign keys enabled."""
        with self._lock:
            if self._connection is None:
                conn = sqlite3.connect(self.db_path, check_same_thread=False)
                conn.row_factory = sqlite3.Row
                # Enforce foreign keys and WAL mode
                conn.execute("PRAGMA foreign_keys = ON;")
                conn.execute("PRAGMA journal_mode = WAL;")
                self._connection = conn
            return self._connection

    @contextmanager
    def transaction(self) -> Generator[sqlite3.Cursor, None, None]:
        """Context manager providing an explicit transactional cursor."""
        with self._lock:
            conn = self.connect()
            cursor = conn.cursor()
            try:
                yield cursor
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            finally:
                cursor.close()

    def initialize_schema(self) -> None:
        """Load and execute schema.sql DDL."""
        schema_path = Path(__file__).parent / "schema.sql"
        with open(schema_path, encoding="utf-8") as f:
            ddl = f.read()

        with self._lock:
            conn = self.connect()
            conn.executescript(ddl)
            conn.commit()

    def close(self) -> None:
        """Close database connection."""
        with self._lock:
            if self._connection is not None:
                self._connection.close()
                self._connection = None

    # --------------------------------------------------------------------------
    # 1. Calibration Profiles
    # --------------------------------------------------------------------------
    def save_calibration_profile(self, profile: CalibrationProfileRecord) -> int:
        """Insert a new calibration profile and return its profile_id."""
        query = """
            INSERT INTO calibration_profiles (
                base_neck_ort_ratio, base_eye_sh_ratio, base_sh_width_px,
                base_sh_y_mid, base_sh_angle_deg, base_head_pitch_deg,
                base_head_roll_deg, sigma_fhp, sigma_neck, sigma_slump,
                sigma_pitch, sigma_tilt
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """
        params = (
            profile.base_neck_ort_ratio,
            profile.base_eye_sh_ratio,
            profile.base_sh_width_px,
            profile.base_sh_y_mid,
            profile.base_sh_angle_deg,
            profile.base_head_pitch_deg,
            profile.base_head_roll_deg,
            profile.sigma_fhp,
            profile.sigma_neck,
            profile.sigma_slump,
            profile.sigma_pitch,
            profile.sigma_tilt,
        )
        with self.transaction() as cursor:
            cursor.execute(query, params)
            profile_id = cursor.lastrowid
            if profile_id is None:
                raise RuntimeError("Failed to obtain profile_id after insert.")
            return profile_id

    def get_calibration_profile(self, profile_id: int) -> CalibrationProfileRecord | None:
        """Fetch calibration profile by ID."""
        with self._lock:
            conn = self.connect()
            cursor = conn.execute(
                "SELECT * FROM calibration_profiles WHERE profile_id = ?",
                (int(profile_id),),
            )
            row = cursor.fetchone()
            if not row:
                return None
            return CalibrationProfileRecord(
                profile_id=row["profile_id"],
                created_at=row["created_at"],
                base_neck_ort_ratio=row["base_neck_ort_ratio"],
                base_eye_sh_ratio=row["base_eye_sh_ratio"],
                base_sh_width_px=row["base_sh_width_px"],
                base_sh_y_mid=row["base_sh_y_mid"],
                base_sh_angle_deg=row["base_sh_angle_deg"],
                base_head_pitch_deg=row["base_head_pitch_deg"],
                base_head_roll_deg=row["base_head_roll_deg"],
                sigma_fhp=row["sigma_fhp"],
                sigma_neck=row["sigma_neck"],
                sigma_slump=row["sigma_slump"],
                sigma_pitch=row["sigma_pitch"],
                sigma_tilt=row["sigma_tilt"],
            )

    # --------------------------------------------------------------------------
    # 2. Monitoring Sessions & Adherence Bounds
    # --------------------------------------------------------------------------
    @staticmethod
    def compute_session_bounds(
        measurable_seconds: float,
        adherent_seconds: float,
        occluded_lower_seconds: float,
        occluded_upper_seconds: float,
    ) -> tuple[float, float, float]:
        """Compute adherence bounds.

        Formulas from docs/specs/04-telemetry-and-schema.md:
        - observability_coverage = measurable / (measurable + occluded_lower + occluded_upper)
        - reference_adherence_ratio = adherent / measurable
        - adherence_conservative_bound = adherent / (measurable + occluded_lower + occluded_upper)
        """
        total_observed = measurable_seconds + occluded_lower_seconds + occluded_upper_seconds
        coverage = (measurable_seconds / total_observed) if total_observed > 0.0 else 1.0
        ref_adherence = (adherent_seconds / measurable_seconds) if measurable_seconds > 0.0 else 1.0
        conservative_bound = (adherent_seconds / total_observed) if total_observed > 0.0 else 1.0
        return coverage, ref_adherence, conservative_bound

    def create_session(self, start_time: datetime | None = None) -> int:
        """Create a new monitoring session and return session_id."""
        ts = (start_time or datetime.now()).strftime("%Y-%m-%d %H:%M:%S")
        query = "INSERT INTO monitoring_sessions (start_time) VALUES (?)"
        with self.transaction() as cursor:
            cursor.execute(query, (ts,))
            session_id = cursor.lastrowid
            if session_id is None:
                raise RuntimeError("Failed to obtain session_id after insert.")
            return session_id

    def close_session(self, session_id: int, end_time: datetime | None = None) -> None:
        """Close an active session with end timestamp and flush any active interval."""
        self.flush_current_interval(end_time or datetime.now())
        ts = (end_time or datetime.now()).strftime("%Y-%m-%d %H:%M:%S")
        with self.transaction() as cursor:
            cursor.execute(
                "UPDATE monitoring_sessions SET end_time = ? WHERE session_id = ?",
                (ts, session_id),
            )

    def update_session_metrics(
        self,
        session_id: int,
        *,
        measurable_seconds: float,
        adherent_seconds: float,
        occluded_lower_seconds: float,
        occluded_upper_seconds: float,
        absent_seconds: float,
        paused_or_drift_seconds: float,
        fhp_violation_seconds: float = 0.0,
        neck_comp_violation_seconds: float = 0.0,
        slump_violation_seconds: float = 0.0,
        gaze_pitch_violation_seconds: float = 0.0,
        tilt_violation_seconds: float = 0.0,
        total_alerts_shown: int = 0,
        total_microbreaks_prompted: int = 0,
        mean_alert_dismissal_seconds: float | None = None,
    ) -> None:
        """Update session durations and recompute adherence and observability bounds."""
        coverage, ref_adherence, conservative_bound = self.compute_session_bounds(
            measurable_seconds=measurable_seconds,
            adherent_seconds=adherent_seconds,
            occluded_lower_seconds=occluded_lower_seconds,
            occluded_upper_seconds=occluded_upper_seconds,
        )

        query = """
            UPDATE monitoring_sessions SET
                measurable_seconds = ?,
                adherent_seconds = ?,
                occluded_lower_seconds = ?,
                occluded_upper_seconds = ?,
                absent_seconds = ?,
                paused_or_drift_seconds = ?,
                fhp_violation_seconds = ?,
                neck_comp_violation_seconds = ?,
                slump_violation_seconds = ?,
                gaze_pitch_violation_seconds = ?,
                tilt_violation_seconds = ?,
                observability_coverage = ?,
                reference_adherence_ratio = ?,
                adherence_conservative_bound = ?,
                total_alerts_shown = ?,
                total_microbreaks_prompted = ?,
                mean_alert_dismissal_seconds = ?
            WHERE session_id = ?
        """
        params = (
            float(measurable_seconds),
            float(adherent_seconds),
            float(occluded_lower_seconds),
            float(occluded_upper_seconds),
            float(absent_seconds),
            float(paused_or_drift_seconds),
            float(fhp_violation_seconds),
            float(neck_comp_violation_seconds),
            float(slump_violation_seconds),
            float(gaze_pitch_violation_seconds),
            float(tilt_violation_seconds),
            float(coverage),
            float(ref_adherence),
            float(conservative_bound),
            int(total_alerts_shown),
            int(total_microbreaks_prompted),
            (
                float(mean_alert_dismissal_seconds)
                if mean_alert_dismissal_seconds is not None
                else None
            ),
            int(session_id),
        )
        with self.transaction() as cursor:
            cursor.execute(query, params)

    def get_session(self, session_id: int) -> MonitoringSessionRecord | None:
        """Retrieve monitoring session by ID."""
        with self._lock:
            conn = self.connect()
            cursor = conn.execute(
                "SELECT * FROM monitoring_sessions WHERE session_id = ?",
                (int(session_id),),
            )
            row = cursor.fetchone()
            if not row:
                return None
            return MonitoringSessionRecord(
                session_id=row["session_id"],
                start_time=row["start_time"],
                end_time=row["end_time"],
                measurable_seconds=row["measurable_seconds"],
                adherent_seconds=row["adherent_seconds"],
                occluded_lower_seconds=row["occluded_lower_seconds"],
                occluded_upper_seconds=row["occluded_upper_seconds"],
                absent_seconds=row["absent_seconds"],
                paused_or_drift_seconds=row["paused_or_drift_seconds"],
                fhp_violation_seconds=row["fhp_violation_seconds"],
                neck_comp_violation_seconds=row["neck_comp_violation_seconds"],
                slump_violation_seconds=row["slump_violation_seconds"],
                gaze_pitch_violation_seconds=row["gaze_pitch_violation_seconds"],
                tilt_violation_seconds=row["tilt_violation_seconds"],
                observability_coverage=row["observability_coverage"],
                reference_adherence_ratio=row["reference_adherence_ratio"],
                adherence_conservative_bound=row["adherence_conservative_bound"],
                total_alerts_shown=row["total_alerts_shown"],
                total_microbreaks_prompted=row["total_microbreaks_prompted"],
                mean_alert_dismissal_seconds=row["mean_alert_dismissal_seconds"],
            )

    def get_latest_session(self) -> MonitoringSessionRecord | None:
        """Retrieve the most recent monitoring session."""
        with self._lock:
            conn = self.connect()
            cursor = conn.execute(
                "SELECT * FROM monitoring_sessions ORDER BY session_id DESC LIMIT 1"
            )
            row = cursor.fetchone()
            if not row:
                return None
            return MonitoringSessionRecord(
                session_id=row["session_id"],
                start_time=row["start_time"],
                end_time=row["end_time"],
                measurable_seconds=row["measurable_seconds"],
                adherent_seconds=row["adherent_seconds"],
                occluded_lower_seconds=row["occluded_lower_seconds"],
                occluded_upper_seconds=row["occluded_upper_seconds"],
                absent_seconds=row["absent_seconds"],
                paused_or_drift_seconds=row["paused_or_drift_seconds"],
                fhp_violation_seconds=row["fhp_violation_seconds"],
                neck_comp_violation_seconds=row["neck_comp_violation_seconds"],
                slump_violation_seconds=row["slump_violation_seconds"],
                gaze_pitch_violation_seconds=row["gaze_pitch_violation_seconds"],
                tilt_violation_seconds=row["tilt_violation_seconds"],
                observability_coverage=row["observability_coverage"],
                reference_adherence_ratio=row["reference_adherence_ratio"],
                adherence_conservative_bound=row["adherence_conservative_bound"],
                total_alerts_shown=row["total_alerts_shown"],
                total_microbreaks_prompted=row["total_microbreaks_prompted"],
                mean_alert_dismissal_seconds=row["mean_alert_dismissal_seconds"],
            )

    # --------------------------------------------------------------------------
    # 3. Posture State Intervals & RLE Engine
    # --------------------------------------------------------------------------
    def record_state(
        self,
        session_id: int,
        profile_id: int,
        signature: IntervalSignature,
        now: datetime | None = None,
        alert_dismissal_sec: float | None = None,
    ) -> None:
        """Process incoming state signature using Run-Length Encoding (RLE).

        If the signature matches the currently active interval, updates ended_at and duration.
        If the signature changes (or no active interval), flushes current and starts new interval.
        """
        with self._lock:
            current_time = now or datetime.now()

            # Check if incoming signature matches current running interval
            if (
                self._current_interval_id is not None
                and self._current_signature == signature
                and self._current_session_id == session_id
                and self._current_profile_id == profile_id
                and self._current_interval_start is not None
            ):
                # Extend current interval duration
                duration = max(0.0, (current_time - self._current_interval_start).total_seconds())
                self._update_interval_duration(
                    self._current_interval_id,
                    current_time.strftime("%Y-%m-%d %H:%M:%S"),
                    duration,
                    alert_dismissal_sec,
                )
                return

            # Signature changed: flush existing interval
            self.flush_current_interval(current_time, alert_dismissal_sec=alert_dismissal_sec)

            # Start new interval
            self._start_new_interval(
                session_id=session_id,
                profile_id=profile_id,
                signature=signature,
                start_time=current_time,
                alert_dismissal_sec=None,
            )

    def _start_new_interval(
        self,
        session_id: int,
        profile_id: int,
        signature: IntervalSignature,
        start_time: datetime,
        alert_dismissal_sec: float | None = None,
    ) -> None:
        """Insert a new interval record into SQLite."""
        with self._lock:
            ts = start_time.strftime("%Y-%m-%d %H:%M:%S")
            v_fhp, v_neck, v_slump, v_pitch, v_tilt = signature.violations.as_db_tuple()

            query = """
                INSERT INTO posture_state_intervals (
                    session_id, profile_id, visibility_state, ui_level,
                    started_at, ended_at, duration_seconds,
                    v_fhp, v_neck_comp, v_slump, v_gaze_pitch, v_tilt,
                    alert_dismissal_sec
                ) VALUES (?, ?, ?, ?, ?, ?, 0.0, ?, ?, ?, ?, ?, ?)
            """
            params = (
                int(session_id),
                int(profile_id),
                str(signature.visibility_state.value),
                str(signature.ui_level.value),
                ts,
                ts,
                int(v_fhp),
                int(v_neck),
                int(v_slump),
                int(v_pitch),
                int(v_tilt),
                float(alert_dismissal_sec) if alert_dismissal_sec is not None else None,
            )

            with self.transaction() as cursor:
                cursor.execute(query, params)
                self._current_interval_id = cursor.lastrowid
                self._current_signature = signature
                self._current_session_id = session_id
                self._current_profile_id = profile_id
                self._current_interval_start = start_time

    def _update_interval_duration(
        self,
        interval_id: int,
        ended_at_str: str,
        duration_seconds: float,
        alert_dismissal_sec: float | None = None,
    ) -> None:
        """Update ended_at and duration_seconds of the active interval."""
        with self._lock:
            int_id = int(interval_id)
            dur = float(duration_seconds)
            dismissal = float(alert_dismissal_sec) if alert_dismissal_sec is not None else None

            if dismissal is not None:
                query = """
                    UPDATE posture_state_intervals
                    SET ended_at = ?, duration_seconds = ?, alert_dismissal_sec = ?
                    WHERE interval_id = ?
                """
                params: tuple[Any, ...] = (
                    str(ended_at_str),
                    dur,
                    dismissal,
                    int_id,
                )
            else:
                query = """
                    UPDATE posture_state_intervals
                    SET ended_at = ?, duration_seconds = ?
                    WHERE interval_id = ?
                """
                params = (str(ended_at_str), dur, int_id)

            with self.transaction() as cursor:
                cursor.execute(query, params)

    def flush_current_interval(
        self,
        flush_time: datetime | None = None,
        alert_dismissal_sec: float | None = None,
    ) -> None:
        """Flush and commit the active interval if one exists."""
        with self._lock:
            if self._current_interval_id is not None and self._current_interval_start is not None:
                end_t = flush_time or datetime.now()
                duration = max(0.0, (end_t - self._current_interval_start).total_seconds())
                self._update_interval_duration(
                    self._current_interval_id,
                    end_t.strftime("%Y-%m-%d %H:%M:%S"),
                    duration,
                    alert_dismissal_sec,
                )
                # Reset active tracking state
                self._current_interval_id = None
                self._current_signature = None
                self._current_session_id = None
                self._current_profile_id = None
                self._current_interval_start = None

    def get_intervals_for_session(self, session_id: int) -> list[PostureStateIntervalRecord]:
        """Fetch all posture intervals for a given session sorted by start time."""
        with self._lock:
            conn = self.connect()
            cursor = conn.execute(
                """
                SELECT * FROM posture_state_intervals
                WHERE session_id = ?
                ORDER BY started_at ASC, interval_id ASC
                """,
                (int(session_id),),
            )
            records = []
            for row in cursor.fetchall():
                records.append(
                    PostureStateIntervalRecord(
                        interval_id=row["interval_id"],
                        session_id=row["session_id"],
                        profile_id=row["profile_id"],
                        visibility_state=row["visibility_state"],
                        ui_level=row["ui_level"],
                        started_at=row["started_at"],
                        ended_at=row["ended_at"],
                        duration_seconds=row["duration_seconds"],
                        v_fhp=row["v_fhp"],
                        v_neck_comp=row["v_neck_comp"],
                        v_slump=row["v_slump"],
                        v_gaze_pitch=row["v_gaze_pitch"],
                        v_tilt=row["v_tilt"],
                        alert_dismissal_sec=row["alert_dismissal_sec"],
                    )
                )
            return records

    # --------------------------------------------------------------------------
    # 4. Session Telemetry Buckets (5-Minute Aggregation)
    # --------------------------------------------------------------------------
    def record_telemetry_bucket(self, bucket: TelemetryBucketRecord) -> int:
        """Insert a 5-minute telemetry aggregation bucket."""
        query = """
            INSERT INTO session_telemetry_buckets (
                session_id, profile_id, bucket_timestamp,
                measurable_sec_in_bucket, occluded_sec_in_bucket, duty_cycle_any_violation,
                mean_violating_fhp, p95_fhp_dev,
                mean_violating_neck, p95_neck_dev,
                mean_violating_slump, p95_slump_dev,
                mean_violating_pitch_deg, p95_pitch_deg,
                mean_violating_tilt_deg, p95_tilt_deg
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """
        params = (
            int(bucket.session_id),
            int(bucket.profile_id),
            str(bucket.bucket_timestamp),
            float(bucket.measurable_sec_in_bucket),
            float(bucket.occluded_sec_in_bucket),
            float(bucket.duty_cycle_any_violation),
            float(bucket.mean_violating_fhp) if bucket.mean_violating_fhp is not None else None,
            float(bucket.p95_fhp_dev),
            float(bucket.mean_violating_neck) if bucket.mean_violating_neck is not None else None,
            float(bucket.p95_neck_dev),
            float(bucket.mean_violating_slump) if bucket.mean_violating_slump is not None else None,
            float(bucket.p95_slump_dev),
            (
                float(bucket.mean_violating_pitch_deg)
                if bucket.mean_violating_pitch_deg is not None
                else None
            ),
            float(bucket.p95_pitch_deg),
            (
                float(bucket.mean_violating_tilt_deg)
                if bucket.mean_violating_tilt_deg is not None
                else None
            ),
            float(bucket.p95_tilt_deg),
        )
        with self.transaction() as cursor:
            cursor.execute(query, params)
            bucket_id = cursor.lastrowid
            if bucket_id is None:
                raise RuntimeError("Failed to obtain bucket_id after insert.")
            return bucket_id

    def get_buckets_for_session(self, session_id: int) -> list[TelemetryBucketRecord]:
        """Fetch all telemetry buckets for a given session."""
        with self._lock:
            conn = self.connect()
            cursor = conn.execute(
                """
                SELECT * FROM session_telemetry_buckets
                WHERE session_id = ?
                ORDER BY bucket_timestamp ASC
                """,
                (int(session_id),),
            )
            records = []
            for row in cursor.fetchall():
                records.append(
                    TelemetryBucketRecord(
                        bucket_id=row["bucket_id"],
                        session_id=row["session_id"],
                        profile_id=row["profile_id"],
                        bucket_timestamp=row["bucket_timestamp"],
                        measurable_sec_in_bucket=row["measurable_sec_in_bucket"],
                        occluded_sec_in_bucket=row["occluded_sec_in_bucket"],
                        duty_cycle_any_violation=row["duty_cycle_any_violation"],
                        mean_violating_fhp=row["mean_violating_fhp"],
                        p95_fhp_dev=row["p95_fhp_dev"],
                        mean_violating_neck=row["mean_violating_neck"],
                        p95_neck_dev=row["p95_neck_dev"],
                        mean_violating_slump=row["mean_violating_slump"],
                        p95_slump_dev=row["p95_slump_dev"],
                        mean_violating_pitch_deg=row["mean_violating_pitch_deg"],
                        p95_pitch_deg=row["p95_pitch_deg"],
                        mean_violating_tilt_deg=row["mean_violating_tilt_deg"],
                        p95_tilt_deg=row["p95_tilt_deg"],
                    )
                )
            return records

    def get_recent_buckets(
        self, session_id: int, limit: int = 20
    ) -> list[sqlite3.Row]:
        """Fetch latest telemetry buckets for a given session."""
        with self._lock:
            conn = self.connect()
            cursor = conn.execute(
                """
                SELECT bucket_timestamp, measurable_sec_in_bucket, duty_cycle_any_violation,
                       mean_violating_fhp, p95_fhp_dev, mean_violating_neck, mean_violating_slump
                FROM session_telemetry_buckets
                WHERE session_id = ?
                ORDER BY bucket_timestamp DESC LIMIT ?
                """,
                (int(session_id), int(limit)),
            )
            return cursor.fetchall()
