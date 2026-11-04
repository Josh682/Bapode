"""Pure Python/NumPy landmark representations for Pose and Face.

Decoupled from camera hardware, OpenCV, and MediaPipe.
"""

import math
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Point2D:
    """2D Cartesian point in image space."""

    x: float
    y: float

    def distance_to(self, other: "Point2D") -> float:
        """Compute Euclidean distance to another point."""
        return math.hypot(self.x - other.x, self.y - other.y)


@dataclass(frozen=True, slots=True)
class BoundingBox:
    """2D Bounding Box in image space."""

    x_min: float
    y_min: float
    x_max: float
    y_max: float

    @property
    def width(self) -> float:
        """Width of bounding box."""
        return max(0.0, self.x_max - self.x_min)

    @property
    def height(self) -> float:
        """Height of bounding box."""
        return max(0.0, self.y_max - self.y_min)

    @property
    def area(self) -> float:
        """Area of bounding box."""
        return self.width * self.height

    @property
    def center(self) -> Point2D:
        """Center coordinate of bounding box."""
        return Point2D(
            x=(self.x_min + self.x_max) / 2.0,
            y=(self.y_min + self.y_max) / 2.0,
        )

    def contains(self, point: Point2D) -> bool:
        """Check if point is inside bounding box."""
        return (
            self.x_min <= point.x <= self.x_max
            and self.y_min <= point.y <= self.y_max
        )


@dataclass(frozen=True, slots=True)
class FaceLandmarkData:
    """Face landmarks and 3D head pose orientation."""

    bbox: BoundingBox
    pitch_deg: float
    yaw_deg: float
    roll_deg: float
    eye_outer_left: Point2D
    eye_outer_right: Point2D

    @property
    def w_eye(self) -> float:
        """Euclidean biocular distance between outer eye corners."""
        return self.eye_outer_left.distance_to(self.eye_outer_right)


@dataclass(frozen=True, slots=True)
class PoseLandmarkData:
    """Pose landmarks for upper body posture analysis."""

    left_shoulder: Point2D
    right_shoulder: Point2D
    left_ear: Point2D
    right_ear: Point2D
    nose: Point2D

    @property
    def shoulder_midpoint(self) -> Point2D:
        """Midpoint between left and right shoulders."""
        return Point2D(
            x=(self.left_shoulder.x + self.right_shoulder.x) / 2.0,
            y=(self.left_shoulder.y + self.right_shoulder.y) / 2.0,
        )

    @property
    def ear_midpoint(self) -> Point2D:
        """Midpoint between left and right ears."""
        return Point2D(
            x=(self.left_ear.x + self.right_ear.x) / 2.0,
            y=(self.left_ear.y + self.right_ear.y) / 2.0,
        )

    @property
    def shoulder_width(self) -> float:
        """Euclidean distance between shoulders."""
        return self.left_shoulder.distance_to(self.right_shoulder)
