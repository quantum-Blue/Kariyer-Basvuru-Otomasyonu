"""Faz 10: Çift Dilli Şablon Yöneticisi ve Dinamik Prompt İnşası Birim ve Hata Avcılığı Testleri.

Bu test modülü şu kritik gereksinimleri (TDD Red -> Green -> Bug Hunting) denetler:
1. Çift Dilli Şablon Yöneticisi (`src/generator/template_manager.py`):
   - Türk şirketler (`language="TR"`) için Türkçe konu başlığı (`subject`) ve Türkçe başvuru
     gövdesi; yabancı şirketler (`language="EN"`) için İngilizce konu başlığı ve İngilizce
     başvuru gövdesi üretilmeli.
   - **Sıfır Dosya Eki (No-Attachment) ve Link Yerleştirme Kuralı:**
     Spam filtrelerine takılmamak için e-postalarda asla fiziksel PDF eki (`attachment`) kullanılmaz.
     Şablonlar, adayın `cv_url` (dile göre `cv_url_tr` veya `cv_url_en`), `portfolio_url`,
     `github_url` ve `linkedin_url` bağlantılarını temiz URL olarak metne yerleştirmelidir.
   - Yanıltıcı ek ifadeleri (*"ekteki özgeçmişim"*, *"ekte yer alan"*, *"attached CV"*,
     *"please find attached"*) şablonlarda kesinlikle yer almamalı ve `contains_forbidden_attachment_phrase`
     fonksiyonu bu ifadeleri hem Türkçe hem İngilizce metinlerde yakalayabilmelidir.
2. Dinamik RAG Prompt İnşası (`src/generator/prompt_builder.py`):
   - Şirketin kazınan **"Hakkımızda / Kariyer"** metnini (`company_context`), ChromaDB RAG
     veritabanından dönen **aday projeleri / yetkinlik parçalarını** (`rag_chunks` / `rag_context`)
     ve çift dilli şablon kurallarını birleştirerek LLM için eksiksiz bir `BuiltPrompt` üretmeli.
   - **Hata Avcılığı (Boş Şirket Metni Koruması — Faz 10 Kritik Kabul Kriteri):**
     Şirketin "Hakkımızda" metni boş (`""` veya sadece boşluk) gelse bile prompt veya yedek şablon
     ASLA bozulmamalı (`None`, `KeyError` veya yarım cümle üretmemeli); genel ama profesyonel bir
     Robotik / Otonom Sistemler / ROS 2 / Yapay Zekâ vurgusuna otomatik olarak dönmelidir!
   - Aşırı uzun şirket metinleri (örn. 50.000 karakterlik ham sayfa dökümü) LLM token limitini
     patlatmamak için güvenli bir uzunlukta kırpılmalıdır (`max_company_context_chars`).
"""

from __future__ import annotations

import pytest

from src.generator.prompt_builder import (
    BuiltPrompt,
    PromptBuilder,
    build_application_prompt,
)
from src.generator.template_manager import (
    CandidateLinks,
    RenderedEmailTemplate,
    TemplateManager,
    contains_forbidden_attachment_phrase,
    render_fallback_email,
)
from src.rag.vector_store import SearchResult


