"""
database_service.py — DatabaseService untuk SISKA Chatbot BPS Sikka
=====================================================================
Perubahan:
  - Hapus duplikat get_cache, set_cache, get_publication_by_title
  - Satukan versi paling lengkap dengan parameter table_name (fleksibel)
  - Tambah method get_or_set_cache yang konsisten
  - Method untuk agen multi‑turn (assign, close, get_thread)
  - Method untuk layanan: add_service
  - Method untuk sinkronisasi cache statistik & publikasi
"""

import json
import os
import mysql.connector
from mysql.connector import Error
from dotenv import load_dotenv
from datetime import datetime, timezone

load_dotenv()

class DatabaseService:
    def __init__(self):
        self.config = {
            "host"    : os.getenv("DB_HOST", "localhost"),
            "user"    : os.getenv("DB_USER", "root"),
            "password": os.getenv("DB_PASSWORD", ""),
            "database": os.getenv("DB_NAME", "chatbot_bps"),
        }

    # ════════════════════════════════════════════════════════════
    #  KONEKSI & QUERY DASAR
    # ════════════════════════════════════════════════════════════

    def get_connection(self):
        try:
            return mysql.connector.connect(**self.config)
        except Error as e:
            print(f"❌ Koneksi MySQL Gagal: {e}")
            return None

    def fetch_all(self, query: str, params=None):
        conn = self.get_connection()
        if not conn:
            return []
        cursor = conn.cursor(dictionary=True)
        try:
            cursor.execute(query, params or ())
            return cursor.fetchall()
        except Error as e:
            print(f"❌ fetch_all error: {e}")
            return []
        finally:
            cursor.close()
            conn.close()

    def fetch_one(self, query: str, params=None):
        conn = self.get_connection()
        if not conn:
            return None
        cursor = conn.cursor(dictionary=True)
        try:
            cursor.execute(query, params or ())
            return cursor.fetchone()
        except Error as e:
            print(f"❌ fetch_one error: {e}")
            return None
        finally:
            cursor.close()
            conn.close()

    def execute_query(self, query: str, params=None) -> bool:
        conn = self.get_connection()
        if not conn:
            return False
        cursor = conn.cursor()
        try:
            cursor.execute(query, params or ())
            conn.commit()
            return True
        except Error as e:
            print(f"🚨 execute_query error: {e}")
            conn.rollback()
            return False
        finally:
            cursor.close()
            conn.close()

    def execute(self, query: str, params=None) -> bool:
        return self.execute_query(query, params)

    def insert_and_get_id(self, query: str, params=None) -> int:
        """Insert dan kembalikan ID yang di-generate."""
        conn = self.get_connection()
        if not conn:
            return 0
        cursor = conn.cursor()
        try:
            cursor.execute(query, params or ())
            conn.commit()
            return cursor.lastrowid
        except Error as e:
            print(f"🚨 insert_and_get_id error: {e}")
            conn.rollback()
            return 0
        finally:
            cursor.close()
            conn.close()

    # ════════════════════════════════════════════════════════════
    #  AGENT REQUESTS (Multi‑Turn)
    # ════════════════════════════════════════════════════════════

    def create_agent_request(self, phone: str, message: str, conversation_id: int = None) -> int:
        """
        Buat request agen baru. Jika conversation_id tidak diberikan,
        buat conversation baru (insert ke tabel terpisah atau gunakan ID request sebagai conv_id).
        Return ID request yang dibuat.
        """
        if conversation_id is None:
            # Buat conversation baru (bisa gunakan tabel agent_conversations, tapi untuk sederhana
            # kita gunakan request pertama sebagai conversation_id)
            conv_id = None
        else:
            conv_id = conversation_id

        # Insert dulu request, dapatkan ID
        req_id = self.insert_and_get_id(
            """INSERT INTO agent_requests (phone, message, status, conversation_id)
               VALUES (%s, %s, 'pending', %s)""",
            (phone, message, conv_id)
        )
        if req_id and conv_id is None:
            # Jika ini request pertama, set conversation_id = req_id
            self.execute_query(
                "UPDATE agent_requests SET conversation_id = %s WHERE id = %s",
                (req_id, req_id)
            )
        return req_id

    def get_active_conversation(self, phone: str):
        """
        Cek apakah user memiliki percakapan agen yang masih aktif.
        Status aktif: 'pending', 'in_progress', 'responded'.
        Kembalikan dict dengan info conversation_id, assigned_admin, status.
        """
        return self.fetch_one(
            """SELECT conversation_id, status, assigned_admin
               FROM agent_requests
               WHERE phone = %s AND status IN ('pending','in_progress','responded')
               ORDER BY created_at DESC LIMIT 1""",
            (phone,)
        )



    def assign_conversation(self, conversation_id: int, admin_username: str) -> bool:
        return self.execute_query(
            """UPDATE agent_requests
            SET status = 'in_progress', assigned_admin = %s
            WHERE conversation_id = %s""",
            (admin_username, conversation_id)
        )

    def add_agent_reply(self, conversation_id: int, admin_username: str, reply_message: str) -> int:
        conv = self.fetch_one(
            "SELECT DISTINCT phone FROM agent_requests WHERE conversation_id = %s LIMIT 1",
            (conversation_id,)
        )
        if not conv:
            return 0

        new_id = self.insert_and_get_id(
            """INSERT INTO agent_requests
            (phone, message, status, conversation_id, assigned_admin, is_admin_reply, is_read)
            VALUES (%s, %s, 'responded', %s, %s, 1, 1)""",
            (conv['phone'], reply_message, conversation_id, admin_username)
        )
        # ✅ Update seluruh conversation: status = 'responded', assigned_admin = admin_username
        self.execute_query(
            "UPDATE agent_requests SET status = 'responded', assigned_admin = %s "
            "WHERE conversation_id = %s",
            (admin_username, conversation_id)
        )
        return new_id

    def close_conversation(self, conversation_id: int) -> bool:
        """Tutup percakapan, status menjadi 'closed'."""
        return self.execute_query(
            "UPDATE agent_requests SET status = 'closed' WHERE conversation_id = %s",
            (conversation_id,)
        )

    def get_all_pending_conversations(self):
        """
        Ambil daftar percakapan yang belum ditutup, dikelompokkan per conversation.
        Tampilkan ringkasan: phone, last_message_time, status, assigned_admin.
        """
        return self.fetch_all(
            """SELECT
                   conversation_id,
                   phone,
                   MAX(created_at) as last_update,
                   status,
                   assigned_admin,
                   COUNT(*) as message_count
               FROM agent_requests
               WHERE status != 'closed'
               GROUP BY conversation_id, phone, status, assigned_admin
               ORDER BY last_update DESC"""
        )

    # ════════════════════════════════════════════════════════════
    #  SERVICES (Layanan BPS)
    # ════════════════════════════════════════════════════════════

    def get_all_services(self):
        return self.fetch_all("SELECT * FROM services ORDER BY id")

    def update_service(self, service_id: int, name: str, content: str) -> bool:
        return self.execute_query(
            "UPDATE services SET name = %s, content = %s WHERE id = %s",
            (name, content, service_id),
        )

    def add_service(self, name: str, content: str, category: str = "Umum") -> bool:
        return self.execute_query(
            "INSERT INTO services (name, content, category) VALUES (%s, %s, %s)",
            (name, content, category)
        )

    # ════════════════════════════════════════════════════════════
    #  PUBLICATIONS (Cache dari API)
    # ════════════════════════════════════════════════════════════

    def replace_all_publications(self, pubs: list) -> bool:
        """
        Hapus semua data publikasi lama, lalu insert yang baru.
        Digunakan oleh scheduler.
        """
        conn = self.get_connection()
        if not conn:
            return False
        cursor = conn.cursor()
        try:
            cursor.execute("DELETE FROM publications")
            for p in pubs:
                cursor.execute(
                    """INSERT INTO publications (title, year, link, description)
                       VALUES (%s, %s, %s, %s)""",
                    (p.get('title'), p.get('rl_date', '')[:4] or None, p.get('pdf'), p.get('abstract', ''))
                )
            conn.commit()
            return True
        except Error as e:
            print(f"🚨 replace_all_publications error: {e}")
            conn.rollback()
            return False
        finally:
            cursor.close()
            conn.close()

    def get_publications_paginated(self, limit: int = 10, offset: int = 0):
        """Ambil publikasi dengan paginasi, urut tahun terbaru."""
        return self.fetch_all(
            """SELECT id, title, year, link, description
               FROM publications
               ORDER BY year DESC, id DESC
               LIMIT %s OFFSET %s""",
            (limit, offset)
        )

    # ════════════════════════════════════════════════════════════
    #  CACHE STATISTIK (untuk data variabel & dimensi)
    # ════════════════════════════════════════════════════════════

    def save_variable_cache(self, var_id: str, parsed_data: list) -> bool:
        json_str = json.dumps(parsed_data, ensure_ascii=False)
        return self.execute_query(
            """INSERT INTO bps_cache_stats (cache_key, json_data)
            VALUES (%s, %s)
            ON DUPLICATE KEY UPDATE json_data = %s, updated_at = CURRENT_TIMESTAMP""",
            (f"var_{var_id}", json_str, json_str)
        )

    def get_variable_cache(self, var_id: str):
        row = self.fetch_one(
            """SELECT json_data FROM bps_cache_stats
            WHERE cache_key = %s
                AND updated_at > NOW() - INTERVAL 48 HOUR
            LIMIT 1""",
            (f"var_{var_id}",)
        )
        if row:
            try:
                return json.loads(row['json_data'])
            except:
                return None
        return None

    # ════════════════════════════════════════════════════════════
    #  USERS & SESSION (tetap)
    # ════════════════════════════════════════════════════════════
    def update_user_stats(self, phone: str, name: str = None) -> bool:
        return self.execute_query(
            """
            INSERT INTO users (phone, name, total_messages, last_seen)
            VALUES (%s, %s, 1, CURRENT_TIMESTAMP)
            ON DUPLICATE KEY UPDATE
                total_messages = total_messages + 1,
                last_seen      = CURRENT_TIMESTAMP
            """,
            (phone, name),
        )

    def get_user_by_phone(self, phone: str):
        return self.fetch_one("SELECT id FROM users WHERE phone = %s LIMIT 1", (phone,))

    def get_session(self, phone: str):
        return self.fetch_one(
            "SELECT * FROM chatbot_sessions WHERE phone = %s LIMIT 1", (phone,)
        )

    def update_session(self, phone: str, user_id: int, current_menu: str, last_intent: str) -> bool:
        try:
            self.execute_query("DELETE FROM chatbot_sessions WHERE phone = %s", (phone,))
            uid = user_id if user_id != 0 else None
            return self.execute_query(
                """
                INSERT INTO chatbot_sessions (user_id, phone, current_menu, last_intent)
                VALUES (%s, %s, %s, %s)
                """,
                (uid, phone, current_menu, last_intent),
            )
        except Exception as e:
            print(f"🚨 update_session error: {e}")
            return False

    def delete_session(self, phone: str) -> bool:
        return self.execute_query("DELETE FROM chatbot_sessions WHERE phone = %s", (phone,))

    # ════════════════════════════════════════════════════════════
    #  VARIABEL STATISTIK (dari DB lokal)
    # ════════════════════════════════════════════════════════════
    def get_categories(self):
        return self.fetch_all("SELECT DISTINCT subjek FROM variabel ORDER BY subjek")

    def get_variables_by_category(self, kategori: str):
        return self.fetch_all(
            "SELECT id_var, nama_var FROM variabel WHERE subjek = %s ORDER BY nama_var",
            (kategori,),
        )

    def get_variable_info(self, var_id: str):
        return self.fetch_one(
            "SELECT * FROM variabel WHERE id_var = %s", (var_id,)
        )

    def sync_variables_metadata(self, vars_metadata: list) -> bool:
        """Sinkronkan metadata variabel ke tabel variabel."""
        conn = self.get_connection()
        if not conn:
            return False
        cursor = conn.cursor()
        try:
            for v in vars_metadata:
                cursor.execute(
                    """INSERT INTO variabel (id_var, nama_var, subjek, unit)
                       VALUES (%s, %s, %s, %s)
                       ON DUPLICATE KEY UPDATE
                           nama_var = VALUES(nama_var),
                           subjek = VALUES(subjek),
                           unit = VALUES(unit)""",
                    (v['id_var'], v['nama_var'], v['subjek'], v['unit'])
                )
            conn.commit()
            return True
        except Error as e:
            print(f"🚨 sync_variables_metadata error: {e}")
            conn.rollback()
            return False
        finally:
            cursor.close()
            conn.close()

    # ════════════════════════════════════════════════════════════
    #  DASHBOARD & LOG
    # ════════════════════════════════════════════════════════════
    def get_dashboard_stats(self) -> dict:
        stats = {}
        res = self.fetch_one("SELECT COUNT(DISTINCT phone) AS total FROM users")
        stats["total_users"] = res["total"] if res else 0
        res = self.fetch_one(
            "SELECT COUNT(*) AS total FROM chat_logs WHERE DATE(created_at) = CURDATE()"
        )
        stats["chats_today"] = res["total"] if res else 0
        stats["top_intents"] = self.fetch_all(
            """
            SELECT intent, COUNT(*) AS jumlah FROM chat_logs
            WHERE intent NOT IN ('greeting', 'unknown', 'unknown_media')
            GROUP BY intent ORDER BY jumlah DESC LIMIT 3
            """
        )
        return stats

    def get_admin_by_username(self, username: str):
        return self.fetch_one(
            "SELECT * FROM admin_users WHERE username = %s LIMIT 1", (username,)
        )

    def get_filtered_logs(self, search_query: str = None, intent_filter: str = None, limit: int = 50):
        query  = "SELECT * FROM chat_logs WHERE 1=1"
        params = []
        if search_query:
            query += " AND sender LIKE %s"
            params.append(f"%{search_query}%")
        if intent_filter:
            query += " AND intent = %s"
            params.append(intent_filter)
        query += " ORDER BY sender, created_at ASC LIMIT %s"
        params.append(limit)
        return self.fetch_all(query, tuple(params))
    


    def close_idle_conversations(self, cutoff_time) -> int:
        """
        Ubah status menjadi 'closed' untuk semua conversation yang
        tidak memiliki aktivitas setelah `cutoff_time`.
        Return jumlah conversation yang ditutup.
        """
        conn = self.get_connection()
        if not conn:
            return 0
        cursor = conn.cursor()
        try:
            # Cari conversation_id yang statusnya bukan 'closed' dan last activity < cutoff_time
            cursor.execute("""
                UPDATE agent_requests
                SET status = 'closed'
                WHERE conversation_id IN (
                    SELECT conversation_id FROM (
                        SELECT conversation_id
                        FROM agent_requests
                        WHERE status != 'closed'
                        GROUP BY conversation_id
                        HAVING MAX(created_at) < %s
                    ) AS idle_convs
                )
            """, (cutoff_time,))
            conn.commit()
            affected = cursor.rowcount
            return affected
        except Error as e:
            print(f"🚨 close_idle_conversations error: {e}")
            conn.rollback()
            return 0
        finally:
            cursor.close()
            conn.close()

    # Method untuk mendapatkan waktu aktivitas terakhir suatu conversation (optional)
    def get_conversation_last_activity(self, conversation_id: int):
        row = self.fetch_one(
            "SELECT MAX(created_at) as last_activity FROM agent_requests WHERE conversation_id = %s",
            (conversation_id,)
        )
        return row['last_activity'] if row else None
    
    def get_conversation_thread(self, conversation_id: int):
        return self.fetch_all(
            """SELECT id, phone, message, status, assigned_admin, 
                    created_at, is_admin_reply, is_read
            FROM agent_requests
            WHERE conversation_id = %s
                AND message NOT LIKE '— Memulai%'
            ORDER BY created_at ASC""",
            (conversation_id,)
        )

    def mark_conversation_read(self, conversation_id: int) -> bool:
        """Tandai semua pesan dalam conversation sebagai sudah dibaca."""
        return self.execute_query(
            "UPDATE agent_requests SET is_read = 1 WHERE conversation_id = %s AND is_admin_reply = 0",
            (conversation_id,)
        )
    
    def get_conversations_for_dashboard(self):
        return self.fetch_all("""
            SELECT 
                conv.conversation_id,
                conv.phone,
                conv.last_update,
                latest.status,
                latest.assigned_admin,
                conv.message_count,
                conv.unread_count,
                conv.last_user_message,
                (SELECT message FROM agent_requests a3 
                WHERE a3.conversation_id = conv.conversation_id AND a3.is_admin_reply = 1
                ORDER BY created_at DESC LIMIT 1) as last_admin_reply
            FROM (
                SELECT 
                    conversation_id,
                    phone,
                    MAX(created_at) as last_update,
                    COUNT(*) as message_count,
                    SUM(CASE WHEN is_admin_reply = 0 AND is_read = 0 THEN 1 ELSE 0 END) as unread_count,
                    (SELECT message FROM agent_requests a2 
                    WHERE a2.conversation_id = a1.conversation_id AND a2.is_admin_reply = 0
                    ORDER BY created_at DESC LIMIT 1) as last_user_message
                FROM agent_requests a1
                WHERE status != 'closed'
                GROUP BY conversation_id, phone
            ) AS conv
            JOIN agent_requests AS latest 
                ON latest.id = (
                    SELECT id FROM agent_requests 
                    WHERE conversation_id = conv.conversation_id 
                    ORDER BY created_at DESC LIMIT 1
                )
            ORDER BY conv.last_update DESC
        """)
    
    def get_agent_conversation_summaries(self, limit: int = 100):
        """
        Mengambil ringkasan semua percakapan agen (termasuk closed) 
        dengan dua pesan terakhir (user & admin) untuk halaman log chat.
        """
        return self.fetch_all("""
            SELECT 
                conv.conversation_id,
                conv.phone,
                conv.last_update,
                conv.status,
                conv.assigned_admin,
                conv.message_count,
                conv.last_user_message,
                conv.last_admin_reply
            FROM (
                SELECT 
                    a.conversation_id,
                    a.phone,
                    MAX(a.created_at) as last_update,
                    (SELECT status FROM agent_requests WHERE conversation_id = a.conversation_id ORDER BY created_at DESC LIMIT 1) as status,
                    (SELECT assigned_admin FROM agent_requests WHERE conversation_id = a.conversation_id ORDER BY created_at DESC LIMIT 1) as assigned_admin,
                    COUNT(*) as message_count,
                    (SELECT message FROM agent_requests WHERE conversation_id = a.conversation_id AND is_admin_reply = 0 ORDER BY created_at DESC LIMIT 1) as last_user_message,
                    (SELECT message FROM agent_requests WHERE conversation_id = a.conversation_id AND is_admin_reply = 1 ORDER BY created_at DESC LIMIT 1) as last_admin_reply
                FROM agent_requests a
                GROUP BY a.conversation_id, a.phone
            ) AS conv
            ORDER BY conv.last_update DESC
            LIMIT %s
        """, (limit,))

    def get_or_refresh_variable(self, var_id: str, api_fetcher):
        """
        Ambil data variabel dengan logika:
        - Jika ada cache yang masih berlaku (≤48 jam) → return cache
        - Jika tidak ada atau expired → panggil api_fetcher(var_id)
        → simpan hasil ke cache → return hasil
        - Jika api_fetcher gagal → return None
        """
        # 1. Coba ambil dari cache yang masih fresh
        cached_data = self.get_variable_cache(var_id)
        if cached_data is not None:
            print(f"✅ Data var_{var_id} diambil dari cache (fresh)")
            return cached_data

        # 2. Cache tidak ada atau expired → panggil API
        print(f"🔄 Cache var_{var_id} tidak ada/expired, mengambil dari API...")
        try:
            fresh_data = api_fetcher(var_id)
            if fresh_data is not None:
                # Simpan ke cache (akan meng-update updated_at)
                self.save_variable_cache(var_id, fresh_data)
                print(f"💾 Data var_{var_id} disimpan ke cache (timestamp baru)")
                return fresh_data
            else:
                print(f"❌ API fetcher tidak mengembalikan data untuk var_{var_id}")
                return None
        except Exception as e:
            print(f"❌ Error saat memanggil API fetcher: {e}")
            return None
        



        # ============================================================
