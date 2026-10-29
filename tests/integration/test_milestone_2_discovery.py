"""🛡️ MILESTONE 2: Entegrasyon Denetimi (Faz 1 – 8 Uçtan Uca Keşif & Kazıma Kontrolü).

Bu entegrasyon testi modülü şu uçtan uca (End-to-End) zinciri denetler:
[Faz 1: Konfigürasyon & Logger]
    -> [Faz 2–4: RAG (CV/Portfolyo Okuma, Chunking, ChromaDB Semantik Arama)]
    -> [Faz 5: Dork Sorgu Üretici (`query_builder`) & Dayanıklı Arama Motoru (`search_engine`)]
    -> [Faz 6: Anti-Aggregator Domain & Liste Başlığı Filtresi (`domain_filter`)]
    -> [Faz 7: Asenkron Hız Sınırlayıcı (`rate_limiter`) & Playwright Sayfa Çekici (`page_fetcher`)]
    -> [Faz 8: 3 Katmanlı E-posta Kazıyıcı & `support@` Kara Liste Filtresi (`email_extractor`)]

Kritik Denetim Noktaları (Kırmızı Çizgiler):
1. Kırmızı Çizgi #1 (Anti-Aggregator): Arama sonuçlarına sızan `clutch.co`, `f6s.com`,
   `tr.linkedin.com`, `kariyer.net` ve *"Top 10 Robotics Companies"* / *"En İyi 15 Şirket"*
   gibi rehber ve liste sayfaları Playwright aşamasına geçmeden %100 elenmelidir.
2. Kırmızı Çizgi #2 (`support@f6s.com` Tuzağı): Gerçek şirket sayfalarının altbilgi (footer)
   veya iletişim kısımlarında yer alan `support@`, `help@`, `destek@`, `sales@`, `noreply@`,
   `kvkk@`, `logo@2x.png` ve `support@f6s.com` gibi adresler KESİN KARA LİSTE (`BLACKLISTED`)
   olarak imha edilmeli; varsa `HIGH_PRIORITY` (`kariyer@`, `hr@`, `ik@`), yoksa `LOW_PRIORITY`
   (`info@`, `contact@`) seçilmelidir.
3. Kırmızı Çizgi #3 (Zombie Process, Bellek Sızıntısı ve Performans SLA):
   Playwright sekme açma/kapama döngüsünde hata (`TimeoutError`, `HTTP 500`, SSL hatası)
   oluşsa dahi açık kalan (unclosed) sekme kalmamalı (`open_pages == 0`), bellek sızıntısı
   (`tracemalloc`) olmamalı ve tüm zincir hızlı çalışmalıdır.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
import gc
from pathlib import Path
import time
import tracemalloc
from typing import Any

import pytest

from src.config.settings import load_settings
from src.rag.chunker import chunk_pdf_document
from src.rag.embedder import MultilingualEmbedder
from src.rag.pdf_loader import extract_text_from_pdf
from src.rag.vector_store import VectorStoreManager
from src.scraper.domain_filter import DomainFilter
from src.scraper.email_extractor import EmailExtractor, EmailTier
from src.scraper.page_fetcher import PlaywrightPageFetcher
from src.scraper.query_builder import build_dork_queries
from src.scraper.rate_limiter import RateLimiter
from src.scraper.search_engine import SearchEngineClient
from src.utils.logger import setup_logging
from tests.integration.test_milestone_1_rag import (
    MilestoneSemanticBackend,
    _build_realistic_candidate_pdfs,
)


class MockPlaywrightResponse:
    """Playwright `page.goto()` çağrısından dönen HTTP yanıtını (status code) simüle eder."""

    def __init__(self, status: int = 200) -> None:
        self.status = status


class MockPlaywrightPage:
    """Playwright `Page` (sekme) nesnesini simüle eder ve sekmenin kapatılıp kapatılmadığını
    (`closed`) takip ederek Zombie Process (açık kalan sekme) denetimi yapılmasını sağlar.
    """

    def __init__(self, site_database: dict[str, dict[str, Any]], context_tracker: MockBrowserContext) -> None:
        self._site_db = site_database
        self._tracker = context_tracker
        self._current_url: str = ""
        self.closed: bool = False

    async def goto(self, url: str, timeout: int = 20000, wait_until: str = "domcontentloaded") -> MockPlaywrightResponse:
        """Verilen URL'yi sanal site veritabanından yükler veya simüle edilmiş ağ hatası fırlatır."""
        _ = (timeout, wait_until)
        self._current_url = url.rstrip("/")
        entry = self._site_db.get(self._current_url)

        if entry is None:
            # Tanımlanmamış alt sayfalar (örn. /about-us) gerçek hayattaki gibi HTTP 404 döner
            return MockPlaywrightResponse(status=404)

        if "exception" in entry and entry["exception"] is not None:
            raise entry["exception"]

        return MockPlaywrightResponse(status=int(entry.get("status", 200)))

    async def content(self) -> str:
        """Sayfanın ham HTML kaynak kodunu döndürür."""
        entry = self._site_db.get(self._current_url, {})
        return str(entry.get("html", ""))

    async def title(self) -> str:
        """Sayfanın `<title>` başlığını döndürür."""
        entry = self._site_db.get(self._current_url, {})
        return str(entry.get("title", ""))

    async def close(self) -> None:
        """Sekmeyi kapatır ve aktif sekme sayacını düşürür."""
        if not self.closed:
            self.closed = True
            self._tracker.active_open_pages -= 1
            self._tracker.total_closed_pages += 1


