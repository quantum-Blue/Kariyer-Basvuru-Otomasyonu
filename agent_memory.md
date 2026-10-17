# 🧠 Agent Memory — Otonom Kariyer Asistanı Proje Takip ve Hafıza Dosyası

> **Amaç:** Bu dosya, projenin 16 mikro-fazlık geliştirme sürecini, 4 Milestone Entegrasyon Denetimini, alınan mimari kararları (ADR) ve mühendislik kurallarını takip eden canlı hafıza belgesidir. Her faz tamamlandığında güncellenir.

---

## 1. Mühendislik Anayasası ve Kesin Kurallar (Özet)

1. **Mikro-Faz Disiplini:** Tek seferde yalnızca **tek bir faz** geliştirilir. Kullanıcı açıkça onay vermeden bir sonraki faza veya kodlama adımına geçilmez.
2. **5 Adımlı Kalite Döngüsü (TDD + Bug Hunting):**
   - `[1. Seçenek Sunma (Gerekliyse)]` $\rightarrow$ `[2. TDD Test Yazımı (Red)]` $\rightarrow$ `[3. Kodlama (Green)]` $\rightarrow$ `[4. Aktif Hata Avcılığı (Break It)]` $\rightarrow$ `[5. Git Commit & Kullanıcı Onayı]`
3. **Dış Bağımlılık İzolasyonu (Mocking):** Birim testlerde gerçek ağ isteği, gerçek LLM API çağrısı veya gerçek SMTP bağlantısı **kesinlikle** yapılmaz (`unittest.mock` / `pytest-mock` kullanılır).
4. **%100 Açık Kaynak ve Sıfır Maliyet:** Ücretli hiçbir servis/API kullanılmaz (ChromaDB, Sentence-Transformers, PyMuPDF, Playwright, SQLite, Gemini API Free Tier, DuckDuckGo, Gmail SMTP).
5. **Önceki Prototipin 3 Kritik Hatasına Karşı Kırmızı Çizgiler:**
   - **Kırmızı Çizgi 1 (Anti-Aggregator):** `clutch.co`, `f6s.com`, `europages`, `linkedin.com`, `kariyer.net`, `glassdoor`, `indeed`, `sortlist`, `yellowpages` gibi B2B rehber/dizin siteleri ve *"Top 10 Companies"* tarzı liste sayfaları hem arama sorgusunda (Dork `-site:`) hem de `domain_filter.py` katmanında (alt alan adları dahil) %100 elenir.
   - **Kırmızı Çizgi 2 (`support@` Kara Listesi):** `support@`, `help@`, `destek@`, `sales@`, `satis@`, `noreply@`, `no-reply@`, `admin@`, `billing@`, `kvkk@`, `privacy@`, `security@` gibi adresler **Kesin Kara Liste (Blacklist)** içindedir ve asla geçerli alıcı kabul edilmez. E-postalar 3 katmanlı sınıflandırılır (Kara Liste $\rightarrow$ Yüksek Öncelik İK/Kariyer $\rightarrow$ Düşük Öncelik Genel İletişim).
   - **Kırmızı Çizgi 3 (ROS 2 & Sistem İzolasyonu):** Tüm çalıştırma ve test süreçleri yerel `.venv` içinde ve `PYTHONPATH=""` sıfırlaması ile izole edilerek global ROS 2 `pytest` eklentilerinin çakışması engellenir.
6. **Milestone Denetimi:** Her 4 fazın sonunda (Faz 4, 8, 12, 16) yeni özellik geliştirme durdurulur ve uçtan uca entegrasyon denetimi yapılır.

---

## 2. 16 Mikro-Faz ve 4 Milestone Durum Tablosu

