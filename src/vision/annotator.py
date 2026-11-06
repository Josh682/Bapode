"""Frame Annotator for Live Camera Preview in GUI.

Draws posture landmarks, status overlays, and calibration guides onto camera frames.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import cv2

from tracker.states import PostureViolationFlags, VisibilityState
from ui.qt import QtGui

if TYPE_CHECKING:
    import numpy as np

    from vision.landmarks import FaceLandmarkData, PoseLandmarkData


class FrameAnnotator:
    """Renders posture landmarks, guidance overlays, and HUD text onto frames."""

    # Color palette in BGR format
    COLOR_EMERALD = (129, 185, 16)  # #10B981 (Good / Healthy)
    COLOR_AMBER = (11, 158, 245)  # #F59E0B (Warning / Caution)
    COLOR_RED = (68, 68, 239)  # #EF4444 (Violation / Error)
    COLOR_CYAN = (235, 206, 135)  # Soft cyan for eye markers
    COLOR_WHITE = (255, 255, 255)
    COLOR_DARK = (17, 24, 39)  # #111827 Dark slate

    @classmethod
    def annotate(
        cls,
        image_bgr: np.ndarray,
        pose: PoseLandmarkData | None = None,
        face: FaceLandmarkData | None = None,
        vis_state: VisibilityState = VisibilityState.MEASURABLE,
        violations: PostureViolationFlags | None = None,
        is_moving: bool = False,
        is_calibrating: bool = False,
        calib_current: int = 0,
        calib_total: int = 15,
        calib_plausible: bool = True,
        calib_feedback: str = "",
    ) -> QtGui.QImage:
        """Annotate frame with posture landmarks, bounding boxes, and HUD status."""
        canvas = image_bgr.copy()
        h, w = canvas.shape[:2]

        # 1. Calibration Silhouette & Guide (when calibrating)
        if is_calibrating:
            cls._draw_calibration_guide(
                canvas, w, h, calib_current, calib_total, calib_plausible, calib_feedback
            )

        # 2. Draw Shoulders & Neck (Pose)
        if pose is not None:
            cls._draw_pose_landmarks(canvas, pose, violations)

        # 3. Draw Face, Eyes & Bounding Box
        if face is not None:
            cls._draw_face_landmarks(canvas, face)

        # 4. Draw Top HUD Banner
        cls._draw_hud_banner(
            canvas,
            w,
            h,
            vis_state,
            violations,
            is_moving,
            face,
            is_calibrating,
        )

        # Convert to QImage
        rgb = cv2.cvtColor(canvas, cv2.COLOR_BGR2RGB)
        bytes_per_line = 3 * w
        return QtGui.QImage(
            rgb.data,
            w,
            h,
            bytes_per_line,
            QtGui.QImage.Format.Format_RGB888,
        ).copy()

    @classmethod
    def _draw_pose_landmarks(
        cls,
        canvas: np.ndarray,
        pose: PoseLandmarkData,
        violations: PostureViolationFlags | None,
    ) -> None:
        """Draw shoulders, neck connection, ears, and nose."""
        has_slump = violations.v_slump if violations else False
        has_tilt = violations.v_tilt if violations else False
        shoulder_color = cls.COLOR_RED if (has_slump or has_tilt) else cls.COLOR_EMERALD

        pt_ls = (int(pose.left_shoulder.x), int(pose.left_shoulder.y))
        pt_rs = (int(pose.right_shoulder.x), int(pose.right_shoulder.y))
        pt_mid = (int(pose.shoulder_midpoint.x), int(pose.shoulder_midpoint.y))
        pt_nose = (int(pose.nose.x), int(pose.nose.y))

        # Shoulder line
        cv2.line(canvas, pt_ls, pt_rs, shoulder_color, 3, cv2.LINE_AA)
        cv2.circle(canvas, pt_ls, 7, shoulder_color, -1, cv2.LINE_AA)
        cv2.circle(canvas, pt_ls, 9, cls.COLOR_WHITE, 1, cv2.LINE_AA)
        cv2.circle(canvas, pt_rs, 7, shoulder_color, -1, cv2.LINE_AA)
        cv2.circle(canvas, pt_rs, 9, cls.COLOR_WHITE, 1, cv2.LINE_AA)
        cv2.circle(canvas, pt_mid, 5, cls.COLOR_WHITE, -1, cv2.LINE_AA)

        # Neck connection from nose to shoulder midpoint
        cv2.line(canvas, pt_nose, pt_mid, (180, 180, 180), 1, cv2.LINE_AA)
        cv2.circle(canvas, pt_nose, 5, cls.COLOR_CYAN, -1, cv2.LINE_AA)

        # Ears
        pt_le = (int(pose.left_ear.x), int(pose.left_ear.y))
        pt_re = (int(pose.right_ear.x), int(pose.right_ear.y))
        cv2.circle(canvas, pt_le, 4, (200, 200, 200), -1, cv2.LINE_AA)
        cv2.circle(canvas, pt_re, 4, (200, 200, 200), -1, cv2.LINE_AA)

    @classmethod
    def _draw_face_landmarks(cls, canvas: np.ndarray, face: FaceLandmarkData) -> None:
        """Draw face corner brackets and eye axis."""
        # Face bounding box corner brackets
        bbox = face.bbox
        x1, y1 = int(bbox.x_min), int(bbox.y_min)
        x2, y2 = int(bbox.x_max), int(bbox.y_max)
        bracket_len = max(12, int((x2 - x1) * 0.18))
        color = cls.COLOR_CYAN

        # Top-left
        cv2.line(canvas, (x1, y1), (x1 + bracket_len, y1), color, 2, cv2.LINE_AA)
        cv2.line(canvas, (x1, y1), (x1, y1 + bracket_len), color, 2, cv2.LINE_AA)
        # Top-right
        cv2.line(canvas, (x2, y1), (x2 - bracket_len, y1), color, 2, cv2.LINE_AA)
        cv2.line(canvas, (x2, y1), (x2, y1 + bracket_len), color, 2, cv2.LINE_AA)
        # Bottom-left
        cv2.line(canvas, (x1, y2), (x1 + bracket_len, y2), color, 2, cv2.LINE_AA)
        cv2.line(canvas, (x1, y2), (x1, y2 - bracket_len), color, 2, cv2.LINE_AA)
        # Bottom-right
        cv2.line(canvas, (x2, y2), (x2 - bracket_len, y2), color, 2, cv2.LINE_AA)
        cv2.line(canvas, (x2, y2), (x2, y2 - bracket_len), color, 2, cv2.LINE_AA)

        # Eye line
        pt_el = (int(face.eye_outer_left.x), int(face.eye_outer_left.y))
        pt_er = (int(face.eye_outer_right.x), int(face.eye_outer_right.y))
        cv2.line(canvas, pt_el, pt_er, cls.COLOR_CYAN, 2, cv2.LINE_AA)
        cv2.circle(canvas, pt_el, 4, cls.COLOR_CYAN, -1, cv2.LINE_AA)
        cv2.circle(canvas, pt_er, 4, cls.COLOR_CYAN, -1, cv2.LINE_AA)

    @classmethod
    def _draw_hud_banner(
        cls,
        canvas: np.ndarray,
        w: int,
        h: int,
        vis_state: VisibilityState,
        violations: PostureViolationFlags | None,
        is_moving: bool,
        face: FaceLandmarkData | None,
        is_calibrating: bool,
    ) -> None:
        """Render upper status bar with posture status and head orientation."""
        # Semi-transparent dark banner overlay
        overlay = canvas.copy()
        banner_h = 36
        cv2.rectangle(overlay, (0, 0), (w, banner_h), cls.COLOR_DARK, -1)
        cv2.addWeighted(overlay, 0.70, canvas, 0.30, 0, canvas)

        # Left status text
        if is_calibrating:
            status_text = "KALIBRASI 3 DETIK SEDANG BERJALAN..."
            status_color = cls.COLOR_AMBER
        elif is_moving:
            status_text = "[BERGERAK] Gerakan tubuh dinamis"
            status_color = cls.COLOR_AMBER
        elif vis_state != VisibilityState.MEASURABLE:
            status_text = f"[VISIBILITAS] {vis_state.value}"
            status_color = cls.COLOR_AMBER
        elif violations and (
            violations.v_fhp
            or violations.v_neck_comp
            or violations.v_slump
            or violations.v_gaze_pitch
            or violations.v_tilt
        ):
            active_issues: list[str] = []
            if violations.v_fhp:
                active_issues.append("FHP")
            if violations.v_neck_comp:
                active_issues.append("NECK")
            if violations.v_slump:
                active_issues.append("SLUMP")
            if violations.v_gaze_pitch:
                active_issues.append("PITCH")
            if violations.v_tilt:
                active_issues.append("TILT")
            status_text = f"[MELANGGAR] {', '.join(active_issues)}"
            status_color = cls.COLOR_RED
        else:
            status_text = "[OK] POSTUR TEGAK SEIMBANG"
            status_color = cls.COLOR_EMERALD

        cv2.putText(
            canvas,
            status_text,
            (14, 23),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.55,
            status_color,
            2,
            cv2.LINE_AA,
        )

        # Right head pose text (pitch, yaw, roll)
        if face is not None:
            pose_str = (
                f"P: {face.pitch_deg:+.1f} | Y: {face.yaw_deg:+.1f} | R: {face.roll_deg:+.1f}"
            )
            cv2.putText(
                canvas,
                pose_str,
                (max(10, w - 260), 23),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.48,
                (209, 213, 219),
                1,
                cv2.LINE_AA,
            )

    @classmethod
    def _draw_calibration_guide(
        cls,
        canvas: np.ndarray,
        w: int,
        h: int,
        current: int,
        total: int,
        is_plausible: bool,
        feedback: str,
    ) -> None:
        """Draw silhouette guides and central instructions during calibration."""
        guide_color = cls.COLOR_EMERALD if is_plausible else cls.COLOR_RED
        cx, cy = w // 2, h // 2

        # Target head guide (ellipse)
        cv2.ellipse(
            canvas,
            (cx, int(cy * 0.70)),
            (int(w * 0.12), int(h * 0.22)),
            0,
            0,
            360,
            guide_color,
            2,
            cv2.LINE_AA,
        )

        # Target shoulder guide (arc / line)
        sh_y = int(cy * 1.30)
        sh_half_w = int(w * 0.28)
        cv2.line(
            canvas,
            (cx - sh_half_w, sh_y),
            (cx + sh_half_w, sh_y),
            guide_color,
            2,
            cv2.LINE_AA,
        )

        # Center feedback prompt
        msg = feedback if feedback else "Duduk tegak & tatap kamera"
        cv2.putText(
            canvas,
            f"{msg} ({current}/{total})",
            (max(20, cx - 220), int(h * 0.92)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.65,
            guide_color,
            2,
            cv2.LINE_AA,
        )
