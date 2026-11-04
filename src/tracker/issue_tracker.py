"""IssueTracker and UI Escalation Controller for Posture Guardian.

Reference: docs/specs/02-signal-and-tracker.md & docs/ARCHITECTURE.md
Implements:
1. Per-metric IssueTracker with hysteresis (tau_enter, tau_exit).
2. One-Way Velocity Gate with freeze_max_s = 3.0s anti-freeze timeout.
3. 15.0s Debounce Bucket with d_floor = 9.0s Step-Down on recovery.
4. Dual-timescale UI Escalation Controller:
   - Cooldown 30.0s (SUPPRESSED_COOLDOWN)
   - 10-minute 3x alert escalation (MICROBREAK_PROMPT)
   - 15-minute exposure duty cycle (> 50%) escalation (MICROBREAK_PROMPT)
   - AMBIENT_WARN at max(D_i) >= 7.5s, SILENT_OK at < 7.5s
"""

from collections import deque
from dataclasses import dataclass

from tracker.filter import AsymmetricEMA, SlidingMedian3
from tracker.states import PostureViolationFlags, UIIntrusivenessLevel


@dataclass(frozen=True, slots=True)
class IssueTrackerState:
    """Snapshot of a single metric's IssueTracker state."""

    filtered_deviation: float
    is_violating: bool
    bucket_s: float
    alert_active: bool
    is_gate_frozen: bool
    alert_count_10m: int
    duty_cycle_15m: float


