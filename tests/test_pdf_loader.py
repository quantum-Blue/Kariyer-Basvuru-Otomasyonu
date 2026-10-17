"""Faz 2 TDD Birim ve Hata Avcılığı (Bug Hunting) Testleri:
PDF Metin Çıkarıcı ve UTF-8 Ön İşleme (src/rag/pdf_loader.py).
"""

from pathlib import Path
import pymupdf
import pytest

from src.rag.pdf_loader import (
    PDFDocument,
    PDFLoadError,
    extract_text_from_pdf,
    normalize_pdf_text,
)


def _create_sample_pdf(
    pdf_path: Path,
    pages_text: list[str],
    password: str | None = None,
) -> Path:
    """Testler için tam UTF-8 (Türkçe karakter) destekli gerçek PDF dosyası üretir."""
    doc = pymupdf.open()
    for text in pages_text:
        page = doc.new_page()
        if text:
            rect = pymupdf.Rect(40, 40, 555, 780)
            page.insert_htmlbox(rect, f"<p>{text}</p>")
    if password:
        doc.save(
            str(pdf_path),
            encryption=pymupdf.PDF_ENCRYPT_AES_256,
            owner_pw=password,
            user_pw=password,
        )
    else:
        doc.save(str(pdf_path))
    doc.close()
    return pdf_path


