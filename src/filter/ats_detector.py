"""ATS (Başvuru Takip Sistemi / Applicant Tracking System) Tespit Motoru (src/filter/ats_detector.py).

Bu modülün amacı:
1. Şirketlerin kariyer veya ana sayfalarında yer alan harici ATS (Başvuru Takip Sistemi)
   platformlarını (`greenhouse.io`, `lever.co`, `myworkdayjobs.com`, `workday.com`,
   `bamboohr.com`, `workable.com`, `smartrecruiters.com`, `personio.de`, `personio.com`,
   `ashbyhq.com`, `jobvite.com`, `icims.com`, `taleo.net`, `successfactors.com`, `recruitee.com` vb.)
   yüksek doğrulukla tespit etmek.
2. **Neden ATS Tespiti Yapıyoruz?**
   Kurumsal şirketler veya hızlı büyüyen girişimler (scale-up) işe alımlarını Greenhouse, Lever
   veya Workday gibi standart formlar üzerinden yürütür. Bu şirketlerin sayfalarında `info@` veya
   `kariyer@` adresi bulunsa bile, adayların başvurularını ATS portalındaki zorunlu alanları
   doldurarak yapması beklenir. Bu nedenle ATS tespit edilen şirketlere otomatik soğuk e-posta
   ATILMAZ; bu şirketler kullanıcının bizzat başvurması için `MANUAL_ATS` kuyruğuna yönlendirilir.
3. **Alt Alan Adı (Subdomain) Duyarlılığı ve False-Positive Koruması:**
   - `boards.greenhouse.io`, `jobs.eu.lever.co`, `nvidia.wd5.myworkdayjobs.com` gibi derin alt
     alan adlarını eksiksiz yakalar.
   - Buna karşılık, içinde `lever.co` harfleri geçiyor diye `https://clever.co` adresini veya
     `greenhouse.io` harfleri geçiyor diye `https://mygreenhouse.io` adresini YANLIŞLIKLA
     ATS sanmaz! Nokta (`.`) sınırlı kök alan adı (`host == ats or host.endswith("." + ats)`)
     kontrolü uygular.
4. **Gömülü (`<iframe>` / `<script>`) ATS Linklerini Yakalama:**
   Sadece `<a href="...">` etiketlerine bakmakla yetinmez; ham HTML içerisine gömülmüş
   `https://boards.greenhouse.io/embed/job_board?for=...` gibi URL'leri de Regex ile tarar.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
import html
import re
from urllib.parse import urlparse

from src.scraper.domain_filter import extract_clean_hostname
from src.utils.logger import get_logger

logger = get_logger(__name__)

# Varsayılan 18 popüler küresel ve Avrupa/Türkiye yaygınlığı olan ATS platformu alan adı
DEFAULT_ATS_DOMAINS: tuple[str, ...] = (
    "greenhouse.io",
    "lever.co",
    "myworkdayjobs.com",
    "workday.com",
    "bamboohr.com",
    "workable.com",
    "smartrecruiters.com",
    "personio.de",
    "personio.com",
    "ashbyhq.com",
    "jobvite.com",
    "icims.com",
    "taleo.net",
    "successfactors.com",
    "recruitee.com",
    "teamtailor.com",
    "breezy.hr",
    "jazzhr.com",
)

# Ham HTML içinde (<iframe src="...">, <script src="..."> veya düz metinde) geçen
# HTTP/HTTPS bağlantıları yakalayan düzenli ifade
_HTML_URL_REGEX = re.compile(
    r"https?://[a-zA-Z0-9.\-_~:/?#\[\]@!$&'()*+,;=%]+",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ATSDetectionResult:
    """Bir şirket sayfasında yapılan ATS taramasının sonucunu taşıyan veri modeli."""

    has_ats: bool                                  # En az bir ATS bağlantısı bulundu mu?
    matched_provider: str | None = None            # Eşleşen ilk ATS sağlayıcısının kök domain'i (Örn: 'greenhouse.io')
    ats_urls: list[str] = field(default_factory=list)  # Sayfada tespit edilen tüm benzersiz ATS URL'leri

    @property
    def primary_ats_url(self) -> str | None:
        """Varsa tespit edilen ilk (birincil) ATS başvuru bağlantısını döndürür."""
        return self.ats_urls[0] if self.ats_urls else None


def match_ats_provider(
    url: str,
    ats_domains: Sequence[str] | None = None,
) -> str | None:
    """Verilen URL bir ATS platformuna aitse o platformun kök domain adını (örn. `'greenhouse.io'`),
    ait değilse veya geçersiz bir URL ise `None` döndürür.

    Nasıl Çalışır?
    1. `mailto:`, `javascript:` gibi web sayfası olmayan şemaları anında reddeder.
    2. URL'nin temiz host adını (`extract_clean_hostname`) çıkarır.
    3. ATS listesindeki her `provider` için:
       - `host == provider` (Tam eşleşme: `workable.com == workable.com`) VEYA
       - `host.endswith("." + provider)` (Alt alan adı: `boards.greenhouse.io` sonu `.greenhouse.io`)
       kontrolü yapar.
       Böylece `clever.co` adresi `.lever.co` ile bitmediği için yanlış alarm (false-positive) üretmez!
    """
    if not isinstance(url, str) or not url.strip():
        return None

    cleaned_url = url.strip()
    lowered = cleaned_url.lower()

    # E-posta (mailto:) veya telefon (tel:) linkleri birer web başvuru formu değildir
    if lowered.startswith(("mailto:", "tel:", "javascript:", "data:", "#")):
        return None

    # Eğer şema belirtilmemişse yalnızca gerçek URL formatındaysa değerlendir
    if "://" in lowered:
        parsed_scheme = urlparse(lowered).scheme
        if parsed_scheme not in ("http", "https"):
            return None

    host = extract_clean_hostname(cleaned_url)
    if not host:
        return None

    providers = ats_domains if ats_domains is not None else DEFAULT_ATS_DOMAINS

    for raw_provider in providers:
        provider = extract_clean_hostname(str(raw_provider))
        if not provider:
            continue
        if host == provider or host.endswith(f".{provider}"):
            return provider

    return None


def is_ats_url(
    url: str,
    ats_domains: Sequence[str] | None = None,
) -> bool:
    """Verilen URL'nin bilinen bir ATS platformuna ait olup olmadığını döndürür."""
    return match_ats_provider(url, ats_domains=ats_domains) is not None


