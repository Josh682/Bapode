# 04 — Kontrak Telemetri, Interval RLE, & Skema SQLite v3.0

> **Catatan Sumber Kebenaran (*Source of Truth*):** Saat kode aplikasi diinisialisasi, definisi DDL di bawah ini wajib diekstrak ke dalam file `src/db/schema.sql`. Setiap perubahan kolom di masa depan harus dilakukan langsung pada `src/db/schema.sql`.

## 1. Prinsip Statistik & Integritas Telemetri
1. **Bebas Dilusi Periode Tegak (*No Upright Dilution*):**
   Pada agregasi *bucket* 5 menit (`session_telemetry_buckets`), rata-rata tingkat keparahan deviasi (`mean_violating_*`) **hanya dihitung dari sampel ketika metrik tersebut sedang melanggar ($V_i == 1$)**, didampingi oleh nilai persentil ke-95 (`p95_*`) dari seluruh sampel `MEASURABLE` di dalam *bucket*.Jika tidak ada pelanggaran selama 5 menit tersebut, kolom `mean_violating_*` bernilai `NULL`.
2. **Batas Kepatuhan Terhadap Oklusi (*Observability & Adherence Bounds*):**
   Pada tabel `monitoring_sessions`, sistem wajib memperbarui tiga rasio utama:
   - $$\text{observability-coverage} = \frac{T_{\text{measurable}}}{T_{\text{measurable}} + T_{\text{occluded-lower}} + T_{\text{occluded-upper}}}$$
   - $$\text{reference-adherence-ratio} = \frac{T_{\text{adherent}}}{T_{\text{measurable}}}$$
   - $$\text{adherence-conservative-bound} = \frac{T_{\text{adherent}}}{T_{\text{measurable}} + T_{\text{occluded-lower}} + T_{\text{occluded-upper}}}$$
3. **Run-Length Encoding (RLE) pada `posture_state_intervals`:**
   Jangan menyimpan baris *database* setiap *frame* ($5\text{ FPS}$). Simpan satu baris interval setiap kali terjadi perubahan kombinasi `(visibility_state, ui_level, v_fhp, v_neck_comp, v_slump, v_gaze_pitch, v_tilt)` atau perbarui `ended_at` dan `duration_seconds` dari interval yang sedang berjalan setiap siklus *flush* $15\text{ detik}$.

---

## 2. Definisi Skema SQLite v3.0 (`src/db/schema.sql`)

