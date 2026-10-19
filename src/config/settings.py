"""Merkezi Konfigürasyon Yöneticisi (Pydantic + PyYAML + python-dotenv).

Bu modül hem `config/settings.yaml` uygulama parametrelerini hem de `.env`
içerisindeki hassas ortam değişkenlerini okur, tiplerini doğrular ve
hatalı yapılandırmalarda anlamlı `ConfigurationError` fırlatır.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from dotenv import dotenv_values
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator
import yaml


class ConfigurationError(Exception):
    """Konfigürasyon okuma, ayrıştırma veya doğrulama hatası."""


class EnvConfig(BaseModel):
    """Hassas ortam değişkenleri (.env veya sistem ortamından okunur)."""

    model_config = ConfigDict(extra="ignore", validate_assignment=True)

    gemini_api_key: str = Field(default="")
    smtp_host: str = Field(default="smtp.gmail.com", min_length=1)
    smtp_port: int = Field(default=465, ge=1, le=65535)
    smtp_email: str = Field(default="")
    smtp_password: str = Field(default="")
    sender_name: str = Field(default="Muhammed Enes BAL", min_length=1)
    cv_url_tr: str = Field(default="")
    cv_url_en: str = Field(default="")
    portfolio_url: str = Field(default="")
    github_url: str = Field(default="")
    linkedin_url: str = Field(default="")


class AppSectionConfig(BaseModel):
    """Genel uygulama çalışma ayarları."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(default="Otonom Kariyer Asistanı", min_length=1)
    version: str = Field(default="1.0.0", min_length=1)
    dry_run: bool = Field(default=True)
    schedule_time: str = Field(default="09:00", pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")


class RagSectionConfig(BaseModel):
    """RAG, PDF Parçalama ve ChromaDB ayarları."""

    model_config = ConfigDict(extra="forbid")

    embedding_model: str = Field(
        default="sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
        min_length=1,
    )
    chroma_persist_dir: str = Field(default="data/chroma_db", min_length=1)
    collection_name: str = Field(default="candidate_profile", min_length=1)
    chunk_size: int = Field(default=600, ge=50, le=10000)
    chunk_overlap: int = Field(default=100, ge=0, le=5000)
    top_k: int = Field(default=4, ge=1, le=50)

    @model_validator(mode="after")
    def validate_overlap_smaller_than_chunk(self) -> RagSectionConfig:
        """Chunk overlap değeri chunk_size değerinden küçük olmalıdır."""
        if self.chunk_overlap >= self.chunk_size:
            raise ValueError(
                f"chunk_overlap ({self.chunk_overlap}), chunk_size ({self.chunk_size}) değerinden küçük olmalıdır."
            )
        return self


class ScraperSectionConfig(BaseModel):
    """Web kazıyıcı, anti-aggregator domain kara listesi ve e-posta filtre ayarları."""

    model_config = ConfigDict(extra="forbid")

    timeout_ms: int = Field(default=20000, ge=1000, le=120000)
    rate_limit_min_sec: float = Field(default=2.0, ge=0.0, le=300.0)
    rate_limit_max_sec: float = Field(default=5.0, ge=0.1, le=600.0)
    max_results_per_query: int = Field(default=10, ge=1, le=100)
    user_agent: str = Field(
        default=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        ),
        min_length=5,
    )
    target_subpages: list[str] = Field(
        default_factory=lambda: [
            "/kariyer",
            "/careers",
            "/career",
            "/hakkimizda",
            "/about",
            "/about-us",
            "/iletisim",
            "/contact",
        ]
    )
    blacklisted_domains: list[str] = Field(
        default_factory=lambda: [
            "clutch.co",
            "f6s.com",
            "europages.com",
            "linkedin.com",
            "kariyer.net",
            "glassdoor.com",
            "indeed.com",
            "sortlist.com",
            "yellowpages.com",
            "hiringcafe.com",
        ]
    )
    blacklisted_title_patterns: list[str] = Field(
        default_factory=lambda: [
            "top 10",
            "top 20",
            "top 30",
            "best robotics companies",
            "b2b şirketleri",
            "şirket rehberi",
        ]
    )
    blacklisted_email_prefixes: list[str] = Field(
        default_factory=lambda: [
            "support",
            "help",
            "destek",
            "sales",
            "satis",
            "marketing",
            "billing",
            "accounting",
            "muhasebe",
            "noreply",
            "no-reply",
            "admin",
            "webmaster",
            "press",
            "media",
            "legal",
            "privacy",
            "kvkk",
            "abuse",
            "security",
        ]
    )
    high_priority_email_prefixes: list[str] = Field(
        default_factory=lambda: [
            "kariyer",
            "career",
            "careers",
            "ik",
            "hr",
            "insankaynaklari",
            "humanresources",
            "talent",
            "talents",
            "jobs",
            "join",
            "recruiting",
            "recruitment",
            "people",
            "cv",
            "basvuru",
        ]
    )
    low_priority_email_prefixes: list[str] = Field(
        default_factory=lambda: [
            "info",
            "iletisim",
            "contact",
            "hello",
            "merhaba",
            "office",
        ]
    )

    @model_validator(mode="after")
    def validate_rate_limits(self) -> ScraperSectionConfig:
        """Minimum bekleme süresi maksimum bekleme süresinden büyük olamaz."""
        if self.rate_limit_min_sec > self.rate_limit_max_sec:
            raise ValueError(
                f"rate_limit_min_sec ({self.rate_limit_min_sec}) > rate_limit_max_sec ({self.rate_limit_max_sec}) olamaz."
            )
        return self


