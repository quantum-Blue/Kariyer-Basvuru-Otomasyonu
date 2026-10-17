# 🗺️ PHASES_PLAN.md — Mikro-Fazlar, TDD ve Entegrasyon Denetim Planı

> **DİKKAT (AJAN TALİMATI):** Bu proje, karmaşıklığı yönetmek ve sıfır hata (zero-bug) hedefine ulaşmak için **16 atomik mikro-faza** ve **4 kapsamlı entegrasyon denetimine (Milestone Audit)** bölünmüştür.
> * **Kesin Kural 1:** Bir fazda yalnızca o faza ait tek bir odak noktası geliştirilir. Asla iki faz birleştirilemez.
> * **Kesin Kural 2:** Her faz; `[1. Seçenek Sunma (Gerekliyse)] -> [2. TDD Test Yazımı] -> [3. Kodlama] -> [4. Aktif Hata Avcılığı (Bug Hunting)] -> [5. Git Commit & Kullanıcı Onayı]` döngüsüyle tamamlanır.
> * **Kesin Kural 3:** Her 4 fazın sonunda yeni özellik ekleme durdurulur ve **Milestone Entegrasyon Denetimi** yapılır.

---

## 📦 BÖLÜM 1: Temel Altyapı ve Algılama Katmanı (Faz 1 – Faz 4)

### Faz 1: Ortam İzolasyonu, Proje İskeleti ve Konfigürasyon Yönetimi
* **Hedef:** ROS 2 gibi sistem paketleriyle çakışmayan izole `.venv` ortamını, klasör yapısını, `.env` / `settings.yaml` okuyucusunu ve merkezi loglama (`logger`) modülünü kurmak.
* **Çıktılar:** `run.sh`, `run_tests.sh`, `src/config/settings.py`, `src/utils/logger.py`, `.gitignore`, `.env.example`.
* **Hatasız Çalışma Şartı & Bug Hunting:**
  * `PYTHONPATH=""` izolasyonu ile sistemdeki ROS 2 `pytest` eklentilerinin devre dışı kaldığı doğrulanmalı.
  * Eksik `.env` dosyası veya bozuk YAML formatı verildiğinde sistem çökmemeli, anlamlı `ConfigurationError` fırlatmalı veya güvenli varsayılanlara (default) dönmeli.
* **Git Commit:** `chore(core): initialize isolated environment, config loader and logging`

### Faz 2: PDF Metin Çıkarıcı ve Ön İşleme (PDF Loader)
* **Hedef:** Türkçe ve İngilizce CV/Portfolyo PDF'lerinden metinleri UTF-8 karakter kaybı olmadan çıkarmak ve temizlemek.
* **Çıktılar:** `src/rag/pdf_loader.py`, `tests/test_pdf_loader.py`.
* **Hatasız Çalışma Şartı & Bug Hunting:**
  * Türkçe karakterler (`ğ, ü, ş, i, ö, ç, İ, Ğ`) bozulmadan çıkarılmalı.
  * Boş PDF, şifreli PDF, bozuk dosya yolu veya PDF olmayan (`.txt`, `.jpg`) bir dosya verildiğinde kontrollü hata yönetimi yapılmalı.
* **Git Commit:** `feat(rag): implement PDF text extractor with UTF-8 normalization`

### Faz 3: Metin Parçalama (Chunking) ve Metadata Zenginleştirme
* **Hedef:** Çıkarılan CV/Portfolyo metinlerini anlam bütünlüğünü bozmadan parçalara (chunks) bölmek ve her parçaya kaynak/dil (`TR`/`EN`) metadata'sı eklemek.
* **Çıktılar:** `src/rag/chunker.py`, `tests/test_chunker.py`.
* **Hatasız Çalışma Şartı & Bug Hunting:**
  * Chunk boyutu ve örtüşme (`overlap`) sınırları korunmalı; kelimeler ortadan bölünmemeli.
  * Çok kısa metinler (1 cümle) veya aşırı uzun tek satırlık metinler (boşluksuz string) sistemi kırmamalı.
* **Git Commit:** `feat(rag): add semantic text chunker with language metadata support`

### Faz 4: Çok Dilli Embedding ve Kalıcı Vektör Veritabanı (ChromaDB)
* **Hedef:** Parçalanan metinleri çok dilli (multilingual) embedding modeliyle vektörleştirip yerel `ChromaDB`'ye kaydetmek ve semantik sorgulama yapmak.
* **Çıktılar:** `src/rag/embedder.py`, `src/rag/vector_store.py`, `tests/test_vector_store.py`.
* **Hatasız Çalışma Şartı & Bug Hunting:**
  * Model belleğe yalnızca bir kez yüklenmeli (Singleton pattern).
  * Türkçe sorulan bir soruya (örn. *"Hangi SLAM algoritmalarını biliyor?"*) hem Türkçe hem İngilizce CV'den doğru parçalar dönebilmeli.
  * Aynı PDF iki kez yüklendiğinde mükerrer (duplicate) kayıt oluşması engellenmeli.
