"""Kalıcı Vektör Veritabanı Yöneticisi — ChromaDB (src/rag/vector_store.py).

Parçalanmış CV/Portfolyo metinlerini (`TextChunk`) çok dilli embedding vektörleriyle
birlikte yerel `ChromaDB` koleksiyonuna kaydeder, içerik özeti (SHA-256) tabanlı
deterministik kimliklerle mükerrer (duplicate) kayıtları engeller ve çapraz dilli
(TR <-> EN) semantik arama yapar.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
import hashlib
from pathlib import Path
from typing import Any

import chromadb
from chromadb.config import Settings as ChromaClientSettings

from src.rag.chunker import TextChunk
from src.rag.embedder import EmbeddingError, MultilingualEmbedder
from src.utils.logger import get_logger

logger = get_logger(__name__)


class VectorStoreError(Exception):
    """ChromaDB işlemleri sırasında oluşan kontrollü vektör veritabanı hatası."""


@dataclass(frozen=True)
class SearchResult:
    """Semantik vektör sorgusundan dönen eşleşme modeli."""

    chunk_id: str
    text: str
    score: float
    metadata: dict[str, Any] = field(default_factory=dict)


def _build_deterministic_chunk_id(chunk: TextChunk) -> str:
    """Aynı PDF/kaynak ve aynı metin içeriği için her zaman aynı benzersiz ID'yi üretir.

    Böylece aynı PDF farklı zamanlarda veya farklı `chunk_id` önekleriyle iki kez
    yüklense bile ChromaDB `upsert` mekanizması kaydı üstüne yazar ve mükerrer
    (duplicate) kayıt oluşmasını %100 engeller.
    """
    source = str(chunk.metadata.get("source", "unknown")).strip() or "unknown"
    normalized_content = " ".join(chunk.text.strip().split())
    digest = hashlib.sha256(f"{source}||{normalized_content}".encode("utf-8")).hexdigest()[:20]
    return f"{source}::{digest}"


def _sanitize_metadata(metadata: dict[str, Any]) -> dict[str, str | int | float | bool]:
    """ChromaDB'nin kabul ettiği ilkel tiplere (str, int, float, bool) göre metadata'yı temizler."""
    sanitized: dict[str, str | int | float | bool] = {}
    for key, value in metadata.items():
        if value is None:
            continue
        if isinstance(value, (str, int, float, bool)):
            sanitized[str(key)] = value
        else:
            sanitized[str(key)] = str(value)
    return sanitized


class VectorStoreManager:
    """Yerel ve kalıcı ChromaDB vektör deposu yöneticisi."""

    def __init__(
        self,
        persist_directory: str | Path = "data/chroma_db",
        collection_name: str = "candidate_profile",
        embedder: MultilingualEmbedder | None = None,
    ) -> None:
        if not collection_name or not str(collection_name).strip():
            raise VectorStoreError("Koleksiyon adı (collection_name) boş olamaz.")

        self.persist_directory = Path(persist_directory).resolve()
        self.collection_name = collection_name.strip()
        self.embedder = embedder or MultilingualEmbedder.get_instance()

        try:
            self.persist_directory.mkdir(parents=True, exist_ok=True)
            self._client = chromadb.PersistentClient(
                path=str(self.persist_directory),
                settings=ChromaClientSettings(anonymized_telemetry=False),
            )
            self._collection = self._client.get_or_create_collection(
                name=self.collection_name,
                metadata={"hnsw:space": "cosine"},
            )
        except Exception as exc:
            raise VectorStoreError(
                f"ChromaDB başlatılamadı (dizin={self.persist_directory}, koleksiyon={self.collection_name}): {exc}"
            ) from exc

    def count(self) -> int:
        """Koleksiyondaki toplam kayıtlı parça (chunk) sayısını döndürür."""
        try:
            return int(self._collection.count())
        except Exception as exc:
            raise VectorStoreError(f"Kayıt sayısı alınamadı: {exc}") from exc

    def upsert_chunks(self, chunks: Sequence[TextChunk]) -> int:
        """Metin parçalarını mükerrer kayıt oluşturmadan (idempotent upsert) veritabanına yazar.

        Args:
            chunks: Kaydedilecek `TextChunk` listesi.

        Returns:
            İşlenen benzersiz parça sayısı.

        Raises:
            VectorStoreError: Geçersiz girdi veya veritabanı yazma hatasında.
        """
        if not chunks:
            return 0

        deduped_by_id: dict[str, TextChunk] = {}
        for idx, chunk in enumerate(chunks):
            if not isinstance(chunk, TextChunk):
                raise VectorStoreError(f"Geçersiz chunk nesnesi (indeks {idx}): TextChunk bekleniyordu.")
            if not chunk.text or not chunk.text.strip():
                raise VectorStoreError(f"Boş metin içeren chunk kaydedilemez (indeks {idx}).")

            det_id = _build_deterministic_chunk_id(chunk)
            deduped_by_id[det_id] = chunk

        ids = list(deduped_by_id.keys())
        documents = [c.text.strip() for c in deduped_by_id.values()]
        metadatas = [_sanitize_metadata(c.metadata) for c in deduped_by_id.values()]

        try:
            embeddings = self.embedder.embed_texts(documents)
            self._collection.upsert(
                ids=ids,
                documents=documents,
                embeddings=embeddings,
                metadatas=metadatas,
            )
            logger.info(
                "%d benzersiz metin parçası ChromaDB koleksiyonuna ('%s') upsert edildi.",
                len(ids),
                self.collection_name,
            )
            return len(ids)
        except EmbeddingError as exc:
            raise VectorStoreError(f"Chunk vektörleştirme hatası: {exc}") from exc
        except Exception as exc:
            raise VectorStoreError(f"ChromaDB upsert hatası: {exc}") from exc

    def query(
        self,
        query_text: str,
        top_k: int = 4,
        language: str | None = None,
    ) -> list[SearchResult]:
        """Verilen sorgu metnine en yakın CV/Portfolyo parçalarını semantik olarak getirir.

        Args:
            query_text: Aranacak soru, teknoloji veya şirket misyonu metni.
            top_k: Döndürülecek maksimum eşleşme sayısı.
            language: Opsiyonel dil filtresi ('TR' veya 'EN'). None ise tüm dillerde arar.

        Returns:
            Benzerlik sırasına göre `SearchResult` listesi.
        """
        if not isinstance(query_text, str) or not query_text.strip():
            raise VectorStoreError("Sorgu metni (query_text) boş olamaz.")
        if top_k <= 0:
            raise VectorStoreError(f"top_k pozitif bir tam sayı olmalıdır, alınan: {top_k}")

        total_items = self.count()
        if total_items == 0:
            return []

        n_results = min(top_k, total_items)
        where_filter: dict[str, Any] | None = None
        if language is not None:
            where_filter = {"language": language.strip().upper()}

        try:
            query_embedding = self.embedder.embed_query(query_text.strip())
            query_kwargs: dict[str, Any] = {
                "query_embeddings": [query_embedding],
                "n_results": n_results,
                "include": ["documents", "metadatas", "distances"],
            }
            if where_filter:
                query_kwargs["where"] = where_filter

            raw = self._collection.query(**query_kwargs)
        except EmbeddingError as exc:
            raise VectorStoreError(f"Sorgu vektörleştirme hatası: {exc}") from exc
        except Exception as exc:
            raise VectorStoreError(f"ChromaDB sorgu hatası: {exc}") from exc

        ids_list = (raw.get("ids") or [[]])[0]
        docs_list = (raw.get("documents") or [[]])[0]
        metas_list = (raw.get("metadatas") or [[]])[0]
        dists_list = (raw.get("distances") or [[]])[0]

        results: list[SearchResult] = []
        for cid, doc, meta, dist in zip(ids_list, docs_list, metas_list, dists_list):
            # Cosine distance -> benzerlik skoru (1.0 - distance)
            similarity = round(max(0.0, 1.0 - float(dist)), 4)
            results.append(
                SearchResult(
                    chunk_id=str(cid),
                    text=str(doc),
                    score=similarity,
                    metadata=dict(meta) if meta else {},
                )
            )

        return results