class TestBilingualTemplateManagerTDDAndBugHunting:
    """`src/generator/template_manager.py` modülünün birim ve sınır durumu testleri."""

    def setup_method(self) -> None:
        self.links = CandidateLinks(
            sender_name="Muhammed Enes BAL",
            cv_url_tr="https://drive.google.com/cv-tr-muhammed-enes-bal",
            cv_url_en="https://drive.google.com/cv-en-muhammed-enes-bal",
            portfolio_url="https://drive.google.com/portfolio-muhammed-enes-bal",
            github_url="https://github.com/quantum-Blue",
            linkedin_url="https://linkedin.com/in/muhammedenes-bal",
        )

    def test_render_turkish_template_uses_turkish_cv_link_and_zero_attachment_phrases(self) -> None:
        """Türk şirket (`language="TR"`) için üretilen şablonda:
        - Türkçe konu başlığı ve Türkçe hitap yer almalı,
        - `cv_url_tr` ve `portfolio_url` metin içinde bulunmalı (`cv_url_en` değil!),
        - Hiçbir 'ekteki dosyada / ekte sunulmuştur' ifadesi geçmemeli!
        """
        rendered = render_fallback_email(
            company_name="Baykar Teknoloji",
            language="TR",
            links=self.links,
            project_highlights="GNSS-bağımsız ortamlarda Fast-LIO2 ve RTAB-MAP ile 3B SLAM navigasyon sistemi",
        )

        assert isinstance(rendered, RenderedEmailTemplate)
        assert rendered.language == "TR"
        assert "Baykar Teknoloji" in rendered.subject or "Muhammed Enes BAL" in rendered.subject
        assert "Baykar Teknoloji" in rendered.body
        assert self.links.cv_url_tr in rendered.body
        assert self.links.portfolio_url in rendered.body
        assert self.links.github_url in rendered.body
        assert self.links.cv_url_en not in rendered.body
        assert contains_forbidden_attachment_phrase(rendered.body) is False

    def test_render_english_template_uses_english_cv_link_and_zero_attachment_phrases(self) -> None:
        """Yabancı şirket (`language="EN"`) için üretilen şablonda:
        - İngilizce konu başlığı ve İngilizce gövde yer almalı,
        - `cv_url_en` ve `portfolio_url` metin içinde bulunmalı,
        - Hiçbir 'attached file / please find attached' ifadesi geçmemeli!
        """
        rendered = render_fallback_email(
            company_name="AutoNav Robotics",
            language="EN",
            links=self.links,
            project_highlights="3D LiDAR SLAM and autonomous quadrotor navigation using ROS 2 and PX4",
        )

        assert rendered.language == "EN"
        assert "AutoNav Robotics" in rendered.body
        assert self.links.cv_url_en in rendered.body
        assert self.links.portfolio_url in rendered.body
        assert self.links.github_url in rendered.body
        assert self.links.cv_url_tr not in rendered.body
        assert contains_forbidden_attachment_phrase(rendered.body) is False

    @pytest.mark.parametrize(
        "forbidden_text",
        [
            "Özgeçmişim ekte yer almaktadır.",
            "Detaylı CV'mi ekteki dosyada bulabilirsiniz.",
            "Portfolyom ektedir, incelemenizi rica ederim.",
            "Please find attached my resume and portfolio.",
            "My CV is attached to this email for your review.",
            "See the attached PDF for details.",
        ],
    )
    def test_contains_forbidden_attachment_phrase_catches_tr_and_en_attachment_words(
        self, forbidden_text: str
    ) -> None:
        """LLM halüsinasyon görüp 'ekteki dosyada' veya 'please find attached' yazarsa,
        `contains_forbidden_attachment_phrase` bunu %100 yakalamalı.
        """
        assert contains_forbidden_attachment_phrase(forbidden_text) is True

    def test_fallback_template_handles_empty_company_name_and_missing_optional_links(self) -> None:
        """Şirket adı boş (`""`) gelse veya `cv_url_tr` tanımlanmamış olsa bile şablon
        çökmeden geçerli `portfolio_url` / `github_url` ile temiz bir metin üretmeli.
        """
        sparse_links = CandidateLinks(
            sender_name="Muhammed Enes BAL",
            portfolio_url="https://github.com/quantum-Blue",
        )
        tr_res = render_fallback_email(company_name="   ", language="tr", links=sparse_links)
        en_res = render_fallback_email(company_name="", language="invalid_lang", links=sparse_links)

        assert tr_res.language == "TR"
        assert "https://github.com/quantum-Blue" in tr_res.body
        assert "{{" not in tr_res.body and "}}" not in tr_res.body

        # Bilinmeyen dil kodu verildiğinde güvenli varsayılan olarak EN seçilmeli
        assert en_res.language == "EN"
        assert "https://github.com/quantum-Blue" in en_res.body


