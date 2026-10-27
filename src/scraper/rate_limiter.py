"""Asenkron Hız Sınırlayıcı (Rate Limiter) Modülü (src/scraper/rate_limiter.py).

Bu modülün amacı:
1. Web kazıma (scraping) sırasında hedef şirket sitelerine veya arama motorlarına
   saniyede puluhan istek gönderip sunucuları yormamak ve bot korumalarına (Cloudflare,
   WAF, IP ban) yakalanmamak.
2. **İnsansı Bekleme (Random Jitter):**
   Sabit bir süre (örneğin her seferinde tam 2.000 saniye) beklemek bot tespit
   algoritmaları tarafından kolayca fark edilir. Bu yüzden her istek arasında
   `min_sec` ile `max_sec` arasında rastgele bir ondalıklı süre (`random.uniform`) beklenir.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
import random

from src.utils.logger import get_logger

logger = get_logger(__name__)


class RateLimiter:
    """Ardışık web istekleri arasında rastgele (jitter'lı) gecikme uygulayan asenkron sınıf."""

    def __init__(
        self,
        min_sec: float = 2.0,
        max_sec: float = 5.0,
        sleep_func: Callable[[float], Awaitable[None]] | None = None,
    ) -> None:
        """Hız sınırlayıcıyı başlatır.

        Args:
            min_sec: İki istek arasındaki minimum bekleme süresi (saniye).
            max_sec: İki istek arasındaki maksimum bekleme süresi (saniye).
            sleep_func: Birim testlerde gerçek zamanlı beklemeyi (uykuyu) izole etmek
                için kullanılan enjekte edilebilir `async sleep` fonksiyonu.

        Raises:
            ValueError: `min_sec < 0`, `max_sec <= 0` veya `min_sec > max_sec` olduğunda.
        """
        if min_sec < 0:
            raise ValueError(f"min_sec negatif olamaz, alınan: {min_sec}")
        if max_sec <= 0:
            raise ValueError(f"max_sec pozitif olmalıdır, alınan: {max_sec}")
        if min_sec > max_sec:
            raise ValueError(f"min_sec ({min_sec}) > max_sec ({max_sec}) olamaz.")

        self.min_sec = float(min_sec)
        self.max_sec = float(max_sec)
        self._sleep_func = sleep_func or asyncio.sleep
        self._request_count = 0

    async def wait(self) -> float:
        """Bir sonraki isteğe geçmeden önce gerekliyse rastgele süre bekler.

        Not: İlk istekte (`_request_count == 0`) hiç beklenmez; yalnızca 2. ve sonraki
        ardışık isteklerin öncesinde bekleme uygulanır.

        Returns:
            Uygulanan bekleme süresi (saniye). İlk istekte `0.0` döner.
        """
        self._request_count += 1
        if self._request_count == 1:
            return 0.0

        delay = random.uniform(self.min_sec, self.max_sec)
        logger.debug("RateLimiter: bir sonraki istek öncesi %.2f sn bekleniyor...", delay)
        await self._sleep_func(delay)
        return delay

    def reset(self) -> None:
        """İstek sayacını sıfırlar."""
        self._request_count = 0
