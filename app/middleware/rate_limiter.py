"""
rate_limiter.py — Sliding Window Rate Limiter untuk SISKA Webhook
Tidak memerlukan dependensi tambahan (pure stdlib).
"""
import time
import threading
from collections import defaultdict
from typing import Tuple


class SlidingWindowRateLimiter:
    """
    Thread-safe sliding window rate limiter berbasis in-memory.
    Aman digunakan dengan uvicorn multi-worker HANYA jika
    satu worker yang aktif (file-lock scheduler pattern).
    Untuk multi-worker sejati, ganti dengan Redis backend.
    """

    def __init__(self):
        self._store: dict = defaultdict(list)
        self._lock = threading.Lock()

    def is_allowed(
        self, key: str, max_requests: int, window_seconds: int
    ) -> Tuple[bool, int]:
        """
        Cek apakah request diizinkan.
        Return: (diizinkan: bool, retry_after_detik: int)
        """
        now = time.monotonic()
        window_start = now - window_seconds

        with self._lock:
            # Bersihkan timestamp yang sudah di luar window
            self._store[key] = [
                ts for ts in self._store[key] if ts > window_start
            ]

            count = len(self._store[key])
            if count >= max_requests:
                oldest = self._store[key][0]
                retry_after = int(oldest + window_seconds - now) + 1
                return False, max(retry_after, 1)

            self._store[key].append(now)
            return True, 0

    def cleanup(self, max_age_seconds: int = 300) -> int:
        """
        Hapus entry lama. Dipanggil oleh scheduler setiap beberapa menit.
        Return jumlah key yang dihapus.
        """
        now = time.monotonic()
        cutoff = now - max_age_seconds
        removed = 0

        with self._lock:
            keys_to_delete = [
                k for k, timestamps in self._store.items()
                if not any(ts > cutoff for ts in timestamps)
            ]
            for key in keys_to_delete:
                del self._store[key]
                removed += 1

        return removed


# ── Instance global (dibuat sekali saat import) ──────────────────────────────
webhook_limiter = SlidingWindowRateLimiter()

# Batas per-nomor HP: maks 15 pesan per 60 detik
PHONE_MAX_REQUESTS  = 15
PHONE_WINDOW_SECONDS = 60

# Batas global webhook: maks 300 request per 60 detik (safety net dari Meta)
GLOBAL_MAX_REQUESTS  = 300
GLOBAL_WINDOW_SECONDS = 60
GLOBAL_RATE_KEY      = "__global_webhook__"