# CACHE PUBLIKASI (TTL 48 jam) – tambahkan di database_service.py
# ============================================================

    def get_publications_cache(self):
        """Ambil data publikasi dari cache jika masih fresh (≤48 jam)."""
        row = self.fetch_one(
            """SELECT json_data FROM bps_cache_pubs
            WHERE cache_key = 'all_publications'
                AND updated_at > NOW() - INTERVAL 48 HOUR
            LIMIT 1""",
            ()
        )
        if row:
            try:
                return json.loads(row['json_data'])
            except Exception as e:
                print(f"❌ Gagal parse cache publikasi: {e}")
                return None
        return None

    def set_publications_cache(self, pubs_list: list) -> bool:
        """Simpan daftar publikasi ke cache (key 'all_publications')."""
        json_str = json.dumps(pubs_list, ensure_ascii=False)
        return self.execute_query(
            """INSERT INTO bps_cache_pubs (cache_key, json_data, updated_at)
            VALUES ('all_publications', %s, NOW())
            ON DUPLICATE KEY UPDATE
                json_data = VALUES(json_data),
                updated_at = NOW()""",
            (json_str,)
        )

    def get_or_refresh_publications(self, api_fetcher):
        """
        Ambil data publikasi dengan cache 48 jam.
        - Jika cache fresh → return cache
        - Jika tidak → panggil api_fetcher, simpan ke cache & tabel publications, return hasil
        """
        cached = self.get_publications_cache()
        if cached is not None:
            print("✅ Publikasi diambil dari cache (fresh)")
            return cached

        print("🔄 Cache publikasi tidak ada/expired, mengambil dari API...")
        try:
            fresh_data = api_fetcher()
            if fresh_data:
                self.set_publications_cache(fresh_data)
                # Sinkronkan ke tabel publications untuk keperluan admin
                self.replace_all_publications(fresh_data)
                print(f"💾 Publikasi disimpan ke cache (total {len(fresh_data)} item)")
                return fresh_data
            else:
                print("❌ API fetcher publikasi gagal")
                return None
        except Exception as e:
            print(f"❌ Error refresh publikasi: {e}")
            return None