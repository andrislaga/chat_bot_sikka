"""
main.py — Entry Point FastAPI SISKA Chatbot BPS Sikka
======================================================
Perubahan v1.2 (Notifikasi Real-Time):
  - Tambah endpoint SSE GET /admin/stream
  - Import asyncio dan StreamingResponse
  - Import notification_manager
  - Semua endpoint lama TIDAK berubah
"""

import asyncio
import os
from datetime import datetime
import pytz
from fastapi import Cookie, FastAPI, Form, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.services.auth import AuthHandler
from app.services.database_service import DatabaseService
from app.services.logger import Logger
from app.services.whatsapp_service import send_whatsapp_message
from app.webhook import router as webhook_router
from app.services.scheduler import start_scheduler

from app.services.logging_config import setup_logging, get_logger
setup_logging()
log = get_logger(__name__)
from app.middleware.security_headers import SecurityHeadersMiddleware

# ── BARU: SSE notification manager ──────────────────────────────
from app.services.notification_manager import notification_manager


app = FastAPI(title="SISKA Admin - BPS Kabupaten Sikka")
app.add_middleware(SecurityHeadersMiddleware)
db_service = DatabaseService()
chat_logger = Logger()
auth = AuthHandler()

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TEMPLATE_DIR = os.path.join(BASE_DIR, "..", "templates")
STATIC_DIR = os.path.join(BASE_DIR, "..", "static")

if os.path.isdir(STATIC_DIR):
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

templates = Jinja2Templates(directory=TEMPLATE_DIR)

def format_wita(value):
    if not value:
        return "-"
    wita_zone = pytz.timezone("Asia/Makassar")
    if isinstance(value, str):
        try:
            value = datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            return value
    if value.tzinfo is None:
        value = pytz.utc.localize(value)
    return value.astimezone(wita_zone).strftime("%d/%m/%Y %H:%M WITA")

templates.env.filters["wita"] = format_wita

def require_login(request: Request):
    if not auth.check_login(request):
        return RedirectResponse(url="/login", status_code=303)
    return None

app.include_router(webhook_router, prefix="/webhook")

@app.on_event("startup")
async def startup_event():
    log.info("SISKA startup — menginisialisasi scheduler...")
    scheduler = start_scheduler()
    if scheduler:
        log.info("Scheduler aktif di proses ini.")
    else:
        log.info("Scheduler berjalan di worker lain — proses ini skip.")


# ════════════════════════════════════════════════════════════════
#  ROOT & AUTH
# ════════════════════════════════════════════════════════════════

@app.get("/")
async def root():
    return RedirectResponse(url="/login")

@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    error = request.query_params.get("error", "")
    return templates.TemplateResponse("login.html", {"request": request, "error": error})

@app.post("/login")
async def do_login(response: Response, username: str = Form(...), password: str = Form(...)):
    admin = db_service.get_admin_by_username(username)
    if admin and auth.verify_password(password, admin["password_hash"]):
        res = RedirectResponse(url="/admin", status_code=303)
        res.set_cookie(key="admin_session", value=username, httponly=True, samesite="lax")
        return res
    return RedirectResponse(url="/login?error=1", status_code=303)

@app.get("/logout")
async def logout():
    res = RedirectResponse(url="/login")
    res.delete_cookie("admin_session")
    return res


# ════════════════════════════════════════════════════════════════
#  SSE ENDPOINT — Real-Time Notifikasi Admin
# ════════════════════════════════════════════════════════════════

@app.get("/admin/stream")
async def admin_stream(request: Request):
    """
    Server-Sent Events endpoint untuk notifikasi real-time admin.
    Browser admin terhubung ke sini saat membuka dashboard.
    Ketika ada pesan agen masuk, webhook.py memanggil
    notification_manager.broadcast() yang akan mengirim event ke sini.
    """
    guard = require_login(request)
    if guard:
        return guard

    q = notification_manager.subscribe()
    log.info(f"SSE admin terhubung. Active: {notification_manager.active_connections}")

    async def event_generator():
        try:
            # Kirim event koneksi berhasil
            yield 'data: {"type":"connected"}\n\n'

            while True:
                # Cek disconnect setiap iterasi
                if await request.is_disconnected():
                    log.debug("SSE client disconnect (request).")
                    break

                try:
                    # Tunggu event dari queue, timeout 20 detik untuk keepalive
                    msg = await asyncio.wait_for(q.get(), timeout=20.0)
                    yield f"data: {msg}\n\n"

                except asyncio.TimeoutError:
                    # Kirim keepalive agar koneksi tidak mati
                    yield ": keepalive\n\n"

        except asyncio.CancelledError:
            log.debug("SSE generator cancelled.")
        except Exception as e:
            log.error(f"SSE generator error: {e}")
        finally:
            notification_manager.unsubscribe(q)
            log.info(f"SSE client selesai. Active: {notification_manager.active_connections}")

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control"   : "no-cache",
            "Connection"      : "keep-alive",
            "X-Accel-Buffering": "no",   # penting untuk Nginx proxy
        },
    )


# ════════════════════════════════════════════════════════════════
#  DASHBOARD UTAMA
# ════════════════════════════════════════════════════════════════

