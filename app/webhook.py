"""
webhook.py — FastAPI Router untuk WhatsApp Webhook SISKA
=========================================================
Perubahan v1.3 (Bug Fixes):
  FIX-1: Deduplication berdasarkan message.id → cegah spam dari webhook duplikat WA
  FIX-2: Hapus "4" dari AGEN_TRIGGER_MESSAGES → tidak konflik dengan nomor publikasi
  FIX-3: Konsistensi cache publikasi → gunakan satu sumber data yang sama
  FIX-4: payload_id="menu_4" eksplisit di-route ke agen → tidak bergantung intent model
  FIX-5: menu_state="" init + else clause untuk stale session (dari v1.2)
"""

import os
import time
from fastapi import APIRouter, Request, Query
from fastapi.responses import PlainTextResponse
from typing import List, Dict, Any, Optional
import json

from app.middleware.rate_limiter import (
    webhook_limiter,
    PHONE_MAX_REQUESTS, PHONE_WINDOW_SECONDS,
    GLOBAL_MAX_REQUESTS, GLOBAL_WINDOW_SECONDS, GLOBAL_RATE_KEY,
)

from app.services.whatsapp_service import (
    send_whatsapp_message,
    send_whatsapp_list,
    send_whatsapp_buttons,
)
from app.services.intent_detection import IntentDetection
from app.services.database_service import DatabaseService
from app.services.bps_api import BpsApi
from app.services.logger import Logger
from app.services.notification_manager import notification_manager

router = APIRouter()
detector = IntentDetection()
db_service = DatabaseService()
bps_api = BpsApi()
chat_logger = Logger()


# ════════════════════════════════════════════════════════════════
#  FIX-1: DEDUPLICATION — cegah webhook duplikat dari WhatsApp
# ════════════════════════════════════════════════════════════════
# WhatsApp Cloud API kadang mengirim webhook yang sama beberapa kali
# (retry mechanism). Tanpa deduplication, satu pesan diproses 2-3× →
# menyebabkan agen terbuka/tertutup berulang dan respon ganda.

_processed_wamid: Dict[str, float] = {}  # {message_id: timestamp}
_DEDUP_TTL = 60  # detik — cukup untuk absorb semua retry WA


def _is_duplicate_message(wamid: str) -> bool:
    """Return True jika message_id ini sudah diproses dalam 60 detik terakhir."""
    now = time.time()
    # Bersihkan entri expired agar tidak memory leak
    expired = [k for k, v in _processed_wamid.items() if now - v > _DEDUP_TTL]
    for k in expired:
        del _processed_wamid[k]

    if wamid in _processed_wamid:
        return True  # duplikat!

    _processed_wamid[wamid] = now
    return False


# ════════════════════════════════════════════════════════════════
#  VERIFIKASI WEBHOOK WHATSAPP (GET)
# ════════════════════════════════════════════════════════════════

@router.get("/")
async def verify_webhook(
    hub_mode: str          = Query(None, alias="hub.mode"),
    hub_verify_token: str  = Query(None, alias="hub.verify_token"),
    hub_challenge: str     = Query(None, alias="hub.challenge"),
):
    verify_token = os.getenv("WEBHOOK_VERIFY_TOKEN", "")
    print(f"\n🔐 Webhook Verification Request:")
    print(f"   hub.mode         = {hub_mode}")
    print(f"   hub.verify_token = {hub_verify_token}")
    print(f"   hub.challenge    = {hub_challenge}")
    if hub_mode == "subscribe" and hub_verify_token == verify_token:
        print("✅ Webhook verified successfully.")
        return PlainTextResponse(content=hub_challenge, status_code=200)
    print("❌ Webhook verification FAILED. Token tidak cocok.")
    return PlainTextResponse(content="Verification token mismatch", status_code=403)


# ════════════════════════════════════════════════════════════════
#  MESSAGE COLLECTOR (Debug + WhatsApp)
# ════════════════════════════════════════════════════════════════

