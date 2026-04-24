"""
notification_manager.py — SSE Notification Manager untuk SISKA Admin
=====================================================================
Singleton yang mengelola koneksi SSE dari admin dashboard.
Ketika ada pesan baru dari user, manager ini broadcast event
ke semua admin yang sedang terhubung secara real-time.

Cara kerja:
  - Setiap tab admin yang buka dashboard akan subscribe() → dapat Queue
  - Webhook memanggil broadcast() saat pesan agen masuk
  - SSE endpoint membaca dari Queue dan stream ke browser
"""

import asyncio
import json
import logging
from typing import List

log = logging.getLogger(__name__)


class NotificationManager:
    def __init__(self):
        self._queues: List[asyncio.Queue] = []

    def subscribe(self) -> asyncio.Queue:
        """Daftarkan koneksi admin baru, kembalikan Queue-nya."""
        q = asyncio.Queue(maxsize=50)
        self._queues.append(q)
        log.debug(f"SSE client terhubung. Total: {len(self._queues)}")
        return q

    def unsubscribe(self, q: asyncio.Queue):
        """Hapus koneksi admin yang sudah disconnect."""
        try:
            self._queues.remove(q)
            log.debug(f"SSE client disconnect. Total: {len(self._queues)}")
        except ValueError:
            pass

    async def broadcast(self, event: dict):
        """
        Kirim event ke SEMUA admin yang sedang terhubung.
        Koneksi yang penuh / mati akan dibersihkan otomatis.
        """
        if not self._queues:
            return

        msg = json.dumps(event, ensure_ascii=False)
        dead: List[asyncio.Queue] = []

        for q in self._queues:
            try:
                q.put_nowait(msg)
            except asyncio.QueueFull:
                log.warning("SSE queue penuh, client mungkin lambat — skip")
                dead.append(q)
            except Exception as e:
                log.error(f"SSE broadcast error: {e}")
                dead.append(q)

        for q in dead:
            self.unsubscribe(q)

    @property
    def active_connections(self) -> int:
        return len(self._queues)


# ── Singleton instance (diimport oleh webhook.py dan main.py) ──
notification_manager = NotificationManager()