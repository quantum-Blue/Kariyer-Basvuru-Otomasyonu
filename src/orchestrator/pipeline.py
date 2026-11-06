"""Hata İzolasyonlu Ana Orkestratör (Pipeline Engine) Modülü (src/orchestrator/pipeline.py).

Bu modülün amacı:
1. Faz 1'den Faz 13'e kadar geliştirilen tüm alt sistemleri (`DorkQueryBuilder` ->
   `SearchEngineClient` -> `DomainFilter` -> `PlaywrightPageFetcher` -> `DecisionTreeEngine` ->
   `DatabaseManager` -> `PromptBuilder` -> `GeminiLLMClient` -> `EmailSender`) gevşek bağlı
   (loosely coupled) ve test edilebilir bir mimariyle tek bir akışta (`AutonomousPipeline`)
   birleştirmek.
2. **Şirket Bazlı Hata İzolasyonu (Fault Isolation — Faz 14 Kritik Kabul Kriteri):**
   Gerçek dünyada web kazıma ve otomasyon sırasında 10 şirketten 3.sünde beklenmeyen bir
   DOM bozukluğu, kodlama (encoding) hatası veya çalışma zamanı istisnası (`Exception`)
   patlayabilir. Bu orkestratör her şirketi bağımsız bir `try...except Exception:` zırhı
   içinde işler; 3. şirkette hata patlarsa bunu loglar, rapora `ISOLATED_ERROR` olarak yazar
   ve **4. şirketten çalışmaya kesintisiz devam eder!**
3. **Maliyet ve Kota Optimizasyonu (Erken Cooldown Kontrolü & Limit Durdurma):**
   - Bir şirket için e-posta gönderim kararı (`SEND_EMAIL` / `SEND_EMAIL_LOW_PRIORITY`)
     çıktığında, **LLM çağrısı yapmadan önce** SQLite üzerinden 30 gün kuralı (`check_cooldown`)
     kontrol edilir. Böylece cooldown içindeki şirketler için gereksiz yere Gemini API kotası
     harcanmaz!
   - Günlük gönderim kotası (`QUOTA_EXCEEDED`) veya kullanıcının belirlediği oturum limiti
     (`max_applications` / `--limit`) dolduğunda döngü erken ve temiz biçimde sonlandırılır.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from src.config.settings import AppSettings, load_settings
from src.database.db_manager import ApplicationStatus, DatabaseManager
from src.filter.ats_detector import ATSDetector
from src.filter.decision_tree import DecisionTreeEngine, RoutingAction
from src.generator.llm_client import GeminiLLMClient
from src.generator.prompt_builder import PromptBuilder
from src.generator.template_manager import CandidateLinks
from src.mailer.email_sender import EmailSender, SendOutcome, SMTPConfig
from src.rag.vector_store import VectorStoreManager
from src.scraper.domain_filter import DomainFilter
from src.scraper.email_extractor import EmailExtractor
from src.scraper.page_fetcher import PlaywrightPageFetcher
from src.scraper.query_builder import build_dork_queries
from src.scraper.rate_limiter import RateLimiter
from src.scraper.search_engine import SearchEngineClient
from src.utils.logger import get_logger

logger = get_logger(__name__)

# Kullanıcı özel anahtar kelime vermezse kullanılacak varsayılan niş uzmanlık kelimeleri
DEFAULT_PIPELINE_KEYWORDS: tuple[str, ...] = (
    "ROS 2",
    "SLAM",
    "Otonom Sistemler",
    "Bilgisayarlı Görü",
)


@dataclass(frozen=True)
class CompanyProcessReport:
    """Orkestratörün tek bir şirket için yürüttüğü sürecin özet raporu."""

    company_name: str
    company_url: str
    language: str = "TR"
    routing_action: str = ""
    target_email: str = ""
    ats_url: str = ""
    status: str = ""          # 'SENT', 'DRY_RUN', 'MANUAL_ATS', 'COOLDOWN_SKIPPED', 'SKIP_NO_EMAIL', 'FAILED', 'ISOLATED_ERROR'
    used_fallback_template: bool = False
    detail: str = ""


@dataclass
class PipelineRunSummary:
    """Tüm orkestrasyon çalışmasının CLI'da (`rich` tablosunda) gösterilecek genel özeti."""

    dry_run: bool = True
    queries_executed: list[str] = field(default_factory=list)
    total_search_results: int = 0
    filtered_aggregator_count: int = 0
    valid_company_candidates: int = 0
    sent_count: int = 0
    dry_run_count: int = 0
    manual_ats_count: int = 0
    cooldown_skipped_count: int = 0
    skipped_no_email_count: int = 0
    failed_send_count: int = 0
    isolated_errors: int = 0
    quota_reached: bool = False
    company_reports: list[CompanyProcessReport] = field(default_factory=list)


