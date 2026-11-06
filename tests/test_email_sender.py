"""Faz 13: Güvenli SMTP Gönderici, Kota Kontrolü ve DRY-RUN Simülatörü Birim ve Hata Avcılığı Testleri.

Bu test modülü şu kritik kuralları (TDD Red -> Green -> Bug Hunting) denetler:
1. **KRİTİK KABUL KRİTERİ #1 — Sıfır Riskli `DRY-RUN` Koruması (`dry_run=True`):**
   - `dry_run=True` iken ağ bağlantısı (`smtplib.SMTP_SSL` soketi) **KESİNLİKLE AÇILMAMALI**!
   - Gönderim simülasyonu veritabanına `ApplicationStatus.DRY_RUN` olarak yazılmalı, günlük
     gerçek gönderim kotasını (`get_today_sent_count`) veya 30 günlük cooldown süresini tüketmemeli.
2. **KRİTİK KABUL KRİTERİ #2 — SMTP Hata İzolasyonu (`SMTPAuthenticationError`, `SMTPException`, `OSError`):**
   - `dry_run=False` (canlı mod) iken Gmail Uygulama Şifresi yanlışsa (`SMTPAuthenticationError`),
     sunucu bağlantıyı koparırsa (`SMTPServerDisconnected`) veya ağ zaman aşımı (`TimeoutError`)
     oluşursa program ASLA ÇÖKMEMELİ!
   - Hata yakalanıp merkezi logger'a yazılmalı, veritabanına `ApplicationStatus.FAILED` olarak
     kaydedilmeli ve `SendResult(outcome=SendOutcome.FAILED, ...)` döndürülmeli.
3. **Günlük Kota (`daily_limit`) ve 30 Gün Cooldown Denetimi:**
   - Günlük gerçek gönderim sayısı (`get_today_sent_count`) `daily_limit` (örn. 10) değerine
     ulaştığında yeni gönderim reddedilmeli (`SendOutcome.QUOTA_EXCEEDED`) ve SMTP soketi açılmamalı.
   - Hedef e-posta veya şirket domain'i 30 günlük cooldown içindeyse (`is_in_cooldown == True`),
     gönderim engellenmeli (`SendOutcome.COOLDOWN_SKIPPED`) ve SMTP soketi açılmamalı.
4. **Kara Liste (`support@` vb.) Son Savunma Hattı ve İnsansı Gecikme (`min_delay_sec` – `max_delay_sec`):**
   - Yanlışlıkla `support@`, `noreply@` gibi kara listedeki bir adres `send_application_email`
     fonksiyonuna kadar gelse bile son savunma hattı bunu reddetmeli (`SendOutcome.INVALID_RECIPIENT`).
   - Ardışık gerçek gönderimler arasında `min_delay_sec` ile `max_delay_sec` arasında rastgele
     gecikme uygulanmalı.
"""

from __future__ import annotations

import smtplib
from pathlib import Path
from typing import Any

import pytest

from src.database.db_manager import ApplicationStatus, DatabaseManager
from src.mailer.email_sender import (
    EmailSender,
    SendOutcome,
    SendResult,
    SMTPConfig,
)


class FakeSMTPConnection:
    """`smtplib.SMTP_SSL` sınıfını simüle eden, açılan bağlantıları, `login` ve `send_message`
    çağrılarını kaydeden test dublörü (Test Double).
    """

    def __init__(
        self,
        host: str,
        port: int,
        timeout: float = 30.0,
        error_on_login: Exception | None = None,
        error_on_send: Exception | None = None,
    ) -> None:
        self.host = host
        self.port = port
        self.timeout = timeout
        self.error_on_login = error_on_login
        self.error_on_send = error_on_send
        self.logged_in_user: str = ""
        self.logged_in_password: str = ""
        self.sent_messages: list[Any] = []
        self.closed: bool = False

    def __enter__(self) -> FakeSMTPConnection:
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.closed = True

    def login(self, user: str, password: str) -> None:
        if self.error_on_login is not None:
            raise self.error_on_login
        self.logged_in_user = user
        self.logged_in_password = password

    def send_message(self, msg: Any) -> None:
        if self.error_on_send is not None:
            raise self.error_on_send
        self.sent_messages.append(msg)