class MessageCollector:
    def __init__(self, is_postman: bool, sender: str):
        self.is_postman = is_postman
        self.sender = sender
        self.log = []

    def send_text(self, text: str):
        print(f"  📤 TEXT → {self.sender}: {text[:80]}...")
        self.log.append({"type": "TEXT", "content": text})
        if not self.is_postman:
            send_whatsapp_message(self.sender, text)

    def send_list(self, body: str, button: str, header: str, options: List[Dict]):
        print(f"  📋 LIST → {self.sender}: [{header}] {len(options)} opsi")
        self.log.append({"type": "LIST", "header": header, "body": body, "options": options})
        if not self.is_postman:
            send_whatsapp_list(self.sender, body, button, header, options)

    def send_buttons(self, body: str, buttons: List[Dict]):
        print(f"  🔘 BUTTONS → {self.sender}: {[b['title'] for b in buttons]}")
        self.log.append({"type": "BUTTONS", "body": body, "buttons": buttons})
        if not self.is_postman:
            send_whatsapp_buttons(self.sender, body, buttons)

    def get_debug(self) -> Dict:
        return {
            "mode": "POSTMAN" if self.is_postman else "WHATSAPP",
            "sender": self.sender,
            "messages_sent": len(self.log),
            "messages": self.log
        }


# ════════════════════════════════════════════════════════════════
#  HELPERS
# ════════════════════════════════════════════════════════════════

def _get_user_id(phone: str) -> int:
    user = db_service.get_user_by_phone(phone)
    return user["id"] if user else 0

def _send_main_menu(mc: MessageCollector):
    mc.send_text("Halo Kak! 👋 Saya *SISKA*, asisten virtual *BPS Kabupaten Sikka*. 😊")
    menu_options = [
        {"id": "menu_1", "title": "📊 Data Statistik",  "desc": "Inflasi, Penduduk, IPM, dll"},
        {"id": "menu_2", "title": "📚 Unduh Publikasi",  "desc": "Buku & Laporan BPS"},
        {"id": "menu_3", "title": "ℹ️ Info Layanan BPS", "desc": "Alamat, Jam Kerja, dll"},
        {"id": "menu_4", "title": "👤 Hubungi Petugas",  "desc": "Bantuan dari Admin"},
    ]
    mc.send_list(
        "Apa yang bisa saya bantu hari ini, Kak?",
        "Pilih Menu", "Menu Utama SISKA", menu_options
    )

def _build_response(status: str, mc: MessageCollector, is_postman: bool, **extra) -> Dict:
    base = {"status": status}
    if is_postman:
        base["debug"] = mc.get_debug()
    base.update(extra)
    return base


# ════════════════════════════════════════════════════════════════
#  HANDLER: LAYANAN
# ════════════════════════════════════════════════════════════════

def handle_layanan(sender: str, user_text: str, payload_id: Optional[str], mc: MessageCollector):
    if payload_id and payload_id.startswith("btn_"):
        service_id = payload_id.replace("btn_", "")
        row = db_service.fetch_one(
            "SELECT * FROM services WHERE id = %s LIMIT 1",
            (service_id,)
        )
        if row:
            reply = (
                f"*{row['name']}*\n"
                f"{'─' * 28}\n"
                f"{row['content']}\n\n"
                f"Ketik *layanan* untuk info lain atau *menu* untuk kembali."
            )
        else:
            reply = "Maaf Kak, informasi layanan tersebut tidak ditemukan. 🙏\nKetik *layanan* untuk melihat daftar."
        mc.send_text(reply)
        return

    if user_text and len(user_text) > 2:
        row = db_service.fetch_one(
            "SELECT * FROM services WHERE name LIKE %s OR content LIKE %s LIMIT 1",
            (f"%{user_text}%", f"%{user_text}%")
        )
        if row:
            reply = (
                f"ℹ️ *{row['name']}*\n"
                f"{'─' * 28}\n"
                f"{row['content']}\n\n"
                f"Ketik *menu* untuk kembali. 😊"
            )
            mc.send_text(reply)
            return

    services = db_service.get_all_services()
    if services:
        options = [
            {"id": f"btn_{s['id']}", "title": s["name"][:24]}
            for s in services[:10]
        ]
        mc.send_list(
            "Berikut informasi layanan BPS Sikka 📋:\nPilih layanan yang Kakak butuhkan:",
            "Pilih Layanan", "Info Layanan BPS Sikka", options
        )
    else:
        mc.send_text(
            "Maaf Kak, informasi layanan belum tersedia. 🙏\n"
            "Silakan hubungi kami langsung atau ketik *petugas* untuk bantuan."
        )


