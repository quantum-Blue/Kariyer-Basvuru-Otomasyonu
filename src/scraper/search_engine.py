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
from dataclasses import dataclass
from typing import Any

from src.utils.logger import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True)
class SearchResultItem:
    """Arama motorundan dönen tek bir web sayfası adayını temsil eden veri modeli."""

    title: str        # Arama sonucunun sayfa başlığı (Örn: "Baykar Teknoloji - Kariyer")
    url: str          # Sayfanın tam HTTP/HTTPS adresi
    snippet: str      # Arama motorunun gösterdiği kısa özet metin (body/snippet)
    query: str = ""   # Bu sonucu bulmak için kullanılan Dork sorgusu


def _default_ddg_search_backend(query: str, max_results: int) -> list[dict[str, Any]]:
    """Gerçek çalışma zamanında `duckduckgo_search.DDGS` kütüphanesini çağırır."""
    from duckduckgo_search import DDGS

    with DDGS() as ddgs:
        raw_results = ddgs.text(query, max_results=max_results)
        return list(raw_results) if raw_results else []


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
