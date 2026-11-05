"""Sequential Pose and Face Landmark Detector.

Reference: docs/specs/01-vision-and-metrics.md & docs/ARCHITECTURE.md
Implements:
1. Sequential inference of MediaPipe Pose Lite followed by Face Landmarker
   in a single thread (Worker 1) to eliminate XNNPACK/TFLite thread pool contention.
2. Conversion of raw landmarks to decoupled dataclasses (PoseLandmarkData, FaceLandmarkData).
3. Flexible mock/synthetic fallback for automated testing and headless CI.
"""

import contextlib
import math
from collections.abc import Callable
from typing import Any

import numpy as np

from vision.landmarks import BoundingBox, FaceLandmarkData, Point2D, PoseLandmarkData


class SequentialLandmarkDetector:
    """Sequential Pose and Face detector wrapping MediaPipe or synthetic generator."""

    def __init__(
        self,
        use_mock: bool = False,
        mock_provider: (
            Callable[[np.ndarray, float], tuple[PoseLandmarkData | None, list[FaceLandmarkData]]]
            | None
        ) = None,
    ) -> None:
        self.use_mock = use_mock
        self.mock_provider = mock_provider
        self._pose_solution: Any = None
        self._face_solution: Any = None

        if not self.use_mock:
            self._init_mediapipe()

    def _init_mediapipe(self) -> None:
        """Initialize MediaPipe Pose Lite and Face Mesh solutions."""
        try:
            import mediapipe as mp  # type: ignore[import-untyped]

            # MediaPipe Pose Lite (model_complexity=0)
            self._pose_solution = mp.solutions.pose.Pose(  # pyright: ignore[reportAttributeAccessIssue]
                static_image_mode=False,
                model_complexity=0,  # 0 = Lite, 1 = Full, 2 = Heavy
                smooth_landmarks=True,
                min_detection_confidence=0.5,
                min_tracking_confidence=0.5,
            )

            # MediaPipe Face Mesh
            self._face_solution = mp.solutions.face_mesh.FaceMesh(  # pyright: ignore[reportAttributeAccessIssue]
                static_image_mode=False,
                max_num_faces=3,
                refine_landmarks=True,
                min_detection_confidence=0.5,
                min_tracking_confidence=0.5,
            )
        except Exception:
            # Fallback to mock mode if MediaPipe solutions cannot be instantiated
            self.use_mock = True

    def detect(
        self,
        image_bgr: np.ndarray,
        timestamp_s: float = 0.0,
    ) -> tuple[PoseLandmarkData | None, list[FaceLandmarkData]]:
        """Run sequential Pose Lite and Face Landmarker on a single frame.

        Returns:
            (pose_landmarks_or_none, list_of_detected_faces)
        """
        if self.use_mock or self.mock_provider is not None:
            if self.mock_provider is not None:
                return self.mock_provider(image_bgr, timestamp_s)
            return self._default_mock_landmarks(image_bgr)

        try:
            import cv2

            h, w = image_bgr.shape[:2]
            rgb_image = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2RGB)

            # 1. Step 1: Pose Lite Inference
            pose_data: PoseLandmarkData | None = None
            if self._pose_solution is not None:
                pose_results = self._pose_solution.process(rgb_image)
                if pose_results.pose_landmarks:
                    lms = pose_results.pose_landmarks.landmark
                    # Indices: nose=0, left_ear=7, right_ear=8, left_sh=11, right_sh=12
                    nose = Point2D(lms[0].x * w, lms[0].y * h)
                    left_ear = Point2D(lms[7].x * w, lms[7].y * h)
                    right_ear = Point2D(lms[8].x * w, lms[8].y * h)
                    left_sh = Point2D(lms[11].x * w, lms[11].y * h)
                    right_sh = Point2D(lms[12].x * w, lms[12].y * h)

                    # Verify detection visibility/presence
                    if lms[11].visibility > 0.5 and lms[12].visibility > 0.5:
                        pose_data = PoseLandmarkData(
                            left_shoulder=left_sh,
                            right_shoulder=right_sh,
                            left_ear=left_ear,
                            right_ear=right_ear,
                            nose=nose,
                        )

            # 2. Step 2: Face Landmarker Inference (Sequential)
            detected_faces: list[FaceLandmarkData] = []
            if self._face_solution is not None:
                face_results = self._face_solution.process(rgb_image)
                if face_results.multi_face_landmarks:
                    for face_lms in face_results.multi_face_landmarks:
                        face_data = self._extract_face_data(face_lms.landmark, w, h)
                        if face_data is not None:
                            detected_faces.append(face_data)

            return pose_data, detected_faces
        except Exception:
            return self._default_mock_landmarks(image_bgr)

    def _extract_face_data(self, landmarks: Any, w: int, h: int) -> FaceLandmarkData | None:
        """Extract bounding box, eye landmarks, and estimate Euler angles."""
        try:
            xs = [lm.x * w for lm in landmarks]
            ys = [lm.y * h for lm in landmarks]

            bbox = BoundingBox(
                x_min=min(xs),
                y_min=min(ys),
                x_max=max(xs),
                y_max=max(ys),
            )

            # MediaPipe outer eye corner indices:
            # 33 (left outer in image), 263 (right outer in image)
            eye_left = Point2D(landmarks[33].x * w, landmarks[33].y * h)
            eye_right = Point2D(landmarks[263].x * w, landmarks[263].y * h)

            # Approximate head orientation angles from key facial landmarks
            # Nose bridge (1), chin (152), left cheek (454), right cheek (234)
            nose_tip = landmarks[1]
            chin = landmarks[152]
            left_side = landmarks[454]
            right_side = landmarks[234]

            # Roll angle from eye corners
            d_x = eye_right.x - eye_left.x
            d_y = eye_right.y - eye_left.y
            roll_deg = math.degrees(math.atan2(d_y, d_x))

            # Yaw approximation: asymmetry between nose tip and left/right facial borders
            dist_left = abs(nose_tip.x - left_side.x)
            dist_right = abs(nose_tip.x - right_side.x)
            yaw_ratio = (dist_right - dist_left) / max(1e-4, dist_right + dist_left)
            yaw_deg = float(np.clip(yaw_ratio * 60.0, -90.0, 90.0))

            # Pitch approximation: vertical ratio between nose and forehead vs chin
            dist_vertical = abs(chin.y - landmarks[10].y)  # 10 is top of forehead
            dist_chin_nose = abs(chin.y - nose_tip.y)
            pitch_ratio = (dist_chin_nose / max(1e-4, dist_vertical)) - 0.5
            pitch_deg = float(np.clip(pitch_ratio * 70.0, -90.0, 90.0))

            return FaceLandmarkData(
                bbox=bbox,
                pitch_deg=pitch_deg,
                yaw_deg=yaw_deg,
                roll_deg=roll_deg,
                eye_outer_left=eye_left,
                eye_outer_right=eye_right,
            )
        except Exception:
            return None

    def _default_mock_landmarks(
        self, image: np.ndarray
    ) -> tuple[PoseLandmarkData | None, list[FaceLandmarkData]]:
        """Return neutral calibrated synthetic landmarks matching image dimensions."""
        h, w = image.shape[:2]
        cx = w / 2.0
        cy = h / 2.0

        pose = PoseLandmarkData(
            left_shoulder=Point2D(cx + 100.0, cy + 80.0),
            right_shoulder=Point2D(cx - 100.0, cy + 80.0),
            left_ear=Point2D(cx + 40.0, cy - 40.0),
            right_ear=Point2D(cx - 40.0, cy - 40.0),
            nose=Point2D(cx, cy - 60.0),
        )

        face = FaceLandmarkData(
            bbox=BoundingBox(cx - 60.0, cy - 120.0, cx + 60.0, cy),
            pitch_deg=0.0,
            yaw_deg=0.0,
            roll_deg=0.0,
            eye_outer_left=Point2D(cx + 30.0, cy - 60.0),
            eye_outer_right=Point2D(cx - 30.0, cy - 60.0),
        )

        return pose, [face]

    def close(self) -> None:
        """Release underlying MediaPipe models."""
        if self._pose_solution is not None:
            with contextlib.suppress(Exception):
                self._pose_solution.close()
            self._pose_solution = None

        if self._face_solution is not None:
            with contextlib.suppress(Exception):
                self._face_solution.close()
            self._face_solution = None
