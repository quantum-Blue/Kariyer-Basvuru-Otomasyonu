"""🛡️ MILESTONE 1: Entegrasyon Denetimi (Faz 1 – 4 Uçtan Uca Kontrolü).

Bu entegrasyon testi modülü şu zinciri uçtan uca (End-to-End) denetler:
[Faz 1: Konfigürasyon & Logger] -> [Faz 2: PDF Okuma & UTF-8 Normalizasyon]
    -> [Faz 3: Kelime Korumalı Chunking & Dil Metadata] -> [Faz 4: ChromaDB Kayıt & Semantik Sorgulama]

Ayrıca:
- Bellek sızıntısı (Memory Leak - `tracemalloc`),
- Açık kalan dosya tanıtıcısı (Windows File Handle Lock),
- Disk yazma izinleri ve kalıcılık (Persistence),
- Hız darboğazları (Performance SLA) kontrollerini gerçekleştirir.
"""

from collections.abc import Sequence
import gc
import math
from pathlib import Path
import time
import tracemalloc

import pymupdf
import pytest

from src.config.settings import load_settings
from src.rag.chunker import chunk_pdf_document
from src.rag.embedder import MultilingualEmbedder
from src.rag.pdf_loader import extract_text_from_pdf
from src.rag.vector_store import VectorStoreManager
from src.utils.logger import setup_logging