class TestPDFLoaderTDDAndBugHunting:
    """Faz 2 TDD ve Aktif Hata Avcılığı (Bug Hunting) test senaryoları."""

    def test_extract_turkish_and_english_text_preserves_utf8(self, tmp_path: Path) -> None:
        """Türkçe karakterler (ğ, ü, ş, i, ö, ç, İ, Ğ) ve teknik terimler bozulmadan çıkarılmalı."""
        pdf_file = _create_sample_pdf(
            tmp_path / "cv_tr.pdf",
            [
                "Muhammed Enes BAL - Bilgisayar Mühendisi",
                "Çekirdek Uzmanlıklar: Gömülü Sistemler, İnsansız Hava Araçları (İHA), Görüntü İşleme, ğüşıöçĞÜŞİÖÇ",
            ],
        )

        result = extract_text_from_pdf(pdf_file)

        assert isinstance(result, PDFDocument)
        assert result.page_count == 2
        assert len(result.pages) == 2
        assert result.source_path == pdf_file.resolve()
        assert "Muhammed Enes BAL" in result.text
        assert "Mühendisi" in result.text
        assert "İnsansız Hava Araçları (İHA)" in result.text
        assert "ğüşıöçĞÜŞİÖÇ" in result.text

    def test_uppercase_pdf_extension_is_accepted(self, tmp_path: Path) -> None:
        """Büyük harfli .PDF uzantısına sahip geçerli dosyalar reddedilmemeli."""
        pdf_file = _create_sample_pdf(
            tmp_path / "PORTFOLIO_EN.PDF",
            ["Autonomous Quadrotor UAV with Fast-LIO2 and RTAB-MAP SLAM"],
        )

        result = extract_text_from_pdf(pdf_file)
        assert "Fast-LIO2" in result.text
        assert result.page_count == 1

    def test_multi_page_pdf_skips_blank_intermediate_pages(self, tmp_path: Path) -> None:
        """Çok sayfalı PDF'te aradaki boş sayfalar metni bozmamalı."""
        pdf_file = _create_sample_pdf(
            tmp_path / "multi.pdf",
            ["Sayfa 1: ROS 2 Humble", "", "Sayfa 3: YOLOv12 ve RT-DETR"],
        )

        result = extract_text_from_pdf(pdf_file)
        assert result.page_count == 3
        assert len(result.pages) == 2
        assert "ROS 2 Humble" in result.text
        assert "YOLOv12 ve RT-DETR" in result.text

    def test_non_existent_file_raises_pdf_load_error(self, tmp_path: Path) -> None:
        """Var olmayan dosya yolu verildiğinde PDFLoadError fırlatılmalı."""
        missing_path = tmp_path / "olmayan_dosya.pdf"
        with pytest.raises(PDFLoadError, match="bulunamadı"):
            extract_text_from_pdf(missing_path)

    @pytest.mark.parametrize("empty_input", ["", "   "])
    def test_empty_path_string_raises_pdf_load_error(self, empty_input: str) -> None:
        """Boş string dosya yolu verildiğinde PDFLoadError fırlatılmalı."""
        with pytest.raises(PDFLoadError, match="boş olamaz"):
            extract_text_from_pdf(empty_input)

    def test_directory_with_pdf_name_raises_pdf_load_error(self, tmp_path: Path) -> None:
        """.pdf isimli bir klasör verildiğinde PDFLoadError fırlatılmalı."""
        fake_dir = tmp_path / "sahte_klasor.pdf"
        fake_dir.mkdir()

        with pytest.raises(PDFLoadError, match="klasör"):
            extract_text_from_pdf(fake_dir)

    @pytest.mark.parametrize("ext", [".txt", ".jpg", ".png", ".docx", ".md"])
    def test_non_pdf_extension_raises_pdf_load_error(self, tmp_path: Path, ext: str) -> None:
        """PDF olmayan (.txt, .jpg vb.) bir dosya verildiğinde PDFLoadError fırlatılmalı."""
        fake_file = tmp_path / f"document{ext}"
        fake_file.write_text("Merhaba dünya", encoding="utf-8")

        with pytest.raises(PDFLoadError, match="uzantı"):
            extract_text_from_pdf(fake_file)

    def test_zero_byte_pdf_raises_pdf_load_error(self, tmp_path: Path) -> None:
        """0 baytlık boş bir .pdf dosyası verildiğinde PDFLoadError fırlatılmalı."""
        zero_pdf = tmp_path / "zero.pdf"
        zero_pdf.write_bytes(b"")

        with pytest.raises(PDFLoadError, match="0 bayt|boş"):
            extract_text_from_pdf(zero_pdf)

    def test_corrupted_pdf_bytes_raises_pdf_load_error(self, tmp_path: Path) -> None:
        """İçeriği bozuk (rastgele baytlar içeren) .pdf dosyası PDFLoadError fırlatmalı."""
        corrupted_pdf = tmp_path / "corrupted.pdf"
        corrupted_pdf.write_bytes(b"%PDF-1.7\nbozuk_icerik_rastgele_baytlar_\x00\xff\xfe")

        with pytest.raises(PDFLoadError, match="Bozuk|okunamayan"):
            extract_text_from_pdf(corrupted_pdf)

    def test_empty_text_pdf_raises_pdf_load_error(self, tmp_path: Path) -> None:
        """Metin içermeyen (boş sayfalı veya tamamen taranmış görsel) PDF verildiğinde PDFLoadError fırlatılmalı."""
        empty_pdf = _create_sample_pdf(tmp_path / "empty.pdf", ["", "   "])

        with pytest.raises(PDFLoadError, match="boş|metin"):
            extract_text_from_pdf(empty_pdf)

    def test_encrypted_pdf_raises_pdf_load_error(self, tmp_path: Path) -> None:
        """Şifreli PDF verildiğinde kontrollü PDFLoadError fırlatılmalı."""
        encrypted_pdf = _create_sample_pdf(
            tmp_path / "secret.pdf",
            ["Gizli CV İçeriği"],
            password="super_secret_password",
        )

        with pytest.raises(PDFLoadError, match="şifreli|encrypted"):
            extract_text_from_pdf(encrypted_pdf)

    def test_normalize_pdf_text_cleans_ligatures_and_decomposed_turkish_chars(self) -> None:
        """NFD ayrık Türkçe karakterler (örn. i + nokta), ligatürler (ﬁ, ﬂ) ve görünmez karakterler temizlenmeli."""
        raw = (
            "Geli\u0307s\u0327ti\u0307ri\u0307ci\u0307\u00a0\u00a0pro\ufb01l\u200b "
            "u\u0308reti\u0307mi\u0307 \ufb02ow \ufb00 \ufb03 \ufb04 "
            "I\u0307stanbul G\u0306U\u0308S\u0327O\u0308C\u0327\x00\u00ad\r\n\r\n\r\n\r\nROS 2"
        )
        cleaned = normalize_pdf_text(raw)

        assert "Geliştirici profil üretimi" in cleaned
        assert "flow ff ffi ffl" in cleaned
        assert "İstanbul ĞÜŞÖÇ" in cleaned
        assert "\u200b" not in cleaned
        assert "\x00" not in cleaned
        assert "\u00ad" not in cleaned
        assert "\r" not in cleaned
        assert "\n\n\n" not in cleaned
        assert normalize_pdf_text("") == ""