class MockBrowserContext:
    """Açılan ve kapatılan tüm Playwright sekmelerini (`MockPlaywrightPage`) izleyen bağlam."""

    def __init__(self, site_database: dict[str, dict[str, Any]]) -> None:
        self.site_database = site_database
        self.active_open_pages: int = 0
        self.total_opened_pages: int = 0
        self.total_closed_pages: int = 0

    async def new_page(self) -> MockPlaywrightPage:
        """Yeni bir sanal sekme açar ve sayacı artırır."""
        self.active_open_pages += 1
        self.total_opened_pages += 1
        return MockPlaywrightPage(self.site_database, self)


def _build_mock_web_ecosystem() -> dict[str, dict[str, Any]]:
    """Milestone 2 denetimi için gerçekçi Türk ve yabancı robotik/otonom sistem şirketlerinin
    ana sayfa, `/kariyer`, `/hakkimizda` ve `/iletisim` sayfalarını içeren sanal web dünyası.

    İçerdiği Tuzaklar:
    - Sayfa içinde `support@`, `sales@`, `noreply@`, `kvkk@`, `support@f6s.com` tuzakları,
    - `logo@2x.png`, `hero-banner@3x.webp`, `bundle@1.4.2.js` gibi sahte `@` içeren statik dosyalar,
    - `mailto:%20kariyer@baykar.example.tr?subject=Basvuru` ve `\\u003ehr@autonav.example.io`
      gibi kirli/kodlanmış bağlantılar,
    - Çöken/zaman aşımına uğrayan (`TimeoutError`) bozuk bir şirket sitesi.
    """
    return {
        # ------------------------------------------------------------------
        # ŞİRKET 1: Türk Savunma & Otonom İHA Şirketi (TR + HIGH_PRIORITY İK maili)
        # ------------------------------------------------------------------
        "https://baykar-otonom.example.tr": {
            "status": 200,
            "title": "Baykar Otonom Sistemler | İnsansız Hava Araçları",
            "html": """
            <html>
              <head><title>Baykar Otonom Sistemler | İnsansız Hava Araçları</title></head>
              <body>
                <img src="/assets/logo@2x.png" alt="Logo" />
                <h1>GNSS-Bağımsız Otonom İHA ve Sürü Navigasyon Teknolojileri</h1>
                <p>Türkiye'nin öncü otonom uçuş kontrol, LiDAR SLAM ve sensör füzyonu Ar-Ge merkezi.</p>
                <a href="/kariyer">Kariyer Fırsatları</a>
                <a href="/hakkimizda">Hakkımızda</a>
                <footer>
                  Müşteri Destek: <a href="mailto:support@baykar-otonom.example.tr">support@baykar-otonom.example.tr</a>
                  KVKK Başvuru: kvkk@baykar-otonom.example.tr
                  F6S Profilimiz: support@f6s.com
                </footer>
              </body>
            </html>
            """,
        },
        "https://baykar-otonom.example.tr/kariyer": {
            "status": 200,
            "title": "Kariyer | Baykar Otonom Sistemler",
            "html": """
            <html>
              <body>
                <h2>Robotik ve ROS 2 Yazılım Mühendisi Arıyoruz</h2>
                <p>SLAM, Fast-LIO2, PX4 ve Bilgisayarlı Görü projelerimizde görev alacak mühendisler
                özgeçmişlerini insan kaynakları ekibimize iletebilir.</p>
                <a href="mailto:%20kariyer@baykar-otonom.example.tr?subject=Otonom%20Sistemler%20Basvurusu">
                  İK Başvuru
                </a>
                <p>Genel bilgi için: info@baykar-otonom.example.tr ve noreply@baykar-otonom.example.tr</p>
              </body>
            </html>
            """,
        },
        "https://baykar-otonom.example.tr/hakkimizda": {
            "status": 200,
            "title": "Hakkımızda | Baykar Otonom Sistemler",
            "html": """
            <html>
              <body>
                <p>Şirketimiz derin öğrenme, YOLOv12 tabanlı havadan nesne tespiti ve gömülü
                yazılım sistemleri geliştirmektedir.</p>
              </body>
            </html>
            """,
        },
        # ------------------------------------------------------------------
        # ŞİRKET 2: Yabancı Robotik & SLAM Şirketi (EN + HIGH_PRIORITY + LOW_PRIORITY)
        # ------------------------------------------------------------------
        "https://autonav-robotics.example.io": {
            "status": 200,
            "title": "AutoNav Robotics - 3D LiDAR SLAM & Autonomous Mobile Robots",
            "html": """
            <html>
              <head><script src="https://cdn.example.com/app@2.1.0.js"></script></head>
              <body>
                <h1>Autonomous Navigation in GPS-Denied Environments</h1>
                <p>We build ROS 2, LIO-SAM and Cartographer SLAM solutions for industrial inspection drones.</p>
                <a href="/careers">Join Our Team</a>
                <p>Sales inquiries: sales@autonav-robotics.example.io | Helpdesk: help@autonav-robotics.example.io</p>
              </body>
            </html>
            """,
        },
        "https://autonav-robotics.example.io/careers": {
            "status": 200,
            "title": "Careers - AutoNav Robotics",
            "html": """
            <html>
              <body>
                <h2>Open Roles: Robotics Software & Embedded Edge AI Engineer</h2>
                <p>Send your portfolio to "\\u003ehr@autonav-robotics.example.io" or general
                inquiries to contact@autonav-robotics.example.io.</p>
              </body>
            </html>
            """,
        },
        # ------------------------------------------------------------------
        # ŞİRKET 3: Sadece Genel İletişim (`info@`) Adresi Olan Su Altı Aracı Şirketi (TR -> LOW_PRIORITY)
        # ------------------------------------------------------------------
        "https://derin-auv.example.com.tr": {
            "status": 200,
            "title": "Derin AUV Teknolojileri - İnsansız Su Altı Araçları",
            "html": """
            <html>
              <body>
                <h1>Biyomimetik Otonom Su Altı Aracı (AUV) ve Sonar Sistemleri</h1>
                <p>Su altı görüntü iyileştirme, hedef tespiti ve akustik haberleşme çözümleri üretiyoruz.</p>
                <p>Satış: satis@derin-auv.example.com.tr | Destek: destek@derin-auv.example.com.tr</p>
                <p>Bize ulaşın: <a href="mailto:iletisim@derin-auv.example.com.tr">iletisim@derin-auv.example.com.tr</a></p>
              </body>
            </html>
            """,
        },
        # ------------------------------------------------------------------
        # ŞİRKET 4: Sadece Kara Liste (`support@`, `noreply@`) Adresi Olan Şirket (Sıfır Geçerli Mail!)
        # ------------------------------------------------------------------
        "https://locked-drone.example.org": {
            "status": 200,
            "title": "LockedDrone Systems - Industrial UAV",
            "html": """
            <html>
              <body>
                <h1>Enterprise UAV Hardware</h1>
                <p>Only customer support is available: support@locked-drone.example.org,
                billing@locked-drone.example.org, no-reply@locked-drone.example.org,
                605a7b9f8c1d4e2a9b8c7d6e5f4a3b2c@sentry.io</p>
              </body>
            </html>
            """,
        },
        # ------------------------------------------------------------------
        # ŞİRKET 5: Zaman Aşımına Uğrayan (TimeoutError) Bozuk Sunucu (Zombie Process Testi)
        # ------------------------------------------------------------------
        "https://timeout-robotics.example.net": {
            "exception": TimeoutError("Navigation timeout of 20000 ms exceeded"),
        },
    }