class TestDynamicRAGPromptBuilderTDDAndBugHunting:
    """`src/generator/prompt_builder.py` modülünün birim ve hata avcılığı testleri."""

    def setup_method(self) -> None:
        self.links = CandidateLinks(
            sender_name="Muhammed Enes BAL",
            cv_url_tr="https://drive.google.com/cv-tr",
            cv_url_en="https://drive.google.com/cv-en",
            portfolio_url="https://drive.google.com/portfolio-pdf",
            github_url="https://github.com/quantum-Blue",
            linkedin_url="https://linkedin.com/in/muhammedenes-bal",
        )
        self.sample_rag_chunks = [
            SearchResult(
                chunk_id="c1",
                text="Bitirme Projesi (Otonom İHA): GNSS-bağımsız ortamlarda LiDAR ve Stereo kamera füzyonu ile Fast-LIO2 + RTAB-MAP hibrit SLAM.",
                score=0.92,
                metadata={"language": "TR", "source": "cv_tr.pdf", "chunk_index": 0},
            ),
            SearchResult(
                chunk_id="c2",
                text="Teknofest İnsansız Su Altı Aracı (AUV): 6 iticili biyomimetik tasarım, YOLO ile hedef tespiti ve otonom rota kontrolü.",
                score=0.88,
                metadata={"language": "TR", "source": "portfolio.pdf", "chunk_index": 1},
            ),
        ]

    def test_build_application_prompt_combines_company_mission_rag_chunks_and_links(self) -> None:
        """`build_application_prompt` fonksiyonu:
        - Şirketin Hakkımızda/Misyon metnini,
        - RAG'den gelen spesifik aday projelerini (`Fast-LIO2`, `AUV`),
        - Hedef dile (`TR`) uygun CV/Portfolyo URL'lerini,
        - Kelime sınırı (`min_words` - `max_words`) ve 'dosya eki yasak' kurallarını
        hem `system_instruction` hem `user_prompt` içinde birleştirmeli.
        Ayrıca olası bir LLM kesintisi için hazır `fallback_email` nesnesini de içinde taşımalı!
        """
        built = build_application_prompt(
            company_name="Aselsan Otonom Sistemler",
            company_context="İnsansız kara ve hava araçları için GNSS-bağımsız navigasyon ve sensör füzyonu geliştiriyoruz.",
            language="TR",
            links=self.links,
            rag_results=self.sample_rag_chunks,
            min_word_count=70,
            max_word_count=250,
        )

        assert isinstance(built, BuiltPrompt)
        assert built.language == "TR"
        assert "Aselsan Otonom Sistemler" in built.user_prompt
        assert "GNSS-bağımsız navigasyon" in built.user_prompt
        # RAG projeleri prompt içinde yer almalı
        assert "Fast-LIO2" in built.user_prompt
        assert "Su Altı Aracı (AUV)" in built.user_prompt
        # Türkçe CV ve Portfolyo linkleri prompt'ta zorunlu link olarak geçmeli
        assert self.links.cv_url_tr in built.user_prompt
        assert self.links.portfolio_url in built.user_prompt
        # Sistem talimatında dosya eki (attachment) yasağı ve kelime sınırları belirtilmeli
        assert "70" in built.system_instruction and "250" in built.system_instruction
        assert "attachment" in built.system_instruction.lower() or "ek" in built.system_instruction.lower()
        # Hazır fallback e-postası da dolu ve geçerli olmalı
        assert built.fallback_email.subject != ""
        assert self.links.portfolio_url in built.fallback_email.body

    def test_empty_company_about_text_falls_back_to_professional_robotics_emphasis(self) -> None:
        """KRİTİK KABUL KRİTERİ (Faz 10 Bug Hunting):
        Şirketin 'Hakkımızda / Kariyer' metni boş (`""` veya `None` veya sadece boşluk) gelse bile
        prompt bozulmamalı; genel ama profesyonel bir Robotik / Otonom Sistemler / ROS 2 / Yapay Zekâ
        vurgusuna otomatik geçiş yapmalı (hem TR hem EN için)!
        """
        # 1. Türkçe boş şirket metni
        built_tr = build_application_prompt(
            company_name="Kuzgun Robotik",
            company_context="   \n\t  ",
            language="TR",
            links=self.links,
            rag_results=[],
        )
        assert "Kuzgun Robotik" in built_tr.user_prompt
        assert (
            "robotik" in built_tr.user_prompt.lower()
            and "otonom sistemler" in built_tr.user_prompt.lower()
        )
        assert "ROS 2" in built_tr.user_prompt or "SLAM" in built_tr.user_prompt
        assert built_tr.fallback_email.body != ""

        # 2. İngilizce boş şirket metni
        built_en = build_application_prompt(
            company_name="Apex Autonomy Inc",
            company_context="",
            language="EN",
            links=self.links,
            rag_results=[],
        )
        assert "Apex Autonomy Inc" in built_en.user_prompt
        assert (
            "robotics" in built_en.user_prompt.lower()
            and "autonomous systems" in built_en.user_prompt.lower()
        )
        assert self.links.cv_url_en in built_en.user_prompt
        assert self.links.portfolio_url in built_en.user_prompt

    def test_prompt_builder_queries_vector_store_automatically_when_provided(self) -> None:
        """`PromptBuilder` sınıfına bir `vector_store` nesnesi verildiğinde, şirket metnini
        kullanarak RAG veritabanını otomatik sorgulamalı ve dönen parçaları prompta eklemeli.
        """
        class DummyVectorStore:
            def __init__(self, chunks: list[SearchResult]) -> None:
                self.chunks = chunks
                self.last_query: str = ""

            def query(self, query_text: str, top_k: int = 4, language: str | None = None) -> list[SearchResult]:
                _ = (top_k, language)
                self.last_query = query_text
                return self.chunks

        dummy_store = DummyVectorStore(self.sample_rag_chunks)
        builder = PromptBuilder(
            links=self.links,
            vector_store=dummy_store,  # type: ignore[arg-type]
            top_k=3,
        )

        built = builder.build_for_company(
            company_name=" Havelsan ",
            company_context="Otonom insansız su altı ve hava araçları simülasyonu.",
            language="TR",
        )

        assert "Otonom insansız su altı" in dummy_store.last_query
        assert "Fast-LIO2" in built.user_prompt
        assert "Havelsan" in built.user_prompt

    def test_giant_company_context_is_safely_truncated_to_prevent_token_overflow(self) -> None:
        """HATA AVCILIĞI:
        Kazınan şirket sayfası 100.000 karakterlik devasa bir metin içeriyorsa,
        `build_application_prompt` bunu `max_company_context_chars` sınırında güvenli biçimde
        kırparak LLM token limitinin aşılmasını önlemeli.
        """
        giant_context = "Robotics SLAM ROS2 autonomy " * 5000  # ~140.000 karakter
        built = build_application_prompt(
            company_name="MegaCorp",
            company_context=giant_context,
            language="EN",
            links=self.links,
            max_company_context_chars=1500,
        )
        assert len(built.company_context_used) <= 1520
        assert len(built.user_prompt) < 6000