class IssueTracker:
    """Single-metric posture issue tracker with One-Way Velocity Gate and Step-Down Floor."""

    def __init__(
        self,
        tau_enter: float,
        tau_exit: float,
        freeze_max_s: float = 3.0,
        d_floor: float = 9.0,
        bucket_max_s: float = 15.0,
        decay_rate: float = 1.5,
        window_10m_s: float = 600.0,
        window_15m_s: float = 900.0,
        min_duty_eval_window_s: float = 300.0,
    ) -> None:
        if tau_exit >= tau_enter:
            raise ValueError(f"tau_exit ({tau_exit}) must be less than tau_enter ({tau_enter})")

        self.tau_enter = tau_enter
        self.tau_exit = tau_exit
        self.freeze_max_s = freeze_max_s
        self.d_floor = d_floor
        self.bucket_max_s = bucket_max_s
        self.decay_rate = decay_rate
        self.window_10m_s = window_10m_s
        self.window_15m_s = window_15m_s
        self.min_duty_eval_window_s = min_duty_eval_window_s

        # Filters
        self.median_filter = SlidingMedian3()
        self.ema_filter = AsymmetricEMA(tau_attack=1.0, tau_release=0.3)

        # Internal state
        self.bucket_s: float = 0.0
        self.is_violating: bool = False
        self.alert_active: bool = False
        self.freeze_elapsed_s: float = 0.0
        self.last_alert_dismissed_time: float = -99999.0

        # Rolling history buffers:
        # alert_history stores timestamps of alert activations
        self.alert_history: deque[float] = deque()
        # samples_history stores (timestamp, dt, is_violating)
        self.samples_history: deque[tuple[float, float, bool]] = deque()

    def reset(self) -> None:
        """Reset all tracker state and filters."""
        self.median_filter.reset()
        self.ema_filter.reset()
        self.bucket_s = 0.0
        self.is_violating = False
        self.alert_active = False
        self.freeze_elapsed_s = 0.0
        self.last_alert_dismissed_time = -99999.0
        self.alert_history.clear()
        self.samples_history.clear()

    def update(
        self,
        raw_deviation: float,
        dt: float,
        is_moving: bool,
        current_time: float,
    ) -> IssueTrackerState:
        """Update tracker with new raw deviation sample.

        Returns:
            IssueTrackerState snapshot
        """
        dt_safe = max(1e-4, dt)

        # 1. Filter pipeline: Median-3 followed by Asymmetric EMA
        d_med = self.median_filter.update(raw_deviation)
        d_hat = self.ema_filter.update(d_med, dt_safe)

        # 2. One-Way Velocity Gate with anti-freeze timeout
        is_gate_frozen = False
        if is_moving:
            self.freeze_elapsed_s += dt_safe
            is_gate_frozen = self.freeze_elapsed_s <= self.freeze_max_s
        else:
            self.freeze_elapsed_s = 0.0
            is_gate_frozen = False

        # 3. One-Way Rule:
        # When gate is frozen, VIOLATIONS CANNOT ENTER (is_violating cannot become True),
        # but RECOVERY CAN NEVER BE BLOCKED (if d_hat <= tau_exit, is_violating drops to False).
        if is_gate_frozen:
            if d_hat <= self.tau_exit:
                self.is_violating = False
            # If d_hat > tau_enter, do not transition to True (stays False / frozen)
        else:
            if d_hat > self.tau_enter:
                self.is_violating = True
            elif d_hat <= self.tau_exit:
                self.is_violating = False

        # 4. Debounce Bucket & Step-Down Floor
        if self.is_violating and not is_gate_frozen:
            # Accumulate violation time (with epsilon tolerance for floating-point 0.2s steps)
            self.bucket_s = min(self.bucket_max_s, self.bucket_s + dt_safe)
            if self.bucket_s >= (self.bucket_max_s - 1e-5):
                self.bucket_s = self.bucket_max_s
                if not self.alert_active:
                    self.alert_active = True
                    self.alert_history.append(current_time)
        else:
            # Recovery or decaying
            if self.alert_active and not self.is_violating:
                # Recovery! Step-down floor logic
                self.alert_active = False
                self.last_alert_dismissed_time = current_time
                self.bucket_s = self.d_floor
            else:
                # Normal decay
                decay_amt = self.decay_rate * dt_safe
                self.bucket_s = max(0.0, self.bucket_s - decay_amt)

        # 5. Maintain rolling history for 10-minute and 15-minute metrics
        self._prune_history(current_time)
        self.samples_history.append((current_time, dt_safe, self.is_violating))

        alert_count_10m = len(self.alert_history)
        duty_cycle_15m = self._calculate_duty_cycle_15m()

        return IssueTrackerState(
            filtered_deviation=d_hat,
            is_violating=self.is_violating,
            bucket_s=self.bucket_s,
            alert_active=self.alert_active,
            is_gate_frozen=is_gate_frozen,
            alert_count_10m=alert_count_10m,
            duty_cycle_15m=duty_cycle_15m,
        )

    def _prune_history(self, current_time: float) -> None:
        """Prune entries older than sliding windows."""
        # Prune 10m alert history
        cutoff_10m = current_time - self.window_10m_s
        while self.alert_history and self.alert_history[0] < cutoff_10m:
            self.alert_history.popleft()

        # Prune 15m samples history
        cutoff_15m = current_time - self.window_15m_s
        while self.samples_history and self.samples_history[0][0] < cutoff_15m:
            self.samples_history.popleft()

    def _calculate_duty_cycle_15m(self) -> float:
        """Calculate violation proportion over 15-minute rolling window.

        Enforces min_duty_eval_window_s (e.g. 300s) to prevent single initial transient
        violations from skewing the long-term static exposure duty cycle.
        """
        if not self.samples_history:
            return 0.0

        total_dt = 0.0
        violating_dt = 0.0
        for _, dt, is_v in self.samples_history:
            total_dt += dt
            if is_v:
                violating_dt += dt

        effective_base = max(self.min_duty_eval_window_s, total_dt)
        return (violating_dt / effective_base) if effective_base > 0.0 else 0.0


