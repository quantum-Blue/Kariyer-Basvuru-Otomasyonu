"""Faz 9: ATS Tespit Motoru ve Yönlendirme Karar Ağacı (Decision Tree) Birim ve Hata Avcılığı Testleri.

Bu test modülü şu kritik kuralları (TDD Red -> Green -> Bug Hunting) denetler:
1. ATS Tespit Motoru (`src/filter/ats_detector.py`):
   - `greenhouse.io`, `lever.co`, `myworkdayjobs.com`, `workday.com`, `bamboohr.com`,
     `workable.com`, `smartrecruiters.com`, `personio.de`, `personio.com`, `ashbyhq.com`,
     `jobvite.com`, `icims.com`, `taleo.net`, `successfactors.com`, `recruitee.com`,
     `teamtailor.com`, `breezy.hr`, `jazzhr.com` gibi 15+ ATS platformunu hem linklerden (`<a href>`)
     hem de ham HTML içerisinden (`<iframe>`, `<script>`, metin içi URL) tespit edebilmeli.
   - Alt alan adlarını (`boards.greenhouse.io`, `jobs.lever.co`, `aselsan.wd3.myworkdayjobs.com`,
     `robotics-company.bamboohr.com/careers`, `apply.workable.com/autonav`) eksiksiz yakalamalı.
   - **False-Positive (Yanlış Alarm) Koruması:**
     İçinde `lever` veya `workable` kelimesi geçen normal şirket alan adlarını
     (örn. `https://clever.co`, `https://cantilever-robotics.com`, `https://unworkable.io`)
     ASLA yanlışlıkla ATS sanmamalı! Nokta (`.`) sınırlı kök domain kontrolü yapmalı.
2. Yönlendirme Karar Ağacı (`src/filter/decision_tree.py`):
   - **KURAL 1 (ATS Override Kuralı — Faz 9 Kritik Kabul Kriteri):**
     Bir şirketin sayfasında hem 1. Öncelik İK e-postası (`kariyer@sirket.com`, `hr@company.io`)
     hem de bir ATS başvuru formu linki (`https://boards.greenhouse.io/company`) varsa,
     ATS kuralı e-postayı EZMELİ (`Override -> MANUAL_ATS`) ve bulunan ATS linkini raporlamalı!
   - **KURAL 2 (`SEND_EMAIL`):**
     Sayfada ATS linki yoksa ve en az bir adet `HIGH_PRIORITY` (`kariyer@`, `ik@`, `hr@`, `jobs@`)
     e-posta adresi varsa karar `SEND_EMAIL` olmalı ve en yüksek öncelikli İK adresi seçilmeli.
   - **KURAL 3 (`SEND_EMAIL_LOW_PRIORITY`):**
     Sayfada ATS linki yoksa, `HIGH_PRIORITY` e-posta da yoksa ama `LOW_PRIORITY` (`info@`,
     `iletisim@`, `contact@`) e-posta varsa karar `SEND_EMAIL_LOW_PRIORITY` olmalı.
   - **KURAL 4 (`SKIP`):**
     Sayfada ne ATS linki ne de geçerli bir e-posta varsa (veya yalnızca `support@`, `noreply@`,
     `sales@`, `kvkk@` gibi kara liste adresleri varsa) karar `SKIP` olmalı ve hedef e-posta `None` kalmalı!
"""

from __future__ import annotations

import pytest

from src.filter.ats_detector import (
     DEFAULT_ATS_DOMAINS,
    ATSDetectionResult,
    ATSDetector,
    detect_ats_in_page,
    is_ats_url,
)
from src.filter.decision_tree import (
    DecisionTreeEngine,
    RoutingAction,
    RoutingDecision,
    evaluate_company_routing,
)
from src.scraper.email_extractor import EmailCandidate, EmailTier
from src.scraper.page_fetcher import CompanyScrapeResult


