# 🤖 Yapay Zekâ Destekli Otonom Kariyer Asistanı

> **Aday Profili:** Muhammed Enes BAL — Bilgisayar Mühendisi | Robotik, Otonom Sistemler, ROS 2, SLAM, Bilgisayarlı Görü ve Gömülü Yapay Zekâ Mühendisi

Bu proje, otonom insansız araçlardaki (**UAV / AUV**) **"Algılama (Perception) $\rightarrow$ Karar Verme (Decision) $\rightarrow$ Planlama (Planning) $\rightarrow$ Eylem (Actuation)"** döngüsünü mühendislik iş ve Ar-Ge başvuru sürecine uyarlayan **%100 açık kaynaklı ve sıfır maliyetli** otonom bir kariyer asistanıdır.

---

## 🏗️ Sistem Mimarisi (6 Katmanlı Otonom Akış)

```text
┌─────────────────────────────────────────────────────────────────────────┐
│ 1. ALGILAMA VE BİLGİ TABANI (RAG - src/rag/)                            │
│ [TR/EN CV & Portfolyo PDF] ──▶ [PyMuPDF] ──▶ [Chunker] ──▶ [ChromaDB]   │
└────────────────────────────────────┬────────────────────────────────────┘
                                     ▼
┌─────────────────────────────────────────────────────────────────────────┐
│ 2. AKILLI KEŞİF VE KAZIMA (SCRAPER - src/scraper/)                      │
│ [Dork Sorgu Üretici (-site:)] ──▶ [DuckDuckGo] ──▶ [Anti-Aggregator]    │
│                                                           │             │
│ [3 Katmanlı E-posta Kazıyıcı (support@ Filtresi)] ◀── [Playwright JS]   │
└────────────────────────────────────┬────────────────────────────────────┘
                                     ▼
┌─────────────────────────────────────────────────────────────────────────┐
│ 3. KARAR MEKANİZMASI (DECISION ENGINE - src/filter/)                    │
│ • 18+ ATS (Greenhouse, Lever, Workday vb.) var mı? ──▶ [MANUAL_ATS]     │
│ • 1. Öncelik İK maili (kariyer@, hr@, ik@) var mı? ──▶ [SEND_EMAIL]     │
│ • 2. Öncelik İletişim (info@, iletisim@) var mı?   ──▶ [LOW_PRIORITY]   │
└────────────────────────────────────┬────────────────────────────────────┘
                                     ▼
┌─────────────────────────────────────────────────────────────────────────┐
│ 4. ÇİFT DİLLİ İÇERİK ÜRETİMİ (GENERATOR - src/generator/)               │
│ [Şirket Misyonu] + [RAG Projeleri] + [TR/EN Link Şablonu]               │
│     ──▶ [Gemini 2.5 Flash + Halüsinasyon Doğrulayıcı + Statik Fallback] │
└────────────────────────────────────┬────────────────────────────────────┘
                                     ▼
┌─────────────────────────────────────────────────────────────────────────┐
│ 5. HAFIZA VE GÜVENLİ GÖNDERİM (DATABASE & MAILER - src/database, mailer)│
│ [SQLite: E-posta & Domain 30 Gün Kuralı + Kota] ──▶ [SMTP_SSL / DRY-RUN]│
└────────────────────────────────────┬────────────────────────────────────┘
                                     ▼
┌─────────────────────────────────────────────────────────────────────────┐
│ 6. ORKESTRASYON VE ZENGİN CLI (src/orchestrator/, src/cli/, main.py)    │
│ [Şirket Bazlı Hata İzolasyonu] + [Rich Tabloları] + [Günlük Zamanlama]  │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## 🛡️ 3 Kritik Mühendislik Koruması (Kırmızı Çizgiler)

1. **Anti-Aggregator (Rehber ve Liste Sitesi) Koruması:**
   - `clutch.co`, `f6s.com`, `europages`, `linkedin.com`, `kariyer.net`, `glassdoor.com`, `indeed.com`, `sortlist.com`, `yellowpages.com` gibi 35+ dizin sitesi ve *"Top 10 Robotics Companies"* / *"En İyi 15 Şirket"* tarzı liste yazıları hem Dork sorgusunda (`-site:`) hem de `src/scraper/domain_filter.py` katmanında (alt alan adları dahil) **%100 elenir**.
2. **`support@` Kara Listesi ve 3 Katmanlı E-posta Sınıflandırması:**
   - **Katman 1 (`BLACKLISTED` — Asla Gönderilmez):** `support@`, `help@`, `destek@`, `sales@`, `satis@`, `noreply@`, `no-reply@`, `admin@`, `billing@`, `kvkk@`, `privacy@`, `security@` ve `logo@2x.png` gibi statik dosya eşleşmeleri anında imha edilir.
   - **Katman 2 (`HIGH_PRIORITY` — Birincil Hedef):** `kariyer@`, `ik@`, `hr@`, `jobs@`, `talent@`, `recruiting@`, `insankaynaklari@`, `basvuru@`.
   - **Katman 3 (`LOW_PRIORITY` — İkincil Hedef):** `info@`, `iletisim@`, `contact@`, `hello@`, `merhaba@`.
3. **E-posta + Domain Bazlı 30 Gün Kuralı ve Sıfır Dosya Eki (No-Attachment):**
   - `ik@sirket.com.tr` adresine gerçek mail (`SENT`) atıldıktan sonra 30 gün boyunca aynı şirketin farklı bir adresine (`kariyer@sirket.com.tr` veya `info@sirket.com.tr`) mail atılması **domain bazlı 30 gün kuralı** ile engellenir.
   - Spam filtrelerine takılmamak için fiziksel PDF eki (`attachment`) kullanılmaz; CV ve Portfolyo bağlantıları çevrimiçi URL olarak metne yerleştirilir.

---

## ⚙️ Kurulum ve Ortam İzolasyonu (ROS 2 Çakışma Korumalı)

### 1. Sanal Ortam (`.venv`) ve Paket Kurulumu
```bash
python -m venv .venv

