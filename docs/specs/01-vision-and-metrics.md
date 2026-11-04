# 01 — Akuisisi Kamera, Fusi Model, & 5 Metrik Geometris

## 1. Akuisisi Kamera & Fusi Subjek Tunggal
1. **Akuisisi 5 FPS ($\Delta t \approx 200\text{ ms}$):**
   - *Thread 3* membaca *frame* kamera resolusi standar (misal $1280 \times 720$, $\text{HFOV} \approx 65^\circ$, panjang fokus efektif $f = \frac{640}{\tan(32.5^\circ)} \approx 1005\text{ px}$).
   - Setiap *frame* diberi cap waktu `timestamp_s = time.monotonic()` tepat setelah diambil dan dimasukkan ke `latest_frame_queue` berukuran $1$.
2. **Ekstraksi Landmark Berurutan:**
   - **Tahap 1 (`MediaPipe Pose Landmarker Lite`):** Mengekstrak koordinat bahu kiri ($P_{LS}$), bahu kanan ($P_{RS}$), telinga kiri/kanan ($P_{LE}, P_{RE}$), dan hidung ($P_{\text{nose}}$).
   - **Tahap 2 (`MediaPipe Face Landmarker`):** Mengekstrak matriks transformasi 3D wajah untuk memperoleh sudut Euler ($\text{pitch}_{\text{head}}, \text{yaw}_{\text{head}}, \text{roll}_{\text{head}}$) serta jarak Euclidean biokular antara sudut mata luar kiri dan kanan ($W_{\text{eye}}$).
3. **Asosiasi Multi-Orang (Lingkungan Kafe/Kantor):**
   Apabila terdeteksi lebih dari satu orang di dalam *frame*:
   - Pilih wajah dengan luas *bounding box* terbesar yang berada paling dekat dengan pusat horizontal saat kalibrasi.
   - Verifikasi bahwa titik hidung $P_{\text{nose}}$ dari `PoseLandmarker` berada di dalam *bounding box* wajah tersebut dengan jarak Euclidean ke pusat wajah $\lt 0.25 \cdot W_{\text{sh}}$. Jika gagal, anggap *frame* tidak valid untuk menghindari pencampuran wajah pengguna dengan bahu orang yang lewat di belakang.

---

## 2. Geometri Dasar & Kompensasi Rotasi 3D
Pada citra kamera depan, bahu kiri anatomis berada di sisi kanan gambar ($X_{LS} \gt X_{RS}$). Definisikan komponen dasar pada waktu $t$:

$$\Delta X_{\text{sh}} = X_{LS} - X_{RS}, \quad \Delta Y_{\text{sh}} = Y_{LS} - Y_{RS}$$

$$\theta_{\text{sh}}(t) = \text{atan2}(\Delta Y_{\text{sh}}, \Delta X_{\text{sh}}) \cdot \frac{180^\circ}{\pi}, \quad W_{\text{sh}}(t) = \sqrt{\Delta X_{\text{sh}}^2 + \Delta Y_{\text{sh}}^2}$$

Titik tengah bahu $M_{\text{sh}} = (X_{\text{sh\_mid}}, Y_{\text{sh\_mid}})$ dan titik tengah telinga $M_{\text{ear}} = (X_{\text{ear\_mid}}, Y_{\text{ear\_mid}})$ digunakan untuk menghitung panjang proyeksi ortogonal leher terhadap garis bahu (kompensasi *roll* tubuh):

$$L_{\text{neck\_ort}}(t) = -(X_{\text{ear\_mid}} - X_{\text{sh\_mid}})\sin\theta_{\text{sh}}(t) - (Y_{\text{ear\_mid}} - Y_{\text{sh\_mid}})\cos\theta_{\text{sh}}(t)$$

Lebar biokular mata dikompensasi terhadap rotasi tolehan kepala ($\text{yaw}_{\text{head}}$):

