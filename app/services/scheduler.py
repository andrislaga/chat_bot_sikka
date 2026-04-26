"""
scheduler.py - SISKA Bot Scheduler
===================================
Windows (dev):  Simple PID lock
Linux (prod):   fcntl.flock (uncomment saat deploy)

Perubahan:
  - close_idle_agent_conversations: timeout 15 menit → 3 menit, pesan lebih rapi
  - [BARU] close_idle_bot_sessions: tutup sesi bot idle > 3 menit + kirim notifikasi WA
"""
import os
import time
from datetime import datetime, timedelta

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from app.services.bps_api import BpsApi
from app.services.database_service import DatabaseService
from app.services.whatsapp_service import send_whatsapp_message
from app.services.logging_config import get_logger
from app.middleware.rate_limiter import webhook_limiter
import fcntl

log = get_logger(__name__)

_LOCK_FILE_PATH = "/tmp/siska_scheduler.lock"  # Linux ✅

_lock_file_handle = None

# ── Konstanta timeout ──────────────────────────────────────────────────────────
BOT_SESSION_TIMEOUT_MINUTES   = int(os.getenv("BOT_SESSION_TIMEOUT_MINUTES",   "3"))
AGENT_SESSION_TIMEOUT_MINUTES = int(os.getenv("AGENT_SESSION_TIMEOUT_MINUTES", "3"))


