"""Faz 8 TDD Birim ve Hata Avcılığı (Bug Hunting) Testleri:
Üç Katmanlı E-posta Kazıyıcı ve `support@` Kara Liste Filtresi (src/scraper/email_extractor.py).

Bu test dosyası, önceki prototipte yaşanan `support@f6s.com` gibi tehlikeli e-posta
tuzaklarının %100 engellendiğini, sahte dosya uzantılarının (`.png`, `.jpg`, `.js`)
elendiğini, URL/Unicode kaçış karakterlerinin (`%20`, `\\u003e`) temizlendiğini ve
bulunan e-postaların Yüksek Öncelik (İK/Kariyer) -> Düşük Öncelik (Genel İletişim)
sırasıyla puanlandığını doğrular.
"""

import pytest

from src.scraper.email_extractor import (
    EmailCandidate,
    EmailExtractor,
    EmailTier,
    classify_email_address,
    clean_raw_email_string,
    extract_emails_from_content,
)


class TestThreeTierEmailClassification:
    """Üç katmanlı e-posta sınıflandırma (Kara Liste / Yüksek Öncelik / Düşük Öncelik) testleri."""

    @pytest.mark.parametrize(
        "blacklisted_email",
        [
            # 1. İlk prototipteki kritik hata ve doğrudan kara liste ön ekleri
            "support@f6s.com",
            "support@robotics-company.com",
            "help@company.io",
            "destek@sirket.com.tr",
            "sales@autonomy.ai",
            "satis@teknoloji.com.tr",
            "marketing@company.com",
            "billing@company.com",
            "accounting@company.com",
            "muhasebe@sirket.com.tr",
            "noreply@company.com",
            "no-reply@company.com",
            "do-not-reply@company.com",
            "admin@company.com",
            "webmaster@company.com",
            "press@company.com",
            "media@company.com",
            "legal@company.com",
            "privacy@company.com",
            "kvkk@sirket.com.tr",
            "abuse@company.com",
            "security@company.com",
            # 2. Noktalı/tireli bileşik kara liste tuzakları (Örn: customer.support@, tech-support@)
            "customer.support@company.com",
            "tech-support@company.com",
            "it_help@company.com",
            # 3. Rehber/Aggregator domain'ine ait e-postalar (Örn: hr@clutch.co veya career@f6s.com bile olsa elenmeli!)
            "career@f6s.com",
            "hr@clutch.co",
            "jobs@linkedin.com",
            # 4. Sahte/örnek ve hata izleme (Sentry/Wix) e-postaları
            "test@example.com",
            "yourname@domain.com",
            "605a7baede844d278b89dc95ae0a9123@sentry.io",
        ],
    )
    def test_blacklisted_emails_are_strictly_classified_as_blacklisted(
        self, blacklisted_email: str
    ) -> None:
        """Kara listedeki hiçbir e-posta ön eki veya rehber site uzantısı geçerli kabul edilmemeli."""
        assert classify_email_address(blacklisted_email) == EmailTier.BLACKLISTED

    @pytest.mark.parametrize(
        "high_priority_email",
        [
            "kariyer@baykartech.com",
            "career@milvusrobotics.com",
            "careers@adastec.com",
            "ik@aselsan.com.tr",
            "hr@altinay.com",
            "insankaynaklari@havelsan.com.tr",
            "humanresources@robotics.io",
            "talent@autonomy.ai",
            "talents@autonomy.ai",
            "jobs@drone-tech.com",
            "join@drone-tech.com",
            "recruiting@vision-ai.com",
            "recruitment@vision-ai.com",
            "people@startup.io",
            "cv@robotik.com.tr",
            "basvuru@savunma.com.tr",
            # Noktalı/tireli İK adresleri (Örn: ik.basvuru@, hr-team@)
            "ik.basvuru@sirket.com.tr",
            "hr-team@company.com",
        ],
    )
    def test_hr_and_career_emails_are_classified_as_high_priority(
        self, high_priority_email: str
    ) -> None:
        """İK ve Kariyer e-posta adresleri HIGH_PRIORITY (Birincil Hedef) olarak sınıflandırılmalı."""
        assert classify_email_address(high_priority_email) == EmailTier.HIGH_PRIORITY

    @pytest.mark.parametrize(
        "low_priority_email",
        [
            "info@rovma.com",
            "iletisim@robotik-arge.com.tr",
            "contact@autonomous-uav.io",
            "hello@deepvision.ai",
            "merhaba@girisim.com.tr",
            "office@tech-company.de",
        ],
    )
    def test_general_contact_emails_are_classified_as_low_priority(
        self, low_priority_email: str
    ) -> None:
        """Genel iletişim adresleri LOW_PRIORITY (İkincil Hedef) olarak sınıflandırılmalı."""
        assert classify_email_address(low_priority_email) == EmailTier.LOW_PRIORITY