class AutonomousPipeline:
    """Tüm keşif, filtreleme, karar ağacı, RAG+LLM üretim ve güvenli gönderim katmanlarını
    şirket bazlı hata izolasyonuyla yöneten ana orkestratör sınıfı.
    """

    def __init__(
        self,
        db_manager: DatabaseManager,
        search_client: SearchEngineClient,
        domain_filter: DomainFilter,
        page_fetcher: PlaywrightPageFetcher,
        decision_engine: DecisionTreeEngine,
        prompt_builder: PromptBuilder,
        llm_client: GeminiLLMClient,
        email_sender: EmailSender,
        excluded_dork_domains: Sequence[str] | None = None,
    ) -> None:
        self.db_manager = db_manager
        self.search_client = search_client
        self.domain_filter = domain_filter
        self.page_fetcher = page_fetcher
        self.decision_engine = decision_engine
        self.prompt_builder = prompt_builder
        self.llm_client = llm_client
        self.email_sender = email_sender
        self.excluded_dork_domains = excluded_dork_domains

    @classmethod
    def from_settings(
        cls,
        settings: AppSettings,
        vector_store: VectorStoreManager | None = None,
        dry_run_override: bool | None = None,
    ) -> AutonomousPipeline:
        """Doğrulanmış `AppSettings` konfigürasyon nesnesinden tüm alt bileşenleri inşa eder."""
        effective_dry_run = (
            dry_run_override if dry_run_override is not None else settings.app.dry_run
        )

        db_manager = DatabaseManager(
            sqlite_path=settings.database.sqlite_path,
            cooldown_days=settings.mailer.cooldown_days,
        )
        search_client = SearchEngineClient(
            max_results=settings.scraper.max_results_per_query,
        )
        domain_filter = DomainFilter(
            blacklisted_domains=settings.scraper.blacklisted_domains,
            blacklisted_title_patterns=settings.scraper.blacklisted_title_patterns,
        )
        rate_limiter = RateLimiter(
            min_sec=settings.scraper.rate_limit_min_sec,
            max_sec=settings.scraper.rate_limit_max_sec,
        )
        page_fetcher = PlaywrightPageFetcher(
            timeout_ms=settings.scraper.timeout_ms,
            rate_limiter=rate_limiter,
            target_subpages=settings.scraper.target_subpages,
            user_agent=settings.scraper.user_agent,
        )
        ats_detector = ATSDetector(ats_domains=settings.filter.ats_domains)
        email_extractor = EmailExtractor(
            blacklisted_prefixes=settings.scraper.blacklisted_email_prefixes,
            high_priority_prefixes=settings.scraper.high_priority_email_prefixes,
            low_priority_prefixes=settings.scraper.low_priority_email_prefixes,
            blacklisted_domains=settings.scraper.blacklisted_domains,
        )
        decision_engine = DecisionTreeEngine(
            ats_detector=ats_detector,
            email_extractor=email_extractor,
        )
        candidate_links = CandidateLinks.from_env_config(settings.env)
        prompt_builder = PromptBuilder(
            links=candidate_links,
            vector_store=vector_store,
            top_k=settings.rag.top_k,
            min_word_count=settings.generator.min_word_count,
            max_word_count=settings.generator.max_word_count,
        )
        llm_client = GeminiLLMClient(
            api_key=settings.env.gemini_api_key,
            model_name=settings.generator.gemini_model,
            temperature=settings.generator.temperature,
            min_word_count=settings.generator.min_word_count,
            max_word_count=settings.generator.max_word_count,
        )
        email_sender = EmailSender(
            smtp_config=SMTPConfig.from_env_config(settings.env),
            db_manager=db_manager,
            dry_run=effective_dry_run,
            daily_limit=settings.mailer.daily_limit,
            min_delay_sec=settings.mailer.min_delay_sec,
            max_delay_sec=settings.mailer.max_delay_sec,
        )

        return cls(
            db_manager=db_manager,
            search_client=search_client,
            domain_filter=domain_filter,
            page_fetcher=page_fetcher,
            decision_engine=decision_engine,
            prompt_builder=prompt_builder,
            llm_client=llm_client,
            email_sender=email_sender,
            excluded_dork_domains=settings.scraper.blacklisted_domains,
        )

    async def run_async(
        self,
        keywords: Sequence[str] | None = None,
        location: str = "Türkiye",
        intent: str = "kariyer",
        max_applications: int | None = None,
        browser_context: Any | None = None,
    ) -> PipelineRunSummary:
        """Uçtan uca asenkron keşif, analiz, üretim ve gönderim hattını çalıştırır."""
        summary = PipelineRunSummary(dry_run=self.email_sender.dry_run)
        kw_list = list(keywords) if keywords else list(DEFAULT_PIPELINE_KEYWORDS)

        # 1. Dork Arama Sorgularını Üret
        dork_queries = build_dork_queries(
            keywords=kw_list,
            location=location,
            intent=intent,
            excluded_domains=self.excluded_dork_domains,
        )
        summary.queries_executed = dork_queries

        # 2. Arama Motorunda Çalıştır ve Anti-Aggregator Filtresinden Geçir
        raw_results = self.search_client.search_batch(dork_queries)
        summary.total_search_results = len(raw_results)

        valid_candidates = self.domain_filter.filter_results(
            raw_results,
            deduplicate_domains=True,
        )
        summary.valid_company_candidates = len(valid_candidates)
        summary.filtered_aggregator_count = max(
            0, summary.total_search_results - summary.valid_company_candidates
        )

        processed_applications = 0

        # 3. Her Şirketi Hata İzolasyonlu (Fault-Isolated) Döngüde İşle
        for candidate in valid_candidates:
            # Kullanıcı oturum limiti (--limit) dolduysa erken çık
            if max_applications is not None and max_applications > 0:
                if processed_applications >= max_applications:
                    logger.info(
                        "Oturum başvuru limiti (%d) tamamlandı, kalan şirketler atlanıyor.",
                        max_applications,
                    )
                    break

            try:
                # 3.1. Sayfayı Çek (Playwright / Mock Fetcher)
                scrape_result = await self.page_fetcher.fetch_company_site(
                    candidate.url,
                    browser_context=browser_context,
                )
                comp_name = scrape_result.company_name or candidate.title or candidate.url

                # 3.2. Yönlendirme Karar Ağacı (ATS Override / High Priority / Low Priority / Skip)
                decision = self.decision_engine.evaluate_scrape_result(scrape_result)

                # Durum A: Şirket ATS kullanıyor -> Manuel ATS kuyruğuna kaydet
                if decision.action == RoutingAction.MANUAL_ATS:
                    self.db_manager.record_manual_ats(
                        company_name=comp_name,
                        company_url=scrape_result.base_url,
                        ats_url=decision.ats_url or candidate.url,
                        ats_provider=decision.ats_provider or "",
                        language=scrape_result.language,
                    )
                    summary.manual_ats_count += 1
                    summary.company_reports.append(
                        CompanyProcessReport(
                            company_name=comp_name,
                            company_url=scrape_result.base_url,
                            language=scrape_result.language,
                            routing_action=decision.action.value,
                            ats_url=decision.ats_url or "",
                            status="MANUAL_ATS",
                            detail=decision.reason,
                        )
                    )
                    continue

                # Durum B: Geçerli e-posta bulunamadı (veya sadece support@ vardı) -> SKIP
                if decision.action == RoutingAction.SKIP or not decision.target_email:
                    summary.skipped_no_email_count += 1
                    summary.company_reports.append(
                        CompanyProcessReport(
                            company_name=comp_name,
                            company_url=scrape_result.base_url,
                            language=scrape_result.language,
                            routing_action=decision.action.value,
                            status="SKIP_NO_EMAIL",
                            detail=decision.reason,
                        )
                    )
                    continue

                # Durum C: LLM Çağrısı Öncesi 30 Gün Cooldown Ön Kontrolü (API Kotası Tasarrufu!)
                cooldown_check = self.db_manager.check_cooldown(
                    target_email=decision.target_email,
                    company_url=scrape_result.base_url,
                )
                if cooldown_check.in_cooldown:
                    self.db_manager.record_application(
                        company_name=comp_name,
                        company_url=scrape_result.base_url,
                        target_email=decision.target_email,
                        status=ApplicationStatus.SKIPPED,
                        language=scrape_result.language,
                        error_message=cooldown_check.reason,
                    )
                    summary.cooldown_skipped_count += 1
                    summary.company_reports.append(
                        CompanyProcessReport(
                            company_name=comp_name,
                            company_url=scrape_result.base_url,
                            language=scrape_result.language,
                            routing_action=decision.action.value,
                            target_email=decision.target_email,
                            status="COOLDOWN_SKIPPED",
                            detail=cooldown_check.reason,
                        )
                    )
                    continue

                # Durum D: RAG + Dinamik Prompt İnşası ve Gemini LLM (veya Fallback) E-posta Üretimi
                built_prompt = self.prompt_builder.build_for_company(
                    company_name=comp_name,
                    company_context=scrape_result.combined_text,
                    language=scrape_result.language,
                )
                generated_email = self.llm_client.generate_email(
                    built_prompt,
                    links=self.prompt_builder.links,
                )

                # Durum E: Güvenli SMTP Gönderimi veya DRY-RUN Simülasyonu
                send_res = self.email_sender.send_application_email(
                    company_name=comp_name,
                    company_url=scrape_result.base_url,
                    recipient_email=decision.target_email,
                    subject=generated_email.subject,
                    body=generated_email.body,
                    language=generated_email.language,
                )

                if send_res.outcome == SendOutcome.SENT:
                    summary.sent_count += 1
                    processed_applications += 1
                elif send_res.outcome == SendOutcome.DRY_RUN:
                    summary.dry_run_count += 1
                    processed_applications += 1
                elif send_res.outcome == SendOutcome.COOLDOWN_SKIPPED:
                    summary.cooldown_skipped_count += 1
                elif send_res.outcome == SendOutcome.QUOTA_EXCEEDED:
                    summary.quota_reached = True
                    summary.company_reports.append(
                        CompanyProcessReport(
                            company_name=comp_name,
                            company_url=scrape_result.base_url,
                            language=scrape_result.language,
                            routing_action=decision.action.value,
                            target_email=decision.target_email,
                            status="QUOTA_EXCEEDED",
                            detail=send_res.error_message,
                        )
                    )
                    logger.warning("Günlük gönderim kotasına ulaşıldı, pipeline durduruluyor.")
                    break
                else:
                    summary.failed_send_count += 1

                summary.company_reports.append(
                    CompanyProcessReport(
                        company_name=comp_name,
                        company_url=scrape_result.base_url,
                        language=generated_email.language,
                        routing_action=decision.action.value,
                        target_email=decision.target_email,
                        status=send_res.outcome.value,
                        used_fallback_template=generated_email.used_fallback,
                        detail=send_res.error_message or decision.reason,
                    )
                )

            except Exception as exc:
                # HATA İZOLASYONU (FAULT ISOLATION):
                # Tek bir şirkette beklenmeyen hata patlarsa sistemi çökertme; logla ve sıradakine geç!
                summary.isolated_errors += 1
                err_msg = f"Şirket işlenirken izole edilen beklenmeyen hata ({candidate.url}): {exc}"
                logger.error(err_msg)
                summary.company_reports.append(
                    CompanyProcessReport(
                        company_name=candidate.title or candidate.url,
                        company_url=candidate.url,
                        status="ISOLATED_ERROR",
                        detail=str(exc),
                    )
                )

        return summary

    def run(
        self,
        keywords: Sequence[str] | None = None,
        location: str = "Türkiye",
        intent: str = "kariyer",
        max_applications: int | None = None,
        browser_context: Any | None = None,
    ) -> PipelineRunSummary:
        """Senkron çağrılar (CLI ve Scheduler) için `run_async` sarmalayıcısı."""
        return asyncio.run(
            self.run_async(
                keywords=keywords,
                location=location,
                intent=intent,
                max_applications=max_applications,
                browser_context=browser_context,
            )
        )


