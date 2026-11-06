# AGENTS.md — Bapode (v3.0 Daily-Use Production)

## 1. Identitas & Batasan Produk
**Bapode** adalah aplikasi utilitas desktop harian (*daily-use personal utility*) berbasis kamera *webcam* monokuler yang memantau **kepatuhan postur relatif terhadap referensi personal** dan **paparan beban statis** secara $100\%$ *on-device*, sadar-privasi, dan *non-intrusive*.
- **Bukan Alat Klinis:** Aplikasi tidak mengukur sudut ortopedi absolut (seperti *Craniovertebral Angle* klinis), melainkan menghitung deviasi relatif $d_i(t)$ terhadap *baseline* kalibrasi 3 detik pengguna.
- **Prinsip Anti-Nagging:** Mengizinkan gerakan dinamis singkat, menerapkan *one-way velocity gate*, *cooldown* peringatan visual ($30\text{ detik}$), serta mengeskalasi pelanggaran berulang menjadi pengingat *micro-break*.

## 2. Tech Stack & Lingkungan Eksekusi
- **Bahasa:** Python $\ge 3.11$ (Type Hints wajib di seluruh modul).
- **GUI & Integrasi OS:** `PySide6` (Qt6), `PyObjC` (khusus macOS untuk perilaku jendela *overlay* & *sleep notification*), `pywin32` / `ctypes` (`SetWindowDisplayAffinity` & `WM_POWERBROADCAST` pada Windows).
- **Computer Vision & ML:** `mediapipe` (`PoseLandmarker Lite` + `FaceLandmarker`), `opencv-python-headless` (atau `opencv-python` untuk akuisisi UVC 5 FPS), `numpy`.
- **Database Lokal:** `sqlite3` bawaan Python dengan mode `PRAGMA journal_mode = WAL;` dan `PRAGMA foreign_keys = ON;`.
- **Pengujian:** `pytest`, `pytest-qt`.

## 3. Struktur Direktori Standar
```text
bapode/
├── AGENTS.md                          # [Lapis 1] Instruksi global & peta konteks agen
├── BACKLOG.md                         # [Lapis 3] Pelacak milestone & status implementasi
├── docs/                              # [Lapis 2] Konteks modular (baca sesuai tugas)
│   ├── ARCHITECTURE.md                # Model 3-thread, State Machine 3-sumbu, integrasi OS
│   ├── DESIGN_SYSTEM.md               # Aturan HUD Overlay, System Tray, & Dashboard UX
│   └── specs/                         # Spesifikasi vertikal per-modul
│       ├── 01-vision-and-metrics.md   # Akuisisi 5 FPS, asosiasi subjek, & 5 rumus metrik
│       ├── 02-signal-and-tracker.md   # Median-3, EMA asimetris, Velocity Gate, IssueTracker
│       ├── 03-calibration-and-drift.md# Plausibility Gate, MAD noise adaptif, & Setup Drift
│       └── 04-telemetry-and-schema.md # Kontrak metrik SQLite, interval RLE, & bucket 5m
├── src/
│   ├── main.py                        # Entrypoint aplikasi & inisialisasi thread
│   ├── camera/                        # Thread 3: Akuisisi kamera 5 FPS & latest-frame queue
│   ├── vision/                        # Thread 1: MediaPipe Pose+Face & ekstraktor 5 metrik
│   ├── tracker/                       # Thread 1: Filter sinyal, 5x IssueTracker, State Machine
│   ├── db/                            # Thread 2: schema.sql, SQLite WAL writer & query API
│   └── ui/                            # Main Thread: System Tray, Screen-Safe HUD, Dashboard
└── tests/                             # Unit test & simulasi sekuens sinyal
```

## 4. Peta Navigasi Konteks (Context Map)
> **INSTRUKSI WAJIB BAGI AGEN:** Jangan memuat seluruh isi folder `docs/` sekaligus. Baca hanya dokumen yang relevan dengan tugas di `BACKLOG.md` yang sedang dikerjakan:
1. Mengerjakan *threading*, komunikasi antar-komponen, *state machine* utama, atau integrasi OS $\rightarrow$ Baca `docs/ARCHITECTURE.md`.
2. Mengerjakan *overlay*, ikon *System Tray*, *screen-sharing protection*, atau tampilan grafik $\rightarrow$ Baca `docs/DESIGN_SYSTEM.md`.
3. Mengerjakan *pipeline* kamera, inferensi MediaPipe, atau perhitungan geometri postur $\rightarrow$ Baca `docs/specs/01-vision-and-metrics.md`.
4. Mengerjakan pemrosesan sinyal (EMA/Median), deteksi gerakan, atau logika `IssueTracker` $\rightarrow$ Baca `docs/specs/02-signal-and-tracker.md`.
5. Mengerjakan alur kalibrasi 3 detik, adaptasi *noise* pencahayaan, atau deteksi perubahan posisi kursi/layar $\rightarrow$ Baca `docs/specs/03-calibration-and-drift.md`.
6. Mengerjakan kueri database, penyimpanan sesi, atau agregasi statistik $\rightarrow$ Baca `docs/specs/04-telemetry-and-schema.md` dan gunakan `src/db/schema.sql` sebagai *Single Source of Truth* struktur tabel.

## 5. Perintah Verifikasi Mandiri (Self-Verification Commands)
Setiap selesai menulis atau mengubah kode, jalankan perintah berikut di terminal sebelum menandai tugas selesai:
- **Type Check:** `mypy src/`
- **Linter & Formatter:** `ruff check src/ tests/`
- **Unit Testing:** `pytest tests/ -v`

## 6. Larangan Keras (Do's & Don'ts)
- **DILARANG** menjalankan inferensi MediaPipe atau kueri tulis SQLite di dalam *Main Thread (GUI)*. Semua komunikasi ke GUI wajib menggunakan `QtCore.Signal`.
- **DILARANG** melakukan rekalibrasi otomatis secara diam-diam (*silent auto-recalibration*) di tengah sesi. Jika terdeteksi *setup drift* selama $\ge 10\text{ detik}$, alihkan status ke `NEEDS_RECALIBRATION`.
- **DILARANG** menahan keluarnya status pelanggaran (`v = False`) menggunakan *velocity gate*. Gerbang kecepatan hanya menahan masuknya pelanggaran baru (`v = True`).
- **DILARANG** menyimpan gambar atau video pengguna ke *disk* atau mengirim telemetri ke jaringan eksternal.