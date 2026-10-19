"""Çok Dilli (Multilingual) Embedding Modülü — Singleton Pattern (src/rag/embedder.py).

`sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2` modelini belleğe
yalnızca bir kez (Thread-Safe Singleton) yükler ve Türkçe/İngilizce metinleri
ortak semantik vektör uzayına dönüştürür.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
import threading
from typing import Any

from src.utils.logger import get_logger

logger = get_logger(__name__)

DEFAULT_EMBEDDING_MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"


class EmbeddingError(Exception):
    """Model yükleme veya metin vektörleştirme sırasında oluşan kontrollü hata."""


def _default_sentence_transformer_loader(model_name: str) -> Any:
    """Varsayılan SentenceTransformer model yükleyicisi."""
    try:
        from sentence_transformers import SentenceTransformer

        return SentenceTransformer(model_name)
    except Exception as exc:
        raise EmbeddingError(f"Embedding modeli yüklenemedi ('{model_name}'): {exc}") from exc


class MultilingualEmbedder:
    """Thread-safe Singleton yapısında çok dilli metin vektörleştirici."""

    _instance: MultilingualEmbedder | None = None
    _lock: threading.Lock = threading.Lock()

    def __init__(
        self,
        model_name: str = DEFAULT_EMBEDDING_MODEL,
        model_loader: Callable[[str], Any] | None = None,
    ) -> None:
        if not model_name or not str(model_name).strip():
            raise EmbeddingError("Embedding model adı (model_name) boş olamaz.")

        self.model_name = model_name.strip()
        loader = model_loader or _default_sentence_transformer_loader

        try:
            logger.info("Çok dilli embedding modeli belleğe yükleniyor: %s", self.model_name)
            self._model = loader(self.model_name)
        except EmbeddingError:
            raise
        except Exception as exc:
            raise EmbeddingError(f"Embedding modeli başlatılamadı ('{self.model_name}'): {exc}") from exc

    @classmethod
    def get_instance(
        cls,
        model_name: str = DEFAULT_EMBEDDING_MODEL,
        model_loader: Callable[[str], Any] | None = None,
    ) -> MultilingualEmbedder:
        """Modeli belleğe yalnızca bir kez yükleyerek tekil (Singleton) örneği döndürür."""
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls(model_name=model_name, model_loader=model_loader)
        return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        """Test izolasyonu için mevcut Singleton örneğini sıfırlar."""
        with cls._lock:
            cls._instance = None

    def embed_texts(self, texts: Sequence[str]) -> list[list[float]]:
        """Verilen metin listesini normalize edilmiş float vektörlerine dönüştürür.

        Args:
            texts: Vektörleştirilecek metin listesi.

        Returns:
            Her metin için `list[float]` içeren 2B vektör listesi.

        Raises:
            EmbeddingError: Metinlerden biri boş/geçersiz olduğunda veya model hata verdiğinde.
        """
        if not texts:
            return []

        cleaned_texts: list[str] = []
        for idx, item in enumerate(texts):
            if not isinstance(item, str) or not item.strip():
                raise EmbeddingError(f"Vektörleştirilecek metin boş veya geçersiz (indeks {idx}).")
            cleaned_texts.append(item.strip())

        try:
            raw_embeddings = self._model.encode(
                cleaned_texts,
                normalize_embeddings=True,
            )
            return [[float(val) for val in vec] for vec in raw_embeddings]
        except Exception as exc:
            raise EmbeddingError(f"Metin vektörleştirme hatası: {exc}") from exc

    def embed_query(self, query: str) -> list[float]:
        """Tek bir arama sorgusunu vektörleştirir."""
        if not isinstance(query, str) or not query.strip():
            raise EmbeddingError("Arama sorgusu (query) boş olamaz.")
        vectors = self.embed_texts([query.strip()])
        return vectors[0]

