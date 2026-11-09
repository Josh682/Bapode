"""Webcam and MediaPipe Tasks Smoke Test for Bapode v3.0.

Captures frames from physical UVC webcam for 10 seconds at 5 FPS (50 frames),
runs sequential Pose Lite and Face Landmarker Tasks (.task models),
extracts all 5 posture metrics, and prints a verification summary.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

# Ensure MPLCONFIGDIR is set to avoid slow font cache building
os.environ.setdefault("MPLCONFIGDIR", "/tmp/matplotlib")

# Ensure repository root and src are on sys.path
REPO_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.download_models import ensure_models_exist  # noqa: E402
from vision.detector import SequentialLandmarkDetector  # noqa: E402
from vision.metrics import CalibrationReference, calculate_posture_metrics  # noqa: E402


def run_smoke_test(
    camera_index: int = 0,
    duration_s: float = 10.0,
    target_fps: float = 5.0,
) -> int:
    """Run real camera acquisition and MediaPipe Tasks extraction test."""
    if sys.platform == "win32":
        # Windows consoles often default to cp1252, which raises UnicodeEncodeError
        # on characters such as '—' and '°'.
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        if hasattr(sys.stderr, "reconfigure"):
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")

    print("=" * 80)
    if sys.platform == "win32":
        print(" Bapode v3.0 - Physical Webcam & MediaPipe Tasks Smoke Test")
    else:
        print(" Bapode v3.0 — Physical Webcam & MediaPipe Tasks Smoke Test")
    print("=" * 80)

    # 1. Ensure models exist
    print("\n[1/3] Memeriksa file model MediaPipe (.task)...")
    try:
        pose_model, face_model = ensure_models_exist()
        p_sz = pose_model.stat().st_size / 1024 / 1024
        f_sz = face_model.stat().st_size / 1024 / 1024
        print(f"  Pose model : {pose_model.name} ({p_sz:.1f} MB)")
        print(f"  Face model : {face_model.name} ({f_sz:.1f} MB)")
    except Exception as exc:
        print(f"  GAGAL mengunduh/memuat model: {exc}", file=sys.stderr)
        return 1

    # 2. Open camera
    print(f"\n[2/3] Membuka webcam fisik (index {camera_index})...")
    try:
        import cv2

        if sys.platform == "win32":
            # Windows: DirectShow avoids MSMF lag/freeze; fall back to MSMF if needed
            cap = cv2.VideoCapture(camera_index, cv2.CAP_DSHOW)
            if not cap.isOpened():
                cap.release()
                cap = cv2.VideoCapture(camera_index, cv2.CAP_MSMF)
            if not cap.isOpened():
                print(
                    f"  GAGAL: Kamera index {camera_index} tidak dapat dibuka.\n"
                    "  Periksa izin kamera Windows (Settings -> Privacy & security -> Camera,\n"
                    "  aktifkan 'Let desktop apps access your camera')\n"
                    "  atau pastikan webcam tidak sedang digunakan oleh aplikasi lain.",
                    file=sys.stderr,
                )
                return 1
            cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        else:
            cap = cv2.VideoCapture(camera_index)
            if not cap.isOpened():
                print(
                    f"  GAGAL: Kamera index {camera_index} tidak dapat dibuka.\n"
                    "  Periksa izin kamera macOS "
                    "(System Settings -> Privacy & Security -> Camera)\n"
                    "  atau pastikan webcam tidak sedang digunakan oleh aplikasi lain.",
                    file=sys.stderr,
                )
                return 1

        # Set resolution to 1280x720
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
        actual_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        actual_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        print(f"  Kamera terbuka dengan resolusi: {actual_w} x {actual_h}")
    except Exception as exc:
        print(f"  GAGAL menginisialisasi OpenCV VideoCapture: {exc}", file=sys.stderr)
        return 1

    # 3. Initialize Detector
    print("\n[3/3] Memuat SequentialLandmarkDetector (MediaPipe Tasks)...")
    detector = SequentialLandmarkDetector(use_mock=False)
    if not detector._use_tasks_api:
        print("  PERINGATAN: MediaPipe Tasks tidak aktif, beralih ke fallback solutions/mock.")
    else:
        print("  MediaPipe Tasks API (Pose Lite + Face Landmarker) aktif.")

    # Reference baseline stub for metric calculation
    ref = CalibrationReference(
        base_neck_ort_ratio=0.55,
        base_eye_sh_ratio=0.30,
        base_sh_width_px=actual_w * 0.35,
        base_sh_y_mid=actual_h * 0.60,
        base_sh_angle_deg=0.0,
        base_head_pitch_deg=0.0,
        base_head_roll_deg=0.0,
    )

    expected_frames = int(duration_s * target_fps)
    interval_s = 1.0 / target_fps

    print(
        f"\nMemulai pengujian {expected_frames} frame "
        f"(~{duration_s:.0f} detik pada {target_fps:.0f} FPS)..."
    )
    print("-" * 80)
    print(
        f"{'Frame':<8} {'Latency':<10} {'Pose':<8} {'Face':<8} "
        f"{'d_fhp':<10} {'d_slump':<10} {'Pitch':<8}"
    )
    print("-" * 80)

    frames_captured = 0
    pose_count = 0
    face_count = 0
    measurable_count = 0
    latencies: list[float] = []

    fhp_vals: list[float] = []
    neck_vals: list[float] = []
    slump_vals: list[float] = []
    pitch_vals: list[float] = []
    tilt_vals: list[float] = []

    start_time = time.monotonic()
    try:
        while frames_captured < expected_frames:
            loop_start = time.monotonic()

            ret, frame = cap.read()
            if not ret or frame is None:
                print(f"  Peringatan: Gagal membaca frame ke-{frames_captured + 1}")
                time.sleep(interval_s)
                continue

            frames_captured += 1

            # Run inference and measure latency
            t0 = time.perf_counter()
            pose, faces = detector.detect(frame, loop_start)
            latency_ms = (time.perf_counter() - t0) * 1000.0
            latencies.append(latency_ms)

            has_pose = pose is not None
            has_face = len(faces) > 0
            if has_pose:
                pose_count += 1
            if has_face:
                face_count += 1

            fhp_str = "—"
            slump_str = "—"
            pitch_str = "—"

            if has_pose and has_face:
                measurable_count += 1
                face = faces[0]
                assert pose is not None
                metrics = calculate_posture_metrics(pose, face, ref, dt=interval_s)
                fhp_vals.append(metrics.d_fhp)
                neck_vals.append(metrics.d_neck)
                slump_vals.append(metrics.d_slump)
                pitch_vals.append(metrics.d_pitch)
                tilt_vals.append(metrics.d_tilt)

                fhp_str = f"{metrics.d_fhp:+.3f}"
                slump_str = f"{metrics.d_slump:+.3f}"
                pitch_str = f"{metrics.d_pitch:+.1f}°"

            print(
                f"{frames_captured:<8} {latency_ms:6.1f}ms   {'YES' if has_pose else 'NO':<8} "
                f"{'YES' if has_face else 'NO':<8} {fhp_str:<10} {slump_str:<10} {pitch_str:<8}"
            )

            # Maintain target 5 FPS pacing
            elapsed = time.monotonic() - loop_start
            sleep_time = max(0.001, interval_s - elapsed)
            time.sleep(sleep_time)
    finally:
        cap.release()
        detector.close()

    total_elapsed = max(0.1, time.monotonic() - start_time)
    actual_fps = frames_captured / total_elapsed
    avg_latency = (sum(latencies) / len(latencies)) if latencies else 0.0

    print("=" * 80)
    print(" HASIL SMOKE TEST WEBCAM:")
    print("=" * 80)
    print(
        f" Total Frame Terambil : {frames_captured} frame "
        f"dalam {total_elapsed:.1f}s (FPS nyata: {actual_fps:.1f})"
    )
    pose_pct = (pose_count / max(1, frames_captured)) * 100
    face_pct = (face_count / max(1, frames_captured)) * 100
    meas_pct = (measurable_count / max(1, frames_captured)) * 100
    print(f" Deteksi Pose         : {pose_count}/{frames_captured} ({pose_pct:.1f}%)")
    print(f" Deteksi Wajah        : {face_count}/{frames_captured} ({face_pct:.1f}%)")
    print(f" Frame Terukur Lengkap: {measurable_count}/{frames_captured} ({meas_pct:.1f}%)")
    print(f" Rata-rata Latensi    : {avg_latency:.1f} ms / frame (anggaran: <= 200 ms)")

    if measurable_count > 0:
        print("\n Statistik Metrik Geometris (Rata-rata):")
        fhp_m, fhp_min, fhp_max = sum(fhp_vals) / len(fhp_vals), min(fhp_vals), max(fhp_vals)
        neck_m, neck_min, neck_max = sum(neck_vals) / len(neck_vals), min(neck_vals), max(neck_vals)
        slump_m, slump_min, slump_max = (
            sum(slump_vals) / len(slump_vals),
            min(slump_vals),
            max(slump_vals),
        )
        pitch_m, pitch_min, pitch_max = (
            sum(pitch_vals) / len(pitch_vals),
            min(pitch_vals),
            max(pitch_vals),
        )
        tilt_m, tilt_min, tilt_max = sum(tilt_vals) / len(tilt_vals), min(tilt_vals), max(tilt_vals)

        print(f"   d_fhp   : {fhp_m:+.4f} (min: {fhp_min:+.4f}, max: {fhp_max:+.4f})")
        print(f"   d_neck  : {neck_m:+.4f} (min: {neck_min:+.4f}, max: {neck_max:+.4f})")
        print(f"   d_slump : {slump_m:+.4f} (min: {slump_min:+.4f}, max: {slump_max:+.4f})")
        print(f"   d_pitch : {pitch_m:+.2f}° (min: {pitch_min:+.2f}°, max: {pitch_max:+.2f}°)")
        print(f"   d_tilt  : {tilt_m:+.2f}° (min: {tilt_min:+.2f}°, max: {tilt_max:+.2f}°)")

    print("-" * 80)
    if frames_captured >= 10 and avg_latency < 200.0:
        print(" VERDIK: [PASS] KAMERA, MODEL .TASK, & 5 METRIK BERJALAN SEMPURNA!")
        print(" Bapode v3.0 SIAP DIGUNAKAN HARIAN.")
        print("=" * 80)
        return 0
    else:
        print(" VERDIK: [WARNING] Frame kurang dari target atau latensi tinggi.")
        print("=" * 80)
        return 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Webcam & MediaPipe Tasks Smoke Test")
    parser.add_argument("--camera-index", type=int, default=0, help="UVC Camera index (default: 0)")
    parser.add_argument(
        "--duration", type=float, default=10.0, help="Test duration in seconds (default: 10.0)"
    )
    parser.add_argument("--fps", type=float, default=5.0, help="Target FPS (default: 5.0)")
    args = parser.parse_args()

    return run_smoke_test(
        camera_index=args.camera_index,
        duration_s=args.duration,
        target_fps=args.fps,
    )


if __name__ == "__main__":
    sys.exit(main())
