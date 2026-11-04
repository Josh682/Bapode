# 02 — Pemrosesan Sinyal, Velocity Gate, & Logika IssueTracker

## 1. Pipeline Pra-Pemrosesan Sinyal Sadar-$\Delta t$

### 1.1. Sliding Median-3
Setiap metrik mentah $d_i(t_k)$ terlebih dahulu melewati jendela median 3 sampel ($600\text{ ms}$ pada $5\text{ FPS}$) untuk menghasilkan $d_{i,k}^{\text{med}}$. Langkah ini membuang *impulse outlier* 1 sampel ($\le 200\text{ ms}$) akibat kedipan detektor.

### 1.2. Filter EMA Asimetris Berbasis $\Delta t$ Nyata
Menggunakan konstanta waktu naik $\tau_{\text{attack}} = 1.0\text{ s}$ (meredam goyangan sesaat ketika mulai melanggar) dan konstanta waktu turun $\tau_{\text{release}} = 0.3\text{ s}$ (memastikan pemadaman *alert* berlangsung cepat ketika pengguna kembali duduk tegak):

$$\tau = \begin{cases} \tau_{\text{attack}} & \text{jika } d_{i,k}^{\text{med}} \ge \hat{d}_{i,k-1} \\ \tau_{\text{release}} & \text{jika } d_{i,k}^{\text{med}} \lt \hat{d}_{i,k-1} \end{cases}, \quad \alpha_k = 1 - \exp\left(-\frac{\Delta t_k}{\tau}\right)$$

$$\hat{d}_{i,k} = \alpha_k d_{i,k}^{\text{med}} + (1 - \alpha_k)\hat{d}_{i,k-1}$$

*Target Latensi Padam (Dismissal Latency):* Rata-rata $100\text{ ms}$ (*sampling*) $+ 200\text{ ms}$ (*group delay* Median-3) $+ 400\text{ ms}$ (peluruhan EMA) $\approx 0.7\text{--}1.2\text{ detik}$.

### 1.3. Detektor Gerakan Kuasi-Statis (*Normalized Velocity Signal*)
Kecepatan gerak dihitung dari perubahan koordinat bahu yang telah dihaluskan, dinormalisasi terhadap lebar bahu saat ini $W_{\text{sh}}(t_k)$ dan selang waktu nyata $\Delta t_k$:

$$v_{\text{norm}}(t_k) = \frac{1}{\Delta t_k W_{\text{sh}}(t_k)} \max\left(|\Delta \hat{Y}_{\text{sh\_mid}}|,\; |\Delta \hat{X}_{\text{sh\_mid}}|,\; |\Delta \hat{W}_{\text{sh}}|\right)$$

Status gerakan dinamis `moving = True` aktif apabila $v_{\text{norm}}(t_k) \gt v_{\text{thresh}}$ (nilai *default* $v_{\text{thresh}} = 0.15\text{ lebar-bahu/detik}$).

---

## 2. Spesifikasi `IssueTracker` per-Metrik ($i \in \{1..5\}$)

Setiap metrik memiliki *instance* `IssueTracker` independen dengan histeresis ambang masuk $\tau_{\text{enter}, i}$ dan ambang keluar $\tau_{\text{exit}, i}$ ($\tau_{\text{exit}, i} \lt \tau_{\text{enter}, i}$):

### 2.1. One-Way Velocity Gate dengan Batas `freeze_max_s = 3.0` Detik
- **Aturan Satu Arah:** Saat `moving == True`, pengguna mungkin sedang meraih barang di meja (jangan isi ember pelanggaran) atau sedang menegakkan badan dengan cepat untuk memperbaiki postur (jangan tahan pemadaman *alert*).
- Oleh karena itu, ketika `moving == True`:
  - Jika $\hat{d}_{i,k} \le \tau_{\text{exit}, i}$, status pelanggaran biner $V_i$ **langsung diizinkan turun menjadi `False`** tanpa hambatan.
  - Jika $\hat{d}_{i,k} \gt \tau_{\text{enter}, i}$, transisi menjadi `True` dan penambahan isi ember $D_i$ **dibekukan sementara**.
- **Anti-Freeze Timeout:** Akumulator durasi beku `freeze_elapsed_s` mencatat berapa lama gerbang menahan sinyal secara beruntun. Jika `moving == True` bertahan terus-menerus selama $\gt 3.0\text{ detik}$ (misalnya akibat *jitter* kamera di ruangan sangat gelap), gerbang kecepatan otomatis dilepas agar deteksi tidak membeku selamanya.

### 2.2. Akumulator Ember Debounce 15 Detik & Step-Down Floor (`d_floor = 9.0` s)
- **Pengisian & Pengosongan Ember ($D_i \in [0.0,\; 15.0]\text{ detik}$):**
  - Saat $V_i == \text{True}$ (dan tidak dibekukan *velocity gate*): $D_i(t_k) = \min(15.0,\; D_i(t_{k-1}) + \Delta t_k)$.
  - Saat $V_i == \text{False}$ dan `alert_active == False`: ember meluruh dengan laju $r_{\text{decay}}$ (misal $1.5\times \Delta t_k$) menuju $0.0$.
- **Pemicu Alert:** Ketika $D_i$ menyentuh $15.0\text{ detik}$, `alert_active` menjadi `True`.
- **Pemulihan dengan Step-Down ke `d_floor = 9.0` s:**
  Begitu pengguna menegakkan badan sehingga $V_i$ turun menjadi `False` saat `alert_active == True`:
  1. `alert_active` langsung menjadi `False` (mematikan *overlay* dalam $\le 1.2\text{ detik}$).
  2. Nilai ember $D_i$ **langsung dipangkas (*step-down*) menjadi $d_{\text{floor}} = 9.0\text{ detik}$** (bukan dikosongkan ke $0$, dan bukan dibiarkan di $15.0$).
  - *Rasional:* Memberi jendela aman $\approx 6\text{ detik}$ terhadap goyangan transien pasca-*alert*, sekaligus memastikan pengguna yang "curang" (tegak $1\text{ detik}$ lalu membungkuk lagi) akan terkena *alert* kembali dalam $\approx 7.4\text{ detik}$ tanpa risiko *deadlock*.

---

## 3. Pengontrol Cooldown UI & Eskalasi Micro-Break (Dual-Timescale)
Di atas kelima `IssueTracker`, pengontrol tingkat sesi mengatur `UIIntrusivenessLevel`:
1. **UI Cooldown (`cooldown_s = 30.0` detik):**
   Jika metrik ke-$i$ memicu `alert_active = True` tetapi *overlay* untuk metrik yang sama baru saja ditutup kurang dari $30.0\text{ detik}$ yang lalu, status UI masuk ke `SUPPRESSED_COOLDOWN` (pelanggaran tetap dicatat di database, tetapi *overlay* visual ditahan hingga masa *cooldown* habis).
2. **Eskalasi Micro-Break (Jendela 10 & 15 Menit):**
   Alihkan `UIIntrusivenessLevel` ke `MICROBREAK_PROMPT` (anjuran istirahat peregangan $60\text{ detik}$) apabila:
   - Sebuah metrik memicu *alert* sebanyak $\ge 3\text{ kali}$ dalam jendela bergulir $10\text{ menit}$, **ATAU**
   - *Duty cycle* pelanggaran gabungan dalam jendela $15\text{ menit}$ terakhir melampaui ambang batas paparan statis (misal $\gt 50\%$ dari waktu `MEASURABLE`).