def run_pipeline(
    keywords: Sequence[str] | None = None,
    location: str = "Türkiye",
    intent: str = "kariyer",
    max_applications: int | None = None,
    dry_run: bool | None = None,
    settings: AppSettings | None = None,
    vector_store: VectorStoreManager | None = None,
    db_manager: DatabaseManager | None = None,
    search_client: SearchEngineClient | None = None,
    domain_filter: DomainFilter | None = None,
    page_fetcher: PlaywrightPageFetcher | None = None,
    decision_engine: DecisionTreeEngine | None = None,
    prompt_builder: PromptBuilder | None = None,
    llm_client: GeminiLLMClient | None = None,
    email_sender: EmailSender | None = None,
) -> PipelineRunSummary:
    """Tek fonksiyon çağrısıyla tüm otonom kariyer asistanı hattını çalıştıran üst düzey yardımcı."""
    if all(
        comp is not None
        for comp in (
            db_manager,
            search_client,
            domain_filter,
            page_fetcher,
            decision_engine,
            prompt_builder,
            llm_client,
            email_sender,
        )
    ):
        pipeline = AutonomousPipeline(
            db_manager=db_manager,          # type: ignore[arg-type]
            search_client=search_client,    # type: ignore[arg-type]
            domain_filter=domain_filter,    # type: ignore[arg-type]
            page_fetcher=page_fetcher,      # type: ignore[arg-type]
            decision_engine=decision_engine,# type: ignore[arg-type]
            prompt_builder=prompt_builder,  # type: ignore[arg-type]
            llm_client=llm_client,          # type: ignore[arg-type]
            email_sender=email_sender,      # type: ignore[arg-type]
        )
    else:
        resolved_settings = settings or load_settings()
        pipeline = AutonomousPipeline.from_settings(
            settings=resolved_settings,
            vector_store=vector_store,
            dry_run_override=dry_run,
        )

    return pipeline.run(
        keywords=keywords,
        location=location,
        intent=intent,
        max_applications=max_applications,
    )