# ════════════════════════════════════════════════════════════════
#  HANDLER: STATISTIK
# ════════════════════════════════════════════════════════════════

def handle_statistik_start(sender: str, user_id: int, mc: MessageCollector):
    kats = db_service.get_categories()
    if kats:
        options = [
            {"id": f"kat_{k['subjek']}", "title": k["subjek"][:24]}
            for k in kats[:10]
        ]
        mc.send_list(
            "Pilih kategori data statistik yang Kakak inginkan 📊:",
            "Pilih Kategori", "Kategori Data BPS Sikka", options
        )
        db_service.update_session(sender, user_id, "pilih_kategori", "none")
    else:
        mc.send_text(
            "⚠️ Maaf Kak, data statistik belum tersedia saat ini.\n"
            "Data diperbarui setiap tengah malam. Coba lagi nanti ya. 😊"
        )


# ════════════════════════════════════════════════════════════════
#  HANDLER: PUBLIKASI
# ════════════════════════════════════════════════════════════════

def handle_publikasi(sender: str, user_text: str, payload_id: Optional[str],
                     mc: MessageCollector, user_id: int):
    pubs_list = db_service.get_or_refresh_publications(bps_api.fetch_all_publications)
    if not pubs_list:
        mc.send_text("📚 Maaf, data publikasi tidak tersedia saat ini. Silakan coba lagi nanti.")
        return
    _show_publications_page(sender, 1, mc, user_id, pubs_list)


