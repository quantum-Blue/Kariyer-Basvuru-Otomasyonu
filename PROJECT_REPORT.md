# 🚀 PROJECT_REPORT.md — Otonom Kariyer Asistanı Sistem Mimarisi ve Teknik Raporu

> **BELGE AMACI:** Bu doküman, geliştirilecek olan "Yapay Zekâ Destekli Otonom Kariyer Asistanı" projesinin iş mantığını (business logic), mimari gereksinimlerini, kullanıcı profilini ve önceki prototiplerden çıkarılan gerçek dünya derslerini içerir. Ajan, kodlama yaparken bu dokümandaki bağlamı temel alacaktır.

---

## 1. Projenin Vizyonu ve Amacı

Bu proje, klasik ve mekanik iş arama sürecini (şirket arama, web sitesine girme, kariyer sayfasını bulma, İK mailini kazıma, niyet mektubu yazma ve gönderme) otonom bir yapay zekâ ajanına devretmek için tasarlanmıştır. 

Otonom insansız araçlardaki (UAV/UUV) **"Algılama (Perception) $\rightarrow$ Karar Verme (Decision) $\rightarrow$ Planlama (Planning) $\rightarrow$ Eylem (Actuation)"** döngüsünün, yazılım mühendisliği iş arama sürecine uyarlanmış halidir.

### Temel Hedefler:
1. **Nokta Atışı Keşif:** Genel iş ilanı sitelerinde (LinkedIn, Kariyer.net) kaybolmak yerine, doğrudan adayın niş uzmanlık alanlarında (Robotik, ROS 2, SLAM, Otonom Sistemler, Bilgisayarlı Görü, Gömülü Sistemler) faaliyet gösteren teknoloji, Ar-Ge ve savunma sanayi şirketlerini web üzerinde keşfetmek.
2. **Akıllı Filtreleme:** B2B dizin/rehber sitelerini (Clutch, Europages vb.) ve karmaşık ATS (Başvuru Takip Sistemi) formlarını tespit edip eleyerek yalnızca doğrudan e-posta ile ulaşılabilir şirketleri hedeflemek.
3. **Hiper-Kişiselleştirme (RAG + LLM):** Adayın Türkçe ve İngilizce CV/Portfolyo verilerini (RAG) şirketin "Hakkımızda" ve vizyon metinleriyle harmanlayarak, soğuk olmayan, samimi ve teknik doygunluğu yüksek başvuru mailleri üretmek.
4. **Sıfır Spam ve Güvenli İletim:** PDF eki göndermek yerine (spam filtrelerine takılmamak için) portfolyo/CV bağlantılarını (link) paylaşmak, 30 gün kuralı ile mükerrer gönderimi önlemek ve günlük gönderim limitlerine uymak.

---

## 2. Aday (Kullanıcı) Teknik Profili — RAG Veri Kaynağı

Sistemin temsil ettiği kişi **Muhammed Enes BAL**'dır. Ajan, arama sorgularını üretirken ve mailleri kişiselleştirirken aşağıdaki profil özetini merkezde tutmalıdır:

* **Unvan:** Bilgisayar Mühendisi | Robotik, Otonom Sistemler ve Yapay Zekâ Mühendisi (2026 Mezunu, GANO: $3.27 / 4.00$).
* **Çekirdek Uzmanlıklar:** 
  * **Robotik & Otonomi:** ROS / ROS 2, PX4 Autopilot, Pixhawk, Nav2, Gazebo, Otonom Navigasyon, Yol Planlama ($A^*$, Lawnmower/Boustrophedon), Engelden Kaçınma.
  * **SLAM & Konumlandırma:** RTAB-MAP, Fast-LIO2, LIO-SAM, Cartographer 3D, GLIM, Kalman Filtresi, GNSS-Bağımsız (GPS-Denied) Konumlandırma, 3B Nokta Bulutu (Open3D, MeshLab).
  * **Sensör Füzyonu & Donanım:** LiDAR (4B/3B), Stereo Kamera Kalibrasyonu ve Derinlik Kestirimi, IMU, Barometre, Raspberry Pi 5 + AI HAT (Edge AI), STM32, Arduino.
  * **Yapay Zekâ & Bilgisayarlı Görü:** Python, C++, PyTorch, TensorFlow, OpenCV, YOLOv5–v12, YOLO-Seg, RT-DETR, ViT (Vision Transformers), Siyam Ağları, Türkçe BERT / NLP.