$$W_{\text{eye\_corr}}(t) = \frac{W_{\text{eye}}(t)}{\cos(\text{yaw}_{\text{head}}(t))}$$

> **Gerbang Yaw & Rotasi Torso:** Evaluasi metrik skala kepala (`HEAD_FORWARD_SCALE`) ditangguhkan sementara (nilai deviasi dibekukan/tidak mengisi ember) apabila $|\text{yaw}_{\text{head}}(t)| \gt 15^\circ$ atau ketika laju perubahan lebar bahu $|\dot{W}_{\text{sh}}|$ mengindikasikan pengguna sedang memutar kursi/badan ke monitor samping.

---

## 3. Perumusan Matematis 5 Metrik Deviasi Satu Arah $d_i(t)$
Setiap metrik $d_i(t)$ dirancang **satu arah** di mana nilai positif ($d_i(t) \gt 0$) selalu bermakna postur memburuk menjauhi referensi kalibrasi (*baseline*).

### 1. `HEAD_FORWARD_SCALE` ($d_{\text{fhp}}$)
Mengukur proporsi majunya kepala mendahului bahu ke arah layar (mengatasi fenomena *kompensasi oksipital* di mana leher bawah fleksi tetapi kepala mendongak menatap layar sehingga $\Delta\text{pitch} \approx 0^\circ$):

$$d_{\text{fhp}}(t) = \frac{W_{\text{eye\_corr}}(t) / W_{\text{sh}}(t)}{W_{\text{eye\_corr, base}} / W_{\text{sh, base}}} - 1.0$$

### 2. `TORSO_NECK_COMPRESSION` ($d_{\text{neck}}$)
Mengukur penyusutan tinggi proyeksi ortogonal leher relatif terhadap lebar bahu (akibat bahu terangkat tegang atau punggung atas membungkuk):

$$d_{\text{neck}}(t) = 1.0 - \frac{L_{\text{neck\_ort}}(t) / W_{\text{sh}}(t)}{L_{\text{neck\_ort, base}} / W_{\text{sh, base}}}$$

### 3. `SHOULDER_VERTICAL_DROP` ($d_{\text{slump}}$)
Mengukur penurunan posisi vertikal bahu di layar akibat badan merosot di kursi (*slumping*), dengan koreksi perspektif optik kamera (karena kamera berada di atas layar pada pusat vertikal $Y_c = H/2$, mendekat ke layar dengan skala $S_w$ secara alami menurunkan koordinat $Y_{\text{sh}}$):

$$S_w(t) = \frac{W_{\text{sh}}(t)}{W_{\text{sh, base}}}$$

$$Y_{\text{exp}}(t) = Y_c + S_w(t)\cdot(Y_{\text{sh, base}} - Y_c)$$

$$d_{\text{slump}}(t) = \frac{Y_{\text{sh\_mid}}(t) - Y_{\text{exp}}(t)}{W_{\text{sh}}(t)}$$

### 4. `GAZE_DOWN_PITCH` ($d_{\text{pitch}}$)
Mengukur sudut anggukan kepala turun dalam satuan derajat (menunduk berkepanjangan menatap *keyboard*, dokumen meja, atau ponsel tanpa memajukan kepala ke layar):

$$d_{\text{pitch}}(t) = \max\left(0.0,\; \text{pitch}_{\text{head, base}} - \text{pitch}_{\text{head}}(t)\right)$$

### 5. `CORONAL_ASYMMETRY` ($d_{\text{tilt}}$)
Mengukur kemiringan lateral garis bahu atau kepala dalam derajat terhadap posisi referensi (bertumpu pada satu siku atau memiringkan kepala):

$$d_{\text{tilt}}(t) = \max\left(|\theta_{\text{sh}}(t) - \theta_{\text{sh, base}}|,\; |\text{roll}_{\text{head}}(t) - \text{roll}_{\text{head, base}}|\right)$$