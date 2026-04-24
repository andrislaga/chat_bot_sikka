"""
whatsapp_service.py
Perbaikan v1.1:
  - Semua fungsi send dibungkus try/except — tidak ada exception yang bocor ke webhook
  - Semua fungsi mengembalikan bool (True=sukses, False=gagal)
  - Helper _post() memusatkan logika error
  - Timeout 10 detik di semua request
  - Logging terstruktur menggantikan print()
"""
import os
import requests
from dotenv import load_dotenv
from app.services.logging_config import get_logger

load_dotenv()
log = get_logger(__name__)

TOKEN           = os.getenv("WHATSAPP_TOKEN")
PHONE_NUMBER_ID = os.getenv("PHONE_NUMBER_ID")

_BASE_URL = f"https://graph.facebook.com/v19.0/{PHONE_NUMBER_ID}/messages"
_HEADERS  = {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"}
_TIMEOUT  = 10


def _post(payload: dict, label: str = "send") -> bool:
    """
    POST ke WhatsApp API. Mengembalikan True/False.
    TIDAK PERNAH melempar exception ke pemanggil.
    """
    try:
        resp = requests.post(_BASE_URL, headers=_HEADERS, json=payload, timeout=_TIMEOUT)
        body = resp.json()

        if resp.status_code == 200:
            msg_id = body.get("messages", [{}])[0].get("id", "?")
            log.info(f"WA {label} OK | msg_id={msg_id}")
            return True

        error = body.get("error", {})
        log.error(
            f"WA {label} gagal | HTTP {resp.status_code} | "
            f"code={error.get('code')} | msg={error.get('message')}"
        )
        return False

    except requests.Timeout:
        log.error(f"WA {label} timeout setelah {_TIMEOUT}s")
        return False
    except requests.ConnectionError as e:
        log.error(f"WA {label} connection error: {e}")
        return False
    except Exception as e:
        log.error(f"WA {label} unexpected error: {e}", exc_info=True)
        return False


def send_whatsapp_message(to: str, message: str) -> bool:
    log.debug(f"send_text → {to} | {len(message)} chars")
    return _post({
        "messaging_product": "whatsapp",
        "to": to, "type": "text",
        "text": {"body": message},
    }, label="text")


def send_whatsapp_list(to: str, body_text: str, button_text: str,
                       list_title: str, options: list) -> bool:
    if not options:
        log.warning("send_whatsapp_list: options kosong, skip")
        return False
    rows = [
        {"id": o["id"], "title": o["title"][:24], "description": o.get("desc", "")[:72]}
        for o in options
    ]
    return _post({
        "messaging_product": "whatsapp",
        "to": to, "type": "interactive",
        "interactive": {
            "type": "list",
            "body": {"text": body_text},
            "action": {"button": button_text, "sections": [{"title": list_title, "rows": rows}]},
        },
    }, label="list")


def send_whatsapp_buttons(to: str, body_text: str, buttons: list) -> bool:
    if not buttons:
        log.warning("send_whatsapp_buttons: buttons kosong, skip")
        return False
    ibuttons = [
        {"type": "reply", "reply": {"id": b["id"], "title": b["title"][:20]}}
        for b in buttons[:3]
    ]
    return _post({
        "messaging_product": "whatsapp",
        "to": to, "type": "interactive",
        "interactive": {
            "type": "button",
            "body": {"text": body_text},
            "action": {"buttons": ibuttons},
        },
    }, label="buttons")