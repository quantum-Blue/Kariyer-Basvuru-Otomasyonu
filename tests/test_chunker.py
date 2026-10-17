"""Faz 3 TDD Birim ve Hata Avcılığı (Bug Hunting) Testleri:
Metin Parçalama (Chunking) ve Dil/Kaynak Metadata Zenginleştirme (src/rag/chunker.py).
"""

from pathlib import Path
import pytest

from src.rag.chunker import (
    ChunkingError,
    TextChunk,
    chunk_pdf_document,
    chunk_text,
    detect_language,
)
from src.rag.pdf_loader import PDFDocument


class TestTextChunkerTDDAndBugHunting:
    """Faz 3 TDD ve Aktif Hata Avcılığı (Bug Hunting) test senaryoları."""

    def test_chunk_text_respects_size_and_does_not_split_words(self) -> None:
        """Normal metin parçalanırken chunk_size aşılmamalı ve kelimeler ortadan bölünmemeli."""
        words = [
            "Otonom", "İHA", "sistemlerinde", "Fast-LIO2", "ve", "RTAB-MAP",
            "tabanlı", "hibrit", "SLAM", "mimarisi", "geliştirilmiştir.",
            "GNSS-bağımsız", "ortamlarda", "LiDAR", "ve", "Stereo", "kamera",
            "sensör", "füzyonu", "ile", "gerçek", "zamanlı", "konumlandırma",
            "ve", "A*", "kurtarma", "algoritması", "başarıyla", "test", "edilmiştir.",
        ]
        sample_text = " ".join(words)

        chunks = chunk_text(
            text=sample_text,
            source="cv_tr.pdf",
            chunk_size=90,
            chunk_overlap=20,
        )

        assert len(chunks) >= 3
        for idx, chunk in enumerate(chunks):
            assert isinstance(chunk, TextChunk)
            assert len(chunk.text) <= 90
            assert chunk.metadata["source"] == "cv_tr.pdf"
            assert chunk.metadata["language"] == "TR"
            assert chunk.metadata["chunk_index"] == idx
            assert chunk.metadata["total_chunks"] == len(chunks)
            assert chunk.metadata["char_count"] == len(chunk.text)
            for token in chunk.text.split():
                assert token in words

    def test_chunk_overlap_preserves_context_between_adjacent_chunks(self) -> None:
        """Ardışık iki parça arasında overlap (örtüşen kelimeler) bulunmalı."""
        text = (
            "Birinci cümle robotik kontrol sistemleri üzerinedir. "
            "İkinci cümle derin öğrenme ve bilgisayarlı görü anlatır. "
            "Üçüncü cümle su altı otonom araçları hakkındadır."
        )
        chunks = chunk_text(text=text, source="portfolio.pdf", chunk_size=85, chunk_overlap=25)

        assert len(chunks) >= 2
        first_words = set(chunks[0].text.split())
        second_words = set(chunks[1].text.split())
        assert len(first_words.intersection(second_words)) >= 1

    def test_detect_language_identifies_turkish_and_english_accurately(self) -> None:
        """Türkçe ve İngilizce CV metinlerinin dili doğru tespit edilmeli (özel harfli ve anahtar kelimeli)."""
        tr_text = "Bilgisayar Mühendisi. İnsansız hava araçları, gömülü sistemler ve görüntü işleme projeleri."
        tr_keyword_text = "Bilgisayar ve otonom sistemler üzerine eğitim ve deneyim."
        en_text = "Computer Engineer. Developed autonomous quadrotor navigation using ROS 2, PX4 and stereo vision."

        assert detect_language(tr_text) == "TR"
        assert detect_language(tr_keyword_text) == "TR"
        assert detect_language(en_text) == "EN"
        assert detect_language("   ") == "EN"

    def test_explicit_language_parameter_overrides_auto_detection(self) -> None:
        """Kullanıcı açıkça language='EN' veya 'tr' verdiğinde metadata büyük harf normuna uymalı."""
        chunks = chunk_text(
            text="ROS 2 Nav2 PX4 Gazebo OpenCV PyTorch",
            source="resume_en.pdf",
            language="en",
            chunk_size=100,
            chunk_overlap=10,
        )
        assert len(chunks) == 1
        assert chunks[0].metadata["language"] == "EN"

    def test_very_short_single_sentence_text_creates_single_chunk(self) -> None:
        """Çok kısa metinler (1 cümle veya 1 kelime) sistemi kırmamalı, tek chunk dönmeli."""
        short_text = "ROS 2 ve C++ uzmanı."
        chunks = chunk_text(text=short_text, source="short.pdf", chunk_size=500, chunk_overlap=50)

        assert len(chunks) == 1
        assert chunks[0].text == short_text
        assert chunks[0].metadata["chunk_index"] == 0
        assert chunks[0].metadata["total_chunks"] == 1

    def test_extremely_long_spaceless_string_does_not_hang_or_crash(self) -> None:
        """Boşluksuz aşırı uzun tek satırlık metinler (örn. 1200 karakterlik string) sonsuz döngüye girmemeli."""
        spaceless = "A" * 1200
        chunks = chunk_text(
            text=spaceless,
            source="spaceless.pdf",
            chunk_size=300,
            chunk_overlap=50,
        )

        assert len(chunks) >= 4
        for chunk in chunks:
            assert 0 < len(chunk.text) <= 300
        reconstructed_len = len(chunks[0].text) + sum(len(c.text) - 50 for c in chunks[1:])
        assert reconstructed_len == 1200

    def test_mixed_normal_words_and_giant_spaceless_url_in_middle(self) -> None:
        """Normal metin içindeki çok uzun boşluksuz bir URL/hash öncesi ve sonrasındaki kelimeleri kaybetmemeli."""
        giant_url = "https://github.com/enes/" + ("x" * 240)
        mixed_text = f"Başlangıç cümlesi burada. {giant_url} Bitiş cümlesi tamamlandı."

        chunks = chunk_text(
            text=mixed_text,
            source="mixed.pdf",
            chunk_size=100,
            chunk_overlap=20,
        )

        assert len(chunks) >= 5
        for chunk in chunks:
            assert len(chunk.text) <= 100
        assert "Başlangıç cümlesi burada." in chunks[0].text
        assert "Bitiş cümlesi tamamlandı." in chunks[-1].text

    def test_boundary_word_exact_chunk_size_and_max_overlap(self) -> None:
        """Kelime uzunluğu tam chunk_size'a eşit olduğunda ve overlap=chunk_size-1 iken sınır aşımı olmamalı."""
        exact_word = "B" * 50
        text = f"Kısa giriş {exact_word} kısa çıkış"

        chunks = chunk_text(text=text, source="boundary.pdf", chunk_size=50, chunk_overlap=49)
        assert len(chunks) >= 3
        for chunk in chunks:
            assert len(chunk.text) <= 50
        assert any(c.text == exact_word for c in chunks)

    def test_extra_metadata_cannot_overwrite_protected_core_keys(self) -> None:
        """extra_metadata ile gelen anahtarlar core metadata'yı (language, chunk_index vb.) ezememeli."""
        chunks = chunk_text(
            text="Otonom su altı aracı kontrol algoritmaları.",
            source="auv.pdf",
            language="TR",
            extra_metadata={"language": "HACKED", "custom_tag": "teknofest"},
        )
        assert chunks[0].metadata["language"] == "TR"
        assert chunks[0].metadata["custom_tag"] == "teknofest"

    def test_empty_or_whitespace_text_returns_empty_list(self) -> None:
        """Boş veya sadece boşluk/satır sonu içeren metin verildiğinde boş liste dönmeli."""
        assert chunk_text("", source="empty.pdf") == []
        assert chunk_text("   \n\t\n  ", source="empty.pdf") == []

    @pytest.mark.parametrize(
        "chunk_size,chunk_overlap",
        [
            (0, 0),
            (-100, 10),
            (200, -5),
            (200, 200),
            (200, 250),
        ],
    )
    def test_invalid_chunk_parameters_raise_chunking_error(
        self, chunk_size: int, chunk_overlap: int
    ) -> None:
        """Geçersiz chunk_size veya chunk_overlap sınırlarında ChunkingError fırlatılmalı."""
        with pytest.raises(ChunkingError):
            chunk_text(
                text="Geçerli bir test metni.",
                source="test.pdf",
                chunk_size=chunk_size,
                chunk_overlap=chunk_overlap,
            )

    def test_invalid_language_code_raises_chunking_error(self) -> None:
        """Desteklenmeyen bir dil kodu (örn. 'DE' veya 'FR') verildiğinde ChunkingError fırlatılmalı."""
        with pytest.raises(ChunkingError, match="dil|language"):
            chunk_text(
                text="Sample text",
                source="test.pdf",
                language="FR",
            )

    def test_chunk_pdf_document_integrates_with_pdf_loader_model_and_rejects_invalid_input(self) -> None:
        """PDFDocument nesnesi doğrudan parçalanabilmeli; geçersiz tipte ChunkingError fırlatmalı."""
        doc = PDFDocument(
            source_path=Path("/tmp/muhammed_enes_cv_tr.pdf"),
            text=(
                "Muhammed Enes BAL\n\n"
                "Bitirme Projesi: GNSS-bağımsız ortamlarda LiDAR ve Stereo kamera füzyonu ile "
                "3B haritalama ve otonom İHA navigasyonu."
            ),
            page_count=1,
            pages=["..."],
        )

        chunks = chunk_pdf_document(doc, chunk_size=80, chunk_overlap=15)

        assert len(chunks) >= 2
        assert chunks[0].metadata["source"] == "muhammed_enes_cv_tr.pdf"
        assert chunks[0].metadata["language"] == "TR"
        assert chunks[0].metadata["page_count"] == 1

        with pytest.raises(ChunkingError):
            chunk_pdf_document("not_a_pdf_document")  # type: ignore[arg-type]
