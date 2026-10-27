"""Faz 7 TDD Birim ve Hata Avcılığı (Bug Hunting) Testleri:
Playwright Headless Sayfa Çekici, HTML Temizleyici, Dil Tespit Edici ve Hız Sınırlayıcı
(src/scraper/rate_limiter.py & src/scraper/page_fetcher.py).

Bu test dosyası şunları doğrular:
1. RateLimiter'ın min/max sınırlarını doğrulamasını ve ardışık istekler arasında
   insansı (jitter'lı) bekleme uygulamasını.
2. HTML içerisindeki `<script>`, `<style>`, `<noscript>` gibi gürültülerin temizlenip
   saf metnin, `mailto:` bağlantılarının ve dış linklerin (ATS tespiti için) çıkarılmasını.
3. Şirket ana sayfası ile `/kariyer`, `/hakkimizda`, `/iletisim` alt sayfalarının çekilip
   şirket dilinin (`TR` veya `EN`) doğru tespit edilmesini.
4. **Zombie Process Koruması (Faz 7 Kritik Kabul Kriteri):**
   Timeout (zaman aşımı), SSL sertifika hatası, HTTP 404/500 veya sonsuz yönlendirme
   (ERR_TOO_MANY_REDIRECTS) durumlarında tarayıcı sekmesinin (`page.close()`) ve
   bağlamının (`context.close()`, `browser.close()`) her koşulda (`finally`) kapatılmasını!
"""

import asyncio
from typing import Any
import pytest

from src.scraper.page_fetcher import (
    CompanyScrapeResult,
    FetchedPage,
    PlaywrightPageFetcher,
    clean_html_to_text_and_links,
)
from src.scraper.rate_limiter import RateLimiter


class FakeAsyncPage:
    """Playwright `Page` nesnesini simüle eden ve kapatılıp kapatılmadığını (`closed`)
    takip eden sahte (mock) sınıf. Zombie process kontrolü için kullanılır.
    """

    def __init__(
        self,
        url_map: dict[str, tuple[int, str]],
        error_urls: dict[str, Exception] | None = None,
    ) -> None:
        self.url_map = url_map
        self.error_urls = error_urls or {}
        self.current_url = ""
        self.closed = False

    async def goto(self, url: str, timeout: int = 20000, wait_until: str = "domcontentloaded") -> Any:
        self.current_url = url
        # Eğer bu URL için simüle edilmiş bir hata (Timeout, SSL, Redirect Loop) varsa fırlat
        if url in self.error_urls:
            raise self.error_urls[url]

        status, _ = self.url_map.get(url, (404, "<html><body>Not Found</body></html>"))

        class FakeResponse:
            def __init__(self, status_code: int) -> None:
                self.status = status_code

        return FakeResponse(status)

    async def content(self) -> str:
        _, html = self.url_map.get(self.current_url, (404, ""))
        return html

    async def title(self) -> str:
        _, html = self.url_map.get(self.current_url, (404, ""))
        if "<title>" in html and "</title>" in html:
            start = html.index("<title>") + 7
            end = html.index("</title>")
            return html[start:end]
        return ""

    async def close(self) -> None:
        self.closed = True


class FakeAsyncBrowserContext:
    """Playwright `BrowserContext` nesnesini simüle eden ve açık kalan sekmeleri izleyen sınıf."""

    def __init__(
        self,
        url_map: dict[str, tuple[int, str]],
        error_urls: dict[str, Exception] | None = None,
    ) -> None:
        self.url_map = url_map
        self.error_urls = error_urls or {}
        self.created_pages: list[FakeAsyncPage] = []
        self.closed = False

    async def new_page(self) -> FakeAsyncPage:
        page = FakeAsyncPage(self.url_map, self.error_urls)
        self.created_pages.append(page)
        return page

    async def close(self) -> None:
        self.closed = True


