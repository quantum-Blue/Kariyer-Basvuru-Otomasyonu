"""Zengin CLI Arayüzü (`argparse` + `rich`) ve Terminal Raporlama Modülü (src/cli/interface.py).

Bu modülün amacı:
1. Kullanıcıya terminal üzerinden esnek ve güvenli komut satırı argümanları sunmak:
   - `--dry-run` (Varsayılan güvenli simülasyon modu) ve `--no-dry-run` (Canlı SMTP gönderim modu)
   - `--limit <N>` (Oturumda gönderilecek maksimum başvuru sayısı)
   - `--keywords <K1> <K2> ...` (Özel arama anahtar kelimeleri)
   - `--location <Konum>` (Hedef şehir veya ülke, varsayılan: Türkiye)
   - `--stats` (SQLite veritabanındaki toplam başvuru ve ATS istatistiklerini tablo olarak gösterir)
   - `--manual-list` (Manuel form doldurulması gereken `MANUAL_ATS` kuyruğunu listeler)
   - `--ingest-pdfs` (`cv/` ve `portfolio/` klasörlerindeki PDF belgelerini ChromaDB'ye indeksler)
   - `--schedule` (Faz 16'daki günlük otonom zamanlayıcıyı başlatır)
2. **Kullanıcı Dostu Argüman Doğrulaması (Faz 15 Kritik Kabul Kriteri):**
   Standart `argparse` geçersiz bir argüman (`--limit -5`, `--limit abc` vb.) aldığında
   kaba bir `SystemExit` veya İngilizce hata mesajı basar. Bu modül `ArgumentParser.error`
   metodunu özelleştirerek tüm hataları anlaşılır Türkçe mesajlarla `CLIArgumentError`
   olarak yakalar ve kullanıcıya temiz bir bilgi paneli gösterir.
3. **`rich` Kütüphanesi ile Renkli Terminal Panelleri ve Özet Tabloları:**
   Orkestratörün çalışması tamamlandığında kaç rehber sitesinin (`clutch.co`, `f6s.com`)
   elendiğini, kaç şirkete mail atıldığını/simüle edildiğini ve her şirketin durumunu
   renkli `rich.table.Table` tablolarıyla ekrana basar.
"""

from __future__ import annotations

import argparse
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from src.config.settings import AppSettings, load_settings
from src.database.db_manager import DatabaseManager, ManualATSRecord
from src.orchestrator.pipeline import PipelineRunSummary, run_pipeline
from src.rag.chunker import chunk_pdf_document
from src.rag.embedder import MultilingualEmbedder
from src.rag.pdf_loader import extract_text_from_pdf
from src.rag.vector_store import VectorStoreManager
from src.utils.logger import get_logger, setup_logging

logger = get_logger(__name__)


class CLIArgumentError(Exception):
    """Geçersiz komut satırı argümanı verildiğinde fırlatılan kullanıcı dostu hata."""


@dataclass(frozen=True)
class ParsedCLIArgs:
    """Doğrulanmış komut satırı argümanlarını taşıyan veri modeli."""

    dry_run: bool = True
    limit: int | None = None
    keywords: list[str] = field(default_factory=list)
    location: str = "Türkiye"
    intent: str = "kariyer"
    stats: bool = False
    manual_list: bool = False
    ingest_pdfs: bool = False
    schedule: bool = False
    config_path: str = "config/settings.yaml"
    env_path: str = ".env"


class _FriendlyArgumentParser(argparse.ArgumentParser):
    """`sys.exit` çağırmak yerine Türkçe açıklamalı `CLIArgumentError` fırlatan özel `ArgumentParser`."""

    def error(self, message: str) -> None:
        raise CLIArgumentError(
            f"Geçersiz komut satırı argümanı: {message}. "
            f"Yardım için '--help' parametresini kullanabilirsiniz."
        )


def _positive_int_validator(raw_value: str) -> int:
    """`--limit` parametresinin 1 veya daha büyük pozitif bir tam sayı olduğunu doğrular."""
    try:
        parsed = int(raw_value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"'--limit' değeri bir tam sayı olmalıdır, alınan: '{raw_value}'"
        ) from exc

    if parsed <= 0:
        raise argparse.ArgumentTypeError(
            f"'--limit' değeri pozitif bir tam sayı (>= 1) olmalıdır, alınan: {parsed}"
        )
    return parsed


