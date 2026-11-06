"""Model Downloader for Bapode MediaPipe Tasks.

Downloads the official Google MediaPipe task models:
- pose_landmarker_lite.task (~9.1 MB)
- face_landmarker.task (~29.8 MB)
from Google MediaPipe Storage into the `models/` directory.
"""

from __future__ import annotations

import argparse
import sys
import time
import urllib.request
from pathlib import Path

MODELS: dict[str, str] = {
    "pose_landmarker_lite.task": (
        "https://storage.googleapis.com/mediapipe-models/pose_landmarker/"
        "pose_landmarker_lite/float16/latest/pose_landmarker_lite.task"
    ),
    "face_landmarker.task": (
        "https://storage.googleapis.com/mediapipe-models/face_landmarker/"
        "face_landmarker/float16/latest/face_landmarker.task"
    ),
}


def download_file_with_progress(url: str, dest_path: Path) -> None:
    """Download a file with streaming progress percentage."""
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = dest_path.with_suffix(".tmp")

    print(f"Mengunduh {dest_path.name} dari:")
    print(f"  {url}")

    req = urllib.request.Request(
        url,
        headers={"User-Agent": "Bapode-ModelDownloader/3.0"},
    )

    start_time = time.time()
    try:
        with urllib.request.urlopen(req, timeout=60) as response, open(temp_path, "wb") as out_file:
            total_size = int(response.headers.get("Content-Length", 0))
            downloaded = 0
            chunk_size = 64 * 1024  # 64 KB

            while True:
                chunk = response.read(chunk_size)
                if not chunk:
                    break
                out_file.write(chunk)
                downloaded += len(chunk)

                if total_size > 0:
                    percent = (downloaded / total_size) * 100.0
                    mb_downloaded = downloaded / (1024 * 1024)
                    mb_total = total_size / (1024 * 1024)
                    sys.stdout.write(
                        f"\r  Progress: {percent:5.1f}% ({mb_downloaded:.1f}/{mb_total:.1f} MB)"
                    )
                    sys.stdout.flush()

        temp_path.replace(dest_path)
        elapsed = max(0.1, time.time() - start_time)
        mb_size = dest_path.stat().st_size / (1024 * 1024)
        print(f"\n  Selesai: {dest_path.name} ({mb_size:.1f} MB) dalam {elapsed:.1f}s.")
    except Exception as e:
        if temp_path.exists():
            temp_path.unlink()
        raise RuntimeError(f"Gagal mengunduh {dest_path.name}: {e}") from e


def ensure_models_exist(models_dir: str | Path = "models") -> tuple[Path, Path]:
    """Ensure both pose and face landmarker models exist in models_dir.

    Returns:
        (pose_model_path, face_model_path)
    """
    base_dir = Path(models_dir)
    if not base_dir.is_absolute():
        # Resolve relative to repo root (where scripts/ or src/ is located)
        repo_root = Path(__file__).resolve().parent.parent
        base_dir = repo_root / models_dir

    base_dir.mkdir(parents=True, exist_ok=True)

    pose_path = base_dir / "pose_landmarker_lite.task"
    face_path = base_dir / "face_landmarker.task"

    for filename, url in MODELS.items():
        file_path = base_dir / filename
        # File is valid if it exists and is > 100 KB
        if file_path.exists() and file_path.stat().st_size > 100 * 1024:
            continue
        download_file_with_progress(url, file_path)

    return pose_path, face_path


def main() -> int:
    """CLI runner to download models manually."""
    parser = argparse.ArgumentParser(
        description="Unduh file model MediaPipe Task untuk Bapode v3.0"
    )
    parser.add_argument(
        "--models-dir",
        type=str,
        default="models",
        help="Direktori penyimpanan model (default: models/)",
    )
    args = parser.parse_args()

    try:
        pose, face = ensure_models_exist(args.models_dir)
        print(f"\nSemua model siap di:\n  Pose: {pose}\n  Face: {face}")
        return 0
    except Exception as exc:
        print(f"\nError: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