* **Git Commit:** `feat(rag): integrate multilingual embeddings and persistent ChromaDB store`

---

### 🛡️ MILESTONE 1: Entegrasyon Denetimi (Faz 1 – 4 Kontrolü)
* **Denetim Hedefi:** Konfigürasyon $\rightarrow$ PDF Okuma $\rightarrow$ Chunking $\rightarrow$ ChromaDB Sorgulama zincirinin gerçek CV/Portfolyo dosyalarıyla uçtan uca testi.
* **Yapılacaklar:** `tests/integration/test_milestone_1_rag.py` yazılır. Bellek sızıntısı, disk yazma izinleri ve hız darboğazları kontrol edilir. Hata varsa Faz 5'e geçilmeden düzeltilir.
* **Git Commit:** `test(milestone-1): verify end-to-end RAG and config integration`

---

## 🌐 BÖLÜM 2: Akıllı Keşif, Anti-Aggregator ve Kazıma Katmanı (Faz 5 – Faz 8)

### Faz 5: Dork Tabanlı Arama Sorgusu Üretici ve Arama Motoru İstemcisi
* **Hedef:** Genel ve kalitesiz aramalar yerine, rehber sitelerini dışlayan (`-site:linkedin.com -site:clutch.co` vb.) spesifik arama sorguları üretmek ve DuckDuckGo üzerinden sonuçları çekmek.
* **Çıktılar:** `src/scraper/query_builder.py`, `src/scraper/search_engine.py`, `tests/test_search_engine.py`.
* **Hatasız Çalışma Şartı & Bug Hunting:**
  * Ağ kopması, DuckDuckGo rate-limit (HTTP 429/403) veya boş sonuç dönmesi durumunda sistem çökmemeli, boş liste dönüp uyarı loglamalı.
* **Git Commit:** `feat(scraper): implement dork query builder and resilient search engine client`

### Faz 6: Domain Kara Listesi ve Rehber Sitesi (Aggregator) Filtresi
* **Hedef:** Arama sonuçlarından gelen `clutch.co`, `europages`, `f6s.com`, `kariyer.net`, `glassdoor`, `indeed`, `yellowpages` gibi B2B dizin sitelerini ve *"Top 10 Robotics Companies"* tarzı blog/liste sayfalarını daha siteye girmeden elemek.
* **Çıktılar:** `src/scraper/domain_filter.py`, `tests/test_domain_filter.py`.
* **Hatasız Çalışma Şartı & Bug Hunting:**
  * Alt alan adları (`tr.linkedin.com`, ` www.clutch.co/profile/...`) kaçırılmadan yakalanmalı.
  * Gerçek bir robotik şirketinin sitesi yanlışlıkla (false-positive) elenmemeli.
* **Git Commit:** `feat(scraper): add anti-aggregator domain and title blacklist filter`

### Faz 7: Playwright Headless Sayfa Çekici ve Hız Sınırlayıcı (Rate Limiter)
* **Hedef:** Filtreden geçen gerçek şirket sitelerinin ana sayfasını, `/kariyer` ve `/hakkimizda` sayfalarını JS render desteğiyle çekmek, metni temizlemek ve şirket dilini (`TR`/`EN`) tespit etmek.
* **Çıktılar:** `src/scraper/page_fetcher.py`, `src/scraper/rate_limiter.py`, `tests/test_page_fetcher.py`.
* **Hatasız Çalışma Şartı & Bug Hunting:**
  * Zaman aşımı (Timeout), SSL sertifika hatası, 404/500 hataları ve sonsuz yönlendirme (redirect loop) durumlarında tarayıcı (browser context) açık kalmamalı (zombie process önleme).
* **Git Commit:** `feat(scraper): implement async Playwright fetcher, language detector and rate limiter`

### Faz 8: Üç Katmanlı E-posta Kazıyıcı ve `support@` Kara Liste Filtresi
* **Hedef:** Sayfa kaynağından ve `mailto:` linklerinden e-postaları kazımak; `.png`, `.jpg`, `.js` gibi sahte eşleşmeleri temizlemek ve e-postaları **Kesin Kara Liste** (`support@`, `noreply@`, `sales@`, `help@` -> ÇÖP), **Yüksek Öncelik** (`kariyer@`, `hr@`, `ik@`) ve **Düşük Öncelik** (`info@`, `iletisim@`) olarak sınıflandırmak.
* **Çıktılar:** `src/scraper/email_extractor.py`, `tests/test_email_extractor.py`.
* **Hatasız Çalışma Şartı & Bug Hunting:**
  * İlk prototipteki `support@f6s.com` hatası kesinlikle tekrarlanmamalı; kara listedeki hiçbir ön ek (prefix) geçerli kabul edilmemeli.
  * `u003e` veya `%20` gibi URL-encoded karakterler içeren `mailto:` linkleri temizlenerek çıkarılmalı.