class FilterSectionConfig(BaseModel):
    """ATS tespit motoru domain listesi."""

    model_config = ConfigDict(extra="forbid")

    ats_domains: list[str] = Field(
        default_factory=lambda: [
            "greenhouse.io",
            "lever.co",
            "myworkdayjobs.com",
            "workday.com",
            "bamboohr.com",
            "workable.com",
            "smartrecruiters.com",
            "personio.de",
            "personio.com",
            "ashbyhq.com",
            "jobvite.com",
            "icims.com",
            "taleo.net",
            "successfactors.com",
            "recruitee.com",
        ]
    )


class GeneratorSectionConfig(BaseModel):
    """LLM içerik üretici ve çıktı doğrulayıcı ayarları."""

    model_config = ConfigDict(extra="forbid")

    gemini_model: str = Field(default="gemini-2.5-flash", min_length=1)
    min_word_count: int = Field(default=60, ge=10, le=2000)
    max_word_count: int = Field(default=320, ge=20, le=5000)
    temperature: float = Field(default=0.4, ge=0.0, le=2.0)

    @model_validator(mode="after")
    def validate_word_counts(self) -> GeneratorSectionConfig:
        """Minimum kelime sayısı maksimum kelime sayısından küçük olmalıdır."""
        if self.min_word_count >= self.max_word_count:
            raise ValueError(
                f"min_word_count ({self.min_word_count}) < max_word_count ({self.max_word_count}) olmalıdır."
            )
        return self


class MailerSectionConfig(BaseModel):
    """E-posta gönderim kotası, bekleme ve 30 gün kuralı ayarları."""

    model_config = ConfigDict(extra="forbid")

    daily_limit: int = Field(default=10, ge=1, le=100)
    cooldown_days: int = Field(default=30, ge=1, le=365)
    min_delay_sec: float = Field(default=15.0, ge=0.0, le=600.0)
    max_delay_sec: float = Field(default=45.0, ge=0.1, le=1800.0)

    @model_validator(mode="after")
    def validate_delays(self) -> MailerSectionConfig:
        """Gönderim gecikmesi alt sınırı üst sınırdan büyük olamaz."""
        if self.min_delay_sec > self.max_delay_sec:
            raise ValueError(
                f"min_delay_sec ({self.min_delay_sec}) > max_delay_sec ({self.max_delay_sec}) olamaz."
            )
        return self


class DatabaseSectionConfig(BaseModel):
    """SQLite veritabanı ayarları."""

    model_config = ConfigDict(extra="forbid")

    sqlite_path: str = Field(default="data/career_assistant.db", min_length=1)


class LoggingSectionConfig(BaseModel):
    """Loglama ayarları."""

    model_config = ConfigDict(extra="forbid")

    level: str = Field(default="INFO")
    log_file: str = Field(default="logs/career_assistant.log", min_length=1)
    max_bytes: int = Field(default=5242880, ge=1024)
    backup_count: int = Field(default=3, ge=0, le=50)

    @field_validator("level")
    @classmethod
    def validate_log_level(cls, value: str) -> str:
        """Log seviyesinin geçerli bir standart Python logging seviyesi olduğunu doğrular."""
        normalized = value.strip().upper()
        valid_levels = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        if normalized not in valid_levels:
            raise ValueError(f"Geçersiz log seviyesi: '{value}'. Geçerli değerler: {sorted(valid_levels)}")
        return normalized


