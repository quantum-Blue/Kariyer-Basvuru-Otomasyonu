"""Playwright Headless Sayfa Çekici, HTML Temizleyici ve Dil Tespit Modülü (src/scraper/page_fetcher.py).

Bu modülün amacı:
1. Faz 6'daki Anti-Aggregator filtresinden başarıyla geçen gerçek şirket sitelerinin
   ana sayfasını ve önemli alt sayfalarını (`/kariyer`, `/careers`, `/hakkimizda`,
   `/about`, `/iletisim`, `/contact`) JavaScript render desteğiyle çekmek.
2. **Zombie Process (Hayalet Süreç) Önleme Garantisi (Faz 7 Kritik Kabul Kriteri):**
   Gerçek dünyada bazı şirket siteleri zaman aşımına (`TimeoutError`) uğrayabilir, bozuk SSL
   sertifikası barındırabilir, `HTTP 404 / 500` döndürebilir veya sonsuz yönlendirme döngüsüne
   (`ERR_TOO_MANY_REDIRECTS`) girebilir. Bu modülde açılan her sekme (`page`) ve her tarayıcı
   bağlamı (`context` / `browser`) `try...finally` blokları içinde yönetilir; böylece ne tür bir
   ağ hatası patlarsa patlasın RAM'de asılı kalan (zombie) Chromium süreci oluşmaz!
3. Çekilen ham HTML'den `<script>`, `<style>`, `<noscript>` gürültülerini temizleyip
   görünür şirket metnini, `mailto:` linklerini ve ATS yönlendirme linklerini çıkarmak.
4. Şirketin web sitesi dilini (`TR` veya `EN`) tespit ederek Faz 10'daki çift dilli şablon
   seçimine girdi sağlamaktır.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
import re
from typing import Any
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from src.rag.chunker import detect_language
from src.scraper.rate_limiter import RateLimiter
from src.utils.logger import get_logger

logger = get_logger(__name__)

# Şirket ana sayfasına girildiğinde taranacak varsayılan alt sayfa yolları
DEFAULT_TARGET_SUBPAGES: tuple[str, ...] = (
    "/kariyer",
    "/careers",
    "/career",
    "/hakkimizda",
    "/about",
    "/about-us",
    "/iletisim",
    "/contact",
)


@dataclass(frozen=True)
class FetchedPage:
    """Tek bir URL'den çekilen sayfanın sonucunu temsil eden veri modeli."""

    url: str
    status_code: int
    title: str
    raw_html: str
    clean_text: str
    links: list[str] = field(default_factory=list)
    ok: bool = True
    error: str = ""


@dataclass(frozen=True)
class CompanyScrapeResult:
    """Bir şirketin ana sayfası ve alt sayfalarının birleştirilmiş kazıma sonucu."""

    base_url: str
    company_name: str
    language: str                                    # 'TR' veya 'EN'
    combined_text: str                               # Hakkımızda + Kariyer + Ana sayfa temiz metni
    combined_html: str                               # E-posta kazıyıcı (Faz 8) için birleşik HTML
    all_links: list[str] = field(default_factory=list)  # ATS tespiti (Faz 9) ve mailto: için tüm linkler
    pages: list[FetchedPage] = field(default_factory=list)


