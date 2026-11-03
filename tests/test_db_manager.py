"""Faz 12: SQLite Hafıza Yöneticisi ve 30 Gün Kuralı (Cooldown Engine) Testleri.

Bu test modülü şu kritik kuralları (TDD Red -> Green -> Bug Hunting) denetler:
1. Kalıcı SQLite Şeması ve Bağlantı Güvenliği (`DatabaseManager`):
   - Veritabanı dosyası ve üst klasörü (`data/`) otomatik oluşturulmalı, her işlem sonunda
     dosya kilidi (Windows file lock) bırakmamalı.
   - Gönderim geçmişi (`applications`) ve manuel ATS kuyruğu (`manual_ats_queue`) tabloları
     idempotent olarak (`CREATE TABLE IF NOT EXISTS`) oluşturulmalı.
2. **KRİTİK KABUL KRİTERİ #1 — E-posta ve Domain Bazlı 30 Gün Kuralı (`is_in_cooldown`):**
   - `ik@baykar.com.tr` adresine başarılı bir gerçek gönderim (`ApplicationStatus.SENT`) yapıldıktan
     sonra 30 gün boyunca hem `ik@baykar.com.tr` adresine hem de aynı şirketin **farklı bir
     e-posta adresine (`kariyer@baykar.com.tr`, `info@baykar.com.tr` veya `hr@sub.baykar.com.tr`)**
     gönderim yapılması KESİN OLARAK ENGELLENMELİ (`is_in_cooldown == True`)!
   - 30 gün (örn. 31 gün) geçtikten sonra ise cooldown süresi dolmalı ve yeniden gönderime
     izin verilmeli (`is_in_cooldown == False`).
3. **KRİTİK KABUL KRİTERİ #2 — `DRY_RUN` ve `FAILED` Kayıtlarının Cooldown'u Tüketmemesi:**
   - Simülasyon (`ApplicationStatus.DRY_RUN`), başarısız gönderim (`ApplicationStatus.FAILED`)
     veya atlanan (`ApplicationStatus.SKIPPED`) kayıtlar veritabanına loglanmalı; ANCAK bu
     kayıtlar 30 günlük gerçek gönderim hakkını (`is_in_cooldown`) veya günlük gerçek gönderim
     kotasını (`get_today_sent_count`) ASLA tüketmemeli!
4. Manuel ATS Kuyruğu (`record_manual_ats` / `get_manual_ats_queue`) ve İstatistikler (`get_statistics`):
   - Faz 9'da `MANUAL_ATS` kararı verilen şirketler mükerrer kayıt oluşturmadan ATS kuyruğuna
     kaydedilmeli ve CLI'da (`--manual-list`, `--stats`) gösterilmek üzere çekilebilmeli.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from src.database.db_manager import (
    ApplicationRecord,
    ApplicationStatus,
    CooldownCheckResult,
    DatabaseError,
    DatabaseManager,
    ManualATSRecord,
)


class TestSQLiteDatabaseAndCooldownEngineTDDAndBugHunting:
    """`src/database/db_manager.py` modülünün birim ve hata avcılığı testleri."""

    def test_exact_email_and_domain_level_30_day_cooldown_blocks_same_company_different_emails(
        self, tmp_path: Path
    ) -> None:
        """KRİTİK KABUL KRİTERİ (Faz 12 Bug Hunting #2):
        `ik@sirket.com` adresine gerçek mail (`SENT`) atıldıktan 5 gün sonra aynı şirketin
        `kariyer@sirket.com` veya `info@sirket.com` adresi bulunursa, **domain bazlı 30 gün kuralı**
        devreye girip bunu da KESİN OLARAK engellemeli!
        Farklı bir şirketin (`hr@baskasirket.com`) adresi ise engellenmemeli.
        """
        db_path = tmp_path / "db" / "career_test.db"
        db = DatabaseManager(sqlite_path=db_path, cooldown_days=30)

        now = datetime(2026, 11, 3, 12, 0, 0, tzinfo=timezone.utc)
        five_days_ago = now - timedelta(days=5)

        # 5 gün önce 'ik@sirket.com.tr' adresine gerçek gönderim (SENT) yapılmış olsun
        db.record_application(
            company_name="Sirket Robotik A.Ş.",
            company_url="https://www.sirket.com.tr",
            target_email="ik@sirket.com.tr",
            status=ApplicationStatus.SENT,
            language="TR",
            subject="Başvuru",
            body="Test gövde",
            sent_at=five_days_ago,
        )

        # 1. Aynı e-posta adresi ('ik@sirket.com.tr') -> Cooldown'da olmalı!
        check_same_email = db.check_cooldown(
            target_email="ik@sirket.com.tr",
            company_url="https://sirket.com.tr",
            reference_time=now,
        )
        assert isinstance(check_same_email, CooldownCheckResult)
        assert check_same_email.in_cooldown is True
        assert "ik@sirket.com.tr" in check_same_email.reason

        # 2. Aynı şirketin FARKLI e-posta adresi ('kariyer@sirket.com.tr') -> Domain kuralıyla Cooldown'da olmalı!
        check_diff_email_same_domain = db.check_cooldown(
            target_email="kariyer@sirket.com.tr",
            company_url="https://sirket.com.tr/kariyer",
            reference_time=now,
        )
        assert check_diff_email_same_domain.in_cooldown is True
        assert "sirket.com.tr" in check_diff_email_same_domain.matched_domain

        # 3. Başka bir şirketin e-postası ('hr@autonav.io') -> Cooldown'da OLMAMALI!
        check_other_company = db.check_cooldown(
            target_email="hr@autonav.io",
            company_url="https://autonav.io",
            reference_time=now,
        )
        assert check_other_company.in_cooldown is False

    def test_dry_run_and_failed_records_do_not_consume_30_day_cooldown_or_daily_quota(
        self, tmp_path: Path
    ) -> None:
        """KRİTİK KABUL KRİTERİ (Faz 12 Bug Hunting #1):
        `DRY_RUN` (simülasyon), `FAILED` (başarısız) veya `SKIPPED` kayıtlar veritabanında
        saklanmalı, ANCAK 30 günlük gerçek gönderim hakkını (`check_cooldown`) ve günlük
        gönderim kotasını (`get_today_sent_count`) ASLA tüketmemeli!
        """
        db_path = tmp_path / "dry_run_test.db"
        db = DatabaseManager(sqlite_path=db_path, cooldown_days=30)

        now = datetime(2026, 11, 3, 14, 0, 0, tzinfo=timezone.utc)

        # Aynı şirkete bugün önce DRY_RUN, sonra FAILED kaydı düşelim
        db.record_application(
            company_name="Aselsan Test",
            company_url="https://aselsan-test.com.tr",
            target_email="kariyer@aselsan-test.com.tr",
            status=ApplicationStatus.DRY_RUN,
            language="TR",
            subject="Dry run konu",
            body="Dry run gövde",
            sent_at=now,
        )
        db.record_application(
            company_name="Aselsan Test",
            company_url="https://aselsan-test.com.tr",
            target_email="kariyer@aselsan-test.com.tr",
            status=ApplicationStatus.FAILED,
            language="TR",
            subject="Failed konu",
            body="Failed gövde",
            error_message="SMTP timeout",
            sent_at=now,
        )

        # Cooldown kilitlenmemiş olmalı!
        cooldown_res = db.check_cooldown(
            target_email="kariyer@aselsan-test.com.tr",
            company_url="https://aselsan-test.com.tr",
            reference_time=now,
        )
        assert cooldown_res.in_cooldown is False
        assert db.is_in_cooldown("kariyer@aselsan-test.com.tr", reference_time=now) is False

        # Günlük gerçek gönderim sayısı (SENT) hâlâ 0 olmalı!
        assert db.get_today_sent_count(reference_time=now) == 0

        # Şimdi gerçek gönderim (SENT) yapalım -> Cooldown devreye girmeli ve günlük sayaç 1 olmalı
        db.record_application(
            company_name="Aselsan Test",
            company_url="https://aselsan-test.com.tr",
            target_email="kariyer@aselsan-test.com.tr",
            status=ApplicationStatus.SENT,
            language="TR",
            subject="Gerçek gönderim",
            body="Gerçek gövde",
            sent_at=now,
        )
        assert db.is_in_cooldown("kariyer@aselsan-test.com.tr", reference_time=now) is True
        assert db.get_today_sent_count(reference_time=now) == 1

    def test_cooldown_expires_after_30_days_allowing_reapplication(
        self, tmp_path: Path
    ) -> None:
        """30 gün sınır testi:
        - Gönderimden 29 gün 23 saat sonra hâlâ `in_cooldown == True` olmalı.
        - Gönderimden 30 gün + 1 saniye (veya 31 gün) sonra `in_cooldown == False` olmalı.
        """
        db = DatabaseManager(sqlite_path=tmp_path / "expiry.db", cooldown_days=30)
        sent_time = datetime(2026, 9, 1, 10, 0, 0, tzinfo=timezone.utc)

        db.record_application(
            company_name="Expiry Robotics",
            company_url="https://expiry-robotics.io",
            target_email="hr@expiry-robotics.io",
            status=ApplicationStatus.SENT,
            sent_at=sent_time,
        )

        # 29 gün sonra -> Hâlâ Cooldown içinde
        at_29_days = sent_time + timedelta(days=29, hours=23)
        assert db.is_in_cooldown("info@expiry-robotics.io", reference_time=at_29_days) is True

        # 31 gün sonra -> Cooldown süresi dolmuş, yeniden başvuruya açık!
        at_31_days = sent_time + timedelta(days=31)
        assert db.is_in_cooldown("info@expiry-robotics.io", reference_time=at_31_days) is False

    def test_company_website_domain_matches_even_if_email_uses_external_or_sub_domain(
        self, tmp_path: Path
    ) -> None:
        """Eğer şirket web sitesi `https://www.robo-tech.com.tr` ise ve ilk başvuruda
        `ik@robo-tech.com.tr` adresine mail atıldıysa; 10 gün sonra aynı şirket sitesinde
        `kariyer@hr.robo-tech.com.tr` alt alan adlı bir e-posta bulunsa bile kök şirket
        domain'i (`robo-tech.com.tr`) üzerinden yakalanıp engellenmeli!
        """
        db = DatabaseManager(sqlite_path=tmp_path / "sub.db", cooldown_days=30)
        now = datetime(2026, 11, 3, 12, 0, 0, tzinfo=timezone.utc)

        db.record_application(
            company_name="RoboTech",
            company_url="https://www.robo-tech.com.tr/kariyer",
            target_email="ik@robo-tech.com.tr",
            status=ApplicationStatus.SENT,
            sent_at=now - timedelta(days=10),
        )

        res = db.check_cooldown(
            target_email="talent@careers.robo-tech.com.tr",
            company_url="https://robo-tech.com.tr",
            reference_time=now,
        )
        assert res.in_cooldown is True

    def test_manual_ats_queue_deduplicates_and_stores_ats_links_and_statistics(
        self, tmp_path: Path
    ) -> None:
        """Faz 9'da `MANUAL_ATS` olarak sınıflandırılan şirketler `record_manual_ats` ile
        kaydedilmeli, aynı ATS linki ikinci kez eklendiğinde mükerrer satır oluşturmamalı
        ve `get_statistics()` tüm durumların (`SENT`, `DRY_RUN`, `FAILED`, `MANUAL_ATS`)
        özetini doğru döndürmeli.
        """
        db = DatabaseManager(sqlite_path=tmp_path / "ats_stats.db", cooldown_days=30)

        db.record_manual_ats(
            company_name="AutoNav Inc",
            company_url="https://autonav.io",
            ats_url="https://boards.greenhouse.io/autonav",
            ats_provider="greenhouse.io",
            language="EN",
        )
        # Aynı ATS URL ikinci kez kaydedildiğinde duplicate olmamalı (idempotent upsert)
        db.record_manual_ats(
            company_name="AutoNav Inc Updated",
            company_url="https://autonav.io",
            ats_url="https://boards.greenhouse.io/autonav",
            ats_provider="greenhouse.io",
            language="EN",
        )

        ats_list = db.get_manual_ats_queue()
        assert len(ats_list) == 1
        assert isinstance(ats_list[0], ManualATSRecord)
        assert ats_list[0].ats_url == "https://boards.greenhouse.io/autonav"
        assert ats_list[0].ats_provider == "greenhouse.io"

        # Birkaç başvuru kaydı ekleyip istatistikleri kontrol et
        db.record_application(
            company_name="C1",
            company_url="https://c1.com",
            target_email="hr@c1.com",
            status=ApplicationStatus.SENT,
        )
        db.record_application(
            company_name="C2",
            company_url="https://c2.com",
            target_email="hr@c2.com",
            status=ApplicationStatus.DRY_RUN,
        )

        stats = db.get_statistics()
        assert stats["sent_count"] == 1
        assert stats["dry_run_count"] == 1
        assert stats["failed_count"] == 0
        assert stats["manual_ats_count"] == 1
        assert stats["total_applications"] == 2

    def test_invalid_inputs_and_windows_file_lock_cleanup(self, tmp_path: Path) -> None:
        """Geçersiz `cooldown_days <= 0` verildiğinde `DatabaseError` fırlatılmalı ve
        Windows üzerinde veritabanı dosyası işlem sonrası silinebilmeli (açık connection kalmamalı).
        """
        with pytest.raises(DatabaseError):
            DatabaseManager(sqlite_path=tmp_path / "bad.db", cooldown_days=0)

        db_file = tmp_path / "lock_test.db"
        db = DatabaseManager(sqlite_path=db_file, cooldown_days=30)
        db.record_application(
            company_name="LockTest",
            company_url="https://locktest.com",
            target_email="hr@locktest.com",
            status=ApplicationStatus.DRY_RUN,
        )
        db.close()
        db_file.unlink()
        assert not db_file.exists()
