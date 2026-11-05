# BACKLOG.md — Pelacak Eksekusi Fitur Posture Guardian

> **Cara Penggunaan oleh Agen:** Periksa daftar di bawah ini sebelum memulai sesi kerja. Ubah tanda `[ ]` menjadi `[~]` saat fitur sedang dikerjakan, dan `[x]` setelah seluruh *unit test* serta *typecheck* lulus.

## Fase 1: Fondasi Database & Arsitektur State
- [x] **M1.1 — Inisialisasi Skema SQLite WAL (`src/db/schema.sql` & `src/db/manager.py`)**
  - Referensi: `docs/specs/04-telemetry-and-schema.md`, `docs/ARCHITECTURE.md`
  - Buat skema 4 tabel (`calibration_profiles`, `monitoring_sessions`, `posture_state_intervals`, `session_telemetry_buckets`) dan *worker thread* penyimpanan asinkron.
- [x] **M1.2 — Definisi Enum & State Machine 3-Sumbu (`src/tracker/states.py`)**
  - Referensi: `docs/ARCHITECTURE.md`
  - Implementasikan sumbu ortogonal `OperatingMode`, `VisibilityState`, dan `UIIntrusivenessLevel`.

## Fase 2: Pipeline Sinyal, Tracker, & Kalibrasi (Tanpa Kamera/GUI)
- [x] **M2.1 — Ekstraktor 5 Metrik Geometris Murni (`src/vision/metrics.py`)**
  - Referensi: `docs/specs/01-vision-and-metrics.md`
  - Implementasikan fungsi matematis untuk `HEAD_FORWARD_SCALE`, `TORSO_NECK_COMPRESSION`, `SHOULDER_VERTICAL_DROP`, `GAZE_DOWN_PITCH`, dan `CORONAL_ASYMMETRY` beserta gerbang *yaw* ($|\text{yaw}_{\text{head}}| \gt 15^\circ$).
- [x] **M2.2 — Filter Sinyal & `IssueTracker` (`src/tracker/issue_tracker.py`)**
  - Referensi: `docs/specs/02-signal-and-tracker.md`
  - Implementasikan *Sliding Median-3*, *Asymmetric EMA* sadar-$\Delta t$, *Normalized Velocity Detector*, *One-Way Velocity Gate* (`freeze_max_s = 3.0`), *Step-Down Floor* (`d_floor = 9.0`), serta *UI Cooldown & Micro-break Escalation*.
- [x] **M2.3 — Modul Kalibrasi, MAD Noise Floor, Setup Drift, & Telemetri Bebas Dilusi (`src/tracker/calibration.py` & `src/tracker/telemetry.py`)**
  - Referensi: `docs/specs/03-calibration-and-drift.md`, `docs/specs/04-telemetry-and-schema.md`
  - Implementasikan *Plausibility Gate* 15 sampel, *Running Quiet-Period Noise Floor*, detektor setup drift ($\ge 10\text{ detik}$), RLE interval manager, agregator *bucket* 5 menit bebas dilusi (`mean_violating_*`, `p95_*`), dan batas kepatuhan sesi.

## Fase 3: Akuisisi Kamera & Fusi MediaPipe
- [x] **M3.1 — Single-Reader Camera Thread 5 FPS (`src/camera/reader.py`)**
  - Referensi: `docs/specs/01-vision-and-metrics.md`, `docs/ARCHITECTURE.md`
  - Implementasikan pembacaan kamera $\Delta t \approx 200\text{ ms}$ dengan *monotonic timestamp* dan *latest-frame queue* berukuran $1$.
- [x] **M3.2 — Sequential Pose + Face Engine & Multi-Person Association (`src/vision/detector.py`)**
  - Referensi: `docs/specs/01-vision-and-metrics.md`
  - Integrasikan `MediaPipe Pose Landmarker (Lite)` dan `MediaPipe Face Landmarker` beserta verifikasi jarak Euclidean hidung ke pusat wajah ($\lt 0.25 \cdot W_{\text{sh}}$).

## Fase 4: Antarmuka Desktop & Integrasi OS
- [x] **M4.1 — Non-Activating & Screen-Share Safe HUD Overlay (`src/ui/overlay.py`)**
  - Referensi: `docs/DESIGN_SYSTEM.md`, `docs/ARCHITECTURE.md`
  - Implementasikan jendela *overlay* tembus klik, anti-curi fokus, dan tersembunyi dari tangkapan layar rapat (`WDA_EXCLUDEFROMCAPTURE` / `NSWindowSharingNone`).
- [x] **M4.2 — System Tray, Quick Snooze, & OS Power Events (`src/ui/tray.py`)**
  - Referensi: `docs/DESIGN_SYSTEM.md`, `docs/ARCHITECTURE.md`
  - Implementasikan menu *System Tray*, tombol *Snooze* ($15/30/60\text{ menit}$), dan *hook* *flush* database saat *sleep/wake*.
- [x] **M4.3 — Jendela Kalibrasi Terpandu & Dashboard Tren (`src/ui/dashboard.py`)**
  - Referensi: `docs/DESIGN_SYSTEM.md`, `docs/specs/04-telemetry-and-schema.md`
  - Implementasikan dialog kalibrasi 3 detik dan visualisasi ringkasan kepatuhan dengan batas konservatif oklusi serta tabel *bucket* 5 menit.

## Fase 4.5: Kesiapan Produksi & Verifikasi Webcam Fisik
- [x] **M4.5 — Model Management, Konfigurasi Eksternal, Live Telemetry TUI & Smoke Test Webcam**
  - `scripts/download_models.py` mengunduh `pose_landmarker_lite.task` dan `face_landmarker.task` dari Google MediaPipe Storage secara otomatis saat aplikasi start.
  - `FaceLandmarkerOptions` mengaktifkan `output_facial_transformation_matrixes=True` dengan dekomposisi matriks rotasi Euler 3D.
  - Ambang batas metrik diekstrak ke `config.toml` dengan loader sadar-tipe di `src/config.py`.
  - CLI flag `--calibrate-thresholds` dan tab Live Telemetry pada Dashboard menampilkan deviasi $\hat{d}_i(t)$, $\sigma_i(t)$, ambang masuk/keluar, dan status gerak real-time untuk tuning empiris (Bab 8 spesifikasi).
  - Skrip pengujian webcam fisik `scripts/smoke_test_webcam.py` memverifikasi akuisisi UVC 5 FPS dan ekstraksi 5 metrik tanpa error.

