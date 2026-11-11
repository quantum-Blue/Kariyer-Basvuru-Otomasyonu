"""Dayanıklı (Resilient) Arama Motoru İstemcisi — DuckDuckGo (src/scraper/search_engine.py).

Bu modülün amacı:
1. `query_builder.py` tarafından üretilen Dork sorgularını ücretsiz DuckDuckGo (`duckduckgo-search`)
   üzerinden çalıştırıp şirket web sitesi adaylarını (`SearchResultItem`) toplamak.
2. **Sıfır Çökme Garantisi (Fault Resilience):**
   Gerçek dünyada arama motorları zaman zaman hız sınırı (`HTTP 429 Too Many Requests`,
   `HTTP 403 Forbidden`, `RatelimitException`) uygulayabilir veya internet bağlantısı anlık kopabilir.
   Bu modül bu tür hatalarda programı ASLA çökertmez; hatayı merkezi logger'a `WARNING`
   seviyesinde kaydeder ve güvenli şekilde boş liste (`[]`) döndürür.
3. **Mükerrer URL Temizliği ve Güvenlik:**
   Arama sonuçlarında aynı URL birden fazla kez dönerse veya `javascript:` gibi geçersiz
   bağlantılar gelirse bunları temizler.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
import re
from typing import Any
from urllib.parse import quote_plus, unquote
import warnings

from bs4 import BeautifulSoup

from src.utils.logger import get_logger

# duckduckgo_search paketinin yeniden adlandırma uyarısının terminali kirletmesini önle
warnings.filterwarnings("ignore", category=RuntimeWarning)

logger = get_logger(__name__)

# Kısa kısaltma aramalarında ("ROS 2", "SLAM" vb.) alakasız oyun/vergi sitelerini
# önlemek için sorguda bulunması beklenen sektör bağlam kelimeleri
_SECTOR_ANCHOR_TERMS: tuple[str, ...] = (
    "robotik",
    "robotics",
    "otonom",
    "autonomous",
    "teknoloji",
    "yazılım",
    "mühendislik",
    "savunma",
    "iha",
    "amr",
)

# Arama motorunun anti-bot zehirlemesi (SERP poisoning: alakasız yemek/oyun/forum sonuçları)
# döndürüp döndürmediğini doğrulamak için sonuçlarda aranan Türkçe/sektörel anahtar kelimeler
_RELEVANCE_CHECK_TOKENS: tuple[str, ...] = (
    "robot",
    "otonom",
    "slam",
    "amr",
    "agv",
    "lidar",
    "iha",
    "insansız",
    "otomasyon",
    "görüntü",
    "autonomous",
    "robotics",
)


@dataclass(frozen=True)
class SearchResultItem:
    """Arama motorundan dönen tek bir web sayfası adayını temsil eden veri modeli."""

    title: str        # Arama sonucunun sayfa başlığı (Örn: "Baykar Teknoloji - Kariyer")
    url: str          # Sayfanın tam HTTP/HTTPS adresi
    snippet: str      # Arama motorunun gösterdiği kısa özet metin (body/snippet)
    query: str = ""   # Bu sonucu bulmak için kullanılan Dork sorgusu


def _adapt_dork_for_ddg_live(query: str) -> str:
    """Üretilen Dork sorgusunu canlı arama motoru için optimize eder."""
    # 1. '-site:...' operatörlerini ve çift tırnakları temizle
    without_sites = re.sub(r"-site:\S+", "", query, flags=re.IGNORECASE)
    cleaned = without_sites.replace('"', " ")
    cleaned = re.sub(r"\bkariyer\b", "", cleaned, flags=re.IGNORECASE)
    cleaned = " ".join(cleaned.split()).strip()

    if not cleaned:
        return query.strip()

    lowered = cleaned.lower()
    # 'ROS 2' aramalarında İngilizce dokümantasyon (ros.org) ve oyun (Rules of Survival)
    # siteleri yerine Türkiye'deki otonom mobil robot (AMR) / ROS şirketlerini hedefle
    if "ros 2" in lowered or "ros2" in lowered or lowered.startswith("ros "):
        return "ROS 2 otonom mobil robot teknoloji şirketi Türkiye"

    if not any(term in lowered for term in _SECTOR_ANCHOR_TERMS):
        cleaned = f"{cleaned} otonom robotik teknoloji şirketi"

    return cleaned


def _is_relevant_robotics_serp(items: Sequence[dict[str, Any]]) -> bool:
    """Dönen arama sonuçlarının gerçek robotik/otonom sistem sonuçları olup olmadığını
    (anti-bot SERP zehirlemesi veya sadece İngilizce ros.org dokümantasyonu olmadığını) doğrular.
    """
    if not items:
        return False

    relevant_hits = 0
    for item in items:
        text_blob = f"{item.get('title', '')} {item.get('body', '')}".lower()
        href = str(item.get("href", "")).lower()
        # ros.org, ros.ie, roslegacy.net gibi küresel gürültüleri pozitif sinyal sayma
        if any(bad in href for bad in ("ros.org", "ros.ie", "roslegacy", "rosinfinity", "rosorigin", "github.com/ros2")):
            continue
        if any(tok in text_blob for tok in _RELEVANCE_CHECK_TOKENS):
            relevant_hits += 1

    return relevant_hits >= 2


def _fetch_yahoo_tr_via_playwright(live_query: str, max_results: int) -> list[dict[str, Any]]:
    """DDGS/Bing anti-bot engeline veya SERP zehirlemesine takıldığında gerçek Playwright
    Chromium tarayıcısıyla `tr.search.yahoo.com` üzerinden temiz sonuçları çeker.
    """
    import asyncio
    from playwright.async_api import async_playwright

    async def _run_browser_search() -> list[dict[str, Any]]:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            context = await browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
                )
            )
            page = await context.new_page()
            try:
                search_url = f"https://tr.search.yahoo.com/search?p={quote_plus(live_query)}"
                await page.goto(search_url, timeout=15000, wait_until="domcontentloaded")
                html_content = await page.content()
            finally:
                try:
                    await page.close()
                    await context.close()
                finally:
                    await browser.close()

        soup = BeautifulSoup(html_content, "html.parser")
        extracted: list[dict[str, Any]] = []
        for div in soup.select("div.algo"):
            anchor = div.find("a", href=True)
            h3 = div.find("h3")
            comp_text = div.select_one(".compText")
            if not anchor or not h3:
                continue

            raw_href = str(anchor.get("href") or "")
            # Yahoo yönlendirme linki (/RU=https%3A%2F%2F.../RK=...) varsa gerçek hedef URL'yi çöz
            ru_match = re.search(r"/RU=([^/]+)/RK=", raw_href)
            clean_href = unquote(ru_match.group(1)) if ru_match else raw_href

            # Başlıktaki Yahoo breadcrumb önekini ('sirket.com › tr Başlık') temizle
            raw_title = h3.get_text(" ", strip=True)
            span_crumb = h3.find("span")
            if span_crumb:
                crumb_txt = span_crumb.get_text(" ", strip=True)
                if crumb_txt and raw_title.startswith(crumb_txt):
                    raw_title = raw_title[len(crumb_txt):].strip()

            extracted.append(
                {
                    "title": raw_title,
                    "href": clean_href,
                    "body": comp_text.get_text(" ", strip=True) if comp_text else "",
                }
            )
            if len(extracted) >= max_results:
                break
        return extracted

    # Halihazırda çalışan bir asyncio döngüsü içindeysek (AutonomousPipeline.run_async),
    # ayrı bir iş parçacığında (thread) yeni olay döngüsüyle çalıştır.
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(asyncio.run, _run_browser_search())
        return future.result(timeout=25)


def _default_ddg_search_backend(query: str, max_results: int) -> list[dict[str, Any]]:
    """Gerçek çalışma zamanında önce hızlı `DDGS` istemcisini dener; eğer anti-bot SERP
    zehirlemesi veya rate-limit tespit edilirse gerçek Playwright Chromium tarayıcısıyla
    yedek arama motorundan (`tr.search.yahoo.com`) doğrulanmış sonuçları çeker.
    """
    live_query = _adapt_dork_for_ddg_live(query)

    # 1. Aşama: Hızlı DDGS araması (max_results=None ile yalnızca 1. sayfa çekilerek
    #    Bing'in 2. sayfa 'FORM=PERE' anti-bot zehirlemesi önlenir)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        try:
            from duckduckgo_search import DDGS

            with DDGS() as ddgs:
                raw_results = ddgs.text(
                    live_query,
                    region="tr-tr",
                    max_results=None,
                )
                if raw_results and _is_relevant_robotics_serp(raw_results):
                    return list(raw_results)[: max(max_results, 10)]
        except Exception as exc:
            logger.debug("DDGS birincil arama denemesi başarısız (%s): %s", live_query, exc)

    # 2. Aşama: DDGS alakasız/zehirlenmiş SERP döndürdüyse veya rate-limit yediyse
    #    Playwright Chromium ile doğrulanmış arama sonuçlarını çek
    logger.info(
        "DDGS sonuçları sektörel doğrulamadan geçemedi, Playwright tarayıcı araması devreye giriyor: '%s'",
        live_query,
    )
    try:
        pw_results = _fetch_yahoo_tr_via_playwright(live_query, max_results=max(max_results, 10))
        if pw_results:
            return pw_results
    except Exception as exc:
        logger.warning("Playwright yedek arama motoru hatası (%s): %s", live_query, exc)

    return []


class SearchEngineClient:
    """DuckDuckGo tabanlı, hata toleranslı ve mocklanabilir arama motoru istemcisi."""

    def __init__(
        self,
        max_results: int = 10,
        search_backend: Callable[[str, int], Sequence[dict[str, Any]]] | None = None,
    ) -> None:
        """Arama motoru istemcisini başlatır.

        Args:
            max_results: Sorgu başına çekilecek maksimum sonuç sayısı (1 ile 100 arası).
            search_backend: Testlerde dış bağımlılığı izole etmek (mocklamak) için kullanılan
                özel arama fonksiyonu. `None` ise gerçek DuckDuckGo (`DDGS`) kullanılır.
        """
        if max_results <= 0:
            raise ValueError(f"max_results pozitif bir tam sayı olmalıdır, alınan: {max_results}")

        self.max_results = max_results
        self._backend = search_backend or _default_ddg_search_backend

    def search(self, query: str) -> list[SearchResultItem]:
        """Tek bir Dork sorgusunu arama motorunda çalıştırır ve temiz sonuç listesi döndürür.

        Hata Yönetimi (Faz 5 Kabul Kriteri):
        - Ağ kopması (`ConnectionError`, `TimeoutError`),
        - DuckDuckGo rate-limit (`HTTP 429 / 403 / RatelimitException`) veya
        - Beklenmeyen herhangi bir API hatasında
        sistem ÇÖKMEZ; `logger.warning` ile durumu kaydedip `[]` döndürür.

        Args:
            query: Çalıştırılacak arama sorgusu.

        Returns:
            Doğrulanmış ve tekilleştirilmiş `SearchResultItem` listesi.
        """
        if not isinstance(query, str) or not query.strip():
            logger.warning("Boş arama sorgusu verildi, arama motoruna istek atılmadı.")
            return []

        clean_query = query.strip()

        try:
            logger.info("Arama motoru sorgusu çalıştırılıyor: %s", clean_query)
            raw_items = self._backend(clean_query, self.max_results)
        except Exception as exc:
            # Rate-limit, ağ kopması veya zaman aşımında sistemi kırma; uyarı logla ve boş dön
            logger.warning(
                "Arama motoru sorgusu sırasında hata/rate-limit oluştu (sorgu='%s'): %s",
                clean_query,
                exc,
            )
            return []

        if not raw_items:
            logger.warning("Arama motoru sorgusu için sonuç bulunamadı: %s", clean_query)
            return []

        parsed_results: list[SearchResultItem] = []
        seen_urls: set[str] = set()

        for item in raw_items:
            if not isinstance(item, dict):
                continue

            # DuckDuckGo sonuçlarında URL 'href' veya 'link' anahtarında gelebilir
            raw_url = str(item.get("href") or item.get("link") or item.get("url") or "").strip()
            if not raw_url:
                continue

            # Sadece gerçek HTTP / HTTPS web sayfalarını kabul et (javascript:, mailto: vb. ele)
            if not (raw_url.startswith("http://") or raw_url.startswith("https://")):
                continue

            # Aynı URL'nin mükerrer eklenmesini önle
            normalized_url_key = raw_url.rstrip("/").lower()
            if normalized_url_key in seen_urls:
                continue
            seen_urls.add(normalized_url_key)

            title = str(item.get("title") or "").strip()
            snippet = str(item.get("body") or item.get("snippet") or "").strip()

            parsed_results.append(
                SearchResultItem(
                    title=title,
                    url=raw_url,
                    snippet=snippet,
                    query=clean_query,
                )
            )

        logger.info(
            "Arama tamamlandı: '%s' sorgusu için %d geçerli sonuç alındı.",
            clean_query,
            len(parsed_results),
        )
        return parsed_results

    def search_batch(self, queries: Sequence[str]) -> list[SearchResultItem]:
        """Birden fazla Dork sorgusunu sırayla çalıştırır ve tüm sonuçları URL bazında
        tekilleştirerek birleştirir.

        Eğer 1. sorguda bulunan bir şirket sitesi 3. sorguda da çıkarsa, listeye yalnızca
        bir kez eklenir.
        """
        combined: list[SearchResultItem] = []
        seen_global_urls: set[str] = set()

        for q in queries:
            items = self.search(q)
            for item in items:
                norm_key = item.url.rstrip("/").lower()
                if norm_key not in seen_global_urls:
                    seen_global_urls.add(norm_key)
                    combined.append(item)

        return combined

