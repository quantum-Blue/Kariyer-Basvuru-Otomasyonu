"""Üç Katmanlı E-posta Kazıyıcı ve `support@` Kara Liste Filtresi (src/scraper/email_extractor.py).

Bu modülün amacı:
1. **Önceki Prototipin 2 Numaralı Kritik Hatasını (`support@f6s.com` Tuzağı) Kesin Olarak Önlemek:**
   Web sayfalarından kazınan e-posta adreslerini 3 katmanlı sıkı bir sınıflandırmadan geçirerek
   müşteri destek (`support@`, `help@`, `destek@`), satış (`sales@`, `satis@`), sistem (`noreply@`,
   `admin@`) ve hukuk/KVKK (`kvkk@`, `privacy@`) adreslerini **KESİN KARA LİSTE (`BLACKLISTED`)**
   olarak işaretlemek ve başvuru listesinden %100 imha etmek.
2. **Kirli HTML / URL Kaçış Karakterlerini Temizlemek (`clean_raw_email_string`):**
   `mailto:%20kariyer@sirket.com?subject=CV`, `\\u003ehr@sirket.com`, `&#105;&#107;@sirket.com`
   gibi kodlanmış dizgeleri temizleyerek saf e-posta adresini çıkarmak.
3. **Sahte Görsel / Script Eşleşmelerini Elemek:**
   HTML içinde `@` işareti barındıran `logo@2x.png`, `banner@3x.jpg`, `bundle@1.2.js` veya
   Sentry hata izleme anahtarlarını (`605a7b...@sentry.io`) e-posta sanma hatasını önlemek.
4. **Akıllı Öncelik Sıralaması:**
   Geçerli şirket e-postalarını önce **Yüksek Öncelik (`HIGH_PRIORITY` — `kariyer@`, `ik@`, `hr@`)**,
   ardından **Düşük Öncelik (`LOW_PRIORITY` — `info@`, `iletisim@`, `contact@`)** sırasıyla döndürmektir.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum
import html
import re
from urllib.parse import unquote

from src.scraper.domain_filter import is_blacklisted_url
from src.utils.logger import get_logger

logger = get_logger(__name__)


class EmailTier(str, Enum):
    """Üç katmanlı e-posta öncelik ve güvenlik sınıfı."""

    HIGH_PRIORITY = "HIGH_PRIORITY"  # 1. Hedef: Doğrudan İK / Kariyer (kariyer@, ik@, hr@, jobs@ vb.)
    LOW_PRIORITY = "LOW_PRIORITY"    # 2. Hedef: Genel İletişim (info@, iletisim@, contact@, hello@ vb.)
    BLACKLISTED = "BLACKLISTED"      # KESİN KARA LİSTE (ÇÖP): support@, noreply@, sales@, @2x.png vb.


@dataclass(frozen=True)
class EmailCandidate:
    """Kazınan ve sınıflandırılan tekil e-posta adayı."""

    email: str
    tier: EmailTier
    domain: str
    local_part: str


# Katman 1: KESİN KARA LİSTE (ASLA GÖNDERİLMEZ)
DEFAULT_BLACKLISTED_EMAIL_PREFIXES: tuple[str, ...] = (
    "support",
    "help",
    "destek",
    "sales",
    "satis",
    "marketing",
    "pazarlama",
    "billing",
    "accounting",
    "muhasebe",
    "fatura",
    "finans",
    "finance",
    "noreply",
    "no-reply",
    "donotreply",
    "do-not-reply",
    "admin",
    "administrator",
    "webmaster",
    "hostmaster",
    "postmaster",
    "press",
    "media",
    "pr",
    "legal",
    "hukuk",
    "privacy",
    "kvkk",
    "gdpr",
    "abuse",
    "security",
    "guvenlik",
    "investor",
    "investors",
    "unsubscribe",
    "newsletter",
    "bulten",
)

# Katman 2: BİRİNCİL HEDEF (YÜKSEK ÖNCELİK — İK VE KARİYER)
DEFAULT_HIGH_PRIORITY_EMAIL_PREFIXES: tuple[str, ...] = (
    "kariyer",
    "career",
    "careers",
    "ik",
    "hr",
    "insankaynaklari",
    "humanresources",
    "talent",
    "talents",
    "jobs",
    "job",
    "join",
    "joinus",
    "recruiting",
    "recruitment",
    "people",
    "cv",
    "basvuru",
    "isealim",
    "staj",
    "intern",
    "internship",
)

# Katman 3: İKİNCİL HEDEF (DÜŞÜK ÖNCELİK — GENEL İLETİŞİM)
DEFAULT_LOW_PRIORITY_EMAIL_PREFIXES: tuple[str, ...] = (
    "info",
    "iletisim",
    "contact",
    "hello",
    "merhaba",
    "office",
    "bilgi",
    "genel",
    "kurumsal",
)

# Sahte/örnek veya telemetri e-posta domainleri
_FAKE_OR_TELEMETRY_DOMAINS: set[str] = {
    "example.com",
    "example.org",
    "example.net",
    "domain.com",
    "yourdomain.com",
    "company.com",
    "email.com",
    "test.com",
    "sentry.io",
    "sentry.wixpress.com",
    "wixpress.com",
}

# Görsel, script, stil ve font dosyası uzantıları (Örn: logo@2x.png veya bundle@1.0.js)
_ASSET_FILE_EXTENSIONS: tuple[str, ...] = (
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".webp",
    ".svg",
    ".ico",
    ".bmp",
    ".tiff",
    ".js",
    ".mjs",
    ".cjs",
    ".ts",
    ".jsx",
    ".tsx",
    ".css",
    ".scss",
    ".less",
    ".woff",
    ".woff2",
    ".ttf",
    ".eot",
    ".otf",
    ".map",
    ".json",
    ".xml",
    ".pdf",
    ".zip",
    ".rar",
)

# Ham metin ve HTML içinden e-posta adaylarını yakalayan düzenli ifade (Regex)
_RAW_EMAIL_REGEX = re.compile(
    r"(?:\\?u003[ce]|%[0-9a-fA-F]{2})*"
    r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,24}",
    re.IGNORECASE,
)

# Standart temiz e-posta doğrulama Regex'i
_STRICT_EMAIL_REGEX = re.compile(
    r"^[a-z0-9][a-z0-9._%+\-]{0,63}@[a-z0-9.\-]+\.[a-z]{2,24}$"
)

# Başlangıçta kalabilen Unicode kaçış dizilerini (\u003e, u003e, \u003c, u0020 vb.) temizleyen Regex
_LEADING_UNICODE_ESCAPE_REGEX = re.compile(r"^(?:\\+u00[0-9a-f]{2}|u00[0-9a-f]{2})+", re.IGNORECASE)


def clean_raw_email_string(raw_input: str) -> str:
    """Ham `mailto:` linkini veya HTML içinden kazınan kirli e-posta dizgesini temizler.

    Adım Adım Temizlik:
    1. HTML entity'lerini (`&#105;&#107;` -> `ik`) çözer.
    2. Başındaki `mailto:` önekini ve sonundaki `?subject=...` sorgu parametrelerini keser.
    3. URL kodlamasını (`%20` -> boşluk, `%40` -> `@`) çözer.
    4. JSON/JS kaynaklı `\\u003e` veya `u003e` (`>` işareti) kaçış dizilerini siler.
    5. Etrafındaki `< > " ' ( ) . , ; :` noktalama işaretlerini kırpar ve küçük harfe çevirir.
    """
    if not isinstance(raw_input, str) or not raw_input.strip():
        return ""

    # 1. HTML entity çözümü
    text = html.unescape(raw_input.strip())

    # 2. 'mailto:' önekini kaldır ve '?' sonrasındaki parametreleri (?subject=...) at
    if text.lower().startswith("mailto:"):
        text = text[7:]
    if "?" in text:
        text = text.split("?", 1)[0]

    # 3. URL percent-encoding (%20 vb.) çözümü
    text = unquote(text).strip()

    # 4. Baştaki \u003e / u003e / u003c gibi JSON/HTML kaçış kalıntılarını temizle
    text = _LEADING_UNICODE_ESCAPE_REGEX.sub("", text)

    # 5. Kenarlardaki noktalama ve parantezleri kırp
    text = text.strip(" \t\r\n<>\"'()[]{};:,.\\/")

    # Tekrar u003e kontrolü (tırnak içinde kalmış olabilir)
    text = _LEADING_UNICODE_ESCAPE_REGEX.sub("", text).strip(" \t\r\n<>\"'()[]{};:,.\\/")

    return text.lower()


def classify_email_address(
    email: str,
    blacklisted_prefixes: Sequence[str] | None = None,
    high_priority_prefixes: Sequence[str] | None = None,
    low_priority_prefixes: Sequence[str] | None = None,
    blacklisted_domains: Sequence[str] | None = None,
) -> EmailTier:
    """Bir e-posta adresini Üç Katmanlı Filtreleme mimarisine göre sınıflandırır.

    Güvenlik Kuralları:
    1. Formatı bozuksa veya `.png`, `.jpg`, `.js` gibi bir statik dosya uzantısıyla bitiyorsa -> `BLACKLISTED`.
    2. Domain kısmı rehber sitelerinden birine (`f6s.com`, `clutch.co`, `linkedin.com`) veya
       sahte/telemetri domainlerine (`sentry.io`, `example.com`) aitse (ön eki `career@f6s.com`
       olsa bile!) -> `BLACKLISTED`.
    3. Ön ekinde (veya nokta/tire ile ayrılmış alt parçalarında, örn. `customer.support@`,
       `no-reply@`) kara liste kelimesi varsa -> `BLACKLISTED`.
    4. Ön ekinde İK / Kariyer kelimesi (`kariyer`, `ik`, `hr`, `jobs`, `talent` vb.) varsa -> `HIGH_PRIORITY`.
    5. Diğer geçerli kurumsal iletişim adresleri (`info@`, `iletisim@`, `contact@` vb.) -> `LOW_PRIORITY`.
    """
    cleaned = clean_raw_email_string(email)
    if not cleaned or "@" not in cleaned:
        return EmailTier.BLACKLISTED

    # 1. Sahte görsel / script dosyası uzantısı kontrolü (Örn: banner@2x.png)
    if cleaned.endswith(_ASSET_FILE_EXTENSIONS):
        return EmailTier.BLACKLISTED

    if not _STRICT_EMAIL_REGEX.match(cleaned):
        return EmailTier.BLACKLISTED

    local_part, domain = cleaned.rsplit("@", 1)
    if not local_part or not domain or "." not in domain:
        return EmailTier.BLACKLISTED

    # 2. Sahte/örnek domain veya Sentry hash kontrolü
    if domain in _FAKE_OR_TELEMETRY_DOMAINS and (
        local_part in {"test", "yourname", "user", "name", "email"}
        or domain in {"example.com", "example.org", "domain.com", "yourdomain.com", "sentry.io", "wixpress.com"}
    ):
        return EmailTier.BLACKLISTED

    # 24+ karakterlik onaltılık (hexadecimal) otomatik üretilmiş adresleri (Sentry/UUID) reddet
    if re.fullmatch(r"[0-9a-f]{24,}", local_part):
        return EmailTier.BLACKLISTED

    # 3. Domain'in rehber/dizin sitesi (f6s.com, clutch.co vb.) olup olmadığını kontrol et
    if is_blacklisted_url(f"https://{domain}", blacklisted_domains=blacklisted_domains):
        return EmailTier.BLACKLISTED

    # Local-part'ı nokta, tire, alt çizgi ve artı işaretlerinden alt kelimelere ayır
    # Örn: "customer.support" -> ["customer", "support"], "ik.basvuru" -> ["ik", "basvuru"]
    sub_tokens = [tok for tok in re.split(r"[.\-_+]+", local_part) if tok]

    blacklist_set = {
        p.strip().lower()
        for p in (
            blacklisted_prefixes
            if blacklisted_prefixes is not None
            else DEFAULT_BLACKLISTED_EMAIL_PREFIXES
        )
        if p and str(p).strip()
    }
    high_set = {
        p.strip().lower()
        for p in (
            high_priority_prefixes
            if high_priority_prefixes is not None
            else DEFAULT_HIGH_PRIORITY_EMAIL_PREFIXES
        )
        if p and str(p).strip()
    }
    low_set = {
        p.strip().lower()
        for p in (
            low_priority_prefixes
            if low_priority_prefixes is not None
            else DEFAULT_LOW_PRIORITY_EMAIL_PREFIXES
        )
        if p and str(p).strip()
    }

    # 4. KESİN KARA LİSTE KONTROLÜ (Herhangi bir alt parça kara listedeyse anında çöpe at)
    if local_part in blacklist_set or any(tok in blacklist_set for tok in sub_tokens):
        return EmailTier.BLACKLISTED

    # 5. YÜKSEK ÖNCELİK (İK / KARİYER) KONTROLÜ
    if local_part in high_set or any(tok in high_set for tok in sub_tokens):
        return EmailTier.HIGH_PRIORITY

    # 6. DÜŞÜK ÖNCELİK (GENEL İLETİŞİM VEYA GEÇERLİ KURUMSAL ADRES)
    if local_part in low_set or any(tok in low_set for tok in sub_tokens):
        return EmailTier.LOW_PRIORITY

    return EmailTier.LOW_PRIORITY


def extract_emails_from_content(
    content: str,
    links: Sequence[str] | None = None,
    blacklisted_prefixes: Sequence[str] | None = None,
    high_priority_prefixes: Sequence[str] | None = None,
    low_priority_prefixes: Sequence[str] | None = None,
    blacklisted_domains: Sequence[str] | None = None,
) -> list[EmailCandidate]:
    """Sayfa kaynağından (HTML/Metin) ve `mailto:` linklerinden tüm e-postaları kazır,
    kara listedekileri (`support@` vb.) eler ve kalanları öncelik sırasına göre döndürür.

    Sıralama Garantisi:
    - `HIGH_PRIORITY` (`kariyer@`, `ik@`, `hr@`) adresler her zaman listenin en başında yer alır.
    - `LOW_PRIORITY` (`info@`, `iletisim@`) adresler listenin sonunda yer alır.
    - `BLACKLISTED` (`support@`, `noreply@`, `sales@`) adresler listeye ASLA dahil edilmez!
    """
    raw_matches: list[str] = []

    # 1. Önce sayfadaki <a href="mailto:..."> bağlantılarından adayları topla
    if links:
        for link in links:
            if isinstance(link, str) and link.strip().lower().startswith("mailto:"):
                raw_matches.append(link)

    # 2. Sayfa HTML/metin içeriğindeki tüm e-posta örüntülerini Regex ile bul
    if isinstance(content, str) and content.strip():
        unescaped_content = html.unescape(content)
        raw_matches.extend(_RAW_EMAIL_REGEX.findall(unescaped_content))

    high_priority_list: list[EmailCandidate] = []
    low_priority_list: list[EmailCandidate] = []
    seen_emails: set[str] = set()

    for raw in raw_matches:
        cleaned = clean_raw_email_string(raw)
        if not cleaned or cleaned in seen_emails:
            continue

        tier = classify_email_address(
            cleaned,
            blacklisted_prefixes=blacklisted_prefixes,
            high_priority_prefixes=high_priority_prefixes,
            low_priority_prefixes=low_priority_prefixes,
            blacklisted_domains=blacklisted_domains,
        )

        # Kara listedeki adresleri (support@, sales@, noreply@, f6s.com vb.) anında çöpe at
        if tier == EmailTier.BLACKLISTED:
            logger.debug("Kara listedeki veya geçersiz e-posta elendi: %s", cleaned)
            continue

        seen_emails.add(cleaned)
        local_part, domain = cleaned.rsplit("@", 1)
        candidate = EmailCandidate(
            email=cleaned,
            tier=tier,
            domain=domain,
            local_part=local_part,
        )

        if tier == EmailTier.HIGH_PRIORITY:
            high_priority_list.append(candidate)
        else:
            low_priority_list.append(candidate)

    sorted_candidates = high_priority_list + low_priority_list
    logger.info(
        "E-posta kazıma tamamlandı: %d yüksek öncelikli (İK), %d düşük öncelikli (Genel) bulundu.",
        len(high_priority_list),
        len(low_priority_list),
    )
    return sorted_candidates


class EmailExtractor:
    """Konfigürasyon parametreleriyle başlatılabilen nesne yönelimli e-posta kazıyıcı."""

    def __init__(
        self,
        blacklisted_prefixes: Sequence[str] | None = None,
        high_priority_prefixes: Sequence[str] | None = None,
        low_priority_prefixes: Sequence[str] | None = None,
        blacklisted_domains: Sequence[str] | None = None,
    ) -> None:
        self.blacklisted_prefixes = blacklisted_prefixes
        self.high_priority_prefixes = high_priority_prefixes
        self.low_priority_prefixes = low_priority_prefixes
        self.blacklisted_domains = blacklisted_domains

    def extract(
        self,
        content: str,
        links: Sequence[str] | None = None,
    ) -> list[EmailCandidate]:
        """Verilen içerik ve linklerden öncelik sıralı geçerli e-posta listesini döndürür."""
        return extract_emails_from_content(
            content=content,
            links=links,
            blacklisted_prefixes=self.blacklisted_prefixes,
            high_priority_prefixes=self.high_priority_prefixes,
            low_priority_prefixes=self.low_priority_prefixes,
            blacklisted_domains=self.blacklisted_domains,
        )

    def select_best_email(
        self,
        content: str,
        links: Sequence[str] | None = None,
    ) -> EmailCandidate | None:
        """Şirket için en uygun tekil e-postayı (önce İK, yoksa genel iletişim, hiçbiri yoksa None) seçer."""
        candidates = self.extract(content=content, links=links)
        return candidates[0] if candidates else None