def parse_cli_args(argv: Sequence[str] | None = None) -> ParsedCLIArgs:
    """Komut satırı argümanlarını ayrıştırır ve sınır değer doğrulamalarından geçirir."""
    parser = _FriendlyArgumentParser(
        prog="career-assistant",
        description="Yapay Zekâ Destekli Otonom Kariyer Asistanı (Muhammed Enes BAL)",
    )

    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Sıfır riskli simülasyon modunda çalıştırır (hiçbir gerçek e-posta gönderilmez).",
    )
    mode_group.add_argument(
        "--no-dry-run",
        action="store_true",
        default=False,
        help="Canlı gönderim modunu etkinleştirir (Gmail SMTP üzerinden gerçek e-posta gönderir).",
    )

    parser.add_argument(
        "--limit",
        type=_positive_int_validator,
        default=None,
        help="Bu oturumda gönderilecek maksimum başvuru sayısı (pozitif tam sayı).",
    )
    parser.add_argument(
        "--keywords",
        nargs="+",
        default=None,
        help="Arama yapılacak teknik uzmanlık kelimeleri (Örn: --keywords 'ROS 2' 'SLAM').",
    )
    parser.add_argument(
        "--location",
        type=str,
        default="Türkiye",
        help="Hedef ülke veya şehir (Varsayılan: Türkiye).",
    )
    parser.add_argument(
        "--intent",
        type=str,
        default="kariyer",
        help="Arama niyeti kelimesi (Varsayılan: kariyer).",
    )
    parser.add_argument(
        "--stats",
        action="store_true",
        help="SQLite veritabanındaki genel başvuru istatistiklerini gösterir ve çıkar.",
    )
    parser.add_argument(
        "--manual-list",
        action="store_true",
        help="Manuel başvuru yapılması gereken ATS (Greenhouse, Lever vb.) şirket listesini gösterir.",
    )
    parser.add_argument(
        "--ingest-pdfs",
        action="store_true",
        help="cv/ ve portfolio/ klasörlerindeki PDF belgelerini okuyup ChromaDB'ye kaydeder.",
    )
    parser.add_argument(
        "--schedule",
        action="store_true",
        help="Günlük otonom zamanlayıcı (daemon) modunu başlatır.",
    )
    parser.add_argument(
        "--config",
        type=str,
        default="config/settings.yaml",
        help="YAML konfigürasyon dosyası yolu.",
    )
    parser.add_argument(
        "--env-file",
        type=str,
        default=".env",
        help="Ortam değişkenleri (.env) dosyası yolu.",
    )

    ns = parser.parse_args(list(argv) if argv is not None else None)

    # --no-dry-run açıkça verilmediği sürece her zaman güvenli dry_run=True kullanılır
    effective_dry_run = not bool(ns.no_dry_run)

    cleaned_keywords: list[str] = []
    if ns.keywords is not None:
        cleaned_keywords = [str(k).strip() for k in ns.keywords if str(k).strip()]
        if not cleaned_keywords:
            raise CLIArgumentError(
                "Geçersiz '--keywords' parametresi: En az bir geçerli anahtar kelime girilmelidir."
            )

    return ParsedCLIArgs(
        dry_run=effective_dry_run,
        limit=ns.limit,
        keywords=cleaned_keywords,
        location=(ns.location or "Türkiye").strip(),
        intent=(ns.intent or "kariyer").strip(),
        stats=bool(ns.stats),
        manual_list=bool(ns.manual_list),
        ingest_pdfs=bool(ns.ingest_pdfs),
        schedule=bool(ns.schedule),
        config_path=str(ns.config),
        env_path=str(ns.env_file),
    )


def render_stats_table(stats: dict[str, Any], console: Console) -> None:
    """SQLite veritabanından alınan genel başvuru istatistiklerini `rich` tablosu olarak basar."""
    table = Table(title="📊 Otonom Kariyer Asistanı — Veritabanı İstatistikleri", show_lines=True)
    table.add_column("Metrik", style="cyan", no_wrap=True)
    table.add_column("Değer", style="bold green", justify="right")

    table.add_row("Gerçek Gönderilen E-posta (SENT)", str(stats.get("sent_count", 0)))
    table.add_row("Simülasyon Kaydı (DRY_RUN)", str(stats.get("dry_run_count", 0)))
    table.add_row("30 Gün Cooldown Nedeniyle Atlanan (SKIPPED)", str(stats.get("skipped_count", 0)))
    table.add_row("Başarısız Gönderim Denemesi (FAILED)", str(stats.get("failed_count", 0)))
    table.add_row("Toplam İşlenen Başvuru Kaydı", str(stats.get("total_applications", 0)))
    table.add_row("Manuel ATS Kuyruğundaki Şirket Sayısı (MANUAL_ATS)", str(stats.get("manual_ats_count", 0)))

    console.print(table)


