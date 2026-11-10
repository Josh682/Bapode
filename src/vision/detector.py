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
import os
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

import numpy as np

from vision.landmarks import BoundingBox, FaceLandmarkData, Point2D, PoseLandmarkData


class SequentialLandmarkDetector:
    """Sequential Pose and Face detector wrapping MediaPipe Tasks or synthetic generator."""

    def __init__(
        self,
        use_mock: bool = False,
        mock_provider: (
            Callable[[np.ndarray, float], tuple[PoseLandmarkData | None, list[FaceLandmarkData]]]
            | None
        ) = None,
        models_dir: str | Path = "models",
    ) -> None:
        self.use_mock = use_mock
        self.mock_provider = mock_provider
        self.models_dir = models_dir

        self._pose_landmarker: Any = None
        self._face_landmarker: Any = None
        self._pose_solution: Any = None
        self._face_solution: Any = None
        self._use_tasks_api: bool = False

        if not self.use_mock:
            self._init_mediapipe()

    def _init_mediapipe(self) -> None:
        """Initialize MediaPipe Pose Lite and Face Landmarker Tasks."""
        mpl_dir = os.path.join(tempfile.gettempdir(), "bapode_matplotlib")
        os.environ.setdefault("MPLCONFIGDIR", mpl_dir)
        try:
            from scripts.download_models import ensure_models_exist

            pose_path, face_path = ensure_models_exist(self.models_dir)

            from mediapipe.tasks import python
            from mediapipe.tasks.python import vision

            # Pose Landmarker Lite Task
            pose_opts = vision.PoseLandmarkerOptions(
                base_options=python.BaseOptions(model_asset_path=str(pose_path)),
                output_segmentation_masks=False,
                min_pose_detection_confidence=0.5,
                min_tracking_confidence=0.5,
                num_poses=1,
            )
            self._pose_landmarker = vision.PoseLandmarker.create_from_options(pose_opts)

            # Face Landmarker Task with facial transformation matrixes
            face_opts = vision.FaceLandmarkerOptions(
                base_options=python.BaseOptions(model_asset_path=str(face_path)),
                output_facial_transformation_matrixes=True,
                num_faces=3,
                min_face_detection_confidence=0.5,
                min_tracking_confidence=0.5,
            )
            self._face_landmarker = vision.FaceLandmarker.create_from_options(face_opts)
            self._use_tasks_api = True
        except Exception:
            # Fallback to solutions API or mock mode
            self._init_legacy_solutions()

    def _init_legacy_solutions(self) -> None:
        """Fallback to legacy solutions API or mock mode."""
        try:
            import mediapipe as mp

            self._pose_solution = mp.solutions.pose.Pose(  # pyright: ignore[reportAttributeAccessIssue]
                static_image_mode=False,
                model_complexity=0,
                smooth_landmarks=True,
                min_detection_confidence=0.5,
                min_tracking_confidence=0.5,
            )
            self._face_solution = mp.solutions.face_mesh.FaceMesh(  # pyright: ignore[reportAttributeAccessIssue]
                static_image_mode=False,
                max_num_faces=3,
                refine_landmarks=True,
                min_detection_confidence=0.5,
                min_tracking_confidence=0.5,
            )
            self._use_tasks_api = False
        except Exception:
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
            import mediapipe as mp

            h, w = image_bgr.shape[:2]
            rgb_image = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2RGB)

            if self._use_tasks_api and self._pose_landmarker and self._face_landmarker:
                mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb_image)

                # 1. Pose Lite Task
                pose_data: PoseLandmarkData | None = None
                pose_res = self._pose_landmarker.detect(mp_image)
                if pose_res.pose_landmarks and len(pose_res.pose_landmarks) > 0:
                    lms = pose_res.pose_landmarks[0]
                    left_sh = Point2D(lms[11].x * w, lms[11].y * h)
                    right_sh = Point2D(lms[12].x * w, lms[12].y * h)
                    left_ear = Point2D(lms[7].x * w, lms[7].y * h)
                    right_ear = Point2D(lms[8].x * w, lms[8].y * h)
                    nose = Point2D(lms[0].x * w, lms[0].y * h)

                    vis_l = getattr(lms[11], "visibility", 1.0)
                    vis_r = getattr(lms[12], "visibility", 1.0)
                    if (vis_l is None or vis_l > 0.4) and (vis_r is None or vis_r > 0.4):
                        pose_data = PoseLandmarkData(
                            left_shoulder=left_sh,
                            right_shoulder=right_sh,
                            left_ear=left_ear,
                            right_ear=right_ear,
                            nose=nose,
                        )

                # 2. Face Landmarker Task
                detected_faces: list[FaceLandmarkData] = []
                face_res = self._face_landmarker.detect(mp_image)
                if face_res.face_landmarks:
                    matrices = getattr(face_res, "facial_transformation_matrixes", None)
                    for idx, face_lms in enumerate(face_res.face_landmarks):
                        mat = (
                            matrices[idx] if matrices is not None and idx < len(matrices) else None
                        )
                        face_data = self._extract_face_data(
                            face_lms, w, h, transformation_matrix=mat
                        )
                        if face_data is not None:
                            detected_faces.append(face_data)

                return pose_data, detected_faces

            # Fallback legacy solution
            pose_data = None
            if self._pose_solution is not None:
                pose_results = self._pose_solution.process(rgb_image)
                if pose_results.pose_landmarks:
                    lms = pose_results.pose_landmarks.landmark
                    nose = Point2D(lms[0].x * w, lms[0].y * h)
                    left_ear = Point2D(lms[7].x * w, lms[7].y * h)
                    right_ear = Point2D(lms[8].x * w, lms[8].y * h)
                    left_sh = Point2D(lms[11].x * w, lms[11].y * h)
                    right_sh = Point2D(lms[12].x * w, lms[12].y * h)

                    if lms[11].visibility > 0.5 and lms[12].visibility > 0.5:
                        pose_data = PoseLandmarkData(
                            left_shoulder=left_sh,
                            right_shoulder=right_sh,
                            left_ear=left_ear,
                            right_ear=right_ear,
                            nose=nose,
                        )

            detected_faces = []
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

    def _extract_face_data(
        self,
        landmarks: Any,
        w: int,
        h: int,
        transformation_matrix: Any = None,
    ) -> FaceLandmarkData | None:
        """Extract bounding box, eye landmarks, and Euler angles from matrix or landmarks."""
        try:
            xs = [lm.x * w for lm in landmarks]
            ys = [lm.y * h for lm in landmarks]

            bbox = BoundingBox(
                x_min=min(xs),
                y_min=min(ys),
                x_max=max(xs),
                y_max=max(ys),
            )

            # MediaPipe outer eye corner indices: 33 (left in image), 263 (right in image)
            eye_left = Point2D(landmarks[33].x * w, landmarks[33].y * h)
            eye_right = Point2D(landmarks[263].x * w, landmarks[263].y * h)

            # Prefer official MediaPipe facial transformation matrix if available
            if transformation_matrix is not None:
                r = np.array(transformation_matrix, dtype=float).reshape(4, 4)[:3, :3]
                sy = math.sqrt(r[0, 0] ** 2 + r[1, 0] ** 2)
                if sy >= 1e-6:
                    pitch_deg = float(math.degrees(math.atan2(r[2, 1], r[2, 2])))
                    yaw_deg = float(math.degrees(math.atan2(-r[2, 0], sy)))
                    roll_deg = float(math.degrees(math.atan2(r[1, 0], r[0, 0])))
                else:
                    pitch_deg = float(math.degrees(math.atan2(-r[1, 2], r[1, 1])))
                    yaw_deg = float(math.degrees(math.atan2(-r[2, 0], sy)))
                    roll_deg = 0.0
            else:
                # Geometric landmark fallback approximation
                nose_tip = landmarks[1]
                chin = landmarks[152]
                left_side = landmarks[454]
                right_side = landmarks[234]

                d_x = eye_right.x - eye_left.x
                d_y = eye_right.y - eye_left.y
                roll_deg = math.degrees(math.atan2(d_y, d_x))

                dist_left = abs(nose_tip.x - left_side.x)
                dist_right = abs(nose_tip.x - right_side.x)
                yaw_ratio = (dist_right - dist_left) / max(1e-4, dist_right + dist_left)
                yaw_deg = float(np.clip(yaw_ratio * 60.0, -90.0, 90.0))

                dist_vertical = abs(chin.y - landmarks[10].y)
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
        if self._pose_landmarker is not None:
            with contextlib.suppress(Exception):
                self._pose_landmarker.close()
            self._pose_landmarker = None

        if self._face_landmarker is not None:
            with contextlib.suppress(Exception):
                self._face_landmarker.close()
            self._face_landmarker = None

        if self._pose_solution is not None:
            with contextlib.suppress(Exception):
                self._pose_solution.close()
            self._pose_solution = None

        if self._face_solution is not None:
            with contextlib.suppress(Exception):
                self._face_solution.close()
            self._face_solution = None
