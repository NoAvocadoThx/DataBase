"""密码哈希 + Cookie 会话 + 角色检查。"""
import hashlib, hmac, os, secrets
from typing import Optional

from fastapi import HTTPException, Request
from itsdangerous import BadSignature, URLSafeTimedSerializer
from sqlalchemy.orm import Session

from .models import User, SENSITIVE_ROLES

SECRET = os.getenv("SECRET_KEY") or "dev-secret-change-me"
serializer = URLSafeTimedSerializer(SECRET, salt="session")
COOKIE = "session"
MAX_AGE = 60 * 60 * 12  # 12 小时


def hash_password(pw: str) -> str:
    salt = secrets.token_hex(16)
    h = hashlib.pbkdf2_hmac("sha256", pw.encode(), salt.encode(), 200_000).hex()
    return f"{salt}${h}"


def verify_password(pw: str, stored: str) -> bool:
    try:
        salt, h = stored.split("$", 1)
    except ValueError:
        return False
    calc = hashlib.pbkdf2_hmac("sha256", pw.encode(), salt.encode(), 200_000).hex()
    return hmac.compare_digest(calc, h)


def make_session_cookie(user: User) -> str:
    return serializer.dumps({"uid": user.id, "role": user.role, "name": user.username})


def read_session(request: Request) -> Optional[dict]:
    raw = request.cookies.get(COOKIE)
    if not raw:
        return None
    try:
        return serializer.loads(raw, max_age=MAX_AGE)
    except BadSignature:
        return None


def ensure_admin(s: Session):
    """首次启动建默认管理员。"""
    if not s.query(User).first():
        s.add(User(username="admin", password_hash=hash_password("admin123"),
                   role="admin", display_name="管理员"))
        s.commit()


def can_see_sensitive(role: str) -> bool:
    return role in SENSITIVE_ROLES


def require(role_ok: set[str]):
    """返回一个依赖: 未登录 → 401(由中间件转跳); 角色不符 → 403。"""
    def dep(request: Request):
        sess = request.state.session
        if not sess:
            raise HTTPException(401)
        if sess["role"] not in role_ok:
            raise HTTPException(403, "无权限")
        return sess
    return dep


ANY = require({"admin", "planner", "intern"})
EDITOR = require({"admin", "planner"})
ADMIN = require({"admin"})
