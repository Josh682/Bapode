"""Signal pre-processing filters aware of dynamic time steps (dt).

Reference: docs/specs/02-signal-and-tracker.md
Implements:
1. SlidingMedian3: 3-sample median filter to reject 1-sample impulse noise.
2. AsymmetricEMA: dt-aware EMA with tau_attack=1.0s and tau_release=0.3s.
3. NormalizedVelocityDetector: Shoulder-normalized velocity detector for quasi-static motion gating.
"""

import math
from collections import deque

from config import load_config


class SlidingMedian3:
    """Sliding median filter over a 3-sample window."""

    def __init__(self) -> None:
        self._window: deque[float] = deque(maxlen=3)

    def reset(self) -> None:
        """Clear filter state."""
        self._window.clear()

    def update(self, value: float) -> float:
        """Push a new value and return the median of current window."""
        self._window.append(value)
        sorted_vals = sorted(self._window)
        n = len(sorted_vals)
        if n == 1:
            return sorted_vals[0]
        if n == 2:
            return (sorted_vals[0] + sorted_vals[1]) / 2.0
        return sorted_vals[1]


class AsymmetricEMA:
    """Asymmetric exponential moving average with dt-aware time constants.

    tau_attack = 1.0s (smoothes short spikes when starting to violate)
    tau_release = 0.3s (fast decay when upright recovery occurs)
    """

    def __init__(self, tau_attack: float = 1.0, tau_release: float = 0.3) -> None:
        self.tau_attack = tau_attack
        self.tau_release = tau_release
        self.value: float | None = None

    def reset(self, initial_value: float | None = None) -> None:
        """Reset filter value."""
        self.value = initial_value

    def update(self, input_val: float, dt: float) -> float:
        """Update filter with input value and delta time dt."""
        if self.value is None:
            self.value = input_val
            return self.value

        dt_safe = max(1e-4, dt)
        tau = self.tau_attack if input_val >= self.value else self.tau_release
        alpha = 1.0 - math.exp(-dt_safe / tau)
        self.value = alpha * input_val + (1.0 - alpha) * self.value
        return self.value


class NormalizedVelocityDetector:
    """Quasi-static motion detector normalized to current shoulder width.

    Formula:
        v_norm(t_k) = (1 / (dt_k * W_sh(t_k))) * max(|dY|, |dX|, |dW|)
        moving = v_norm > v_thresh (default 0.15 shoulder-widths / s)
    """

    def __init__(
        self,
        v_thresh: float | None = None,
        smoothing_alpha: float = 0.5,
    ) -> None:
        cfg = load_config()
        self.v_thresh = v_thresh if v_thresh is not None else cfg.motion.v_thresh
        self.smoothing_alpha = smoothing_alpha

        # Smoothed previous shoulder geometry
        self._prev_x: float | None = None
        self._prev_y: float | None = None
        self._prev_w: float | None = None

    def reset(self) -> None:
        """Clear motion detector state."""
        self._prev_x = None
        self._prev_y = None
        self._prev_w = None

    def update(
        self,
        x_sh_mid: float,
        y_sh_mid: float,
        w_sh: float,
        dt: float,
    ) -> tuple[float, bool]:
        """Compute normalized velocity and determine moving status.

        Returns:
            (v_norm, is_moving)
        """
        # Smooth incoming observations
        if self._prev_x is None or self._prev_y is None or self._prev_w is None:
            self._prev_x = x_sh_mid
            self._prev_y = y_sh_mid
            self._prev_w = w_sh
            return 0.0, False

        dt_safe = max(1e-4, dt)
        w_safe = max(1.0, w_sh)

        # Smooth current coordinates
        curr_x = self.smoothing_alpha * x_sh_mid + (1.0 - self.smoothing_alpha) * self._prev_x
        curr_y = self.smoothing_alpha * y_sh_mid + (1.0 - self.smoothing_alpha) * self._prev_y
        curr_w = self.smoothing_alpha * w_sh + (1.0 - self.smoothing_alpha) * self._prev_w

        dx = abs(curr_x - self._prev_x)
        dy = abs(curr_y - self._prev_y)
        dw = abs(curr_w - self._prev_w)

        max_delta = max(dx, dy, dw)
        v_norm = max_delta / (dt_safe * w_safe)

        # Update historical coordinates
        self._prev_x = curr_x
        self._prev_y = curr_y
        self._prev_w = curr_w

        is_moving = v_norm > self.v_thresh
        return v_norm, is_moving
