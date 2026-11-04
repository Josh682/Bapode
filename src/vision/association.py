"""Subject association and 2x2 Visibility State evaluation.

Reference: docs/specs/01-vision-and-metrics.md & docs/ARCHITECTURE.md
Implements:
- Multi-person face association and pose-nose verification.
- 2x2 VisibilityState matrix (Face x Shoulders).
- Extreme close proximity detection when shoulders are occluded.
"""

from tracker.states import VisibilityState
from vision.landmarks import FaceLandmarkData, PoseLandmarkData


def associate_primary_subject(
    candidate_faces: list[FaceLandmarkData],
    pose: PoseLandmarkData | None,
    x_calib_center: float,
) -> FaceLandmarkData | None:
    """Select primary user's face and verify spatial consistency with pose landmarks.

    Rules:
    1. Select candidate face with largest area closest to calibration horizontal center.
    2. If pose is detected, verify Pose nose landmark is inside chosen face bbox
       AND Euclidean distance from nose to face center is < 0.25 * W_sh.
    3. If verification fails, return None to prevent cross-person contamination.
    """
    if not candidate_faces:
        return None

    # Score candidates: prioritize area while penalizing horizontal distance from calibration center
    def score_candidate(face: FaceLandmarkData) -> float:
        dx = abs(face.bbox.center.x - x_calib_center)
        # Bounding box area divided by (1 + dx / 100)
        return face.bbox.area / (1.0 + dx / 100.0)

    best_face = max(candidate_faces, key=score_candidate)

    if pose is not None:
        w_sh = pose.shoulder_width
        # Avoid division or invalid geometry if shoulder width is degenerate
        if w_sh <= 1e-4:
            return None

        # Verification 1: Pose nose must be inside face bounding box
        if not best_face.bbox.contains(pose.nose):
            return None

        # Verification 2: Euclidean distance from nose to face center < 0.25 * W_sh
        dist = pose.nose.distance_to(best_face.bbox.center)
        if dist >= 0.25 * w_sh:
            return None

    return best_face


class VisibilityEvaluator:
    """Stateful evaluator for 2x2 Visibility Matrix and Extreme Close Proximity tracking."""

    def __init__(
        self,
        close_proximity_threshold: float = 1.30,
        close_proximity_time_s: float = 15.0,
    ) -> None:
        self.close_proximity_threshold = close_proximity_threshold
        self.close_proximity_time_s = close_proximity_time_s
        self.close_proximity_elapsed_s: float = 0.0
        self.is_extreme_close_proximity: bool = False

    def reset(self) -> None:
        """Reset proximity tracking state."""
        self.close_proximity_elapsed_s = 0.0
        self.is_extreme_close_proximity = False

    def evaluate(
        self,
        *,
        has_face: bool,
        has_shoulders: bool,
        w_eye_ratio: float | None = None,
        dt: float = 0.2,
    ) -> tuple[VisibilityState, bool]:
        """Evaluate 2x2 visibility state and return (visibility_state, is_extreme_close_proximity).

        Matrix:
        - Face=True,  Shoulders=True  -> MEASURABLE
        - Face=True,  Shoulders=False -> OCCLUDED_LOWER (with close proximity monitoring)
        - Face=False, Shoulders=True  -> OCCLUDED_UPPER
        - Face=False, Shoulders=False -> ABSENT
        """
        if has_face and has_shoulders:
            self.reset()
            return VisibilityState.MEASURABLE, False

        if has_face and not has_shoulders:
            # Check close proximity ratio: W_eye_corr / W_eye_base > 1.30
            if w_eye_ratio is not None and w_eye_ratio > self.close_proximity_threshold:
                self.close_proximity_elapsed_s += dt
                if self.close_proximity_elapsed_s >= (self.close_proximity_time_s - 1e-5):
                    self.is_extreme_close_proximity = True
            else:
                self.close_proximity_elapsed_s = max(0.0, self.close_proximity_elapsed_s - dt)
                self.is_extreme_close_proximity = False
            return VisibilityState.OCCLUDED_LOWER, self.is_extreme_close_proximity

        if not has_face and has_shoulders:
            self.reset()
            return VisibilityState.OCCLUDED_UPPER, False

        # not has_face and not has_shoulders
        self.reset()
        return VisibilityState.ABSENT, False