def clean_html_to_text_and_links(raw_html: str, base_url: str = "") -> tuple[str, list[str]]:
    """Ham HTML içerisinden görünmeyen kodları (`<script>`, `<style>` vb.) siler;
    temiz metni ve sayfadaki tüm bağlantıları (`href`) çıkarır.

    Args:
        raw_html: Sayfadan alınan ham HTML kaynak kodu.
        base_url: Göreli linkleri (`/kariyer`) tam URL'ye (`https://sirket.com/kariyer`)
            dönüştürmek için kullanılan kök adres.

    Returns:
        `(temiz_metin, benzersiz_link_listesi)` ikilisi (tuple).
    """
    if not raw_html or not raw_html.strip():
        return "", []

    soup = BeautifulSoup(raw_html, "html.parser")

    # 1. Önce sayfadaki tüm <a href="..."> bağlantılarını topla
    extracted_links: list[str] = []
    seen_links: set[str] = set()

    for anchor in soup.find_all("a", href=True):
        href = str(anchor.get("href") or "").strip()
        if not href or href.startswith(("#", "javascript:")):
            continue

        # Eğer 'mailto:' ise olduğu gibi koru; göreli yol ise ('/kariyer') base_url ile birleştir
        if href.lower().startswith("mailto:"):
            resolved = href
        elif base_url:
            resolved = urljoin(base_url.rstrip("/") + "/", href)
        else:
            resolved = href

        if resolved not in seen_links:
            seen_links.add(resolved)
            extracted_links.append(resolved)

    # 2. Görünür metni kirleten <script>, <style>, <noscript>, <svg> etiketlerini DOM'dan sök
    for noisy_tag in soup(["script", "style", "noscript", "svg", "iframe", "meta", "link"]):
        noisy_tag.decompose()

    # 3. Kalan görünür metni çıkar ve fazla boşlukları standartlaştır
    raw_text = soup.get_text(separator="\n")
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in raw_text.splitlines()]
    non_empty_lines = [line for line in lines if line]
    clean_text = "\n".join(non_empty_lines)

    return clean_text, extracted_links


def _normalize_base_origin(url: str) -> str:
    """Verilen bir URL'den kök şema + domain adresini (`https://sirket.com`) döndürür."""
    target = url.strip()
    if "://" not in target:
        target = "https://" + target
    parsed = urlparse(target)
    return f"{parsed.scheme}://{parsed.netloc}"