| Faz / Milestone | Odak Noktası | Hedef Çıktılar | Durum | Test Durumu | Hedef Git Commit |
| :--- | :--- | :--- | :---: | :---: | :--- |
| **Faz 0** | Hazırlık, Doküman Analizi ve Onay | `agent_memory.md` | ✅ Tamamlandı | - | - |
| **Faz 1** | Ortam İzolasyonu, Proje İskeleti ve Konfigürasyon Yönetimi | `run.sh`, `run_tests.sh`, `src/config/settings.py`, `src/utils/logger.py`, `.gitignore`, `.env.example`, `config/settings.yaml`, `pytest.ini` | ✅ Tamamlandı | 🟢 21/21 Geçti | `chore(core): initialize isolated environment, config loader and logging` |
| **Faz 2** | PDF Metin Çıkarıcı ve Ön İşleme (PDF Loader) | `src/rag/pdf_loader.py`, `tests/test_pdf_loader.py` | ✅ Tamamlandı | 🟢 17/17 Geçti | `feat(rag): implement PDF text extractor with UTF-8 normalization` |
| **Faz 3** | Metin Parçalama (Chunking) ve Metadata Zenginleştirme | `src/rag/chunker.py`, `tests/test_chunker.py` | ⏳ Bekliyor | ⚪ Yazılmadı | `feat(rag): add semantic text chunker with language metadata support` |
| **Faz 4** | Çok Dilli Embedding ve Kalıcı Vektör Veritabanı (ChromaDB) | `src/rag/embedder.py`, `src/rag/vector_store.py`, `tests/test_vector_store.py` | ⏳ Bekliyor | ⚪ Yazılmadı | `feat(rag): integrate multilingual embeddings and persistent ChromaDB store` |
| **🛡️ MILESTONE 1** | **Entegrasyon Denetimi (Faz 1 – 4 Kontrolü)** | `tests/integration/test_milestone_1_rag.py` | ⏳ Bekliyor | ⚪ Yazılmadı | `test(milestone-1): verify end-to-end RAG and config integration` |
| **Faz 5** | Dork Tabanlı Arama Sorgusu Üretici ve Arama Motoru İstemcisi | `src/scraper/query_builder.py`, `src/scraper/search_engine.py`, `tests/test_search_engine.py` | ⏳ Bekliyor | ⚪ Yazılmadı | `feat(scraper): implement dork query builder and resilient search engine client` |
| **Faz 6** | Domain Kara Listesi ve Rehber Sitesi (Aggregator) Filtresi | `src/scraper/domain_filter.py`, `tests/test_domain_filter.py` | ⏳ Bekliyor | ⚪ Yazılmadı | `feat(scraper): add anti-aggregator domain and title blacklist filter` |
| **Faz 7** | Playwright Headless Sayfa Çekici ve Hız Sınırlayıcı (Rate Limiter) | `src/scraper/page_fetcher.py`, `src/scraper/rate_limiter.py`, `tests/test_page_fetcher.py` | ⏳ Bekliyor | ⚪ Yazılmadı | `feat(scraper): implement async Playwright fetcher, language detector and rate limiter` |
| **Faz 8** | Üç Katmanlı E-posta Kazıyıcı ve `support@` Kara Liste Filtresi | `src/scraper/email_extractor.py`, `tests/test_email_extractor.py` | ⏳ Bekliyor | ⚪ Yazılmadı | `feat(scraper): implement 3-tier email extractor with strict prefix blacklist` |
| **🛡️ MILESTONE 2** | **Entegrasyon Denetimi (Faz 1 – 8 Kontrolü)** | `tests/integration/test_milestone_2_discovery.py` | ⏳ Bekliyor | ⚪ Yazılmadı | `test(milestone-2): verify discovery, anti-aggregator and email extraction pipeline` |
| **Faz 9** | ATS Tespit Motoru ve Yönlendirme Karar Ağacı (Decision Tree) | `src/filter/ats_detector.py`, `src/filter/decision_tree.py`, `tests/test_decision_tree.py` | ⏳ Bekliyor | ⚪ Yazılmadı | `feat(filter): implement deterministic ATS detector and routing decision tree` |
| **Faz 10** | Çift Dilli Şablon Yöneticisi ve Dinamik Prompt İnşası | `src/generator/template_manager.py`, `src/generator/prompt_builder.py`, `tests/test_prompt_builder.py` | ⏳ Bekliyor | ⚪ Yazılmadı | `feat(generator): add bilingual link-based templates and RAG prompt builder` |
| **Faz 11** | Gemini LLM İstemcisi, Çıktı Doğrulayıcı ve Fallback Mekanizması | `src/generator/llm_client.py`, `tests/test_llm_client.py` | ⏳ Bekliyor | ⚪ Yazılmadı | `feat(generator): implement Gemini LLM client with output validation and fallback` |
| **Faz 12** | SQLite Hafıza Yöneticisi ve 30 Gün Kuralı (Cooldown Engine) | `src/database/db_manager.py`, `tests/test_db_manager.py` | ⏳ Bekliyor | ⚪ Yazılmadı | `feat(database): implement SQLite manager with email and domain-level 30-day cooldown` |
| **🛡️ MILESTONE 3** | **Entegrasyon Denetimi (Faz 1 – 12 Kontrolü)** | `tests/integration/test_milestone_3_intelligence.py` | ⏳ Bekliyor | ⚪ Yazılmadı | `test(milestone-3): verify decision tree, LLM generation and DB cooldown integration` |
| **Faz 13** | Güvenli SMTP Gönderici, Kota Kontrolü ve DRY-RUN Simülatörü | `src/mailer/email_sender.py`, `tests/test_email_sender.py` | ⏳ Bekliyor | ⚪ Yazılmadı | `feat(mailer): implement SMTP SSL sender with daily quota and dry-run protection` |
| **Faz 14** | Hata İzolasyonlu Ana Orkestratör (Pipeline Engine) | `src/orchestrator/pipeline.py`, `tests/test_pipeline.py` | ⏳ Bekliyor | ⚪ Yazılmadı | `feat(orchestrator): build fault-tolerant end-to-end execution pipeline` |
| **Faz 15** | Zengin CLI Arayüzü (`argparse` + `rich`) ve Raporlama | `src/cli/interface.py`, `main.py`, `tests/test_cli.py` | ⏳ Bekliyor | ⚪ Yazılmadı | `feat(cli): implement rich terminal dashboard and CLI argument parser` |
| **Faz 16** | Zamanlayıcı (Scheduler) ve Son Kullanıcı Dokümantasyonu | `src/orchestrator/scheduler.py`, `README.md`, `tests/test_scheduler.py` | ⏳ Bekliyor | ⚪ Yazılmadı | `feat(scheduler): add daily autonomous execution daemon and complete README` |
| **🏆 MILESTONE 4** | **Tam Sistem Kabul ve Canlı DRY-RUN Denetimi (Faz 1 – 16)** | Tüm Birim + Entegrasyon Testleri & Canlı `DRY-RUN` | ⏳ Bekliyor | ⚪ Yazılmadı | `release(v1.0.0): pass final system audit and complete autonomous career assistant` |

