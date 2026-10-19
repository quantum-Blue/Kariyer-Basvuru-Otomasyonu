"""Faz 5 TDD Birim ve Hata Avcılığı (Bug Hunting) Testleri:
Dork Tabanlı Arama Sorgusu Üretici ve Dayanıklı (Resilient) Arama Motoru İstemcisi
(src/scraper/query_builder.py & src/scraper/search_engine.py).
"""

import logging
from typing import Any
from unittest.mock import MagicMock
import pytest

from src.scraper.query_builder import (
    QueryBuildError,
    build_dork_queries,
    build_single_dork_query,
)
from src.scraper.search_engine import (
    SearchEngineClient,
    SearchResultItem,
)


class TestDorkQueryBuilder:
    """Dork tabanlı arama sorgusu üretici (query_builder.py) birim ve sınır değer testleri."""

    def test_build_single_dork_query_includes_keywords_and_negative_site_operators(self) -> None:
        """Üretilen sorguda uzmanlık kelimesi, kariyer/Ar-Ge niyeti ve -site: dışlama operatörleri bulunmalı."""
        query = build_single_dork_query(
            keyword="ROS 2",
            location="Türkiye",
            intent="kariyer",
            excluded_domains=["linkedin.com", "clutch.co", "f6s.com", "kariyer.net"],
        )

        # Anahtar kelime çift tırnak içinde tam eşleşme olarak yer almalı
        assert '"ROS 2"' in query
        assert "Türkiye" in query
        assert "kariyer" in query
        # İlk prototipteki rehber siteleri tuzağını önleyen negatif Dork operatörleri
        assert "-site:linkedin.com" in query
        assert "-site:clutch.co" in query
        assert "-site:f6s.com" in query
        assert "-site:kariyer.net" in query

    def test_build_dork_queries_generates_deduplicated_queries_for_multiple_keywords(self) -> None:
        """Birden fazla anahtar kelime verildiğinde mükerrer olmayan temiz Dork sorgu listesi üretilmeli."""
        keywords = ["SLAM", "Otonom İHA", "SLAM", "  Bilgisayarlı Görü  "]
        queries = build_dork_queries(
            keywords=keywords,
            location="İstanbul",
            excluded_domains=["clutch.co", "europages.com"],
        )

        # 'SLAM' iki kez verildiği için tekilleştirilmeli -> toplam 3 benzersiz sorgu
        assert len(queries) == 3
        assert any('"SLAM"' in q for q in queries)
        assert any('"Otonom İHA"' in q for q in queries)
        assert any('"Bilgisayarlı Görü"' in q for q in queries)
        assert all("-site:clutch.co" in q and "-site:europages.com" in q for q in queries)

    def test_empty_or_invalid_keywords_raise_query_build_error(self) -> None:
        """Boş anahtar kelime veya sadece boşluk içeren girdi verildiğinde QueryBuildError fırlatılmalı."""
        with pytest.raises(QueryBuildError, match="keyword"):
            build_single_dork_query(keyword="   ")

        with pytest.raises(QueryBuildError, match="keywords"):
            build_dork_queries(keywords=[])

        with pytest.raises(QueryBuildError, match="keywords"):
            build_dork_queries(keywords=["", "   "])

    def test_domain_sanitization_strips_protocols_and_paths_in_dork_operators(self) -> None:
        """Kara listedeki domain 'https://www.clutch.co/profile' şeklinde gelse bile '-site:clutch.co' olarak temizlenmeli."""
        query = build_single_dork_query(
            keyword="Gömülü Sistemler",
            excluded_domains=["https://www.clutch.co/tr/robotics", "  HTTP://F6S.COM/  "],
        )

        assert "-site:clutch.co" in query
        assert "-site:f6s.com" in query
        assert "https://" not in query