# Windows (PowerShell):
.\.venv\Scripts\pip.exe install pytest pydantic pydantic-settings pyyaml python-dotenv pymupdf chromadb sentence-transformers duckduckgo-search playwright beautifulsoup4 lxml rich
.\.venv\Scripts\playwright.exe install chromium

# Linux / WSL / Git Bash:
./.venv/bin/pip install pytest pydantic pydantic-settings pyyaml python-dotenv pymupdf chromadb sentence-transformers duckduckgo-search playwright beautifulsoup4 lxml rich
./.venv/bin/playwright install chromium
```

### 2. `.env` Yapılandırması
`.env.example` dosyasını `.env` adıyla kopyalayın ve kendi bağlantılarınızı girin:
```bash
cp .env.example .env
```
- `GEMINI_API_KEY`: Google AI Studio ücretsiz API anahtarınız (Boş bırakılırsa sistem otomatik olarak profesyonel çift dilli statik şablona geçer).
- `SMTP_EMAIL` & `SMTP_PASSWORD`: Gmail adresiniz ve 16 haneli **Gmail Uygulama Şifresi (App Password)**.
- `CV_URL_TR`, `CV_URL_EN`, `PORTFOLIO_URL`, `GITHUB_URL`, `LINKEDIN_URL`: E-posta gövdesine eklenecek çevrimiçi bağlantılarınız.

---

## 🚀 Kullanım Komutları (CLI)

### 1. CV ve Portfolyo PDF Belgelerini ChromaDB'ye İndeksleme
`cv/` ve `portfolio/` klasörlerindeki PDF belgelerinizi çok dilli vektör veritabanına kaydetmek için:
```bash
python main.py --ingest-pdfs
```

### 2. Sıfır Riskli Simülasyon Modu (`--dry-run` — Varsayılan)
Hiçbir gerçek SMTP bağlantısı açmadan tüm keşif, filtreleme, karar ağacı ve e-posta üretim sürecini simüle eder:
```bash
python main.py --dry-run --limit 5 --keywords "ROS 2" "SLAM" "Otonom İHA"
```

### 3. Canlı Gönderim Modu (`--no-dry-run`)
Gmail SMTP (`SSL/TLS`) üzerinden günlük kota ve 30 gün kuralı denetimiyle gerçek başvuru gönderir:
```bash
python main.py --no-dry-run --limit 5 --keywords "ROS 2" "SLAM"
```

### 4. Veritabanı İstatistikleri ve Manuel ATS Kuyruğu
```bash
# Genel başvuru istatistiklerini görüntüle:
python main.py --stats

# Greenhouse, Lever, Workday vb. ATS tespit edilen şirketlerin başvuru linklerini listele:
python main.py --manual-list
```

### 5. Günlük Otonom Zamanlayıcı (`--schedule`)
`config/settings.yaml` içindeki `app.schedule_time` (varsayılan `"09:00"`) saatinde her gün otomatik çalışır:
```bash
python main.py --schedule
```

---

## 🧪 Testleri Çalıştırma

ROS 2 veya global Python eklentilerinden tamamen izole olarak tüm birim ve entegrasyon testlerini çalıştırmak için:

```powershell
# Windows PowerShell:
$env:PYTHONPATH=""; .\.venv\Scripts\python.exe -m pytest -v

# Linux / Git Bash:
./run_tests.sh
```
