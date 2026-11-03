"""🛡️ MILESTONE 3: Entegrasyon Denetimi (Faz 1 – 12 Uçtan Uca Zekâ, Karar ve Hafıza Kontrolü).

Bu entegrasyon testi modülü şu uçtan uca (End-to-End) zinciri denetler:
[Faz 1–4: Konfigürasyon + Çok Dilli RAG Vektör Veritabanı (ChromaDB)]
    -> [Faz 5–8: Keşif + Anti-Aggregator Filtresi + Playwright Sayfa Kazıma + 3 Katmanlı E-posta Çıkarımı]
    -> [Faz 9: ATS Tespit Motoru & Yönlendirme Karar Ağacı (`DecisionTreeEngine`)]
    -> [Faz 10: Çift Dilli (`TR`/`EN`) Şablon Yöneticisi & Dinamik RAG Prompt İnşası (`PromptBuilder`)]
    -> [Faz 11: Gemini LLM İstemcisi, Halüsinasyon Doğrulayıcı, Link Onarımı & Fallback (`GeminiLLMClient`)]
    -> [Faz 12: SQLite Hafıza Yöneticisi & E-posta/Domain Bazlı 30 Gün Kuralı (`DatabaseManager`)]

Kritik Denetim Noktaları:
1. **ATS Override Kuralı:** Sayfada hem `kariyer@` e-postası hem de `greenhouse.io` / `lever.co`
   linki olan bir şirket otomatik e-posta akışına GİRMEMELİ; `DatabaseManager.record_manual_ats`
   ile manuel ATS kuyruğuna kaydedilmelidir.
2. **Domain Bazlı 30 Gün Kuralı (Cooldown):** Bir şirketin `ik@sirket.com.tr` adresine gönderim
   (`SENT`) yapıldıktan sonra, aynı şirketin başka bir alt sayfasından `kariyer@sirket.com.tr`
   veya `info@sirket.com.tr` adresi keşfedilse bile domain bazlı 30 gün kuralı devreye girip
   LLM çağrısını ve gönderimi engellemelidir!
3. **Çift Dilli (`TR` / `EN`) RAG + LLM + Fallback Bütünlüğü:**
   - Türk şirketler için Türkçe CV linki ve Türkçe metin, yabancı şirketler için İngilizce CV
     linki ve İngilizce metin üretilmelidir.
   - LLM halüsinasyon gördüğünde veya API çöktüğünde sistem durmamalı, statik yedek (`fallback_email`)
     şablona geçerek süreci tamamlamalıdır.
4. **Bellek Sızıntısı (`tracemalloc`), Dosya Kilidi (Windows File Lock) ve Performans SLA Denetimi:**
   Tüm 12 fazlık zincir döngüsel stres testinde bellek sızdırmamalı, SQLite `.db` dosyasını
   kilitlememeli ve SLA süresi içinde tamamlanmalıdır.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import gc
from pathlib import Path
import time
import tracemalloc
from typing import Any

import pytest

from src.config.settings import load_settings
from src.database.db_manager import ApplicationStatus, DatabaseManager
from src.filter.ats_detector import ATSDetector
from src.filter.decision_tree import DecisionTreeEngine, RoutingAction
from src.generator.llm_client import GeminiLLMClient
from src.generator.prompt_builder import PromptBuilder
from src.generator.template_manager import CandidateLinks, contains_forbidden_attachment_phrase
from src.rag.chunker import chunk_pdf_document
from src.rag.embedder import MultilingualEmbedder
from src.rag.pdf_loader import extract_text_from_pdf
from src.rag.vector_store import VectorStoreManager
from src.scraper.domain_filter import DomainFilter
from src.scraper.email_extractor import EmailExtractor
from src.scraper.page_fetcher import PlaywrightPageFetcher
from src.scraper.rate_limiter import RateLimiter
from src.scraper.search_engine import SearchResultItem
from src.utils.logger import setup_logging
from tests.integration.test_milestone_1_rag import (
    MilestoneSemanticBackend,
    _build_realistic_candidate_pdfs,
)
from tests.integration.test_milestone_2_discovery import MockBrowserContext


def _build_milestone_3_web_ecosystem() -> dict[str, dict[str, Any]]:
    """Milestone 3 için 5 farklı arketipte şirket içeren sanal web ekosistemi:
    1. `https://baykar-otonom.example.tr`: Türk İHA/SLAM şirketi (`TR`, `kariyer@` -> `SEND_EMAIL`).
    2. `https://autonav-ai.example.io`: Yabancı Robotik şirketi (`EN`, `hr@` -> `SEND_EMAIL`,
       LLM uydurma link halüsinasyonu görecek -> Fallback devreye girecek!).
    3. `https://skydio-global.example.com`: Hem `jobs@skydio-global.example.com` e-postası HEM DE
       `https://boards.greenhouse.io/skydio` ATS linki olan şirket (`MANUAL_ATS` Override!).
    4. `https://derin-auv.example.com.tr`: 5 gün önce `ik@derin-auv.example.com.tr` adresine
       başvuru yapılmış şirket; bu kez sayfasında `kariyer@derin-auv.example.com.tr` bulunuyor
       (Domain Bazlı 30 Gün Cooldown engellemeli!).
    5. `https://only-support.example.org`: Sayfasında sadece `support@` ve `noreply@` olan şirket (`SKIP`).
    """
    return {
        "https://baykar-otonom.example.tr": {
            "status": 200,
            "title": "Baykar Otonom Sistemler | İHA ve SLAM Ar-Ge",
            "html": """
            <html>
              <body>
                <h1>GNSS-Bağımsız Otonom İHA, ROS 2 ve Fast-LIO2 SLAM Çözümleri</h1>
                <p>İnsansız hava araçlarımızda LiDAR-Stereo sensör füzyonu ve derin öğrenme
                tabanlı bilgisayarlı görü algoritmaları geliştiriyoruz.</p>
                <p>Destek: support@baykar-otonom.example.tr</p>
                <a href="mailto:kariyer@baykar-otonom.example.tr">kariyer@baykar-otonom.example.tr</a>
              </body>
            </html>
            """,
        },
        "https://autonav-ai.example.io": {
            "status": 200,
            "title": "AutoNav AI - Autonomous Mobile Robots & 3D SLAM",
            "html": """
            <html>
              <body>
                <h1>Next-Gen Autonomous Navigation with ROS 2 and LIO-SAM</h1>
                <p>We engineer embedded edge AI and real-time obstacle avoidance for industrial robots.</p>
                <a href="mailto:hr@autonav-ai.example.io">hr@autonav-ai.example.io</a>
              </body>
            </html>
            """,
        },
        "https://skydio-global.example.com": {
            "status": 200,
            "title": "Skydio Global - Autonomous Flight",
            "html": """
            <html>
              <body>
                <h1>Autonomous Drone Engineering</h1>
                <p>Questions? Email <a href="mailto:jobs@skydio-global.example.com">jobs@skydio-global.example.com</a></p>
                <p>Apply for open positions on our portal:
                  <a href="https://boards.greenhouse.io/skydio-global">Greenhouse Career Portal</a>
                </p>
              </body>
            </html>
            """,
        },
        "https://derin-auv.example.com.tr": {
            "status": 200,
            "title": "Derin AUV Teknolojileri",
            "html": """
            <html>
              <body>
                <h1>İnsansız Su Altı Araçları (AUV) ve Biyomimetik Robotik</h1>
                <p>Yeni İK adresimiz: <a href="mailto:kariyer@derin-auv.example.com.tr">kariyer@derin-auv.example.com.tr</a></p>
              </body>
            </html>
            """,
        },
        "https://only-support.example.org": {
            "status": 200,
            "title": "OnlySupport Hardware",
            "html": """
            <html>
              <body>
                <h1>Industrial Sensors</h1>
                <p>Contact customer service: support@only-support.example.org or noreply@only-support.example.org</p>
              </body>
            </html>
            """,
        },
    }


class TestMilestone3EndToEndIntelligenceAndMemory:
    """Faz 1 – Faz 12 arası tüm keşif, karar ağacı, RAG+LLM üretim ve SQLite hafıza denetimi."""

    def setup_method(self) -> None:
        MultilingualEmbedder.reset_instance()

    def teardown_method(self) -> None:
        MultilingualEmbedder.reset_instance()

    def test_end_to_end_discovery_to_decision_tree_to_rag_llm_and_sqlite_cooldown(
        self, tmp_path: Path
    ) -> None:
        """Uçtan Uca Senaryo (Faz 1 -> Faz 12):
        1. Konfigürasyon, Logger, RAG (ChromaDB) ve SQLite `DatabaseManager` başlatılır.
        2. SQLite veritabanına 5 gün öncesine ait `ik@derin-auv.example.com.tr` (`SENT`) kaydı eklenir.
        3. Arama sonuçlarından `clutch.co` elenir, kalan 5 şirket `PlaywrightPageFetcher` ile çekilir.
        4. Her şirket `DecisionTreeEngine` ile değerlendirilir:
           - `skydio-global.example.com` -> `MANUAL_ATS` (ATS Override kuralı `jobs@` adresini ezer!)
           - `only-support.example.org` -> `SKIP` (`support@` kara listededir!)
           - `derin-auv.example.com.tr` -> `SEND_EMAIL` (`kariyer@`), FAKAT SQLite Domain Cooldown
             kontrolünde (`ik@` ile aynı domain olduğu için) yakalanıp `SKIPPED` olarak kaydedilir!
           - `baykar-otonom.example.tr` -> `SEND_EMAIL` (`TR`), RAG + LLM ile Türkçe kişiselleştirilmiş
             e-posta üretilir ve SQLite'a `SENT` olarak kaydedilir.
           - `autonav-ai.example.io` -> `SEND_EMAIL` (`EN`), LLM halüsinasyonlu link üretir,
             doğrulayıcı bunu yakalayıp İngilizce statik `fallback_email` şablonuna geçer ve
             SQLite'a `SENT` olarak kaydedilir.
        """
        # ------------------------------------------------------------------
        # ADIM 1: Faz 1–4 — Konfigürasyon, Logger ve Çok Dilli RAG Kurulumu
        # ------------------------------------------------------------------
        project_root = Path(__file__).resolve().parent.parent.parent
        settings = load_settings(
            yaml_path=project_root / "config" / "settings.yaml",
            env_path=tmp_path / "missing.env",
        )
        setup_logging(
            log_file=tmp_path / "logs" / "milestone3.log",
            level=settings.logging.level,
            logger_name="milestone3_audit",
        )

        pdf_dir = tmp_path / "pdfs"
        pdf_dir.mkdir(parents=True, exist_ok=True)
        tr_pdf, en_pdf = _build_realistic_candidate_pdfs(pdf_dir)

        chunks = chunk_pdf_document(
            extract_text_from_pdf(tr_pdf),
            chunk_size=settings.rag.chunk_size,
            chunk_overlap=settings.rag.chunk_overlap,
        ) + chunk_pdf_document(
            extract_text_from_pdf(en_pdf),
            chunk_size=settings.rag.chunk_size,
            chunk_overlap=settings.rag.chunk_overlap,
        )

        embedder = MultilingualEmbedder.get_instance(
            model_name=settings.rag.embedding_model,
            model_loader=lambda _: MilestoneSemanticBackend(),
        )
        vector_store = VectorStoreManager(
            persist_directory=tmp_path / "chroma_m3",
            collection_name=settings.rag.collection_name,
            embedder=embedder,
        )
        vector_store.upsert_chunks(chunks)

        # ------------------------------------------------------------------
        # ADIM 2: Faz 12 — SQLite Hafıza ve Önceki Gönderim (Cooldown) Hazırlığı
        # ------------------------------------------------------------------
        db = DatabaseManager(
            sqlite_path=tmp_path / "db" / "milestone3.db",
            cooldown_days=settings.mailer.cooldown_days,
        )
        now_utc = datetime(2026, 11, 3, 15, 0, 0, tzinfo=timezone.utc)

        # 5 gün önce 'derin-auv.example.com.tr' şirketinin 'ik@' adresine gerçek gönderim yapılmış olsun
        db.record_application(
            company_name="Derin AUV Teknolojileri",
            company_url="https://derin-auv.example.com.tr",
            target_email="ik@derin-auv.example.com.tr",
            status=ApplicationStatus.SENT,
            language="TR",
            subject="Önceki Başvuru",
            body="Önceki gövde",
            sent_at=now_utc - timedelta(days=5),
        )

        # ------------------------------------------------------------------
        # ADIM 3: Faz 6–8 — Anti-Aggregator Filtreleme ve Playwright Kazıma
        # ------------------------------------------------------------------
        domain_filter = DomainFilter(
            blacklisted_domains=settings.scraper.blacklisted_domains,
            blacklisted_title_patterns=settings.scraper.blacklisted_title_patterns,
        )
        raw_candidates = [
            SearchResultItem("Clutch Robotics List", "https://clutch.co/robotics", "Aggregator"),
            SearchResultItem("Baykar Otonom", "https://baykar-otonom.example.tr", "İHA SLAM"),
            SearchResultItem("AutoNav AI", "https://autonav-ai.example.io", "ROS 2 AMR"),
            SearchResultItem("Skydio Global", "https://skydio-global.example.com", "Drones"),
            SearchResultItem("Derin AUV", "https://derin-auv.example.com.tr", "AUV"),
            SearchResultItem("OnlySupport", "https://only-support.example.org", "Sensors"),
        ]
        valid_companies = domain_filter.filter_results(raw_candidates)
        assert len(valid_companies) == 5

        async def zero_sleep(_: float) -> None:
            return None

        fetcher = PlaywrightPageFetcher(
            timeout_ms=5000,
            rate_limiter=RateLimiter(min_sec=0.01, max_sec=0.02, sleep_func=zero_sleep),
            target_subpages=["/kariyer", "/careers"],
        )
        mock_browser = MockBrowserContext(_build_milestone_3_web_ecosystem())

        async def scrape_all() -> list[Any]:
            return [
                await fetcher.fetch_company_site(item.url, browser_context=mock_browser)
                for item in valid_companies
            ]

        scraped_sites = asyncio.run(scrape_all())
        assert mock_browser.active_open_pages == 0

        # ------------------------------------------------------------------
        # ADIM 4: Faz 9, 10, 11, 12 — Karar Ağacı -> RAG Prompt -> LLM -> SQLite
        # ------------------------------------------------------------------
        decision_engine = DecisionTreeEngine(
            ats_detector=ATSDetector(ats_domains=settings.filter.ats_domains),
            email_extractor=EmailExtractor(
                blacklisted_prefixes=settings.scraper.blacklisted_email_prefixes,
                high_priority_prefixes=settings.scraper.high_priority_email_prefixes,
                low_priority_prefixes=settings.scraper.low_priority_email_prefixes,
                blacklisted_domains=settings.scraper.blacklisted_domains,
            ),
        )

        candidate_links = CandidateLinks(
            sender_name="Muhammed Enes BAL",
            cv_url_tr="https://drive.google.com/cv-tr-enes",
            cv_url_en="https://drive.google.com/cv-en-enes",
            portfolio_url="https://drive.google.com/portfolio-enes",
            github_url="https://github.com/quantum-Blue",
            linkedin_url="https://linkedin.com/in/muhammedenes-bal",
        )

        prompt_builder = PromptBuilder(
            links=candidate_links,
            vector_store=vector_store,
            top_k=settings.rag.top_k,
            min_word_count=settings.generator.min_word_count,
            max_word_count=settings.generator.max_word_count,
        )

        def simulated_gemini_backend(
            sys_inst: str,
            usr_prompt: str,
            model: str,
            temp: float,
        ) -> str:
            _ = (sys_inst, model, temp)
            # Baykar Otonom (TR) için kurallara %100 uyan gerçekçi Türkçe çıktı üret
            if "Baykar Otonom" in usr_prompt:
                return (
                    "Sayın Baykar Otonom Sistemler İnsan Kaynakları ve Mühendislik Ekibi,\n\n"
                    "Bilgisayar Mühendisliği mezunu bir Robotik, Otonom Sistemler ve Yapay Zekâ "
                    "mühendisi olarak GNSS-bağımsız İHA navigasyonu ve ROS 2 tabanlı SLAM "
                    "çalışmalarınızı yakından takip ediyorum. Bitirme projemde LiDAR ve stereo "
                    "kamera füzyonu ile Fast-LIO2 ve RTAB-MAP tabanlı 3B haritalama ve otonom "
                    "uçuş kontrolü gerçekleştirdim. Ayrıca Teknofest ve TÜBİTAK projelerinde "
                    "YOLOv12 ile gerçek zamanlı nesne tespiti üzerine çalıştım.\n\n"
                    "Özgeçmiş ve teknik portfolyo bağlantılarımı aşağıda incelemenize sunarım:\n"
                    f"- Özgeçmiş (CV): {candidate_links.cv_url_tr}\n"
                    f"- Teknik Portfolyo: {candidate_links.portfolio_url}\n"
                    f"- GitHub: {candidate_links.github_url}\n\n"
                    "Saygılarımla,\nMuhammed Enes BAL"
                )
            # AutoNav AI (EN) için kasıtlı olarak UYDURMA (halüsinasyon) URL döndür ki
            # doğrulayıcının bunu yakalayıp İngilizce Fallback şablonuna geçtiği kanıtlansın!
            return (
                "Dear AutoNav AI Team, "
                + ("I build ROS 2 and LIO-SAM autonomous robots. " * 10)
                + "Check my fake link: https://hallucinated-domain.org/fake-portfolio.pdf"
            )

        llm_client = GeminiLLMClient(
            api_key="simulated-gemini-api-key",
            model_name=settings.generator.gemini_model,
            temperature=settings.generator.temperature,
            min_word_count=settings.generator.min_word_count,
            max_word_count=settings.generator.max_word_count,
            generation_backend=simulated_gemini_backend,
        )

        generated_results: dict[str, Any] = {}

        for site in scraped_sites:
            decision = decision_engine.evaluate_scrape_result(site)

            if decision.action == RoutingAction.MANUAL_ATS:
                db.record_manual_ats(
                    company_name=site.company_name,
                    company_url=site.base_url,
                    ats_url=decision.ats_url or "",
                    ats_provider=decision.ats_provider or "",
                    language=site.language,
                    discovered_at=now_utc,
                )
                generated_results[site.base_url] = ("MANUAL_ATS", None)
                continue

            if decision.action == RoutingAction.SKIP or not decision.target_email:
                generated_results[site.base_url] = ("SKIP_NO_EMAIL", None)
                continue

            # SQLite 30 Gün (E-posta + Domain) Cooldown Kontrolü
            cooldown_check = db.check_cooldown(
                target_email=decision.target_email,
                company_url=site.base_url,
                reference_time=now_utc,
            )
            if cooldown_check.in_cooldown:
                db.record_application(
                    company_name=site.company_name,
                    company_url=site.base_url,
                    target_email=decision.target_email,
                    status=ApplicationStatus.SKIPPED,
                    language=site.language,
                    error_message=cooldown_check.reason,
                    sent_at=now_utc,
                )
                generated_results[site.base_url] = ("SKIP_COOLDOWN", cooldown_check)
                continue

            # Cooldown'da değilse RAG + PromptBuilder + GeminiLLMClient ile e-posta üret
            built_prompt = prompt_builder.build_for_company(
                company_name=site.company_name,
                company_context=site.combined_text,
                language=site.language,
            )
            email_res = llm_client.generate_email(built_prompt, links=candidate_links)

            # Üretilen e-postayı başarılı gönderim olarak SQLite'a kaydet
            db.record_application(
                company_name=site.company_name,
                company_url=site.base_url,
                target_email=decision.target_email,
                status=ApplicationStatus.SENT,
                language=email_res.language,
                subject=email_res.subject,
                body=email_res.body,
                sent_at=now_utc,
            )
            generated_results[site.base_url] = ("SENT", email_res)

        # ------------------------------------------------------------------
        # ADIM 5: Sonuçların Kesin Doğrulaması (Assertions)
        # ------------------------------------------------------------------
        # 1. Skydio Global -> Sayfada jobs@ olmasına rağmen Greenhouse ATS kuralı e-postayı ezdi!
        assert generated_results["https://skydio-global.example.com"][0] == "MANUAL_ATS"
        ats_queue = db.get_manual_ats_queue()
        assert len(ats_queue) == 1
        assert ats_queue[0].ats_url == "https://boards.greenhouse.io/skydio-global"
        assert ats_queue[0].ats_provider == "greenhouse.io"

        # 2. OnlySupport -> Sadece support@ ve noreply@ olduğu için atlandı!
        assert generated_results["https://only-support.example.org"][0] == "SKIP_NO_EMAIL"

        # 3. Derin AUV -> Yeni adresi 'kariyer@' olmasına rağmen 5 gün önceki 'ik@' gönderimi
        # yüzünden Domain Cooldown tarafından yakalanıp engellendi!
        derin_status, derin_cd = generated_results["https://derin-auv.example.com.tr"]
        assert derin_status == "SKIP_COOLDOWN"
        assert derin_cd.in_cooldown is True
        assert "derin-auv.example.com.tr" in derin_cd.matched_domain

        # 4. Baykar Otonom (TR) -> Gerçek LLM çıktısı doğrulamadan geçti (used_fallback=False)
        baykar_status, baykar_email = generated_results["https://baykar-otonom.example.tr"]
        assert baykar_status == "SENT"
        assert baykar_email.language == "TR"
        assert baykar_email.used_fallback is False
        assert candidate_links.cv_url_tr in baykar_email.body
        assert candidate_links.portfolio_url in baykar_email.body
        assert contains_forbidden_attachment_phrase(baykar_email.body) is False

        # 5. AutoNav AI (EN) -> LLM halüsinasyonlu link ürettiği için doğrulayıcı yakaladı
        # ve İngilizce statik Fallback şablonuna geçti (used_fallback=True)!
        autonav_status, autonav_email = generated_results["https://autonav-ai.example.io"]
        assert autonav_status == "SENT"
        assert autonav_email.language == "EN"
        assert autonav_email.used_fallback is True
        assert candidate_links.cv_url_en in autonav_email.body
        assert candidate_links.portfolio_url in autonav_email.body
        assert "hallucinated-domain.org" not in autonav_email.body
        assert contains_forbidden_attachment_phrase(autonav_email.body) is False

        # 6. SQLite İstatistik ve Bugünün Gönderim Sayısı Kontrolü
        stats = db.get_statistics()
        assert stats["sent_count"] == 3          # 1 önceki (5 gün önce) + 2 bugün (Baykar + AutoNav)
        assert stats["skipped_count"] == 1       # 1 Cooldown (Derin AUV)
        assert stats["manual_ats_count"] == 1    # 1 ATS (Skydio)
        assert db.get_today_sent_count(reference_time=now_utc) == 2

    def test_milestone_3_memory_leak_file_lock_and_performance_sla_audit(
        self, tmp_path: Path
    ) -> None:
        """Bellek sızıntısı (`tracemalloc`), açık kalan SQLite dosya kilidi ve
        performans SLA (< 4.0 sn) denetimi.
        """
        start_time = time.perf_counter()

        db_file = tmp_path / "perf_m3.db"
        db = DatabaseManager(sqlite_path=db_file, cooldown_days=30)
        decision_engine = DecisionTreeEngine()
        links = CandidateLinks(
            sender_name="Muhammed Enes BAL",
            cv_url_tr="https://drive.google.com/cv-tr",
            cv_url_en="https://drive.google.com/cv-en",
            portfolio_url="https://drive.google.com/portfolio",
        )
        builder = PromptBuilder(links=links)
        llm = GeminiLLMClient(api_key="")  # Fallback modu ile hızlı stres testi

        gc.collect()
        tracemalloc.start()
        mem_before, _ = tracemalloc.get_traced_memory()

        for i in range(25):
            html = f'<p>Contact our HR team at <a href="mailto:hr@company{i}.io">hr@company{i}.io</a></p>'
            decision = decision_engine.evaluate(html_content=html, links=[f"mailto:hr@company{i}.io"])
            assert decision.action == RoutingAction.SEND_EMAIL

            if not db.is_in_cooldown(decision.target_email or "", f"https://company{i}.io"):
                prompt = builder.build_for_company(f"Company {i}", "Robotics and SLAM", language="EN")
                email_out = llm.generate_email(prompt, links=links)
                db.record_application(
                    company_name=f"Company {i}",
                    company_url=f"https://company{i}.io",
                    target_email=decision.target_email or "",
                    status=ApplicationStatus.DRY_RUN,
                    language=email_out.language,
                    subject=email_out.subject,
                    body=email_out.body,
                )

        gc.collect()
        mem_after, _ = tracemalloc.get_traced_memory()
        tracemalloc.stop()

        elapsed = time.perf_counter() - start_time
        mem_growth = mem_after - mem_before

        assert db.get_statistics()["dry_run_count"] == 25
        assert mem_growth < 5 * 1024 * 1024
        assert elapsed < 4.0

        # Windows üzerinde SQLite veritabanı dosyası kilitli kalmamalı (silinebilmeli)
        db.close()
        db_file.unlink()
        assert not db_file.exists()
