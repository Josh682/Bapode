"""Unit tests for signal processing, velocity gate, IssueTracker, and UI escalation."""

import pytest

from tracker.filter import AsymmetricEMA, NormalizedVelocityDetector, SlidingMedian3
from tracker.issue_tracker import IssueTracker, PostureEscalationController
from tracker.states import UIIntrusivenessLevel


class TestSlidingMedian3:
    """Test 3-sample sliding median filter."""

    def test_impulse_noise_rejection(self) -> None:
        # A 1-sample spike (impulse) must be rejected
        med = SlidingMedian3()
        # Step 1: 0.0 -> returns 0.0
        assert med.update(0.0) == 0.0
        # Step 2: 1.0 (spike begins) -> average of [0.0, 1.0] = 0.5
        assert med.update(1.0) == 0.5
        # Step 3: 0.0 (spike ended) -> median of [0.0, 1.0, 0.0] = 0.0
        # The 1-sample spike is completely eliminated!
        assert med.update(0.0) == 0.0


class TestAsymmetricEMA:
    """Test dt-aware Asymmetric EMA filter."""

    def test_decay_latency_at_5fps(self) -> None:
        """CRITICAL VERIFICATION: Decay latency from violation to recovery at 5 FPS (dt=0.2s).

        Target: Latency to decay from a violating level (e.g. 1.0) to exit threshold (<= 0.04)
        must fall in the range ~0.7 - 1.2 seconds.
        """
        ema = AsymmetricEMA(tau_attack=1.0, tau_release=0.3)
        ema.reset(initial_value=1.0)  # Established violation state

        dt = 0.2  # 5 FPS
        time_elapsed = 0.0
        threshold_exit = 0.04

        # User sits upright: input drops to 0.0
        while ema.value is not None and ema.value > threshold_exit:
            ema.update(0.0, dt)
            time_elapsed += dt
            if time_elapsed > 5.0:
                break

        # Verification: time to reach exit threshold must be between 0.7s and 1.2s
        assert 0.7 <= time_elapsed <= 1.2


class TestNormalizedVelocityDetector:
    """Test shoulder-normalized motion detector."""

    def test_stationary_vs_moving(self) -> None:
        detector = NormalizedVelocityDetector(v_thresh=0.15)
        # First sample initializes
        v, moving = detector.update(x_sh_mid=640.0, y_sh_mid=400.0, w_sh=200.0, dt=0.2)
        assert moving is False

        # Stationary small jitter: dx = 0.5px -> v_norm = 0.5 / (0.2 * 200) = 0.0125 << 0.15
        v, moving = detector.update(x_sh_mid=640.5, y_sh_mid=400.0, w_sh=200.0, dt=0.2)
        assert moving is False
        assert v < 0.15

        # Fast reaching motion: dx = 20px, dy = 15px in 0.2s -> max_delta = 20px
        # v_norm ~ 10px smoothed / (0.2 * 200) = 0.25 > 0.15
        v, moving = detector.update(x_sh_mid=660.0, y_sh_mid=415.0, w_sh=200.0, dt=0.2)
        assert moving is True
        assert v > 0.15


