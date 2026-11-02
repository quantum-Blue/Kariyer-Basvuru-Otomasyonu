"""Faz 11: Gemini LLM İstemcisi, Çıktı Doğrulayıcı ve Fallback Mekanizması Testleri.

Bu test modülü şu kritik kuralları (TDD Red -> Green -> Bug Hunting) denetler:
1. Çıktı Doğrulayıcı (`validate_generated_email_body`):
   - Kelime sayısı sınırlarını (`min_word_count` ile `max_word_count` arası) kontrol etmeli.
   - Zorunlu aday bağlantılarının (`required_links` / portfolyo ve CV linki) metin içinde
     birebir yer aldığını doğrulamalı.
   - **Halüsinasyonlu / Uydurma Link Yakalama (Faz 11 Kritik Kabul Kriteri):**
     LLM halüsinasyon görüp metin içerisine adayın izin verilen linkleri (`allowed_links`)
     dışında uydurma bir URL (`https://example.com/fake-cv`, `https://bit.ly/123` vb.) yazarsa
     doğrulayıcı bunu anında yakalamalı (`is_valid = False`)!
   - **Yasaklı Dosya Eki (Attachment) İfadesi Yakalama:**
     LLM *"ekteki dosyada"*, *"özgeçmişim ektedir"*, *"please find attached"* gibi bir ifade
     kullanırsa doğrulayıcı bunu reddetmeli (`is_valid = False`).
2. Eksik Bağlantı Onarımı ve Fallback Mekanizması (`GeminiLLMClient.generate_email`):
   - Eğer LLM geçerli bir metin ürettiyse ancak sonuna zorunlu bağlantı bloğunu eklemeyi
     unuttuysa (veya sildiyse), istemci önce bağlantı bloğunu metnin sonuna ekleyerek onarmayı
     denemeli; onarılmış metin tüm doğrulama kurallarından geçerse onu kullanmalı (`used_fallback=False`
     veya onarılmış LLM çıktısı).
   - Eğer LLM uydurma (halüsinasyon) link yazdıysa, kelime sınırını aştıysa, yasaklı "attached/ekte"
     ifadesi kullandıysa, boş yanıt döndürdüyse VEYA Gemini API hata fırlattıysa (`HTTP 429 Quota`,
     `ConnectionError`, eksik `api_key`), sistem ASLA çökmemeli; kesintisiz olarak Faz 10'daki
     statik yedek şablona (`fallback_email`, `used_fallback=True`) geçiş yapmalı!
"""

from __future__ import annotations

import pytest

from src.generator.llm_client import (
    EmailGenerationResult,
    EmailValidationResult,
    GeminiLLMClient,
    extract_urls_from_text,
    validate_generated_email_body,
)
from src.generator.prompt_builder import build_application_prompt
from src.generator.template_manager import CandidateLinks