* **Git Commit:** `feat(scraper): implement 3-tier email extractor with strict prefix blacklist`

---

### 🛡️ MILESTONE 2: Entegrasyon Denetimi (Faz 1 – 8 Kontrolü)
* **Denetim Hedefi:** RAG'den anahtar kelime alıp arama yapma $\rightarrow$ Rehber sitelerini eleme $\rightarrow$ Sayfa çekme $\rightarrow$ Temiz İK maili çıkarma akışının entegrasyon testi.
* **Yapılacaklar:** `tests/integration/test_milestone_2_discovery.py` yazılır ve çalıştırılır.
* **Git Commit:** `test(milestone-2): verify discovery, anti-aggregator and email extraction pipeline`

---

## 🧠 BÖLÜM 3: Karar Mekanizması, LLM Üretimi ve Hafıza (Faz 9 – Faz 12)

### Faz 9: ATS Tespit Motoru ve Yönlendirme Karar Ağacı (Decision Tree)
* **Hedef:** Şirket sayfasında 15+ popüler ATS platformundan (`greenhouse.io`, `lever.co`, `workday`, `bamboohr`, `workable`, `smartrecruiters`, `personio` vb.) biri varsa şirketi `MANUAL_ATS` listesine yönlendirmek; yoksa bulunan e-posta kalitesine göre `SEND_EMAIL`, `SEND_EMAIL_LOW_PRIORITY` veya `SKIP` kararı vermek.
* **Çıktılar:** `src/filter/ats_detector.py`, `src/filter/decision_tree.py`, `tests/test_decision_tree.py`.
* **Hatasız Çalışma Şartı & Bug Hunting:**
  * Sayfada hem `kariyer@sirket.com` hem de `greenhouse.io` linki varsa, ATS kuralı e-postayı ezmeli (`Override -> MANUAL_ATS`).
* **Git Commit:** `feat(filter): implement deterministic ATS detector and routing decision tree`

### Faz 10: Çift Dilli Şablon Yöneticisi ve Dinamik Prompt İnşası
* **Hedef:** Türk şirketler için Türkçe, yabancı şirketler için İngilizce şablonları yönetmek; dosya eki (attachment) ifadelerini yasaklayıp CV/Portfolyo URL'lerini yerleştirmek ve RAG verisi + Şirket misyonunu birleştiren LLM promptunu hazırlamak.
* **Çıktılar:** `src/generator/template_manager.py`, `src/generator/prompt_builder.py`, `tests/test_prompt_builder.py`.
* **Hatasız Çalışma Şartı & Bug Hunting:**
  * Şirketin "Hakkımızda" metni boş gelse bile prompt bozulmamalı, genel ama profesyonel bir robotik/otonom sistemler vurgusuna dönmeli.
* **Git Commit:** `feat(generator): add bilingual link-based templates and RAG prompt builder`

### Faz 11: Gemini LLM İstemcisi, Çıktı Doğrulayıcı ve Fallback Mekanizması
* **Hedef:** Google Gemini API (Free Tier) ile kişiselleştirilmiş e-posta üretmek; üretilen metinde portfolyo linkinin varlığını ve kelime sınırlarını doğrulamak; API hatası/kotası durumunda statik yedek (fallback) şablona kesintisiz geçiş yapmak.
* **Çıktılar:** `src/generator/llm_client.py`, `tests/test_llm_client.py`.
* **Hatasız Çalışma Şartı & Bug Hunting:**
  * LLM halüsinasyon görüp portfolyo linkini silerse veya uydurma bir link yazarsa, doğrulayıcı (validator) bunu yakalayıp düzeltebilmeli veya fallback şablonu devreye sokmalı.
* **Git Commit:** `feat(generator): implement Gemini LLM client with output validation and fallback`

### Faz 12: SQLite Hafıza Yöneticisi ve 30 Gün Kuralı (Cooldown Engine)
* **Hedef:** Şirketleri, gönderilen e-postaları ve manuel ATS kuyruğunu SQLite'ta tutmak; aynı e-postaya veya aynı şirket domain'ine son 30 gün içinde gönderim yapılmışsa yeni gönderimi kesin olarak engellemek.
* **Çıktılar:** `src/database/db_manager.py`, `tests/test_db_manager.py`.
* **Hatasız Çalışma Şartı & Bug Hunting:**
  * `DRY-RUN` (simülasyon) veya `FAILED` (başarısız) kayıtlar 30 günlük gerçek gönderim hakkını tüketmemeli.
  * `ik@sirket.com` adresine mail atıldıktan 5 gün sonra aynı şirketin `kariyer@sirket.com` adresi bulunursa, **domain bazlı 30 gün kuralı** devreye girip bunu da engellemeli!