class TestIssueTracker:
    """Test One-Way Velocity Gate, Debounce Bucket, and Step-Down Floor."""

    def test_normal_accumulation_and_alert(self) -> None:
        tracker = IssueTracker(tau_enter=0.08, tau_exit=0.04)
        dt = 0.2
        curr_t = 0.0

        state = None
        # Feed sustained violation for 15.0 seconds
        for _ in range(75):  # 75 * 0.2s = 15.0s
            curr_t += dt
            state = tracker.update(raw_deviation=0.20, dt=dt, is_moving=False, current_time=curr_t)

        assert state is not None
        assert state.is_violating is True
        assert state.bucket_s == pytest.approx(15.0)
        assert state.alert_active is True

    def test_step_down_floor_on_recovery(self) -> None:
        """CRITICAL VERIFICATION: Step-down floor (d_floor = 9.0s) when recovering post-alert."""
        tracker = IssueTracker(tau_enter=0.08, tau_exit=0.04, d_floor=9.0)
        dt = 0.2
        curr_t = 0.0

        # Trigger alert (15s)
        for _ in range(75):
            curr_t += dt
            tracker.update(raw_deviation=0.20, dt=dt, is_moving=False, current_time=curr_t)
        assert tracker.alert_active is True

        # User sits upright: raw_deviation = 0.0
        # Feed recovery until is_violating drops to False
        recovered = False
        for _ in range(10):  # ~2s
            curr_t += dt
            st = tracker.update(raw_deviation=0.0, dt=dt, is_moving=False, current_time=curr_t)
            if not st.is_violating:
                recovered = True
                # Immediately upon recovery: alert must be False and bucket stepped down to 9.0s!
                assert st.alert_active is False
                assert st.bucket_s == pytest.approx(9.0)
                break
        assert recovered is True

    def test_one_way_velocity_gate_prevents_violation_entry(self) -> None:
        """When moving == True, new violations must NOT enter and bucket must NOT fill."""
        tracker = IssueTracker(tau_enter=0.08, tau_exit=0.04)
        dt = 0.2
        curr_t = 0.0

        # User reaches for cup on desk (moving == True) while body dips
        for _ in range(10):  # 2.0s (< freeze_max_s 3.0s)
            curr_t += dt
            st = tracker.update(raw_deviation=0.30, dt=dt, is_moving=True, current_time=curr_t)
            assert st.is_gate_frozen is True
            assert st.is_violating is False
            assert st.bucket_s == 0.0

    def test_one_way_velocity_gate_never_blocks_recovery(self) -> None:
        """CRITICAL VERIFICATION: When moving == True, recovery (v -> False) is never blocked!"""
        tracker = IssueTracker(tau_enter=0.08, tau_exit=0.04)
        dt = 0.2
        curr_t = 0.0

        st = None
        # Establish violation while stationary (bucket at 5s)
        for _ in range(25):  # 5.0s
            curr_t += dt
            st = tracker.update(raw_deviation=0.20, dt=dt, is_moving=False, current_time=curr_t)
        assert st is not None
        assert st.is_violating is True

        # Now user rapidly straightens body (moving == True, raw_deviation = 0.0)
        # Because it's a one-way gate, recovery to False must NOT be blocked!
        recovered = False
        for _ in range(10):
            curr_t += dt
            st = tracker.update(raw_deviation=0.0, dt=dt, is_moving=True, current_time=curr_t)
            if not st.is_violating:
                recovered = True
                break
        assert recovered is True

    def test_anti_freeze_timeout_releases_after_3_seconds(self) -> None:
        """CRITICAL VERIFICATION: Continuous moving == True for > 3.0s releases gate."""
        tracker = IssueTracker(tau_enter=0.08, tau_exit=0.04, freeze_max_s=3.0)
        dt = 0.2
        curr_t = 0.0

        # Moving for 2.8s (<= 3.0s) -> gate frozen
        for _ in range(14):  # 14 * 0.2s = 2.8s
            curr_t += dt
            st = tracker.update(raw_deviation=0.25, dt=dt, is_moving=True, current_time=curr_t)
            assert st.is_gate_frozen is True

        # Moving past 3.0s (3.2s, 3.4s) -> timeout triggers, gate released!
        curr_t += dt  # 3.0s
        st = tracker.update(raw_deviation=0.25, dt=dt, is_moving=True, current_time=curr_t)

        curr_t += dt  # 3.2s
        st = tracker.update(raw_deviation=0.25, dt=dt, is_moving=True, current_time=curr_t)
        assert st.is_gate_frozen is False
        assert st.is_violating is True


