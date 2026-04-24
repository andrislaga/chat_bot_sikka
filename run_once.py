# run_once.py
from app.services.auth import AuthHandler
from app.services.database_service import DatabaseService

db = DatabaseService()
auth = AuthHandler()

hashed = auth.get_password_hash("admin123") # Ganti password sesukamu
db.execute_query("INSERT INTO admin_users (username, password_hash) VALUES (%s, %s)", ("admin", hashed))
print("Admin berhasil dibuat!")