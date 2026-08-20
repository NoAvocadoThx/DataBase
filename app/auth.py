"""密码哈希 + Cookie 会话 + 角色检查。"""
import hashlib, hmac, os, secrets, time
from typing import Optional

from fastapi import HTTPException, Request
from itsdangerous import BadSignature, URLSafeTimedSerializer
from sqlalchemy.orm import Session

from .models import User, SENSITIVE_ROLES

def _secret() -> str:
    """会话签名密钥。生产必须通过环境变量提供；缺失时只在测试/开发下随机生成，
    绝不使用写死的默认值——写死的密钥等于任何人都能伪造管理员 Cookie。"""
    key = os.getenv("SECRET_KEY", "").strip()
    if key:
        if len(key) < 32:
            raise RuntimeError("SECRET_KEY 太短（至少 32 字符）。用 `openssl rand -hex 32` 生成。")
        return key
    if os.getenv("ALLOW_INSECURE_SECRET") == "1" or os.getenv("PYTEST_CURRENT_TEST"):
        return secrets.token_hex(32)  # 每次启动随机：开发方便，且无法被离线伪造
    raise RuntimeError(
        "未设置 SECRET_KEY。生产部署必须提供：\n"
        "  在 .env 中写入 SECRET_KEY=$(openssl rand -hex 32)\n"
        "  本地开发可临时设置 ALLOW_INSECURE_SECRET=1（每次启动会话失效）")


SECRET = _secret()
serializer = URLSafeTimedSerializer(SECRET, salt="session")
COOKIE = "session"
MAX_AGE = 60 * 60 * 12  # 12 小时


def secure_cookie() -> bool:
    """Cookie 是否只在 HTTPS 下发送。默认开启；仅内网/演示的纯 HTTP 环境可设 SECURE_COOKIE=0。"""
    return os.getenv("SECURE_COOKIE", "1") != "0"


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
    return serializer.dumps({"uid": user.id, "role": user.role, "name": user.username,
                             "pw": user.password_hash[:16]})  # 改密码后旧会话立即失效


def set_session_cookie(resp, user: User):
    resp.set_cookie(COOKIE, make_session_cookie(user), httponly=True, max_age=MAX_AGE,
                    secure=secure_cookie(), samesite="lax", path="/")


def safe_next(target: str) -> str:
    """只允许跳回站内路径，挡住 ?next=https://钓鱼站 的开放重定向。"""
    if not target or not target.startswith("/") or target.startswith("//") or "\\" in target:
        return "/"
    return target


def read_session(request: Request) -> Optional[dict]:
    raw = request.cookies.get(COOKIE)
    if not raw:
        return None
    try:
        return serializer.loads(raw, max_age=MAX_AGE)
    except BadSignature:
        return None


MAX_TRIES, LOCK_SECONDS = 8, 300
_fails: dict[str, list[float]] = {}


def login_blocked(key: str) -> int:
    """返回剩余锁定秒数（0 表示可以尝试）。防止对已知用户名 admin 暴力破解。"""
    now = time.time()
    tries = [t for t in _fails.get(key, []) if now - t < LOCK_SECONDS]
    _fails[key] = tries
    if len(tries) >= MAX_TRIES:
        return int(LOCK_SECONDS - (now - tries[0])) + 1
    return 0


def record_fail(key: str):
    _fails.setdefault(key, []).append(time.time())


def clear_fails(key: str):
    _fails.pop(key, None)


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
