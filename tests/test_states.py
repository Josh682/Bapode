"""Unit tests for 3-Axis Orthogonal State Architecture."""

from tracker.states import (
    IntervalSignature,
    OperatingMode,
    PostureViolationFlags,
    UIIntrusivenessLevel,
    VisibilityState,
)


class TestOperatingMode:
    """Test Axis 1: OperatingMode."""

    def test_all_enum_values(self) -> None:
        expected = {
            "UNINITIALIZED",
            "CALIBRATING",
            "ACTIVE_MONITORING",
            "PAUSED_MANUAL",
            "PAUSED_MEETING_AUTO",
            "NEEDS_RECALIBRATION",
        }
        actual = {mode.value for mode in OperatingMode}
        assert actual == expected

    def test_properties(self) -> None:
        assert OperatingMode.ACTIVE_MONITORING.is_active is True
        assert OperatingMode.PAUSED_MANUAL.is_active is False

        assert OperatingMode.PAUSED_MANUAL.is_paused is True
        assert OperatingMode.PAUSED_MEETING_AUTO.is_paused is True
        assert OperatingMode.ACTIVE_MONITORING.is_paused is False
        assert OperatingMode.UNINITIALIZED.is_paused is False


class TestVisibilityState:
    """Test Axis 2: VisibilityState."""

    def test_all_enum_values(self) -> None:
        expected = {
            "MEASURABLE",
            "OCCLUDED_LOWER",
            "OCCLUDED_UPPER",
            "ABSENT",
            "PAUSED",
            "NEEDS_RECALIBRATION",
        }
        actual = {state.value for state in VisibilityState}
        assert actual == expected

    def test_properties(self) -> None:
        assert VisibilityState.MEASURABLE.is_measurable is True
        assert VisibilityState.OCCLUDED_LOWER.is_measurable is False
        assert VisibilityState.OCCLUDED_UPPER.is_measurable is False
        assert VisibilityState.ABSENT.is_measurable is False

        # Presence
        assert VisibilityState.MEASURABLE.is_user_present is True
        assert VisibilityState.OCCLUDED_LOWER.is_user_present is True
        assert VisibilityState.OCCLUDED_UPPER.is_user_present is True
        assert VisibilityState.ABSENT.is_user_present is False
        assert VisibilityState.PAUSED.is_user_present is False

        # Face landmark visibility
        assert VisibilityState.MEASURABLE.has_face is True
        assert VisibilityState.OCCLUDED_LOWER.has_face is True
        assert VisibilityState.OCCLUDED_UPPER.has_face is False
        assert VisibilityState.ABSENT.has_face is False

        # Shoulder landmark visibility
        assert VisibilityState.MEASURABLE.has_shoulders is True
        assert VisibilityState.OCCLUDED_UPPER.has_shoulders is True
        assert VisibilityState.OCCLUDED_LOWER.has_shoulders is False
        assert VisibilityState.ABSENT.has_shoulders is False


class TestUIIntrusivenessLevel:
    """Test Axis 3: UIIntrusivenessLevel."""

    def test_all_enum_values(self) -> None:
        expected = {
            "SILENT_OK",
            "AMBIENT_WARN",
            "ACTIVE_OVERLAY",
            "SUPPRESSED_COOLDOWN",
            "MICROBREAK_PROMPT",
        }
        actual = {lvl.value for lvl in UIIntrusivenessLevel}
        assert actual == expected

    def test_visual_overlay_active(self) -> None:
        assert UIIntrusivenessLevel.SILENT_OK.is_visual_overlay_active is False
        assert UIIntrusivenessLevel.AMBIENT_WARN.is_visual_overlay_active is False
        assert UIIntrusivenessLevel.SUPPRESSED_COOLDOWN.is_visual_overlay_active is False
        assert UIIntrusivenessLevel.ACTIVE_OVERLAY.is_visual_overlay_active is True
        assert UIIntrusivenessLevel.MICROBREAK_PROMPT.is_visual_overlay_active is True


class TestPostureViolationFlags:
    """Test binary posture violation flags."""

    def test_defaults_clean(self) -> None:
        flags = PostureViolationFlags()
        assert flags.has_any_violation is False
        assert flags.as_db_tuple() == (0, 0, 0, 0, 0)

    def test_single_violation(self) -> None:
        flags = PostureViolationFlags(v_fhp=True)
        assert flags.has_any_violation is True
        assert flags.as_db_tuple() == (1, 0, 0, 0, 0)

    def test_multiple_violations(self) -> None:
        flags = PostureViolationFlags(v_neck_comp=True, v_tilt=True)
        assert flags.has_any_violation is True
        assert flags.as_db_tuple() == (0, 1, 0, 0, 1)


class TestIntervalSignature:
    """Test RLE IntervalSignature."""

    def test_signature_key(self) -> None:
        sig = IntervalSignature(
            visibility_state=VisibilityState.MEASURABLE,
            ui_level=UIIntrusivenessLevel.AMBIENT_WARN,
            violations=PostureViolationFlags(v_slump=True),
        )
        assert sig.to_rle_key() == ("MEASURABLE", "AMBIENT_WARN", 0, 0, 1, 0, 0)