```sql
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

-- 1. TABEL PROFIL KALIBRASI (Mendukung multi-kalibrasi dalam satu sesi)
CREATE TABLE IF NOT EXISTS calibration_profiles (
    profile_id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    base_neck_ort_ratio REAL NOT NULL,
    base_eye_sh_ratio REAL NOT NULL,
    base_sh_width_px REAL NOT NULL,
    base_sh_y_mid REAL NOT NULL,
    base_sh_angle_deg REAL NOT NULL,
    base_head_pitch_deg REAL NOT NULL,
    base_head_roll_deg REAL NOT NULL,
    -- Simpangan baku noise awal (MAD-based) untuk perhitungan threshold adaptif:
    sigma_fhp REAL NOT NULL,
    sigma_neck REAL NOT NULL,
    sigma_slump REAL NOT NULL,
    sigma_pitch REAL NOT NULL,
    sigma_tilt REAL NOT NULL
);

-- 2. TABEL SESI PEMANTAUAN HARIAN
CREATE TABLE IF NOT EXISTS monitoring_sessions (
    session_id INTEGER PRIMARY KEY AUTOINCREMENT,
    start_time DATETIME NOT NULL,
    end_time DATETIME,
    measurable_seconds REAL DEFAULT 0.0,            -- Waktu valid (Wajah + Bahu terlihat)
    adherent_seconds REAL DEFAULT 0.0,              -- Waktu di mana kelima V_i(t) == 0
    occluded_lower_seconds REAL DEFAULT 0.0,        -- Wajah terlihat, bahu hilang
    occluded_upper_seconds REAL DEFAULT 0.0,        -- Bahu terlihat, wajah hilang
    absent_seconds REAL DEFAULT 0.0,                -- Pengguna pergi dari meja
    paused_or_drift_seconds REAL DEFAULT 0.0,       -- Waktu snooze / menunggu rekalibrasi
    -- Durasi pelanggaran per metrik (dapat tumpang tindih jika pelanggaran ganda):
    fhp_violation_seconds REAL DEFAULT 0.0,
    neck_comp_violation_seconds REAL DEFAULT 0.0,
    slump_violation_seconds REAL DEFAULT 0.0,
    gaze_pitch_violation_seconds REAL DEFAULT 0.0,
    tilt_violation_seconds REAL DEFAULT 0.0,
    -- Ringkasan Kepatuhan & Cakupan Pengamatan:
    observability_coverage REAL DEFAULT 1.0,        -- measurable / (measurable + occluded)
    reference_adherence_ratio REAL DEFAULT 1.0,     -- adherent / measurable
    adherence_conservative_bound REAL DEFAULT 1.0,  -- adherent / (measurable + occluded)
    total_alerts_shown INTEGER DEFAULT 0,
    total_microbreaks_prompted INTEGER DEFAULT 0,
    mean_alert_dismissal_seconds REAL               -- Rata-rata waktu sejak overlay muncul hingga V_i == 0
);

-- 3. TABEL INTERVAL STATE (Run-Length Encoding Penuh)
CREATE TABLE IF NOT EXISTS posture_state_intervals (
    interval_id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL,
    profile_id INTEGER NOT NULL,
    visibility_state TEXT CHECK(visibility_state IN (
        'MEASURABLE', 'OCCLUDED_LOWER', 'OCCLUDED_UPPER', 'ABSENT', 'PAUSED', 'NEEDS_RECALIBRATION'
    )) NOT NULL,
    ui_level TEXT CHECK(ui_level IN (
        'SILENT_OK', 'AMBIENT_WARN', 'ACTIVE_OVERLAY', 'SUPPRESSED_COOLDOWN', 'MICROBREAK_PROMPT'
    )) NOT NULL,
    started_at DATETIME NOT NULL,
    ended_at DATETIME NOT NULL,
    duration_seconds REAL NOT NULL,
    -- Flag biner pelanggaran simultan pada interval tersebut:
    v_fhp INTEGER DEFAULT 0,
    v_neck_comp INTEGER DEFAULT 0,
    v_slump INTEGER DEFAULT 0,
    v_gaze_pitch INTEGER DEFAULT 0,
    v_tilt INTEGER DEFAULT 0,
    alert_dismissal_sec REAL,                       -- Diisi jika ui_level == ACTIVE_OVERLAY
    FOREIGN KEY (session_id) REFERENCES monitoring_sessions(session_id) ON DELETE CASCADE,
    FOREIGN KEY (profile_id) REFERENCES calibration_profiles(profile_id)
);

-- 4. TABEL AGREGASI TELEMETRI 5 MENIT (Bebas Dilusi Periode Tegak)
CREATE TABLE IF NOT EXISTS session_telemetry_buckets (
    bucket_id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL,
    profile_id INTEGER NOT NULL,
    bucket_timestamp DATETIME NOT NULL,
    measurable_sec_in_bucket REAL NOT NULL,
    occluded_sec_in_bucket REAL NOT NULL,
    duty_cycle_any_violation REAL NOT NULL,         -- Proporsi waktu melanggar (0.0 - 1.0) dari waktu measurable
    -- Rata-rata deviasi HANYA saat melanggar (V_i == 1) + Persentil ke-95 keseluruhan:
    mean_violating_fhp REAL,
    p95_fhp_dev REAL NOT NULL,
    mean_violating_neck REAL,
    p95_neck_dev REAL NOT NULL,
    mean_violating_slump REAL,
    p95_slump_dev REAL NOT NULL,
    mean_violating_pitch_deg REAL,
    p95_pitch_deg REAL NOT NULL,
    mean_violating_tilt_deg REAL,
    p95_tilt_deg REAL NOT NULL,
    FOREIGN KEY (session_id) REFERENCES monitoring_sessions(session_id) ON DELETE CASCADE,
    FOREIGN KEY (profile_id) REFERENCES calibration_profiles(profile_id)
);

CREATE INDEX IF NOT EXISTS idx_intervals_session ON posture_state_intervals(session_id, started_at);
CREATE INDEX IF NOT EXISTS idx_buckets_session ON session_telemetry_buckets(session_id, bucket_timestamp);
```

---

## 3. Target Parameter & Benchmarking Harian (Acuan Pengujian)
Saat menyetel konstanta *default* di dalam kode dan menulis pengujian otomatis:
- **Beban Perangkat:** Konsumsi CPU rata-rata tetap ringan pada mode konstan $5\text{ FPS}$ ($\Delta t \approx 200\text{ ms}$) selama sesi 2 jam.
- **Target Kualitas UX:** *False Alarm Rate* (FAR) $\lt 0.5\text{ kali per jam}$ selama aktivitas kerja normal (mengetik, minum, menoleh ke monitor kedua), dan latensi padam *overlay* $\le 1.2\text{ detik}$ saat pengguna kembali duduk tegak.