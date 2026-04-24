"""
bps_api.py — BPS API Client untuk SISKA Chatbot BPS Sikka
==========================================================
Perubahan:
  - Konsistenkan timeout di semua endpoint (30–60 detik)
  - Tambah User-Agent header di semua request
  - extract_data: handle kasus datacontent kosong lebih aman
  - parse_bps_universal: tambah turvar_id ke setiap hasil
  - Fungsi sinkronisasi publikasi & data variabel untuk scheduler
"""

import os
import requests
from dotenv import load_dotenv

load_dotenv()

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
}

class DataBPS:
    def __init__(self, nama, wilayah, tahun, bulan, nilai, unit):
        self.nama    = nama
        self.wilayah = wilayah
        self.tahun   = tahun
        self.bulan   = bulan
        self.nilai   = nilai
        self.unit    = unit

    def to_dict(self):
        return {
            "nama"   : self.nama,
            "wilayah": self.wilayah,
            "tahun"  : self.tahun,
            "nilai"  : self.nilai,
            "unit"   : self.unit,
        }

class BpsApi:
    def __init__(self):
        self.token          = os.getenv("BPS_API_TOKEN")
        self.base_url       = os.getenv("BPS_BASE_URL")
        self.default_domain = "5310"  # Kabupaten Sikka

    # ── HELPER ──────────────────────────────────────────────────
    def _get(self, url: str, params: dict, timeout: int = 30):
        try:
            resp = requests.get(url, params=params, headers=_HEADERS, timeout=timeout)
            if resp.status_code == 200:
                return resp.json()
            print(f"⚠️ HTTP {resp.status_code} untuk {url}")
        except requests.Timeout:
            print(f"⏱️ Timeout saat akses: {url}")
        except Exception as e:
            print(f"❌ Request error: {e}")
        return None

    # ════════════════════════════════════════════════════════════
    #  PUBLIKASI
    # ════════════════════════════════════════════════════════════

    def get_publications(self, keyword: str = "", page: int = 1):
        """Ambil daftar publikasi dari BPS API, opsional filter kata kunci."""
        data = self._get(
            f"{self.base_url}/list",
            {
                "model"  : "publication",
                "domain" : self.default_domain,
                "keyword": keyword,
                "page"   : page,
                "key"    : self.token,
            },
            timeout=30,
        )

        if data and data.get("status") == "OK" and "data" in data:
            res_data = data["data"]
            if isinstance(res_data, list) and len(res_data) >= 2:
                return res_data[1]
        return []

    def fetch_all_publications(self):
        """
        Ambil SEMUA publikasi dan pastikan struktur field konsisten.
        """
        all_pubs = []
        page = 1
        while True:
            pubs = self.get_publications(page=page)
            if not pubs:
                break
                
            for p in pubs:
                # Normalisasi data di sini agar UI lebih bersih
                normalized_pub = {
                    'title': p.get('title', 'Publikasi BPS'),
                    'year': p.get('rl_date', '')[:4] if p.get('rl_date') else '-',
                    'link': p.get('pdf', '-'),
                    'desc': p.get('abstract', '')
                }
                all_pubs.append(normalized_pub)
                
            if len(pubs) < 10: 
                break
            page += 1
        return all_pubs

    # ════════════════════════════════════════════════════════════
    #  DATA VARIABEL DINAMIS
    # ════════════════════════════════════════════════════════════

    def get_dynamic_data(self, subject_id: str):
        """Ambil data variabel berdasarkan subject_id."""
        data = self._get(
            f"{self.base_url}/list",
            {
                "model"  : "var",
                "domain" : self.default_domain,
                "subject": subject_id,
                "key"    : self.token,
            },
            timeout=60,
        )
        if data and data.get("status") == "OK":
            return data.get("data", [])
        return None

    def get_actual_data_all_pages(self, var_id: str):
        """
        Ambil data aktual (semua halaman) via path-style URL.
        Dicoba dua lokasi datacontent: root atau di dalam key 'data'.
        """
        url = (
            f"{self.base_url}/list/model/data/domain/{self.default_domain}"
            f"/var/{var_id}/th/all/page/1/key/{self.token}/"
        )

        print(f"📡 BPS API Request: {url}")
        data = self._get(url, {}, timeout=60)

        if not data:
            return None

        if data.get("status") == "OK":
            if "datacontent" in data:
                print(f"✅ datacontent di ROOT ({len(data['datacontent'])} entri)")
                return data
            if "data" in data and "datacontent" in data.get("data", {}):
                print("✅ datacontent di key 'data'")
                return data["data"]
            print(f"⚠️ Status OK tapi datacontent tidak ditemukan. Keys: {list(data.keys())}")
        else:
            print(f"❌ BPS API Error: {data.get('message', 'Unknown error')}")

        return None

    def parse_bps_universal(self, data: dict) -> list:
        if not data or "var" not in data:
            return []

        hasil = []
        var_info = data["var"][0]
        nama_var = var_info["label"]
        unit = var_info.get("unit", "")

        vervar_list = data.get("vervar", [])
        turvar_list = data.get("turvar", [])
        tahun_list = data.get("tahun", [])
        turtahun_list = data.get("turtahun", [{"val": "", "label": ""}])
        datacontent = data.get("datacontent", {})

        # Mapping manual untuk jaga-jaga jika label kosong
        fallback_turvar = {"41": "Laki-laki", "42": "Perempuan", "43": "Total"}

        for v in vervar_list:
            vervar_val = str(v["val"])
            vervar_label = v["label"]

            for tvar in turvar_list:
                turvar_val = str(tvar["val"])
                turvar_label = tvar.get("label") or fallback_turvar.get(turvar_val, f"Dimensi {turvar_val}")

                for th in tahun_list:
                    tahun_val = str(th["val"])
                    tahun_label = th["label"]

                    for tth in turtahun_list:
                        turtahun_val = str(tth["val"])
                        turtahun_label = tth.get("label", "")
                        if turtahun_label and turtahun_label != "Tahun" and not turtahun_label.isdigit():
                            periode = f"{turtahun_label} {tahun_label}"
                        else:
                            periode = tahun_label

                        key = vervar_val + str(var_info["val"]) + turvar_val + tahun_val + turtahun_val
                        if key in datacontent:
                            nilai = datacontent[key]
                            try:
                                nilai_float = float(nilai) if nilai and nilai != "-" else 0.0
                            except:
                                nilai_float = 0.0

                            hasil.append({
                                "nama": nama_var,
                                "wilayah": vervar_label,
                                "tahun": periode,
                                "nilai": nilai_float,
                                "unit": unit,
                                "turvar_id": turvar_val,
                                "turvar_label": turvar_label,
                                "vervar_id": vervar_val,
                                "vervar_label": vervar_label,
                            })
        return hasil



        # ════════════════════════════════════════════════════════════════
    #  SINKRONISASI DATA (untuk scheduler)
    # ════════════════════════════════════════════════════════════════

    def fetch_all_variables_metadata(self):
        """
        Ambil metadata semua variabel dari BPS API.
        Jika endpoint tidak tersedia, kembalikan list kosong.
        """
        endpoints = [
            f"{self.base_url}/list/model/sub/domain/{self.default_domain}/key/{self.token}/",
            f"{self.base_url}/list/model/subject/domain/{self.default_domain}/key/{self.token}/",
        ]
        
        for url in endpoints:
            data = self._get(url, {}, timeout=30)
            if data and data.get("status") == "OK":
                res_data = data.get("data", [])
                subject_list = []
                if isinstance(res_data, list) and len(res_data) >= 2:
                    subject_list = res_data[1] if isinstance(res_data[1], list) else []
                elif isinstance(res_data, list):
                    subject_list = res_data
                
                all_vars = []
                for sub in subject_list:
                    subject_id = sub.get("sub_id") or sub.get("subject_id") or sub.get("id")
                    subject_name = sub.get("sub_name") or sub.get("subject_name") or sub.get("name") or sub.get("title")
                    if not subject_id:
                        continue
                    vars_data = self.get_dynamic_data(subject_id)
                    if vars_data:
                        for v in vars_data:
                            all_vars.append({
                                "id_var": v.get("val"),
                                "nama_var": v.get("label"),
                                "subjek": subject_name,
                                "unit": v.get("unit", ""),
                            })
                if all_vars:
                    print(f"✅ Berhasil ambil {len(all_vars)} metadata variabel.")
                    return all_vars
        
        print("⚠️ Metadata variabel tidak tersedia (endpoint subject tidak dapat diakses).")
        return []


    def get_aggregated_yearly_data(self, var_id: str):
        """
        Ambil data mentah dari API, lalu agregasi per tahun
        (jumlahkan seluruh vervar dan turvar).
        Return (list_of_dict, unit) jika ada data tahunan, atau (None, None) jika tidak.
        """
        raw = self.get_actual_data_all_pages(var_id)
        if not raw:
            return None, None

        parsed = self.parse_bps_universal(raw)
        if not parsed:
            return None, None

        has_year = False
        agg = {}
        unit = parsed[0].get('unit', '') if parsed else ''
        import re
        for item in parsed:
            tahun_str = item.get('tahun', '')
            match = re.search(r'\b(19|20)\d{2}\b', tahun_str)
            if not match:
                continue
            tahun = match.group()
            has_year = True
            nilai = item.get('nilai', 0)
            try:
                nilai = float(nilai)
            except:
                nilai = 0.0
            agg[tahun] = agg.get(tahun, 0) + nilai

        if not has_year:
            return None, None

        result = []
        for tahun in sorted(agg.keys()):
            result.append({
                'tahun': tahun,
                'nilai': agg[tahun],
                'unit': unit
            })
        return result, unit