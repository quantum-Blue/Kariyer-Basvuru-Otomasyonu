"""Metin Parçalama (Semantic Text Chunker) ve Dil/Kaynak Metadata Modülü (src/rag/chunker.py).

Bu modülün amacı:
1. PDF'ten çıkarılan uzun CV ve Portfolyo metinlerini, embedding modelinin ve vektör
   veritabanının en yüksek doğrulukla arayabileceği boyutlarda (`chunk_size`) parçalara bölmek.
2. Parçalama yaparken kelimeleri ASLA ortadan bölmemek (Örn. "Fast-LIO2" kelimesini "Fast-" ve "LIO2"
   diye ikiye ayırmamak) ve cümleler arası bağlamın kopmaması için her parçanın sonundan bir miktar
   kelimeyi (`chunk_overlap`) bir sonraki parçanın başına aktarmak.
3. Boşluksuz aşırı uzun metinlerde (örn. 1200 karakterlik tek satır URL veya hash) sonsuz döngüye
   girmeden güvenli dilimleme (`_slice_spaceless_token`) yapmak.
4. Her parçanın dilini (`TR` veya `EN`) tespit edip kaynak dosya adıyla birlikte metadata'ya eklemektir.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Any

from src.rag.pdf_loader import PDFDocument
from src.utils.logger import get_logger

logger = get_logger(__name__)

# Desteklenen diller
_SUPPORTED_LANGUAGES = {"TR", "EN"}

# Yalnızca Türkçede bulunan ayırt edici harfler (Bunlardan 2+ adet varsa metin kesin Türkçedir)
_TURKISH_UNIQUE_CHARS = set("ğĞşŞıİ")
# Türkçede sık kullanılan diğer noktalı harfler
_TURKISH_EXTENDED_CHARS = set("çÇöÖüÜ")

# Özel harf içermeyen kısa Türkçe metinleri de yakalayabilmek için Türkçe CV anahtar kelimeleri
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

# İngilizce CV anahtar kelimeleri
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
    """Vektör veritabanına (ChromaDB) kaydedilmeye hazır zenginleştirilmiş metin parçası."""

    chunk_id: str                                      # Örn: "cv_tr.pdf::chunk_0"
    text: str                                          # Parçanın metin içeriği
    metadata: dict[str, Any] = field(default_factory=dict)  # Kaynak, dil, indeks ve karakter sayısı


def detect_language(text: str) -> str:
    """Metnin dilini deterministik bir puanlama algoritmasıyla 'TR' veya 'EN' olarak tespit eder.

    Nasıl Çalışır?
    1. Metinde `ğ, Ğ, ş, Ş, ı, İ` gibi sadece Türkçeye özgü harflerden en az 2 tane varsa doğrudan 'TR' döner.
    2. Yoksa Türkçe bağlaç/terim sayısı ile İngilizce bağlaç/terim sayısını ağırlıklı olarak karşılaştırır.

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

    # Ağırlıklı skor hesabı
    tr_score = (unique_tr_count * 3) + extended_tr_count + (tr_word_hits * 2)
    en_score = en_word_hits * 2

    if tr_score > en_score and tr_score > 0:
        return "TR"
    return "EN"


