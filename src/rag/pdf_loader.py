"""PDF Metin Çıkarıcı ve UTF-8 Ön İşleme Modülü (src/rag/pdf_loader.py).

Bu modülün amacı:
1. Adayın Türkçe ve İngilizce CV / Portfolyo PDF belgelerini `PyMuPDF` (`pymupdf`) ile okumak.
2. PDF oluşturucu araçların (LaTeX, Word, macOS Quartz, Canva vb.) sıklıkla bozduğu Türkçe
   karakterleri (`ğ, ü, ş, i, ö, ç, İ, Ğ`) ve bitişik harf ligatürlerini (`ﬁ -> fi`, `ﬂ -> fl`)
   temizleyerek standart UTF-8 NFC formuna dönüştürmek.
3. Bozuk, şifreli, 0 baytlık, yanlış uzantılı (`.jpg`, `.txt`) veya içi boş (taranmış resim)
   PDF'lerde dosya tanıtıcısını (file handle) açık bırakmadan `PDFLoadError` fırlatmaktır.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import re
import unicodedata

import pymupdf

from src.utils.logger import get_logger

logger = get_logger(__name__)

# PDF motorlarının tek bir özel sembol olarak kaydettiği tipografik ligatürlerin
# ve görünmez (zero-width) kontrol karakterlerinin temizleme tablosu
_LIGATURE_MAP = str.maketrans(
    {
        "\ufb00": "ff",   # ﬀ -> ff
        "\ufb01": "fi",   # ﬁ -> fi (Örn. "proﬁl" -> "profil")
        "\ufb02": "fl",   # ﬂ -> fl (Örn. "ﬂow" -> "flow")
        "\ufb03": "ffi",  # ﬃ -> ffi
        "\ufb04": "ffl",  # ﬄ -> ffl
        "\u200b": "",     # Sıfır genişlikli boşluk (Zero-width space) -> Sil
        "\u200c": "",     # Zero-width non-joiner -> Sil
        "\u200d": "",     # Zero-width joiner -> Sil
        "\ufeff": "",     # Byte Order Mark (BOM) -> Sil
        "\u00ad": "",     # Satır sonu yumuşak tire (Soft hyphen) -> Sil
        "\x00": "",       # Null byte -> Sil
        "\u00a0": " ",    # Bölünemez boşluk (Non-breaking space) -> Normal boşluk
    }
)


class PDFLoadError(Exception):
    """PDF okuma, doğrulama veya metin çıkarma sırasında oluşan kontrollü hata."""


@dataclass(frozen=True)
class PDFDocument:
    """Çıkarılan ve normalize edilen PDF belgesi veri modeli.

    `frozen=True` sayesinde oluşturulduktan sonra yanlışlıkla değiştirilmesi (mutation) engellenir.
    """

    source_path: Path                  # PDF dosyasının tam (mutlak) yolu
    text: str                          # Tüm sayfaların birleştirilmiş ve temizlenmiş tam metni
    page_count: int                    # PDF'teki toplam sayfa sayısı
    pages: list[str] = field(default_factory=list)  # Boş olmayan her sayfanın ayrı ayrı temiz metni


def normalize_pdf_text(raw_text: str) -> str:
    """Ham PDF metnini UTF-8 NFC standardına göre normalize eder ve temizler.

    Neden Özel Türkçe Normalizasyonu Yapıyoruz?
    - Bazı PDF üreticileri (özellikle LaTeX ve macOS) Türkçe harfleri NFD (ayrık) formda yazar:
      Örneğin `ü` harfini `u` + `\u0308` (iki nokta) olarak iki ayrı karakter şeklinde saklar.
    - Daha kritik olarak, küçük `i` harfini `i` (`U+0069`) + `\u0307` (üst nokta) olarak kaydeder.
      Standart `unicodedata.normalize("NFC", ...)` çağrısı `I\u0307` dizisini `İ` yaparken,
      küçük `i\u0307` dizisini birleştirmez! Bu da `"geliştirici"` kelimesinin aramalarda
      bulunamamasına yol açar. Bu fonksiyon önce `i\u0307 -> i` dönüşümü yaparak bu hatayı çözer.

    Args:
        raw_text: PDF sayfasından çıkarılan ham metin.

    Returns:
        Temizlenmiş ve UTF-8 NFC normalize edilmiş metin.
    """
    if not raw_text:
        return ""

    # Adım 1: Küçük 'i' + birleştirici üst nokta ('i\u0307') ikilisini standart tekil 'i' harfine indirge
    text = raw_text.replace("i\u0307", "i")

    # Adım 2: Ligatürleri (ﬁ, ﬂ vb.) düzelt ve görünmez kontrol karakterlerini (\u200b, \x00) sil
    text = text.translate(_LIGATURE_MAP)

    # Adım 3: Ayrık (NFD) Türkçe karakterleri (u\u0308 -> ü, I\u0307 -> İ, s\u0327 -> ş, g\u0306 -> ğ)
    # tekil standart Unicode (NFC) karakterlerine birleştir
    text = unicodedata.normalize("NFC", text)
    text = text.replace("i\u0307", "i")

    # Adım 4: Windows (\r\n) ve eski Mac (\r) satır sonlarını standart Unix (\n) satır sonuna çevir
    text = text.replace("\r\n", "\n").replace("\r", "\n")

    # Adım 5: Her satırın kendi içindeki çoklu boşluk/tab karakterlerini tek boşluğa indir
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.split("\n")]
    text = "\n".join(lines)

    # Adım 6: 3 veya daha fazla alt alta boş satırı en fazla 1 boş satıra (\n\n - paragraf ayrımı) indir
    text = re.sub(r"\n{3,}", "\n\n", text)

    return text.strip()


def extract_text_from_pdf(pdf_path: str | Path) -> PDFDocument:
    """Verilen PDF dosyasından tüm sayfaların metnini UTF-8 korumalı olarak çıkarır.

    Güvenlik ve Hata Kontrolleri:
    1. Boş yol, var olmayan dosya veya dosya yerine klasör verilmesi kontrol edilir.
    2. Dosya uzantısının `.pdf` (büyük/küçük harf duyarsız) olduğu doğrulanır.
    3. 0 baytlık boş dosyalar daha PyMuPDF'e gönderilmeden reddedilir.
    4. Şifreli (encrypted) PDF'ler ve metin içermeyen (taranmış görüntü) PDF'ler yakalanır.
    5. `try...finally: doc.close()` ile hata oluşsa bile dosyanın işletim sisteminde kilitli
       kalması (resource leak) kesin olarak engellenir.

    Args:
        pdf_path: Okunacak PDF dosyasının yolu.

    Returns:
        Normalize edilmiş tam metni ve sayfa bazlı metinleri içeren `PDFDocument`.

    Raises:
        PDFLoadError: Yukarıdaki doğrulama adımlarından herhangi biri başarısız olduğunda.
    """
    # 1. Girdi boş mu kontrolü
    if pdf_path is None or str(pdf_path).strip() == "":
        raise PDFLoadError("PDF dosya yolu boş olamaz.")

    file_path = Path(pdf_path).resolve()

    # 2. Dosya sistemde mevcut mu ve klasör mü kontrolü
    if not file_path.exists():
        raise PDFLoadError(f"PDF dosyası bulunamadı: {file_path}")

    if file_path.is_dir():
        raise PDFLoadError(f"Belirtilen yol bir dosya değil, klasör: {file_path}")

    # 3. Uzantı kontrolü (.txt, .jpg, .docx vb. dosyaları erken aşamada reddet)
    if file_path.suffix.lower() != ".pdf":
        raise PDFLoadError(
            f"Geçersiz dosya uzantısı ('{file_path.suffix}'). Yalnızca '.pdf' dosyaları desteklenir: {file_path}"
        )

    # 4. Dosya boyutu (0 bayt) kontrolü
    try:
        file_size = file_path.stat().st_size
    except OSError as exc:
        raise PDFLoadError(f"PDF dosya bilgisi okunamadı ({file_path}): {exc}") from exc

    if file_size == 0:
        raise PDFLoadError(f"PDF dosyası boş (0 bayt): {file_path}")

    # 5. PyMuPDF ile dosyayı aç (Bozuk bayt içeriyorsa PDFLoadError'a çevir)
    try:
        doc = pymupdf.open(str(file_path))
    except Exception as exc:
        raise PDFLoadError(f"Bozuk veya okunamayan PDF dosyası ({file_path}): {exc}") from exc

    # 6. Dosya içeriğini oku ve iş bitince her koşulda (finally) belgeyi kapat
    try:
        # Şifreli PDF kontrolü
        if doc.needs_pass or doc.is_encrypted:
            raise PDFLoadError(f"PDF dosyası şifreli (encrypted), okunamıyor: {file_path}")

        page_count = doc.page_count
        if page_count == 0:
            raise PDFLoadError(f"PDF dosyasında hiç sayfa yok: {file_path}")

        normalized_pages: list[str] = []
        for page_index in range(page_count):
            page = doc.load_page(page_index)
            # Sayfadaki ham metni çıkar ve UTF-8 normalizasyonundan geçir
            raw_page_text = page.get_text("text")
            cleaned_page_text = normalize_pdf_text(raw_page_text)
            # Sadece metin içeren sayfaları listeye ekle (aradaki boş sayfaları atla)
            if cleaned_page_text:
                normalized_pages.append(cleaned_page_text)

        full_text = "\n\n".join(normalized_pages).strip()
        # Eğer tüm sayfalar boşsa veya PDF sadece taranmış resimden (OCR'sız) oluşuyorsa hata ver
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
