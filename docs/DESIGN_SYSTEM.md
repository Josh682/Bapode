# DESIGN_SYSTEM.md — Aturan UI/UX, Non-Activating HUD, & Integrasi OS

## 1. Prinsip Utama UX: Non-Intrusive & Anti-Focus-Stealing
Pengguna aplikasi ini sedang mengetik kode, menulis dokumen, atau mengikuti rapat. Munculnya peringatan postur **tidak boleh sekalipun mencuri fokus ketikan (*keyboard focus*)**, tidak boleh memblokir klik tetikus, dan tidak boleh terlihat oleh orang lain saat pengguna membagikan layar (*screen sharing*).

## 2. Spesifikasi Teknis Jendela HUD Overlay (`src/ui/overlay.py`)
1. **Qt Window Flags & Attributes Wajib:**
   - Flags: `Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool | Qt.WindowTransparentForInput | Qt.WindowDoesNotAcceptFocus`
   - Attributes: `Qt.WA_ShowWithoutActivating = True`, `Qt.WA_TranslucentBackground = True`
2. **Perilaku Spesifik macOS (`PyObjC`):**
   - Setel `NSWindow.setCollectionBehavior_()` dengan kombinasi `NSWindowCollectionBehaviorCanJoinAllSpaces | NSWindowCollectionBehaviorFullScreenAuxiliary | NSWindowCollectionBehaviorIgnoresCycle`.
3. **Proteksi Privasi Saat *Screen Sharing* (Wajib):**
   - **Windows:** Panggil Win32 API `SetWindowDisplayAffinity(hwnd, WDA_EXCLUDEFROMCAPTURE)` (`0x00000011`).
   - **macOS:** Panggil `ns_window.setSharingType_(NSWindowSharingNone)` (`0`).
   - *Efek:* Jendela *overlay* tetap terlihat oleh mata pengguna di monitor fisik, tetapi otomatis transparan/tidak terekam oleh Zoom, Google Meet, Microsoft Teams, atau OBS saat pengguna melakukan *Share Entire Screen*.

## 3. Pemetaan Visual Status (`UIIntrusivenessLevel`)

| Level State | Indikator System Tray | Perilaku HUD Overlay | Warna Aksen (Hex) |
| :--- | :--- | :--- | :--- |
| `SILENT_OK` | Lingkaran Hijau Solid | Tersembunyi (`hide()`) | `#10B981` (Emerald 500) |
| `AMBIENT_WARN` | Lingkaran Kuning Amber | Tersembunyi (`hide()`) | `#F59E0B` (Amber 500) |
| `ACTIVE_OVERLAY` | Lingkaran Oranye/Merah | *Vignette* halus atau *pill badge* di tepi atas layar beserta ikon arah koreksi postur | `#EF4444` (Red 500) |
| `SUPPRESSED_COOLDOWN` | Lingkaran Oranye Redup | Tersembunyi (menunggu jeda $30\text{ detik}$ selesai) | `#F97316` (Orange 500) |
| `MICROBREAK_PROMPT` | Ikon Peregangan Biru | Kartu mengambang di sudut layar dengan penghitung mundur $60\text{ detik}$ | `#3B82F6` (Blue 500) |
| `NEEDS_RECALIBRATION` | Ikon Target / Kalibrasi | *Badge* pasif kecil selama $5\text{ detik}$, lalu hanya tampil di *System Tray* | `#8B5CF6` (Violet 500) |

## 4. Desain Alur Kalibrasi Terpandu (3 Detik)
- Menampilkan pratinjau *webcam* dengan siluet pemandu bahu dan kepala di tengah layar.
- Menampilkan indikator kelayakan *real-time* sebelum dan selama $3\text{ detik}$ ($15\text{ sampel}$):
  - Jika sudut kepala/bahu miring atau rasio leher-bahu di luar $[0.35,\; 0.75]$, bingkai pemandu berwarna kuning dengan teks instruksi spesifik (misal: *"Luruskan pandangan ke tengah layar"*).
  - Jika simpangan baku *noise* $4\hat{\sigma}_{i,0} \gt 0.8 \cdot \tau_{\text{default-enter}, i}$, tampilkan peringatan: *"Pencahayaan terlalu redup atau posisi belum stabil"*.

## 5. Aturan Visualisasi Dashboard Statistik (`src/ui/dashboard.py`)
1. **Pelaporan Jujur Terhadap Oklusi:**
   Tampilkan selalu dua angka kepatuhan berdampingan:
   - **Rasio Kepatuhan Terukur (`reference_adherence_ratio`):** Persentase waktu duduk baik dari total waktu wajah & bahu terlihat (`adherent / measurable`).
   - **Batas Konservatif (`adherence_conservative_bound`):** Persentase waktu duduk baik dengan mengasumsikan seluruh waktu oklusi sebagai buruk (`adherent / (measurable + occluded)`), dilengkapi indikator persentase `observability_coverage`.
2. **Grafik Tren Bebas Dilusi:**
   Saat memvisualisasikan tingkat keparahan pelanggaran dari tabel `session_telemetry_buckets`, plot nilai `mean_violating_*` dan `p95_*` agar grafik tidak terdilusi menuju angka nol oleh periode ketika pengguna duduk tegak.