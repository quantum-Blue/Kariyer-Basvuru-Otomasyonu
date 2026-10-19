"""Merkezi Loglama (Logger) Modülü.

UTF-8 tam destekli (Türkçe karakter korumalı), hem konsola hem de dönen
dosyalara (RotatingFileHandler) kayıt yapan, mükerrer handler oluşumunu
engelleyen merkezi loglama altyapısı.
"""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
import sys

LOG_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

_VALID_LOG_LEVELS = {
    "DEBUG": logging.DEBUG,
    "INFO": logging.INFO,
    "WARNING": logging.WARNING,
    "ERROR": logging.ERROR,
    "CRITICAL": logging.CRITICAL,
}


def setup_logging(
    log_file: str | Path = "logs/career_assistant.log",
    level: str = "INFO",
    logger_name: str = "career_assistant",
    max_bytes: int = 5_242_880,
    backup_count: int = 3,
) -> logging.Logger:
    """Merkezi logger'ı UTF-8 dosya ve konsol handler'ları ile yapılandırır.

    Aynı `logger_name` ile tekrar çağrıldığında mükerrer (duplicate) handler
    eklemez; mevcut handler'ları temizleyip güncel ayarlarla yeniden kurar
    veya korur.

    Args:
        log_file: Log dosyasının yazılacağı yol.
        level: Log seviyesi ('DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL').
        logger_name: Logger örneğinin adı.
        max_bytes: Dönen log dosyası başına maksimum bayt boyutu.
        backup_count: Saklanacak yedek log dosyası sayısı.

    Returns:
        Yapılandırılmış `logging.Logger` nesnesi.

    Raises:
        ValueError: Geçersiz bir log seviyesi veya negatif `max_bytes` verildiğinde.
    """
    normalized_level = str(level).strip().upper()
    if normalized_level not in _VALID_LOG_LEVELS:
        raise ValueError(
            f"Geçersiz log seviyesi: '{level}'. Geçerli seviyeler: {sorted(_VALID_LOG_LEVELS.keys())}"
        )
    if max_bytes <= 0:
        raise ValueError(f"max_bytes pozitif bir tam sayı olmalıdır, alınan: {max_bytes}")
    if backup_count < 0:
        raise ValueError(f"backup_count negatif olamaz, alınan: {backup_count}")

    numeric_level = _VALID_LOG_LEVELS[normalized_level]
    log_path = Path(log_file).resolve()

    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise OSError(f"Log dizini oluşturulamadı ({log_path.parent}): {exc}") from exc

    logger = logging.getLogger(logger_name)
    logger.setLevel(numeric_level)
    logger.propagate = False

    # Eğer aynı log dosyası ile zaten yapılandırılmışsa mevcut handler'ları koru
    existing_file_handlers = [
        h for h in logger.handlers if isinstance(h, RotatingFileHandler)
    ]
    if (
        len(logger.handlers) == 2
        and len(existing_file_handlers) == 1
        and Path(existing_file_handlers[0].baseFilename).resolve() == log_path
    ):
        for handler in logger.handlers:
            handler.setLevel(numeric_level)
        return logger

    # Farklı bir dosya ile yeniden yapılandırılıyorsa eski handler'ları kapat ve temizle
    for handler in list(logger.handlers):
        try:
            handler.flush()
            handler.close()
        except Exception:
            pass
        logger.removeHandler(handler)

    formatter = logging.Formatter(fmt=LOG_FORMAT, datefmt=DATE_FORMAT)

    file_handler = RotatingFileHandler(
        filename=str(log_path),
        mode="a",
        maxBytes=max_bytes,
        backupCount=backup_count,
        encoding="utf-8",
    )
    file_handler.setLevel(numeric_level)
    file_handler.setFormatter(formatter)

    console_handler = logging.StreamHandler(stream=sys.stdout)
    console_handler.setLevel(numeric_level)
    console_handler.setFormatter(formatter)

    logger.addHandler(file_handler)
    logger.addHandler(console_handler)

    return logger


def get_logger(name: str = "career_assistant") -> logging.Logger:
    """Belirtilen isimdeki logger'ı döndürür.

    Eğer logger henüz hiçbir handler'a sahip değilse varsayılan ayarlarla başlatır.
    """
    logger = logging.getLogger(name)
    if not logger.handlers:
        return setup_logging(logger_name=name)
    return logger

