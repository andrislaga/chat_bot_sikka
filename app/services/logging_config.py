"""
logging_config.py — Konfigurasi Logging Terpusat SISKA
Menggantikan semua print() dan logging.basicConfig() yang tersebar.

Cara pakai di modul lain:
    from app.services.logging_config import get_logger
    log = get_logger(__name__)
    log.info("Scheduler dimulai")
    log.error("DB error", exc_info=True)
"""
import logging
import logging.handlers
import os
import sys

LOG_DIR   = os.getenv("LOG_DIR", "logs")
os.makedirs(LOG_DIR, exist_ok=True)

APP_LOG   = os.path.join(LOG_DIR, "siska_app.log")
ERROR_LOG = os.path.join(LOG_DIR, "siska_error.log")

_FMT_DETAIL = "%(asctime)s [%(levelname)-8s] %(name)-30s | %(message)s"
_FMT_SIMPLE = "%(asctime)s [%(levelname)s] %(message)s"
_DATE_FMT   = "%Y-%m-%d %H:%M:%S"


class _ColorFormatter(logging.Formatter):
    _COLORS = {
        logging.DEBUG   : "\033[36m",   # Cyan
        logging.INFO    : "\033[32m",   # Green
        logging.WARNING : "\033[33m",   # Yellow
        logging.ERROR   : "\033[31m",   # Red
        logging.CRITICAL: "\033[35m",   # Magenta
    }
    _RESET = "\033[0m"

    def format(self, record):
        color = self._COLORS.get(record.levelno, "")
        record.levelname = f"{color}{record.levelname:<8}{self._RESET}"
        return super().format(record)


def _is_color_supported() -> bool:
    return hasattr(sys.stdout, "isatty") and sys.stdout.isatty() and os.name != "nt"


def setup_logging(level: str = None):
    """Inisialisasi logging global. Panggil SEKALI di main.py sebelum app = FastAPI()."""
    log_level_str = level or os.getenv("LOG_LEVEL", "INFO").upper()
    log_level = getattr(logging, log_level_str, logging.INFO)

    root_logger = logging.getLogger()
    if root_logger.handlers:
        return  # Sudah diinisialisasi, hindari handler ganda

    root_logger.setLevel(logging.DEBUG)  # Root tangkap semua, handler yang filter

    # Handler 1: Console
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(log_level)
    fmt = _ColorFormatter(_FMT_SIMPLE, datefmt=_DATE_FMT) if _is_color_supported() \
          else logging.Formatter(_FMT_SIMPLE, datefmt=_DATE_FMT)
    console_handler.setFormatter(fmt)

    # Handler 2: File aplikasi — rotasi 5 MB x 5 file
    app_handler = logging.handlers.RotatingFileHandler(
        APP_LOG, maxBytes=5*1024*1024, backupCount=5, encoding="utf-8"
    )
    app_handler.setLevel(logging.INFO)
    app_handler.setFormatter(logging.Formatter(_FMT_DETAIL, datefmt=_DATE_FMT))

    # Handler 3: File error saja — rotasi 2 MB x 3 file
    error_handler = logging.handlers.RotatingFileHandler(
        ERROR_LOG, maxBytes=2*1024*1024, backupCount=3, encoding="utf-8"
    )
    error_handler.setLevel(logging.ERROR)
    error_handler.setFormatter(logging.Formatter(_FMT_DETAIL, datefmt=_DATE_FMT))

    root_logger.addHandler(console_handler)
    root_logger.addHandler(app_handler)
    root_logger.addHandler(error_handler)

    # Kurangi verbosity library pihak ketiga
    for lib in ("uvicorn.access", "httpx", "urllib3", "mysql.connector"):
        logging.getLogger(lib).setLevel(logging.WARNING)
    logging.getLogger("apscheduler").setLevel(logging.INFO)

    logging.getLogger(__name__).info(
        f"Logging aktif | level={log_level_str} | app={APP_LOG} | error={ERROR_LOG}"
    )


def get_logger(name: str) -> logging.Logger:
    """Shortcut — selalu pakai __name__ sebagai argumen."""
    return logging.getLogger(name)