class AppSettings(BaseModel):
    """Tüm uygulama konfigürasyonunu ve ortam değişkenlerini barındıran ana model."""

    model_config = ConfigDict(extra="forbid")

    app: AppSectionConfig = Field(default_factory=AppSectionConfig)
    rag: RagSectionConfig = Field(default_factory=RagSectionConfig)
    scraper: ScraperSectionConfig = Field(default_factory=ScraperSectionConfig)
    filter: FilterSectionConfig = Field(default_factory=FilterSectionConfig)
    generator: GeneratorSectionConfig = Field(default_factory=GeneratorSectionConfig)
    mailer: MailerSectionConfig = Field(default_factory=MailerSectionConfig)
    database: DatabaseSectionConfig = Field(default_factory=DatabaseSectionConfig)
    logging: LoggingSectionConfig = Field(default_factory=LoggingSectionConfig)
    env: EnvConfig = Field(default_factory=EnvConfig)


def _load_env_config(env_path: Path) -> EnvConfig:
    """Verilen .env dosyasından ve ortam değişkenlerinden EnvConfig oluşturur.

    Eksik .env dosyası durumunda çökmez; güvenli varsayılanlara döner.
    """
    file_values: dict[str, str | None] = {}
    if env_path.exists():
        if env_path.is_dir():
            raise ConfigurationError(f".env yolu bir klasör olamaz: {env_path}")
        try:
            file_values = dotenv_values(env_path, encoding="utf-8")
        except Exception as exc:
            raise ConfigurationError(f".env dosyası okunurken hata oluştu ({env_path}): {exc}") from exc

    env_field_map = {
        "gemini_api_key": "GEMINI_API_KEY",
        "smtp_host": "SMTP_HOST",
        "smtp_port": "SMTP_PORT",
        "smtp_email": "SMTP_EMAIL",
        "smtp_password": "SMTP_PASSWORD",
        "sender_name": "SENDER_NAME",
        "cv_url_tr": "CV_URL_TR",
        "cv_url_en": "CV_URL_EN",
        "portfolio_url": "PORTFOLIO_URL",
        "github_url": "GITHUB_URL",
        "linkedin_url": "LINKEDIN_URL",
    }

    resolved: dict[str, Any] = {}
    for field_name, env_key in env_field_map.items():
        val = os.environ.get(env_key)
        if val is None and env_key in file_values and file_values[env_key] is not None:
            val = file_values[env_key]
        if val is not None:
            resolved[field_name] = val.strip()

    try:
        return EnvConfig(**resolved)
    except ValidationError as exc:
        raise ConfigurationError(f"Ortam değişkenleri (.env) doğrulama hatası: {exc}") from exc


def load_settings(
    yaml_path: str | Path = "config/settings.yaml",
    env_path: str | Path = ".env",
) -> AppSettings:
    """YAML konfigürasyon dosyasını ve .env değişkenlerini okuyup doğrular.

    Args:
        yaml_path: `settings.yaml` dosyasının yolu.
        env_path: `.env` dosyasının yolu (yoksa güvenli varsayılanlar kullanılır).

    Returns:
        Doğrulanmış `AppSettings` nesnesi.

    Raises:
        ConfigurationError: YAML dosyası bulunamadığında, bozuk formatta olduğunda
            veya şema doğrulaması (ValidationError) başarısız olduğunda.
    """
    yaml_file = Path(yaml_path)
    env_file = Path(env_path)

    if not yaml_file.exists() or not yaml_file.is_file():
        raise ConfigurationError(f"YAML konfigürasyon dosyası bulunamadı: {yaml_file}")

    try:
        raw_text = yaml_file.read_text(encoding="utf-8")
    except Exception as exc:
        raise ConfigurationError(f"YAML dosyası okunamadı ({yaml_file}): {exc}") from exc

    try:
        parsed_yaml = yaml.safe_load(raw_text)
    except yaml.YAMLError as exc:
        raise ConfigurationError(f"Bozuk YAML formatı ({yaml_file}): {exc}") from exc

    if parsed_yaml is None:
        parsed_yaml = {}

    if not isinstance(parsed_yaml, dict):
        raise ConfigurationError(
            f"YAML kök yapısı bir sözlük (dict) olmalıdır, alınan tip: {type(parsed_yaml).__name__}"
        )

    env_config = _load_env_config(env_file)
    parsed_yaml["env"] = env_config

    try:
        return AppSettings(**parsed_yaml)
    except ValidationError as exc:
        raise ConfigurationError(f"Konfigürasyon şema doğrulama hatası ({yaml_file}): {exc}") from exc