def render_manual_ats_table(records: Sequence[ManualATSRecord], console: Console) -> None:
    """Manuel form doldurulması gereken ATS şirketlerini `rich` tablosu olarak basar."""
    table = Table(
        title="📝 Manuel Başvuru Kuyruğu (ATS Tespit Edilen Şirketler)",
        show_lines=True,
    )
    table.add_column("#", style="dim", justify="right")
    table.add_column("Şirket Adı", style="bold cyan")
    table.add_column("ATS Sağlayıcısı", style="magenta")
    table.add_column("Dil", justify="center")
    table.add_column("Doğrudan ATS Başvuru Bağlantısı", style="underline blue")

    if not records:
        console.print(
            Panel(
                "Henüz manuel ATS kuyruğunda kayıtlı şirket bulunmuyor.",
                title="Bilgi",
                border_style="yellow",
            )
        )
        return

    for idx, rec in enumerate(records, start=1):
        table.add_row(
            str(idx),
            rec.company_name,
            rec.ats_provider or "-",
            rec.language,
            rec.ats_url,
        )

    console.print(table)


def render_pipeline_summary(summary: PipelineRunSummary, console: Console) -> None:
    """Orkestratör çalışmasının genel özetini ve şirket bazlı sonuçlarını terminale basar."""
    mode_label = (
        "[bold yellow]SİMÜLASYON (DRY-RUN)[/bold yellow]"
        if summary.dry_run
        else "[bold red]CANLI GÖNDERİM (LIVE SMTP)[/bold red]"
    )
    overview = Table(title="🚀 Otonom Çalışma Özeti", show_header=True, show_lines=True)
    overview.add_column("Aşama / Gösterge", style="cyan")
    overview.add_column("Sonuç", style="bold white", justify="right")

    overview.add_row("Çalışma Modu", mode_label)
    overview.add_row("Çalıştırılan Dork Sorgu Sayısı", str(len(summary.queries_executed)))
    overview.add_row("Ham Arama Motoru Sonucu", str(summary.total_search_results))
    overview.add_row("Elenen Rehber/Liste Sitesi (Anti-Aggregator)", str(summary.filtered_aggregator_count))
    overview.add_row("Analiz Edilen Gerçek Şirket Adayı", str(summary.valid_company_candidates))
    overview.add_row("Canlı Gönderilen E-posta (SENT)", str(summary.sent_count))
    overview.add_row("Simüle Edilen E-posta (DRY_RUN)", str(summary.dry_run_count))
    overview.add_row("Manuel ATS Listesine Ayrılan (MANUAL_ATS)", str(summary.manual_ats_count))
    overview.add_row("30 Gün Kuralıyla Atlanan (COOLDOWN)", str(summary.cooldown_skipped_count))
    overview.add_row("Geçerli E-postası Olmayan (SKIP)", str(summary.skipped_no_email_count))
    overview.add_row("İzole Edilen Hata Sayısı (Fault Isolation)", str(summary.isolated_errors))

    console.print(overview)

    if summary.company_reports:
        details = Table(title="🏢 Şirket Bazlı İşlem Raporu", show_lines=True)
        details.add_column("Şirket Adı", style="bold cyan")
        details.add_column("Dil", justify="center")
        details.add_column("Durum", style="bold green")
        details.add_column("Hedef E-posta / ATS URL", style="yellow")
        details.add_column("Açıklama", style="dim")

        for rep in summary.company_reports:
            target_info = rep.target_email or rep.ats_url or rep.company_url
            details.add_row(
                rep.company_name,
                rep.language,
                rep.status,
                target_info,
                rep.detail[:90],
            )
        console.print(details)