@app.get("/admin", response_class=HTMLResponse)
async def admin_dashboard(request: Request):
    guard = require_login(request)
    if guard:
        return guard
    try:
        conversations = db_service.get_conversations_for_dashboard()
        stats = db_service.get_dashboard_stats()
        return templates.TemplateResponse("dashboard.html", {
            "request": request,
            "conversations": conversations,
            "stats": stats,
        })
    except Exception as e:
        return HTMLResponse(content=f"<pre>{e}</pre>", status_code=500)


@app.get("/admin/conversation/{conversation_id}", response_class=HTMLResponse)
async def view_conversation(conversation_id: int, request: Request):
    guard = require_login(request)
    if guard:
        return guard
    db_service.mark_conversation_read(conversation_id)
    thread = db_service.get_conversation_thread(conversation_id)
    return templates.TemplateResponse("conversation_detail.html", {
        "request": request,
        "thread": thread,
        "conversation_id": conversation_id
    })

@app.post("/admin/assign/{conversation_id}")
async def assign_conversation(conversation_id: int, request: Request):
    guard = require_login(request)
    if guard:
        return guard
    admin_user = request.cookies.get("admin_session")
    db_service.assign_conversation(conversation_id, admin_user)
    return RedirectResponse(url="/admin", status_code=303)

@app.post("/admin/close/{conversation_id}")
async def close_conversation(conversation_id: int, request: Request):
    guard = require_login(request)
    if guard:
        return guard

    conv = db_service.fetch_one(
        "SELECT DISTINCT phone FROM agent_requests WHERE conversation_id = %s LIMIT 1",
        (conversation_id,)
    )
    db_service.close_conversation(conversation_id)

    if conv:
        send_whatsapp_message(
            conv['phone'],
            "✅ *Sesi bantuan telah ditutup oleh petugas BPS Sikka.*\n\n"
            "Terima kasih telah menggunakan layanan kami. 🙏\n"
            "Ketik *menu* jika ada yang bisa kami bantu lagi. 😊"
        )
    return RedirectResponse(url="/admin", status_code=303)

@app.post("/admin/reply")
async def reply_agent_request(
    request: Request,
    conversation_id: int = Form(...),
    reply_message: str = Form(...),
):
    guard = require_login(request)
    if guard:
        return guard

    admin_user = request.cookies.get("admin_session", "admin")

    conv = db_service.fetch_one(
        "SELECT DISTINCT phone FROM agent_requests WHERE conversation_id = %s LIMIT 1",
        (conversation_id,)
    )
    if not conv:
        return HTMLResponse(content="<h3>Conversation tidak ditemukan.</h3>", status_code=404)

    new_id = db_service.add_agent_reply(conversation_id, admin_user, reply_message)
    if new_id:
        send_whatsapp_message(
            conv['phone'],
            f"*[Petugas BPS Sikka - {admin_user}]*\n\n{reply_message}"
        )
        chat_logger.log_chat(conv['phone'], "[Balasan Admin]", "agen_reply", reply_message)

    return RedirectResponse(
        url=f"/admin/conversation/{conversation_id}", status_code=303
    )


# ════════════════════════════════════════════════════════════════
#  KELOLA LAYANAN
# ════════════════════════════════════════════════════════════════

@app.get("/admin/services", response_class=HTMLResponse)
async def manage_services(request: Request):
    guard = require_login(request)
    if guard:
        return guard
    services = db_service.get_all_services()
    return templates.TemplateResponse("services.html", {
        "request": request,
        "services": services,
    })

@app.post("/admin/services/update")
async def do_update_service(
    request: Request,
    service_id: int = Form(...),
    name: str = Form(...),
    content: str = Form(...),
):
    guard = require_login(request)
    if guard:
        return guard
    db_service.update_service(service_id, name, content)
    return RedirectResponse(url="/admin/services?updated=1", status_code=303)

@app.post("/admin/services/add")
async def do_add_service(
    request: Request,
    name: str = Form(...),
    content: str = Form(...),
    category: str = Form("Umum"),
):
    guard = require_login(request)
    if guard:
        return guard
    db_service.add_service(name, content, category)
    return RedirectResponse(url="/admin/services?added=1", status_code=303)


# ════════════════════════════════════════════════════════════════
#  KELOLA PUBLIKASI (Read-only dari cache)
# ════════════════════════════════════════════════════════════════

@app.get("/admin/publications", response_class=HTMLResponse)
async def manage_publications(request: Request):
    guard = require_login(request)
    if guard:
        return guard
    pubs = db_service.get_publications_paginated(limit=100)
    return templates.TemplateResponse("publications.html", {
        "request": request,
        "publications": pubs,
    })


# ════════════════════════════════════════════════════════════════
#  LOG CHAT
# ════════════════════════════════════════════════════════════════

@app.get("/admin/logs", response_class=HTMLResponse)
async def view_chat_logs(
    request: Request,
    search: str = None,
    intent: str = None,
):
    guard = require_login(request)
    if guard:
        return guard

    summaries = db_service.get_agent_conversation_summaries(limit=200)

    if search:
        summaries = [s for s in summaries if search in s['phone']]

    return templates.TemplateResponse("chat_logs.html", {
        "request": request,
        "summaries": summaries,
        "search_val": search or "",
    })

@app.get("/admin/services/delete/{service_id}")
async def delete_service(service_id: int, request: Request):
    guard = require_login(request)
    if guard:
        return guard
    db_service.execute_query("DELETE FROM services WHERE id = %s", (service_id,))
    return RedirectResponse(url="/admin/services?deleted=1", status_code=303)