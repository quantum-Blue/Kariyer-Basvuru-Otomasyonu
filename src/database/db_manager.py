"""SQLite Hafıza Yöneticisi ve 30 Gün Kuralı (Cooldown Engine) Modülü (src/database/db_manager.py).

Bu modülün amacı:
1. Otonom Kariyer Asistanının keşfettiği şirketleri, gönderilen/simüle edilen başvuru
   e-postalarını (`applications` tablosu) ve manuel form doldurulması gereken ATS bağlantılarının
   listesini (`manual_ats_queue` tablosu) yerel bir `SQLite` veritabanında kalıcı olarak tutmak.
2. **Çift Katmanlı 30 Gün Kuralı (Email + Domain Cooldown Engine — Faz 12 Kritik Kabul Kriteri):**
   - Bir şirkete başarılı gerçek başvuru (`ApplicationStatus.SENT`) yapıldıktan sonra 30 gün
     boyunca aynı şirketi tekrar rahatsız etmemek (anti-spam) en temel mühendislik kuralımızdır.
   - Sadece e-posta bazlı kontrol yapmak YETERSİZDİR! Çünkü bugün `ik@sirket.com.tr` adresine
     mail attıktan 5 gün sonra sistem aynı şirketin başka bir sayfasında `kariyer@sirket.com.tr`
     veya `info@sirket.com.tr` adresini bulabilir. Bu yüzden `check_cooldown` metodu **hem hedef
     e-posta adresini hem de şirketin kök alan adını (`domain`)** son 30 gündeki başarılı
     gönderimlerle karşılaştırır; ikisinden biri bile eşleşirse yeni gönderimi KESİN OLARAK engeller!
3. **`DRY_RUN` ve `FAILED` Kayıtlarının Cooldown ve Kotayı Tüketmemesi:**
   - Kullanıcı sistemi simülasyon modunda (`--dry-run` -> `ApplicationStatus.DRY_RUN`) çalıştırdığında
     veya anlık bir ağ/SMTP arızası (`ApplicationStatus.FAILED`) yaşandığında bu denemeler
     izlenebilirlik için veritabanına kaydedilir.
   - Ancak bu kayıtlar gerçekte karşı tarafa ulaşmış bir e-posta olmadığı için **ne 30 günlük
     cooldown süresini başlatır ne de günlük gönderim kotasını (`get_today_sent_count`) harcar!**
4. **Windows Dosya Kilidi (File Lock) Güvenliği:**
   - Her veritabanı işlemi kısa ömürlü ve `try...finally: conn.close()` korumalı bağlantılarla
     yürütülür; böylece Windows üzerinde `.db` dosyası asla asılı (locked) kalmaz.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
import sqlite3
from typing import Any, Iterator

from src.scraper.domain_filter import extract_clean_hostname
from src.scraper.email_extractor import clean_raw_email_string
from src.utils.logger import get_logger

logger = get_logger(__name__)


class DatabaseError(Exception):
    """SQLite veritabanı işlemleri veya parametre doğrulaması sırasında oluşan kontrollü hata."""


class ApplicationStatus(str, Enum):
    """Bir başvuru kaydının alabileceği durum kodları."""

    SENT = "SENT"            # Gerçek SMTP üzerinden başarıyla gönderildi (Cooldown ve Kotayı etkiler!)
    DRY_RUN = "DRY_RUN"      # Simülasyon modunda üretildi, gerçek mail atılmadı (Cooldown'u etkilemez)
    FAILED = "FAILED"        # SMTP / Ağ hatası nedeniyle gönderilemedi (Cooldown'u etkilemez)
    SKIPPED = "SKIPPED"      # Cooldown veya geçerli e-posta yokluğu nedeniyle atlandı


@dataclass(frozen=True)
class ApplicationRecord:
    """`applications` tablosundaki tek bir başvuru kaydını temsil eden veri modeli."""

    id: int
    company_name: str
    company_url: str
    company_domain: str
    target_email: str
    email_domain: str
    status: ApplicationStatus
    language: str
    subject: str
    body: str
    error_message: str
    sent_at: str


@dataclass(frozen=True)
class ManualATSRecord:
    """`manual_ats_queue` tablosundaki tek bir ATS başvuru kaydını temsil eden veri modeli."""

    id: int
    company_name: str
    company_url: str
    company_domain: str
    ats_url: str
    ats_provider: str
    language: str
    discovered_at: str


@dataclass(frozen=True)
class CooldownCheckResult:
    """30 günlük bekleme süresi (cooldown) sorgusunun detaylı sonucu."""

    in_cooldown: bool                  # Şirket veya e-posta şu an 30 günlük yasak kapsamında mı?
    matched_email: str = ""            # Eşleşen önceki gönderim e-postası
    matched_domain: str = ""           # Eşleşen önceki gönderim domain'i
    last_sent_at: str = ""             # En son başarılı gönderimin ISO-8601 zaman damgası
    reason: str = ""                   # İnsan tarafından okunabilir açıklama


def _extract_email_domain(email: str) -> str:
    """Verilen e-posta adresinden `@` sonrasındaki alan adını temizleyerek döndürür."""
    cleaned = clean_raw_email_string(email)
    if "@" not in cleaned:
        return ""
    _, domain = cleaned.rsplit("@", 1)
    return extract_clean_hostname(domain)


def _domains_share_company_identity(domain_a: str, domain_b: str) -> bool:
    """İki alan adının aynı kurumsal şirkete ait olup olmadığını (alt alan adları dahil) denetler.

    Örnek:
    - `'sirket.com.tr'` ile `'sirket.com.tr'` -> `True`
    - `'careers.robo-tech.com.tr'` ile `'robo-tech.com.tr'` -> `True`
    - `'sirket.com'` ile `'baskasirket.com'` -> `False`
    """
    a = (domain_a or "").strip().lower().rstrip(".")
    b = (domain_b or "").strip().lower().rstrip(".")
    if not a or not b:
        return False
    if a == b:
        return True
    return a.endswith(f".{b}") or b.endswith(f".{a}")


def _to_utc_datetime(dt: datetime | None) -> datetime:
    """Verilen `datetime` nesnesini UTC zaman dilimine normalize eder; `None` ise şu anı döndürür."""
    if dt is None:
        return datetime.now(timezone.utc)
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


class DatabaseManager:
    """SQLite tabanlı başvuru geçmişi, ATS kuyruğu ve 30 gün kuralı (Cooldown) yöneticisi."""

    def __init__(
        self,
        sqlite_path: str | Path = "data/career_assistant.db",
        cooldown_days: int = 30,
    ) -> None:
        if cooldown_days <= 0:
            raise DatabaseError(f"cooldown_days pozitif bir tam sayı olmalıdır, alınan: {cooldown_days}")

        self.sqlite_path = Path(sqlite_path)
        self.cooldown_days = int(cooldown_days)
        self._initialize_schema()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        """Her veritabanı işlemi için güvenli, otomatik commit/rollback yapan ve işlem
        biter bitmez bağlantıyı (`conn.close()`) kesin olarak kapatan bağlam yöneticisi.
        """
        try:
            self.sqlite_path.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(str(self.sqlite_path), timeout=15.0)
            conn.row_factory = sqlite3.Row
        except Exception as exc:
            raise DatabaseError(
                f"SQLite veritabanına bağlanılamadı ({self.sqlite_path}): {exc}"
            ) from exc

        try:
            yield conn
            conn.commit()
        except Exception as exc:
            conn.rollback()
            raise DatabaseError(f"SQLite işlem hatası: {exc}") from exc
        finally:
            conn.close()

    def _initialize_schema(self) -> None:
        """Veritabanı tablolarını ve hızlandırıcı indeksleri (index) oluşturur."""
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS applications (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    company_name TEXT NOT NULL,
                    company_url TEXT NOT NULL,
                    company_domain TEXT NOT NULL,
                    target_email TEXT NOT NULL,
                    email_domain TEXT NOT NULL,
                    status TEXT NOT NULL,
                    language TEXT NOT NULL DEFAULT 'TR',
                    subject TEXT NOT NULL DEFAULT '',
                    body TEXT NOT NULL DEFAULT '',
                    error_message TEXT NOT NULL DEFAULT '',
                    sent_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_apps_status_sent_at ON applications(status, sent_at)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_apps_target_email ON applications(target_email)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_apps_domains ON applications(email_domain, company_domain)"
            )

            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS manual_ats_queue (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    company_name TEXT NOT NULL,
                    company_url TEXT NOT NULL,
                    company_domain TEXT NOT NULL,
                    ats_url TEXT NOT NULL UNIQUE,
                    ats_provider TEXT NOT NULL DEFAULT '',
                    language TEXT NOT NULL DEFAULT 'TR',
                    discovered_at TEXT NOT NULL
                )
                """
            )

    def record_application(
        self,
        company_name: str,
        company_url: str,
        target_email: str,
        status: ApplicationStatus | str,
        language: str = "TR",
        subject: str = "",
        body: str = "",
        error_message: str = "",
        sent_at: datetime | None = None,
    ) -> int:
        """Bir başvuru denemesini (`SENT`, `DRY_RUN`, `FAILED`, `SKIPPED`) veritabanına kaydeder."""
        resolved_status = (
            status if isinstance(status, ApplicationStatus) else ApplicationStatus(str(status).upper())
        )
        clean_email = clean_raw_email_string(target_email)
        email_dom = _extract_email_domain(clean_email)
        comp_dom = extract_clean_hostname(company_url) or email_dom
        timestamp_iso = _to_utc_datetime(sent_at).isoformat()

        with self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO applications (
                    company_name, company_url, company_domain,
                    target_email, email_domain, status,
                    language, subject, body, error_message, sent_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    (company_name or "").strip() or comp_dom,
                    (company_url or "").strip(),
                    comp_dom,
                    clean_email,
                    email_dom,
                    resolved_status.value,
                    (language or "TR").strip().upper(),
                    subject or "",
                    body or "",
                    error_message or "",
                    timestamp_iso,
                ),
            )
            row_id = int(cursor.lastrowid or 0)

        logger.debug(
            "Veritabanına başvuru kaydedildi: id=%d, şirket='%s', mail='%s', durum=%s",
            row_id,
            company_name,
            clean_email,
            resolved_status.value,
        )
        return row_id

    def check_cooldown(
        self,
        target_email: str = "",
        company_url: str = "",
        reference_time: datetime | None = None,
    ) -> CooldownCheckResult:
        """Hedef e-postaya VEYA hedef şirketin domain'ine son `cooldown_days` (30) gün içinde
        gerçek bir gönderim (`status = 'SENT'`) yapılıp yapılmadığını denetler.

        Kritik Kurallar:
        1. Yalnızca `status = 'SENT'` olan kayıtlar dikkate alınır (`DRY_RUN` ve `FAILED` yoksayılır).
        2. `target_email` birebir eşleşiyorsa -> `in_cooldown = True`.
        3. E-postalar farklı olsa bile (`ik@sirket.com` vs `kariyer@sirket.com`), e-posta domain'i
           veya şirket web sitesi domain'i eşleşiyorsa -> `in_cooldown = True`!
        """
        now_utc = _to_utc_datetime(reference_time)
        cutoff_utc = now_utc - timedelta(days=self.cooldown_days)
        cutoff_iso = cutoff_utc.isoformat()

        clean_email = clean_raw_email_string(target_email)
        query_email_domain = _extract_email_domain(clean_email)
        query_company_domain = extract_clean_hostname(company_url)

        with self._connect() as conn:
            # Yalnızca son 30 gün içindeki başarılı gerçek gönderimleri (SENT) çek
            rows = conn.execute(
                """
                SELECT target_email, email_domain, company_domain, sent_at
                FROM applications
                WHERE status = ? AND sent_at >= ?
                ORDER BY sent_at DESC
                """,
                (ApplicationStatus.SENT.value, cutoff_iso),
            ).fetchall()

        for row in rows:
            prev_email = str(row["target_email"] or "")
            prev_email_dom = str(row["email_domain"] or "")
            prev_comp_dom = str(row["company_domain"] or "")
            prev_sent_at = str(row["sent_at"] or "")

            # Kural A: Birebir e-posta adresi eşleşmesi
            if clean_email and prev_email and clean_email == prev_email:
                reason = (
                    f"30 Gün Kuralı (E-posta Eşleşmesi): '{clean_email}' adresine "
                    f"{prev_sent_at} tarihinde zaten başvuru gönderilmiş."
                )
                return CooldownCheckResult(
                    in_cooldown=True,
                    matched_email=prev_email,
                    matched_domain=prev_email_dom or prev_comp_dom,
                    last_sent_at=prev_sent_at,
                    reason=reason,
                )

            # Kural B: Domain / Alt-Domain bazlı şirket kimliği eşleşmesi
            candidate_query_domains = [d for d in (query_email_domain, query_company_domain) if d]
            candidate_prev_domains = [d for d in (prev_email_dom, prev_comp_dom) if d]

            for q_dom in candidate_query_domains:
                for p_dom in candidate_prev_domains:
                    if _domains_share_company_identity(q_dom, p_dom):
                        reason = (
                            f"30 Gün Kuralı (Domain Eşleşmesi): '{q_dom}' (eşleşen: '{p_dom}', "
                            f"önceki mail: '{prev_email}') şirketine {prev_sent_at} tarihinde "
                            f"zaten başvuru gönderilmiş."
                        )
                        return CooldownCheckResult(
                            in_cooldown=True,
                            matched_email=prev_email,
                            matched_domain=p_dom,
                            last_sent_at=prev_sent_at,
                            reason=reason,
                        )

        return CooldownCheckResult(in_cooldown=False)

    def is_in_cooldown(
        self,
        target_email: str = "",
        company_url: str = "",
        reference_time: datetime | None = None,
    ) -> bool:
        """Şirketin veya e-postanın 30 günlük bekleme süresinde olup olmadığını `bool` olarak döndürür."""
        return self.check_cooldown(
            target_email=target_email,
            company_url=company_url,
            reference_time=reference_time,
        ).in_cooldown

    def get_today_sent_count(self, reference_time: datetime | None = None) -> int:
        """İçinde bulunulan UTC gününde (`00:00:00` sonrasında) gerçekleştirilen gerçek gönderimlerin
        (`status = 'SENT'`) sayısını döndürür. `DRY_RUN` ve `FAILED` kayıtları sayılmaz.
        """
        now_utc = _to_utc_datetime(reference_time)
        start_of_day = now_utc.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()

        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT COUNT(*) AS cnt
                FROM applications
                WHERE status = ? AND sent_at >= ?
                """,
                (ApplicationStatus.SENT.value, start_of_day),
            ).fetchone()
            return int(row["cnt"]) if row is not None else 0

    def record_manual_ats(
        self,
        company_name: str,
        company_url: str,
        ats_url: str,
        ats_provider: str = "",
        language: str = "TR",
        discovered_at: datetime | None = None,
    ) -> None:
        """Faz 9'da tespit edilen bir ATS başvuru sayfasını `manual_ats_queue` tablosuna
        mükerrer kayıt oluşturmadan (`ON CONFLICT(ats_url) DO UPDATE`) kaydeder.
        """
        clean_ats = (ats_url or "").strip()
        if not clean_ats:
            return

        comp_dom = extract_clean_hostname(company_url) or extract_clean_hostname(clean_ats)
        ts_iso = _to_utc_datetime(discovered_at).isoformat()

        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO manual_ats_queue (
                    company_name, company_url, company_domain,
                    ats_url, ats_provider, language, discovered_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(ats_url) DO UPDATE SET
                    company_name = excluded.company_name,
                    ats_provider = excluded.ats_provider
                """,
                (
                    (company_name or "").strip() or comp_dom,
                    (company_url or "").strip(),
                    comp_dom,
                    clean_ats,
                    (ats_provider or "").strip(),
                    (language or "TR").strip().upper(),
                    ts_iso,
                ),
            )

    def get_manual_ats_queue(self) -> list[ManualATSRecord]:
        """Kaydedilen tüm manuel ATS başvuru hedeflerini yeniden eskiye doğru döndürür."""
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT id, company_name, company_url, company_domain,
                       ats_url, ats_provider, language, discovered_at
                FROM manual_ats_queue
                ORDER BY id DESC
                """
            ).fetchall()

        return [
            ManualATSRecord(
                id=int(r["id"]),
                company_name=str(r["company_name"]),
                company_url=str(r["company_url"]),
                company_domain=str(r["company_domain"]),
                ats_url=str(r["ats_url"]),
                ats_provider=str(r["ats_provider"]),
                language=str(r["language"]),
                discovered_at=str(r["discovered_at"]),
            )
            for r in rows
        ]

    def get_recent_applications(self, limit: int = 50) -> list[ApplicationRecord]:
        """En son kaydedilen başvuru kayıtlarını döndürür."""
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT id, company_name, company_url, company_domain,
                       target_email, email_domain, status, language,
                       subject, body, error_message, sent_at
                FROM applications
                ORDER BY id DESC
                LIMIT ?
                """,
                (max(1, int(limit)),),
            ).fetchall()

        return [
            ApplicationRecord(
                id=int(r["id"]),
                company_name=str(r["company_name"]),
                company_url=str(r["company_url"]),
                company_domain=str(r["company_domain"]),
                target_email=str(r["target_email"]),
                email_domain=str(r["email_domain"]),
                status=ApplicationStatus(str(r["status"])),
                language=str(r["language"]),
                subject=str(r["subject"]),
                body=str(r["body"]),
                error_message=str(r["error_message"]),
                sent_at=str(r["sent_at"]),
            )
            for r in rows
        ]

    def get_statistics(self) -> dict[str, Any]:
        """CLI (`--stats`) raporlaması için veritabanındaki tüm kayıtların özet sayılarını döndürür."""
        with self._connect() as conn:
            status_rows = conn.execute(
                "SELECT status, COUNT(*) AS cnt FROM applications GROUP BY status"
            ).fetchall()
            ats_row = conn.execute(
                "SELECT COUNT(*) AS cnt FROM manual_ats_queue"
            ).fetchone()

        counts: dict[str, int] = {
            ApplicationStatus.SENT.value: 0,
            ApplicationStatus.DRY_RUN.value: 0,
            ApplicationStatus.FAILED.value: 0,
            ApplicationStatus.SKIPPED.value: 0,
        }
        for r in status_rows:
            st = str(r["status"])
            counts[st] = int(r["cnt"])

        total_apps = sum(counts.values())
        manual_ats_cnt = int(ats_row["cnt"]) if ats_row is not None else 0

        return {
            "sent_count": counts[ApplicationStatus.SENT.value],
            "dry_run_count": counts[ApplicationStatus.DRY_RUN.value],
            "failed_count": counts[ApplicationStatus.FAILED.value],
            "skipped_count": counts[ApplicationStatus.SKIPPED.value],
            "total_applications": total_apps,
            "manual_ats_count": manual_ats_cnt,
        }

    def close(self) -> None:
        """Bağlantılar her işlem sonunda otomatik kapatıldığı için uyumluluk amaçlı no-op metot."""
        return None
