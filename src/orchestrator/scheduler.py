"""Günlük Otonom Zamanlayıcı (Scheduler Daemon) Modülü (src/orchestrator/scheduler.py).

Bu modülün amacı:
1. Otonom Kariyer Asistanının her gün belirlenen saatte (`settings.app.schedule_time`,
   varsayılan: `"09:00"`) insan müdahalesi olmadan otomatik olarak çalışmasını sağlamak.
2. **Aynı Gün İçinde Tekil Tetikleme (`last_run_date` Koruması):**
   Zamanlayıcı dakikada birkaç kez saati kontrol eder. Saat `09:00` olduğunda görevi
   tetikler ve o günün tarihini (`date`) kaydeder; böylece saat `09:00:30` olduğunda
   aynı gün içinde ikinci kez tetiklenmez.
3. **Hata İzolasyonlu Daemon Döngüsü (Fault-Tolerant Daemon):**
   Zamanlanmış görev (`job_func`) çalışırken beklenmeyen bir hata oluşsa bile arka plan
   süreci (daemon) ASLA çökmez; hatayı loglar ve ertesi gün çalışmaya devam eder.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime
import re
import time
from typing import Any

from src.utils.logger import get_logger

logger = get_logger(__name__)

_TIME_24H_REGEX = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")


class SchedulerConfigError(Exception):
    """Zamanlayıcı saat formatı (`HH:MM`) geçersiz olduğunda fırlatılan hata."""


def validate_schedule_time(schedule_time: str) -> tuple[int, int]:
    """Verilen saat dizgesinin `00:00` – `23:59` (`HH:MM`) formatında olduğunu doğrular
    ve `(saat, dakika)` tam sayı ikilisini döndürür.
    """
    if not isinstance(schedule_time, str) or not schedule_time.strip():
        raise SchedulerConfigError("schedule_time boş olamaz (Örn: '09:00').")

    cleaned = schedule_time.strip()
    if not _TIME_24H_REGEX.match(cleaned):
        raise SchedulerConfigError(
            f"Geçersiz saat formatı: '{schedule_time}'. Beklenen 24-saat formatı: 'HH:MM' (Örn: '09:00')."
        )

    hour_str, minute_str = cleaned.split(":", 1)
    return int(hour_str), int(minute_str)


class AutonomousScheduler:
    """Her gün belirlenen saatte otonom başvuru hattını tetikleyen hata toleranslı zamanlayıcı."""

    def __init__(
        self,
        schedule_time: str = "09:00",
        job_func: Callable[[], Any] | None = None,
        poll_interval_sec: float = 20.0,
        now_func: Callable[[], datetime] | None = None,
        sleep_func: Callable[[float], None] | None = None,
    ) -> None:
        self.target_hour, self.target_minute = validate_schedule_time(schedule_time)
        self.schedule_time = f"{self.target_hour:02d}:{self.target_minute:02d}"
        if job_func is None:
            raise SchedulerConfigError("Zamanlayıcı için çalıştırılacak bir 'job_func' verilmelidir.")

        self.job_func = job_func
        self.poll_interval_sec = max(0.1, float(poll_interval_sec))
        self._now_func = now_func or datetime.now
        self._sleep_func = sleep_func or time.sleep

        self.last_run_date: date | None = None
        self.success_count: int = 0
        self.error_count: int = 0

    def should_trigger(self, current_dt: datetime) -> bool:
        """Verilen zaman damgasının hedef saat/dakika ile eşleşip eşleşmediğini ve bugün
        henüz çalıştırılmamış olduğunu kontrol eder.
        """
        if self.last_run_date == current_dt.date():
            return False
        return (
            current_dt.hour == self.target_hour
            and current_dt.minute == self.target_minute
        )

    def tick(self) -> bool:
        """Tek bir zaman kontrol adımı yürütür. Saat geldiyse görevi hata izolasyonuyla çalıştırır."""
        now_dt = self._now_func()
        if not self.should_trigger(now_dt):
            return False

        self.last_run_date = now_dt.date()
        logger.info("Zamanlanmış günlük görev tetiklendi (%s)...", self.schedule_time)

        try:
            self.job_func()
            self.success_count += 1
            logger.info("Zamanlanmış günlük görev başarıyla tamamlandı.")
        except Exception as exc:
            self.error_count += 1
            logger.error("Zamanlanmış görev sırasında hata oluştu (Daemon çalışmaya devam ediyor): %s", exc)

        return True

    def run_forever(self, max_iterations: int | None = None) -> None:
        """Zamanlayıcı döngüsünü sürekli (veya testlerde `max_iterations` kadar) çalıştırır."""
        iterations = 0
        logger.info("Otonom zamanlayıcı aktif: Her gün saat %s'de çalışacak.", self.schedule_time)

        while True:
            self.tick()
            iterations += 1
            if max_iterations is not None and iterations >= max_iterations:
                break
            self._sleep_func(self.poll_interval_sec)