class TestPostureEscalationController:
    """Test full UIIntrusivenessLevel escalation state transitions."""

    def test_escalation_transitions(self) -> None:
        """Test transitions: OK -> WARN -> OVERLAY -> COOLDOWN -> MICROBREAK."""
        controller = PostureEscalationController(cooldown_s=30.0, warn_threshold_s=7.5)
        dt = 0.2
        curr_t = 0.0

        # 1. SILENT_OK: D_i < 7.5s
        ui, _, states = controller.update(
            d_fhp=0.20,
            d_neck=0.0,
            d_slump=0.0,
            d_pitch=0.0,
            d_tilt=0.0,
            dt=dt,
            is_moving=False,
            current_time=curr_t,
        )
        assert ui == UIIntrusivenessLevel.SILENT_OK

        # 2. AMBIENT_WARN: advance to 8.0s (>= 7.5s and < 15.0s)
        for _ in range(39):  # ~8.0s total
            curr_t += dt
            ui, _, states = controller.update(
                d_fhp=0.20,
                d_neck=0.0,
                d_slump=0.0,
                d_pitch=0.0,
                d_tilt=0.0,
                dt=dt,
                is_moving=False,
                current_time=curr_t,
            )
        assert ui == UIIntrusivenessLevel.AMBIENT_WARN

        # 3. ACTIVE_OVERLAY: advance to 15.0s
        for _ in range(36):  # ~15.2s total
            curr_t += dt
            ui, _, states = controller.update(
                d_fhp=0.20,
                d_neck=0.0,
                d_slump=0.0,
                d_pitch=0.0,
                d_tilt=0.0,
                dt=dt,
                is_moving=False,
                current_time=curr_t,
            )
        assert ui == UIIntrusivenessLevel.ACTIVE_OVERLAY

        # Dismiss overlay by sitting upright (5 steps)
        for _ in range(5):
            curr_t += dt
            ui, _, states = controller.update(
                d_fhp=0.0,
                d_neck=0.0,
                d_slump=0.0,
                d_pitch=0.0,
                d_tilt=0.0,
                dt=dt,
                is_moving=False,
                current_time=curr_t,
            )
        assert ui != UIIntrusivenessLevel.ACTIVE_OVERLAY

        # 4. SUPPRESSED_COOLDOWN: user cheats (slumps again immediately within 30s cooldown)
        # Bucket was at 9.0s and decayed slightly, reaches 15.0s in ~40 steps (~8s),
        # where elapsed time since dismiss is only ~9s (< 30s cooldown!)
        for _ in range(40):
            curr_t += dt
            ui, _, states = controller.update(
                d_fhp=0.20,
                d_neck=0.0,
                d_slump=0.0,
                d_pitch=0.0,
                d_tilt=0.0,
                dt=dt,
                is_moving=False,
                current_time=curr_t,
            )
        assert states["fhp"].alert_active is True
        assert ui == UIIntrusivenessLevel.SUPPRESSED_COOLDOWN

        # Dismiss alert #2 by sitting upright
        for _ in range(5):
            curr_t += dt
            controller.update(
                d_fhp=0.0,
                d_neck=0.0,
                d_slump=0.0,
                d_pitch=0.0,
                d_tilt=0.0,
                dt=dt,
                is_moving=False,
                current_time=curr_t,
            )

        # 5. MICROBREAK_PROMPT: 3 alerts in 10 minutes
        # Advance clock past 30s cooldown (e.g. 35s = 175 steps)
        for _ in range(175):
            curr_t += dt
            controller.update(
                d_fhp=0.0,
                d_neck=0.0,
                d_slump=0.0,
                d_pitch=0.0,
                d_tilt=0.0,
                dt=dt,
                is_moving=False,
                current_time=curr_t,
            )

        # Trigger alert #3 (80 steps to account for initial EMA filter ramp-up)
        for _ in range(80):
            curr_t += dt
            ui, _, states = controller.update(
                d_fhp=0.20,
                d_neck=0.0,
                d_slump=0.0,
                d_pitch=0.0,
                d_tilt=0.0,
                dt=dt,
                is_moving=False,
                current_time=curr_t,
            )
        # Now 3 alerts in rolling 10-minute window -> escalates to MICROBREAK_PROMPT!
        assert states["fhp"].alert_count_10m >= 3
        assert ui == UIIntrusivenessLevel.MICROBREAK_PROMPT
