"""
formatter.py — Formatter Response SISKA Chatbot BPS Sikka
=========================================================
Perubahan:
  - fix: format_service_response menggunakan kolom DB yang benar
    (tabel `services` punya kolom: id, name, content, category, updated_at)
    → bukan nama_layanan/deskripsi/link_info yang lama dan tidak ada di DB
  - Tambah format_statistik_response untuk data list dari BPS API
  - Semua formatter sekarang konsisten dengan skema DB
"""


class Formatter:

    # ════════════════════════════════════════════════════════════
    #  LAYANAN
    # ════════════════════════════════════════════════════════════
    @staticmethod
    def format_service_response(data: dict) -> str:
        """
        Format data layanan dari tabel `services`.
        Kolom yang tersedia: id, name, content, category, updated_at
        """
        if not data:
            return (
                "Maaf Kak, informasi layanan tersebut belum tersedia di BPS Sikka. 🙏\n"
                "Ketik *menu* untuk kembali ke Menu Utama."
            )

        res  = f"ℹ️ *{data.get('name', 'Informasi Layanan')}*\n"
        res += "─" * 30 + "\n"
        res += f"{data.get('content', 'Informasi belum tersedia.')}\n"

        if data.get("category"):
            res += f"\n📂 Kategori: _{data['category']}_"

        res += "\n\nKetik *layanan* untuk melihat info lainnya atau *menu* untuk Menu Utama."
        return res

    @staticmethod
    def format_service_list(services: list) -> str:
        """Format daftar singkat semua layanan (untuk tampilan teks biasa)."""
        if not services:
            return "Belum ada data layanan tersedia. 🙏"

        res = "ℹ️ *INFORMASI LAYANAN BPS KABUPATEN SIKKA*\n" + "─" * 30 + "\n"
        for i, s in enumerate(services, 1):
            res += f"{i}. *{s.get('name', '-')}*\n"
        res += "\nBalas dengan nomor layanan atau ketik nama layanan untuk detail. 😊"
        return res

    # ════════════════════════════════════════════════════════════
    #  PUBLIKASI
    # ════════════════════════════════════════════════════════════
    @staticmethod
    def format_publication_response(list_data: list) -> str:
        """
        Format daftar publikasi dari BPS API atau database lokal.
        Mendukung kedua format (API BPS: 'rl_date'/'pdf', DB lokal: 'year'/'link').
        """
        if not list_data:
            return (
                "Maaf Kak, tidak ada publikasi yang ditemukan. 📚\n"
                "Coba ketik *publikasi* atau nama publikasi yang Kakak cari."
            )

        res = "📚 *DAFTAR PUBLIKASI BPS KABUPATEN SIKKA*\n" + "─" * 30 + "\n\n"

        for i, pub in enumerate(list_data[:5], 1):
            title   = pub.get("title", "─")
            # Tanggal: coba format API dulu, fallback ke year
            tanggal = pub.get("rl_date") or str(pub.get("year", "─"))
            # Link: coba 'pdf' (format API) lalu 'link' (format DB)
            link    = pub.get("pdf") or pub.get("link", "─")

            res += f"{i}. *{title}*\n"
            res += f"   📅 {tanggal}\n"
            res += f"   🔗 {link}\n\n"

        res += "_Ketik *publikasi* untuk daftar lengkap._"
        return res

    @staticmethod
    def format_publication_detail(pub: dict) -> str:
        """Format detail satu publikasi dengan link unduhan."""
        if not pub:
            return "Maaf Kak, publikasi tidak ditemukan. 🙏"

        res  = f"📗 *{pub.get('title', 'Publikasi BPS')}*\n"
        res += "─" * 30 + "\n"

        if pub.get("description"):
            res += f"📝 {pub['description']}\n\n"

        link = pub.get("link") or pub.get("pdf", "─")
        res += f"🔗 *Link Unduhan:*\n{link}\n\n"
        res += "_Ketik *publikasi* untuk melihat daftar lainnya._"
        return res

    # ════════════════════════════════════════════════════════════
    #  STATISTIK
    # ════════════════════════════════════════════════════════════
    @staticmethod
    def format_statistik_response(data_list: list) -> str:
        """
        Format list data statistik dari parse_bps_universal.
        Setiap item: {nama, wilayah, tahun, nilai, unit}
        """
        if not data_list:
            return "Maaf Kak, data statistik tidak ditemukan untuk periode tersebut. 🙏"

        res = "📊 *DATA STATISTIK BPS KABUPATEN SIKKA*\n" + "─" * 30 + "\n"

        for item in data_list[:10]:
            nama    = item.get("nama",    "─")
            wilayah = item.get("wilayah", "")
            tahun   = item.get("tahun",   "─")
            nilai   = item.get("nilai",   0)
            unit    = item.get("unit",    "")

            try:
                nilai_fmt = f"{float(nilai):,.2f}".rstrip("0").rstrip(".")
            except (ValueError, TypeError):
                nilai_fmt = str(nilai)

            res += f"\n• *{nama}*"
            if wilayah:
                res += f" ({wilayah})"
            res += f"\n  Tahun *{tahun}*: *{nilai_fmt} {unit}*"

        if len(data_list) > 10:
            res += f"\n\n_...dan {len(data_list) - 10} data lainnya tersedia._"

        res += "\n\nSumber: BPS Kabupaten Sikka 🏛️"
        return res

    # ════════════════════════════════════════════════════════════
    #  UNKNOWN / FALLBACK
    # ════════════════════════════════════════════════════════════
    @staticmethod
    def format_unknown_response() -> str:
        """Pesan fallback jika chatbot tidak mengenali intent user."""
        return (
            "Maaf Kak, saya belum memahami maksud Kakak. 🤔\n\n"
            "Coba gunakan kata kunci seperti:\n"
            "• *inflasi* / *penduduk* → Data Statistik\n"
            "• *publikasi* / *buku*   → Publikasi BPS\n"
            "• *alamat* / *layanan*   → Info Layanan\n"
            "• *petugas* / *admin*    → Hubungi Petugas\n\n"
            "Atau ketik *menu* untuk melihat Menu Utama. 😊"
        )

    # ════════════════════════════════════════════════════════════
    #  HELPER
    # ════════════════════════════════════════════════════════════
    @staticmethod
    def _format_number(value) -> str:
        """Format angka ke string dengan titik ribuan."""
        try:
            f = float(value)
            if f == int(f):
                return f"{int(f):,}".replace(",", ".")
            return f"{f:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
        except (ValueError, TypeError):
            return str(value)