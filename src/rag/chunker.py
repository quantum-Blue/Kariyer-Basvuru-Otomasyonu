"""Metin Parçalama (Semantic Text Chunker) ve Dil/Kaynak Metadata Modülü (src/rag/chunker.py).

Çıkarılan CV ve Portfolyo metinlerini kelimeleri ortadan bölmeden, `chunk_size`
ve `chunk_overlap` sınırlarına sadık kalarak anlamlı parçalara (`TextChunk`)
ayırır. Boşluksuz aşırı uzun tek satırlık dizilerde (spaceless strings) sonsuz
döngüye girmeden güvenli dilimleme yapar ve her parçaya `TR` / `EN` dil bilgisi
ile kaynak metadata'sı ekler.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Any

from src.rag.pdf_loader import PDFDocument
from src.utils.logger import get_logger

logger = get_logger(__name__)

_SUPPORTED_LANGUAGES = {"TR", "EN"}
_TURKISH_UNIQUE_CHARS = set("ğĞşŞıİ")
_TURKISH_EXTENDED_CHARS = set("çÇöÖüÜ")
_TURKISH_KEYWORDS = {
    "ve",
    "ile",
    "için",
    "olarak",
    "üzerine",
    "mühendisi",
    "mühendislik",
    "bilgisayar",
    "otonom",
    "sistemler",
    "sistemleri",
    "projesi",
    "geliştirilmiştir",
    "eğitim",
    "deneyim",
    "üniversitesi",
    "bölümü",
    "türkiye",
}
_ENGLISH_KEYWORDS = {
    "the",
    "and",
    "with",
    "for",
    "from",
    "developed",
    "engineered",
    "autonomous",
    "systems",
    "computer",
    "engineering",
    "university",
    "experience",
    "education",
    "projects",
    "using",
}


class ChunkingError(Exception):
    """Metin parçalama veya parametre doğrulama sırasında oluşan kontrollü hata."""


@dataclass(frozen=True)
class TextChunk:
    """Vektör veritabanına kaydedilmeye hazır zenginleştirilmiş metin parçası."""

    chunk_id: str
    text: str
    metadata: dict[str, Any] = field(default_factory=dict)


def detect_language(text: str) -> str:
    """Metnin dilini deterministik olarak 'TR' veya 'EN' şeklinde tespit eder.

    Args:
        text: Analiz edilecek metin.

    Returns:
        'TR' veya 'EN' dil kodu.
    """
    if not text or not text.strip():
        return "EN"

    unique_tr_count = sum(1 for ch in text if ch in _TURKISH_UNIQUE_CHARS)
    if unique_tr_count >= 2:
        return "TR"

    extended_tr_count = sum(1 for ch in text if ch in _TURKISH_EXTENDED_CHARS)
    tokens = re.findall(r"\b\w+\b", text.lower())
    tr_word_hits = sum(1 for token in tokens if token in _TURKISH_KEYWORDS)
    en_word_hits = sum(1 for token in tokens if token in _ENGLISH_KEYWORDS)

    tr_score = (unique_tr_count * 3) + extended_tr_count + (tr_word_hits * 2)
    en_score = en_word_hits * 2

    if tr_score > en_score and tr_score > 0:
        return "TR"
    return "EN"


def _slice_spaceless_token(token: str, chunk_size: int, chunk_overlap: int) -> list[str]:
    """Boşluk içermeyen ve chunk_size'dan uzun olan tekil bir diziyi güvenli şekilde dilimler."""
    slices: list[str] = []
    step = chunk_size - chunk_overlap
    start = 0
    token_len = len(token)

    while start < token_len:
        end = min(start + chunk_size, token_len)
        slices.append(token[start:end])
        if end >= token_len:
            break
        start += step

    return slices


def _compute_overlap_words(
    words: list[str],
    chunk_overlap: int,
    next_word_len: int,
    chunk_size: int,
) -> list[str]:
    """Mevcut chunk sonundaki kelimelerden overlap sınırını aşmayan alt listeyi seçer."""
    if chunk_overlap <= 0 or not words:
        return []

    selected: list[str] = []
    current_len = 0

    for word in reversed(words):
        added_len = len(word) if not selected else len(word) + 1
        if current_len + added_len > chunk_overlap:
            break
        # Yeni eklenecek kelimeyle birlikte chunk_size'ı aşmayacağından emin ol
        projected_total = current_len + added_len + 1 + next_word_len
        if projected_total > chunk_size:
            break
        selected.append(word)
        current_len += added_len

    selected.reverse()
    # Sonsuz döngüyü önlemek için overlap hiçbir zaman tüm kelimeleri kopyalamamalı
    if len(selected) >= len(words) and len(words) > 0:
        selected = selected[1:]

    return selected


