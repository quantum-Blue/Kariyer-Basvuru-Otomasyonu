"""Güvenli SMTP Gönderici, Kota Kontrolü ve DRY-RUN Simülatörü (src/mailer/email_sender.py).

Bu modülün amacı:
1. **Sıfır Riskli `DRY-RUN` Koruması (Faz 13 Kritik Kabul Kriteri #1):**
   Sistem varsayılan olarak simülasyon (`dry_run=True`) modunda çalışır. `dry_run=True` iken
   ağ soketi (`smtplib.SMTP_SSL`) **KESİNLİKLE AÇILMAZ!** E-posta gönderimi simüle edilir
   ve `DatabaseManager` içerisine `ApplicationStatus.DRY_RUN` olarak kaydedilir.
2. **Son Savunma Hattı (Kara Liste & 30 Gün Cooldown & Günlük Kota Kontrolü):**
   Bir e-posta gönderilmeden hemen önce 3 güvenlik kapısından geçer:
   - **Kapı 1 (Kara Liste Kontrolü):** Alıcı adresi `classify_email_address` ile denetlenir;
     `support@`, `noreply@`, `sales@` veya `f6s.com` gibi bir `BLACKLISTED` adres ise anında
     reddedilir (`SendOutcome.INVALID_RECIPIENT`).
   - **Kapı 2 (30 Gün Cooldown Kontrolü):** `db_manager.check_cooldown` ile alıcı e-postaya
     veya şirketin domain'ine son 30 gün içinde gerçek mail (`SENT`) atılıp atılmadığı denetlenir.
   - **Kapı 3 (Günlük Kota Kontrolü):** `db_manager.get_today_sent_count()` değeri `daily_limit`
     (varsayılan: 10) sınırına ulaşmışsa gönderim durdurulur (`SendOutcome.QUOTA_EXCEEDED`).
3. **Hata İzolasyonu (`SMTPAuthenticationError` / Ağ Kopması — Faz 13 Kritik Kabul Kriteri #2):**
   Canlı modda (`dry_run=False`) Gmail Uygulama Şifresi hatası (`SMTPAuthenticationError`),
   sunucu kopması veya zaman aşımı yaşanırsa program **ASLA ÇÖKMEZ**; hatayı loglar, durumu
   `ApplicationStatus.FAILED` olarak veritabanına yazar ve `SendResult(outcome=SendOutcome.FAILED)`
   döndürür.
4. **İnsansı Gecikme (Random Jitter Delay):**
   Ardışık canlı gönderimler arasında Gmail spam korumasına takılmamak için `min_delay_sec`
   ile `max_delay_sec` arasında rastgele (`random.uniform`) süre beklenir.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from email.header import Header
from email.mime.text import MIMEText
from email.utils import formataddr
from enum import Enum
import random
import smtplib
import ssl
import time
from typing import Any

from src.config.settings import EnvConfig
from src.database.db_manager import ApplicationStatus, DatabaseManager
from src.scraper.email_extractor import EmailTier, classify_email_address, clean_raw_email_string
from src.utils.logger import get_logger

logger = get_logger(__name__)


class SendOutcome(str, Enum):
    """Gönderim denemesinin nihai durum kodu."""

    SENT = "SENT"                              # Gerçek SMTP üzerinden başarıyla gönderildi
    DRY_RUN = "DRY_RUN"                        # Simülasyon modunda kaydedildi (Ağ bağlantısı açılmadı!)
    FAILED = "FAILED"                          # SMTP veya ağ hatası oluştu (DB'ye FAILED yazıldı)
    COOLDOWN_SKIPPED = "COOLDOWN_SKIPPED"      # 30 gün kuralına takıldığı için atlandı
    QUOTA_EXCEEDED = "QUOTA_EXCEEDED"          # Günlük gönderim kotası dolduğu için durduruldu
    INVALID_RECIPIENT = "INVALID_RECIPIENT"    # Kara listedeki (support@ vb.) veya geçersiz alıcı engellendi


@dataclass(frozen=True)
class SMTPConfig:
    """Gmail / Kurumsal SMTP SSL bağlantı yapılandırması."""

    host: str = "smtp.gmail.com"
    port: int = 465
    email: str = ""
    password: str = ""
    sender_name: str = "Muhammed Enes BAL"
    timeout_sec: float = 30.0

    @classmethod
    def from_env_config(cls, env_cfg: EnvConfig) -> SMTPConfig:
        """`EnvConfig` nesnesinden `SMTPConfig` oluşturur."""
        return cls(
            host=env_cfg.smtp_host,
            port=env_cfg.smtp_port,
            email=env_cfg.smtp_email,
            password=env_cfg.smtp_password,
            sender_name=env_cfg.sender_name,
        )


@dataclass(frozen=True)
class SendResult:
    """Tek bir e-posta gönderim işleminin detaylı sonucu."""

    outcome: SendOutcome
    recipient_email: str
    company_name: str
    subject: str
    error_message: str = ""
    delay_applied_sec: float = 0.0

    @property
    def success(self) -> bool:
        """İşlem başarılı bir gönderim (`SENT`) veya başarılı bir simülasyon (`DRY_RUN`) ise `True`."""
        return self.outcome in (SendOutcome.SENT, SendOutcome.DRY_RUN)


def _default_smtp_ssl_factory(host: str, port: int, timeout: float) -> Any:
    """Güvenli TLS/SSL bağlamı (`ssl.create_default_context()`) ile `smtplib.SMTP_SSL` açar."""
    context = ssl.create_default_context()
    return smtplib.SMTP_SSL(host=host, port=port, timeout=timeout, context=context)


def build_utf8_mime_message(
    sender_name: str,
    sender_email: str,
    recipient_email: str,
    subject: str,
    body: str,
) -> MIMEText:
    """Türkçe karakterleri (`ğüşıöçĞÜŞİÖÇ`) ve uluslararası sembolleri hiçbir e-posta
    istemcisinde bozulmayacak şekilde UTF-8 düz metin (`text/plain; charset=utf-8`)
    olarak paketler. Asla fiziksel dosya eki (`attachment`) barındırmaz.
    """
    msg = MIMEText(body, _subtype="plain", _charset="utf-8")
    msg["Subject"] = str(Header(subject, "utf-8"))
    msg["From"] = formataddr((str(Header(sender_name, "utf-8")), sender_email))
    msg["To"] = recipient_email
    return msg


class EmailSender:
    """Kota kontrolü, 30 gün cooldown denetimi, kara liste son savunma hattı ve
    sıfır riskli `DRY-RUN` korumasına sahip güvenli SMTP gönderici.
    """

    def __init__(
        self,
        smtp_config: SMTPConfig,
        db_manager: DatabaseManager,
        dry_run: bool = True,
        daily_limit: int = 10,
        min_delay_sec: float = 15.0,
        max_delay_sec: float = 45.0,
        smtp_factory: Callable[[str, int, float], Any] | None = None,
        sleep_func: Callable[[float], None] | None = None,
    ) -> None:
        if daily_limit <= 0:
            raise ValueError(f"daily_limit pozitif olmalıdır, alınan: {daily_limit}")
        if min_delay_sec < 0 or max_delay_sec < min_delay_sec:
            raise ValueError(
                f"Geçersiz gecikme aralığı: min={min_delay_sec}, max={max_delay_sec}"
            )

        self.smtp_config = smtp_config
        self.db_manager = db_manager
        self.dry_run = bool(dry_run)
        self.daily_limit = int(daily_limit)
        self.min_delay_sec = float(min_delay_sec)
        self.max_delay_sec = float(max_delay_sec)
        self._smtp_factory = smtp_factory or _default_smtp_ssl_factory
        self._sleep_func = sleep_func or time.sleep
        self._live_sent_in_session: int = 0

    def send_application_email(
        self,
        company_name: str,
        company_url: str,
        recipient_email: str,
        subject: str,
        body: str,
        language: str = "TR",
        reference_time: datetime | None = None,
    ) -> SendResult:
        """Başvuru e-postasını tüm güvenlik kapılarından geçirerek gönderir veya simüle eder."""
        clean_recipient = clean_raw_email_string(recipient_email)

        # ------------------------------------------------------------------
        # KAPI 1: SON SAVUNMA HATTI — Alıcı E-posta Kara Liste Kontrolü
        # ------------------------------------------------------------------
        tier = classify_email_address(clean_recipient)
        if not clean_recipient or tier == EmailTier.BLACKLISTED:
            err = f"Alıcı e-posta adresi geçersiz veya kara listede (support@ vb.): '{recipient_email}'"
            logger.warning(err)
            return SendResult(
                outcome=SendOutcome.INVALID_RECIPIENT,
                recipient_email=clean_recipient,
                company_name=company_name,
                subject=subject,
                error_message=err,
            )

        # ------------------------------------------------------------------
        # KAPI 2: 30 GÜN KURALI (E-POSTA + DOMAIN COOLDOWN) KONTROLÜ
        # ------------------------------------------------------------------
        cooldown_check = self.db_manager.check_cooldown(
            target_email=clean_recipient,
            company_url=company_url,
            reference_time=reference_time,
        )
        if cooldown_check.in_cooldown:
            logger.info("Gönderim 30 gün kuralına takıldı: %s", cooldown_check.reason)
            self.db_manager.record_application(
                company_name=company_name,
                company_url=company_url,
                target_email=clean_recipient,
                status=ApplicationStatus.SKIPPED,
                language=language,
                subject=subject,
                body=body,
                error_message=cooldown_check.reason,
                sent_at=reference_time,
            )
            return SendResult(
                outcome=SendOutcome.COOLDOWN_SKIPPED,
                recipient_email=clean_recipient,
                company_name=company_name,
                subject=subject,
                error_message=cooldown_check.reason,
            )

        # ------------------------------------------------------------------
        # KAPI 3: GÜNLÜK GÖNDERİM KOTASI KONTROLÜ
        # ------------------------------------------------------------------
        today_sent = self.db_manager.get_today_sent_count(reference_time=reference_time)
        if today_sent >= self.daily_limit:
            msg = f"Günlük gönderim kotası doldu ({today_sent}/{self.daily_limit})."
            logger.warning(msg)
            return SendResult(
                outcome=SendOutcome.QUOTA_EXCEEDED,
                recipient_email=clean_recipient,
                company_name=company_name,
                subject=subject,
                error_message=msg,
            )

        # ------------------------------------------------------------------
        # KAPI 4: SIFIR RİSKLİ DRY-RUN SİMÜLASYON MODU (AĞ BAĞLANTISI AÇILMAZ!)
        # ------------------------------------------------------------------
        if self.dry_run:
            logger.info(
                "[DRY-RUN] E-posta simüle edildi (SMTP soketi açılmadı): alıcı='%s', şirket='%s'",
                clean_recipient,
                company_name,
            )
            self.db_manager.record_application(
                company_name=company_name,
                company_url=company_url,
                target_email=clean_recipient,
                status=ApplicationStatus.DRY_RUN,
                language=language,
                subject=subject,
                body=body,
                sent_at=reference_time,
            )
            return SendResult(
                outcome=SendOutcome.DRY_RUN,
                recipient_email=clean_recipient,
                company_name=company_name,
                subject=subject,
            )

        # ------------------------------------------------------------------
        # KAPI 5: CANLI SMTP GÖNDERİMİ (dry_run=False)
        # ------------------------------------------------------------------
        if not self.smtp_config.email.strip() or not self.smtp_config.password.strip():
            cred_err = "SMTP_EMAIL veya SMTP_PASSWORD tanımlanmamış (.env kontrol edin)."
            logger.error(cred_err)
            self.db_manager.record_application(
                company_name=company_name,
                company_url=company_url,
                target_email=clean_recipient,
                status=ApplicationStatus.FAILED,
                language=language,
                subject=subject,
                body=body,
                error_message=cred_err,
                sent_at=reference_time,
            )
            return SendResult(
                outcome=SendOutcome.FAILED,
                recipient_email=clean_recipient,
                company_name=company_name,
                subject=subject,
                error_message=cred_err,
            )

        # Ardışık 2. ve sonraki canlı gönderimler öncesinde insansı rastgele bekleme uygula
        applied_delay = 0.0
        if self._live_sent_in_session > 0 and self.max_delay_sec > 0:
            applied_delay = random.uniform(self.min_delay_sec, self.max_delay_sec)
            logger.info("Canlı gönderim öncesi %.1f saniye insansı bekleme uygulanıyor...", applied_delay)
            self._sleep_func(applied_delay)

        mime_msg = build_utf8_mime_message(
            sender_name=self.smtp_config.sender_name,
            sender_email=self.smtp_config.email,
            recipient_email=clean_recipient,
            subject=subject,
            body=body,
        )

        try:
            with self._smtp_factory(
                self.smtp_config.host,
                self.smtp_config.port,
                self.smtp_config.timeout_sec,
            ) as server:
                server.login(self.smtp_config.email, self.smtp_config.password)
                server.send_message(mime_msg)

            self._live_sent_in_session += 1
            self.db_manager.record_application(
                company_name=company_name,
                company_url=company_url,
                target_email=clean_recipient,
                status=ApplicationStatus.SENT,
                language=language,
                subject=subject,
                body=body,
                sent_at=reference_time,
            )
            logger.info(
                "[CANLI GÖNDERİM BAŞARILI] E-posta iletildi: alıcı='%s', şirket='%s'",
                clean_recipient,
                company_name,
            )
            return SendResult(
                outcome=SendOutcome.SENT,
                recipient_email=clean_recipient,
                company_name=company_name,
                subject=subject,
                delay_applied_sec=applied_delay,
            )
        except Exception as exc:
            err_str = f"SMTP gönderim hatası ({type(exc).__name__}): {exc}"
            logger.error("E-posta gönderilemedi (%s -> %s): %s", company_name, clean_recipient, err_str)
            self.db_manager.record_application(
                company_name=company_name,
                company_url=company_url,
                target_email=clean_recipient,
                status=ApplicationStatus.FAILED,
                language=language,
                subject=subject,
                body=body,
                error_message=err_str,
                sent_at=reference_time,
            )
            return SendResult(
                outcome=SendOutcome.FAILED,
                recipient_email=clean_recipient,
                company_name=company_name,
                subject=subject,
                error_message=err_str,
                delay_applied_sec=applied_delay,
            )