def _slice_spaceless_token(token: str, chunk_size: int, chunk_overlap: int) -> list[str]:
    """Boşluk içermeyen ve tek başına `chunk_size` sınırından uzun olan devasa bir kelimeyi/URL'yi
    sonsuz döngüye girmeden `chunk_size - chunk_overlap` adımlarıyla güvenli şekilde dilimler.
    """
    slices: list[str] = []
    # Her adımda ilerlenecek net karakter sayısı (chunk_size > chunk_overlap olduğu garanti altındadır)
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
    """Bir önceki parçanın (chunk) sonundaki kelimelerden, `chunk_overlap` karakter sınırını
    aşmayacak kadarını seçip bir sonraki parçanın başına devreder.

    Kritik Güvenlik Kontrolleri:
    1. Seçilen overlap kelimelerinin toplam uzunluğu `chunk_overlap` değerini aşamaz.
    2. Seçilen overlap kelimeleri + yeni eklenecek kelimenin uzunluğu (`next_word_len`) toplamda
       `chunk_size` sınırını aşamaz (aşarsa yeni kelime parçaya sığmazdı).
    3. Sonsuz döngüyü önlemek için bir önceki parçadaki kelimelerin tamamı overlap olarak kopyalanamaz;
       en az 1 kelimelik ilerleme (forward progress) zorunludur.
    """
    if chunk_overlap <= 0 or not words:
        return []

    selected: list[str] = []
    current_len = 0

    # Mevcut parçanın sonundan başa doğru kelimeleri tek tek dene
    for word in reversed(words):
        added_len = len(word) if not selected else len(word) + 1
        if current_len + added_len > chunk_overlap:
            break
        # Yeni eklenecek kelimeyle birlikte toplam uzunluk chunk_size'ı aşmamalı
        projected_total = current_len + added_len + 1 + next_word_len
        if projected_total > chunk_size:
            break
        selected.append(word)
        current_len += added_len

    # Sondan başa topladığımız için kelime sırasını tekrar düzelt
    selected.reverse()

    # İlerleme garantisi: Overlap hiçbir zaman önceki parçanın tüm kelimelerini alamaz
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
    """Verilen metni kelime sınırlarını koruyarak anlamlı parçalara (`TextChunk`) böler.

    Adım Adım İşleyiş:
    1. `chunk_size`, `chunk_overlap` ve `language` parametrelerinin geçerliliğini doğrular.
    2. Metni boşluklardan kelimelere (`words`) ayırır.
    3. Kelimeleri sırayla `current_words` tamponuna (buffer) ekler:
       - Eğer tek bir kelime `chunk_size`'dan uzunsa (boşluksuz dev URL/metin), tamponu boşaltır ve
         `_slice_spaceless_token` ile o uzun diziyi dilimler.
       - Eğer kelime mevcut tampona sığıyorsa (`<= chunk_size`) ekler.
       - Sığmıyorsa mevcut tamponu bir parça (chunk) olarak kaydeder ve `_compute_overlap_words` ile
         örtüşen son kelimeleri yeni tamponun başına koyar.
    4. Üretilen her parçaya kaynak, dil, sıra numarası ve karakter sayısı metadata'sını ekler.

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
    # 1. Sınır değer doğrulamaları
    if chunk_size <= 0:
        raise ChunkingError(f"chunk_size pozitif olmalıdır, alınan: {chunk_size}")
    if chunk_overlap < 0:
        raise ChunkingError(f"chunk_overlap negatif olamaz, alınan: {chunk_overlap}")
    if chunk_overlap >= chunk_size:
        raise ChunkingError(
            f"chunk_overlap ({chunk_overlap}), chunk_size ({chunk_size}) değerinden küçük olmalıdır."
        )

    # 2. Dil parametresini doğrula veya otomatik tespit et
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

    # Boş metin verilmişse hata fırlatmak yerine boş liste döndür
    if not text or not text.strip():
        return []

    clean_source = (source or "unknown").strip() or "unknown"
    words = text.split()
    if not words:
        return []

    raw_chunks: list[str] = []
    current_words: list[str] = []
    current_len = 0

    # 3. Kelime bazlı kayan pencere (sliding window) döngüsü
    for word in words:
        word_len = len(word)

        # Durum A: Tek bir kelime/dizi (boşluksuz string) tek başına chunk_size'dan büyükse
        if word_len > chunk_size:
            if current_words:
                raw_chunks.append(" ".join(current_words))
                current_words = []
                current_len = 0
            raw_chunks.extend(_slice_spaceless_token(word, chunk_size, chunk_overlap))
            continue

        # Durum B: Kelime mevcut parçaya eklendiğinde chunk_size sınırını aşmıyorsa ekle
        candidate_len = word_len if not current_words else current_len + 1 + word_len
        if candidate_len <= chunk_size:
            current_words.append(word)
            current_len = candidate_len
        else:
            # Durum C: Sınır aşılıyorsa mevcut parçayı kapat ve overlap kelimeleriyle yeni parça başlat
            raw_chunks.append(" ".join(current_words))
            overlap_words = _compute_overlap_words(
                current_words,
                chunk_overlap=chunk_overlap,
                next_word_len=word_len,
                chunk_size=chunk_size,
            )
            current_words = overlap_words + [word]
            current_len = len(" ".join(current_words))

    # Döngü bittiğinde tamponda kalan son kelimeleri de son parça olarak ekle
    if current_words:
        raw_chunks.append(" ".join(current_words))

    # 4. Her parçayı zenginleştirilmiş TextChunk modeline dönüştür
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
        # Dışarıdan ek metadata verilmişse, çekirdek (core) alanları ezmesine izin vermeden ekle
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
    """Faz 2'de üretilen `PDFDocument` nesnesini doğrudan alıp dosya adı ve sayfa sayısı
    bilgisiyle birlikte parçalara (`TextChunk`) ayırır.
    """
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