class TestEmailCleaningAndExtraction:
    """URL/Unicode kaçış temizliği, sahte dosya uzantısı (.png, .js) eleme ve sıralama testleri."""

    @pytest.mark.parametrize(
        "dirty_input,expected_clean",
        [
            ("mailto:kariyer@sirket.com.tr?subject=Is%20Basvurusu", "kariyer@sirket.com.tr"),
            ("mailto:%20hr@company.com%20", "hr@company.com"),
            ("\\u003ekariyer@milvusrobotics.com", "kariyer@milvusrobotics.com"),
            ("u003eik@aselsan.com.tr", "ik@aselsan.com.tr"),
            ("u003cbank@sirket.com", "bank@sirket.com"),
            ("<info@rovma.com>.", "info@rovma.com"),
            ("&#105;&#107;@sirket.com.tr", "ik@sirket.com.tr"),
        ],
    )
    def test_clean_raw_email_strips_url_encoding_and_unicode_escapes(
        self, dirty_input: str, expected_clean: str
    ) -> None:
        """u003e, %20, HTML entity ve ?subject= parametreleri temizlenerek saf e-posta çıkarılmalı."""
        assert clean_raw_email_string(dirty_input) == expected_clean

    @pytest.mark.parametrize(
        "fake_asset_string",
        [
            "hero-banner@2x.png",
            "company-logo@3x.jpg",
            "background@2x.jpeg",
            "icon@2x.webp",
            "sprite@1x.svg",
            "bundle.min@4.2.1.js",
            "styles@1.0.0.css",
            "font@2.woff2",
        ],
    )
    def test_false_positive_image_and_script_filenames_are_rejected(
        self, fake_asset_string: str
    ) -> None:
        """Retina görsel (@2x.png) ve JS/CSS paket isimleri kesinlikle e-posta sanılmamalı."""
        assert classify_email_address(fake_asset_string) == EmailTier.BLACKLISTED

    def test_extract_emails_from_content_filters_blacklist_and_sorts_by_priority(
        self,
    ) -> None:
        """Karmaşık bir sayfa içeriğinden:
        1. support@, sales@, noreply@ ve @2x.png tamamen çöpe atılmalı,
        2. Bulunan geçerli mailler HIGH_PRIORITY (kariyer@, hr@) en üstte,
           LOW_PRIORITY (info@) altta olacak şekilde sıralanmalı.
        """
        raw_html_and_text = """
        <html>
            <body>
                <img src="/assets/logo@2x.png" />
                <p>Müşteri destek için: support@milvusrobotics.com veya help@milvusrobotics.com</p>
                <p>Satış ekibi: sales@milvusrobotics.com | Bülten: noreply@milvusrobotics.com</p>
                <p>Genel sorularınız için: info@milvusrobotics.com</p>
                <a href="mailto:%20kariyer@milvusrobotics.com?subject=CV">Kariyer</a>
                <span>JSON içi kaçışlı: \\u003ehr@milvusrobotics.com</span>
            </body>
        </html>
        """
        links = [
            "mailto:%20kariyer@milvusrobotics.com?subject=CV",
            "mailto:support@f6s.com",
        ]

        candidates = extract_emails_from_content(raw_html_and_text, links=links)

        extracted_addresses = [c.email for c in candidates]
        # Kara listedekiler kesinlikle listede olmamalı!
        assert "support@milvusrobotics.com" not in extracted_addresses
        assert "help@milvusrobotics.com" not in extracted_addresses
        assert "sales@milvusrobotics.com" not in extracted_addresses
        assert "noreply@milvusrobotics.com" not in extracted_addresses
        assert "support@f6s.com" not in extracted_addresses
        assert "logo@2x.png" not in extracted_addresses

        # Yalnızca 3 geçerli mail kalmalı ve ilk ikisi HIGH_PRIORITY, sonuncusu LOW_PRIORITY olmalı
        assert len(candidates) == 3
        assert candidates[0].tier == EmailTier.HIGH_PRIORITY
        assert candidates[1].tier == EmailTier.HIGH_PRIORITY
        assert {candidates[0].email, candidates[1].email} == {
            "kariyer@milvusrobotics.com",
            "hr@milvusrobotics.com",
        }
        assert candidates[2].email == "info@milvusrobotics.com"
        assert candidates[2].tier == EmailTier.LOW_PRIORITY

    def test_email_extractor_class_select_best_email_helper(self) -> None:
        """EmailExtractor.select_best_email varsa İK mailini, yoksa info@ mailini, hiçbiri yoksa None döndürmeli."""
        extractor = EmailExtractor()

        best_with_hr = extractor.select_best_email(
            "Bize info@sirket.com veya ik@sirket.com adresinden ulaşın."
        )
        assert isinstance(best_with_hr, EmailCandidate)
        assert best_with_hr.email == "ik@sirket.com"
        assert best_with_hr.tier == EmailTier.HIGH_PRIORITY

        best_only_info = extractor.select_best_email(
            "Teknik destek: support@sirket.com, İletişim: iletisim@sirket.com"
        )
        assert best_only_info is not None
        assert best_only_info.email == "iletisim@sirket.com"
        assert best_only_info.tier == EmailTier.LOW_PRIORITY

        none_when_only_blacklist = extractor.select_best_email(
            "Sadece destek: support@f6s.com ve noreply@f6s.com"
        )
        assert none_when_only_blacklist is None