def _show_publications_page(sender: str, page: int, mc: MessageCollector, user_id: int, pubs_list: list):
    limit = 10
    total_pubs = len(pubs_list)
    total_pages = max(1, (total_pubs + limit - 1) // limit)

    offset = (page - 1) * limit
    pubs_page = pubs_list[offset:offset + limit]

    if not pubs_page:
        mc.send_text("📚 Tidak ada publikasi ditemukan.")
        return

    lines = [f"📚 *Daftar Publikasi BPS Sikka* (Hal {page}/{total_pages})\n"]

    for i, p in enumerate(pubs_page, 1):
        title = p.get('title', 'No Title')
        year = p.get('year', '-')
        lines.append(f"{offset + i}. *{title}* ({year})")

    start_num = offset + 1
    end_num = offset + len(pubs_page)
    lines.append(f"\n_Balas angka {start_num}–{end_num} untuk detail publikasi._")

    if total_pages > 1:
        lines.append("_Atau gunakan tombol navigasi di bawah._")

    # Simpan hanya halaman saat ini
    session_data = json.dumps({"page": page})
    db_service.update_session(sender, user_id, "publikasi_list", session_data)

    buttons = []
    if page > 1:
        buttons.append({"id": f"page_prev_{page - 1}", "title": "⬅️ Sebelumnya"})
    if page < total_pages:
        buttons.append({"id": f"page_next_{page + 1}", "title": "Selanjutnya ➡️"})

    msg_text = "\n".join(lines)
    if buttons:
        mc.send_buttons(msg_text, buttons)
    else:
        mc.send_text(msg_text)


def _show_publication_detail(pub: dict, mc: MessageCollector):
    title = pub.get('title', 'Publikasi BPS')
    year = pub.get('year', '-')
    link = pub.get('link') or pub.get('pdf', '-')
    desc = pub.get('desc', '') or pub.get('description', '')

    reply = f"📗 *{title}*\n{'─' * 28}\n"
    if year:
        reply += f"📅 Tahun: *{year}*\n"
    if desc:
        reply += f"📝 {desc[:200]}{'...' if len(desc) > 200 else ''}\n"
    reply += f"\n🔗 *Link Unduhan:*\n{link}\n\n"
    reply += "_Ketik *publikasi* untuk daftar lainnya atau *menu* untuk kembali._"
    mc.send_text(reply)


# ════════════════════════════════════════════════════════════════
#  HANDLER: AGEN (Live Chat Interaktif)
# ════════════════════════════════════════════════════════════════

# FIX-2: Hapus "4" dari trigger set — "4" ambigu karena juga nomor item publikasi.
# Agen hanya dipicu oleh kata kunci teks ATAU payload_id="menu_4" (ditangani di PRIORITAS 3).
AGEN_TRIGGER_MESSAGES = {"petugas", "admin", "agen", "operator", "bantuan manusia", "hubungi petugas"}


def handle_agen_start(sender: str, user_text: str, user_id: int, mc: MessageCollector):
    active_conv = db_service.get_active_conversation(sender)
    is_trigger = user_text.lower().strip() in AGEN_TRIGGER_MESSAGES

    if active_conv and active_conv['status'] != 'closed':
        conv_id = active_conv['conversation_id']
        if not is_trigger:
            db_service.create_agent_request(sender, user_text, conv_id)
        mc.send_text(
            "✅ Pesan Kakak telah diteruskan ke petugas. 🙏\n"
            "Ketik *selesai* untuk mengakhiri percakapan."
        )
    else:
        req_id = db_service.create_agent_request(
            sender,
            "— Memulai sesi bantuan —",
            None
        )
        mc.send_text(
            "✅ Halo Kak! Permintaan bantuan Kakak telah kami terima. 🙏\n\n"
            "Silakan langsung sampaikan pertanyaan Kakak.\n"
            "Ketik *selesai* untuk mengakhiri percakapan."
        )
        print(f"  🎫 Agen baru: req_id={req_id}, phone={sender}")


# ════════════════════════════════════════════════════════════════
#  MAIN WEBHOOK ROUTER (POST)
# ════════════════════════════════════════════════════════════════

@router.post("/")
async def receive_message(request: Request):
    body = await request.json()

    # Global rate limit
    global_ok, _ = webhook_limiter.is_allowed(
        GLOBAL_RATE_KEY, GLOBAL_MAX_REQUESTS, GLOBAL_WINDOW_SECONDS
    )
    if not global_ok:
        print("⚠️ Global webhook rate limit tercapai — request diabaikan.")
        return {"status": "ok"}

    sender = "Postman-Tester"
    user_text = ""
    payload_id = None
    is_postman = False

    try:
        # ── Parse payload ─────────────────────────────────────────
        if "entry" in body:
            try:
                value = body["entry"][0]["changes"][0]["value"]
                if "messages" not in value:
                    return {"status": "ignored", "reason": "no_messages"}
                msg_obj = value["messages"][0]
                sender = msg_obj["from"]

                # ── FIX-1: Cek duplikat berdasarkan message ID ────
                wamid = msg_obj.get("id", "")
                if wamid and _is_duplicate_message(wamid):
                    print(f"  ♻️ Duplikat WA message {wamid} dari {sender} — diabaikan.")
                    return {"status": "ignored", "reason": "duplicate_message"}

                # Rate limit per phone
                phone_ok, retry_after = webhook_limiter.is_allowed(
                    f"phone:{sender}", PHONE_MAX_REQUESTS, PHONE_WINDOW_SECONDS
                )
                if not phone_ok:
                    print(f"⚠️ Rate limit untuk {sender} — coba lagi {retry_after}s.")
                    send_whatsapp_message(
                        sender,
                        f"⏳ Terlalu banyak pesan. Silakan tunggu {retry_after} detik sebelum mengirim lagi. 🙏"
                    )
                    return {"status": "ok"}

                if msg_obj["type"] == "text":
                    user_text = msg_obj["text"]["body"].strip()
                elif msg_obj["type"] == "interactive":
                    itype = msg_obj["interactive"]["type"]
                    user_text = msg_obj["interactive"][itype].get("title", "")
                    payload_id = msg_obj["interactive"][itype].get("id")
                elif msg_obj["type"] in ("image", "audio", "video", "document", "sticker"):
                    send_whatsapp_message(
                        sender,
                        "Maaf Kak, saya hanya menerima pesan teks saat ini. 😊\n"
                        "Silakan ketik *menu* untuk mulai."
                    )
                    return {"status": "ignored", "reason": "media_not_supported"}
                else:
                    return {"status": "ignored", "reason": f"unsupported_type_{msg_obj['type']}"}
            except (KeyError, IndexError) as e:
                print(f"  ⚠️ Parse WA error: {e}")
                return {"status": "ignored", "reason": "parse_error"}

        elif "sender" in body:
            is_postman = True
            sender = str(body.get("sender", "Postman-Tester"))
            user_text = str(body.get("message", "")).strip()
            payload_id = body.get("payload_id")
            phone_ok, retry_after = webhook_limiter.is_allowed(
                f"phone:{sender}", PHONE_MAX_REQUESTS, PHONE_WINDOW_SECONDS
            )
            if not phone_ok:
                return {"status": "rate_limited", "retry_after": retry_after}
            print(f"\n{'=' * 60}")
            print(f"🧪 POSTMAN TEST | sender={sender} | text='{user_text}' | payload={payload_id}")
            print(f"{'=' * 60}")
        else:
            return {"status": "ignored", "reason": "unknown_format"}

        if not user_text and not payload_id:
            return {"status": "ignored", "reason": "empty_input"}

        user_text_lower = user_text.lower().strip()
        mc = MessageCollector(is_postman, sender)
        db_service.update_user_stats(sender)
        user_id = _get_user_id(sender)

        print(f"\n  👤 {sender} | text='{user_text}' | payload='{payload_id}'")

        # ── PRIORITAS 1: Agen aktif ───────────────────────────────
        active_conv = db_service.get_active_conversation(sender)
        if active_conv and active_conv['status'] in ('in_progress', 'responded', 'pending'):
            if user_text_lower in ("selesai", "menu", "batal", "exit", "keluar", "stop"):
                db_service.close_conversation(active_conv['conversation_id'])
                db_service.delete_session(sender)
                db_service.execute_query(
                    """INSERT INTO agent_requests
                    (phone, message, status, conversation_id, is_admin_reply, is_read)
                    VALUES (%s, %s, 'closed', %s, 1, 1)""",
                    (sender, "[Sistem] User mengakhiri percakapan.", active_conv['conversation_id'])
                )
                mc.send_text(
                    "✅ Percakapan dengan petugas telah *diakhiri*.\n"
                    "Terima kasih Kak! 😊\n\n"
                    "Ketik *menu* jika ada yang bisa dibantu lagi."
                )
                chat_logger.log_chat(sender, user_text, "agen_close", "Percakapan diakhiri user.")
                return _build_response("success", mc, is_postman)

            # Teruskan pesan user ke agen
            conv_id = active_conv['conversation_id']
            db_service.create_agent_request(sender, user_text, conv_id)
            db_service.execute_query(
                "UPDATE agent_requests SET status = 'pending' WHERE conversation_id = %s",
                (conv_id,)
            )
            mc.send_text(
                "✅ Pesan Kakak telah diteruskan ke petugas. 🙏\n"
                "Ketik *selesai* untuk mengakhiri percakapan."
            )
            chat_logger.log_chat(sender, user_text, "agen", "Pesan diteruskan ke petugas.")

            await notification_manager.broadcast({
                "type"           : "new_message",
                "phone"          : sender,
                "message"        : user_text[:80],
                "conversation_id": conv_id,
                "is_new_conv"    : False,
            })

            return _build_response("success", mc, is_postman)

        # ── PRIORITAS 2: State machine ────────────────────────────
        current_session = db_service.get_session(sender)
        menu_state = ""  # FIX-5: inisialisasi defensif sebelum blok if

        if user_text_lower in ("menu", "batal", "kembali", "exit", "keluar", "0"):
            db_service.delete_session(sender)
            _send_main_menu(mc)
            return _build_response("success", mc, is_postman)

        if current_session:
            menu_state = current_session.get("current_menu", "")
            print(f"  🗂️ State: {menu_state}")

            # STATE: PILIH KATEGORI
            if menu_state == "pilih_kategori":
                kat_nama = None
                if payload_id and payload_id.startswith("kat_"):
                    kat_nama = payload_id.replace("kat_", "").replace("_", " ")
                elif user_text_lower not in ("menu", "batal", "kembali"):
                    kats = db_service.get_categories()
                    matched = next(
                        (k['subjek'] for k in kats if user_text_lower in k['subjek'].lower()),
                        None
                    )
                    if matched:
                        kat_nama = matched
                        payload_id = f"kat_{matched}"
                    else:
                        options = [{"id": f"kat_{k['subjek']}", "title": k["subjek"][:24]} for k in kats[:10]]
                        mc.send_list(
                            "⚠️ Kategori tidak ditemukan. Silakan *pilih dari daftar* berikut:",
                            "Pilih Kategori", "Kategori Data BPS Sikka", options
                        )
                        return _build_response("success", mc, is_postman)

                if kat_nama:
                    list_var = db_service.get_variables_by_category(kat_nama)
                    if list_var:
                        options = [
                            {"id": f"var_{v['id_var']}", "title": v["nama_var"][:24]}
                            for v in list_var[:10]
                        ]
                        mc.send_list(
                            f"📊 Pilih variabel data *{kat_nama}*:",
                            "Pilih Variabel", f"Variabel {kat_nama}", options
                        )
                        db_service.update_session(sender, user_id, "pilih_variabel", kat_nama)
                    else:
                        mc.send_text(
                            f"⚠️ Tidak ada variabel untuk kategori *{kat_nama}*.\n"
                            "Ketik *statistik* untuk mencoba kategori lain."
                        )
                        db_service.delete_session(sender)
                return _build_response("success", mc, is_postman)

            # STATE: PILIH VARIABEL
            elif menu_state == "pilih_variabel":
                if payload_id and payload_id.startswith("var_"):
                    var_id = payload_id.replace("var_", "")
                    var_info = db_service.get_variable_info(var_id)

                    def fetch_yearly_data(var_id):
                        data, _ = bps_api.get_aggregated_yearly_data(var_id)
                        return data

                    cached_data = db_service.get_or_refresh_variable(var_id, fetch_yearly_data)

                    if not cached_data:
                        mc.send_text(
                            "⚠️ Maaf Kak, data variabel ini tidak dapat diambil saat ini. Coba lagi nanti."
                        )
                        db_service.delete_session(sender)
                        return _build_response("success", mc, is_postman)

                    available_years = sorted(set(str(d.get('tahun', '')) for d in cached_data if d.get('tahun')))
                    nama_var = var_info['nama_var'] if var_info else "Variabel"

                    if not available_years:
                        mc.send_text(
                            "⚠️ Data tahun tidak tersedia untuk variabel ini. 🙏\n"
                            "Ketik *statistik* untuk mencoba variabel lain."
                        )
                        db_service.delete_session(sender)
                        return _build_response("success", mc, is_postman)

                    db_service.update_session(sender, user_id, "tanya_tahun", json.dumps({
                        "var_id": var_id,
                        "nama_var": nama_var,
                        "cached_data": cached_data
                    }))

                    if len(available_years) <= 6:
                        buttons = [{"id": f"tahun_{y}", "title": str(y)} for y in available_years[:3]]
                        tahun_list_str = ", ".join(available_years)
                        mc.send_text(
                            f"📊 *{nama_var}*\n\n"
                            f"📅 Tahun tersedia: *{tahun_list_str}*\n\n"
                            f"Ketik tahun yang Kakak inginkan\n"
                            f"atau ketik *semua* untuk semua tahun:"
                        )
                        if buttons:
                            mc.send_buttons(f"Pilih tahun data *{nama_var}*:", buttons)
                    else:
                        tahun_list_str = ", ".join(available_years)
                        mc.send_text(
                            f"📊 *{nama_var}*\n\n"
                            f"📅 Tahun tersedia:\n*{tahun_list_str}*\n\n"
                            f"Ketik tahun yang Kakak inginkan (contoh: *2023*)\n"
                            f"atau ketik *semua* untuk semua tahun:"
                        )
                else:
                    mc.send_text("⚠️ Silakan pilih variabel dari daftar yang tersedia.")
                return _build_response("success", mc, is_postman)

            # STATE: TANYA TAHUN
            elif menu_state == "tanya_tahun":
                session_data_raw = current_session.get("last_intent", "{}")
                try:
                    session_data = json.loads(session_data_raw)
                    var_id = session_data.get("var_id", "")
                    nama_var = session_data.get("nama_var", "Data Statistik")
                    cached_data = session_data.get("cached_data", [])
                except Exception:
                    var_id = current_session.get("last_intent", "")
                    if not var_id:
                        mc.send_text("⚠️ Sesi tidak valid, silakan mulai dari *statistik*.")
                        db_service.delete_session(sender)
                        return _build_response("success", mc, is_postman)

                    def fetch(var_id):
                        data, _ = bps_api.get_aggregated_yearly_data(var_id)
                        return data

                    cached_data = db_service.get_or_refresh_variable(var_id, fetch)
                    var_info = db_service.get_variable_info(var_id)
                    nama_var = var_info['nama_var'] if var_info else "Data Statistik"

                tahun_diminta = (
                    payload_id.replace("tahun_", "")
                    if payload_id and payload_id.startswith("tahun_")
                    else user_text.strip()
                )

                is_semua = tahun_diminta.lower() in ("semua", "all", "semua tahun")
                is_valid_year = tahun_diminta.isdigit() and 1900 <= int(tahun_diminta) <= 2100

                if not is_semua and not is_valid_year:
                    available_years = sorted(set(str(d.get('tahun', '')) for d in cached_data if d.get('tahun')))
                    mc.send_text(f"⚠️ Tahun *{tahun_diminta}* tidak valid.\n📅 Tersedia: *{', '.join(available_years)}*")
                    return _build_response("ignored", mc, is_postman)

                unit = cached_data[0].get('unit', '') if cached_data else ''
                db_service.delete_session(sender)

                if cached_data:
                    filtered = (
                        cached_data if is_semua
                        else [d for d in cached_data if str(d.get('tahun', '')) == tahun_diminta]
                    )
                    if filtered:
                        lines = [
                            f"📊 *{nama_var}*",
                            f"📏 Satuan: {unit}" if unit else "",
                            f"📅 Periode: *{'Semua Tahun' if is_semua else tahun_diminta}*\n{'─' * 28}",
                        ]
                        for item in filtered:
                            nilai = item.get('nilai', 0)
                            try:
                                valor = float(nilai)
                                nilai_str = (
                                    f"{int(valor):,}".replace(",", ".")
                                    if valor == int(valor)
                                    else f"{valor:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
                                )
                            except Exception:
                                nilai_str = str(nilai)
                            lines.append(f"• {item.get('tahun', '-')}: *{nilai_str} {unit}*")
                        lines.append(f"\n_Sumber: BPS Kabupaten Sikka 🏛️_\nKetik *statistik* atau *menu*.")
                        mc.send_text("\n".join(lines))
                    else:
                        mc.send_text(f"⚠️ Data tahun *{tahun_diminta}* tidak ditemukan.")
                return _build_response("success", mc, is_postman)

            # STATE: PUBLIKASI LIST
            elif menu_state == "publikasi_list":
                # Izinkan keluar kapan saja
                if user_text_lower in ("menu", "batal", "kembali", "exit", "keluar", "0"):
                    db_service.delete_session(sender)
                    _send_main_menu(mc)
                    return _build_response("success", mc, is_postman)

                # Ambil halaman saat ini dari session
                session_data_raw = current_session.get("last_intent", "{}")
                try:
                    session_data = json.loads(session_data_raw)
                    current_page = session_data.get("page", 1)
                except Exception:
                    current_page = 1

                # FIX-3: Selalu gunakan get_or_refresh_publications agar data konsisten
                # dengan yang ditampilkan saat daftar pertama kali muncul.
                # Hindari get_publications_cache() yang bisa mengembalikan urutan berbeda.
                pubs_list = db_service.get_or_refresh_publications(bps_api.fetch_all_publications)
                if not pubs_list:
                    mc.send_text("📚 Maaf, data publikasi tidak tersedia saat ini. Silakan coba lagi nanti.")
                    db_service.delete_session(sender)
                    return _build_response("success", mc, is_postman)

                # Navigasi halaman dengan tombol
                if payload_id:
                    if payload_id.startswith("page_next_"):
                        new_page = int(payload_id.replace("page_next_", ""))
                        _show_publications_page(sender, new_page, mc, user_id, pubs_list)
                        return _build_response("success", mc, is_postman)
                    elif payload_id.startswith("page_prev_"):
                        new_page = int(payload_id.replace("page_prev_", ""))
                        _show_publications_page(sender, new_page, mc, user_id, pubs_list)
                        return _build_response("success", mc, is_postman)

                # User mengetik angka (nomor global sesuai tampilan)
                if user_text.strip().isdigit():
                    global_idx = int(user_text.strip()) - 1
                    if 0 <= global_idx < len(pubs_list):
                        _show_publication_detail(pubs_list[global_idx], mc)
                    else:
                        mc.send_text(f"⚠️ Pilihan tidak valid. Masukkan angka 1–{len(pubs_list)}.")
                    db_service.delete_session(sender)
                    return _build_response("success", mc, is_postman)

                # Teks terlalu panjang / tidak wajar
                if len(user_text.strip()) > 80:
                    mc.send_text(
                        "⚠️ Maaf, saya hanya menerima angka atau tombol navigasi.\n"
                        "Ketik *menu* untuk kembali ke menu utama."
                    )
                    return _build_response("success", mc, is_postman)

                # Input tidak dikenali
                mc.send_text("⚠️ Silakan pilih angka atau gunakan tombol navigasi.")
                return _build_response("success", mc, is_postman)

            else:
                # FIX-5: State tidak dikenal / stale session → bersihkan, lanjut ke intent detection
                print(f"  ⚠️ Stale/unknown session state='{menu_state}' — session dihapus, lanjut ke intent.")
                db_service.delete_session(sender)
                # (tidak return — fall-through ke PRIORITAS 3 secara eksplisit & aman)

        # ── PRIORITAS 3: Intent detection ─────────────────────────
        intent = detector.get_intent(user_text, payload_id)

        # FIX-4: payload_id="menu_4" selalu route ke agen, tidak bergantung pada
        # intent model. Ini memastikan tombol "Hubungi Petugas" selalu berfungsi
        # bahkan jika model tidak mendeteksi intent "agen" dari payload tersebut.
        if payload_id == "menu_4":
            intent = "agen"

        if intent == "greeting" or user_text_lower in ("menu", "halo", "mulai", "start"):
            _send_main_menu(mc)
        elif intent == "statistik":
            handle_statistik_start(sender, user_id, mc)
        elif intent == "publikasi":
            handle_publikasi(sender, user_text, payload_id, mc, user_id)
        elif intent == "layanan":
            handle_layanan(sender, user_text, payload_id, mc)
        elif intent == "agen":
            handle_agen_start(sender, user_text, user_id, mc)

            new_conv = db_service.get_active_conversation(sender)
            if new_conv:
                await notification_manager.broadcast({
                    "type"           : "new_message",
                    "phone"          : sender,
                    "message"        : user_text[:80],
                    "conversation_id": new_conv.get("conversation_id", 0),
                    "is_new_conv"    : True,
                })
        else:
            mc.send_text(
                "Maaf Kak, saya belum memahami pesan Kakak. 🤔\n"
                "Ketik *menu* untuk melihat bantuan."
            )

        chat_logger.log_chat(sender, user_text, intent if 'intent' in locals() else "unknown", "")
        return _build_response("success", mc, is_postman)

    except Exception as e:
        print(f"❌ WEBHOOK ERROR: {e}")
        chat_logger.log_error(str(e))
        return {"status": "error", "message": str(e)}