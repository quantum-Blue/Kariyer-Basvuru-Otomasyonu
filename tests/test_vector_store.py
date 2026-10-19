"""Faz 4 TDD Birim ve Hata Avcılığı (Bug Hunting) Testleri:
Çok Dilli Embedding (Singleton) ve Kalıcı ChromaDB Vektör Veritabanı
(src/rag/embedder.py & src/rag/vector_store.py).
"""

from collections.abc import Sequence
import math
from pathlib import Path
import threading
from unittest.mock import MagicMock
import pytest

from src.rag.chunker import TextChunk
from src.rag.embedder import (
    EmbeddingError,
    MultilingualEmbedder,
)
from src.rag.vector_store import (
    SearchResult,
    VectorStoreError,
    VectorStoreManager,
)


class FakeMultilingualBackend:
    """Birim testlerde internet/model indirme bağımlılığını izole eden,
    ancak Türkçe ve İngilizce kavramları ortak semantik uzayda eşleştiren deterministik test modeli.
    """

    _CONCEPT_AXES: list[set[str]] = [
        {
            "slam", "rtab-map", "fast-lio2", "lio-sam", "cartographer",
            "haritalama", "mapping", "localization", "konumlandırma", "lidar",
        },
        {
            "iha", "uav", "quadrotor", "px4", "pixhawk",
            "uçuş", "flight", "gnss", "gps-denied", "nav2",
        },
        {
            "yolo", "yolov12", "rt-detr", "opencv", "görüntü",
            "vision", "kamera", "stereo", "detection", "tespit",
        },
        {
            "auv", "su", "altı", "underwater", "şamandıra",
            "biyomimetik", "derinlik", "itici",
        },
    ]

    def encode(
        self,
        sentences: Sequence[str],
        normalize_embeddings: bool = True,
        **_: object,
    ) -> list[list[float]]:
        vectors: list[list[float]] = []
        for text in sentences:
            lowered = text.lower()
            vec = [0.01, 0.01, 0.01, 0.01]
            for axis_idx, keywords in enumerate(self._CONCEPT_AXES):
                for kw in keywords:
                    if kw in lowered:
                        vec[axis_idx] += 1.0
            if normalize_embeddings:
                norm = math.sqrt(sum(v * v for v in vec)) or 1.0
                vec = [v / norm for v in vec]
            vectors.append(vec)
        return vectors


class TestMultilingualEmbedderSingleton:
    """MultilingualEmbedder Singleton ve hata yönetimi testleri."""

    def setup_method(self) -> None:
        MultilingualEmbedder.reset_instance()

    def teardown_method(self) -> None:
        MultilingualEmbedder.reset_instance()

    def test_singleton_loads_underlying_model_only_once_even_across_threads(self) -> None:
        """Model çoklu iş parçacığı (multi-thread) altında bile belleğe yalnızca bir kez yüklenmeli."""
        mock_loader = MagicMock(return_value=FakeMultilingualBackend())
        instances: list[MultilingualEmbedder] = []

        def worker() -> None:
            inst = MultilingualEmbedder.get_instance(
                model_name="sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
                model_loader=mock_loader,
            )
            instances.append(inst)

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(instances) == 8
        assert all(inst is instances[0] for inst in instances)
        assert mock_loader.call_count == 1

    def test_embed_texts_and_query_return_normalized_float_vectors(self) -> None:
        """Metin listesi ve tekil sorgu doğru boyutta float vektörlerine dönüştürülmeli."""
        emb = MultilingualEmbedder.get_instance(
            model_loader=lambda _: FakeMultilingualBackend()
        )

        batch_vecs = emb.embed_texts(["Fast-LIO2 ve RTAB-MAP SLAM", "Autonomous UAV flight"])
        assert len(batch_vecs) == 2
        assert len(batch_vecs[0]) == 4
        assert all(isinstance(x, float) for x in batch_vecs[0])

        query_vec = emb.embed_query("Hangi SLAM algoritmalarını biliyor?")
        assert len(query_vec) == 4

    def test_embed_invalid_inputs_and_loader_failures_raise_embedding_error(self) -> None:
        """Boş model adı, çöken model yükleyici veya boş sorgu metni EmbeddingError fırlatmalı."""
        with pytest.raises(EmbeddingError, match="model_name"):
            MultilingualEmbedder(model_name="   ")

        def broken_loader(_: str) -> object:
            raise RuntimeError("Disk dolu veya model indirilemedi")

        with pytest.raises(EmbeddingError, match="başlatılamadı"):
            MultilingualEmbedder(model_name="valid-model", model_loader=broken_loader)

        emb = MultilingualEmbedder.get_instance(
            model_loader=lambda _: FakeMultilingualBackend()
        )
        assert emb.embed_texts([]) == []

        with pytest.raises(EmbeddingError, match="boş"):
            emb.embed_query("   ")

        with pytest.raises(EmbeddingError, match="boş veya geçersiz"):
            emb.embed_texts(["Geçerli metin", "   "])