class PlaywrightPageFetcher:
    """Playwright tabanlı, hız sınırlayıcılı ve zombie process korumalı asenkron sayfa çekici."""

    def __init__(
        self,
        timeout_ms: int = 20000,
        rate_limiter: RateLimiter | None = None,
        target_subpages: Sequence[str] | None = None,
        user_agent: str | None = None,
    ) -> None:
        if timeout_ms <= 0:
            raise ValueError(f"timeout_ms pozitif olmalıdır, alınan: {timeout_ms}")

        self.timeout_ms = timeout_ms
        self.rate_limiter = rate_limiter or RateLimiter()
        self.target_subpages = list(
            target_subpages if target_subpages is not None else DEFAULT_TARGET_SUBPAGES
        )
        self.user_agent = user_agent or (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        )

    async def fetch_single_page(
        self,
        url: str,
        browser_context: Any,
    ) -> FetchedPage:
        """Verilen tarayıcı bağlamı (`browser_context`) üzerinde yeni bir sekme açar,
        sayfayı çeker ve hata oluşsa dahi sekmeyi `finally` bloğunda KESİN OLARAK kapatır.

        Args:
            url: Çekilecek sayfa URL'si.
            browser_context: Playwright `BrowserContext` (veya testlerdeki mock context) nesnesi.

        Returns:
            Başarı veya hata bilgisini taşıyan `FetchedPage` nesnesi.
        """
        await self.rate_limiter.wait()

        page = None
        try:
            page = await browser_context.new_page()
            response = await page.goto(
                url,
                timeout=self.timeout_ms,
                wait_until="domcontentloaded",
            )

            status_code = int(getattr(response, "status", 200)) if response is not None else 200
            if status_code >= 400:
                logger.debug("Sayfa HTTP %d döndürdü, atlanıyor: %s", status_code, url)
                return FetchedPage(
                    url=url,
                    status_code=status_code,
                    title="",
                    raw_html="",
                    clean_text="",
                    links=[],
                    ok=False,
                    error=f"HTTP {status_code}",
                )

            raw_html = await page.content()
            title = (await page.title()).strip() if hasattr(page, "title") else ""
            clean_text, links = clean_html_to_text_and_links(raw_html, base_url=url)

            return FetchedPage(
                url=url,
                status_code=status_code,
                title=title,
                raw_html=raw_html,
                clean_text=clean_text,
                links=links,
                ok=True,
            )
        except Exception as exc:
            # Timeout, SSL hatası (ERR_CERT_*) veya sonsuz yönlendirme (ERR_TOO_MANY_REDIRECTS)
            # durumlarında sistemi çökertme; hatayı kaydet ve ok=False döndür
            logger.warning("Sayfa çekilirken hata oluştu (%s): %s", url, exc)
            return FetchedPage(
                url=url,
                status_code=0,
                title="",
                raw_html="",
                clean_text="",
                links=[],
                ok=False,
                error=str(exc),
            )
        finally:
            # ZOMBIE PROCESS KORUMASI: Sekme (page) ne olursa olsun kapatılır!
            if page is not None:
                try:
                    await page.close()
                except Exception:
                    pass

    async def fetch_company_site(
        self,
        entry_url: str,
        browser_context: Any | None = None,
    ) -> CompanyScrapeResult:
        """Bir şirketin giriş URL'sini ve sayfa içinde keşfedilen `/kariyer`, `/hakkimizda`,
        `/iletisim` alt sayfalarını çekerek tek bir `CompanyScrapeResult` altında birleştirir.

        Nasıl Çalışır?
        1. Eğer dışarıdan bir `browser_context` verilmemişse gerçek Playwright Chromium headless
           tarayıcısını açar ve fonksiyon bittiğinde `finally` bloğunda tarayıcıyı tamamen kapatır.
        2. Önce giriş sayfasını (`entry_url`) çeker.
        3. Sayfa içindeki linklerden veya standart `target_subpages` listesinden şirketin
           kariyer/hakkımızda/iletişim alt sayfalarını ziyaret eder.
        4. Toplanan tüm görünür metinlerden şirketin dilini (`TR` veya `EN`) tespit eder.
        """
        if browser_context is not None:
            return await self._scrape_with_context(entry_url, browser_context)

        # Gerçek Playwright tarayıcısını güvenli bağlam (context manager + finally) içinde çalıştır
        from playwright.async_api import async_playwright

        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            context = await browser.new_context(
                user_agent=self.user_agent,
                ignore_https_errors=False,
            )
            try:
                return await self._scrape_with_context(entry_url, context)
            finally:
                try:
                    await context.close()
                finally:
                    await browser.close()

    async def _scrape_with_context(
        self,
        entry_url: str,
        context: Any,
    ) -> CompanyScrapeResult:
        """Verilen tarayıcı bağlamını kullanarak ana sayfa ve alt sayfaları gezer."""
        origin = _normalize_base_origin(entry_url)
        visited_urls: set[str] = set()
        successful_pages: list[FetchedPage] = []

        # 1. Giriş sayfasını çek
        clean_entry = entry_url.rstrip("/")
        visited_urls.add(clean_entry.lower())
        main_page = await self.fetch_single_page(clean_entry, context)
        if main_page.ok:
            successful_pages.append(main_page)

        # 2. Alt sayfaları (/kariyer, /hakkimizda vb.) oluştur ve henüz ziyaret edilmemiş olanları çek
        for sub_path in self.target_subpages:
            sub_url = f"{origin.rstrip('/')}/{sub_path.lstrip('/')}".rstrip("/")
            if sub_url.lower() in visited_urls:
                continue
            visited_urls.add(sub_url.lower())

            sub_page = await self.fetch_single_page(sub_url, context)
            if sub_page.ok and sub_page.clean_text:
                successful_pages.append(sub_page)

        # 3. Çekilen tüm sayfaların metinlerini, HTML'lerini ve linklerini birleştir
        combined_text = "\n\n".join(p.clean_text for p in successful_pages if p.clean_text).strip()
        combined_html = "\n".join(p.raw_html for p in successful_pages if p.raw_html)

        all_links: list[str] = []
        seen_links: set[str] = set()
        for p in successful_pages:
            for link in p.links:
                if link not in seen_links:
                    seen_links.add(link)
                    all_links.append(link)

        # Şirket adını ilk başarılı sayfanın başlığından veya alan adından çıkar
        company_name = (
            successful_pages[0].title.split("|")[0].split("-")[0].strip()
            if successful_pages and successful_pages[0].title
            else urlparse(origin).netloc
        )

        # Şirketin dilini ('TR' veya 'EN') birleşik metin üzerinden tespit et
        detected_lang = detect_language(combined_text) if combined_text else "EN"

        return CompanyScrapeResult(
            base_url=origin,
            company_name=company_name,
            language=detected_lang,
            combined_text=combined_text,
            combined_html=combined_html,
            all_links=all_links,
            pages=successful_pages,
        )