class TestSafeSMTPSenderQuotaAndDryRunTDDAndBugHunting:
    """`src/mailer/email_sender.py` modülünün birim ve hata avcılığı testleri."""

    def setup_method(self) -> None:
        self.smtp_config = SMTPConfig(
            host="smtp.gmail.com",
            port=465,
            email="muhammedenesbal@gmail.com",
            password="abcd efgh ijkl mnop",
            sender_name="Muhammed Enes BAL",
        )

    def test_dry_run_mode_never_opens_smtp_socket_and_records_dry_run_in_db(
        self, tmp_path: Path
    ) -> None:
        """KRİTİK KABUL KRİTERİ #1 (Faz 13):
        `dry_run=True` iken SMTP fabrikası (`smtp_factory` / `smtplib.SMTP_SSL`) KESİNLİKLE
        çağrılmamalı (0 ağ bağlantısı!), sonuç `SendOutcome.DRY_RUN` olmalı ve SQLite
        veritabanına `ApplicationStatus.DRY_RUN` olarak kaydedilmeli!
        """
        db = DatabaseManager(sqlite_path=tmp_path / "dryrun.db", cooldown_days=30)
        socket_opened = False

        def forbidden_smtp_factory(host: str, port: int, timeout: float) -> Any:
            nonlocal socket_opened
            _ = (host, port, timeout)
            socket_opened = True
            raise AssertionError("DRY-RUN modunda SMTP soketi kesinlikle açılmamalıdır!")

        sender = EmailSender(
            smtp_config=self.smtp_config,
            db_manager=db,
            dry_run=True,
            daily_limit=10,
            smtp_factory=forbidden_smtp_factory,
        )

        result = sender.send_application_email(
            company_name="Baykar Otonom",
            company_url="https://baykar-otonom.example.tr",
            recipient_email="kariyer@baykar-otonom.example.tr",
            subject="Robotik Mühendisliği Başvurusu - Muhammed Enes BAL",
            body="Sayın Ekip, başvurum ve portfolyo bağlantım aşağıdadır.",
            language="TR",
        )

        assert isinstance(result, SendResult)
        assert socket_opened is False
        assert result.outcome == SendOutcome.DRY_RUN
        assert result.success is True
        assert db.get_statistics()["dry_run_count"] == 1
        assert db.get_today_sent_count() == 0

    def test_live_mode_sends_utf8_email_via_smtp_ssl_and_applies_human_delay(
        self, tmp_path: Path
    ) -> None:
        """`dry_run=False` (canlı mod) iken:
        - `SMTP_SSL` bağlantısı açılmalı, `login` ve `send_message` çağrılmalı,
        - Türkçe karakterler (`ğüşıöçĞÜŞİÖÇ`) UTF-8 olarak bozulmadan iletilmeli,
        - Veritabanına `ApplicationStatus.SENT` kaydedilmeli,
        - İkinci gönderim öncesinde `min_delay_sec` – `max_delay_sec` arasında rastgele
          bekleme uygulanmalı.
        """
        db = DatabaseManager(sqlite_path=tmp_path / "live.db", cooldown_days=30)
        created_connections: list[FakeSMTPConnection] = []
        recorded_sleeps: list[float] = []

        def fake_factory(host: str, port: int, timeout: float) -> FakeSMTPConnection:
            conn = FakeSMTPConnection(host, port, timeout)
            created_connections.append(conn)
            return conn

        sender = EmailSender(
            smtp_config=self.smtp_config,
            db_manager=db,
            dry_run=False,
            daily_limit=10,
            min_delay_sec=15.0,
            max_delay_sec=45.0,
            smtp_factory=fake_factory,
            sleep_func=lambda sec: recorded_sleeps.append(sec),
        )

        # 1. Şirkete gönderim (İlk gönderimde bekleme yapılmaz)
        res1 = sender.send_application_email(
            company_name="Aselsan",
            company_url="https://aselsan.example.tr",
            recipient_email="ik@aselsan.example.tr",
            subject="Başvuru ğüşıöçĞÜŞİÖÇ",
            body="Merhaba ğüşıöçĞÜŞİÖÇ, portfolyo: https://github.com/quantum-Blue",
            language="TR",
        )
        assert res1.outcome == SendOutcome.SENT
        assert res1.success is True
        assert len(recorded_sleeps) == 0

        # 2. Şirkete gönderim (Ardışık 2. canlı gönderim öncesinde 15-45 sn arası beklenmeli!)
        res2 = sender.send_application_email(
            company_name="AutoNav",
            company_url="https://autonav.example.io",
            recipient_email="hr@autonav.example.io",
            subject="Robotics Application",
            body="Hello team, portfolio: https://github.com/quantum-Blue",
            language="EN",
        )
        assert res2.outcome == SendOutcome.SENT
        assert len(recorded_sleeps) == 1
        assert 15.0 <= recorded_sleeps[0] <= 45.0

        assert len(created_connections) == 2
        assert created_connections[0].closed is True
        assert created_connections[0].logged_in_user == "muhammedenesbal@gmail.com"
        assert db.get_today_sent_count() == 2

    @pytest.mark.parametrize(
        "smtp_error",
        [
            smtplib.SMTPAuthenticationError(535, b"5.7.8 Username and Password not accepted"),
            smtplib.SMTPServerDisconnected("Connection unexpectedly closed"),
            smtplib.SMTPException("Generic SMTP failure"),
            TimeoutError("SMTP SSL handshake timed out"),
            ConnectionRefusedError("Connection refused by host"),
        ],
    )
    def test_smtp_authentication_or_network_error_does_not_crash_and_records_failed_in_db(
        self, tmp_path: Path, smtp_error: Exception
    ) -> None:
        """KRİTİK KABUL KRİTERİ #2 (Faz 13 Bug Hunting):
        `SMTPAuthenticationError` veya bağlantı kopmasında program ASLA çökmemeli;
        hatayı yakalayıp durumu `ApplicationStatus.FAILED` olarak DB'ye yazmalı ve
        cooldown/kota hakkını tüketmemeli!
        """
        db = DatabaseManager(sqlite_path=tmp_path / "fail.db", cooldown_days=30)

        def failing_factory(host: str, port: int, timeout: float) -> FakeSMTPConnection:
            return FakeSMTPConnection(host, port, timeout, error_on_login=smtp_error)

        sender = EmailSender(
            smtp_config=self.smtp_config,
            db_manager=db,
            dry_run=False,
            daily_limit=10,
            smtp_factory=failing_factory,
        )

        result = sender.send_application_email(
            company_name="Faulty Corp",
            company_url="https://faulty.example.com",
            recipient_email="hr@faulty.example.com",
            subject="Application",
            body="Body text",
            language="EN",
        )

        assert result.outcome == SendOutcome.FAILED
        assert result.success is False
        assert result.error_message != ""
        stats = db.get_statistics()
        assert stats["failed_count"] == 1
        assert stats["sent_count"] == 0
        # Başarısız gönderim cooldown başlatmamalı!
        assert db.is_in_cooldown("hr@faulty.example.com") is False

    def test_daily_quota_limit_blocks_sending_when_limit_reached(
        self, tmp_path: Path
    ) -> None:
        """Günlük gönderim kotası (`daily_limit=2`) dolduğunda 3. gönderim denemesi
        `SendOutcome.QUOTA_EXCEEDED` dönmeli ve SMTP bağlantısı açmamalı.
        """
        db = DatabaseManager(sqlite_path=tmp_path / "quota.db", cooldown_days=30)
        smtp_calls = 0

        def counting_factory(host: str, port: int, timeout: float) -> FakeSMTPConnection:
            nonlocal smtp_calls
            smtp_calls += 1
            return FakeSMTPConnection(host, port, timeout)

        sender = EmailSender(
            smtp_config=self.smtp_config,
            db_manager=db,
            dry_run=False,
            daily_limit=2,
            sleep_func=lambda _: None,
            smtp_factory=counting_factory,
        )

        r1 = sender.send_application_email("C1", "https://c1.com", "hr@c1.com", "S1", "B1")
        r2 = sender.send_application_email("C2", "https://c2.com", "hr@c2.com", "S2", "B2")
        r3 = sender.send_application_email("C3", "https://c3.com", "hr@c3.com", "S3", "B3")

        assert r1.outcome == SendOutcome.SENT
        assert r2.outcome == SendOutcome.SENT
        assert r3.outcome == SendOutcome.QUOTA_EXCEEDED
        assert r3.success is False
        assert smtp_calls == 2

    def test_cooldown_and_blacklisted_recipient_guards_prevent_sending(
        self, tmp_path: Path
    ) -> None:
        """1. Aynı şirketin farklı bir e-postasına (`kariyer@` -> `info@`) 30 gün içinde
              tekrar gönderim yapılmak istenirse `SendOutcome.COOLDOWN_SKIPPED` dönmeli.
        2. Yanlışlıkla `support@sirket.com` veya `support@f6s.com` gibi kara listedeki bir
           alıcı verilirse son güvenlik duvarı `SendOutcome.INVALID_RECIPIENT` ile engellemeli!
        """
        db = DatabaseManager(sqlite_path=tmp_path / "guards.db", cooldown_days=30)
        smtp_calls = 0

        def counting_factory(host: str, port: int, timeout: float) -> FakeSMTPConnection:
            nonlocal smtp_calls
            smtp_calls += 1
            return FakeSMTPConnection(host, port, timeout)

        sender = EmailSender(
            smtp_config=self.smtp_config,
            db_manager=db,
            dry_run=False,
            daily_limit=10,
            sleep_func=lambda _: None,
            smtp_factory=counting_factory,
        )

        # İlk gönderim: ik@sirket.com.tr -> Başarılı (SENT)
        first = sender.send_application_email(
            "Sirket", "https://sirket.com.tr", "ik@sirket.com.tr", "Konu", "Gövde"
        )
        assert first.outcome == SendOutcome.SENT
        assert smtp_calls == 1

        # Aynı şirketin farklı adresi: kariyer@sirket.com.tr -> Cooldown engellemeli!
        second = sender.send_application_email(
            "Sirket", "https://sirket.com.tr", "kariyer@sirket.com.tr", "Konu", "Gövde"
        )
        assert second.outcome == SendOutcome.COOLDOWN_SKIPPED
        assert smtp_calls == 1  # Yeni soket açılmadı!

        # Kara listedeki alıcı: support@f6s.com veya support@baskasirket.com -> Engellenmeli!
        bad_recipient = sender.send_application_email(
            "Other", "https://baskasirket.com", "support@baskasirket.com", "Konu", "Gövde"
        )
        assert bad_recipient.outcome == SendOutcome.INVALID_RECIPIENT
        assert smtp_calls == 1
