"""Orthogonal 3-Axis State Architecture for Posture Guardian.

Reference: docs/ARCHITECTURE.md
Decomposes system conditions into 3 independent orthogonal axes:
1. OperatingMode: Session & drift control.
2. VisibilityState: 2x2 Face x Shoulder visibility matrix + pause/drift interval states.
3. UIIntrusivenessLevel: Anti-nagging alert escalation levels.
"""

from dataclasses import dataclass
from enum import StrEnum


class OperatingMode(StrEnum):
    """Axis 1: Operating mode controlling session flow, user intent, and drift."""

    UNINITIALIZED = "UNINITIALIZED"
    CALIBRATING = "CALIBRATING"
    ACTIVE_MONITORING = "ACTIVE_MONITORING"
    PAUSED_MANUAL = "PAUSED_MANUAL"
    PAUSED_MEETING_AUTO = "PAUSED_MEETING_AUTO"
    NEEDS_RECALIBRATION = "NEEDS_RECALIBRATION"

    @property
    def is_paused(self) -> bool:
        """Return True if session is paused either manually or automatically."""
        return self in (
            OperatingMode.PAUSED_MANUAL,
            OperatingMode.PAUSED_MEETING_AUTO,
        )

    @property
    def is_active(self) -> bool:
        """Return True if active monitoring is actively underway."""
        return self == OperatingMode.ACTIVE_MONITORING


class VisibilityState(StrEnum):
    """Axis 2: 2x2 Face x Shoulder presence matrix and interval states."""

    MEASURABLE = "MEASURABLE"
    OCCLUDED_LOWER = "OCCLUDED_LOWER"
    OCCLUDED_UPPER = "OCCLUDED_UPPER"
    ABSENT = "ABSENT"
    PAUSED = "PAUSED"
    NEEDS_RECALIBRATION = "NEEDS_RECALIBRATION"

    @property
    def is_measurable(self) -> bool:
        """Return True when both face and shoulders are detected."""
        return self == VisibilityState.MEASURABLE

    @property
    def is_user_present(self) -> bool:
        """Return True if user is present in front of the camera (even if partially occluded)."""
        return self in (
            VisibilityState.MEASURABLE,
            VisibilityState.OCCLUDED_LOWER,
            VisibilityState.OCCLUDED_UPPER,
        )

    @property
    def has_face(self) -> bool:
        """Return True if face landmarks are visible."""
        return self in (
            VisibilityState.MEASURABLE,
            VisibilityState.OCCLUDED_LOWER,
        )

    @property
    def has_shoulders(self) -> bool:
        """Return True if shoulder landmarks are visible."""
        return self in (
            VisibilityState.MEASURABLE,
            VisibilityState.OCCLUDED_UPPER,
        )


class UIIntrusivenessLevel(StrEnum):
    """Axis 3: Escalation and anti-nagging notification level."""

    SILENT_OK = "SILENT_OK"
    AMBIENT_WARN = "AMBIENT_WARN"
    ACTIVE_OVERLAY = "ACTIVE_OVERLAY"
    SUPPRESSED_COOLDOWN = "SUPPRESSED_COOLDOWN"
    MICROBREAK_PROMPT = "MICROBREAK_PROMPT"

    @property
    def is_visual_overlay_active(self) -> bool:
        """Return True if screen overlay should be visible."""
        return self in (
            UIIntrusivenessLevel.ACTIVE_OVERLAY,
            UIIntrusivenessLevel.MICROBREAK_PROMPT,
        )


@dataclass(frozen=True, slots=True)
class PostureViolationFlags:
    """Binary violation flags for the 5 geometric posture metrics."""

    v_fhp: bool = False
    v_neck_comp: bool = False
    v_slump: bool = False
    v_gaze_pitch: bool = False
    v_tilt: bool = False

    @property
    def has_any_violation(self) -> bool:
        """Return True if any of the 5 metrics is currently violating baseline."""
        return self.v_fhp or self.v_neck_comp or self.v_slump or self.v_gaze_pitch or self.v_tilt

    def as_db_tuple(self) -> tuple[int, int, int, int, int]:
        """Convert flags to binary integers (0 or 1) for database insertion."""
        return (
            int(self.v_fhp),
            int(self.v_neck_comp),
            int(self.v_slump),
            int(self.v_gaze_pitch),
            int(self.v_tilt),
        )


@dataclass(frozen=True, slots=True)
class IntervalSignature:
    """Signature representing an invariant state block for Run-Length Encoding (RLE)."""

    visibility_state: VisibilityState
    ui_level: UIIntrusivenessLevel
    violations: PostureViolationFlags

    def to_rle_key(self) -> tuple[str, str, int, int, int, int, int]:
        """Return hashable key representation for interval change detection."""
        return (
            self.visibility_state.value,
            self.ui_level.value,
            *self.violations.as_db_tuple(),
        )
