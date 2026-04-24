"""
security_headers.py — Security Headers Middleware untuk halaman Admin SISKA
Menambahkan X-Frame-Options, CSP, dll. hanya pada route admin & login.
"""
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.types import ASGIApp

# Path prefix yang memerlukan header keamanan
_PROTECTED_PREFIXES = ("/admin", "/login", "/logout")

# Content Security Policy untuk halaman admin
# - Bootstrap & ikon dari CDN jsdelivr diizinkan
# - Inline script diizinkan (dibutuhkan oleh template Jinja2)
# - frame-ancestors 'none' menggantikan X-Frame-Options (modern browsers)
_CSP = (
    "default-src 'self'; "
    "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
    "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
    "font-src 'self' https://cdn.jsdelivr.net data:; "
    "img-src 'self' data:; "
    "connect-src 'self'; "
    "object-src 'none'; "
    "base-uri 'self'; "
    "frame-ancestors 'none';"
)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """
    Middleware yang menambahkan security headers pada respons halaman admin.
    Tidak mempengaruhi endpoint /webhook.
    """

    def __init__(self, app: ASGIApp):
        super().__init__(app)

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)

        # Hanya tambahkan header pada route yang dilindungi
        path = request.url.path
        if not any(path.startswith(p) for p in _PROTECTED_PREFIXES):
            return response

        content_type = response.headers.get("content-type", "")

        # ── Header berlaku untuk semua respons admin ──────────────────────
        response.headers["X-Frame-Options"]        = "DENY"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-XSS-Protection"]       = "1; mode=block"
        response.headers["Referrer-Policy"]        = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"]     = (
            "geolocation=(), microphone=(), camera=()"
        )

        # ── CSP hanya untuk respons HTML ──────────────────────────────────
        if "text/html" in content_type:
            response.headers["Content-Security-Policy"] = _CSP

        return response