def _try_acquire_scheduler_lock() -> bool:
    """
    Dapatkan lock agar hanya 1 worker yang jalankan scheduler.
    
    Windows: PID check (development sekarang)
    Linux:   fcntl.flock (uncomment saat deploy)
    """
    global _lock_file_handle
    
    # ============================================================
    # DEVELOPMENT MODE (Windows) - PAKAI SEKARANG
    # ============================================================
    # try:
    #     if os.path.exists(_LOCK_FILE_PATH):
    #         with open(_LOCK_FILE_PATH, 'r') as f:
    #             old_pid = f.read().strip()
    #         try:
    #             os.kill(int(old_pid), 0)
    #             log.info(f"⏭️ PID {os.getpid()} skip - scheduler di PID {old_pid}")
    #             return False
    #         except (OSError, ValueError):
    #             log.info(f"🧹 Membersihkan lock lama (PID {old_pid} mati)")
    #             os.remove(_LOCK_FILE_PATH)
        
    #     _lock_file_handle = open(_LOCK_FILE_PATH, 'w')
    #     _lock_file_handle.write(str(os.getpid()))
    #     _lock_file_handle.flush()
    #     log.info(f"✅ Lock diperoleh PID {os.getpid()}")
    #     return True
        
    # except Exception as e:
    #     log.error(f"❌ Lock error: {e}")
    #     return False
    
    # ============================================================
    # PRODUCTION MODE (Linux) - UNCOMMENT SAAT DEPLOY:
    # ============================================================
    try:
        _lock_file_handle = open(_LOCK_FILE_PATH, 'w')
        fcntl.flock(_lock_file_handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        _lock_file_handle.write(str(os.getpid()))
        _lock_file_handle.flush()
        log.info(f"✅ Lock diperoleh PID {os.getpid()}")
        return True
    except (IOError, OSError):
        log.info(f"⏭️ PID {os.getpid()} skip - lock dipegang worker lain")
        return False


# ════════════════════════════════════════════════════════════════
#  JOB: Sinkronisasi Publikasi (00:00 WITA)
# ════════════════════════════════════════════════════════════════

def sync_publications():
    """Sinkronisasi publikasi BPS (00:00 WITA)."""
    log.info("[Scheduler] 📚 Sinkronisasi publikasi...")
    api, db = BpsApi(), DatabaseService()
    try:
        pubs = api.fetch_all_publications()
        if pubs:
            db.set_publications_cache(pubs)
            db.replace_all_publications(pubs)
            log.info(f"[Scheduler] ✅ {len(pubs)} publikasi tersimpan")
        else:
            log.warning("[Scheduler] ⚠️ API publikasi kosong")
    except Exception as e:
        log.error(f"[Scheduler] ❌ Error publikasi: {e}", exc_info=True)


# ════════════════════════════════════════════════════════════════
#  JOB: Sinkronisasi Variabel (01:00 WITA)
# ════════════════════════════════════════════════════════════════

def sync_all_variables_data():
    """Sinkronisasi data variabel statistik (01:00 WITA)."""
    log.info("[Scheduler] 📊 Sinkronisasi data variabel...")
    api, db = BpsApi(), DatabaseService()
    
    vars_list = db.fetch_all("SELECT id_var, nama_var FROM variabel")
    if not vars_list:
        log.warning("[Scheduler] ⚠️ Tabel variabel kosong")
        return

    cached = skipped = errors = 0
    for var in vars_list:
        var_id = str(var["id_var"])
        try:
            agg_data, _ = api.get_aggregated_yearly_data(var_id)
            if agg_data:
                db.save_variable_cache(var_id, agg_data)
                cached += 1
            else:
                skipped += 1
            time.sleep(0.5)
        except Exception as e:
            errors += 1
            log.error(f"[Scheduler] Error var_{var_id}: {e}")
    
    log.info(f"[Scheduler] ✅ Variabel: cached={cached} skip={skipped} error={errors}")


# ════════════════════════════════════════════════════════════════
#  JOB: Auto-Tutup Sesi Bot Idle (setiap 1 menit)
# ════════════════════════════════════════════════════════════════

def close_idle_bot_sessions():
    """
    Tutup sesi percakapan bot (bukan agen) yang idle > BOT_SESSION_TIMEOUT_MINUTES.
    Kirim pesan notifikasi ke user sebelum sesi dihapus.

    PRASYARAT — kolom updated_at harus ada di chatbot_sessions (jalankan sekali):
        ALTER TABLE chatbot_sessions
            ADD COLUMN updated_at TIMESTAMP
                DEFAULT CURRENT_TIMESTAMP
                ON UPDATE CURRENT_TIMESTAMP;
    """
    db     = DatabaseService()
    cutoff = datetime.utcnow() - timedelta(minutes=BOT_SESSION_TIMEOUT_MINUTES)

    expired = db.fetch_all(
        """SELECT phone, current_menu
           FROM chatbot_sessions
           WHERE updated_at < %s""",
        (cutoff,)
    )

    if not expired:
        return

    log.info(f"[Scheduler] ⏱️ Menutup {len(expired)} sesi bot idle...")

    for sess in expired:
        phone      = sess["phone"]
        menu_state = sess.get("current_menu", "")

        # Lewati sesi kosong / tidak relevan
        if not menu_state:
            db.delete_session(phone)
            continue

        log.info(f"  🔒 Bot timeout: {phone} | state={menu_state}")

        try:
            send_whatsapp_message(
                phone,
                f"⏱️ *Percakapan diakhiri karena tidak ada balasan.*\n\n"
                f"Ketik *menu* untuk memulai percakapan baru. 😊"
            )
        except Exception as e:
            log.warning(f"  ⚠️ Gagal kirim pesan timeout ke {phone}: {e}")

        db.delete_session(phone)


# ════════════════════════════════════════════════════════════════
#  JOB: Auto-Tutup Percakapan Agen Idle (setiap 1 menit)
# ════════════════════════════════════════════════════════════════

def close_idle_agent_conversations():
    """
    Tutup percakapan agen yang idle > AGENT_SESSION_TIMEOUT_MINUTES.
    Kirim pesan notifikasi ke user sebelum ditutup.
    """
    db     = DatabaseService()
    cutoff = datetime.utcnow() - timedelta(minutes=AGENT_SESSION_TIMEOUT_MINUTES)

    idle_convs = db.fetch_all(
        """SELECT conversation_id, phone 
           FROM agent_requests
           WHERE status != 'closed'
           GROUP BY conversation_id, phone 
           HAVING MAX(created_at) < %s""",
        (cutoff,)
    )

    if not idle_convs:
        return

    log.info(f"[Scheduler] ⏱️ Menutup {len(idle_convs)} percakapan agen idle...")

    for conv in idle_convs:
        conv_id = conv["conversation_id"]
        phone   = conv["phone"]

        log.info(f"  🔒 Agent timeout: conv_id={conv_id} | phone={phone}")

        try:
            send_whatsapp_message(
                phone,
                f"⏱️ *Sesi bantuan petugas diakhiri karena tidak ada balasan.*\n\n"
                f"Terima kasih Kak! 🙏 Ketik *menu* jika ada yang bisa kami bantu lagi. 😊"
            )
        except Exception as e:
            log.warning(f"  ⚠️ Gagal kirim pesan timeout agen ke {phone}: {e}")

        db.close_conversation(conv_id)

        # Catat sebagai pesan sistem di thread percakapan
        try:
            db.execute_query(
                """INSERT INTO agent_requests
                   (phone, message, status, conversation_id, is_admin_reply, is_read)
                   VALUES (%s, %s, 'closed', %s, 1, 1)""",
                (phone, "[Sistem] Sesi ditutup otomatis karena tidak ada balasan.", conv_id)
            )
        except Exception as e:
            log.warning(f"  ⚠️ Gagal insert pesan sistem: {e}")


# ════════════════════════════════════════════════════════════════
#  JOB: Bersihkan Rate Limiter (setiap 5 menit)
# ════════════════════════════════════════════════════════════════

def cleanup_rate_limiter():
    """Bersihkan rate limiter setiap 5 menit."""
    removed = webhook_limiter.cleanup(max_age_seconds=300)
    if removed:
        log.debug(f"[Scheduler] Rate limiter cleanup: {removed} key")


# ════════════════════════════════════════════════════════════════
#  ENTRY POINT
# ════════════════════════════════════════════════════════════════

def start_scheduler():
    """
    Jalankan scheduler dengan lock protection.
    Return scheduler object atau None jika lock gagal.
    """
    if not _try_acquire_scheduler_lock():
        return None

    sched = BackgroundScheduler(timezone="Asia/Makassar")

    # Sinkronisasi harian
    sched.add_job(sync_publications,       CronTrigger(hour=0, minute=0),  id="sync_pub")
    sched.add_job(sync_all_variables_data, CronTrigger(hour=1, minute=0),  id="sync_var")

    # Timeout sesi — berjalan setiap 1 menit
    sched.add_job(close_idle_bot_sessions,        IntervalTrigger(minutes=1), id="bot_session_timeout")
    sched.add_job(close_idle_agent_conversations,  IntervalTrigger(minutes=1), id="agent_timeout")

    # Maintenance
    sched.add_job(cleanup_rate_limiter, IntervalTrigger(minutes=5), id="cleanup_rl")

    sched.start()
    log.info(
        f"✅ Scheduler aktif | {len(sched.get_jobs())} jobs | PID {os.getpid()} | "
        f"bot_timeout={BOT_SESSION_TIMEOUT_MINUTES}m | "
        f"agent_timeout={AGENT_SESSION_TIMEOUT_MINUTES}m"
    )
    return sched