def detect_ats_in_page(
    links: Sequence[str] | None = None,
    html_content: str = "",
    ats_domains: Sequence[str] | None = None,
) -> ATSDetectionResult:
    """Şirket sayfasından çıkarılan bağlantıları (`links`) ve ham HTML kaynağını (`html_content`)
    tarayarak ATS başvuru formlarını tespit eder.

    Neden Hem `links` Hem `html_content` Taranır?
    - `links` listesi `BeautifulSoup` tarafından `<a href="...">` etiketlerinden çıkarılır.
    - Ancak pek çok şirket Greenhouse veya Lever başvuru formunu sayfa içine `<iframe src="...">`
      veya `<script src="...">` olarak gömer. `html_content` üzerindeki Regex taraması bu gömülü
      (embedded) ATS portallarını da kaçırmadan yakalar!

    Args:
        links: Sayfadan çıkarılan `<a href>` bağlantıları listesi.
        html_content: Sayfanın ham HTML veya metin içeriği.
        ats_domains: Aranacak ATS kök domain listesi (`None` ise varsayılan 18 platform kullanılır).

    Returns:
        Bulunan tüm ATS bağlantılarını ve sağlayıcı adını içeren `ATSDetectionResult` nesnesi.
    """
    candidate_urls: list[str] = []

    # 1. Önce doğrudan verilen link listesini ekle
    if links:
        for item in links:
            if isinstance(item, str) and item.strip():
                candidate_urls.append(item.strip())

    # 2. Ham HTML içerisindeki gömülü (iframe, script, json) HTTP/HTTPS URL'leri çıkar
    if isinstance(html_content, str) and html_content.strip():
        unescaped_html = html.unescape(html_content)
        for match in _HTML_URL_REGEX.findall(unescaped_html):
            # HTML tırnak veya parantez kalıntılarını URL sonundan temizle
            clean_match = match.rstrip("\"'<>);,]}")
            if clean_match:
                candidate_urls.append(clean_match)

    matched_urls: list[str] = []
    seen_urls: set[str] = set()
    first_provider: str | None = None

    for raw_url in candidate_urls:
        normalized_url = raw_url.strip()
        if not normalized_url or normalized_url in seen_urls:
            continue

        provider = match_ats_provider(normalized_url, ats_domains=ats_domains)
        if provider is not None:
            seen_urls.add(normalized_url)
            matched_urls.append(normalized_url)
            if first_provider is None:
                first_provider = provider

    if matched_urls:
        logger.info(
            "ATS platformu tespit edildi: sağlayıcı='%s', toplam_link=%d (ilk_link=%s)",
            first_provider,
            len(matched_urls),
            matched_urls[0],
        )
        return ATSDetectionResult(
            has_ats=True,
            matched_provider=first_provider,
            ats_urls=matched_urls,
        )

    return ATSDetectionResult(has_ats=False, matched_provider=None, ats_urls=[])


class ATSDetector:
    """Konfigürasyon (`settings.filter.ats_domains`) ile başlatılabilen nesne yönelimli ATS tespitçisi."""

    def __init__(self, ats_domains: Sequence[str] | None = None) -> None:
        self.ats_domains = list(
            ats_domains if ats_domains is not None else DEFAULT_ATS_DOMAINS
        )

    def is_ats_link(self, url: str) -> bool:
        """Tek bir bağlantının ATS platformuna ait olup olmadığını kontrol eder."""
        return is_ats_url(url, ats_domains=self.ats_domains)

    def detect(
        self,
        links: Sequence[str] | None = None,
        html_content: str = "",
    ) -> ATSDetectionResult:
        """Sayfa bağlantıları ve HTML içeriği üzerinde tam ATS taraması gerçekleştirir."""
        return detect_ats_in_page(
            links=links,
            html_content=html_content,
            ats_domains=self.ats_domains,
        )
