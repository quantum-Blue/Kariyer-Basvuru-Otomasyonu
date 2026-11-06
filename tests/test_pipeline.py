"""Faz 14: Hata İzolasyonlu Ana Orkestratör (Pipeline Engine) Birim ve Hata Avcılığı Testleri.

Bu test modülü şu kritik kuralları (TDD Red -> Green -> Bug Hunting) denetler:
1. **Uçtan Uca Orkestrasyon (`AutonomousPipeline.run` / `run_pipeline`):**
   - Dork sorgu üretimi (`build_dork_queries`) -> Arama motoru (`SearchEngineClient`)
     -> Anti-Aggregator filtreleme (`DomainFilter`) -> Sayfa çekme (`PlaywrightPageFetcher`)
     -> Karar ağacı (`DecisionTreeEngine`) -> Cooldown ön kontrolü (`DatabaseManager`)
     -> RAG + Prompt inşası (`PromptBuilder`) -> LLM üretim (`GeminiLLMClient`)
     -> Güvenli gönderim / DRY-RUN (`EmailSender`) adımlarını eksiksiz koşturmalı
     ve detaylı bir `PipelineRunSummary` döndürmeli.
2. **KRİTİK KABUL KRİTERİ — Şirket Bazlı Hata İzolasyonu (Fault Isolation):**
   - 5 veya 10 şirketten 3.sünde beklenmeyen bir çalışma zamanı hatası (`RuntimeError`,
     `ValueError`, bozuk DOM vb.) patlarsa, orkestratör ASLA ÇÖKMEMELİ!
   - Hatayı merkezi logger'a kaydetmeli, `summary.isolated_errors` sayacını 1 artırmalı
     ve **4. şirketten çalışmaya kesintisiz devam etmeli!**
3. **Oturum Limiti (`max_applications`) ve Günlük Kota (`QUOTA_EXCEEDED`) Erken Durdurma:**
   - Kullanıcı `--limit 2` verdiğinde veya günlük SMTP gönderim kotası dolduğunda orkestratör
     gereksiz yere kalan şirketleri kazımaya/LLM çağırmaya devam etmeden döngüyü temiz
     biçimde sonlandırmalı.
4. **Cooldown Ön Kontrolü (Erken Atlama Optimizasyonu):**
   - Bir şirket için `SEND_EMAIL` kararı çıktığında, eğer o e-posta veya şirket domain'i
     zaten 30 günlük cooldown içindeyse (`db.check_cooldown`), boşu boşuna Gemini LLM
     çağrısı yapılmadan doğrudan `COOLDOWN_SKIPPED` olarak işaretlenmeli!
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from src.database.db_manager import ApplicationStatus, DatabaseManager
from src.filter.ats_detector import ATSDetector
from src.filter.decision_tree import DecisionTreeEngine
from src.generator.llm_client import GeminiLLMClient
from src.generator.prompt_builder import PromptBuilder
from src.generator.template_manager import CandidateLinks
from src.mailer.email_sender import EmailSender, SMTPConfig
from src.orchestrator.pipeline import (
    AutonomousPipeline,
    CompanyProcessReport,
    PipelineRunSummary,
    run_pipeline,
)
from src.scraper.domain_filter import DomainFilter
from src.scraper.email_extractor import EmailExtractor
from src.scraper.page_fetcher import CompanyScrapeResult, FetchedPage
from src.scraper.search_engine import SearchEngineClient


class DummyPageFetcher:
    """Playwright tarayıcısını taklit eden ve istenen şirkette kasıtlı istisna (`RuntimeError`)
    fırlatarak Hata İzolasyonu (Fault Isolation) davranışını test etmeyi sağlayan sınıf.
    """

    def __init__(self, pages_by_url: dict[str, CompanyScrapeResult | Exception]) -> None:
        self.pages_by_url = pages_by_url
        self.fetched_urls: list[str] = []

    async def fetch_company_site(
        self,
        entry_url: str,
        browser_context: Any | None = None,
    ) -> CompanyScrapeResult:
        _ = browser_context
        clean = entry_url.rstrip("/")
        self.fetched_urls.append(clean)
        item = self.pages_by_url.get(clean)
        if isinstance(item, Exception):
            raise item
        if item is None:
            return CompanyScrapeResult(
                base_url=clean,
                company_name="Unknown",
                language="TR",
                combined_text="",
                combined_html="",
                all_links=[],
                pages=[],
            )
        return item


class TestFaultTolerantPipelineOrchestratorTDDAndBugHunting:
    """`src/orchestrator/pipeline.py` modülünün birim ve hata avcılığı testleri."""

    def setup_method(self) -> None:
        self.links = CandidateLinks(
            sender_name="Muhammed Enes BAL",
            cv_url_tr="https://drive.google.com/cv-tr-enes",
            cv_url_en="https://drive.google.com/cv-en-enes",
            portfolio_url="https://drive.google.com/portfolio-enes",
            github_url="https://github.com/quantum-Blue",
            linkedin_url="https://linkedin.com/in/muhammedenes-bal",
        )
        self.smtp_config = SMTPConfig(
            host="smtp.gmail.com",
            port=465,
            email="enes@example.com",
            password="app-password",
            sender_name="Muhammed Enes BAL",
        )

    def test_fault_isolation_continues_to_4th_company_when_3rd_company_explodes(
        self, tmp_path: Path
    ) -> None:
        """KRİTİK KABUL KRİTERİ (Faz 14 - Fault Isolation):
        Listede 5 gerçek şirket varken 3. şirkette beklenmeyen bir çalışma zamanı hatası
        (`RuntimeError("Unexpected DOM crash on 3rd company")`) patladığında:
        - Orkestratör ASLA çökmemeli,
        - 1. ve 2. şirketleri işlemeli,
        - 3. şirketin hatasını yakalayıp `isolated_errors == 1` olarak kaydetmeli,
        - **4. ve 5. şirketlerden çalışmaya kesintisiz devam etmeli!**
        """
        db = DatabaseManager(sqlite_path=tmp_path / "pipeline_fault.db", cooldown_days=30)

        # Arama motorundan 1 adet rehber sitesi (clutch.co) + 5 gerçek şirket dönsün
        raw_ddg_items = [
            {"title": "Top 10 Robotics Firms - Clutch", "href": "https://clutch.co/tr/robotics", "body": "List"},
            {"title": "Şirket 1 Robotik", "href": "https://sirket1.example.tr", "body": "ROS 2"},
            {"title": "Şirket 2 ATS", "href": "https://sirket2.example.io", "body": "SLAM"},
            {"title": "Şirket 3 Patlayan", "href": "https://sirket3-crash.example.com", "body": "Crash"},
            {"title": "Şirket 4 Otonom", "href": "https://sirket4.example.tr", "body": "İHA"},
            {"title": "Şirket 5 Sadece Support", "href": "https://sirket5.example.org", "body": "Support"},
        ]

        pages_map: dict[str, CompanyScrapeResult | Exception] = {
            "https://sirket1.example.tr": CompanyScrapeResult(
                base_url="https://sirket1.example.tr",
                company_name="Şirket 1 Robotik",
                language="TR",
                combined_text="ROS 2 ve otonom İHA navigasyon sistemleri geliştiriyoruz.",
                combined_html='<a href="mailto:kariyer@sirket1.example.tr">kariyer@sirket1.example.tr</a>',
                all_links=["mailto:kariyer@sirket1.example.tr"],
                pages=[FetchedPage("https://sirket1.example.tr", 200, "Şirket 1", "", "ROS 2", [])],
            ),
            "https://sirket2.example.io": CompanyScrapeResult(
                base_url="https://sirket2.example.io",
                company_name="Şirket 2 ATS",
                language="EN",
                combined_text="Autonomous mobile robots.",
                combined_html='<a href="https://boards.greenhouse.io/sirket2">Apply</a>',
                all_links=["https://boards.greenhouse.io/sirket2", "mailto:hr@sirket2.example.io"],
                pages=[FetchedPage("https://sirket2.example.io", 200, "Şirket 2", "", "AMR", [])],
            ),
            # 3. ŞİRKETTE BEKLENMEYEN HATA PATLIYOR!
            "https://sirket3-crash.example.com": RuntimeError(
                "Unexpected critical crash while parsing 3rd company!"
            ),
            "https://sirket4.example.tr": CompanyScrapeResult(
                base_url="https://sirket4.example.tr",
                company_name="Şirket 4 Otonom",
                language="TR",
                combined_text="Su altı otonom araçları (AUV) ve sonar görüntüleme.",
                combined_html='<a href="mailto:iletisim@sirket4.example.tr">iletisim@sirket4.example.tr</a>',
                all_links=["mailto:iletisim@sirket4.example.tr"],
                pages=[FetchedPage("https://sirket4.example.tr", 200, "Şirket 4", "", "AUV", [])],
            ),
            "https://sirket5.example.org": CompanyScrapeResult(
                base_url="https://sirket5.example.org",
                company_name="Şirket 5",
                language="EN",
                combined_text="Only support email here.",
                combined_html='<a href="mailto:support@sirket5.example.org">support@sirket5.example.org</a>',
                all_links=["mailto:support@sirket5.example.org"],
                pages=[FetchedPage("https://sirket5.example.org", 200, "Şirket 5", "", "Support", [])],
            ),
        }

        pipeline = AutonomousPipeline(
            db_manager=db,
            search_client=SearchEngineClient(max_results=10, search_backend=lambda q, m: raw_ddg_items),
            domain_filter=DomainFilter(),
            page_fetcher=DummyPageFetcher(pages_map),  # type: ignore[arg-type]
            decision_engine=DecisionTreeEngine(ats_detector=ATSDetector(), email_extractor=EmailExtractor()),
            prompt_builder=PromptBuilder(links=self.links),
            llm_client=GeminiLLMClient(api_key=""),  # Fallback şablonu ile hızlı üretim
            email_sender=EmailSender(smtp_config=self.smtp_config, db_manager=db, dry_run=True),
        )

        summary = pipeline.run(keywords=["ROS 2"], location="Türkiye")

        assert isinstance(summary, PipelineRunSummary)
        assert summary.total_search_results == 6
        assert summary.filtered_aggregator_count == 1  # clutch.co elendi
        assert summary.valid_company_candidates == 5

        # 3. şirket patlamasına rağmen 5 şirketin tamamı döngüde ele alınmış olmalı!
        assert len(summary.company_reports) == 5
        assert summary.isolated_errors == 1
        assert summary.dry_run_count == 2          # Şirket 1 (kariyer@) ve Şirket 4 (iletisim@)
        assert summary.manual_ats_count == 1       # Şirket 2 (Greenhouse Override)
        assert summary.skipped_no_email_count == 1 # Şirket 5 (Sadece support@)

        # 3. şirketin raporunu ve 4. şirketin başarıyla işlendiğini doğrula
        report_3 = summary.company_reports[2]
        report_4 = summary.company_reports[3]
        assert isinstance(report_3, CompanyProcessReport)
        assert report_3.status == "ISOLATED_ERROR"
        assert "Unexpected critical crash" in report_3.detail
        assert report_4.company_name == "Şirket 4 Otonom"
        assert report_4.status == "DRY_RUN"
        assert report_4.target_email == "iletisim@sirket4.example.tr"

    def test_pipeline_skips_llm_call_when_company_is_in_30_day_cooldown(
        self, tmp_path: Path
    ) -> None:
        """Bir şirketin `ik@sirket.com.tr` adresine 10 gün önce gerçek mail (`SENT`) atılmışsa,
        pipeline bugün aynı şirketin `kariyer@sirket.com.tr` adresini bulduğunda:
        - Boşu boşuna LLM (`GeminiLLMClient`) çağrısı YAPMAMALI,
        - Doğrudan `COOLDOWN_SKIPPED` olarak raporlamalı!
        """
        db = DatabaseManager(sqlite_path=tmp_path / "cooldown_opt.db", cooldown_days=30)
        now = datetime.now(timezone.utc)

        db.record_application(
            company_name="Baykar",
            company_url="https://baykar.example.tr",
            target_email="ik@baykar.example.tr",
            status=ApplicationStatus.SENT,
            sent_at=now - timedelta(days=10),
        )

        llm_called = False

        def tracking_llm_backend(s: str, u: str, m: str, t: float) -> str:
            nonlocal llm_called
            _ = (s, u, m, t)
            llm_called = True
            return "Should not be called because of cooldown"

        raw_ddg = [
            {"title": "Baykar", "href": "https://baykar.example.tr", "body": "İHA"},
        ]
        pages_map: dict[str, CompanyScrapeResult | Exception] = {
            "https://baykar.example.tr": CompanyScrapeResult(
                base_url="https://baykar.example.tr",
                company_name="Baykar",
                language="TR",
                combined_text="Otonom İHA",
                combined_html='<a href="mailto:kariyer@baykar.example.tr">kariyer@baykar.example.tr</a>',
                all_links=["mailto:kariyer@baykar.example.tr"],
                pages=[],
            )
        }

        pipeline = AutonomousPipeline(
            db_manager=db,
            search_client=SearchEngineClient(max_results=5, search_backend=lambda q, m: raw_ddg),
            domain_filter=DomainFilter(),
            page_fetcher=DummyPageFetcher(pages_map),  # type: ignore[arg-type]
            decision_engine=DecisionTreeEngine(),
            prompt_builder=PromptBuilder(links=self.links),
            llm_client=GeminiLLMClient(api_key="valid-key", generation_backend=tracking_llm_backend),
            email_sender=EmailSender(smtp_config=self.smtp_config, db_manager=db, dry_run=True),
        )

        summary = pipeline.run(keywords=["İHA"])

        assert llm_called is False
        assert summary.cooldown_skipped_count == 1
        assert summary.company_reports[0].status == "COOLDOWN_SKIPPED"

    def test_pipeline_stops_early_when_max_applications_limit_is_reached(
        self, tmp_path: Path
    ) -> None:
        """Kullanıcı `max_applications=2` (`--limit 2`) verdiğinde, 4 geçerli şirket adayı
        olsa bile ilk 2 başarılı gönderim/simülasyon tamamlandığında 3. ve 4. şirketler
        kazınmadan döngü durdurulmalı!
        """
        db = DatabaseManager(sqlite_path=tmp_path / "limit.db", cooldown_days=30)

        raw_ddg = [
            {"title": f"Company {i}", "href": f"https://comp{i}.example.com", "body": "Robotics"}
            for i in range(1, 5)
        ]
        pages_map: dict[str, CompanyScrapeResult | Exception] = {
            f"https://comp{i}.example.com": CompanyScrapeResult(
                base_url=f"https://comp{i}.example.com",
                company_name=f"Company {i}",
                language="EN",
                combined_text="Robotics and SLAM",
                combined_html=f'<a href="mailto:hr@comp{i}.example.com">hr@comp{i}.example.com</a>',
                all_links=[f"mailto:hr@comp{i}.example.com"],
                pages=[],
            )
            for i in range(1, 5)
        }

        dummy_fetcher = DummyPageFetcher(pages_map)
        summary = run_pipeline(
            keywords=["ROS 2"],
            max_applications=2,
            db_manager=db,
            search_client=SearchEngineClient(max_results=10, search_backend=lambda q, m: raw_ddg),
            domain_filter=DomainFilter(),
            page_fetcher=dummy_fetcher,  # type: ignore[arg-type]
            decision_engine=DecisionTreeEngine(),
            prompt_builder=PromptBuilder(links=self.links),
            llm_client=GeminiLLMClient(api_key=""),
            email_sender=EmailSender(smtp_config=self.smtp_config, db_manager=db, dry_run=True),
        )

        assert summary.dry_run_count == 2
        assert len(dummy_fetcher.fetched_urls) == 2
        assert len(summary.company_reports) == 2