class TestVectorStoreChromaDB:
    """Kalıcı ChromaDB kayıt, mükerrer (duplicate) önleme ve çok dilli semantik arama testleri."""

    def setup_method(self) -> None:
        MultilingualEmbedder.reset_instance()
        self.embedder = MultilingualEmbedder.get_instance(
            model_loader=lambda _: FakeMultilingualBackend()
        )

    def teardown_method(self) -> None:
        MultilingualEmbedder.reset_instance()

    def test_cross_lingual_query_returns_both_turkish_and_english_cv_chunks(
        self, tmp_path: Path
    ) -> None:
        """Türkçe sorulan bir soruya ('Hangi SLAM algoritmalarını biliyor?') hem TR hem EN CV'den doğru parçalar dönmeli."""
        store = VectorStoreManager(
            persist_directory=tmp_path / "chroma_test",
            collection_name="test_cv_profile",
            embedder=self.embedder,
        )

        chunks = [
            TextChunk(
                chunk_id="cv_tr.pdf::chunk_0",
                text="Bitirme projesinde Fast-LIO2 ve RTAB-MAP SLAM algoritmaları ile 3B LiDAR haritalama yapıldı.",
                metadata={"source": "cv_tr.pdf", "language": "TR", "chunk_index": 0},
            ),
            TextChunk(
                chunk_id="cv_en.pdf::chunk_0",
                text="Implemented 3D localization and mapping using LIO-SAM, Cartographer SLAM and ROS 2.",
                metadata={"source": "cv_en.pdf", "language": "EN", "chunk_index": 0},
            ),
            TextChunk(
                chunk_id="cv_tr.pdf::chunk_1",
                text="Teknofest İnsansız Su Altı Aracı (AUV) biyomimetik tasarım ve derinlik kontrolü.",
                metadata={"source": "cv_tr.pdf", "language": "TR", "chunk_index": 1},
            ),
        ]

        added_count = store.upsert_chunks(chunks)
        assert added_count == 3
        assert store.count() == 3

        results = store.query("Hangi SLAM algoritmalarını biliyor?", top_k=2)

        assert len(results) == 2
        assert all(isinstance(r, SearchResult) for r in results)
        returned_languages = {r.metadata["language"] for r in results}
        returned_sources = {r.metadata["source"] for r in results}
        assert returned_languages == {"TR", "EN"}
        assert returned_sources == {"cv_tr.pdf", "cv_en.pdf"}
        assert any("Fast-LIO2" in r.text for r in results)
        assert any("LIO-SAM" in r.text for r in results)

    def test_uploading_same_pdf_twice_prevents_duplicate_records(
        self, tmp_path: Path
    ) -> None:
        """Aynı PDF iki kez yüklendiğinde (veya aynı batch içinde mükerrer parça geldiğinde) duplicate oluşmamalı."""
        store = VectorStoreManager(
            persist_directory=tmp_path / "chroma_dedup",
            collection_name="dedup_profile",
            embedder=self.embedder,
        )

        first_upload = [
            TextChunk(
                chunk_id="cv_tr.pdf::chunk_0",
                text="ROS 2, PX4 ve Gazebo ile otonom İHA uçuş kontrolü.",
                metadata={"source": "cv_tr.pdf", "language": "TR", "chunk_index": 0},
            ),
            TextChunk(
                chunk_id="cv_tr.pdf::chunk_1",
                text="YOLOv12 ve RT-DETR ile gerçek zamanlı nesne tespiti.",
                metadata={"source": "cv_tr.pdf", "language": "TR", "chunk_index": 1},
            ),
            # Aynı batch içinde mükerrer parça (Chroma DuplicateIDError vermemeli)
            TextChunk(
                chunk_id="cv_tr.pdf::chunk_0_dup",
                text="ROS 2, PX4   ve Gazebo ile otonom İHA uçuş kontrolü.",
                metadata={"source": "cv_tr.pdf", "language": "TR", "chunk_index": 0},
            ),
        ]

        processed = store.upsert_chunks(first_upload)
        assert processed == 2
        assert store.count() == 2

        # İkinci kez aynı PDF yüklendiğinde
        second_upload_same_content = [
            TextChunk(
                chunk_id="different_id_1",
                text="ROS 2, PX4 ve Gazebo ile otonom İHA uçuş kontrolü.",
                metadata={"source": "cv_tr.pdf", "language": "TR", "chunk_index": 0},
            ),
            TextChunk(
                chunk_id="different_id_2",
                text="YOLOv12 ve RT-DETR ile gerçek zamanlı nesne tespiti.",
                metadata={"source": "cv_tr.pdf", "language": "TR", "chunk_index": 1},
            ),
        ]
        store.upsert_chunks(second_upload_same_content)
        assert store.count() == 2

    def test_persistent_storage_survives_reinitialization(self, tmp_path: Path) -> None:
        """Veritabanı diskte kalıcı olmalı; yeni bir VectorStoreManager örneği açıldığında veriler korunmalı."""
        persist_dir = tmp_path / "chroma_persist"

        store1 = VectorStoreManager(
            persist_directory=persist_dir,
            collection_name="persistent_col",
            embedder=self.embedder,
        )
        store1.upsert_chunks(
            [
                TextChunk(
                    chunk_id="cv_en.pdf::chunk_0",
                    text="Stereo camera calibration and 3D point cloud processing with Open3D.",
                    metadata={"source": "cv_en.pdf", "language": "EN", "chunk_index": 0},
                )
            ]
        )
        assert store1.count() == 1

        store2 = VectorStoreManager(
            persist_directory=persist_dir,
            collection_name="persistent_col",
            embedder=self.embedder,
        )
        assert store2.count() == 1
        results = store2.query("stereo camera calibration", top_k=1)
        assert len(results) == 1
        assert "Open3D" in results[0].text

    def test_query_empty_collection_and_top_k_larger_than_count(self, tmp_path: Path) -> None:
        """Boş koleksiyon sorgulandığında [] dönmeli; top_k > mevcut kayıt sayısı olduğunda hata vermemeli."""
        store = VectorStoreManager(
            persist_directory=tmp_path / "chroma_empty",
            collection_name="empty_col",
            embedder=self.embedder,
        )

        assert store.upsert_chunks([]) == 0
        assert store.query("SLAM algoritmaları", top_k=5) == []

        store.upsert_chunks(
            [
                TextChunk(
                    chunk_id="c1",
                    text="Fast-LIO2 LiDAR SLAM",
                    metadata={"source": "cv.pdf", "language": "TR", "extra_none": None, "path_obj": Path("/tmp/a")},
                )
            ]
        )
        results = store.query("SLAM", top_k=10, language="tr")
        assert len(results) == 1
        assert "extra_none" not in results[0].metadata

    def test_invalid_vector_store_inputs_raise_vector_store_error(self, tmp_path: Path) -> None:
        """Geçersiz koleksiyon adı, boş sorgu, negatif top_k veya bozuk TextChunk VectorStoreError fırlatmalı."""
        with pytest.raises(VectorStoreError, match="collection_name"):
            VectorStoreManager(persist_directory=tmp_path / "c1", collection_name="   ", embedder=self.embedder)

        store = VectorStoreManager(
            persist_directory=tmp_path / "c2",
            collection_name="valid_col",
            embedder=self.embedder,
        )

        with pytest.raises(VectorStoreError, match="TextChunk"):
            store.upsert_chunks(["not_a_chunk"])  # type: ignore[list-item]

        with pytest.raises(VectorStoreError, match="Boş metin"):
            store.upsert_chunks([TextChunk(chunk_id="id1", text="   ", metadata={})])

        with pytest.raises(VectorStoreError, match="query_text"):
            store.query("   ")

        with pytest.raises(VectorStoreError, match="top_k"):
            store.query("ROS 2", top_k=0)

