"""Çok Dilli (Multilingual) Embedding Modülü — Singleton Pattern (src/rag/embedder.py).

Bu modülün amacı:
1. Türkçe ve İngilizce cümleleri aynı 384 boyutlu matematiksel vektör uzayına yerleştiren
   `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2` modelini yönetmek.
2. **Singleton Tasarım Deseni (Design Pattern):** Derin öğrenme modeli ~470 MB boyutundadır ve
   RAM'e yüklenmesi birkaç saniye sürer. Uygulama çalışırken her sorguda modeli tekrar tekrar
   RAM'e yüklememek (bellek sızıntısı ve yavaşlığı önlemek) için model belleğe YALNIZCA BİR KEZ
   yüklenir ve tüm modüller aynı örneği (`_instance`) paylaşır.
3. **Bağımlılık Enjeksiyonu (Dependency Injection):** Birim testlerde internete çıkıp model
   indirmemek için `model_loader` parametresiyle sahte (mock/fake) model enjekte edilebilir.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
import threading
from typing import Any

from src.utils.logger import get_logger

logger = get_logger(__name__)

# Varsayılan çok dilli (50+ dil, Türkçe ve İngilizce dahil) açık kaynak embedding modeli
DEFAULT_EMBEDDING_MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"


class EmbeddingError(Exception):
    """Model yükleme veya metin vektörleştirme sırasında oluşan kontrollü hata."""


def _default_sentence_transformer_loader(model_name: str) -> Any:
    """Gerçek çalışma zamanında `sentence_transformers.SentenceTransformer` modelini yükler."""
    try:
        from sentence_transformers import SentenceTransformer

        return SentenceTransformer(model_name)
    except Exception as exc:
        raise EmbeddingError(f"Embedding modeli yüklenemedi ('{model_name}'): {exc}") from exc


class MultilingualEmbedder:
    """Thread-safe (Çoklu iş parçacığı güvenli) Singleton yapısında çok dilli metin vektörleştirici."""

    # Sınıf düzeyinde tekil örnek (Singleton) referansı ve eşzamanlılık kilidi (Lock)
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
        # Eğer test için özel bir model_loader verilmemişse gerçek SentenceTransformer yükleyicisini kullan
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
        """Modeli belleğe yalnızca bir kez yükleyerek tekil (Singleton) örneği döndürür.

        Double-Checked Locking (Çift Kontrollü Kilitleme) Tekniği:
        1. İlk `if cls._instance is None:` kontrolü kilit (`_lock`) almadan hızlıca bakar.
        2. Eğer örnek henüz oluşturulmamışsa `with cls._lock:` ile diğer thread'leri bekletir ve
           içeride ikinci kez kontrol edip modeli yalnızca tek bir kez oluşturur.
        """
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls(model_name=model_name, model_loader=model_loader)
        return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        """Birim testleri arasında temiz başlangıç yapabilmek için mevcut Singleton örneğini sıfırlar."""
        with cls._lock:
            cls._instance = None

    def embed_texts(self, texts: Sequence[str]) -> list[list[float]]:
        """Verilen metin listesini (batch) normalize edilmiş float vektörlerine dönüştürür.

        Neden `normalize_embeddings=True` Kullanıyoruz?
        Vektörlerin uzunluğu (L2 normu) 1.0'a normalize edildiğinde, ChromaDB'deki kosinüs
        benzerliği (Cosine Similarity) hesabı çok daha hızlı ve matematiksel olarak kararlı çalışır.

        Args:
            texts: Vektörleştirilecek metin listesi.

        Returns:
            Her metin için `list[float]` içeren 2 boyutlu vektör listesi.

        Raises:
            EmbeddingError: Metinlerden biri boş/geçersiz olduğunda veya model hata verdiğinde.
        """
        if not texts:
            return []

        # Girdi listesindeki her bir metnin geçerli ve dolu bir string olduğunu doğrula
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
            # Numpy dizilerini (ndarray) ChromaDB'nin doğrudan kabul ettiği standart Python float listesine çevir
            return [[float(val) for val in vec] for vec in raw_embeddings]
        except Exception as exc:
            raise EmbeddingError(f"Metin vektörleştirme hatası: {exc}") from exc

    def embed_query(self, query: str) -> list[float]:
        """Tek bir arama sorgusunu (örn. şirket vizyonu veya soru) vektörleştirir."""
        if not isinstance(query, str) or not query.strip():
            raise EmbeddingError("Arama sorgusu (query) boş olamaz.")
        vectors = self.embed_texts([query.strip()])
        return vectors[0]
