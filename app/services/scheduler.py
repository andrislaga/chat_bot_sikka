"""
scheduler.py - SISKA Bot Scheduler
===================================
Windows (dev):  Simple PID lock
Linux (prod):   fcntl.flock (uncomment saat deploy)
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


def close_idle_agent_conversations():
    """Tutup percakapan agen idle > 15 menit."""
    db = DatabaseService()
    idle_minutes = 15
    cutoff = datetime.utcnow() - timedelta(minutes=idle_minutes)

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

    log.info(f"[Scheduler] Menutup {len(idle_convs)} percakapan idle...")
    for conv in idle_convs:
        try:
            db.close_conversation(conv["conversation_id"])
            send_whatsapp_message(
                conv["phone"],
                f"⏰ *Sesi bantuan berakhir* (tidak aktif {idle_minutes} menit).\n"
                "Ketik *petugas* jika masih butuh bantuan. 😊"
            )
        except Exception as e:
            log.error(f"[Scheduler] Error tutup conv {conv['conversation_id']}: {e}")


def cleanup_rate_limiter():
    """Bersihkan rate limiter setiap 5 menit."""
    removed = webhook_limiter.cleanup(max_age_seconds=300)
    if removed:
        log.debug(f"[Scheduler] Rate limiter cleanup: {removed} key")


def start_scheduler():
    """
    Jalankan scheduler dengan lock protection.
    Return scheduler object atau None jika lock gagal.
    """
    if not _try_acquire_scheduler_lock():
        return None

    sched = BackgroundScheduler(timezone="Asia/Makassar")
    
    sched.add_job(sync_publications, CronTrigger(hour=0, minute=0), id="sync_pub")
    sched.add_job(sync_all_variables_data, CronTrigger(hour=1, minute=0), id="sync_var")
    sched.add_job(close_idle_agent_conversations, IntervalTrigger(minutes=5), id="close_idle")
    sched.add_job(cleanup_rate_limiter, IntervalTrigger(minutes=5), id="cleanup_rl")
    
    sched.start()
    log.info(f"✅ Scheduler aktif | {len(sched.get_jobs())} jobs | PID {os.getpid()}")
    return sched