class TestLLMOutputValidatorTDDAndBugHunting:
    """`validate_generated_email_body` ve yardımcı doğrulama fonksiyonlarının birim testleri."""

    def setup_method(self) -> None:
        self.links = CandidateLinks(
            sender_name="Muhammed Enes BAL",
            cv_url_tr="https://drive.google.com/cv-tr-enes",
            cv_url_en="https://drive.google.com/cv-en-enes",
            portfolio_url="https://drive.google.com/portfolio-enes",
            github_url="https://github.com/quantum-Blue",
            linkedin_url="https://linkedin.com/in/muhammedenes-bal",
        )
        self.allowed_urls = [
            self.links.cv_url_tr,
            self.links.cv_url_en,
            self.links.portfolio_url,
            self.links.github_url,
            self.links.linkedin_url,
        ]

    def test_valid_llm_output_passes_all_validation_checks(self) -> None:
        """Kelime sınırları içinde olan, zorunlu CV ve Portfolyo linklerini içeren,
        uydurma link veya yasaklı 'ekte/attached' kelimesi barındırmayan metin doğrulamadan geçmeli.
        """
        valid_body = (
            "Sayın Baykar Teknoloji İnsan Kaynakları ve Mühendislik Ekibi,\n\n"
            "Bilgisayar Mühendisliği mezunu bir Robotik, Otonom Sistemler ve Yapay Zekâ mühendisi "
            "olarak insansız hava araçları ve otonom seyrüsefer alanındaki çalışmalarınızı büyük bir "
            "heyecanla takip ediyorum. Bitirme projemde GNSS-bağımsız ortamlarda LiDAR ve stereo kamera "
            "füzyonu kullanarak Fast-LIO2 ve RTAB-MAP tabanlı 3B SLAM mimarisi geliştirdim. Ayrıca "
            "Teknofest İnsansız Su Altı Aracı yarışmasında otonom rota kontrolü ve YOLOv12 tabanlı "
            "nesne tespiti görevlerini üstlendim. ROS 2, PX4 ve gömülü yapay zekâ konularındaki "
            "pratik tecrübemin Ar-Ge ekibinize güçlü katkı sağlayacağına inanıyorum.\n\n"
            "Özgeçmiş ve teknik portfolyo belgelerime aşağıdaki bağlantılardan ulaşabilirsiniz:\n"
            f"- Özgeçmiş (CV): {self.links.cv_url_tr}\n"
            f"- Teknik Portfolyo: {self.links.portfolio_url}\n"
            f"- GitHub: {self.links.github_url}\n\n"
            "Saygılarımla,\nMuhammed Enes BAL"
        )

        validation = validate_generated_email_body(
            body=valid_body,
            required_links=[self.links.cv_url_tr, self.links.portfolio_url],
            allowed_links=self.allowed_urls,
            min_word_count=60,
            max_word_count=300,
        )

        assert isinstance(validation, EmailValidationResult)
        assert validation.is_valid is True
        assert validation.errors == []
        assert 60 <= validation.word_count <= 300

    def test_validator_catches_hallucinated_fake_urls(self) -> None:
        """KRİTİK KABUL KRİTERİ (Faz 11 Bug Hunting):
        LLM gerçek portfolyo linkinin yanına (veya yerine) uydurma bir URL
        (`https://muhammedenesbal.com/portfolio.pdf` veya `https://example.com/cv`) yazarsa,
        `validate_generated_email_body` bunu halüsinasyon olarak yakalayıp reddetmeli!
        """
        body_with_hallucinated_link = (
            "Dear AutoNav Robotics Engineering Team,\n\n"
            + ("I am a Robotics and Autonomous Systems Engineer experienced in ROS 2, SLAM, and UAV navigation. " * 6)
            + f"\nPortfolio: {self.links.portfolio_url}\n"
            + f"Resume: {self.links.cv_url_en}\n"
            + "Additional Projects: https://fake-hallucinated-portfolio.example.com/enes-projects\n"
        )

        urls = extract_urls_from_text(body_with_hallucinated_link)
        assert "https://fake-hallucinated-portfolio.example.com/enes-projects" in urls

        validation = validate_generated_email_body(
            body=body_with_hallucinated_link,
            required_links=[self.links.portfolio_url, self.links.cv_url_en],
            allowed_links=self.allowed_urls,
            min_word_count=40,
            max_word_count=300,
        )

        assert validation.is_valid is False
        assert any("halüsinasyon" in err.lower() or "uydurma" in err.lower() for err in validation.errors)

    def test_validator_rejects_missing_required_links_word_count_violation_and_attachment_phrases(
        self,
    ) -> None:
        """Doğrulayıcı;
        1. Çok kısa (< min_word_count) veya çok uzun (> max_word_count) metinleri,
        2. Zorunlu portfolyo linki eksik olan metinleri,
        3. İçinde 'ekteki dosyada' veya 'attached' geçen metinleri
        ayrı ayrı tespit edip `errors` listesinde raporlamalı.
        """
        # Çok kısa + zorunlu link yok + yasaklı 'attached' kelimesi var
        bad_body = "Hello team, please find attached my resume for the robotics engineer role."

        validation = validate_generated_email_body(
            body=bad_body,
            required_links=[self.links.portfolio_url],
            allowed_links=self.allowed_urls,
            min_word_count=60,
            max_word_count=250,
        )

        assert validation.is_valid is False
        assert len(validation.errors) >= 3


