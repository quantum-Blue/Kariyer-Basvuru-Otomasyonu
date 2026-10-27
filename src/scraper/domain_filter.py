"""Domain Kara Listesi ve Rehber Sitesi (Anti-Aggregator) Filtresi (src/scraper/domain_filter.py).

Bu modülün amacı:
1. **Önceki Prototipin 1 Numaralı Hatasını (Aggregator Trap) Kesin Olarak Önlemek:**
   Arama motorundan gelen sonuçlar arasında yer alan `clutch.co`, `f6s.com`, `europages`,
   `linkedin.com`, `kariyer.net`, `glassdoor.com`, `indeed.com`, `sortlist.com`, `yellowpages.com`
   gibi B2B rehber/dizin sitelerini daha Playwright ile siteye girmeden kapıda elemek.
2. **Alt Alan Adı (Subdomain) Duyarlılığı ve False-Positive Koruması:**
   - `tr.linkedin.com` veya `sub.deep.f6s.com` gibi alt alan adlarını %100 yakalamak.
   - Buna karşılık, örneğin kara listede `x.com` veya `f6s.com` var diye, gerçek bir şirket olan
     `apex.com` veya `myf6srobotics.com` sitelerini YANLIŞLIKLA ELEMEMEK! Bunun için basit
     `in` kontrolü yerine nokta (`.`) sınırlı kök domain kontrolü (`host == d or host.endswith("." + d)`) yapılır.
3. **Liste / Blog Yazısı (Listicle) Başlık Filtresi:**
   Başlığında *"Top 10 Robotics Companies"*, *"15 Best Companies"*, *"Türkiye'deki En İyi 10 Şirket"*,
   *"Firma Rehberi"* geçen blog ve liste sayfalarını tespit edip elemektir.
"""

from __future__ import annotations

from collections.abc import Sequence
import re
from urllib.parse import urlparse

from src.scraper.search_engine import SearchResultItem
from src.utils.logger import get_logger

logger = get_logger(__name__)

# Varsayılan kapsamlı B2B rehber, iş ilanı, sosyal medya ve sözlük/ansiklopedi kara listesi
DEFAULT_BLACKLISTED_DOMAINS: tuple[str, ...] = (
    "clutch.co",
    "f6s.com",
    "europages.com",
    "europages.co.uk",
    "europages.com.tr",
    "europages.de",
    "europages.fr",
    "linkedin.com",
    "kariyer.net",
    "glassdoor.com",
    "indeed.com",
    "sortlist.com",
    "yellowpages.com",
    "goodfirms.co",
    "crunchbase.com",
    "pitchbook.com",
    "ycombinator.com",
    "g2.com",
    "capterra.com",
    "trustpilot.com",
    "sikayetvar.com",
    "yenibiris.com",
    "eleman.net",
    "secretcv.com",
    "jooble.org",
    "kompass.com",
    "wikipedia.org",
    "youtube.com",
    "instagram.com",
    "facebook.com",
    "twitter.com",
    "x.com",
    "medium.com",
    "reddit.com",
    "quora.com",
    "github.com",
    "gitlab.com",
)

# Varsayılan rehber/liste başlığı alt dizgeleri (küçük harf)
DEFAULT_BLACKLISTED_TITLE_PATTERNS: tuple[str, ...] = (
    "top 10",
    "top 20",
    "top 25",
    "top 50",
    "top 100",
    "best robotics companies",
    "best companies",
    "b2b şirketleri",
    "şirket rehberi",
    "firma rehberi",
    "company directory",
    "list of companies",
)

# "Top 15 ...", "20 Best ... Companies", "En İyi 10 ... Şirketi", "List of ... Companies"
# kalıplarını yakalayan derlenmiş düzenli ifadeler (Regex)
_LISTICLE_TITLE_REGEXES: tuple[re.Pattern[str], ...] = (
    re.compile(r"\b(?:top|best)\s+\d+\b", re.IGNORECASE),
    re.compile(r"\b\d+\s+best\s+.*\b(?:companies|firms|startups|suppliers)\b", re.IGNORECASE),
    re.compile(
        r"\b(?:best|top)\s+[\w\s&-]{1,40}\b(?:companies|firms|startups|suppliers)\b",
        re.IGNORECASE,
    ),
    re.compile(r"\ben\s+iyi\s+\d*\s*.*\b(?:şirket|sirket|firma)", re.IGNORECASE),
    re.compile(r"\blist\s+of\s+.*\b(?:companies|firms|startups)\b", re.IGNORECASE),
)