class TestATSDetectorTDDAndBugHunting:
    """`src/filter/ats_detector.py` modülünün birim ve sınır durumu (edge-case) testleri."""

    @pytest.mark.parametrize(
        "ats_url, expected_provider",
        [
            ("https://boards.greenhouse.io/boston-dynamics/jobs/12345", "greenhouse.io"),
            ("https://job-boards.eu.greenhouse.io/autonav", "greenhouse.io"),
            ("https://jobs.lever.co/skydio/8a9b-1234", "lever.co"),
            ("https://jobs.eu.lever.co/robotics-inc", "lever.co"),
            ("https://nvidia.wd5.myworkdayjobs.com/en-US/NVIDIAExternalCareerSite", "myworkdayjobs.com"),
            ("https://www.workday.com/en-us/company/careers.html", "workday.com"),
            ("https:// drone-startup.bamboohr.com/careers ", "bamboohr.com"),
            ("https://apply.workable.com/baykar-test/", "workable.com"),
            ("https://jobs.smartrecruiters.com/BoschGroup", "smartrecruiters.com"),
            ("https://autonav-gmbh.jobs.personio.de/job/998877", "personio.de"),
            ("https://robotics.jobs.personio.com/", "personio.com"),
            ("https://jobs.ashbyhq.com/figure-ai/123", "ashbyhq.com"),
            ("https://jobs.jobvite.com/careers/aerovironment", "jobvite.com"),
            ("https://careers-lockheed.icims.com/jobs/search", "icims.com"),
            ("https://sirket.taleo.net/careersection/2/jobsearch.ftl", "taleo.net"),
            ("https://career5.successfactors.com/career?company=test", "successfactors.com"),
            ("https://slamtech.recruitee.com/o/ros2-engineer", "recruitee.com"),
        ],
    )
    def test_is_ats_url_recognizes_all_15_plus_major_ats_platforms_and_subdomains(
        self, ats_url: str, expected_provider: str
    ) -> None:
        """15+ popüler ATS platformunun doğrudan URL'leri ve derin alt alan adları (subdomains)
        %100 doğrulukla tespit edilmeli.
        """
        assert len(DEFAULT_ATS_DOMAINS) >= 15
        assert is_ats_url(ats_url) is True

        detector = ATSDetector()
        result = detector.detect(links=[ats_url])
        assert result.has_ats is True
        assert result.matched_provider == expected_provider
        assert len(result.ats_urls) == 1

    @pytest.mark.parametrize(
        "safe_company_url",
        [
            "https://clever.co/careers",                    # 'lever.co' soneki ama başka domain!
            "https://cantilever-robotics.com/jobs",         # içinde 'lever' geçiyor
            "https://unworkable.com/about",                 # içinde 'workable.com' soneki
            "https://mygreenhouse.io/contact",              # 'greenhouse.io' soneki ama farklı kök domain
            "https://baykar.com.tr/tr/kariyer",             # normal Türk şirket kariyer sayfası
            "https://aselsan.com.tr/insan-kaynaklari",      # normal şirket sayfası
            "mailto:hr@greenhouse-robotics.com",            # mailto linki ATS sayfası değildir!
            "javascript:void(0)",                           # geçersiz şema
            "",                                             # boş dizge
            "   ",                                          # sadece boşluk
        ],
    )
    def test_is_ats_url_prevents_false_positives_on_similar_company_domains(
        self, safe_company_url: str
    ) -> None:
        """HATA AVCILIĞI (False-Positive Koruması):
        `clever.co` (`lever.co` içerir) veya `mygreenhouse.io` (`greenhouse.io` içerir) gibi
        normal şirket siteleri yanlışlıkla ATS olarak işaretlenmemeli!
        Ayrıca `mailto:` linkleri asla ATS web sayfası sayılmamalı.
        """
        assert is_ats_url(safe_company_url) is False

    def test_detect_ats_in_page_extracts_embedded_iframe_and_raw_html_ats_links(self) -> None:
        """Bazı şirketler `<a href>` koymak yerine kariyer sayfalarının içine
        `<iframe src="https://boards.greenhouse.io/embed/job_board?for=sirket">`
        veya `<script src="https://jobs.lever.co/...">` gömerler.
        `detect_ats_in_page` hem `links` listesini hem de `html_content` içindeki gömülü
        ATS URL'lerini yakalayabilmeli.
        """
        raw_html = """
        <html>
          <body>
            <h1>Careers at Autonomous Drone Corp</h1>
            <iframe id="grnhse_iframe" src="https://boards.greenhouse.io/embed/job_board?for=autodrone"></iframe>
            <p>Or view our European portal at https://jobs.eu.lever.co/autodrone-eu</p>
          </body>
        </html>
        """
        result = detect_ats_in_page(links=["https://autodrone.io/about"], html_content=raw_html)
        assert isinstance(result, ATSDetectionResult)
        assert result.has_ats is True
        assert result.matched_provider == "greenhouse.io"
        assert "https://boards.greenhouse.io/embed/job_board?for=autodrone" in result.ats_urls
        assert "https://jobs.eu.lever.co/autodrone-eu" in result.ats_urls

    def test_ats_detector_ignores_static_asset_urls_on_ats_domains_if_invalid_or_deduplicates(self) -> None:
        """Aynı ATS linki hem `links` içinde hem `html_content` içinde birden fazla kez geçiyorsa
        mükerrer kayıt oluşturmadan tekilleştirilmeli (`deduplication`).
        """
        dup_url = "https://jobs.ashbyhq.com/robotics-ai/apply"
        html = f'<a href="{dup_url}">Apply 1</a> <a href="{dup_url}">Apply 2</a>'
        res = detect_ats_in_page(links=[dup_url, dup_url], html_content=html)
        assert res.has_ats is True
        assert res.ats_urls == [dup_url]


