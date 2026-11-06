"""Faz 15: Zengin CLI Arayüzü (`argparse` + `rich`) ve Raporlama Birim ve Hata Avcılığı Testleri.

Bu test modülü şu kritik kuralları (TDD Red -> Green -> Bug Hunting) denetler:
1. Argüman Ayrıştırma ve Doğrulama (`parse_cli_args`):
   - `--dry-run` (varsayılan `True`) ve `--no-dry-run` (`dry_run=False`) bayraklarını doğru çözmeli.
   - `--limit`, `--keywords`, `--location`, `--stats`, `--manual-list`, `--ingest-pdfs`, `--schedule`
     parametrelerini desteklemeli.
   - **KRİTİK KABUL KRİTERİ (Faz 15 Bug Hunting):**
     Geçersiz argümanlarda (örn. `--limit -5`, `--limit 0`, `--limit abc` veya boş `--keywords ""`)
     program kaba bir Python `ValueError` stacktrace'i ile çökmemeli; kullanıcı dostu, anlaşılır
     bir Türkçe hata mesajı içeren `CLIArgumentError` fırlatmalı (veya `run_cli` içinde yakalayıp
     hata kodu `2` ile ekrana basmalı)!
2. `rich` Tablo Raporlama (`render_stats_table`, `render_manual_ats_table`, `render_pipeline_summary`):
   - `--stats` verildiğinde veritabanındaki toplam gönderim, simülasyon, hata ve ATS sayılarını
     renkli `rich` tablosu olarak basmalı.
   - `--manual-list` verildiğinde manuel ATS kuyruğundaki şirketleri ve başvuru URL'lerini listelemeli.
   - Normal çalıştırmada (`run_cli`) orkestratörü (`run_pipeline`) tetikleyip sonuç tablosunu ekrana basmalı.
3. Ana Giriş Noktası (`main.py`):
   - `main(argv)` fonksiyonu CLI arayüzünü çağırıp çıkış kodunu (`0` veya hata kodu) döndürmeli.
"""

from __future__ import annotations

from io import StringIO
from pathlib import Path

import pytest
from rich.console import Console

from src.cli.interface import (
    CLIArgumentError,
    ParsedCLIArgs,
    parse_cli_args,
    run_cli,
)
from src.database.db_manager import ApplicationStatus, DatabaseManager
from src.orchestrator.pipeline import CompanyProcessReport, PipelineRunSummary
import main as main_entry


