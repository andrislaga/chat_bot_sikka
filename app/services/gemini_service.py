"""
gemini_service.py — Gemini sebagai Text Beautifier untuk SISKA
===============================================================
Tugas satu-satunya:
  - Terima teks yang sudah dibuat oleh bot (data, layanan, dll)
  - Rapikan agar lebih natural dan enak dibaca di WhatsApp
  - Kembalikan teks asli jika Gemini gagal (fallback aman)

Tambahkan ke .env:
    GEMINI_API_KEY=your_api_key_here
    GEMINI_MODEL=gemini-2.0-flash       # opsional
"""

import os
import requests
from dotenv import load_dotenv

load_dotenv()

_API_KEY  = os.getenv("GEMINI_API_KEY", "")
_MODEL    = os.getenv("GEMINI_MODEL", "gemini-2.0-flash")
_ENDPOINT = (
    "https://generativelanguage.googleapis.com/v1beta/models"
    f"/{_MODEL}:generateContent"
)
_TIMEOUT  = 10  # detik — cukup singkat agar tidak blokir respons


class GeminiService:

    def beautify(self, raw_text: str) -> str:
        """
        Rapikan teks output bot agar lebih natural di WhatsApp.

        - Konten/data/fakta TIDAK boleh diubah atau ditambah
        - Format WhatsApp tetap dijaga: *bold*, _italic_, newline
        - Emoji boleh disesuaikan, tapi jangan berlebihan
        - Jika Gemini gagal → kembalikan raw_text apa adanya

        Parameters
        ----------
        raw_text : teks yang sudah digenerate oleh bot

        Returns
        -------
        Teks yang sudah dirapikan, atau raw_text jika gagal.
        """
        if not _API_KEY or not raw_text or not raw_text.strip():
            return raw_text

        prompt = (
            "Kamu adalah editor pesan WhatsApp untuk chatbot BPS Kabupaten Sikka.\n\n"
            "Tugas kamu: rapikan pesan berikut agar lebih natural, mudah dibaca, "
            "dan terasa seperti ditulis oleh asisten yang ramah.\n\n"
            "ATURAN WAJIB:\n"
            "1. Jangan ubah, tambah, atau kurangi data/fakta/angka apapun.\n"
            "2. Jangan ubah link, nomor telepon, atau email.\n"
            "3. Gunakan format WhatsApp: *teks* untuk bold, _teks_ untuk italic.\n"
            "4. Emoji boleh disesuaikan tapi tidak berlebihan.\n"
            "5. Struktur dan alur pesan tetap sama.\n"
            "6. Keluarkan HANYA teks yang sudah dirapikan, tanpa komentar tambahan.\n\n"
            f"PESAN ASLI:\n{raw_text}"
        )

        payload = {
            "contents": [
                {"role": "user", "parts": [{"text": prompt}]}
            ],
            "generationConfig": {
                "temperature"    : 0.3,
                "maxOutputTokens": 600,
            },
        }

        try:
            resp = requests.post(
                _ENDPOINT,
                params={"key": _API_KEY},
                json=payload,
                timeout=_TIMEOUT,
            )
            if resp.status_code == 200:
                candidates = resp.json().get("candidates", [])
                if candidates:
                    parts = candidates[0].get("content", {}).get("parts", [])
                    result = "".join(p.get("text", "") for p in parts).strip()
                    if result:
                        print("✨ Gemini beautify: OK")
                        return result
            else:
                print(f"⚠️ Gemini HTTP {resp.status_code} — pakai teks asli.")
        except requests.Timeout:
            print("⏱️ Gemini timeout — pakai teks asli.")
        except Exception as e:
            print(f"❌ Gemini error: {e} — pakai teks asli.")

        return raw_text  # fallback: teks asli tidak berubah


# Singleton — satu instance untuk seluruh aplikasi
gemini_service = GeminiService()