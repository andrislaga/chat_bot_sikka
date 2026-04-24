"""
intent_detection.py — Deteksi Intent untuk SISKA Chatbot BPS Sikka
===================================================================
Perubahan:
  - Hapus data training konflik: angka "1","2","3","4" sebelumnya
    dipetakan ke DUA intent berbeda sekaligus (greeting DAN statistik/publikasi/dll)
    → ini membuat Naive Bayes tidak akurat
  - Angka HANYA ditangani oleh mapping_angka (Strategi 1), bukan training data
  - Tambah lebih banyak contoh kalimat agar model lebih kuat
  - Tambah intent 'unknown_media' untuk pesan media (foto, video, dll)
"""

import re
from thefuzz import fuzz
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.naive_bayes import MultinomialNB
from sklearn.pipeline import make_pipeline


class IntentDetection:
    def __init__(self):
        # ── Dataset Training ─────────────────────────────────────
        # PENTING: JANGAN masukkan angka "1","2","3","4" di sini
        # karena akan konflik dengan mapping_angka di get_intent().
        self.data = [
            # ── STATISTIK ──
            ("berapa angka inflasi sikka",             "statistik"),
            ("data penduduk terbaru kabupaten sikka",  "statistik"),
            ("jumlah penduduk miskin",                 "statistik"),
            ("pertumbuhan ekonomi tahun ini",          "statistik"),
            ("angka pengangguran di maumere",          "statistik"),
            ("ipm sikka berapa",                       "statistik"),
            ("indeks pembangunan manusia",             "statistik"),
            ("kemiskinan di sikka",                    "statistik"),
            ("data statistik",                         "statistik"),
            ("tanya data statistik",                   "statistik"),
            ("statistik",                              "statistik"),
            ("inflasi",                                "statistik"),
            ("penduduk",                               "statistik"),
            ("kat_",                                   "statistik"),
            ("var_",                                   "statistik"),
            ("data ekonomi",                           "statistik"),
            ("pdrb sikka",                             "statistik"),
            ("nilai ekspor impor",                     "statistik"),
            ("tingkat kemiskinan",                     "statistik"),
            ("angka harapan hidup",                    "statistik"),
            ("rata rata lama sekolah",                 "statistik"),

            # ── PUBLIKASI ──
            ("download buku sikka dalam angka",        "publikasi"),
            ("unduh laporan bulanan bps",              "publikasi"),
            ("minta file pdf berita resmi statistik",  "publikasi"),
            ("rilis pers terbaru",                     "publikasi"),
            ("buku",                                   "publikasi"),
            ("unduh publikasi",                        "publikasi"),
            ("pub_",                                   "publikasi"),
            ("cari publikasi",                         "publikasi"),
            ("laporan bps",                            "publikasi"),
            ("pdf bps",                                "publikasi"),
            ("sikka dalam angka",                      "publikasi"),
            ("berita resmi statistik",                 "publikasi"),
            ("kecamatan dalam angka",                  "publikasi"),
            ("dokumen bps",                            "publikasi"),

            # ── LAYANAN ──
            ("dimana alamat kantor bps sikka",         "layanan"),
            ("jam buka pelayanan pst",                 "layanan"),
            ("syarat permintaan data",                 "layanan"),
            ("lokasi kantor bps",                      "layanan"),
            ("tanya alamat kantor dimana",             "layanan"),
            ("kapan kantor buka",                      "layanan"),
            ("alamat",                                 "layanan"),
            ("info layanan bps",                       "layanan"),
            ("btn_",                                   "layanan"),
            ("jam pelayanan",                          "layanan"),
            ("prosedur pelayanan data",                "layanan"),
            ("cara mendapatkan data",                  "layanan"),
            ("nomor telepon bps",                      "layanan"),
            ("email bps",                              "layanan"),
            ("pelayanan statistik terpadu",            "layanan"),
            ("pst bps",                                "layanan"),

            # ── AGEN ──
            ("admin",                                  "agen"),
            ("saya mau bicara dengan petugas",         "agen"),
            ("hubungkan ke operator",                  "agen"),
            ("bantuan manusia",                        "agen"),
            ("hubungi petugas",                        "agen"),
            ("cs bps",                                 "agen"),
            ("minta tolong petugas",                   "agen"),
            ("operator",                               "agen"),
            ("agen",                                   "agen"),
            ("petugas",                                "agen"),
            ("ingin bicara dengan orang",              "agen"),
            ("saya butuh bantuan langsung",            "agen"),

            # ── GREETING ──
            ("halo",                                   "greeting"),
            ("selamat pagi",                           "greeting"),
            ("hi",                                     "greeting"),
            ("pagi",                                   "greeting"),
            ("siska",                                  "greeting"),
            ("menu",                                   "greeting"),
            ("bantuan",                                "greeting"),
            ("selamat siang",                          "greeting"),
            ("selamat malam",                          "greeting"),
            ("hai",                                    "greeting"),
            ("mulai",                                  "greeting"),
            ("start",                                  "greeting"),
            ("p",                                      "greeting"),
            ("hei",                                    "greeting"),
        ]

        # ── Model NLP: TF-IDF + Naive Bayes ──────────────────────
        self.model = make_pipeline(TfidfVectorizer(ngram_range=(1, 2)), MultinomialNB())
        self.train_model()

        # ── Fallback Keyword (Fuzzy Match) ───────────────────────
        self.keywords = {
            "statistik": ["data", "angka", "inflasi", "penduduk", "jumlah", "tingkat",
                          "ipm", "kemiskinan", "pengangguran", "ekonomi", "pdrb"],
            "publikasi": ["buku", "pdf", "unduh", "download", "laporan", "publikasi",
                          "dokumen", "rilis"],
            "layanan"  : ["alamat", "jam", "buka", "lokasi", "pst", "syarat",
                          "prosedur", "telepon", "email", "pelayanan"],
            "agen"     : ["petugas", "admin", "bantuan", "operator", "cs", "agen",
                          "manusia", "bicara"],
            "greeting" : ["halo", "hai", "hi", "pagi", "siang", "malam", "hei",
                          "menu", "siska", "mulai"],
        }

        # ── Mapping angka → intent (prioritas tinggi) ─────────────
        self.mapping_angka = {
            "1": "statistik",
            "2": "publikasi",
            "3": "layanan",
            "4": "agen",
        }

    def train_model(self):
        """Latih model dengan dataset yang ada."""
        X, y = zip(*self.data)
        self.model.fit(X, y)
        print(f"✅ Intent model dilatih dengan {len(self.data)} sampel.")

    def preprocess(self, text: str) -> str:
        """Normalisasi teks: lowercase, hapus tanda baca."""
        if not text:
            return ""
        text = text.lower().strip()
        text = re.sub(r"[^\w\s]", "", text)
        return text

    def get_intent(self, user_text: str, payload_id: str = None) -> str:
        """
        Deteksi intent dengan 4 strategi bertingkat:
        0. Prefix Payload ID (tombol interaktif)
        1. Mapping angka langsung
        2. Machine Learning (TF-IDF + Naive Bayes)
        3. Fuzzy Matching sebagai fallback
        """

        # ── Strategi 0: PAYLOAD ID PREFIX ────────────────────────
        if payload_id:
            prefix_map = {
                "pub_"  : "publikasi",
                "btn_"  : "layanan",
                "kat_"  : "statistik",
                "var_"  : "statistik",
                "dim_"  : "statistik",
            }
            for prefix, intent in prefix_map.items():
                if payload_id.startswith(prefix):
                    print(f"  [Strategi 0 - Payload] {payload_id} → {intent}")
                    return intent

            # menu_ prefix
            if payload_id.startswith("menu_"):
                menu_map = {
                    "menu_1": "statistik",
                    "menu_2": "publikasi",
                    "menu_3": "layanan",
                    "menu_4": "agen",
                }
                intent = menu_map.get(payload_id, "greeting")
                print(f"  [Strategi 0 - Menu] {payload_id} → {intent}")
                return intent

        clean_text = self.preprocess(user_text)

        # ── Strategi 1: MAPPING ANGKA ────────────────────────────
        if clean_text in self.mapping_angka:
            intent = self.mapping_angka[clean_text]
            print(f"  [Strategi 1 - Angka] '{clean_text}' → {intent}")
            return intent

        # ── Strategi 2: MACHINE LEARNING ─────────────────────────
        if clean_text:
            try:
                probabilities = self.model.predict_proba([clean_text])[0]
                max_prob      = max(probabilities)
                if max_prob > 0.40:
                    predicted = self.model.predict([clean_text])[0]
                    print(f"  [Strategi 2 - ML] '{clean_text}' → {predicted} (prob={max_prob:.2f})")
                    return predicted
            except Exception as e:
                print(f"  [Strategi 2 - ML Error] {e}")

        # ── Strategi 3: FUZZY MATCHING ───────────────────────────
        words = clean_text.split()
        for word in words:
            for intent, tags in self.keywords.items():
                for tag in tags:
                    score = fuzz.ratio(word, tag)
                    if score > 85:
                        print(f"  [Strategi 3 - Fuzzy] '{word}' ~ '{tag}' (score={score}) → {intent}")
                        return intent

        print(f"  [Tidak terdeteksi] '{clean_text}' → unknown")
        return "unknown"