class TestGeminiLLMClientWithRepairAndFallback:
    """`GeminiLLMClient` sınıfının üretim, eksik link onarımı ve statik fallback geçiş testleri."""

    def setup_method(self) -> None:
        self.links = CandidateLinks(
            sender_name="Muhammed Enes BAL",
            cv_url_tr="https://drive.google.com/cv-tr-enes",
            cv_url_en="https://drive.google.com/cv-en-enes",
            portfolio_url="https://drive.google.com/portfolio-enes",
            github_url="https://github.com/quantum-Blue",
            linkedin_url="https://linkedin.com/in/muhammedenes-bal",
        )
        self.built_prompt_tr = build_application_prompt(
            company_name="Baykar Otonom",
            company_context="İnsansız hava araçları ve GNSS-bağımsız SLAM navigasyon.",
            language="TR",
            links=self.links,
            min_word_count=50,
            max_word_count=280,
        )

    def test_generate_email_returns_llm_output_when_valid(self) -> None:
        """Mock Gemini backend geçerli ve kurallara tam uyan bir metin döndürdüğünde
        `used_fallback=False` olmalı ve doğrudan LLM'in ürettiği kişiselleştirilmiş metin seçilmeli.
        """
        perfect_llm_response = (
            "Sayın Baykar Otonom İnsan Kaynakları ve Ar-Ge Ekibi,\n\n"
            "Bilgisayar Mühendisliği mezunu bir Robotik ve Otonom Sistemler mühendisi olarak "
            "GNSS-bağımsız SLAM navigasyon ve insansız hava araçları alanındaki öncü çalışmalarınızı "
            "yakından takip ediyorum. Bitirme projemde LiDAR ve stereo kamera sensör füzyonu ile "
            "Fast-LIO2 ve RTAB-MAP tabanlı 3B haritalama ve otonom uçuş kontrolü gerçekleştirdim. "
            "Ayrıca Teknofest ve TÜBİTAK projelerinde ROS 2, PX4 ve derin öğrenme tabanlı nesne "
            "tespiti algoritmaları geliştirdim.\n\n"
            "Özgeçmiş ve teknik portfolyo bağlantılarımı aşağıda bilgilerinize sunarım:\n"
            f"- Özgeçmiş (CV): {self.links.cv_url_tr}\n"
            f"- Teknik Portfolyo: {self.links.portfolio_url}\n"
            f"- GitHub: {self.links.github_url}\n\n"
            "Saygılarımla,\nMuhammed Enes BAL"
        )

        client = GeminiLLMClient(
            api_key="test-fake-gemini-key",
            model_name="gemini-2.5-flash",
            min_word_count=50,
            max_word_count=280,
            generation_backend=lambda sys_inst, usr_prompt, model, temp: perfect_llm_response,
        )

        result = client.generate_email(self.built_prompt_tr, links=self.links)

        assert isinstance(result, EmailGenerationResult)
        assert result.used_fallback is False
        assert result.repaired_links is False
        assert result.subject == self.built_prompt_tr.subject
        assert self.links.portfolio_url in result.body
        assert self.links.cv_url_tr in result.body
        assert "Fast-LIO2" in result.body

    def test_generate_email_repairs_missing_portfolio_link_when_llm_forgets_it(self) -> None:
        """KRİTİK KABUL KRİTERİ (Faz 11 Bug Hunting):
        LLM harika bir başvuru metni yazdıysa fakat dalgınlıkla portfolyo/CV linklerini
        metne eklemeyi unuttuysa (sildiyse), istemci metni çöpe atmadan önce aday bağlantı
        bloğunu (`format_links_block`) metne otomatik ekleyip onarmalı (`repaired_links=True`)!
        """
        llm_response_missing_links = (
            "Sayın Baykar Otonom İnsan Kaynakları ve Mühendislik Ekibi,\n\n"
            "Bilgisayar Mühendisliği mezunu bir Robotik, Otonom Sistemler ve Yapay Zekâ mühendisi "
            "olarak GNSS-bağımsız navigasyon ve insansız hava araçları alanındaki projelerinizi "
            "yakından takip ediyorum. Bitirme projemde LiDAR ve stereo kamera füzyonu ile Fast-LIO2 "
            "ve RTAB-MAP tabanlı 3B SLAM mimarisi geliştirdim. ROS 2, PX4 ve bilgisayarlı görü "
            "üzerine edindiğim deneyimle ekibinize katkı sağlamaya hazırım.\n\n"
            "Saygılarımla,\nMuhammed Enes BAL"
        )

        client = GeminiLLMClient(
            api_key="test-fake-gemini-key",
            min_word_count=50,
            max_word_count=280,
            generation_backend=lambda s, u, m, t: llm_response_missing_links,
        )

        result = client.generate_email(self.built_prompt_tr, links=self.links)

        assert result.used_fallback is False
        assert result.repaired_links is True
        assert self.links.portfolio_url in result.body
        assert self.links.cv_url_tr in result.body

    @pytest.mark.parametrize(
        "bad_llm_output_or_exception",
        [
            # Senaryo 1: LLM uydurma (halüsinasyon) URL üretti
            (
                "Sayın Ekip, " + ("ROS 2 ve SLAM projeleri geliştirdim. " * 12)
                + "Portfolyom: https://uydurma-link.com/cv.pdf ve https://drive.google.com/portfolio-enes"
            ),
            # Senaryo 2: LLM yasaklı 'ekteki dosyada' ifadesi kullandı
            (
                "Sayın Ekip, özgeçmişim ekteki dosyada yer almaktadır. "
                + ("ROS 2 ve SLAM projeleri geliştirdim. " * 12)
                + "https://drive.google.com/portfolio-enes https://drive.google.com/cv-tr-enes"
            ),
            # Senaryo 3: LLM kelime sınırını aştı (500+ kelime)
            ("Robotik otonom sistemler SLAM ROS2 " * 100),
            # Senaryo 4: LLM boş yanıt döndürdü
            "   ",
            # Senaryo 5: Gemini API HTTP 429 Quota / Rate Limit hatası fırlattı
            RuntimeError("429 Resource Exhausted: Gemini Free Tier quota exceeded"),
            # Senaryo 6: Ağ bağlantısı koptu
            ConnectionError("Failed to establish SSL connection to generativelanguage.googleapis.com"),
        ],
    )
    def test_generate_email_seamlessly_falls_back_on_validation_failure_or_api_error(
        self, bad_llm_output_or_exception: str | Exception
    ) -> None:
        """LLM halüsinasyon gördüğünde, kelime sınırını ihlal ettiğinde veya Gemini API
        çöktüğünde (`429 Quota`, `ConnectionError`), `GeminiLLMClient` ASLA çökmemeli;
        anında `used_fallback=True` ile güvenli statik şablona (`fallback_email`) geçmeli!
        """
        def faulty_backend(s: str, u: str, m: str, t: float) -> str:
            _ = (s, u, m, t)
            if isinstance(bad_llm_output_or_exception, Exception):
                raise bad_llm_output_or_exception
            return bad_llm_output_or_exception

        client = GeminiLLMClient(
            api_key="test-fake-gemini-key",
            min_word_count=50,
            max_word_count=280,
            generation_backend=faulty_backend,
        )

        result = client.generate_email(self.built_prompt_tr, links=self.links)

        assert result.used_fallback is True
        assert result.body == self.built_prompt_tr.fallback_email.body
        assert self.links.portfolio_url in result.body
        assert self.links.cv_url_tr in result.body
        assert "uydurma-link.com" not in result.body

    def test_missing_api_key_skips_network_call_and_uses_fallback_immediately(self) -> None:
        """`.env` içinde `GEMINI_API_KEY` tanımlanmamışsa (boş dizge `""`),
        hiçbir ağ çağrısı yapılmadan doğrudan statik fallback şablon döndürülmeli.
        """
        backend_called = False

        def should_not_be_called(s: str, u: str, m: str, t: float) -> str:
            nonlocal backend_called
            _ = (s, u, m, t)
            backend_called = True
            return "Should not run"

        client = GeminiLLMClient(
            api_key="   ",
            generation_backend=should_not_be_called,
        )
        result = client.generate_email(self.built_prompt_tr, links=self.links)

        assert backend_called is False
        assert result.used_fallback is True
        assert result.body == self.built_prompt_tr.fallback_email.body
