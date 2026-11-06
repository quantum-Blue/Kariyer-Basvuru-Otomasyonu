"""Yapay Zekâ Destekli Otonom Kariyer Asistanı — Ana Giriş Noktası (`main.py`).

Kullanım Örnekleri:
- Güvenli Simülasyon (Varsayılan DRY-RUN):
    python main.py --dry-run --limit 5 --keywords "ROS 2" "SLAM"
- Veritabanı İstatistiklerini Görüntüleme:
    python main.py --stats
- Manuel ATS (Greenhouse, Lever vb.) Başvuru Kuyruğunu Listeleme:
    python main.py --manual-list
- CV ve Portfolyo PDF'lerini ChromaDB Vektör Veritabanına İndeksleme:
    python main.py --ingest-pdfs
- Canlı Gönderim Modu (Gmail SMTP):
    python main.py --no-dry-run --limit 10
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
import sys

from rich.console import Console

from src.cli.interface import run_cli
from src.database.db_manager import DatabaseManager
from src.orchestrator.pipeline import PipelineRunSummary


def main(
    argv: Sequence[str] | None = None,
    console: Console | None = None,
    db_manager: DatabaseManager | None = None,
    pipeline_runner: Callable[..., PipelineRunSummary] | None = None,
) -> int:
    """Uygulamanın ana giriş fonksiyonu."""
    return run_cli(
        argv=argv,
        console=console,
        db_manager=db_manager,
        pipeline_runner=pipeline_runner,
    )


if __name__ == "__main__":
    sys.exit(main())

