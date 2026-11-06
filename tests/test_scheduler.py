"""Faz 16: Günlük Zamanlayıcı (Scheduler) Birim ve Hata Avcılığı Testleri.

Bu test modülü şu kritik kuralları (TDD Red -> Green -> Bug Hunting) denetler:
1. Saat Formatı Doğrulama (`validate_schedule_time` / `AutonomousScheduler`):
   - `"09:00"`, `"00:00"`, `"23:59"` gibi geçerli 24-saat (`HH:MM`) formatlarını kabul etmeli.
   - `"25:00"`, `"09:60"`, `"9:00"`, `"abc"`, `""` gibi bozuk saat formatlarında anlamlı
     `SchedulerConfigError` fırlatmalı.
2. Günlük Tetikleme ve Aynı Gün İçinde Tekil Çalışma (`should_trigger_now` / `tick`):
   - Sistem saati hedef saate (`09:00`) ulaştığında görevi (`job_func`) tam 1 kez çalıştırmalı
     ve o günün tarihini (`last_run_date`) kaydetmeli.
   - Aynı gün içinde saat `09:00:15` veya `09:00:45` olduğunda görevi ikinci kez (mükerrer)
     TETİKLEMEMELİ!
   - Ertesi gün saat `09:00` olduğunda ise görevi tekrar çalıştırmalı.
3. **Hata İzolasyonlu Daemon Döngüsü (Fault-Tolerant Scheduler):**
   - Zamanlanmış görev (`job_func`) beklenmeyen bir istisna (`RuntimeError`) fırlatsa bile
     zamanlayıcı (`tick` veya `run_forever(max_iterations=...)`) ASLA çökmemeli; hatayı
     loglayıp bir sonraki güne/iterasyona sağ salim devam etmeli!
"""

from __future__ import annotations

from datetime import datetime

import pytest

from src.orchestrator.scheduler import (
    AutonomousScheduler,
    SchedulerConfigError,
    validate_schedule_time,
)


class TestAutonomousSchedulerTDDAndBugHunting:
    """`src/orchestrator/scheduler.py` modülünün birim ve hata avcılığı testleri."""

    @pytest.mark.parametrize(
        "valid_time, expected_hm",
        [
            ("09:00", (9, 0)),
            ("00:00", (0, 0)),
            ("23:59", (23, 59)),
            (" 14:30 ", (14, 30)),
        ],
    )
    def test_validate_schedule_time_accepts_valid_24h_times(
        self, valid_time: str, expected_hm: tuple[int, int]
    ) -> None:
        """Geçerli `HH:MM` saat dizgileri `(saat, dakika)` ikilisine doğru dönüştürülmeli."""
        assert validate_schedule_time(valid_time) == expected_hm

    @pytest.mark.parametrize(
        "invalid_time",
        [
            "",
            "   ",
            "24:00",
            "25:99",
            "09:60",
            "-01:00",
            "9:00",
            "09:5",
            "sabah_dokuz",
        ],
    )
    def test_invalid_schedule_time_raises_scheduler_config_error(
        self, invalid_time: str
    ) -> None:
        """Geçersiz saat formatlarında `SchedulerConfigError` fırlatılmalı."""
        with pytest.raises(SchedulerConfigError):
            validate_schedule_time(invalid_time)

        with pytest.raises(SchedulerConfigError):
            AutonomousScheduler(schedule_time=invalid_time, job_func=lambda: None)

    def test_scheduler_triggers_once_per_day_at_target_time_and_runs_again_next_day(self) -> None:
        """Zamanlayıcı hedef saatte (`09:00`) görevi çalıştırmalı, aynı gün içinde tekrar
        tetiklememeli ve ertesi gün `09:00`'da tekrar çalıştırmalı.
        """
        run_timestamps: list[datetime] = []
        simulated_clock = [
            datetime(2026, 11, 6, 8, 59, 0),   # Henüz 09:00 olmadı -> Çalışmamalı
            datetime(2026, 11, 6, 9, 0, 5),    # 1. Gün 09:00 -> ÇALIŞMALI (#1)
            datetime(2026, 11, 6, 9, 0, 45),   # 1. Gün hâlâ 09:00 -> Tekrar çalışmamalı!
            datetime(2026, 11, 7, 8, 58, 0),   # 2. Gün 08:58 -> Çalışmamalı
            datetime(2026, 11, 7, 9, 0, 10),   # 2. Gün 09:00 -> ÇALIŞMALI (#2)
        ]
        clock_idx = 0

        def fake_now() -> datetime:
            nonlocal clock_idx
            val = simulated_clock[min(clock_idx, len(simulated_clock) - 1)]
            clock_idx += 1
            return val

        scheduler = AutonomousScheduler(
            schedule_time="09:00",
            job_func=lambda: run_timestamps.append(simulated_clock[clock_idx - 1]),
            now_func=fake_now,
            sleep_func=lambda _: None,
        )

        scheduler.run_forever(max_iterations=5)

        assert len(run_timestamps) == 2
        assert run_timestamps[0].day == 6
        assert run_timestamps[1].day == 7

    def test_scheduler_isolates_job_exceptions_without_crashing_daemon(self) -> None:
        """Zamanlanmış görev (`job_func`) beklenmeyen bir hata (`RuntimeError`) fırlattığında
        daemon döngüsü çökmemeli; hatayı yakalayıp sonraki gün çalışmaya devam etmeli!
        """
        call_count = 0

        def flaky_job() -> None:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("1. gün internet koptu!")

        simulated_days = [
            datetime(2026, 11, 6, 9, 0, 0),  # 1. Gün (Hata fırlatacak ama daemon çökmeyecek!)
            datetime(2026, 11, 7, 9, 0, 0),  # 2. Gün (Başarıyla çalışacak!)
        ]
        idx = 0

        def fake_now() -> datetime:
            nonlocal idx
            val = simulated_days[min(idx, len(simulated_days) - 1)]
            idx += 1
            return val

        scheduler = AutonomousScheduler(
            schedule_time="09:00",
            job_func=flaky_job,
            now_func=fake_now,
            sleep_func=lambda _: None,
        )

        scheduler.run_forever(max_iterations=2)
        assert call_count == 2
        assert scheduler.error_count == 1
        assert scheduler.success_count == 1
