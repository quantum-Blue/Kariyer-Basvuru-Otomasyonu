"""Merkezi Loglama (Logger) Modülü.

Bu modülün amacı:
1. Uygulamanın tüm katmanlarında (RAG, Scraper, Filter, Generator, Mailer) üretilen
   kayıtları tek bir standart formatta (`Tarih | Seviye | Modül | Mesaj`) toplamak.
2. Türkçe karakterlerin (`ğ, ü, ş, i, ö, ç, İ, Ğ`) Windows ve Linux'ta bozulmaması için
   dosya yazımında `encoding="utf-8"` zorunluluğu getirmek.
3. Log dosyalarının diski doldurmasını önlemek için `RotatingFileHandler` (belirli boyuta
   ulaşınca eski logu yedekleyip yeni dosya açan yapı) kullanmak.
4. Aynı logger birden fazla kez çağrıldığında aynı mesajın ekrana 2-3 kez basılmasına
   yol açan "mükerrer (duplicate) handler" hatasını önlemektir.
"""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
import sys

# Standart log satırı şablonu: Örn. "2026-10-19 13:40:00 | INFO     | src.rag.pdf_loader | PDF okundu"
LOG_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

# İzin verilen standart Python log seviyeleri eşlemesi
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
    """Merkezi logger'ı UTF-8 dosya ve konsol (terminal) handler'ları ile yapılandırır.

    Neden Mükerrer Handler Kontrolü Var?
    Python'un yerleşik `logging.getLogger(name)` fonksiyonu her çağrıldığında aynı
    nesneyi (Singleton) döndürür. Eğer her çağrıda koşulsuz `logger.addHandler(...)`
    yapılırsa, bir log mesajı hem ekrana hem dosyaya defalarca yazılır. Bu fonksiyon
    mevcut handler'ları kontrol ederek bu sorunu tamamen ortadan kaldırır.

    Args:
        log_file: Log dosyasının yazılacağı yol (klasör yoksa otomatik oluşturulur).
        level: Log seviyesi ('DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL').
        logger_name: Logger örneğinin adı.
        max_bytes: Dönen log dosyası başına maksimum bayt boyutu (Varsayılan: 5 MB).
        backup_count: Saklanacak maksimum eski yedek log dosyası sayısı.

    Returns:
        Yapılandırılmış `logging.Logger` nesnesi.

    Raises:
        ValueError: Geçersiz bir log seviyesi veya negatif/sıfır boyut verildiğinde.
    """
    # 1. Parametrelerin geçerliliğini kontrol et
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

    # 2. Log dosyasının içinde bulunacağı klasörü (örn. logs/) yoksa oluştur
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise OSError(f"Log dizini oluşturulamadı ({log_path.parent}): {exc}") from exc

    # 3. İlgili isimdeki logger nesnesini al ve üst (root) logger'a çift kayıt sızmasını kapat
    logger = logging.getLogger(logger_name)
    logger.setLevel(numeric_level)
    logger.propagate = False

    # 4. Eğer bu logger zaten aynı log dosyası ile kurulmuşsa yeni handler ekleme (Duplicate Önleme)
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

    # 5. Eğer farklı bir log dosyası ile yeniden yapılandırılıyorsa, açık dosya tanıtıcılarını
    # (file descriptor) sızdırmamak için eski handler'ları güvenli şekilde kapat ve sil
    for handler in list(logger.handlers):
        try:
            handler.flush()
            handler.close()
        except Exception:
            pass
        logger.removeHandler(handler)

    formatter = logging.Formatter(fmt=LOG_FORMAT, datefmt=DATE_FORMAT)

    # 6. UTF-8 kodlamalı dönen dosya yazıcısını (RotatingFileHandler) oluştur
    file_handler = RotatingFileHandler(
        filename=str(log_path),
        mode="a",
        maxBytes=max_bytes,
        backupCount=backup_count,
        encoding="utf-8",
    )
    file_handler.setLevel(numeric_level)
    file_handler.setFormatter(formatter)

    # 7. Terminale (stdout) basacak olan konsol yazıcısını (StreamHandler) oluştur
    console_handler = logging.StreamHandler(stream=sys.stdout)
    console_handler.setLevel(numeric_level)
    console_handler.setFormatter(formatter)

    logger.addHandler(file_handler)
    logger.addHandler(console_handler)

    return logger


def get_logger(name: str = "career_assistant") -> logging.Logger:
    """Belirtilen isimdeki logger'ı döndürür.

    Eğer çağrılan logger'a henüz hiçbir handler eklenmemişse (ilk kez çağrılıyorsa),
    varsayılan ayarlarla `setup_logging` fonksiyonunu otomatik olarak devreye sokar.
    """
    logger = logging.getLogger(name)
    if not logger.handlers:
        return setup_logging(logger_name=name)
    return logger