# Playwright'ın HTML sayfası diye açmaya çalışmaması gereken ikili (binary) dosya uzantıları
_DISALLOWED_URL_EXTENSIONS: tuple[str, ...] = (
    ".pdf",
    ".zip",
    ".rar",
    ".7z",
    ".tar",
    ".gz",
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".svg",
    ".mp4",
    ".mp3",
    ".exe",
    ".apk",
)


def extract_clean_hostname(url: str) -> str:
    """Verilen bir URL'den şemayı (`https://`), `www.` önekini, port numarasını (`:443`)
    ve alt yolları (`/tr/kariyer`) temizleyerek saf alan adını (hostname) döndürür.

    Örnekler:
    - `'https://www.clutch.co:443/profile/test'` -> `'clutch.co'`
    - `'https://tr.linkedin.com/company/xyz'` -> `'tr.linkedin.com'`
    - `'www.kariyer.net/is-ilanlari'` -> `'kariyer.net'`
    - `'javascript:alert(1)'` -> `''` (Geçersiz şema)
    """
    if not isinstance(url, str) or not url.strip():
        return ""

    raw = url.strip()
    lowered = raw.lower()

    # Güvenli olmayan veya web sayfası olmayan şemaları anında reddet
    if lowered.startswith(("javascript:", "mailto:", "tel:", "data:", "file:", "ftp:")):
        return ""

    # Eğer URL başında 'http://' veya 'https://' yoksa urlparse'ın host'u doğru tanıması için ekle
    if "://" not in lowered:
        lowered = "https://" + lowered

    try:
        parsed = urlparse(lowered)
    except Exception:
        return ""

    if parsed.scheme not in ("http", "https"):
        return ""

    host = (parsed.hostname or "").strip().lower().rstrip(".")
    if not host or "." not in host:
        return ""

    # Başındaki 'www.' önekini temizle (örn. 'www.aselsan.com.tr' -> 'aselsan.com.tr')
    if host.startswith("www."):
        host = host[4:]

    return host


def is_blacklisted_url(
    url: str,
    blacklisted_domains: Sequence[str] | None = None,
) -> bool:
    """Verilen URL'nin kara listedeki bir rehber/dizin sitesine ait olup olmadığını
    (alt alan adları dahil) veya geçersiz bir bağlantı olup olmadığını kontrol eder.

    Nasıl Çalışır?
    1. URL geçersizse, boşsa veya `.pdf` / `.zip` gibi bir dosya indirme linkiyse `True` (engelle) döner.
    2. URL'nin temiz host adını çıkarır (`extract_clean_hostname`).
    3. Kara listedeki her `blocked` domain için:
       - `host == blocked` (Tam eşleşme: `clutch.co == clutch.co`) VEYA
       - `host.endswith("." + blocked)` (Alt alan adı eşleşmesi: `tr.linkedin.com` sonu `.linkedin.com`)
       kontrolü yapar.
       Bu sayede `apex.com` adresi `x.com` yüzünden yanlışlıkla ENGELLENMEZ!

    Args:
        url: Kontrol edilecek URL.
        blacklisted_domains: Özel domain kara listesi. `None` ise varsayılan liste kullanılır.

    Returns:
        URL engellenmeli ise `True`, gerçek/geçerli bir şirket URL'si ise `False`.
    """
    host = extract_clean_hostname(url)
    if not host:
        return True

    # URL yolunun (path) ikili bir dosya (.pdf, .zip vb.) olup olmadığını kontrol et
    try:
        parsed_path = urlparse(url if "://" in url else f"https://{url}").path.lower()
        if parsed_path.endswith(_DISALLOWED_URL_EXTENSIONS):
            return True
    except Exception:
        return True

    domains = (
        blacklisted_domains if blacklisted_domains is not None else DEFAULT_BLACKLISTED_DOMAINS
    )

    for raw_blocked in domains:
        blocked = extract_clean_hostname(str(raw_blocked))
        if not blocked:
            continue
        # Tam domain eşleşmesi veya nokta sınırlı alt alan adı (subdomain) eşleşmesi
        if host == blocked or host.endswith(f".{blocked}"):
            logger.debug("URL domain kara listesine takıldı: url=%s (eşleşen=%s)", url, blocked)
            return True

    return False


def _normalize_turkish_lower(text: str) -> str:
    """Türkçe büyük 'İ' harfinin Python `.lower()` çağrısında 'i\\u0307' (çift karakter)
    üretip kelime eşleşmelerini ('İyi' != 'iyi', 'İstanbul' != 'istanbul') bozmasını önler.
    """
    cleaned = text.replace("İ", "i").replace("I", "ı").lower().replace("i\u0307", "i")
    return " ".join(cleaned.split())