def chunk_text(
    text: str,
    source: str = "unknown",
    language: str | None = None,
    chunk_size: int = 600,
    chunk_overlap: int = 100,
    extra_metadata: dict[str, Any] | None = None,
) -> list[TextChunk]:
    """Verilen metni kelime sınırlarını koruyarak parçalara (chunks) böler.

    Args:
        text: Parçalanacak kaynak metin.
        source: Kaynak dosya adı veya tanımlayıcısı (örn. 'cv_tr.pdf').
        language: Açık dil kodu ('TR' veya 'EN'). None ise otomatik tespit edilir.
        chunk_size: Parça başına maksimum karakter sayısı.
        chunk_overlap: Ardışık parçalar arasındaki maksimum örtüşme karakter sayısı.
        extra_metadata: Her parçanın metadata sözlüğüne eklenecek ek anahtar/değerler.

    Returns:
        `TextChunk` nesnelerinden oluşan liste.

    Raises:
        ChunkingError: `chunk_size`, `chunk_overlap` veya `language` geçersiz olduğunda.
    """
    if chunk_size <= 0:
        raise ChunkingError(f"chunk_size pozitif olmalıdır, alınan: {chunk_size}")
    if chunk_overlap < 0:
        raise ChunkingError(f"chunk_overlap negatif olamaz, alınan: {chunk_overlap}")
    if chunk_overlap >= chunk_size:
        raise ChunkingError(
            f"chunk_overlap ({chunk_overlap}), chunk_size ({chunk_size}) değerinden küçük olmalıdır."
        )

    resolved_lang: str
    if language is not None:
        normalized_lang = language.strip().upper()
        if normalized_lang not in _SUPPORTED_LANGUAGES:
            raise ChunkingError(
                f"Desteklenmeyen dil kodu (language): '{language}'. Geçerli değerler: {sorted(_SUPPORTED_LANGUAGES)}"
            )
        resolved_lang = normalized_lang
    else:
        resolved_lang = detect_language(text)

    if not text or not text.strip():
        return []

    clean_source = (source or "unknown").strip() or "unknown"
    words = text.split()
    if not words:
        return []

    raw_chunks: list[str] = []
    current_words: list[str] = []
    current_len = 0

    for word in words:
        word_len = len(word)

        # Durum 1: Tek bir kelime/dizi (boşluksuz string) tek başına chunk_size'dan büyükse
        if word_len > chunk_size:
            if current_words:
                raw_chunks.append(" ".join(current_words))
                current_words = []
                current_len = 0
            raw_chunks.extend(_slice_spaceless_token(word, chunk_size, chunk_overlap))
            continue

        # Durum 2: Kelime mevcut parçaya sığıyorsa ekle
        candidate_len = word_len if not current_words else current_len + 1 + word_len
        if candidate_len <= chunk_size:
            current_words.append(word)
            current_len = candidate_len
        else:
            # Mevcut parçayı kaydet ve overlap kelimeleriyle yeni parça başlat
            raw_chunks.append(" ".join(current_words))
            overlap_words = _compute_overlap_words(
                current_words,
                chunk_overlap=chunk_overlap,
                next_word_len=word_len,
                chunk_size=chunk_size,
            )
            current_words = overlap_words + [word]
            current_len = len(" ".join(current_words))

    if current_words:
        raw_chunks.append(" ".join(current_words))

    total_chunks = len(raw_chunks)
    result: list[TextChunk] = []

    for idx, chunk_str in enumerate(raw_chunks):
        metadata: dict[str, Any] = {
            "source": clean_source,
            "language": resolved_lang,
            "chunk_index": idx,
            "total_chunks": total_chunks,
            "char_count": len(chunk_str),
        }
        if extra_metadata:
            for k, v in extra_metadata.items():
                if k not in metadata:
                    metadata[k] = v

        result.append(
            TextChunk(
                chunk_id=f"{clean_source}::chunk_{idx}",
                text=chunk_str,
                metadata=metadata,
            )
        )

    logger.debug(
        "Metin parçalandı: kaynak=%s, dil=%s, parça_sayısı=%d",
        clean_source,
        resolved_lang,
        total_chunks,
    )
    return result


def chunk_pdf_document(
    document: PDFDocument,
    language: str | None = None,
    chunk_size: int = 600,
    chunk_overlap: int = 100,
) -> list[TextChunk]:
    """Bir `PDFDocument` nesnesini kaynak adı ve sayfa sayısı metadata'sıyla parçalar."""
    if not isinstance(document, PDFDocument):
        raise ChunkingError("Geçerli bir PDFDocument nesnesi verilmelidir.")

    return chunk_text(
        text=document.text,
        source=document.source_path.name,
        language=language,
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        extra_metadata={"page_count": document.page_count},
    )