class PostureEscalationController:
    """Coordinates 5 IssueTracker instances and determines UIIntrusivenessLevel."""

    def __init__(
        self,
        cooldown_s: float = 30.0,
        warn_threshold_s: float = 7.5,
        duty_microbreak_threshold: float = 0.50,
        alerts_microbreak_count: int = 3,
    ) -> None:
        self.cooldown_s = cooldown_s
        self.warn_threshold_s = warn_threshold_s
        self.duty_microbreak_threshold = duty_microbreak_threshold
        self.alerts_microbreak_count = alerts_microbreak_count

        # Default hysteresis thresholds per metric
        self.trackers: dict[str, IssueTracker] = {
            "fhp": IssueTracker(tau_enter=0.08, tau_exit=0.04),
            "neck": IssueTracker(tau_enter=0.08, tau_exit=0.04),
            "slump": IssueTracker(tau_enter=0.06, tau_exit=0.03),
            "pitch": IssueTracker(tau_enter=12.0, tau_exit=6.0),
            "tilt": IssueTracker(tau_enter=8.0, tau_exit=4.0),
        }

    def reset(self) -> None:
        """Reset all trackers."""
        for tracker in self.trackers.values():
            tracker.reset()

    def update(
        self,
        *,
        d_fhp: float,
        d_neck: float,
        d_slump: float,
        d_pitch: float,
        d_tilt: float,
        dt: float,
        is_moving: bool,
        current_time: float,
    ) -> tuple[UIIntrusivenessLevel, PostureViolationFlags, dict[str, IssueTrackerState]]:
        """Update all 5 trackers and evaluate session UIIntrusivenessLevel.

        Returns:
            (ui_level, violation_flags, tracker_states)
        """
        states: dict[str, IssueTrackerState] = {
            "fhp": self.trackers["fhp"].update(d_fhp, dt, is_moving, current_time),
            "neck": self.trackers["neck"].update(d_neck, dt, is_moving, current_time),
            "slump": self.trackers["slump"].update(d_slump, dt, is_moving, current_time),
            "pitch": self.trackers["pitch"].update(d_pitch, dt, is_moving, current_time),
            "tilt": self.trackers["tilt"].update(d_tilt, dt, is_moving, current_time),
        }

        flags = PostureViolationFlags(
            v_fhp=states["fhp"].is_violating,
            v_neck_comp=states["neck"].is_violating,
            v_slump=states["slump"].is_violating,
            v_gaze_pitch=states["pitch"].is_violating,
            v_tilt=states["tilt"].is_violating,
        )

        ui_level = self._evaluate_ui_level(states, current_time)
        return ui_level, flags, states

    def _evaluate_ui_level(
        self,
        states: dict[str, IssueTrackerState],
        current_time: float,
    ) -> UIIntrusivenessLevel:
        """Determine UI level based on alerts, cooldowns, and microbreak conditions."""
        # 1. Micro-break Escalation:
        # Check if any issue triggered >= 3 alerts in 10m window,
        # or combined 15m duty cycle exceeds threshold
        max_10m_alerts = max(state.alert_count_10m for state in states.values())
        max_15m_duty = max(state.duty_cycle_15m for state in states.values())

        if (
            max_10m_alerts >= self.alerts_microbreak_count
            or max_15m_duty > self.duty_microbreak_threshold
        ):
            return UIIntrusivenessLevel.MICROBREAK_PROMPT

        # 2. Alert & Cooldown Evaluation
        active_alert_issues = [name for name, st in states.items() if st.alert_active]

        if active_alert_issues:
            # Check if all active alert issues are currently suppressed by cooldown
            all_suppressed = True
            for name in active_alert_issues:
                tracker = self.trackers[name]
                time_since_dismiss = current_time - tracker.last_alert_dismissed_time
                if time_since_dismiss >= self.cooldown_s:
                    # At least one active alert is NOT in cooldown -> show overlay!
                    all_suppressed = False
                    break

            if all_suppressed:
                return UIIntrusivenessLevel.SUPPRESSED_COOLDOWN
            return UIIntrusivenessLevel.ACTIVE_OVERLAY

        # 3. Ambient Warning vs Silent OK
        max_bucket = max(st.bucket_s for st in states.values())
        if max_bucket >= self.warn_threshold_s:
            return UIIntrusivenessLevel.AMBIENT_WARN

        return UIIntrusivenessLevel.SILENT_OK