* **Öne Çıkan Projeler:**
  1. *Otonom İHA (Bitirme Projesi):* GNSS olmayan ortamlarda LiDAR + Stereo Kamera füzyonu ile 3B haritalama, hibrit SLAM (Fast-LIO2 + RTAB-MAP) ve $A^*$ kurtarma mekanizmalı otonom uçuş.
  2. *İnsansız Su Altı Aracı (AUV - Teknofest İleri Kategori):* 6 iticili biyomimetik tasarım, su altı görüntü iyileştirme, YOLO ile hedef/şamandıra tespiti ve kapalı çevrim derinlik/rota kontrolü.
  3. *Otonom Kargo Dağıtım Aracı (TÜBİTAK 2209-A Yürütücüsü):* Seviye-4 otonom kara aracı simülasyonu, kamera-LiDAR-radar füzyonu.
  4. *Havacılıkta Yapay Zekâ (Teknofest):* İHA görüntülerinden gerçek zamanlı nesne tespiti (YOLO + RT-DETR hibrit yapı) ve GPS'siz görsel pozisyon/iniş alanı kestirimi.

---

## 3. İlk Prototipten Çıkarılan Dersler (Kritik Mühendislik Bulguları)

Bu proje daha önce basit bir prototip olarak denenmiş ve canlı simülasyonlarda (DRY-RUN) çok kritik **gerçek dünya hataları** tespit edilmiştir. Yeni mimari bu hataları **kesin olarak** çözmek üzere tasarlanmalıdır:

### 🚨 Hata 1: Dizin ve Rehber Siteleri Tuzağı (Aggregator Trap)
* **Sorun:** Arama motorunda `"robotik şirketleri türkiye"` gibi genel aramalar yapıldığında, en üst sıralarda gerçek şirketler yerine `clutch.co`, `europages`, `f6s.com`, `linkedin.com`, `kariyer.net`, `glassdoor`, `indeed`, `sortlist` gibi B2B şirket listeleme/rehber siteleri çıkmaktadır.
* **Mimari Çözüm:** 
  1. Arama sorguları "Dork" mantığıyla spesifikleştirilecek ve negatif anahtar kelimeler eklenecektir (Örn: `"ROS 2" "otonom" "kariyer" -site:linkedin.com -site:kariyer.net -site:clutch.co`).
  2. Scraper modülünün önünde katı bir **Domain Kara Listesi (Domain Blacklist)** ve **Rehber Sitesi Tespit Filtresi** bulunacaktır. Başlığında *"Top 10 Companies"*, *"Best Robotics Firms"*, *"B2B Şirketleri"* geçen siteler anında elenecektir.

### 🚨 Hata 2: Yanlış ve Tehlikeli E-posta Adresleri (`support@` Tuzağı)
* **Sorun:** İlk prototip, bir platformun sayfasındaki `support@f6s.com` adresini bulup oraya iş başvurusu maili göndermeye çalışmıştır. Müşteri destek, satış veya sistem adreslerine CV göndermek spam olarak işaretlenmeye yol açar.
* **Mimari Çözüm:** E-posta çıkarıcı (Extractor) modülünde **Üç Katmanlı E-posta Sınıflandırması** uygulanacaktır:
  1. **Kesin Kara Liste (Blacklist - ASLA GÖNDERİLMEZ):** `support@`, `help@`, `destek@`, `sales@`, `satis@`, `marketing@`, `billing@`, `accounting@`, `muhasebe@`, `noreply@`, `no-reply@`, `admin@`, `webmaster@`, `press@`, `media@`, `legal@`, `privacy@`, `kvkk@`, `abuse@`, `security@`.
  2. **Birincil Hedef (Yüksek Öncelik - İK/Kariyer):** `kariyer@`, `career@`, `careers@`, `ik@`, `hr@`, `insankaynaklari@`, `humanresources@`, `talent@`, `talents@`, `jobs@`, `join@`, `recruiting@`, `recruitment@`, `people@`, `cv@`, `basvuru@`.
  3. **İkincil Hedef (Düşük Öncelik - Genel İletişim):** `info@`, `iletisim@`, `contact@`, `hello@`, `merhaba@`, `office@`. *(Yalnızca şirketin küçük/orta ölçekli bir Ar-Ge/Robotik girişimi olduğu doğrulanırsa ve İK maili yoksa kullanılır).*

