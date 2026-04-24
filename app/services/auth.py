from passlib.context import CryptContext
from fastapi import Request, HTTPException
from fastapi.responses import RedirectResponse

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

class AuthHandler:
    @staticmethod
    def verify_password(plain_password, hashed_password):
        return pwd_context.verify(plain_password, hashed_password)

    @staticmethod
    def get_password_hash(password):
        return pwd_context.hash(password)

    @staticmethod
    def check_login(request: Request):
        # Mengecek apakah ada cookie 'admin_session'
        session = request.cookies.get("admin_session")
        if not session:
            return False
        return True