"""PDF Metin Çıkarıcı ve UTF-8 Ön İşleme Modülü (src/rag/pdf_loader.py).

Türkçe ve İngilizce CV/Portfolyo PDF belgelerinden metinleri UTF-8 karakter
kaybı olmadan (`ğ, ü, ş, i, ö, ç, İ, Ğ`) çıkarır, PDF ligatürlerini ve ayrık
(NFD) Unicode karakterlerini normalize eder, şifreli/bozuk/boş dosyalarda
kontrollü `PDFLoadError` fırlatır.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import re
import unicodedata

import pymupdf

from src.utils.logger import get_logger

logger = get_logger(__name__)

# PDF motorlarının ürettiği tipografik ligatürlerin standart ASCII karşılıkları
_LIGATURE_MAP = str.maketrans(
    {
        "\ufb00": "ff",
        "\ufb01": "fi",
        "\ufb02": "fl",
        "\ufb03": "ffi",
        "\ufb04": "ffl",
        "\u200b": "",  # Zero-width space
        "\u200c": "",  # Zero-width non-joiner
        "\u200d": "",  # Zero-width joiner
        "\ufeff": "",  # BOM
        "\u00ad": "",  # Soft hyphen
        "\x00": "",    # Null byte
        "\u00a0": " ",  # Non-breaking space
    }
)


class PDFLoadError(Exception):
    """PDF okuma, doğrulama veya metin çıkarma sırasında oluşan kontrollü hata."""


@dataclass(frozen=True)
class PDFDocument:
    """Çıkarılan ve normalize edilen PDF belgesi modeli."""

    source_path: Path
    text: str
    page_count: int
    pages: list[str] = field(default_factory=list)


def normalize_pdf_text(raw_text: str) -> str:
    """Ham PDF metnini UTF-8 NFC standardına göre normalize eder ve temizler.

    Özellikle LaTeX/macOS kaynaklı PDF'lerde küçük 'i' harfinin 'i + U+0307'
    (combining dot above) olarak ayrık yazılmasını, NFD ayrık Türkçe harfleri
    (`ğ, ü, ş, ö, ç, İ, Ğ`) ve PDF ligatürlerini (`ﬁ, ﬂ`) düzeltir.

    Args:
        raw_text: PDF sayfasından çıkarılan ham metin.

    Returns:
        Temizlenmiş ve UTF-8 NFC normalize edilmiş metin.
    """
    if not raw_text:
        return ""

    # 1. Küçük 'i' + combining dot above ('i\u0307') dizilimini tekil 'i' yap
    text = raw_text.replace("i\u0307", "i")

    # 2. Ligatürleri ve görünmez kontrol karakterlerini temizle
    text = text.translate(_LIGATURE_MAP)

    # 3. NFD ayrık Türkçe karakterleri (örn. u\u0308 -> ü, I\u0307 -> İ, s\u0327 -> ş) NFC'ye birleştir
    text = unicodedata.normalize("NFC", text)
    text = text.replace("i\u0307", "i")

    # 4. Satır sonlarını standartlaştır (\r\n veya \r -> \n)
    text = text.replace("\r\n", "\n").replace("\r", "\n")

    # 5. Her satır içindeki ardışık yatay boşlukları tek boşluğa indir ve sağ/sol kırp
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.split("\n")]
    text = "\n".join(lines)

    # 6. 3 veya daha fazla ardışık boş satırı en fazla çift satır sonuna (\n\n) indir
    text = re.sub(r"\n{3,}", "\n\n", text)

    return text.strip()


def extract_text_from_pdf(pdf_path: str | Path) -> PDFDocument:
    """Verilen PDF dosyasından tüm sayfaların metnini UTF-8 korumalı olarak çıkarır.

    Args:
        pdf_path: Okunacak PDF dosyasının yolu.

    Returns:
        Normalize edilmiş tam metni ve sayfa bazlı metinleri içeren `PDFDocument`.

    Raises:
        PDFLoadError: Dosya bulunamadığında, klasör olduğunda, `.pdf` uzantılı
            olmadığında, şifreli/bozuk olduğunda veya metin içermediğinde.
    """
    if pdf_path is None or str(pdf_path).strip() == "":
        raise PDFLoadError("PDF dosya yolu boş olamaz.")

    file_path = Path(pdf_path).resolve()

    if not file_path.exists():
        raise PDFLoadError(f"PDF dosyası bulunamadı: {file_path}")

    if file_path.is_dir():
        raise PDFLoadError(f"Belirtilen yol bir dosya değil, klasör: {file_path}")

    if file_path.suffix.lower() != ".pdf":
        raise PDFLoadError(
            f"Geçersiz dosya uzantısı ('{file_path.suffix}'). Yalnızca '.pdf' dosyaları desteklenir: {file_path}"
        )

    try:
        file_size = file_path.stat().st_size
    except OSError as exc:
        raise PDFLoadError(f"PDF dosya bilgisi okunamadı ({file_path}): {exc}") from exc

    if file_size == 0:
        raise PDFLoadError(f"PDF dosyası boş (0 bayt): {file_path}")

    try:
        doc = pymupdf.open(str(file_path))
    except Exception as exc:
        raise PDFLoadError(f"Bozuk veya okunamayan PDF dosyası ({file_path}): {exc}") from exc

    try:
        if doc.needs_pass or doc.is_encrypted:
            raise PDFLoadError(f"PDF dosyası şifreli (encrypted), okunamıyor: {file_path}")

        page_count = doc.page_count
        if page_count == 0:
            raise PDFLoadError(f"PDF dosyasında hiç sayfa yok: {file_path}")

        normalized_pages: list[str] = []
        for page_index in range(page_count):
            page = doc.load_page(page_index)
            raw_page_text = page.get_text("text")
            cleaned_page_text = normalize_pdf_text(raw_page_text)
            if cleaned_page_text:
                normalized_pages.append(cleaned_page_text)

        full_text = "\n\n".join(normalized_pages).strip()
        if not full_text:
            raise PDFLoadError(
                f"PDF dosyasından metin çıkarılamadı (boş içerik veya taranmış resim PDF): {file_path}"
            )

        logger.info(
            "PDF başarıyla okundu: %s (%d sayfa, %d karakter)",
            file_path.name,
            page_count,
            len(full_text),
        )
        return PDFDocument(
            source_path=file_path,
            text=full_text,
            page_count=page_count,
            pages=normalized_pages,
        )
    finally:
        doc.close()