### 🚨 Hata 3: Ortam Çakışmaları (ROS 2 & Sanal Ortam)
* **Sorun:** Geliştirici makinesinde kurulu olan ROS 2 paketleri global Python ortamındaki `pytest` ve kütüphanelerle çakışmaktadır.
* **Mimari Çözüm:** Proje kök dizininde her zaman `.venv` kullanılacak ve çalıştırma scriptleri (`run.sh`, `run_tests.sh`) `PYTHONPATH` değişkenini izole ederek çalışacaktır.

---

## 4. Sistem Mimarisi ve Modüllerin İşleyişi

Sistem, birbirine gevşek bağlı (loosely coupled) 6 ana katmandan oluşur:

```
┌─────────────────────────────────────────────────────────────────────────┐
│                        1. ALGILAMA VE BİLGİ TABANI (RAG)                │
│  [TR/EN CV & Portfolyo PDF] ──▶ [PyMuPDF] ──▶ [Chunker] ──▶ [ChromaDB]  │
└────────────────────────────────────┬────────────────────────────────────┘
                                     ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                        2. AKILLI KEŞİF VE KAZIMA (SCRAPER)              │
│  [Arama Sorgusu Üretici] ──▶ [DuckDuckGo] ──▶ [Domain/Aggregator Filtre]│
│                                                       │                 │
│  [Veri Çıkarıcı (Hakkımızda, Dil, Mailler)] ◀── [Playwright Headless]   │
└────────────────────────────────────┬────────────────────────────────────┘
                                     ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                        3. KARAR MEKANİZMASI (DECISION ENGINE)           │
│  • Şirket gerçekten hedef sektörde mi? (Uygunluk Kontrolü)              │
│  • ATS (Workday, Greenhouse, Lever vb.) var mı? ──▶ [EVET: MANUEL LİSTE]│
│  • Geçerli İK veya İletişim maili var mı?       ──▶ [EVET: GÖNDERİM]    │
└────────────────────────────────────┬────────────────────────────────────┘
                                     ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                        4. İÇERİK ÜRETİMİ (LLM GENERATOR)                │
│  [Şirket Misyonu] + [RAG Yetkinlikleri] + [TR/EN Şablon]                │
│         ──▶ [Gemini API / Lokal Fallback] ──▶ [Gövde + Konu + Linkler]  │
└────────────────────────────────────┬────────────────────────────────────┘
                                     ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                        5. HAFIZA VE EYLEM (DATABASE & MAILER)           │
│  [SQLite: 30 Gün Kuralı & Kota Kontrolü] ──▶ [smtplib SSL/TLS / DRY-RUN]│
└────────────────────────────────────┬────────────────────────────────────┘
                                     ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                        6. ORKESTRASYON VE ARAYÜZ (CLI & SCHEDULER)      │
│  [Hata İzolasyonlu Pipeline] + [Rich CLI Tabloları] + [Günlük Zamanlama]│
└─────────────────────────────────────────────────────────────────────────┘
```

### Katman Detayları:

