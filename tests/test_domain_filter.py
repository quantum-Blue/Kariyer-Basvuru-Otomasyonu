"""Faz 6 TDD Birim ve Hata Avcılığı (Bug Hunting) Testleri:
Domain Kara Listesi ve Rehber Sitesi (Anti-Aggregator) Filtresi (src/scraper/domain_filter.py).

Bu test dosyası, önceki prototipin en büyük hatası olan B2B rehber/dizin sitelerine
(clutch.co, f6s.com, europages, linkedin, kariyer.net vb.) ve "Top 10 Robotics Companies"
tarzı blog/liste sayfalarına karşı geliştirilen çift katmanlı savunma filtresini denetler.
"""

import pytest

from src.scraper.domain_filter import (
    DomainFilter,
    extract_clean_hostname,
    is_aggregator_title,
    is_blacklisted_url,
)
from src.scraper.search_engine import SearchResultItem


class TestAntiAggregatorDomainFilter:
    """Domain kara listesi, alt alan adı (subdomain) yakalama ve false-positive koruma testleri."""

    @pytest.mark.parametrize(
        "blocked_url",
        [
            # 1. Doğrudan kök domain eşleşmeleri
            "https://clutch.co/tr/it-services/robotics",
            "https://f6s.com/companies/robotics/turkey/co",
            "https://kariyer.net/is-ilanlari/robotik",
            "https://glassdoor.com/Reviews/index.htm",
            "https://indeed.com/q-ros2-jobs.html",
            "https://sortlist.com/robotics",
            "https://yellowpages.com/search",
            # 2. Alt alan adı (Subdomain) tuzakları (Faz 6 Kritik Kabul Kriteri)
            "https://www.clutch.co/profile/some-company",
            "https://tr.linkedin.com/company/some-robotics",
            "https://www.linkedin.com/jobs/view/12345",
            "https://de.europages.com/filiale/turkiye.html",
            "https://www.europages.com.tr/sirketler/robotik.html",
            "https://tr.indeed.com/jobs?q=otonom",
            # 3. Büyük/küçük harf, port numarası ve şemasız (http/https olmayan) URL tuzakları
            "HTTPS://WWW.CLUTCH.CO:443/profile/test",
            "http://sub.deep.f6s.com/page",
            "www.kariyer.net/firma-profil",
        ],
    )
    def test_blacklisted_domains_and_subdomains_are_strictly_blocked(
        self, blocked_url: str
    ) -> None:
        """Kara listedeki tüm domainler ve bunların alt alan adları (%100) yakalanıp elenmeli."""
        assert is_blacklisted_url(blocked_url) is True

    @pytest.mark.parametrize(
        "legitimate_company_url",
        [
            # Gerçek Türk ve küresel robotik / savunma / otonom sistem şirketleri
            "https://baykartech.com/tr/kariyer/",
            "https://www.aselsan.com.tr/tr/kariyer",
            "https://www.havelsan.com.tr/kariyer",
            "https://www.altinay.com/hakkimizda",
            "https://milvusrobotics.com/careers",
            "https://www.adastec.com/about-us",
            "https://rovma.com/iletisim",
            # İsim benzerliği tuzağı (Substring False-Positive Koruması):
            # Örneğin 'x.com', 'f6s.com' veya 'clutch.co' kara listede diye,
            # içinde bu harfler geçen gerçek şirket domainleri YANLIŞLIKLA ELENMEMELİ!
            "https://apex.com/careers",             # 'x.com' ile bitiyor ama kök domain 'apex.com'
            "https://rovmax.com.tr/kariyer",        # 'x.com.tr' benzerliği
            "https://myf6srobotics.com/about",      # içinde 'f6s' geçiyor ama farklı domain
            "https://clutchrobotics.com.tr/ik",     # içinde 'clutch' geçiyor ama farklı domain
            "https://indeedautonomy.ai/jobs",       # içinde 'indeed' geçiyor ama farklı domain
        ],
    )
    def test_legitimate_robotics_companies_are_not_falsely_blocked(
        self, legitimate_company_url: str
    ) -> None:
        """Gerçek robotik şirketlerinin siteleri (isim benzerliği olsa bile) yanlışlıkla elenmemeli."""
        assert is_blacklisted_url(legitimate_company_url) is False

    @pytest.mark.parametrize(
        "aggregator_title",
        [
            "Top 10 Robotics Companies in Turkey (2026 Updated)",
            "Top 25 Autonomous Vehicle Startups to Watch",
            "Best Robotics Companies & Suppliers in Istanbul",
            "15 Best Companies for Computer Vision Engineers",
            "Türkiye'deki En İyi 10 Robotik ve Otonom Şirketleri",
            "B2B Şirketleri ve Endüstriyel Otomasyon Firma Rehberi",
            "İstanbul Yazılım ve Robotik Şirket Rehberi",
            "List of Autonomous Drone Companies in Europe",
            "Company Directory: Robotics & Embedded Systems",
        ],
    )
    def test_aggregator_and_listicle_titles_are_detected_and_blocked(
        self, aggregator_title: str
    ) -> None:
        """Başlığında 'Top 10 Companies', 'Şirket Rehberi' vb. geçen liste sayfaları yakalanmalı."""
        assert is_aggregator_title(aggregator_title) is True

    @pytest.mark.parametrize(
        "valid_company_title",
        [
            "Milvus Robotics | Autonomous Mobile Robots (AMR)",
            "Baykar Teknoloji - İnsansız Hava Araçları ve Kariyer",
            "Altınay Robotik Teknolojileri A.Ş. - Hakkımızda",
            "Adastec Corp - Level-4 Automated Driving SoftwarePlatform",
            "Top Tier Autonomy & LiDAR SLAM Solutions | Rovma Robotics",
        ],
    )
    def test_normal_company_titles_are_not_blocked_as_aggregators(
        self, valid_company_title: str
    ) -> None:
        """Gerçek şirketlerin sayfa başlıkları yanlışlıkla rehber/liste sayfası sanılmamalı."""
        assert is_aggregator_title(valid_company_title) is False

    def test_domain_filter_pipeline_filters_search_result_items_and_deduplicates_domains(
        self,
    ) -> None:
        """DomainFilter, SearchResultItem listesindeki tüm tuzakları elemeli ve sadece gerçek şirketleri döndürmeli."""
        raw_results = [
            SearchResultItem(
                title="Top 10 Robotics Companies in Turkey - Clutch.co",
                url="https://www.clutch.co/tr/robotics",
                snippet="Compare the best robotics firms...",
            ),
            SearchResultItem(
                title="Robotics Startups in Istanbul | F6S",
                url="https://www.f6s.com/companies/robotics/turkey/co",
                snippet="Contact support@f6s.com...",
            ),
            SearchResultItem(
                title="Milvus Robotics - Kariyer ve Açık Pozisyonlar",
                url="https://milvusrobotics.com/tr/kariyer",
                snippet="ROS 2, SLAM ve otonom mobil robot (AMR) mühendisliği.",
            ),
            # Aynı şirketin (milvusrobotics.com) başka bir alt sayfası arama sonucunda tekrar gelirse:
            # deduplicate_domains=True iken aynı şirkete iki kez gitmemek için tekilleştirilmeli
            SearchResultItem(
                title="Milvus Robotics - Hakkımızda",
                url="https://www.milvusrobotics.com/tr/hakkimizda",
                snippet="AMR üreticisi.",
            ),
            SearchResultItem(
                title="20 Best Robotics Companies in Europe (2026 Blog)",
                url="https://some-tech-blog.io/best-robotics-companies-europe",
                snippet="Here is our list of companies...",
            ),
            SearchResultItem(
                title="Aselsan | İnsansız Sistemler ve Otonomi",
                url="https://www.aselsan.com.tr/tr/kariyer",
                snippet="Otonom su altı ve hava araçları Ar-Ge merkezi.",
            ),
        ]

        domain_filter = DomainFilter()
        clean_results = domain_filter.filter_results(raw_results, deduplicate_domains=True)

        assert len(clean_results) == 2
        assert clean_results[0].url == "https://milvusrobotics.com/tr/kariyer"
        assert clean_results[1].url == "https://www.aselsan.com.tr/tr/kariyer"

    def test_invalid_or_empty_urls_are_rejected_safely(self) -> None:
        """Boş string, bozuk URL veya dosya indirme (.pdf/.zip) linkleri güvenli şekilde elenmeli."""
        assert extract_clean_hostname("") == ""
        assert extract_clean_hostname("   ") == ""
        assert is_blacklisted_url("") is True
        assert is_blacklisted_url("javascript:alert(1)") is True
        assert is_blacklisted_url("mailto:support@f6s.com") is True