def is_aggregator_title(
    title: str,
    blacklisted_patterns: Sequence[str] | None = None,
) -> bool:
    """Sayfa başlığının bir şirket rehberi veya 'Top 10 Companies' tarzı blog/liste
    yazısı olup olmadığını kontrol eder.

    Args:
        title: Arama motorundan veya sayfadan gelen başlık (title).
        blacklisted_patterns: Başlıkta aranacak kara liste alt dizgeleri.

    Returns:
        Başlık bir rehber/liste sayfasına aitse `True`, normal şirket başlığıysa `False`.
    """
    if not isinstance(title, str) or not title.strip():
        return False

    normalized_title = _normalize_turkish_lower(title)
    patterns = (
        blacklisted_patterns
        if blacklisted_patterns is not None
        else DEFAULT_BLACKLISTED_TITLE_PATTERNS
    )

    # 1. Açık alt dizge (substring) kalıplarını kontrol et (örn. 'şirket rehberi', 'b2b şirketleri')
    for pattern in patterns:
        clean_pat = _normalize_turkish_lower(str(pattern))
        if clean_pat and clean_pat in normalized_title:
            logger.debug("Başlık kara liste kalıbına takıldı: title='%s' (kalıp='%s')", title, clean_pat)
            return True

    # 2. Dinamik sayısal liste başlıklarını ('Top 25 ...', '15 Best ... Companies' vb.) Regex ile kontrol et
    for regex in _LISTICLE_TITLE_REGEXES:
        if regex.search(normalized_title):
            logger.debug("Başlık listicle regex filtresine takıldı: title='%s'", title)
            return True

    return False


class DomainFilter:
    """Arama sonuçlarını (`SearchResultItem`) hem domain kara listesi hem de başlık
    kalıplarına göre süzen ve isteğe bağlı olarak şirket domain'i bazında tekilleştiren sınıf.
    """

    def __init__(
        self,
        blacklisted_domains: Sequence[str] | None = None,
        blacklisted_title_patterns: Sequence[str] | None = None,
    ) -> None:
        self.blacklisted_domains = list(
            blacklisted_domains if blacklisted_domains is not None else DEFAULT_BLACKLISTED_DOMAINS
        )
        self.blacklisted_title_patterns = list(
            blacklisted_title_patterns
            if blacklisted_title_patterns is not None
            else DEFAULT_BLACKLISTED_TITLE_PATTERNS
        )

    def is_valid_candidate(self, item: SearchResultItem) -> bool:
        """Tek bir arama sonucunun gerçek bir şirket adayı olup olmadığını doğrular."""
        if not isinstance(item, SearchResultItem):
            return False

        # 1. Domain kara listesi kontrolü (clutch.co, f6s.com, tr.linkedin.com vb.)
        if is_blacklisted_url(item.url, blacklisted_domains=self.blacklisted_domains):
            return False

        # 2. Başlık (Title) rehber/liste kontrolü ("Top 10 Robotics Companies" vb.)
        if is_aggregator_title(item.title, blacklisted_patterns=self.blacklisted_title_patterns):
            return False

        return True

    def filter_results(
        self,
        results: Sequence[SearchResultItem],
        deduplicate_domains: bool = True,
    ) -> list[SearchResultItem]:
        """Arama motoru sonuç listesini filtreler ve yalnızca gerçek şirket adaylarını döndürür.

        Neden `deduplicate_domains=True` Kullanıyoruz?
        Arama sonucunda aynı şirketin hem `/kariyer` hem `/hakkimizda` sayfası ayrı ayrı
        çıkabilir. Zaten Faz 7'deki Playwright modülümüz bir şirketin sitesine girdiğinde
        alt sayfalarını otomatik tarayacağı için, aynı şirket domain'ini kuyruğa iki kez
        sokmamak adına domain bazında tekilleştirme yapılır.

        Args:
            results: Ham arama motoru sonuçları.
            deduplicate_domains: Aynı alan adına (hostname) sahip ikinci sonucu elesin mi?

        Returns:
            Filtrelenmiş temiz `SearchResultItem` listesi.
        """
        if not results:
            return []

        valid_items: list[SearchResultItem] = []
        seen_hosts: set[str] = set()

        for item in results:
            if not self.is_valid_candidate(item):
                continue

            host = extract_clean_hostname(item.url)
            if deduplicate_domains:
                if host in seen_hosts:
                    logger.debug("Aynı şirket domain'ine ait mükerrer sonuç atlandı: %s", item.url)
                    continue
                seen_hosts.add(host)

            valid_items.append(item)

        logger.info(
            "Anti-Aggregator filtreleme tamamlandı: %d adaydan %d gerçek şirket kaldı.",
            len(results),
            len(valid_items),
        )
        return valid_items