---

## 3. Mimari Karar Kayıtları (ADR) & Notlar

- **ADR-001 (Faz 1 - Konfigürasyon Yönetimi):** Kullanıcı onayıyla **Seçenek A (`Pydantic` + `PyYAML` + `python-dotenv`)** seçildi ve `ConfigurationError` sarmalayıcısı ile devreye alındı. Eksik `.env` dosyasında güvenli varsayılanlar (default) kullanılırken, bozuk YAML veya sınır değer/şema ihlallerinde (`extra="forbid"`, negatif limitler, `chunk_overlap >= chunk_size` vb.) sistem erken aşamada anlamlı `ConfigurationError` fırlatmaktadır.
- **Ortam Notu:** Geliştirme Windows üzerinde yürütülmekte olup `run.sh`, `run_tests.sh` ve `pytest.ini` hem Linux/WSL/Git Bash hem de Windows `.venv` yollarını (`.venv/Scripts/python.exe` ve `.venv/bin/python`) destekleyecek ve ROS 2 `pytest` eklentilerini (`-p no:ament_*`, `-p no:launch_testing*`, `PYTHONPATH=""`) devre dışı bırakacak şekilde yapılandırılmıştır.
- **Dosya Notu:** Çalışma dizinindeki `AGENT_RULES.md` ve `PHASES_PLAN.md` dosyalarının içerikleri birbirinin yerine kaydedilmiş durumda ve `PHASES_PLAN.md` (içinde `AGENT_RULES.md` metni olan dosya) 65. satırda yarım kalmış. Kullanıcı isterse bu iki dosyanın isim/içerik senkronizasyonu düzeltilebilir.

