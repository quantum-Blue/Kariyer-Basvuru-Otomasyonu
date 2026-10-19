"""Merkezi Konfigürasyon Yöneticisi (Pydantic + PyYAML + python-dotenv).

Bu modülün amacı:
1. Uygulamanın çalışma parametrelerini tutan `config/settings.yaml` dosyasını okumak.
2. Şifreler ve API anahtarları gibi hassas verileri tutan `.env` dosyasını okumak.
3. Okunan tüm bu verilerin veri tiplerini (int, float, bool, str, list) ve mantıksal
   sınırlarını (örn. günlük mail limiti negatif olamaz, chunk_overlap >= chunk_size olamaz)
   Pydantic kütüphanesi ile daha program başlarken doğrulamak.
4. Herhangi bir yazım hatası veya bozuk YAML durumunda programı anlamsız hatalarla
   çökertmek yerine tek ve anlaşılır bir `ConfigurationError` fırlatmaktır.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from dotenv import dotenv_values
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator
import yaml


class ConfigurationError(Exception):
    """Konfigürasyon okuma, ayrıştırma (parsing) veya şema doğrulama hatası.

    Tüm alt seviye hatalar (YAML sözdizimi hatası, Pydantic doğrulama hatası,
    dosya bulunamama hatası vb.) yakalanıp bu sınıfa sarmalanarak (wrap edilerek)
    üst katmanlara fırlatılır.
    """


class EnvConfig(BaseModel):
    """Hassas ortam değişkenleri modeli (.env dosyasından veya işletim sistemi ortamından okunur).

    Not: `extra="ignore"` sayesinde işletim sistemindeki diğer yabancı ortam değişkenleri
    (PATH, PYTHONPATH vb.) hata üretmeden yoksayılır.
    """

    model_config = ConfigDict(extra="ignore", validate_assignment=True)

    # Google Gemini LLM API anahtarı (.env içinde tanımlanmazsa boş string olur, fallback devreye girer)
    gemini_api_key: str = Field(default="")

    # Gmail SMTP sunucu bilgileri (SSL için varsayılan port: 465, geçerli port aralığı: 1 - 65535)
    smtp_host: str = Field(default="smtp.gmail.com", min_length=1)
    smtp_port: int = Field(default=465, ge=1, le=65535)
    smtp_email: str = Field(default="")
    smtp_password: str = Field(default="")
    sender_name: str = Field(default="Muhammed Enes BAL", min_length=1)

    # Spam filtrelerine takılmamak için mail içine PDF eki (attachment) koymak yerine
    # gövdeye eklenecek olan CV ve Portfolyo URL bağlantıları
    cv_url_tr: str = Field(default="")
    cv_url_en: str = Field(default="")
    portfolio_url: str = Field(default="")
    github_url: str = Field(default="")
    linkedin_url: str = Field(default="")


class AppSectionConfig(BaseModel):
    """Genel uygulama çalışma ayarları (`settings.yaml -> app` bölümü).

    Not: `extra="forbid"` ayarı, YAML dosyasında yanlışlıkla yapılan yazım hatalarını
    (örneğin `dry_run` yerine `dry_runn` yazılmasını) anında yakalayıp hata fırlatır.
    """

    model_config = ConfigDict(extra="forbid")

    name: str = Field(default="Otonom Kariyer Asistanı", min_length=1)
    version: str = Field(default="1.0.0", min_length=1)
    # Güvenlik gereği varsayılan olarak simülasyon (dry_run=True) modunda başlar
    dry_run: bool = Field(default=True)
    # Zamanlayıcının (scheduler) çalışacağı saat; Regex ile yalnızca 00:00 - 23:59 formatı kabul edilir
    schedule_time: str = Field(default="09:00", pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")


class RagSectionConfig(BaseModel):
    """RAG, PDF Parçalama ve ChromaDB ayarları (`settings.yaml -> rag` bölümü)."""

    model_config = ConfigDict(extra="forbid")

    # Türkçe ve İngilizce metinleri aynı vektör uzayına eşleyen çok dilli model
    embedding_model: str = Field(
        default="sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
        min_length=1,
    )
    chroma_persist_dir: str = Field(default="data/chroma_db", min_length=1)
    collection_name: str = Field(default="candidate_profile", min_length=1)
    # Her bir metin parçasının (chunk) maksimum karakter uzunluğu (50 ile 10000 arası)
    chunk_size: int = Field(default=600, ge=50, le=10000)
    # Ardışık iki parça arasında bağlam kopmasını önleyen örtüşme (overlap) karakter sayısı
    chunk_overlap: int = Field(default=100, ge=0, le=5000)
    # Vektör aramasında getirilecek en alakalı parça sayısı
    top_k: int = Field(default=4, ge=1, le=50)

    @model_validator(mode="after")
    def validate_overlap_smaller_than_chunk(self) -> RagSectionConfig:
        """Çapraz alan doğrulaması: Örtüşme (overlap) miktarı parça boyutundan (chunk_size)
        büyük veya eşit olursa parçalayıcı sonsuz döngüye girebileceği için bunu engeller.
        """
        if self.chunk_overlap >= self.chunk_size:
            raise ValueError(
                f"chunk_overlap ({self.chunk_overlap}), chunk_size ({self.chunk_size}) değerinden küçük olmalıdır."
            )
        return self


class ScraperSectionConfig(BaseModel):
    """Web kazıyıcı, anti-aggregator domain kara listesi ve e-posta filtre ayarları
    (`settings.yaml -> scraper` bölümü).
    """

    model_config = ConfigDict(extra="forbid")

    # Playwright sayfa yükleme zaman aşımı (milisaniye cinsinden)
    timeout_ms: int = Field(default=20000, ge=1000, le=120000)
    # Anti-ban (bot engeli önleme) için istekler arası minimum ve maksimum bekleme süresi (saniye)
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
    # Şirket ana sayfasına girildiğinde taranacak alt sayfalar
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
    # HATA 1 ÇÖZÜMÜ: İlk prototipteki rehber/dizin siteleri tuzağını (clutch.co, f6s.com vb.)
    # daha siteye girmeden eleyecek olan Domain Kara Listesi
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
        ]
    )
    # Liste/blog sayfalarını ("Top 10 Robotics Companies" vb.) başlığından yakalayan kara liste
    blacklisted_title_patterns: list[str] = Field(
        default_factory=lambda: [
            "top 10",
            "top 20",
            "best robotics companies",
            "b2b şirketleri",
            "şirket rehberi",
        ]
    )
    # HATA 2 ÇÖZÜMÜ: İlk prototipteki support@f6s.com hatasını kesin olarak önleyen,
    # asla iş başvurusu gönderilmeyecek e-posta ön ekleri (prefix) kara listesi
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
    # 1. Öncelikli (Doğrudan İK / Kariyer) e-posta ön ekleri
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
    # 2. Öncelikli (İK maili bulunamazsa kullanılacak genel iletişim) e-posta ön ekleri
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
        """Minimum bekleme süresinin maksimum bekleme süresinden büyük olmamasını doğrular."""
        if self.rate_limit_min_sec > self.rate_limit_max_sec:
            raise ValueError(
                f"rate_limit_min_sec ({self.rate_limit_min_sec}) > rate_limit_max_sec ({self.rate_limit_max_sec}) olamaz."
            )
        return self


class FilterSectionConfig(BaseModel):
    """ATS (Başvuru Takip Sistemi) tespit motoru domain listesi (`settings.yaml -> filter`).

    Şirket sayfasında bu sistemlerden birinin linki varsa, şirkete otomatik mail atılmaz;
    kullanıcının manuel form doldurması için `MANUAL_ATS` kuyruğuna ayrılır.
    """

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
    """LLM içerik üretici ve çıktı doğrulayıcı ayarları (`settings.yaml -> generator`)."""

    model_config = ConfigDict(extra="forbid")

    gemini_model: str = Field(default="gemini-2.5-flash", min_length=1)
    # Üretilen niyet mektubunun çok kısa veya aşırı uzun olmasını önleyen kelime sınırları
    min_word_count: int = Field(default=60, ge=10, le=2000)
    max_word_count: int = Field(default=320, ge=20, le=5000)
    # Yaratıcılık/deterministiklik dengesi (0.4 profesyonel ve tutarlı metinler için idealdir)
    temperature: float = Field(default=0.4, ge=0.0, le=2.0)

    @model_validator(mode="after")
    def validate_word_counts(self) -> GeneratorSectionConfig:
        """Minimum kelime sayısının maksimum kelime sayısından küçük olduğunu doğrular."""
        if self.min_word_count >= self.max_word_count:
            raise ValueError(
                f"min_word_count ({self.min_word_count}) < max_word_count ({self.max_word_count}) olmalıdır."
            )
        return self


class MailerSectionConfig(BaseModel):
    """E-posta gönderim kotası, bekleme ve 30 gün kuralı ayarları (`settings.yaml -> mailer`)."""

    model_config = ConfigDict(extra="forbid")

    # Gmail hesabının spam'e düşmemesi için günlük maksimum gönderim limiti
    daily_limit: int = Field(default=10, ge=1, le=100)
    # Aynı şirkete/domain'e tekrar mail atmadan önce geçmesi gereken minimum gün sayısı
    cooldown_days: int = Field(default=30, ge=1, le=365)
    # İki mail gönderimi arasındaki rastgele bekleme aralığı (saniye)
    min_delay_sec: float = Field(default=15.0, ge=0.0, le=600.0)
    max_delay_sec: float = Field(default=45.0, ge=0.1, le=1800.0)

    @model_validator(mode="after")
    def validate_delays(self) -> MailerSectionConfig:
        """Gönderim gecikmesi alt sınırının üst sınırdan büyük olmamasını doğrular."""
        if self.min_delay_sec > self.max_delay_sec:
            raise ValueError(
                f"min_delay_sec ({self.min_delay_sec}) > max_delay_sec ({self.max_delay_sec}) olamaz."
            )
        return self


class DatabaseSectionConfig(BaseModel):
    """SQLite veritabanı dosya yolu ayarları (`settings.yaml -> database`)."""

    model_config = ConfigDict(extra="forbid")

    sqlite_path: str = Field(default="data/career_assistant.db", min_length=1)


class LoggingSectionConfig(BaseModel):
    """Merkezi loglama ayarları (`settings.yaml -> logging`)."""

    model_config = ConfigDict(extra="forbid")

    level: str = Field(default="INFO")
    log_file: str = Field(default="logs/career_assistant.log", min_length=1)
    # Dönen log dosyası (RotatingFileHandler) maksimum boyutu (varsayılan: 5 MB)
    max_bytes: int = Field(default=5242880, ge=1024)
    backup_count: int = Field(default=3, ge=0, le=50)

    @field_validator("level")
    @classmethod
    def validate_log_level(cls, value: str) -> str:
        """Log seviyesinin standart Python logging seviyelerinden biri olduğunu doğrular."""
        normalized = value.strip().upper()
        valid_levels = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        if normalized not in valid_levels:
            raise ValueError(f"Geçersiz log seviyesi: '{value}'. Geçerli değerler: {sorted(valid_levels)}")
        return normalized


class AppSettings(BaseModel):
    """Tüm alt konfigürasyon bölümlerini ve `.env` verilerini tek çatı altında toplayan kök model."""

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
    """Verilen `.env` dosyasından ve sistem ortam değişkenlerinden `EnvConfig` nesnesi üretir.

    Çalışma Mantığı:
    1. Eğer `.env` dosyası mevcutsa `dotenv_values` ile global ortamı kirletmeden okunur.
    2. Eğer `.env` dosyası yoksa sistem ÇÖKMEZ; güvenli varsayılan değerlerle (boş string vb.) devam eder.
    3. İşletim sistemi ortamında (`os.environ`) açıkça tanımlanmış bir değişken varsa `.env` dosyasına göre önceliklidir.
    """
    file_values: dict[str, str | None] = {}
    if env_path.exists():
        # Eğer yanlışlıkla dosya yerine bir klasör yolu verilmişse kontrollü hata fırlat
        if env_path.is_dir():
            raise ConfigurationError(f".env yolu bir klasör olamaz: {env_path}")
        try:
            file_values = dotenv_values(env_path, encoding="utf-8")
        except Exception as exc:
            raise ConfigurationError(f".env dosyası okunurken hata oluştu ({env_path}): {exc}") from exc

    # Python modelindeki alan adları ile .env dosyasındaki BÜYÜK_HARF anahtarların eşlemesi
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
        # Önce işletim sistemi ortam değişkenine bak, yoksa .env dosyasından al
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
    """YAML konfigürasyon dosyasını ve `.env` değişkenlerini okuyup tam doğrulamadan geçirir.

    Adım Adım İşleyiş:
    1. `yaml_path` dosyasının varlığını ve gerçekten bir dosya olduğunu kontrol eder.
    2. Dosyayı UTF-8 olarak okur ve `yaml.safe_load` ile ayrıştırır (kod çalıştırma açığını önlemek için `safe_load` şarttır).
    3. Boş YAML dosyalarını `{}` olarak kabul eder; kök yapısı sözlük (`dict`) değilse hata fırlatır.
    4. `_load_env_config` ile `.env` verilerini okur ve `AppSettings` Pydantic modelini oluşturur.

    Args:
        yaml_path: `settings.yaml` dosyasının yolu.
        env_path: `.env` dosyasının yolu (yoksa güvenli varsayılanlar kullanılır).

    Returns:
        Doğrulanmış `AppSettings` nesnesi.

    Raises:
        ConfigurationError: YAML dosyası bulunamadığında, bozuk formatta olduğunda
            veya şema doğrulaması (`ValidationError`) başarısız olduğunda.
    """
    yaml_file = Path(yaml_path)
    env_file = Path(env_path)

    # 1. YAML dosyasının varlık kontrolü
    if not yaml_file.exists() or not yaml_file.is_file():
        raise ConfigurationError(f"YAML konfigürasyon dosyası bulunamadı: {yaml_file}")

    # 2. Dosyayı UTF-8 karakter kodlamasıyla oku
    try:
        raw_text = yaml_file.read_text(encoding="utf-8")
    except Exception as exc:
        raise ConfigurationError(f"YAML dosyası okunamadı ({yaml_file}): {exc}") from exc

    # 3. YAML metnini Python veri yapısına ayrıştır
    try:
        parsed_yaml = yaml.safe_load(raw_text)
    except yaml.YAMLError as exc:
        raise ConfigurationError(f"Bozuk YAML formatı ({yaml_file}): {exc}") from exc

    # Dosya tamamen boşsa veya sadece yorum satırı içeriyorsa None döner -> boş sözlüğe çevir
    if parsed_yaml is None:
        parsed_yaml = {}

    # YAML kök yapısı bir liste veya düz metin ise reddet
    if not isinstance(parsed_yaml, dict):
        raise ConfigurationError(
            f"YAML kök yapısı bir sözlük (dict) olmalıdır, alınan tip: {type(parsed_yaml).__name__}"
        )

    # 4. Ortam değişkenlerini (.env) oku ve ana sözlüğe ekle
    env_config = _load_env_config(env_file)
    parsed_yaml["env"] = env_config

    # 5. Tüm ayarları Pydantic şeması ile doğrula
    try:
        return AppSettings(**parsed_yaml)
    except ValidationError as exc:
        raise ConfigurationError(f"Konfigürasyon şema doğrulama hatası ({yaml_file}): {exc}") from exc
