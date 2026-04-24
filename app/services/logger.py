"""
logger.py — Logger Chatbot SISKA BPS Sikka
==========================================
Perubahan:
  - Tambah status parameter default ke "success"
  - Perbaikan error handling
  - Gunakan timezone UTC untuk konsistensi
"""

import logging
from datetime import datetime, timezone
from app.services.database_service import DatabaseService

logging.basicConfig(
    filename="siska_error.log",
    level=logging.ERROR,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

class Logger:
    def __init__(self):
        self.db = DatabaseService()

    def log_chat(
        self,
        sender: str,
        message: str,
        intent: str,
        reply: str,
        status: str = "success",
    ):
        query = """
            INSERT INTO chat_logs (sender, message, intent, reply, status, created_at)
            VALUES (%s, %s, %s, %s, %s, %s)
        """
        reply_trimmed = (reply or "")[:2000]
        now_utc = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')

        try:
            self.db.execute(query, (sender, message, intent, reply_trimmed, status, now_utc))
            print(f"📝 Log OK: {sender} → [{intent}]")
        except Exception as e:
            print(f"❌ Gagal simpan log ke DB: {e}")
            logging.error(f"DB Log Error (sender={sender}, intent={intent}): {e}")

    def log_error(self, error_msg: str):
        print(f"🚨 System Error: {error_msg}")
        logging.error(error_msg)

        try:
            self.db.execute(
                """
                INSERT INTO chat_logs (sender, message, intent, reply, status, created_at)
                VALUES (%s, %s, %s, %s, %s, %s)
                """,
                ("SYSTEM", error_msg[:500], "system_error", "-", "error",
                 datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')),
            )
        except Exception:
            pass