class TestCLIArgumentParserAndRichDashboardTDDAndBugHunting:
    """`src/cli/interface.py` ve `main.py` birim ve hata avcılığı testleri."""

    def test_parse_cli_args_defaults_to_safe_dry_run_mode(self) -> None:
        """Hiçbir argüman verilmediğinde güvenlik gereği `dry_run=True` varsayılan olmalı."""
        parsed = parse_cli_args([])
        assert isinstance(parsed, ParsedCLIArgs)
        assert parsed.dry_run is True
        assert parsed.limit is None
        assert parsed.stats is False
        assert parsed.manual_list is False

    def test_parse_cli_args_supports_no_dry_run_limit_and_keywords(self) -> None:
        """`--no-dry-run`, `--limit 5`, `--keywords "ROS 2" "SLAM"` ve `--location "Ankara"`
        argümanları doğru ayrıştırılmalı.
        """
        parsed = parse_cli_args(
            [
                "--no-dry-run",
                "--limit",
                "5",
                "--keywords",
                "ROS 2",
                "Fast-LIO2",
                "--location",
                "Ankara",
            ]
        )
        assert parsed.dry_run is False
        assert parsed.limit == 5
        assert parsed.keywords == ["ROS 2", "Fast-LIO2"]
        assert parsed.location == "Ankara"

    @pytest.mark.parametrize(
        "invalid_argv",
        [
            ["--limit", "-5"],          # Negatif limit!
            ["--limit", "0"],           # Sıfır limit!
            ["--limit", "not_a_num"],   # Sayı olmayan limit!
            ["--keywords", "   "],      # Sadece boşluktan oluşan anahtar kelime!
            ["--dry-run", "--no-dry-run"],  # Birbiriyle çelişen bayraklar!
            ["--unknown-flag-xyz"],     # Tanınmayan argüman!
        ],
    )
    def test_invalid_cli_arguments_raise_user_friendly_cli_argument_error(
        self, invalid_argv: list[str]
    ) -> None:
        """KRİTİK KABUL KRİTERİ (Faz 15 Bug Hunting):
        Geçersiz argümanlarda (örn. `--limit -5`) kullanıcı dostu `CLIArgumentError`
        fırlatılmalı ve `run_cli` çağrıldığında çıkış kodu `2` ile kullanıcıya net
        açıklama basılmalı!
        """
        with pytest.raises(CLIArgumentError):
            parse_cli_args(invalid_argv)

        buf = StringIO()
        console = Console(file=buf, force_terminal=False, width=100)
        exit_code = run_cli(invalid_argv, console=console)
        output = buf.getvalue()

        assert exit_code == 2
        assert "HATA" in output or "Geçersiz" in output or "limit" in output.lower()

    def test_run_cli_stats_prints_rich_database_statistics_table(
        self, tmp_path: Path
    ) -> None:
        """`--stats` komutu verildiğinde SQLite veritabanındaki istatistikler renkli `rich`
        tablosu olarak terminale basılmalı ve pipeline çalıştırılmadan `0` ile çıkılmalı.
        """
        db = DatabaseManager(sqlite_path=tmp_path / "cli_stats.db", cooldown_days=30)
        db.record_application(
            company_name="Baykar",
            company_url="https://baykar.example.tr",
            target_email="kariyer@baykar.example.tr",
            status=ApplicationStatus.SENT,
        )
        db.record_application(
            company_name="Aselsan",
            company_url="https://aselsan.example.tr",
            target_email="ik@aselsan.example.tr",
            status=ApplicationStatus.DRY_RUN,
        )
        db.record_manual_ats(
            company_name="Skydio",
            company_url="https://skydio.com",
            ats_url="https://boards.greenhouse.io/skydio",
            ats_provider="greenhouse.io",
        )

        buf = StringIO()
        console = Console(file=buf, force_terminal=False, width=120)
        exit_code = run_cli(["--stats"], console=console, db_manager=db)
        output = buf.getvalue()

        assert exit_code == 0
        assert "İstatistik" in output or "Gönderilen" in output
        assert "1" in output

    def test_run_cli_manual_list_prints_manual_ats_queue_table(
        self, tmp_path: Path
    ) -> None:
        """`--manual-list` komutu verildiğinde `MANUAL_ATS` kuyruğundaki şirketler ve
        ATS başvuru linkleri `rich` tablosunda gösterilmeli.
        """
        db = DatabaseManager(sqlite_path=tmp_path / "cli_ats.db", cooldown_days=30)
        db.record_manual_ats(
            company_name="AutoNav Robotics",
            company_url="https://autonav.io",
            ats_url="https://jobs.lever.co/autonav",
            ats_provider="lever.co",
            language="EN",
        )

        buf = StringIO()
        console = Console(file=buf, force_terminal=False, width=120)
        exit_code = run_cli(["--manual-list"], console=console, db_manager=db)
        output = buf.getvalue()

        assert exit_code == 0
        assert "AutoNav Robotics" in output
        assert "https://jobs.lever.co/autonav" in output
        assert "lever.co" in output

    def test_main_entrypoint_executes_pipeline_and_renders_summary_table(
        self, tmp_path: Path
    ) -> None:
        """`main.main(...)` çağrıldığında orkestratör çalışmalı ve dönen `PipelineRunSummary`
        verisi `rich` tablosuyla terminale basılmalı.
        """
        fake_summary = PipelineRunSummary(
            dry_run=True,
            queries_executed=['"ROS 2" Türkiye kariyer -site:clutch.co'],
            total_search_results=3,
            filtered_aggregator_count=1,
            valid_company_candidates=2,
            dry_run_count=1,
            manual_ats_count=1,
            company_reports=[
                CompanyProcessReport(
                    company_name="Baykar Otonom",
                    company_url="https://baykar-otonom.example.tr",
                    language="TR",
                    routing_action="SEND_EMAIL",
                    target_email="kariyer@baykar-otonom.example.tr",
                    status="DRY_RUN",
                    detail="1. Öncelik İK maili",
                ),
                CompanyProcessReport(
                    company_name="Skydio Global",
                    company_url="https://skydio.example.com",
                    language="EN",
                    routing_action="MANUAL_ATS",
                    ats_url="https://boards.greenhouse.io/skydio",
                    status="MANUAL_ATS",
                    detail="Greenhouse tespit edildi",
                ),
            ],
        )

        buf = StringIO()
        console = Console(file=buf, force_terminal=False, width=120)
        db = DatabaseManager(sqlite_path=tmp_path / "main_run.db", cooldown_days=30)

        exit_code = main_entry.main(
            ["--dry-run", "--limit", "2", "--keywords", "ROS 2"],
            console=console,
            db_manager=db,
            pipeline_runner=lambda **kwargs: fake_summary,
        )
        output = buf.getvalue()

        assert exit_code == 0
        assert "Baykar Otonom" in output
        assert "kariyer@baykar-otonom.example.tr" in output
        assert "Skydio Global" in output
        assert "DRY_RUN" in output