def ingest_workspace_pdfs(settings: AppSettings, console: Console) -> int:
    """`cv/` ve `portfolio/` klasörlerindeki tüm PDF belgelerini okuyup ChromaDB'ye kaydeder."""
    pdf_files: list[Path] = []
    for folder_name in ("cv", "portfolio"):
        folder_path = Path(folder_name)
        if folder_path.exists() and folder_path.is_dir():
            pdf_files.extend(sorted(folder_path.glob("*.pdf")))

    if not pdf_files:
        console.print("[yellow]Uyarı: 'cv/' veya 'portfolio/' klasörlerinde PDF dosyası bulunamadı.[/yellow]")
        return 0

    embedder = MultilingualEmbedder.get_instance(model_name=settings.rag.embedding_model)
    store = VectorStoreManager(
        persist_directory=settings.rag.chroma_persist_dir,
        collection_name=settings.rag.collection_name,
        embedder=embedder,
    )

    total_chunks = 0
    for pdf_path in pdf_files:
        doc = extract_text_from_pdf(pdf_path)
        chunks = chunk_pdf_document(
            doc,
            chunk_size=settings.rag.chunk_size,
            chunk_overlap=settings.rag.chunk_overlap,
        )
        store.upsert_chunks(chunks)
        total_chunks += len(chunks)

    console.print(
        f"[bold green]✅ {len(pdf_files)} PDF belgesi ({total_chunks} metin parçası) "
        f"ChromaDB vektör veritabanına başarıyla indekslendi.[/bold green]"
    )
    return total_chunks


def run_cli(
    argv: Sequence[str] | None = None,
    console: Console | None = None,
    db_manager: DatabaseManager | None = None,
    pipeline_runner: Callable[..., PipelineRunSummary] | None = None,
) -> int:
    """CLI argümanlarını ayrıştırır ve ilgili komutu (`--stats`, `--manual-list`, pipeline vb.) çalıştırır.

    Returns:
        İşletim sistemi çıkış kodu (`0`: başarılı, `2`: argüman hatası, `1`: çalışma hatası).
    """
    out_console = console or Console()

    try:
        parsed = parse_cli_args(argv)
    except CLIArgumentError as exc:
        out_console.print(
            Panel(
                f"[bold red][HATA] {exc}[/bold red]",
                title="Komut Satırı Hatası",
                border_style="red",
            )
        )
        return 2

    try:
        settings = load_settings(yaml_path=parsed.config_path, env_path=parsed.env_path)
        setup_logging(
            log_file=settings.logging.log_file,
            level=settings.logging.level,
            max_bytes=settings.logging.max_bytes,
            backup_count=settings.logging.backup_count,
        )
    except Exception as exc:
        out_console.print(f"[bold red][HATA] Konfigürasyon yüklenemedi: {exc}[/bold red]")
        return 1

    resolved_db = db_manager or DatabaseManager(
        sqlite_path=settings.database.sqlite_path,
        cooldown_days=settings.mailer.cooldown_days,
    )

    # 1. Yalnızca İstatistik Görüntüleme (--stats)
    if parsed.stats:
        render_stats_table(resolved_db.get_statistics(), out_console)
        return 0

    # 2. Yalnızca Manuel ATS Listesi Görüntüleme (--manual-list)
    if parsed.manual_list:
        render_manual_ats_table(resolved_db.get_manual_ats_queue(), out_console)
        return 0

    # 3. PDF Belgelerini ChromaDB'ye İndeksleme (--ingest-pdfs)
    if parsed.ingest_pdfs:
        ingest_workspace_pdfs(settings, out_console)
        return 0

    # 4. Zamanlayıcı (Scheduler) Modu (--schedule -> Faz 16)
    if parsed.schedule:
        from src.orchestrator.scheduler import AutonomousScheduler

        scheduler = AutonomousScheduler(
            schedule_time=settings.app.schedule_time,
            job_func=lambda: (pipeline_runner or run_pipeline)(
                keywords=parsed.keywords or None,
                location=parsed.location,
                intent=parsed.intent,
                max_applications=parsed.limit,
                dry_run=parsed.dry_run,
                settings=settings,
            ),
        )
        out_console.print(
            f"[bold cyan]⏰ Günlük zamanlayıcı başlatıldı (Her gün saat {settings.app.schedule_time}).[/bold cyan]"
        )
        scheduler.run_forever()
        return 0

    # 5. Standart Otonom Başvuru Pipeline Çalıştırması
    runner = pipeline_runner or run_pipeline
    summary = runner(
        keywords=parsed.keywords or None,
        location=parsed.location,
        intent=parsed.intent,
        max_applications=parsed.limit,
        dry_run=parsed.dry_run,
        settings=settings,
    )
    render_pipeline_summary(summary, out_console)
    return 0