1. **Algılama Katmanı (`src/rag/`):**
   * PDF belgelerini okur, anlamlı parçalara (chunk) böler.
   * `paraphrase-multilingual-MiniLM-L12-v2` gibi çok dilli bir modelle vektörleştirip yerel `ChromaDB`'ye kaydeder.
   * Herhangi bir şirketin faaliyet alanı verildiğinde, adayın o şirketle en alakalı 3-4 projesini/yetkinliğini saniyeler içinde getirir.

2. **Keşif ve Kazıma Katmanı (`src/scraper/`):**
   * Arama motorundan gelen sonuçları önce **Aggregator/Domain Kara Listesinden** geçirir.
   * Geçerli şirket sitelerine `Playwright` ile girer; ana sayfayı, `/kariyer`, `/careers`, `/hakkimizda`, `/about`, `/iletisim`, `/contact` sayfalarını tarar.
   * Sayfa metinlerinden şirketin dilini (`TR` veya `EN`) ve ne iş yaptığını çıkarır.
   * HTML ve `mailto:` linklerinden e-postaları kazır, kara listedekileri (`support@` vb.) çöpe atar, kalanları puanlayarak sıralar.

3. **Karar Katmanı (`src/filter/`):**
   * Sayfada `greenhouse.io`, `lever.co`, `workday`, `bamboohr`, `workable`, `smartrecruiters`, `personio` gibi ATS linkleri varsa, e-posta adresi bulunsa bile bu şirketi **`MANUAL_ATS`** olarak işaretler (kullanıcının form doldurması için kaydeder).
   * ATS yoksa ve geçerli bir mail varsa **`SEND_EMAIL`** (İK maili) veya **`SEND_EMAIL_LOW_PRIORITY`** (`info@` maili) kararı verir.

4. **Üretim Katmanı (`src/generator/`):**
   * Şirket Türk ise Türkçe şablon ve Türkçe CV/Portfolyo linki; yabancı ise İngilizce şablon ve İngilizce CV/Portfolyo linki seçilir.
   * **Asla dosya eki (attachment) kullanılmaz.** Tüm belgeler metin içine temiz URL olarak yerleştirilir.
   * LLM (Gemini Free Tier), belirlenen şablonun dışına çıkmadan, şirketin vizyonuna atıfta bulunan ve adayın RAG'den gelen spesifik projelerini (örn. *"Geliştirdiğim Fast-LIO2 tabanlı GNSS-bağımsız İHA navigasyon sistemi..."*) öne çıkaran bir metin üretir. API hatası olursa statik şablon (fallback) devreye girer.

5. **Hafıza ve Gönderim Katmanı (`src/database/` & `src/mailer/`):**
   * `SQLite` veritabanı; taranan şirketleri, gönderilen mailleri ve ATS kuyruğunu tutar.
   * **30 Gün Kuralı:** Aynı e-posta adresine veya aynı şirket domain'ine son 30 gün içinde mail atılmışsa (başarılı gönderim), sistem ikinci bir mail atılmasını kesin olarak engeller.
   * **Günlük Kota ve Rate Limiting:** Günde maksimum 10-15 mail atılır. Gönderimler arasında rastgele (insansı) bekleme süreleri uygulanır.
   * **DRY-RUN Modu:** Sistem varsayılan olarak simülasyon modunda çalışır; hiçbir gerçek mail atmadan tüm süreci ekrana ve veritabanına yazar. Yalnızca kullanıcı açıkça canlı modu (`--no-dry-run`) seçtiğinde `smtplib` devreye girer.

---

## 5. Güvenlik ve Konfigürasyon Yönetimi

* E-posta şifreleri (Gmail App Password), Gemini API anahtarı ve kişisel portfolyo linkleri **asla** kaynak kodun içine gömülmez (hardcoded yapılmaz).
* Tüm hassas veriler `.env` dosyasında tutulur ve `.gitignore` ile koruma altına alınır.
* Sistemde örnek bir `.env.example` ve yapılandırma parametrelerini (günlük limit, bekleme süreleri, kara liste kelimeleri) tutan bir `config/settings.yaml` (veya `config.py`) bulunur.