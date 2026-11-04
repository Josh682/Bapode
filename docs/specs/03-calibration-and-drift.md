# 03 — Kalibrasi Terpandu, Adaptasi Noise MAD, & Deteksi Setup Drift

## 1. Kalibrasi Terpandu 3 Detik & *Plausibility Gate*
Saat sesi dimulai atau pengguna menekan tombol **Recalibrate**, sistem merekam postur duduk tegak selama $3\text{ detik}$ ($15\text{ sampel}$ berturut-turut pada $5\text{ FPS}$ dalam kondisi `VisibilityState == MEASURABLE`).

### Syarat Kelulusan *Calibration Plausibility Gate*:
1. **Batas Orientasi Kepala & Bahu:**
   Nilai median selama $15\text{ sampel}$ wajib memenuhi:
   - $|\text{yaw}_{\text{head}}| \le 8^\circ$
   - $|\text{roll}_{\text{head}}| \le 5^\circ$
   - $|\theta_{\text{sh}}| \le 6^\circ$
2. **Kewajaran Anatomi Proyeksi 2D:**
   Rasio panjang ortogonal leher terhadap lebar bahu wajib berada di dalam rentang:
   $$0.35 \le \frac{L_{\text{neck\_ort, base}}}{W_{\text{sh, base}}} \le 0.75$$
3. **Estimasi Noise Baseline Berbasis MAD ($\hat{\sigma}_{i,0}$):**
   Untuk setiap metrik $i \in \{1..5\}$, hitung *Median Absolute Deviation* (MAD) dari ke-15 sampel kalibrasi dan konversikan ke estimasi simpangan baku yang tahan *outlier*:
   $$\hat{\sigma}_{i,0} = 1.4826 \cdot \text{median}\left(|d_{i,k} - \text{median}(d_i)|\right)$$
   - **Gerbang Kualitas Cahaya/Stabilitas:** Jika untuk salah satu metrik berlaku $4\hat{\sigma}_{i,0} \gt 0.80 \cdot \tau_{\text{default\_enter}, i}$, tolak kalibrasi dan tampilkan pesan: *"Pencahayaan terlalu redup atau posisi belum stabil"*.

---

## 2. Estimasi Noise Berjalan (*Running Quiet-Period Noise Floor*)
Karena $15\text{ sampel}$ kalibrasi memiliki galat taksiran relatif $\approx 30\%$ dan direkam saat pengguna sengaja menahan badan tetap kaku, sistem memperbarui estimasi *noise* $\hat{\sigma}_i(t)$ secara perlahan selama sesi berlangsung:
1. **Kondisi Pembaruan (*Quiet Period*):**
   Sampel hanya dimasukkan ke penaksir *noise* berjalan apabila kelima metrik sedang berada di zona aman ($\forall i, V_i == 0$) **dan** `moving == False`.
2. **Penyesuaian Ambang Satu Arah (Hanya Menaikkan Ambang):**
   Ambang masuk efektif untuk metrik ke-$i$ dihitung sebagai:
   $$\tau_{\text{enter}, i}(t) = \min\left(\tau_{\text{cap}, i},\; \max\left(\tau_{\text{default\_enter}, i},\; 4\hat{\sigma}_i(t)\right)\right)$$
   - Estimasi *noise* berjalan **tidak pernah menurunkan** ambang di bawah $\tau_{\text{default\_enter}, i}$.
   - Jika $\hat{\sigma}_i(t)$ meningkat hingga membuat $\tau_{\text{enter}, i}(t)$ menyentuh plafon maksimum $\tau_{\text{cap}, i}$ (contoh: lampu ruangan dimatikan pada malam hari sehingga titik *landmark* MediaPipe bergetar hebat), munculkan indikator non-intrusif di UI: **"Kualitas Cahaya Kamera Buruk — Akurasi Menurun"**.

---

## 3. Deteksi Perubahan Sudut Layar / Posisi Kursi (*Setup Drift*)
Sistem **dilarang melakukan rekalibrasi otomatis secara diam-diam (*no silent auto-recalibration*)**. Sebagai gantinya, sistem memindahkan `OperatingMode` ke `NEEDS_RECALIBRATION` apabila selama $\ge 10\text{ detik}$ berturut-turut terdeteksi salah satu kondisi berikut:

1. **Pergeseran Jarak Kursi Permanen:**
   Rasio lebar bahu $S_w(t) = W_{\text{sh}}(t) / W_{\text{sh, base}}$ berada di luar rentang $[0.78,\; 1.30]$ sementara metrik proporsi kepala maju $d_{\text{fhp}}(t)$ berada dalam batas normal (pengguna memundurkan atau memajukan kursi secara permanen tanpa membungkuk).
2. **Perubahan Sudut Engsel Layar Laptop (*Screen Hinge Drift*):**
   Terjadi pergeseran serempak pada koordinat vertikal bahu $Y_{\text{sh\_mid}}$, koordinat vertikal mata $Y_{\text{eye\_mid}}$, dan sudut anggukan $\text{pitch}_{\text{head}}$ dengan arah dan besaran yang konsisten terhadap rotasi kemiringan kamera laptop (dapat diverifikasi silang dengan pergeseran *optical flow* titik latar belakang statis jika tekstur latar mencukupi).

### Perilaku Saat `NEEDS_RECALIBRATION`:
- Seluruh akumulator `IssueTracker` dibekukan sementara dan waktu yang berlalu dicatat ke dalam `paused_or_drift_seconds` (serta interval `visibility_state = 'NEEDS_RECALIBRATION'`).
- Indikator *System Tray* berubah menjadi ikon kalibrasi hingga pengguna menekan tombol **"Kalibrasi Ulang (3 dtk)"**.