class MilestoneSemanticBackend:
    """Milestone 1 uçtan uca entegrasyon testinde Türkçe ve İngilizce CV/Portfolyo
    kavramlarını 5 ana mühendislik ekseninde vektörleştiren deterministik çok dilli model.
    """

    # 5 ana uzmanlık ekseni:
    # 0: SLAM & Haritalama, 1: Otonom İHA & Uçuş, 2: Su Altı Aracı (AUV),
    # 3: Bilgisayarlı Görü & Derin Öğrenme, 4: Gömülü Sistemler & Edge AI
    _AXES: list[set[str]] = [
        {
            "slam", "fast-lio2", "rtab-map", "lio-sam", "cartographer",
            "haritalama", "mapping", "konumlandırma", "localization", "point cloud", "open3d",
        },
        {
            "iha", "uav", "quadrotor", "px4", "pixhawk", "gnss", "gps-denied",
            "nav2", "gazebo", "uçuş", "flight", "a*", "kurtarma",
        },
        {
            "auv", "su altı", "underwater", "biyomimetik", "biomimetic",
            "şamandıra", "buoy", "derinlik", "itici", "thruster",
        },
        {
            "yolo", "yolov12", "rt-detr", "opencv", "pytorch", "görüntü",
            "vision", "nesne tespiti", "object detection", "vit",
        },
        {
            "stm32", "raspberry pi", "ai hat", "edge ai", "gömülü",
            "embedded", "sensör füzyonu", "sensor fusion", "lidar", "stereo",
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
            # Sıfıra bölünmeyi önlemek için küçük taban değerleri
            vec = [0.01, 0.01, 0.01, 0.01, 0.01]
            for axis_idx, keywords in enumerate(self._AXES):
                for kw in keywords:
                    if kw in lowered:
                        vec[axis_idx] += 1.0
            if normalize_embeddings:
                norm = math.sqrt(sum(v * v for v in vec)) or 1.0
                vec = [v / norm for v in vec]
            vectors.append(vec)
        return vectors


def _build_realistic_candidate_pdfs(target_dir: Path) -> tuple[Path, Path]:
    """PROJECT_REPORT.md içindeki Muhammed Enes BAL profilini temel alan
    gerçekçi Türkçe CV ve İngilizce Portfolyo PDF dosyalarını üretir.
    """
    tr_cv_path = target_dir / "muhammed_enes_bal_cv_tr.pdf"
    en_portfolio_path = target_dir / "muhammed_enes_bal_portfolio_en.pdf"

    # 1. Türkçe CV PDF (2 Sayfa)
    doc_tr = pymupdf.open()
    page1_tr = doc_tr.new_page()
    page1_tr.insert_htmlbox(
        pymupdf.Rect(40, 40, 555, 780),
        (
            "<h1>Muhammed Enes BAL - Bilgisayar Mühendisi</h1>"
            "<p>Robotik, Otonom Sistemler ve Yapay Zekâ Mühendisi (2026 Mezunu, GANO: 3.27 / 4.00).</p>"
            "<p><b>Bitirme Projesi (Otonom İHA):</b> GNSS-bağımsız ortamlarda LiDAR ve Stereo kamera "
            "sensör füzyonu ile 3B haritalama, Fast-LIO2 ve RTAB-MAP tabanlı hibrit SLAM mimarisi "
            "ve A* kurtarma mekanizmalı otonom uçuş kontrolü geliştirilmiştir.</p>"
        ),
    )
    page2_tr = doc_tr.new_page()
    page2_tr.insert_htmlbox(
        pymupdf.Rect(40, 40, 555, 780),
        (
            "<p><b>Teknofest İnsansız Su Altı Aracı (AUV):</b> 6 iticili biyomimetik tasarım, "
            "su altı görüntü iyileştirme, YOLO ile hedef ve şamandıra tespiti, kapalı çevrim "
            "derinlik ve rota kontrolü gerçekleştirilmiştir.</p>"
            "<p><b>TÜBİTAK 2209-A Projesi:</b> Seviye-4 otonom kargo dağıtım aracı simülasyonu, "
            "kamera, LiDAR ve radar sensör füzyonu üzerine çalışılmıştır.</p>"
        ),
    )
    doc_tr.save(str(tr_cv_path))
    doc_tr.close()

    # 2. İngilizce Portfolyo PDF (2 Sayfa)
    doc_en = pymupdf.open()
    page1_en = doc_en.new_page()
    page1_en.insert_htmlbox(
        pymupdf.Rect(40, 40, 555, 780),
        (
            "<h1>Muhammed Enes BAL - Robotics & Autonomous Systems Portfolio</h1>"
            "<p><b>Autonomous Quadrotor Navigation:</b> Engineered GPS-denied localization and 3D point cloud "
            "mapping with LIO-SAM, Cartographer SLAM, ROS 2, PX4 Autopilot and Nav2 in Gazebo simulation.</p>"
        ),
    )
    page2_en = doc_en.new_page()
    page2_en.insert_htmlbox(
        pymupdf.Rect(40, 40, 555, 780),
        (
            "<p><b>Aerial AI & Computer Vision:</b> Real-time object detection from UAV imagery using "
            "hybrid YOLOv12 and RT-DETR architectures with PyTorch and OpenCV.</p>"
            "<p><b>Embedded Edge AI Hardware:</b> Deployed low-latency sensor fusion and deep learning "
            "models on Raspberry Pi 5 with AI HAT and STM32 microcontrollers.</p>"
        ),
    )
    doc_en.save(str(en_portfolio_path))
    doc_en.close()

    return tr_cv_path, en_portfolio_path


class TestMilestone1EndToEndRAG:
    """Faz 1 – Faz 4 arası tüm modüllerin uçtan uca entegrasyon ve kalite denetimi."""

    def setup_method(self) -> None:
        MultilingualEmbedder.reset_instance()

    def teardown_method(self) -> None:
        MultilingualEmbedder.reset_instance()

    def test_end_to_end_config_to_pdf_to_chunker_to_chromadb(
        self, tmp_path: Path
    ) -> None:
        """1. Konfigürasyon -> 2. Merkezi Logger -> 3. PDF Okuma -> 4. Chunking -> 5. ChromaDB Sorgulama
        zinciri gerçekçi TR ve EN CV/Portfolyo PDF'leriyle eksiksiz çalışmalı.
        """
        # Adım 1: Gerçek üretim settings.yaml dosyasını yükle
        project_root = Path(__file__).resolve().parent.parent.parent
        settings = load_settings(
            yaml_path=project_root / "config" / "settings.yaml",
            env_path=tmp_path / "missing.env",
        )
        assert settings.app.dry_run is True

        # Adım 2: Merkezi UTF-8 logger'ı geçici dizinde başlat
        log_file = tmp_path / "logs" / "milestone1.log"
        logger = setup_logging(
            log_file=log_file,
            level=settings.logging.level,
            logger_name="milestone1_audit",
        )
        logger.info("Milestone 1 entegrasyon denetimi başladı (Türkçe karakter kontrolü: ğüşıöçĞÜŞİÖÇ).")

        # Adım 3: Türkçe CV ve İngilizce Portfolyo PDF'lerini oluştur ve PyMuPDF ile oku
        pdf_dir = tmp_path / "cvs"
        pdf_dir.mkdir(parents=True, exist_ok=True)
        tr_pdf_path, en_pdf_path = _build_realistic_candidate_pdfs(pdf_dir)

        tr_doc = extract_text_from_pdf(tr_pdf_path)
        en_doc = extract_text_from_pdf(en_pdf_path)

        assert tr_doc.page_count == 2
        assert en_doc.page_count == 2
        assert "GNSS-bağımsız" in tr_doc.text
        assert "YOLOv12" in en_doc.text

        # Adım 4: Okunan PDF belgelerini anlam bütünlüğünü bozmadan parçalara (chunks) böl
        tr_chunks = chunk_pdf_document(tr_doc, chunk_size=220, chunk_overlap=40)
        en_chunks = chunk_pdf_document(en_doc, chunk_size=220, chunk_overlap=40)

        assert len(tr_chunks) >= 2
        assert len(en_chunks) >= 2
        assert all(c.metadata["language"] == "TR" for c in tr_chunks)
        assert all(c.metadata["language"] == "EN" for c in en_chunks)

        # Adım 5: Singleton Embedder ve kalıcı ChromaDB'ye kaydet
        embedder = MultilingualEmbedder.get_instance(
            model_name=settings.rag.embedding_model,
            model_loader=lambda _: MilestoneSemanticBackend(),
        )
        store = VectorStoreManager(
            persist_directory=tmp_path / "chroma_milestone1",
            collection_name=settings.rag.collection_name,
            embedder=embedder,
        )

        all_chunks = tr_chunks + en_chunks
        inserted = store.upsert_chunks(all_chunks)
        assert inserted == len(all_chunks)
        assert store.count() == len(all_chunks)

        # Adım 6: Aynı PDF'leri ikinci kez yükle ve mükerrer (duplicate) kayıt oluşmadığını kanıtla
        store.upsert_chunks(all_chunks)
        assert store.count() == len(all_chunks)

        # Adım 7: Çapraz dilli semantik sorgulama (Türkçe soru -> hem TR hem EN SLAM parçaları)
        slam_results = store.query(
            "Hangi SLAM ve 3B haritalama algoritmalarını biliyor?",
            top_k=3,
        )
        assert len(slam_results) == 3
        slam_combined_text = " ".join(r.text for r in slam_results)
        assert "Fast-LIO2" in slam_combined_text or "RTAB-MAP" in slam_combined_text
        assert "LIO-SAM" in slam_combined_text or "Cartographer" in slam_combined_text

        # Adım 8: Su altı aracı (AUV) sorgusu
        auv_results = store.query("Underwater autonomous vehicle biomimetic AUV", top_k=2)
        assert any("AUV" in r.text or "Su Altı" in r.text for r in auv_results)

        # Adım 9: Log dosyasının diske UTF-8 olarak yazıldığını doğrula
        for h in logger.handlers:
            h.flush()
        assert log_file.exists()
        assert "ğüşıöçĞÜŞİÖÇ" in log_file.read_text(encoding="utf-8")

    def test_memory_leak_file_locks_and_performance_sla_audit(
        self, tmp_path: Path
    ) -> None:
        """Bellek sızıntısı (tracemalloc), açık kalan dosya kilidi (Windows file lock)
        ve hız darboğazı (< 5.0 sn) denetimi.
        """
        start_time = time.perf_counter()

        pdf_dir = tmp_path / "perf_cvs"
        pdf_dir.mkdir(parents=True, exist_ok=True)
        tr_pdf, en_pdf = _build_realistic_candidate_pdfs(pdf_dir)

        mock_loader_calls = 0

        def counting_loader(_: str) -> MilestoneSemanticBackend:
            nonlocal mock_loader_calls
            mock_loader_calls += 1
            return MilestoneSemanticBackend()

        embedder = MultilingualEmbedder.get_instance(model_loader=counting_loader)
        store = VectorStoreManager(
            persist_directory=tmp_path / "chroma_perf",
            collection_name="perf_audit",
            embedder=embedder,
        )

        # Bellek izlemeyi başlat
        gc.collect()
        tracemalloc.start()
        snapshot_before_current, _ = tracemalloc.get_traced_memory()

        # Döngü içinde 5 kez PDF okuma -> Parçalama -> Upsert -> Sorgulama çalıştır
        for _ in range(5):
            doc_tr = extract_text_from_pdf(tr_pdf)
            doc_en = extract_text_from_pdf(en_pdf)
            chunks = chunk_pdf_document(doc_tr, chunk_size=250, chunk_overlap=40) + chunk_pdf_document(
                doc_en, chunk_size=250, chunk_overlap=40
            )
            # Singleton'ın tekrar çağrıldığında yeni model yüklemediğini doğrula
            _ = MultilingualEmbedder.get_instance(model_loader=counting_loader)
            store.upsert_chunks(chunks)
            _ = store.query("GNSS-bağımsız İHA ve SLAM", top_k=2)

        gc.collect()
        snapshot_after_current, _ = tracemalloc.get_traced_memory()
        tracemalloc.stop()

        elapsed_sec = time.perf_counter() - start_time
        memory_growth_bytes = snapshot_after_current - snapshot_before_current

        # Kriter 1: Model belleğe yalnızca 1 kez yüklenmiş olmalı
        assert mock_loader_calls == 1

        # Kriter 2: 5 tekrar sonunda bellek artışı 10 MB'ın altında olmalı (Memory Leak yok)
        assert memory_growth_bytes < 10 * 1024 * 1024

        # Kriter 3: Windows'ta PDF dosyaları açık kalmamalı (silinebilmeli -> PermissionError vermemeli)
        tr_pdf.unlink()
        en_pdf.unlink()
        assert not tr_pdf.exists()
        assert not en_pdf.exists()

        # Kriter 4: Tüm 5 döngülük uçtan uca akış 5 saniyenin altında tamamlanmalı
        assert elapsed_sec < 5.0
