"""Dork Tabanlı Arama Sorgusu Üretici Modülü (src/scraper/query_builder.py).

Bu modülün amacı:
1. İlk prototipteki 'robotik şirketleri türkiye' gibi genel ve kalitesiz aramalar yerine,
   adayın niş uzmanlıklarını ("ROS 2", "SLAM", "Otonom İHA", "Bilgisayarlı Görü" vb.)
   tam eşleşme tırnakları içinde arayan akıllı arama motoru sorguları (Search Dorks) üretmek.
2. Arama motorunda en üst sıraları işgal eden B2B şirket rehberlerini ve iş ilanı portallarını
   (`-site:linkedin.com -site:clutch.co -site:f6s.com -site:kariyer.net -site:europages.com` vb.)
   daha arama aşamasında dışlayarak gerçek Ar-Ge/Teknoloji şirketlerinin kendi sitelerini öne çıkarmaktır.
"""

from __future__ import annotations

from collections.abc import Sequence
from urllib.parse import urlparse

from src.utils.logger import get_logger

logger = get_logger(__name__)

# Arama sorgusunda varsayılan olarak dışlanacak en yaygın rehber/dizin ve sosyal medya siteleri.
# Not: Arama motorlarının sorgu uzunluğu sınırını (URL/token limit) aşmamak için Dork içinde
# en kritik 8-10 domain dışlanır; geri kalan 30+ domain Faz 6'daki `domain_filter.py` katmanında elenir.
DEFAULT_DORK_EXCLUDED_DOMAINS: tuple[str, ...] = (
    "linkedin.com",
    "clutch.co",
    "f6s.com",
    "kariyer.net",
    "europages.com",
    "glassdoor.com",
    "indeed.com",
    "yenibiris.com",
)


class QueryBuildError(Exception):
    """Arama sorgusu (Dork) oluşturulurken geçersiz parametre verildiğinde fırlatılan hata."""


def _sanitize_domain_for_dork(raw_domain: str) -> str:
    """Kullanıcının veya ayar dosyasının verdiği domain dizgesini temiz bir kök/alan adına dönüştürür.

    Örnek:
    - `'https://www.clutch.co/tr/robotics'` -> `'clutch.co'`
    - `'  HTTP://F6S.COM/  '` -> `'f6s.com'`
    """
    cleaned = raw_domain.strip().lower()
    if not cleaned:
        return ""

    # Eğer başında http:// veya https:// varsa urlparse ile sadece host kısmını (netloc) al
    if "://" in cleaned:
        parsed = urlparse(cleaned)
        host = parsed.netloc or parsed.path.split("/")[0]
    else:
        host = cleaned.split("/")[0]

    # Başındaki 'www.' önekini kaldır ki '-site:clutch.co' tüm alt alan adlarını kapsasın
    if host.startswith("www."):
        host = host[4:]

    return host.strip()


def build_single_dork_query(
    keyword: str,
    location: str = "Türkiye",
    intent: str = "kariyer",
    excluded_domains: Sequence[str] | None = None,
) -> str:
    """Tek bir teknik anahtar kelime için negatif `-site:` operatörlü Dork sorgusu üretir.

    Üretilen Sorgu Örneği:
    `"ROS 2" Türkiye kariyer -site:linkedin.com -site:clutch.co -site:f6s.com -site:kariyer.net`

    Args:
        keyword: Aranacak niş uzmanlık veya teknoloji terimi (Örn: 'ROS 2', 'Fast-LIO2').
        location: Hedef ülke veya şehir (Örn: 'Türkiye', 'İstanbul', 'Ankara'). Boş bırakılabilir.
        intent: Şirket sayfası niyeti (Örn: 'kariyer', 'Ar-Ge', 'hakkımızda', 'careers').
        excluded_domains: `-site:` ile dışlanacak domain listesi. None ise varsayılan liste kullanılır.

    Returns:
        Arama motoruna gönderilmeye hazır Dork sorgu dizgesi.

    Raises:
        QueryBuildError: `keyword` boş veya sadece boşluktan oluşuyorsa.
    """
    if not isinstance(keyword, str) or not keyword.strip():
        raise QueryBuildError("Arama anahtar kelimesi (keyword) boş olamaz.")

    clean_kw = keyword.strip().strip('"')
    if not clean_kw:
        raise QueryBuildError("Arama anahtar kelimesi (keyword) sadece tırnak işaretinden oluşamaz.")

    # 1. Anahtar kelimeyi tam eşleşme için çift tırnak içine al
    query_parts: list[str] = [f'"{clean_kw}"']

    # 2. Konum (location) ve niyet (intent) kelimelerini ekle
    if location and location.strip():
        query_parts.append(location.strip())

    if intent and intent.strip():
        query_parts.append(intent.strip())

    # 3. Dışlanacak rehber/dizin sitelerini (-site:domain.com) formatında ekle
    domains_to_use = (
        excluded_domains if excluded_domains is not None else DEFAULT_DORK_EXCLUDED_DOMAINS
    )

    seen_domains: set[str] = set()
    for raw_dom in domains_to_use:
        sanitized = _sanitize_domain_for_dork(str(raw_dom))
        if sanitized and sanitized not in seen_domains:
            seen_domains.add(sanitized)
            query_parts.append(f"-site:{sanitized}")

    final_query = " ".join(query_parts)
    logger.debug("Dork sorgusu üretildi: %s", final_query)
    return final_query


def build_dork_queries(
    keywords: Sequence[str],
    location: str = "Türkiye",
    intent: str = "kariyer",
    excluded_domains: Sequence[str] | None = None,
) -> list[str]:
    """Birden fazla uzmanlık kelimesi için tekilleştirilmiş (deduplicated) Dork sorgu listesi üretir.

    Neden Tekilleştirme Yapıyoruz?
    RAG katmanından veya kullanıcı girdisinden aynı anahtar kelime ('SLAM', 'slam', '  SLAM  ')
    birden fazla kez gelebilir. Arama motoruna gereksiz yere aynı sorguyu iki kez atıp
    rate-limit (hız sınırı) yememek için kelimeler normalize edilerek tekilleştirilir.

    Args:
        keywords: Uzmanlık kelimeleri listesi.
        location: Hedef konum.
        intent: Hedef sayfa niyeti.
        excluded_domains: Dışlanacak rehber site domainleri.

    Returns:
        Benzersiz Dork sorgularından oluşan liste.

    Raises:
        QueryBuildError: Liste boşsa veya listedeki hiçbir kelime geçerli değilse.
    """
    if not keywords:
        raise QueryBuildError("Anahtar kelime listesi (keywords) boş olamaz.")

    queries: list[str] = []
    seen_keywords_lower: set[str] = set()

    for raw_kw in keywords:
        if not isinstance(raw_kw, str):
            continue
        cleaned = raw_kw.strip().strip('"').strip()
        if not cleaned:
            continue

        # Büyük/küçük harf duyarsız tekilleştirme kontrolü
        norm_key = cleaned.lower()
        if norm_key in seen_keywords_lower:
            continue
        seen_keywords_lower.add(norm_key)

        queries.append(
            build_single_dork_query(
                keyword=cleaned,
                location=location,
                intent=intent,
                excluded_domains=excluded_domains,
            )
        )

    if not queries:
        raise QueryBuildError("Geçerli hiçbir arama anahtar kelimesi (keywords) bulunamadı.")

    return queries