class TestDecisionTreeRoutingTDDAndBugHunting:
    """`src/filter/decision_tree.py` Yönlendirme Karar Ağacı birim ve sınır durumu testleri."""

    def test_ats_override_rule_beats_high_priority_hr_email(self) -> None:
        """KRİTİK KABUL KRİTERİ (Faz 9 - ATS Override Kuralı):
        Sayfada hem 1. Öncelik İK e-postası (`kariyer@sirket.com` ve `hr@sirket.com`)
        hem de bir ATS linki (`greenhouse.io`) varsa, ATS kuralı e-postayı EZMELİ
        (`RoutingAction.MANUAL_ATS`)!
        Böylece ATS kullanan kurumsal bir şirkete gereksiz soğuk e-posta atılmaz,
        kullanıcının manuel form doldurması için ATS kuyruğuna yönlendirilir.
        """
        html_with_both = """
        <html>
          <body>
            <h2>Kariyer</h2>
            <p>Sorularınız için: <a href="mailto:kariyer@sirket.com.tr">kariyer@sirket.com.tr</a></p>
            <p>Açık pozisyonlara başvurmak için:
              <a href="https://boards.greenhouse.io/sirket-robotics">Greenhouse İlanlarımız</a>
            </p>
          </body>
        </html>
        """
        links = [
            "mailto:kariyer@sirket.com.tr",
            "https://boards.greenhouse.io/sirket-robotics",
        ]

        decision = evaluate_company_routing(
            html_content=html_with_both,
            links=links,
        )

        assert isinstance(decision, RoutingDecision)
        assert decision.action == RoutingAction.MANUAL_ATS
        assert decision.ats_url == "https://boards.greenhouse.io/sirket-robotics"
        assert decision.ats_provider == "greenhouse.io"
        # Bulunan aday e-postalar bilgi amaçlı candidate_emails listesinde tutulabilir
        # ancak otomatik gönderim yapılacak target_email kesinlikle None olmalıdır
        # (veya eylemin MANUAL_ATS olduğu garanti edilmelidir).
        assert decision.target_email is None
        assert len(decision.candidate_emails) == 1
        assert decision.candidate_emails[0].email == "kariyer@sirket.com.tr"

    def test_no_ats_with_high_priority_email_routes_to_send_email(self) -> None:
        """Sayfada ATS yoksa ve hem `kariyer@` (`HIGH_PRIORITY`) hem `info@` (`LOW_PRIORITY`)
        hem de `support@` (`BLACKLISTED`) varsa:
        - Karar `RoutingAction.SEND_EMAIL` olmalı,
        - Hedef e-posta (`target_email`) olarak `kariyer@` seçilmeli!
        """
        html = """
        <html>
          <body>
            <p>Destek: support@otonom-iha.com.tr | Genel: info@otonom-iha.com.tr</p>
            <p>İnsan Kaynakları: ik@otonom-iha.com.tr</p>
          </body>
        </html>
        """
        decision = evaluate_company_routing(html_content=html, links=[])

        assert decision.action == RoutingAction.SEND_EMAIL
        assert decision.target_email == "ik@otonom-iha.com.tr"
        assert decision.email_tier == EmailTier.HIGH_PRIORITY
        assert decision.ats_url is None

    def test_no_ats_with_only_low_priority_email_routes_to_send_email_low_priority(self) -> None:
        """Sayfada ATS yoksa, `kariyer@`/`hr@` gibi bir İK adresi de yoksa, fakat
        `info@` veya `iletisim@` gibi genel iletişim adresi varsa:
        - Karar `RoutingAction.SEND_EMAIL_LOW_PRIORITY` olmalı,
        - Hedef e-posta `info@...` olmalı.
        """
        html = """
        <html>
          <body>
            <p>Teknik destek: help@robo-startup.io | Satış: sales@robo-startup.io</p>
            <p>İletişim: hello@robo-startup.io</p>
          </body>
        </html>
        """
        decision = evaluate_company_routing(html_content=html, links=[])

        assert decision.action == RoutingAction.SEND_EMAIL_LOW_PRIORITY
        assert decision.target_email == "hello@robo-startup.io"
        assert decision.email_tier == EmailTier.LOW_PRIORITY
        assert decision.ats_url is None

    def test_Allow_low_priority_disabled_routes_low_priority_only_company_to_skip(self) -> None:
        """Eğer karar ağacında `allow_low_priority=False` parametresi verilirse,
        yalnızca `info@` adresi bulunan şirketlere mail atılmayıp `SKIP` kararı verilmeli.
        """
        html = "<p>Contact us at info@strictly-hr-only.com</p>"
        engine = DecisionTreeEngine(allow_low_priority=False)
        decision = engine.evaluate(html_content=html, links=[])

        assert decision.action == RoutingAction.SKIP
        assert decision.target_email is None

    def test_only_blacklisted_emails_and_no_ats_routes_to_skip(self) -> None:
        """KIRMIZI ÇİZGİ #2 KORUMASI:
        Sayfada ATS yoksa ve bulunan tüm e-postalar kara listede (`support@`, `noreply@`,
        `sales@`, `kvkk@`, `support@f6s.com`, `logo@2x.png`) ise:
        - Karar kesinlikle `RoutingAction.SKIP` olmalı,
        - `target_email` `None` olmalı!
        """
        html = """
        <html>
          <body>
            <img src="banner@2x.png" />
            <p>Support: support@f6s.com, help@company.com, noreply@company.com, kvkk@company.com</p>
          </body>
        </html>
        """
        decision = evaluate_company_routing(html_content=html, links=["mailto:support@company.com"])

        assert decision.action == RoutingAction.SKIP
        assert decision.target_email is None
        assert decision.email_tier is None
        assert decision.ats_url is None
        assert decision.candidate_emails == []

    def test_evaluate_scrape_result_integrates_directly_with_faz7_company_scrape_result(self) -> None:
        """`DecisionTreeEngine.evaluate_scrape_result` metodu Faz 7'nin `CompanyScrapeResult`
        nesnesini doğrudan alıp değerlendirebilmeli.
        """
        scrape_result = CompanyScrapeResult(
            base_url="https://slam-vision.ai",
            company_name="SLAM Vision AI",
            language="EN",
            combined_text="We build 3D SLAM and ROS 2 autonomy.",
            combined_html='<p>Apply via <a href="mailto:talent@slam-vision.ai">talent@slam-vision.ai</a></p>',
            all_links=["mailto:talent@slam-vision.ai"],
            pages=[],
        )
        engine = DecisionTreeEngine()
        decision = engine.evaluate_scrape_result(scrape_result)

        assert decision.action == RoutingAction.SEND_EMAIL
        assert decision.target_email == "talent@slam-vision.ai"
        assert decision.email_tier == EmailTier.HIGH_PRIORITY

    def test_pre_extracted_emails_and_empty_inputs_handled_gracefully(self) -> None:
        """Tamamen boş HTML/link verildiğinde veya dışarıdan önceden kazınmış `EmailCandidate`
        listesi verildiğinde sistem çökmeden doğru kararı üretmeli.
        """
        # 1. Tamamen boş girdi -> SKIP
        empty_decision = evaluate_company_routing(html_content="", links=None)
        assert empty_decision.action == RoutingAction.SKIP
        assert empty_decision.target_email is None

        # 2. Dışarıdan hazır EmailCandidate listesi + ATS linki -> MANUAL_ATS (Override!)
        pre_extracted = [
            EmailCandidate(
                email="hr@override-test.com",
                tier=EmailTier.HIGH_PRIORITY,
                domain="override-test.com",
                local_part="hr",
            )
        ]
        override_decision = evaluate_company_routing(
            html_content="",
            links=["https://jobs.lever.co/override-test"],
            extracted_emails=pre_extracted,
        )
        assert override_decision.action == RoutingAction.MANUAL_ATS
        assert override_decision.ats_provider == "lever.co"
        assert override_decision.target_email is None