class TestRateLimiter:
    """Hız sınırlayıcı (RateLimiter) birim ve sınır değer testleri."""

    def test_rate_limiter_waits_within_configured_bounds(self) -> None:
        """RateLimiter ardışık çağrılarda min_sec ile max_sec arasında rastgele süre beklemeli."""
        recorded_sleeps: list[float] = []

        async def fake_sleep(seconds: float) -> None:
            recorded_sleeps.append(seconds)

        limiter = RateLimiter(min_sec=1.5, max_sec=3.5, sleep_func=fake_sleep)

        # İlk istekte bekleme yapılmaz; ikinci ve üçüncü isteklerde bekleme uygulanır
        asyncio.run(limiter.wait())
        asyncio.run(limiter.wait())
        asyncio.run(limiter.wait())

        assert len(recorded_sleeps) == 2
        for delay in recorded_sleeps:
            assert 1.5 <= delay <= 3.5

    @pytest.mark.parametrize(
        "min_sec,max_sec",
        [
            (-1.0, 2.0),
            (5.0, 2.0),
            (0.0, 0.0),
        ],
    )
    def test_invalid_rate_limiter_bounds_raise_value_error(
        self, min_sec: float, max_sec: float
    ) -> None:
        """Negatif bekleme süresi veya min_sec > max_sec verildiğinde ValueError fırlatılmalı."""
        with pytest.raises(ValueError):
            RateLimiter(min_sec=min_sec, max_sec=max_sec)


class TestHTMLCleanerAndLanguageDetection:
    """HTML temizleme, link çıkarma ve şirket dili (TR/EN) tespiti testleri."""

    def test_clean_html_strips_scripts_styles_and_extracts_links(self) -> None:
        """HTML içindeki <script> ve <style> kodları silinmeli, görünür metin ve linkler korunmalı."""
        raw_html = """
        <html>
            <head>
                <title>Milvus Robotics - Kariyer</title>
                <style>body { color: red; }</style>
                <script>console.log("hidden secret script");</script>
            </head>
            <body>
                <h1>Otonom Mobil Robotlar (AMR)</h1>
                <p>ROS 2 ve LiDAR SLAM teknolojileri geliştiriyoruz.</p>
                <a href="/hakkimizda">Hakkımızda</a>
                <a href="mailto:kariyer@milvusrobotics.com">İK Başvuru</a>
                <a href="https://boards.greenhouse.io/milvus">ATS Link</a>
            </body>
        </html>
        """

        cleaned_text, links = clean_html_to_text_and_links(
            raw_html, base_url="https://milvusrobotics.com"
        )

        assert "Otonom Mobil Robotlar (AMR)" in cleaned_text
        assert "ROS 2 ve LiDAR SLAM" in cleaned_text
        assert "console.log" not in cleaned_text
        assert "color: red" not in cleaned_text
        assert "https://milvusrobotics.com/hakkimizda" in links
        assert "mailto:kariyer@milvusrobotics.com" in links
        assert "https://boards.greenhouse.io/milvus" in links