* **Git Commit:** `feat(database): implement SQLite manager with email and domain-level 30-day cooldown`

---

### 🛡️ MILESTONE 3: Entegrasyon Denetimi (Faz 1 – 12 Kontrolü)
* **Denetim Hedefi:** Kazınmış şirket verisinden $\rightarrow$ Karar Ağacı $\rightarrow$ RAG + LLM Mail Üretimi $\rightarrow$ SQLite 30 Gün Kontrolüne kadar olan tüm zekâ katmanının entegrasyon testi.
* **Yapılacaklar:** `tests/integration/test_milestone_3_intelligence.py` yazılır ve çalıştırılır.
* **Git Commit:** `test(milestone-3): verify decision tree, LLM generation and DB cooldown integration`

---

## 🚀 BÖLÜM 4: Güvenli Gönderim, Orkestrasyon ve Otonomi (Faz 13 – Faz 16)

### Faz 13: Güvenli SMTP Gönderici, Kota Kontrolü ve DRY-RUN Simülatörü
* **Hedef:** `smtplib` (SSL/TLS) ile Gmail üzerinden e-posta göndermek, günlük maksimum gönderim kotasını (örn. 10 mail/gün) denetlemek ve `dry_run=True` modunda sıfır riskli simülasyon yapmak.
* **Çıktılar:** `src/mailer/email_sender.py`, `tests/test_email_sender.py`.
* **Hatasız Çalışma Şartı & Bug Hunting:**
  * `dry_run=True` iken ağ bağlantısı (SMTP socket) **kesinlikle** açılmamalı.
  * SMTP kimlik doğrulama hatası (`SMTPAuthenticationError`) veya bağlantı kopmasında program çökmemeli, durumu `FAILED` olarak DB'ye yazmalı.
* **Git Commit:** `feat(mailer): implement SMTP SSL sender with daily quota and dry-run protection`

### Faz 14: Hata İzolasyonlu Ana Orkestratör (Pipeline Engine)
* **Hedef:** Faz 1'den Faz 13'e kadar olan tüm modülleri tek bir akışta (`run_pipeline`) birleştirmek ve şirket bazlı hata izolasyonu sağlamak.
* **Çıktılar:** `src/orchestrator/pipeline.py`, `tests/test_pipeline.py`.
* **Hatasız Çalışma Şartı & Bug Hunting:**
  * 10 şirketten 3.sünde beklenmeyen bir hata patlarsa, orkestratör hatayı loglayıp 4. şirketten çalışmaya devam etmeli (Fault Isolation).
* **Git Commit:** `feat(orchestrator): build fault-tolerant end-to-end execution pipeline`

### Faz 15: Zengin CLI Arayüzü (`argparse` + `rich`) ve Raporlama
* **Hedef:** Kullanıcıya terminal üzerinden `--dry-run`, `--no-dry-run`, `--limit`, `--keywords`, `--stats`, `--manual-list` komutlarını sunmak ve `rich` kütüphanesi ile renkli özet tabloları basmak.
* **Çıktılar:** `src/cli/interface.py`, `main.py`, `tests/test_cli.py`.
* **Hatasız Çalışma Şartı & Bug Hunting:**
  * Geçersiz argümanlarda (örn. `--limit -5`) kullanıcı dostu hata mesajı verilmeli.
* **Git Commit:** `feat(cli): implement rich terminal dashboard and CLI argument parser`

### Faz 16: Zamanlayıcı (Scheduler) ve Son Kullanıcı Dokümantasyonu
* **Hedef:** Sistemin her gün belirlenen saatte (örn. 09:00) otonom çalışmasını sağlayacak zamanlayıcıyı (`schedule`) eklemek ve kapsamlı `README.md` dosyasını hazırlamak.
* **Çıktılar:** `src/orchestrator/scheduler.py`, `README.md`, `tests/test_scheduler.py`.
* **Git Commit:** `feat(scheduler): add daily autonomous execution daemon and complete README`

---

### 🏆 MILESTONE 4 (FİNAL): Tam Sistem Kabul ve Canlı DRY-RUN Denetimi (Faz 1 – 16)
* **Denetim Hedefi:** Tüm birim testlerin, 4 milestone entegrasyon testinin ve gerçek CV'lerle yapılacak canlı bir `DRY-RUN` simülasyonunun eksiksiz doğrulanması.
* **Kabul Kriteri:** 0 Hata, %100 Test Başarısı, Rehber sitelerinin (`clutch`, `f6s` vb.) ve `support@` maillerinin %100 başarıyla elendiğinin canlı kanıtı.
* **Git Commit:** `release(v1.0.0): pass final system audit and complete autonomous career assistant`