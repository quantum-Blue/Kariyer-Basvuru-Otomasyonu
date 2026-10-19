"""Faz 1 TDD Birim ve Hata Avcılığı (Bug Hunting) Testleri:
Ortam İzolasyonu, Konfigürasyon Yönetimi (Pydantic + PyYAML + dotenv) ve Merkezi Logger.
"""

import logging
from pathlib import Path
import pytest

from src.config.settings import (
    AppSettings,
    ConfigurationError,
    load_settings,
)
from src.utils.logger import get_logger, setup_logging


class TestConfigurationManagement:
    """Konfigürasyon yükleme, doğrulama ve hata yönetimi (TDD + Bug Hunting) testleri."""

    def test_load_valid_yaml_and_env(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Geçerli bir settings.yaml ve .env dosyası verildiğinde tüm ayarlar doğru tiplerle yüklenmeli."""
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        monkeypatch.delenv("SMTP_EMAIL", raising=False)

        env_file = tmp_path / ".env"
        env_file.write_text(
            "GEMINI_API_KEY=test_gemini_key_123\n"
            "SMTP_EMAIL=enes@example.com\n"
            "SMTP_PASSWORD=secret_app_pass\n"
            "CV_URL_TR=https://enes.dev/cv-tr.pdf\n"
            "CV_URL_EN=https://enes.dev/cv-en.pdf\n"
            "PORTFOLIO_URL=https://enes.dev\n",
            encoding="utf-8",
        )

        yaml_file = tmp_path / "settings.yaml"
        yaml_file.write_text(
            "app:\n"
            "  name: 'Otonom Kariyer Asistanı'\n"
            "  dry_run: true\n"
            "mailer:\n"
            "  daily_limit: 12\n"
            "  cooldown_days: 30\n"
            "scraper:\n"
            "  timeout_ms: 15000\n"
            "  blacklisted_domains:\n"
            "    - clutch.co\n"
            "    - f6s.com\n",
            encoding="utf-8",
        )

        settings = load_settings(yaml_path=yaml_file, env_path=env_file)

        assert isinstance(settings, AppSettings)
        assert settings.env.gemini_api_key == "test_gemini_key_123"
        assert settings.env.smtp_email == "enes@example.com"
        assert settings.app.dry_run is True
        assert settings.mailer.daily_limit == 12
        assert settings.mailer.cooldown_days == 30
        assert "clutch.co" in settings.scraper.blacklisted_domains
        assert "f6s.com" in settings.scraper.blacklisted_domains

    def test_production_settings_yaml_loads_and_has_critical_blacklists(
        self, tmp_path: Path
    ) -> None:
        """Gerçek config/settings.yaml dosyası hatasız yüklenmeli ve kritik kara listeleri içermeli."""
        root = Path(__file__).resolve().parent.parent
        prod_yaml = root / "config" / "settings.yaml"

        settings = load_settings(yaml_path=prod_yaml, env_path=tmp_path / "non_existent.env")

        assert settings.app.dry_run is True
        for domain in ("clutch.co", "f6s.com", "europages.com", "linkedin.com", "kariyer.net"):
            assert domain in settings.scraper.blacklisted_domains
        for prefix in ("support", "noreply", "no-reply", "sales", "help", "kvkk"):
            assert prefix in settings.scraper.blacklisted_email_prefixes
        for ats in ("greenhouse.io", "lever.co", "workday.com", "bamboohr.com"):
            assert ats in settings.filter.ats_domains

    def test_missing_env_file_uses_safe_defaults(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Eksik .env dosyası verildiğinde sistem çökmemeli, güvenli varsayılanlara dönmeli."""
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        non_existent_env = tmp_path / "non_existent.env"

        yaml_file = tmp_path / "settings.yaml"
        yaml_file.write_text("app:\n  dry_run: true\n", encoding="utf-8")

        settings = load_settings(yaml_path=yaml_file, env_path=non_existent_env)

        assert settings.app.dry_run is True
        assert settings.env.gemini_api_key == ""
        assert settings.mailer.daily_limit > 0

    def test_empty_yaml_file_uses_safe_defaults(self, tmp_path: Path) -> None:
        """Bomboş (0 byte veya sadece yorum satırı içeren) bir YAML dosyası güvenli varsayılanlarla yüklenmeli."""
        empty_yaml = tmp_path / "empty.yaml"
        empty_yaml.write_text("# Sadece yorum satırı\n", encoding="utf-8")

        settings = load_settings(yaml_path=empty_yaml, env_path=tmp_path / ".env")
        assert settings.app.dry_run is True
        assert settings.rag.chunk_size == 600
        assert settings.rag.chunk_overlap == 100

    def test_corrupted_yaml_raises_configuration_error(self, tmp_path: Path) -> None:
        """Bozuk YAML formatı verildiğinde sistem çökmemeli, anlamlı ConfigurationError fırlatmalı."""
        bad_yaml = tmp_path / "bad_settings.yaml"
        bad_yaml.write_text(
            "app:\n  name: [unclosed_list\n  dry_run: : : invalid\n",
            encoding="utf-8",
        )

        with pytest.raises(ConfigurationError) as exc_info:
            load_settings(yaml_path=bad_yaml, env_path=tmp_path / ".env")

        assert "YAML" in str(exc_info.value) or "format" in str(exc_info.value).lower()

    def test_non_dict_yaml_root_raises_configuration_error(self, tmp_path: Path) -> None:
        """YAML kök yapısı sözlük (dict) yerine liste veya düz metin ise ConfigurationError fırlatılmalı."""
        list_yaml = tmp_path / "list_root.yaml"
        list_yaml.write_text("- item1\n- item2\n", encoding="utf-8")

        with pytest.raises(ConfigurationError) as exc_info:
            load_settings(yaml_path=list_yaml, env_path=tmp_path / ".env")

        assert "dict" in str(exc_info.value)

    def test_unknown_yaml_key_typo_raises_configuration_error(self, tmp_path: Path) -> None:
        """YAML içinde yazım hatası olan bilinmeyen bir alan (örn. dry_runn) varsa ConfigurationError fırlatılmalı."""
        typo_yaml = tmp_path / "typo.yaml"
        typo_yaml.write_text("app:\n  dry_runn: false\n", encoding="utf-8")

        with pytest.raises(ConfigurationError):
            load_settings(yaml_path=typo_yaml, env_path=tmp_path / ".env")

    @pytest.mark.parametrize(
        "invalid_yaml_content,expected_field",
        [
            ("mailer:\n  daily_limit: -5\n", "daily_limit"),
            ("mailer:\n  min_delay_sec: 50.0\n  max_delay_sec: 10.0\n", "min_delay_sec"),
            ("rag:\n  chunk_size: 200\n  chunk_overlap: 250\n", "chunk_overlap"),
            ("scraper:\n  rate_limit_min_sec: 10.0\n  rate_limit_max_sec: 2.0\n", "rate_limit_min_sec"),
            ("generator:\n  min_word_count: 400\n  max_word_count: 100\n", "min_word_count"),
            ("app:\n  schedule_time: '25:99'\n", "schedule_time"),
            ("logging:\n  level: 'INVALID_LEVEL'\n", "level"),
        ],
    )
    def test_invalid_schema_constraints_raise_configuration_error(
        self, tmp_path: Path, invalid_yaml_content: str, expected_field: str
    ) -> None:
        """Sınır değer ve mantıksal çapraz doğrulama ihlallerinde ConfigurationError fırlatılmalı."""
        invalid_yaml = tmp_path / "invalid_constraint.yaml"
        invalid_yaml.write_text(invalid_yaml_content, encoding="utf-8")

        with pytest.raises(ConfigurationError) as exc_info:
            load_settings(yaml_path=invalid_yaml, env_path=tmp_path / ".env")

        assert expected_field in str(exc_info.value)

    def test_invalid_env_port_raises_configuration_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """.env içinde geçersiz SMTP_PORT (örn. sayı olmayan veya 65535 üstü) varsa ConfigurationError fırlatılmalı."""
        monkeypatch.delenv("SMTP_PORT", raising=False)
        env_file = tmp_path / ".env"
        env_file.write_text("SMTP_PORT=99999\n", encoding="utf-8")

        yaml_file = tmp_path / "settings.yaml"
        yaml_file.write_text("app:\n  dry_run: true\n", encoding="utf-8")

        with pytest.raises(ConfigurationError) as exc_info:
            load_settings(yaml_path=yaml_file, env_path=env_file)

        assert "smtp_port" in str(exc_info.value)

    def test_env_path_as_directory_raises_configuration_error(self, tmp_path: Path) -> None:
        """.env yolu olarak bir klasör verilirse ConfigurationError fırlatılmalı."""
        env_dir = tmp_path / "env_dir"
        env_dir.mkdir()
        yaml_file = tmp_path / "settings.yaml"
        yaml_file.write_text("app:\n  dry_run: true\n", encoding="utf-8")

        with pytest.raises(ConfigurationError):
            load_settings(yaml_path=yaml_file, env_path=env_dir)

    def test_missing_yaml_file_raises_configuration_error(self, tmp_path: Path) -> None:
        """Var olmayan bir YAML dosya yolu açıkça verildiğinde ConfigurationError fırlatılmalı."""
        missing_yaml = tmp_path / "missing.yaml"

        with pytest.raises(ConfigurationError):
            load_settings(yaml_path=missing_yaml, env_path=tmp_path / ".env")


class TestLoggerModule:
    """Merkezi loglama (logger) modülü birim, sınır değer ve UTF-8 karakter testleri."""

    def test_logger_writes_utf8_turkish_characters(self, tmp_path: Path) -> None:
        """Logger Türkçe karakterleri (ğ, ü, ş, i, ö, ç, İ, Ğ) bozmadan dosyaya yazmalı."""
        log_file = tmp_path / "logs" / "app.log"
        logger = setup_logging(log_file=log_file, level="INFO", logger_name="test_utf8_logger")

        test_message = "Türkçe karakter testi: ğüşıöçĞÜŞİÖÇ - Otonom İHA & Robotik"
        logger.info(test_message)

        for handler in logger.handlers:
            handler.flush()

        assert log_file.exists()
        content = log_file.read_text(encoding="utf-8")
        assert test_message in content

    def test_get_logger_prevents_duplicate_handlers(self, tmp_path: Path) -> None:
        """Aynı isimle birden fazla kez çağrılan logger mükerrer handler eklememeli."""
        log_file = tmp_path / "logs" / "dup.log"
        logger1 = setup_logging(log_file=log_file, level="DEBUG", logger_name="singleton_logger")
        initial_handler_count = len(logger1.handlers)

        logger2 = setup_logging(log_file=log_file, level="DEBUG", logger_name="singleton_logger")
        logger3 = get_logger("singleton_logger")

        assert logger1 is logger2 is logger3
        assert len(logger2.handlers) == initial_handler_count

    def test_logger_rejects_invalid_level_or_sizes(self, tmp_path: Path) -> None:
        """Geçersiz log seviyesi veya negatif dosya boyutlarında ValueError fırlatılmalı."""
        log_file = tmp_path / "logs" / "err.log"

        with pytest.raises(ValueError, match="Geçersiz log seviyesi"):
            setup_logging(log_file=log_file, level="TRACE_INVALID", logger_name="err_logger")

        with pytest.raises(ValueError, match="max_bytes"):
            setup_logging(log_file=log_file, level="INFO", max_bytes=0, logger_name="err_logger")

        with pytest.raises(ValueError, match="backup_count"):
            setup_logging(log_file=log_file, level="INFO", backup_count=-1, logger_name="err_logger")


class TestEnvironmentIsolation:
    """ROS 2 ve sistem paketlerinden izolasyon kontrol testleri."""

    def test_isolation_scripts_and_configs_exist(self) -> None:
        """Proje kök dizininde run.sh, run_tests.sh, .gitignore, .env.example ve config/settings.yaml bulunmalı."""
        root = Path(__file__).resolve().parent.parent
        assert (root / "run.sh").exists()
        assert (root / "run_tests.sh").exists()
        assert (root / "pytest.ini").exists()
        assert (root / ".gitignore").exists()
        assert (root / ".env.example").exists()
        assert (root / "config" / "settings.yaml").exists()

        run_tests_content = (root / "run_tests.sh").read_text(encoding="utf-8")
        assert 'PYTHONPATH=""' in run_tests_content
        assert "-p no:ament_flake8" in run_tests_content
        assert "-p no:launch_testing" in run_tests_content

