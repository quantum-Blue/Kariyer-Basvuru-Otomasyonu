"""Deterministik Yönlendirme Karar Ağacı (Routing Decision Tree) Modülü (src/filter/decision_tree.py).

Bu modülün amacı:
1. Kazıma katmanından (`src/scraper/`) gelen şirket verilerini (HTML, linkler ve kazınmış e-postalar)
   değerlendirerek her şirket için **%100 deterministik** (öngörülebilir ve tekrarlanabilir) bir
   başvuru stratejisi (`RoutingAction`) belirlemek.
2. **4 Temel Karar Dalı (`RoutingAction`):**
   - `MANUAL_ATS` (1. Öncelik / Override):
     Sayfada Greenhouse, Lever, Workday, BambooHR, Workable vb. bir ATS başvuru sistemi linki varsa,
     sayfada `kariyer@sirket.com` bulunsa bile ATS kuralı e-postayı EZER (Override)!
     Şirkete otomatik e-posta atılmaz (`target_email = None`), kullanıcının manuel form doldurması
     için `MANUAL_ATS` kuyruğuna kaydedilir.
   - `SEND_EMAIL` (2. Öncelik):
     Sayfada ATS yoksa ve en az bir adet **1. Öncelik (`HIGH_PRIORITY` — `kariyer@`, `ik@`, `hr@`,
     `jobs@`, `talent@`)** e-posta adresi bulunduysa şirkete doğrudan İK başvurusu yapılır.
   - `SEND_EMAIL_LOW_PRIORITY` (3. Öncelik):
     Sayfada ATS yoksa, İK e-postası da bulunamadıysa ancak **2. Öncelik (`LOW_PRIORITY` —
     `info@`, `iletisim@`, `contact@`, `hello@`)** genel iletişim adresi varsa (ve `allow_low_priority=True`
     ise) bu adrese başvuru yapılır.
   - `SKIP` (4. Öncelik):
     Sayfada ne bir ATS linki ne de geçerli bir e-posta bulunduysa (örneğin yalnızca `support@`,
     `noreply@`, `sales@`, `kvkk@` gibi kara liste adresleri varsa) şirket atlanır (`SKIP`).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import Enum

from src.filter.ats_detector import ATSDetector
from src.scraper.email_extractor import EmailCandidate, EmailExtractor, EmailTier
from src.scraper.page_fetcher import CompanyScrapeResult
from src.utils.logger import get_logger

logger = get_logger(__name__)


class RoutingAction(str, Enum):
    """Bir şirket için Yönlendirme Karar Ağacının verebileceği 4 kesin eylem kararı."""

    MANUAL_ATS = "MANUAL_ATS"                            # ATS formu var -> Manuel başvuru listesine ekle (E-postayı ezer!)
    SEND_EMAIL = "SEND_EMAIL"                            # 1. Öncelik İK e-postası (kariyer@, hr@, ik@) -> Otomatik mail gönder
    SEND_EMAIL_LOW_PRIORITY = "SEND_EMAIL_LOW_PRIORITY"  # 2. Öncelik genel iletişim (info@, iletisim@) -> Otomatik mail gönder
    SKIP = "SKIP"                                        # Ne ATS ne geçerli e-posta var -> Şirketi atla


@dataclass(frozen=True)
class RoutingDecision:
    """Karar ağacının ürettiği gerekçeli yönlendirme sonucu."""

    action: RoutingAction                                         # Verilen nihai karar
    target_email: str | None = None                               # Gönderim yapılacaksa seçilen alıcı e-posta adresi
    email_tier: EmailTier | None = None                           # Seçilen e-postanın katmanı (HIGH_PRIORITY / LOW_PRIORITY)
    ats_url: str | None = None                                    # MANUAL_ATS kararında tespit edilen birincil ATS başvuru linki
    ats_provider: str | None = None                               # Tespit edilen ATS sağlayıcısı (Örn: 'greenhouse.io')
    all_ats_urls: list[str] = field(default_factory=list)         # Sayfadaki tüm ATS linkleri
    candidate_emails: list[EmailCandidate] = field(default_factory=list)  # Sayfada bulunan tüm geçerli e-posta adayları
    reason: str = ""                                              # Kararın insan tarafından okunabilir açıklaması


def evaluate_company_routing(
    html_content: str = "",
    links: Sequence[str] | None = None,
    extracted_emails: Sequence[EmailCandidate] | None = None,
    ats_detector: ATSDetector | None = None,
    email_extractor: EmailExtractor | None = None,
    allow_low_priority: bool = True,
) -> RoutingDecision:
    """Verilen şirket sayfası verilerini deterministik karar ağacından geçirerek yönlendirme kararı üretir.

    Karar Ağacı Algoritması (Adım Adım):
    1. **Adım 1 (ATS Tespiti):** `ATSDetector` ile sayfadaki linkler ve HTML taranır.
    2. **Adım 2 (E-posta Kazıma):** Eğer `extracted_emails` dışarıdan hazır verilmemişse,
       `EmailExtractor` ile sayfadaki geçerli (`HIGH_PRIORITY` ve `LOW_PRIORITY`) e-postalar çıkarılır
       (`BLACKLISTED` olan `support@`, `noreply@` vb. bu aşamada elenir).
    3. **Adım 3 (KURAL 1 — ATS Override):**
       Eğer `ats_result.has_ats == True` ise, sayfada `kariyer@` bulunsa bile e-posta gönderimi
       iptal edilir (`target_email = None`) ve `RoutingAction.MANUAL_ATS` döndürülür.
    4. **Adım 4 (KURAL 2 — Yüksek Öncelikli İK E-postası):**
       Sayfada `EmailTier.HIGH_PRIORITY` e-posta varsa ilk sıradaki İK adresi seçilir ve
       `RoutingAction.SEND_EMAIL` döndürülür.
    5. **Adım 5 (KURAL 3 — Düşük Öncelikli Genel İletişim E-postası):**
       İK e-postası yoksa fakat `EmailTier.LOW_PRIORITY` (`info@`, `iletisim@`) varsa ve
       `allow_low_priority=True` ise ilk sıradaki genel adres seçilir ve
       `RoutingAction.SEND_EMAIL_LOW_PRIORITY` döndürülür.
    6. **Adım 6 (KURAL 4 — Atla / SKIP):**
       Yukarıdaki koşulların hiçbiri sağlanmazsa `RoutingAction.SKIP` döndürülür.
    """
    detector = ats_detector or ATSDetector()
    extractor = email_extractor or EmailExtractor()

    # 1. ATS Taraması
    ats_result = detector.detect(links=links, html_content=html_content)

    # 2. Geçerli E-posta Adaylarının Belirlenmesi (Kara listedekiler elenmiş olarak gelir)
    if extracted_emails is not None:
        valid_emails = [
            c for c in extracted_emails
            if isinstance(c, EmailCandidate) and c.tier != EmailTier.BLACKLISTED
        ]
    else:
        valid_emails = extractor.extract(content=html_content, links=links)

    # 3. KURAL 1: ATS OVERRIDE KURALI (ATS varsa e-postayı ezer!)
    if ats_result.has_ats:
        reason_msg = (
            f"ATS platformu ({ats_result.matched_provider}) tespit edildi -> "
            f"Otomatik e-posta devre dışı bırakıldı, MANUAL_ATS kuyruğuna yönlendirildi."
        )
        logger.info(reason_msg)
        return RoutingDecision(
            action=RoutingAction.MANUAL_ATS,
            target_email=None,
            email_tier=None,
            ats_url=ats_result.primary_ats_url,
            ats_provider=ats_result.matched_provider,
            all_ats_urls=list(ats_result.ats_urls),
            candidate_emails=valid_emails,
            reason=reason_msg,
        )

    # 4. KURAL 2: YÜKSEK ÖNCELİKLİ (İK / KARİYER) E-POSTA KONTROLÜ
    high_priority_candidates = [
        c for c in valid_emails if c.tier == EmailTier.HIGH_PRIORITY
    ]
    if high_priority_candidates:
        best_hr = high_priority_candidates[0]
        reason_msg = f"1. Öncelik İK/Kariyer e-postası bulundu ({best_hr.email}) -> SEND_EMAIL."
        logger.info(reason_msg)
        return RoutingDecision(
            action=RoutingAction.SEND_EMAIL,
            target_email=best_hr.email,
            email_tier=EmailTier.HIGH_PRIORITY,
            ats_url=None,
            ats_provider=None,
            all_ats_urls=[],
            candidate_emails=valid_emails,
            reason=reason_msg,
        )

    # 5. KURAL 3: DÜŞÜK ÖNCELİKLİ (GENEL İLETİŞİM: info@, iletisim@) E-POSTA KONTROLÜ
    low_priority_candidates = [
        c for c in valid_emails if c.tier == EmailTier.LOW_PRIORITY
    ]
    if low_priority_candidates and allow_low_priority:
        best_low = low_priority_candidates[0]
        reason_msg = (
            f"İK e-postası bulunamadı, 2. Öncelik genel iletişim adresi ({best_low.email}) "
            f"seçildi -> SEND_EMAIL_LOW_PRIORITY."
        )
        logger.info(reason_msg)
        return RoutingDecision(
            action=RoutingAction.SEND_EMAIL_LOW_PRIORITY,
            target_email=best_low.email,
            email_tier=EmailTier.LOW_PRIORITY,
            ats_url=None,
            ats_provider=None,
            all_ats_urls=[],
            candidate_emails=valid_emails,
            reason=reason_msg,
        )

    # 6. KURAL 4: GEÇERLİ E-POSTA VEYA ATS YOK -> SKIP
    reason_msg = "Sayfada ATS bağlantısı veya geçerli (kara liste dışı) bir e-posta adresi bulunamadı -> SKIP."
    logger.info(reason_msg)
    return RoutingDecision(
        action=RoutingAction.SKIP,
        target_email=None,
        email_tier=None,
        ats_url=None,
        ats_provider=None,
        all_ats_urls=[],
        candidate_emails=valid_emails,
        reason=reason_msg,
    )


class DecisionTreeEngine:
    """Konfigürasyon nesneleriyle (`ATSDetector`, `EmailExtractor`) başlatılabilen karar ağacı motoru."""

    def __init__(
        self,
        ats_detector: ATSDetector | None = None,
        email_extractor: EmailExtractor | None = None,
        allow_low_priority: bool = True,
    ) -> None:
        self.ats_detector = ats_detector or ATSDetector()
        self.email_extractor = email_extractor or EmailExtractor()
        self.allow_low_priority = allow_low_priority

    def evaluate(
        self,
        html_content: str = "",
        links: Sequence[str] | None = None,
        extracted_emails: Sequence[EmailCandidate] | None = None,
    ) -> RoutingDecision:
        """Ham HTML, linkler veya önceden kazınmış e-posta adayları üzerinden yönlendirme kararı verir."""
        return evaluate_company_routing(
            html_content=html_content,
            links=links,
            extracted_emails=extracted_emails,
            ats_detector=self.ats_detector,
            email_extractor=self.email_extractor,
            allow_low_priority=self.allow_low_priority,
        )

    def evaluate_scrape_result(
        self,
        scrape_result: CompanyScrapeResult,
        extracted_emails: Sequence[EmailCandidate] | None = None,
    ) -> RoutingDecision:
        """Faz 7'nin ürettiği `CompanyScrapeResult` nesnesini doğrudan değerlendirir."""
        return self.evaluate(
            html_content=scrape_result.combined_html,
            links=scrape_result.all_links,
            extracted_emails=extracted_emails,
        )