class TestResilientSearchEngineClient:
    """Dayanıklı (Resilient) DuckDuckGo arama motoru istemcisi (search_engine.py) testleri.
    Not: AGENT_RULES Madde 4.1.2 gereği gerçek internete çıkılmaz, backend mocklanır.
    """

    def test_search_returns_parsed_and_deduplicated_results(self) -> None:
        """Geçerli arama sonuçları SearchResultItem listesine dönüştürülmeli ve mükerrer URL'ler elenmeli."""
        raw_ddg_results: list[dict[str, Any]] = [
            {
                "title": "Baykar Teknoloji - Kariyer ve Otonom Sistemler",
                "href": "https://baykartech.com/tr/kariyer/",
                "body": "İnsansız hava araçları, ROS 2 ve otonom uçuş algoritmaları.",
            },
            # Aynı URL tekrar gelirse (mükerrer sonuç) elenmeli
            {
                "title": "Baykar Teknoloji Duplicate",
                "href": "https://baykartech.com/tr/kariyer/",
                "body": "Duplicate",
            },
            {
                "title": "Altınay Robotik | Ar-Ge Mühendisliği",
                "href": "https://www.altinay.com/hakkimizda",
                "body": "Endüstriyel robotik ve hareket kontrol sistemleri.",
            },
        ]

        mock_backend = MagicMock(return_value=raw_ddg_results)
        client = SearchEngineClient(max_results=10, search_backend=mock_backend)

        results = client.search('"ROS 2" "kariyer" -site:clutch.co')

        assert len(results) == 2
        assert all(isinstance(item, SearchResultItem) for item in results)
        assert results[0].title == "Baykar Teknoloji - Kariyer ve Otonom Sistemler"
        assert results[0].url == "https://baykartech.com/tr/kariyer/"
        assert "ROS 2" in results[0].snippet
        assert results[1].url == "https://www.altinay.com/hakkimizda"

    @pytest.mark.parametrize(
        "simulated_exception",
        [
            RuntimeError("HTTP 429 Too Many Requests - RatelimitException"),
            RuntimeError("HTTP 403 Forbidden"),
            ConnectionError("Network unreachable / DNS lookup failed"),
            TimeoutError("Read timed out after 20 seconds"),
        ],
    )
    def test_network_or_rate_limit_error_does_not_crash_returns_empty_list_and_logs_warning(
        self,
        simulated_exception: Exception,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Ağ kopması, zaman aşımı veya DuckDuckGo rate-limit (HTTP 429/403) durumunda
        sistem ASLA çökmemeli; uyarı (WARNING) loglayıp boş liste ([]) dönmeli.
        """
        def failing_backend(_query: str, _max_results: int) -> list[dict[str, Any]]:
            raise simulated_exception

        client = SearchEngineClient(max_results=5, search_backend=failing_backend)

        with caplog.at_level(logging.WARNING):
            results = client.search('"SLAM" "otonom"')

        assert results == []
        assert any("Arama motoru" in record.message or "hata" in record.message.lower() for record in caplog.records)

    def test_empty_or_malformed_search_results_handled_gracefully(self) -> None:
        """Arama motoru None, boş liste veya URL'siz bozuk kayıtlar döndürdüğünde sistem çökmemeli."""
        malformed_results: list[dict[str, Any]] = [
            {"title": "URL olmayan bozuk kayıt", "body": "test"},
            {"title": "Geçersiz protokol", "href": "javascript:void(0)", "body": "test"},
            {"title": "Geçerli Şirket", "href": "https://sirket.com.tr", "body": "Otonom"},
        ]

        client_empty = SearchEngineClient(search_backend=lambda _q, _m: [])
        assert client_empty.search("ROS 2") == []

        client_malformed = SearchEngineClient(search_backend=lambda _q, _m: malformed_results)
        valid_items = client_malformed.search("ROS 2")
        assert len(valid_items) == 1
        assert valid_items[0].url == "https://sirket.com.tr"

    def test_empty_query_string_returns_empty_list_without_calling_backend(self) -> None:
        """Boş sorgu verildiğinde arama motoruna gereksiz istek atılmadan boş liste dönmeli."""
        mock_backend = MagicMock(return_value=[])
        client = SearchEngineClient(search_backend=mock_backend)

        assert client.search("   ") == []
        assert mock_backend.call_count == 0

    def test_search_batch_deduplicates_urls_across_multiple_queries_and_isolates_partial_failure(
        self,
    ) -> None:
        """search_batch birden fazla sorguda çıkan aynı şirketi tekilleştirmeli;
        sorgulardan biri rate-limit yese bile diğer sorgunun sonuçlarını kaybetmemeli.
        """
        call_idx = 0

        def flaky_backend(query: str, _max_results: int) -> list[dict[str, Any]]:
            nonlocal call_idx
            call_idx += 1
            if "SLAM" in query:
                raise RuntimeError("HTTP 429 Ratelimit on second query")
            return [
                {"title": "Aselsan", "href": "https://www.aselsan.com/tr/kariyer", "body": "Otonom"},
                {"title": "Havelsan", "href": "https://www.havelsan.com.tr/", "body": "Simülasyon"},
            ]

        client = SearchEngineClient(max_results=5, search_backend=flaky_backend)
        results = client.search_batch(['"ROS 2"', '"SLAM"', '"Otonom İHA"'])

        # 1. ve 3. sorgu aynı 2 şirketi döndürdü, 2. sorgu 429 hatası verdi -> toplam 2 benzersiz sonuç dönmeli
        assert len(results) == 2
        assert {r.url for r in results} == {
            "https://www.aselsan.com/tr/kariyer",
            "https://www.havelsan.com.tr/",
        }

    def test_invalid_max_results_raises_value_error(self) -> None:
        """Sıfır veya negatif max_results verildiğinde ValueError fırlatılmalı."""
        with pytest.raises(ValueError, match="max_results"):
            SearchEngineClient(max_results=0)

