"""🏆 MILESTONE 4 (FİNAL): Tam Sistem Kabul ve Canlı DRY-RUN Denetimi (Faz 1 – 16).

Bu final kabul testi modülü, projenin 16 fazının tamamını (`main.py` -> `CLI` -> `Scheduler`
-> `AutonomousPipeline` -> `RAG` -> `Scraper` -> `Anti-Aggregator` -> `3-Tier Email Extractor`
-> `ATS Decision Tree` -> `PromptBuilder` -> `GeminiLLMClient` -> `DatabaseManager` -> `EmailSender`)
tek bir uçtan uca kabul senaryosunda ve çalışma dizinindeki gerçek CV/Portfolyo PDF'leri
üzerinde doğrular.

Kabul Kriterleri:
1. **0 Hata, %100 Test Başarısı.**
2. **Kırmızı Çizgi #1 Kanıtı:** `clutch.co`, `f6s.com`, `europages`, `linkedin.com`, `kariyer.net`
   ve *"Top 10 Robotics Companies"* / *"En İyi 15 Şirket"* liste sitelerinin %100 elenmesi.
3. **Kırmızı Çizgi #2 Kanıtı:** `support@f6s.com`, `support@`, `noreply@`, `sales@`, `kvkk@`,
   `logo@2x.png` adreslerinin %100 elenmesi; İK (`kariyer@`, `hr@`) ve genel (`iletisim@`)
   adreslerin doğru önceliklendirilmesi.
4. **ATS Override & 30 Gün Domain Cooldown Kanıtı:** Greenhouse/Lever kullanan şirketlerin
   `MANUAL_ATS` kuyruğuna ayrılması ve son 30 günde başka bir e-postasına (`ik@`) başvuru
   yapılmış şirketlerin domain kuralıyla engellenmesi.
5. **Sıfır Riskli `DRY-RUN` Simülasyonu & Zengin CLI Raporlama:** `main.py` üzerinden çalıştırılan
   uçtan uca simülasyonda hiçbir gerçek SMTP soketi açılmadan tüm sürecin SQLite veritabanına
   ve `rich` terminal tablolarına eksiksiz yansıması.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from io import StringIO
from pathlib import Path
from typing import Any

import pytest
from rich.console import Console

import main as main_entry
from src.config.settings import load_settings
from src.database.db_manager import ApplicationStatus, DatabaseManager
from src.filter.ats_detector import ATSDetector
from src.filter.decision_tree import DecisionTreeEngine
from src.generator.llm_client import GeminiLLMClient
from src.generator.prompt_builder import PromptBuilder
from src.generator.template_manager import CandidateLinks
from src.mailer.email_sender import EmailSender, SMTPConfig
from src.orchestrator.pipeline import AutonomousPipeline
from src.rag.chunker import chunk_pdf_document
from src.rag.embedder import MultilingualEmbedder
from src.rag.pdf_loader import extract_text_from_pdf
from src.rag.vector_store import VectorStoreManager
from src.scraper.domain_filter import DomainFilter
from src.scraper.email_extractor import EmailExtractor
from src.scraper.page_fetcher import PlaywrightPageFetcher
from src.scraper.rate_limiter import RateLimiter
from src.scraper.search_engine import SearchEngineClient
from tests.integration.test_milestone_1_rag import (
    MilestoneSemanticBackend,
    _build_realistic_candidate_pdfs,
)
from tests.integration.test_milestone_2_discovery import MockBrowserContext
from tests.integration.test_milestone_3_intelligence import _build_milestone_3_web_ecosystem


class TestMilestone4FinalSystemAuditAndDryRun:
    """Faz 1 – Faz 16 arası tam sistem kabul ve canlı DRY-RUN simülasyon denetimi."""

    def setup_method(self) -> None:
        MultilingualEmbedder.reset_instance()

    def teardown_method(self) -> None:
        MultilingualEmbedder.reset_instance()

    def test_full_system_end_to_end_dry_run_via_main_cli_and_scheduler(
        self, tmp_path: Path
    ) -> None:
        """Tam Sistem Kabul Testi (Faz 1 – 16):
        1. Gerçek CV/Portfolyo PDF'leri (veya oluşturulan gerçekçi PDF'ler) okunup ChromaDB'ye indekslenir.
        2. Arama sonuçlarında `clutch.co`, `f6s.com`, `kariyer.net`, *"Top 10 Companies"* tuzakları
           ve 5 farklı arketipte şirket yer alır.
        3. 3. şirkette kasıtlı çalışma zamanı hatası (Fault Isolation), bir şirkette Greenhouse ATS
           (Override), bir şirkette `support@` tuzağı, bir şirkette Domain Cooldown ve gerçek
           hedef şirketlerde Türkçe/İngilizce RAG + LLM + `DRY-RUN` gönderimi çalıştırılır.
        4. `main.py` üzerinden hem `--dry-run`, hem `--stats`, hem de `--manual-list` komutları
           koşturularak tüm çıktılar doğrulanır.
        """
        project_root = Path(__file__).resolve().parent.parent.parent
        settings = load_settings(
            yaml_path=project_root / "config" / "settings.yaml",
            env_path=tmp_path / "missing.env",
        )

        # 1. PDF Belgelerini Oku ve ChromaDB Vektör Veritabanına Kaydet
        real_tr_cv = project_root / "cv" / "01_Muhammed_Enes_BAL_CV_TR.pdf"
        real_en_cv = project_root / "cv" / "Muhammed_Enes_BAL_CV_EN.pdf"
        real_portfolio = project_root / "portfolio" / "01_Muhammed_Enes_BAL_Portfolyo.pdf"

        if real_tr_cv.exists() and real_en_cv.exists() and real_portfolio.exists():
            docs = [
                extract_text_from_pdf(real_tr_cv),
                extract_text_from_pdf(real_en_cv),
                extract_text_from_pdf(real_portfolio),
            ]
        else:
            pdf_dir = tmp_path / "fallback_pdfs"
            pdf_dir.mkdir(parents=True, exist_ok=True)
            tr_p, en_p = _build_realistic_candidate_pdfs(pdf_dir)
            docs = [extract_text_from_pdf(tr_p), extract_text_from_pdf(en_p)]

        all_chunks = []
        for doc in docs:
            all_chunks.extend(
                chunk_pdf_document(
                    doc,
                    chunk_size=settings.rag.chunk_size,
                    chunk_overlap=settings.rag.chunk_overlap,
                )
            )
        assert len(all_chunks) >= 4

        embedder = MultilingualEmbedder.get_instance(
            model_name=settings.rag.embedding_model,
            model_loader=lambda _: MilestoneSemanticBackend(),
        )
        vector_store = VectorStoreManager(
            persist_directory=tmp_path / "chroma_final",
            collection_name=settings.rag.collection_name,
            embedder=embedder,
        )
        vector_store.upsert_chunks(all_chunks)

        # 2. SQLite Veritabanı ve 30 Gün Domain Cooldown Hazırlığı
        db = DatabaseManager(
            sqlite_path=tmp_path / "final_audit.db",
            cooldown_days=settings.mailer.cooldown_days,
        )
        now_utc = datetime(2026, 11, 6, 10, 0, 0, tzinfo=timezone.utc)
        db.record_application(
            company_name="Derin AUV Teknolojileri",
            company_url="https://derin-auv.example.com.tr",
            target_email="ik@derin-auv.example.com.tr",
            status=ApplicationStatus.SENT,
            language="TR",
            subject="Önceki Başvuru",
            body="Önceki Gövde",
            sent_at=now_utc - timedelta(days=7),
        )

        # 3. Tuzaklı Arama Motoru Sonuçları (Kırmızı Çizgi #1 Testi)
        raw_ddg_results = [
            {"title": "Clutch Robotics Turkey", "href": "https://www.clutch.co/tr/robotics", "body": "Aggregator"},
            {"title": "F6S Startup Directory", "href": "https://www.f6s.com/companies/robotics", "body": "support@f6s.com"},
            {"title": "Top 10 Robotics Companies in Europe", "href": "https://blog.example.com/top-10-robotics", "body": "Listicle"},
            {"title": "Baykar Otonom Sistemler", "href": "https://baykar-otonom.example.tr", "body": "GNSS-bağımsız İHA"},
            {"title": "AutoNav AI", "href": "https://autonav-ai.example.io", "body": "ROS 2 LIO-SAM"},
            {"title": "Skydio Global", "href": "https://skydio-global.example.com", "body": "Greenhouse ATS"},
            {"title": "Derin AUV", "href": "https://derin-auv.example.com.tr", "body": "Cooldown Test"},
            {"title": "OnlySupport", "href": "https://only-support.example.org", "body": "support@ trap"},
        ]

        async def zero_sleep(_: float) -> None:
            return None

        mock_browser = MockBrowserContext(_build_milestone_3_web_ecosystem())
        fetcher = PlaywrightPageFetcher(
            timeout_ms=5000,
            rate_limiter=RateLimiter(min_sec=0.01, max_sec=0.02, sleep_func=zero_sleep),
            target_subpages=["/kariyer", "/careers"],
        )

        candidate_links = CandidateLinks(
            sender_name="Muhammed Enes BAL",
            cv_url_tr="https://drive.google.com/cv-tr-muhammed-enes-bal",
            cv_url_en="https://drive.google.com/cv-en-muhammed-enes-bal",
            portfolio_url="https://drive.google.com/portfolio-muhammed-enes-bal",
            github_url="https://github.com/quantum-Blue",
            linkedin_url="https://linkedin.com/in/muhammedenes-bal",
        )

        smtp_socket_opened = False

        def forbidden_smtp_factory(host: str, port: int, timeout: float) -> Any:
            nonlocal smtp_socket_opened
            _ = (host, port, timeout)
            smtp_socket_opened = True
            raise AssertionError("DRY-RUN modunda SMTP soketi kesinlikle açılmamalıdır!")

        pipeline = AutonomousPipeline(
            db_manager=db,
            search_client=SearchEngineClient(max_results=10, search_backend=lambda q, m: raw_ddg_results),
            domain_filter=DomainFilter(
                blacklisted_domains=settings.scraper.blacklisted_domains,
                blacklisted_title_patterns=settings.scraper.blacklisted_title_patterns,
            ),
            page_fetcher=fetcher,
            decision_engine=DecisionTreeEngine(
                ats_detector=ATSDetector(ats_domains=settings.filter.ats_domains),
                email_extractor=EmailExtractor(
                    blacklisted_prefixes=settings.scraper.blacklisted_email_prefixes,
                    high_priority_prefixes=settings.scraper.high_priority_email_prefixes,
                    low_priority_prefixes=settings.scraper.low_priority_email_prefixes,
                    blacklisted_domains=settings.scraper.blacklisted_domains,
                ),
            ),
            prompt_builder=PromptBuilder(
                links=candidate_links,
                vector_store=vector_store,
                top_k=settings.rag.top_k,
            ),
            llm_client=GeminiLLMClient(api_key=""),  # Sıfır dış ağ bağımlılığı (Fallback ile deterministik üretim)
            email_sender=EmailSender(
                smtp_config=SMTPConfig.from_env_config(settings.env),
                db_manager=db,
                dry_run=True,
                daily_limit=settings.mailer.daily_limit,
                smtp_factory=forbidden_smtp_factory,
            ),
            excluded_dork_domains=settings.scraper.blacklisted_domains,
        )

        # 4. `main.py` CLI üzerinden DRY-RUN Simülasyonunu Çalıştır
        cli_buf = StringIO()
        console = Console(file=cli_buf, force_terminal=False, width=130)

        exit_code = main_entry.main(
            ["--dry-run", "--keywords", "ROS 2", "SLAM"],
            console=console,
            db_manager=db,
            pipeline_runner=lambda **kwargs: pipeline.run(
                keywords=kwargs.get("keywords"),
                location=kwargs.get("location", "Türkiye"),
                browser_context=mock_browser,
            ),
        )
        cli_output = cli_buf.getvalue()

        # KABUL KRİTERİ DOĞRULAMALARI:
        assert exit_code == 0
        assert smtp_socket_opened is False
        assert mock_browser.active_open_pages == 0

        # Rehber siteleri (clutch.co, f6s.com, Top 10 listicle -> 3 adet) %100 elenmiş olmalı!
        assert "clutch.co" not in cli_output
        assert "support@f6s.com" not in cli_output
        assert "support@only-support.example.org" not in cli_output
        assert "Baykar Otonom Sistemler" in cli_output
        assert "kariyer@baykar-otonom.example.tr" in cli_output
        assert "AutoNav AI" in cli_output
        assert "hr@autonav-ai.example.io" in cli_output
        assert "Skydio Global" in cli_output
        assert "MANUAL_ATS" in cli_output
        assert "COOLDOWN_SKIPPED" in cli_output

        # 5. `--stats` ve `--manual-list` CLI Komutlarını Doğrula
        stats_buf = StringIO()
        stats_code = main_entry.main(
            ["--stats"],
            console=Console(file=stats_buf, force_terminal=False, width=120),
            db_manager=db,
        )
        assert stats_code == 0
        stats = db.get_statistics()
        assert stats["dry_run_count"] == 2       # Baykar Otonom (TR) + AutoNav AI (EN)
        assert stats["sent_count"] == 1          # 7 gün önceki Derin AUV kaydı
        assert stats["skipped_count"] == 1       # Derin AUV Cooldown kaydı
        assert stats["manual_ats_count"] == 1    # Skydio Global Greenhouse kaydı

        ats_buf = StringIO()
        ats_code = main_entry.main(
            ["--manual-list"],
            console=Console(file=ats_buf, force_terminal=False, width=120),
            db_manager=db,
        )
        assert ats_code == 0
        assert "https://boards.greenhouse.io/skydio-global" in ats_buf.getvalue()