class TestPlaywrightPageFetcherAndZombiePrevention:
    """Playwright sayfa çekici, alt sayfa gezgini ve zombie process önleme testleri."""

    def test_fetch_company_pages_combines_home_career_and_about_pages(self) -> None:
        """Ana sayfa, /kariyer ve /hakkimizda sayfaları çekilmeli ve şirket dili 'TR' olarak tespit edilmeli."""
        url_map = {
            "https://milvusrobotics.com": (
                200,
                """
                <html><head><title>Milvus Robotics</title></head>
                <body>
                    <p>Endüstriyel otonom mobil robot (AMR) çözümleri geliştiriyoruz.</p>
                    <a href="/kariyer">Kariyer</a>
                    <a href="/hakkimizda">Hakkımızda</a>
                </body></html>
                """,
            ),
            "https://milvusrobotics.com/kariyer": (
                200,
                """
                <html><head><title>Kariyer | Milvus</title></head>
                <body>
                    <p>ROS 2, C++ ve SLAM mühendisleri arıyoruz. Başvuru: kariyer@milvusrobotics.com</p>
                </body></html>
                """,
            ),
            "https://milvusrobotics.com/hakkimizda": (
                200,
                """
                <html><head><title>Hakkımızda | Milvus</title></head>
                <body>
                    <p>Vizyonumuz karanlık fabrikalar için tam otonom filo yönetim sistemleri kurmaktır.</p>
                </body></html>
                """,
            ),
        }

        fake_context = FakeAsyncBrowserContext(url_map=url_map)
        limiter = RateLimiter(min_sec=0.0, max_sec=0.01, sleep_func=lambda _: asyncio.sleep(0))
        fetcher = PlaywrightPageFetcher(
            timeout_ms=5000,
            rate_limiter=limiter,
            target_subpages=["/kariyer", "/hakkimizda", "/iletisim"],
        )

        result = asyncio.run(
            fetcher.fetch_company_site(
                "https://milvusrobotics.com",
                browser_context=fake_context,
            )
        )

        assert isinstance(result, CompanyScrapeResult)
        assert result.language == "TR"
        assert len(result.pages) == 3  # Ana sayfa + /kariyer + /hakkimizda (/iletisim 404 olduğu için atlandı)
        assert "karanlık fabrikalar" in result.combined_text
        assert "kariyer@milvusrobotics.com" in result.combined_text
        # Zombie process kontrolü: Açılan tüm sayfa sekmeleri (Page) kapatılmış olmalı!
        assert all(p.closed is True for p in fake_context.created_pages)

    @pytest.mark.parametrize(
        "simulated_error",
        [
            TimeoutError("Playwright Navigation Timeout 20000ms exceeded"),
            RuntimeError("net::ERR_CERT_AUTHORITY_INVALID (SSL Certificate Error)"),
            RuntimeError("net::ERR_TOO_MANY_REDIRECTS (Infinite Redirect Loop)"),
            RuntimeError("net::ERR_NAME_NOT_RESOLVED"),
        ],
    )
    def test_timeout_ssl_and_redirect_loop_errors_always_close_page_and_prevent_zombies(
        self, simulated_error: Exception
    ) -> None:
        """Timeout, SSL sertifika hatası veya sonsuz yönlendirme (redirect loop) durumunda:
        1. Sistem asla çökmemeli (FetchedPage.ok == False dönmeli),
        2. Tarayıcı sekmesi (`page.closed`) %100 kapatılmalı (Zombie process önleme).
        """
        target_url = "https://broken-robotics-site.com"
        fake_context = FakeAsyncBrowserContext(
            url_map={},
            error_urls={target_url: simulated_error},
        )
        limiter = RateLimiter(min_sec=0.0, max_sec=0.01, sleep_func=lambda _: asyncio.sleep(0))
        fetcher = PlaywrightPageFetcher(timeout_ms=3000, rate_limiter=limiter)

        page_result = asyncio.run(
            fetcher.fetch_single_page(target_url, browser_context=fake_context)
        )

        assert isinstance(page_result, FetchedPage)
        assert page_result.ok is False
        assert page_result.error != ""
        # Kritik kontrol: Hata fırlatılmış olmasına rağmen sekme açık kalmamalı!
        assert len(fake_context.created_pages) == 1
        assert fake_context.created_pages[0].closed is True

    def test_http_404_and_500_status_codes_mark_page_not_ok_and_close_page(self) -> None:
        """HTTP 404 veya 500 dönen sayfalarda sekme kapatılmalı ve ok=False olarak işaretlenmeli."""
        url_map = {
            "https://error-site.com/500": (500, "<html><body>Internal Server Error</body></html>"),
            "https://error-site.com/404": (404, "<html><body>Not Found</body></html>"),
        }
        fake_context = FakeAsyncBrowserContext(url_map=url_map)
        limiter = RateLimiter(min_sec=0.0, max_sec=0.01, sleep_func=lambda _: asyncio.sleep(0))
        fetcher = PlaywrightPageFetcher(rate_limiter=limiter)

        res_500 = asyncio.run(fetcher.fetch_single_page("https://error-site.com/500", fake_context))
        res_404 = asyncio.run(fetcher.fetch_single_page("https://error-site.com/404", fake_context))

        assert res_500.ok is False
        assert res_500.status_code == 500
        assert res_404.ok is False
        assert res_404.status_code == 404
        assert all(p.closed is True for p in fake_context.created_pages)

    def test_english_company_site_detected_as_en_and_handles_empty_html(self) -> None:
        """Yabancı (İngilizce) bir robotik şirketinin sitesi çekildiğinde dil 'EN' olarak tespit edilmeli."""
        url_map = {
            "https://boston-autonomy-example.io": (
                200,
                """
                <html><head><title>Boston Autonomy - Robotics & AI</title></head>
                <body>
                    <p>We develop autonomous aerial vehicles with ROS 2, stereo vision and LiDAR SLAM.</p>
                </body></html>
                """,
            ),
        }
        fake_context = FakeAsyncBrowserContext(url_map=url_map)
        limiter = RateLimiter(min_sec=0.0, max_sec=0.01, sleep_func=lambda _: asyncio.sleep(0))
        fetcher = PlaywrightPageFetcher(
            rate_limiter=limiter,
            target_subpages=["/careers", "/about"],
        )

        result = asyncio.run(
            fetcher.fetch_company_site("https://boston-autonomy-example.io", browser_context=fake_context)
        )

        assert result.language == "EN"
        assert result.company_name == "Boston Autonomy"
        assert clean_html_to_text_and_links("") == ("", [])

        with pytest.raises(ValueError, match="timeout_ms"):
            PlaywrightPageFetcher(timeout_ms=0)