class TestMilestone2EndToEndDiscoveryAndExtraction:
    """Faz 1 – Faz 8 arası tüm keşif, filtreleme ve e-posta kazıma modüllerinin uçtan uca denetimi."""

    def setup_method(self) -> None:
        MultilingualEmbedder.reset_instance()

    def teardown_method(self) -> None:
        MultilingualEmbedder.reset_instance()

    def test_end_to_end_rag_to_dork_to_filter_to_playwright_to_email_extraction(
        self, tmp_path: Path
    ) -> None:
        """Uçtan Uca Senaryo:
        1. `settings.yaml` konfigürasyonunu ve merkezi UTF-8 logger'ı yükle.
        2. Adayın CV/Portfolyo PDF'lerini RAG vektör veritabanına (ChromaDB) kaydet ve
           semantik sorgu ile adayın öne çıkan teknik uzmanlıklarını çek.
        3. Çekilen uzmanlıklarla negatif `-site:` operatörlü Dork arama sorguları üret.
        4. Arama motorundan (hem gerçek şirketler hem de `clutch.co`, `f6s.com`, `kariyer.net`,
           *"Top 10 Robotics Companies"* gibi tuzaklar içeren) sonuçları al.
        5. `DomainFilter` ile tüm rehber/liste sitelerini %100 ele.
        6. `PlaywrightPageFetcher` + `RateLimiter` ile kalan şirket sitelerini asenkron çek.
        7. `EmailExtractor` ile `support@`, `sales@`, `noreply@`, `logo@2x.png` tuzaklarını eleyip
           doğru İK (`HIGH_PRIORITY`) veya genel iletişim (`LOW_PRIORITY`) adreslerini çıkar.
        """
        # ------------------------------------------------------------------
        # ADIM 1: Faz 1 — Konfigürasyon ve Merkezi Logger Yükleme
        # ------------------------------------------------------------------
        project_root = Path(__file__).resolve().parent.parent.parent
        settings = load_settings(
            yaml_path=project_root / "config" / "settings.yaml",
            env_path=tmp_path / "non_existent.env",
        )
        log_file = tmp_path / "logs" / "milestone2.log"
        logger = setup_logging(
            log_file=log_file,
            level=settings.logging.level,
            logger_name="milestone2_audit",
        )
        logger.info("Milestone 2 keşif ve e-posta kazıma entegrasyon denetimi başladı.")

        # ------------------------------------------------------------------
        # ADIM 2: Faz 2, 3, 4 — RAG Hattından Aday Yetkinliklerinin Çekilmesi
        # ------------------------------------------------------------------
        pdf_dir = tmp_path / "candidate_docs"
        pdf_dir.mkdir(parents=True, exist_ok=True)
        tr_cv_path, en_port_path = _build_realistic_candidate_pdfs(pdf_dir)

        tr_doc = extract_text_from_pdf(tr_cv_path)
        en_doc = extract_text_from_pdf(en_port_path)
        chunks = chunk_pdf_document(
            tr_doc,
            chunk_size=settings.rag.chunk_size,
            chunk_overlap=settings.rag.chunk_overlap,
        ) + chunk_pdf_document(
            en_doc,
            chunk_size=settings.rag.chunk_size,
            chunk_overlap=settings.rag.chunk_overlap,
        )

        embedder = MultilingualEmbedder.get_instance(
            model_name=settings.rag.embedding_model,
            model_loader=lambda _: MilestoneSemanticBackend(),
        )
        vector_store = VectorStoreManager(
            persist_directory=tmp_path / "chroma_m2",
            collection_name=settings.rag.collection_name,
            embedder=embedder,
        )
        vector_store.upsert_chunks(chunks)

        # RAG'den adayın SLAM/İHA ve AUV projelerini sorgula ve arama anahtar kelimelerini doğrula
        rag_hits = vector_store.query("SLAM ROS 2 Otonom İHA AUV", top_k=3)
        assert len(rag_hits) == 3
        combined_rag_context = " ".join(hit.text for hit in rag_hits)

        discovered_keywords: list[str] = []
        for candidate_kw in ("ROS 2", "Fast-LIO2", "AUV"):
            if candidate_kw in combined_rag_context:
                discovered_keywords.append(candidate_kw)
        assert len(discovered_keywords) >= 2

        # ------------------------------------------------------------------
        # ADIM 3: Faz 5 — Dork Sorgu Üretimi ve Dayanıklı Arama Motoru
        # ------------------------------------------------------------------
        dork_queries = build_dork_queries(
            keywords=discovered_keywords,
            location="Türkiye",
            intent="kariyer",
            excluded_domains=settings.scraper.blacklisted_domains,
        )
        assert len(dork_queries) == len(discovered_keywords)
        # Her sorguda negatif -site: operatörlerinin yer aldığını doğrula
        for q in dork_queries:
            assert "-site:clutch.co" in q
            assert "-site:f6s.com" in q
            assert "-site:linkedin.com" in q
            assert "-site:kariyer.net" in q

        # Arama motorunun döndürdüğü (içinde rehber siteleri, liste yazıları ve mükerrer domainler
        # barındıran) gerçekçi ve tuzaklı arama sonuçları:
        simulated_ddg_payload: list[dict[str, str]] = [
            # TUZAK 1: B2B Rehber Siteleri (clutch.co, f6s.com, tr.linkedin.com, kariyer.net, europages)
            {
                "title": "Top Robotics Companies in Turkey - Clutch.co",
                "href": "https://www.clutch.co/tr/it-services/robotics",
                "body": "Find the best robotics firms.",
            },
            {
                "title": "F6S Robotics Startups in Istanbul",
                "href": "https://www.f6s.com/companies/robotics/turkey/co",
                "body": "Contact support@f6s.com for startup listings.",
            },
            {
                "title": "Robotik Mühendisi İş İlanları - Kariyer.net",
                "href": "https://www.kariyer.net/is-ilanlari/robotik-muhendisi",
                "body": "Güncel iş ilanları.",
            },
            {
                "title": "ROS 2 Jobs in Türkiye | LinkedIn",
                "href": "https://tr.linkedin.com/jobs/ros-2-is-ilanlari",
                "body": "Apply on LinkedIn.",
            },
            # TUZAK 2: Domain kara listede olmasa bile başlığı "Top 10 / En İyi 15" olan blog/liste siteleri
            {
                "title": "Top 10 Autonomous Drone Companies to Watch in 2026",
                "href": "https://techblog-insider.example.com/top-10-drone-companies",
                "body": "A curated list of top 10 robotics startups.",
            },
            {
                "title": "Türkiye'deki En İyi 15 Robotik Şirketi ve Firma Rehberi",
                "href": "https://endustri-haber.example.com/en-iyi-15-robotik-sirketi",
                "body": "B2B şirketleri listesi.",
            },
            # TUZAK 3: PDF Dosya linki (HTML sayfası değil)
            {
                "title": "Brochure PDF",
                "href": "https://baykar-otonom.example.tr/docs/catalog.pdf",
                "body": "Download PDF.",
            },
            # GERÇEK ŞİRKET 1 (ve aynı şirketin mükerrer alt sayfası)
            {
                "title": "Baykar Otonom Sistemler | İnsansız Hava Araçları",
                "href": "https://baykar-otonom.example.tr",
                "body": "GNSS-bağımsız otonom İHA ve ROS 2 SLAM çözümleri.",
            },
            {
                "title": "Baykar Otonom - İletişim",
                "href": "https://www.baykar-otonom.example.tr/iletisim",
                "body": "Aynı domain'in ikinci sonucu (deduplicate_domains ile tekilleştirilmeli).",
            },
            # GERÇEK ŞİRKET 2 (Yabancı İngilizce Robotik Şirketi)
            {
                "title": "AutoNav Robotics - 3D LiDAR SLAM",
                "href": "https://autonav-robotics.example.io",
                "body": "Autonomous mobile robots and LIO-SAM navigation.",
            },
            # GERÇEK ŞİRKET 3 (Sadece düşük öncelikli iletisim@ adresi olan AUV şirketi)
            {
                "title": "Derin AUV Teknolojileri | Su Altı Sistemleri",
                "href": "https://derin-auv.example.com.tr",
                "body": "Otonom su altı aracı (AUV) Ar-Ge merkezi.",
            },
            # GERÇEK ŞİRKET 4 (Sadece support@ ve noreply@ adresi olan şirket)
            {
                "title": "LockedDrone Systems",
                "href": "https://locked-drone.example.org",
                "body": "Industrial UAV hardware.",
            },
            # GERÇEK ŞİRKET 5 (Sunucusu Timeout veren şirket)
            {
                "title": "Timeout Robotics Lab",
                "href": "https://timeout-robotics.example.net",
                "body": "Experimental robotics lab.",
            },
        ]

        call_index = 0

        def mock_ddg_backend(query: str, max_results: int) -> Sequence[dict[str, Any]]:
            nonlocal call_index
            _ = (query, max_results)
            call_index += 1
            # İkinci sorguda DuckDuckGo 429 Rate Limit simülasyonu yapalım ki
            # SearchEngineClient.search_batch'in kısmi arızada çökmediği de kanıtlansın!
            if call_index == 2:
                raise RuntimeError("HTTP 429 Too Many Requests (DuckDuckGo Ratelimit)")
            return simulated_ddg_payload

        search_client = SearchEngineClient(
            max_results=settings.scraper.max_results_per_query,
            search_backend=mock_ddg_backend,
        )
        raw_search_results = search_client.search_batch(dork_queries)
        assert len(raw_search_results) == len(simulated_ddg_payload)

        # ------------------------------------------------------------------
        # ADIM 4: Faz 6 — Anti-Aggregator Domain ve Başlık Filtreleme
        # ------------------------------------------------------------------
        domain_filter = DomainFilter(
            blacklisted_domains=settings.scraper.blacklisted_domains,
            blacklisted_title_patterns=settings.scraper.blacklisted_title_patterns,
        )
        filtered_companies = domain_filter.filter_results(
            raw_search_results,
            deduplicate_domains=True,
        )

        filtered_urls = [item.url for item in filtered_companies]

        # KIRMIZI ÇİZGİ #1 KANITI: Hiçbir rehber sitesi, liste yazısı veya PDF linki sızmamalı!
        assert not any("clutch.co" in u for u in filtered_urls)
        assert not any("f6s.com" in u for u in filtered_urls)
        assert not any("kariyer.net" in u for u in filtered_urls)
        assert not any("linkedin.com" in u for u in filtered_urls)
        assert not any("techblog-insider" in u for u in filtered_urls)
        assert not any("endustri-haber" in u for u in filtered_urls)
        assert not any(u.endswith(".pdf") for u in filtered_urls)

        # Mükerrer domain tekilleştirilmiş olmalı ve tam olarak 5 benzersiz gerçek şirket kalmalı
        assert len(filtered_companies) == 5
        assert filtered_urls == [
            "https://baykar-otonom.example.tr",
            "https://autonav-robotics.example.io",
            "https://derin-auv.example.com.tr",
            "https://locked-drone.example.org",
            "https://timeout-robotics.example.net",
        ]

        # ------------------------------------------------------------------
        # ADIM 5: Faz 7 & Faz 8 — Playwright Kazıma + 3 Katmanlı E-posta Çıkarımı
        # ------------------------------------------------------------------
        recorded_delays: list[float] = []

        async def fast_mock_sleep(delay: float) -> None:
            recorded_delays.append(delay)

        rate_limiter = RateLimiter(
            min_sec=settings.scraper.rate_limit_min_sec,
            max_sec=settings.scraper.rate_limit_max_sec,
            sleep_func=fast_mock_sleep,
        )
        page_fetcher = PlaywrightPageFetcher(
            timeout_ms=settings.scraper.timeout_ms,
            rate_limiter=rate_limiter,
            target_subpages=settings.scraper.target_subpages,
            user_agent=settings.scraper.user_agent,
        )
        email_extractor = EmailExtractor(
            blacklisted_prefixes=settings.scraper.blacklisted_email_prefixes,
            high_priority_prefixes=settings.scraper.high_priority_email_prefixes,
            low_priority_prefixes=settings.scraper.low_priority_email_prefixes,
            blacklisted_domains=settings.scraper.blacklisted_domains,
        )

        mock_context = MockBrowserContext(_build_mock_web_ecosystem())

        async def run_scraping_pipeline() -> dict[str, tuple[Any, list[Any], Any]]:
            results_map: dict[str, tuple[Any, list[Any], Any]] = {}
            for candidate in filtered_companies:
                scrape_res = await page_fetcher.fetch_company_site(
                    candidate.url,
                    browser_context=mock_context,
                )
                extracted_list = email_extractor.extract(
                    content=scrape_res.combined_html,
                    links=scrape_res.all_links,
                )
                best_email = email_extractor.select_best_email(
                    content=scrape_res.combined_html,
                    links=scrape_res.all_links,
                )
                results_map[candidate.url] = (scrape_res, extracted_list, best_email)
            return results_map

        pipeline_output = asyncio.run(run_scraping_pipeline())

        # KIRMIZI ÇİZGİ #3 (ZOMBIE PROCESS KANITI):
        # Timeout veren sunucu dahil açılan TÜM sekmeler (`page.close()`) kapatılmış olmalı!
        assert mock_context.total_opened_pages > 0
        assert mock_context.active_open_pages == 0
        assert mock_context.total_opened_pages == mock_context.total_closed_pages

        # RateLimiter tüm ardışık sayfa istekleri arasında yapılandırılan sınırlar içinde beklemiş olmalı
        assert len(recorded_delays) == mock_context.total_opened_pages - 1
        assert all(
            settings.scraper.rate_limit_min_sec <= d <= settings.scraper.rate_limit_max_sec
            for d in recorded_delays
        )

        # ŞİRKET 1 DOĞRULAMASI (Baykar Otonom - TR):
        # Sayfada support@, kvkk@, noreply@, support@f6s.com, logo@2x.png, info@ ve kariyer@ vardı.
        # Kara listedekiler elenmeli, en iyi e-posta olarak 'kariyer@baykar-otonom.example.tr' seçilmeli!
        baykar_scrape, baykar_emails, baykar_best = pipeline_output["https://baykar-otonom.example.tr"]
        assert baykar_scrape.language == "TR"
        assert "Fast-LIO2" in baykar_scrape.combined_text
        assert baykar_best is not None
        assert baykar_best.email == "kariyer@baykar-otonom.example.tr"
        assert baykar_best.tier == EmailTier.HIGH_PRIORITY
        baykar_email_strs = [c.email for c in baykar_emails]
        assert baykar_email_strs == [
            "kariyer@baykar-otonom.example.tr",
            "info@baykar-otonom.example.tr",
        ]
        assert "support@baykar-otonom.example.tr" not in baykar_email_strs
        assert "kvkk@baykar-otonom.example.tr" not in baykar_email_strs
        assert "noreply@baykar-otonom.example.tr" not in baykar_email_strs
        assert "support@f6s.com" not in baykar_email_strs

        # ŞİRKET 2 DOĞRULAMASI (AutoNav Robotics - EN):
        # Sayfada sales@, help@, app@2.1.0.js, \u003ehr@ ve contact@ vardı.
        # \u003e temizlenmeli ve 'hr@autonav-robotics.example.io' (HIGH_PRIORITY) seçilmeli!
        autonav_scrape, autonav_emails, autonav_best = pipeline_output["https://autonav-robotics.example.io"]
        assert autonav_scrape.language == "EN"
        assert autonav_best is not None
        assert autonav_best.email == "hr@autonav-robotics.example.io"
        assert autonav_best.tier == EmailTier.HIGH_PRIORITY
        assert [c.email for c in autonav_emails] == [
            "hr@autonav-robotics.example.io",
            "contact@autonav-robotics.example.io",
        ]

        # ŞİRKET 3 DOĞRULAMASI (Derin AUV - TR, Sadece Genel İletişim):
        # Sayfada satis@, destek@ ve iletisim@ vardı -> 'iletisim@derin-auv.example.com.tr' (LOW_PRIORITY)
        auv_scrape, auv_emails, auv_best = pipeline_output["https://derin-auv.example.com.tr"]
        assert auv_scrape.language == "TR"
        assert auv_best is not None
        assert auv_best.email == "iletisim@derin-auv.example.com.tr"
        assert auv_best.tier == EmailTier.LOW_PRIORITY
        assert len(auv_emails) == 1

        # ŞİRKET 4 DOĞRULAMASI (LockedDrone - Sadece support@, billing@, no-reply@, sentry.io):
        # Hiçbir kara liste adresi geçmemeli -> best_email None olmalı!
        _, locked_emails, locked_best = pipeline_output["https://locked-drone.example.org"]
        assert locked_emails == []
        assert locked_best is None

        # ŞİRKET 5 DOĞRULAMASI (Timeout Robotics - Sunucu Hatası):
        # Sistem çökmeden boş sonuç dönmeli
        timeout_scrape, timeout_emails, timeout_best = pipeline_output["https://timeout-robotics.example.net"]
        assert timeout_scrape.pages == []
        assert timeout_emails == []
        assert timeout_best is None

    def test_milestone_2_memory_leak_zombie_process_and_performance_sla_audit(
        self, tmp_path: Path
    ) -> None:
        """Bellek sızıntısı (`tracemalloc`), açık kalan sekme (`active_open_pages == 0`)
        ve performans SLA (< 3.0 saniye) stres denetimi.
        """
        _ = tmp_path
        start_time = time.perf_counter()

        domain_filter = DomainFilter()
        email_extractor = EmailExtractor()

        async def zero_sleep(_: float) -> None:
            return None

        fetcher = PlaywrightPageFetcher(
            timeout_ms=5000,
            rate_limiter=RateLimiter(min_sec=0.01, max_sec=0.02, sleep_func=zero_sleep),
            target_subpages=["/kariyer", "/careers", "/iletisim"],
        )
        mock_context = MockBrowserContext(_build_mock_web_ecosystem())

        gc.collect()
        tracemalloc.start()
        mem_before, _ = tracemalloc.get_traced_memory()

        async def stress_cycle() -> None:
            for _ in range(10):
                queries = build_dork_queries(["ROS 2", "SLAM", "Otonom İHA"])
                client = SearchEngineClient(
                    max_results=10,
                    search_backend=lambda q, m: [
                        {"title": "Clutch List", "href": "https://clutch.co/robotics", "body": q},
                        {"title": "Baykar Otonom", "href": "https://baykar-otonom.example.tr", "body": q},
                        {"title": "AutoNav", "href": "https://autonav-robotics.example.io", "body": q},
                        {"title": "Timeout Site", "href": "https://timeout-robotics.example.net", "body": q},
                    ],
                )
                raw = client.search_batch(queries)
                valid = domain_filter.filter_results(raw)
                for item in valid:
                    scraped = await fetcher.fetch_company_site(item.url, browser_context=mock_context)
                    _ = email_extractor.extract(scraped.combined_html, links=scraped.all_links)

        asyncio.run(stress_cycle())

        gc.collect()
        mem_after, _ = tracemalloc.get_traced_memory()
        tracemalloc.stop()

        elapsed = time.perf_counter() - start_time
        memory_growth = mem_after - mem_before

        # 1. Açık kalan (zombie) sekme sayısı kesinlikle 0 olmalı
        assert mock_context.active_open_pages == 0
        assert mock_context.total_opened_pages == 120  # 10 döngü * 3 geçerli şirket * 4 sayfa (ana + 3 alt)
        assert mock_context.total_closed_pages == 120

        # 2. 10 tam döngü sonunda bellek artışı 5 MB'ın altında olmalı
        assert memory_growth < 5 * 1024 * 1024

        # 3. 120 sayfalık simüle edilmiş kazıma + e-posta çıkarımı 3 saniyenin altında bitmeli
        assert elapsed < 3.0

    def test_real_workspace_pdfs_drive_milestone_2_discovery_pipeline(
        self, tmp_path: Path
    ) -> None:
        """Çalışma dizinindeki gerçek `cv/` ve `portfolio/` PDF'lerinden okunan
        metinlerin RAG üzerinden Dork arama sorgusuna ve e-posta keşif hattına
        sorunsuz bağlandığını doğrulayan gerçek veri testi.
        """
        project_root = Path(__file__).resolve().parent.parent.parent
        real_tr_cv = project_root / "cv" / "01_Muhammed_Enes_BAL_CV_TR.pdf"
        real_portfolio = project_root / "portfolio" / "01_Muhammed_Enes_BAL_Portfolyo.pdf"

        if not (real_tr_cv.exists() and real_portfolio.exists()):
            pytest.skip("Gerçek CV/Portfolyo PDF dosyaları bulunamadı.")

        doc_tr = extract_text_from_pdf(real_tr_cv)
        chunks_tr = chunk_pdf_document(doc_tr, chunk_size=600, chunk_overlap=100)

        embedder = MultilingualEmbedder.get_instance(
            model_loader=lambda _: MilestoneSemanticBackend()
        )
        store = VectorStoreManager(
            persist_directory=tmp_path / "chroma_real_m2",
            collection_name="real_m2_collection",
            embedder=embedder,
        )
        store.upsert_chunks(chunks_tr)

        hits = store.query("ROS 2 SLAM Fast-LIO2", top_k=2)
        assert len(hits) >= 1

        # Gerçek CV içinde geçen teknik terimlerle Dork sorgusu üret
        dorks = build_dork_queries(["ROS 2", "Fast-LIO2"], location="Türkiye", intent="Ar-Ge")
        assert len(dorks) == 2
        assert all("-site:f6s.com" in d and "-site:clutch.co" in d for d in dorks)
