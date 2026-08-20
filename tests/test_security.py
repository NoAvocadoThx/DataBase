"""安全回归测试：每一条对应一次审计发现，防止改动时把防护改回去。"""
import importlib, os, re, sys

os.environ.setdefault("ALLOW_INSECURE_SECRET", "1")
os.environ.setdefault("SECURE_COOKIE", "0")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from itsdangerous import URLSafeTimedSerializer


@pytest.fixture
def client(tmp_path):
    from app import models
    eng = models.make_engine(str(tmp_path / "sec.db"))
    models.engine = eng
    models.SessionLocal.configure(bind=eng)
    models.init_db(eng)
    from fastapi.testclient import TestClient
    from app.main import app
    from app import auth
    auth._fails.clear()
    with models.SessionLocal() as s:
        auth.ensure_admin(s)
    return TestClient(app, follow_redirects=False)


def login(c, u="admin", p="admin123"):
    c.cookies.clear()
    r = c.post("/login", data={"username": u, "password": p})
    assert r.status_code == 303 and r.headers["location"] == "/", r.headers.get("location")


# ---------- 1. 会话密钥 ----------
def test_no_hardcoded_secret_in_source():
    """代码里不得出现可预测的默认密钥（公开代码 + 固定密钥 = 任何人可伪造管理员）。"""
    src = open(os.path.join(os.path.dirname(__file__), "..", "app", "auth.py"), encoding="utf8").read()
    assert "dev-secret-change-me" not in src
    assert 'os.getenv("SECRET_KEY") or ' not in src


def test_missing_secret_key_refuses_to_start(monkeypatch):
    """没有 SECRET_KEY 时必须启动失败，而不是退回到某个可预测的默认值。"""
    from app import auth
    monkeypatch.delenv("SECRET_KEY", raising=False)
    monkeypatch.delenv("ALLOW_INSECURE_SECRET", raising=False)
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    with pytest.raises(RuntimeError, match="SECRET_KEY"):
        auth._secret()


def test_short_secret_key_rejected(monkeypatch):
    from app import auth
    monkeypatch.setenv("SECRET_KEY", "tooshort")
    with pytest.raises(RuntimeError, match="太短"):
        auth._secret()


def test_dev_fallback_is_random_not_fixed(monkeypatch):
    """开发模式的兜底密钥必须每次随机，离线无法伪造。"""
    from app import auth
    monkeypatch.delenv("SECRET_KEY", raising=False)
    monkeypatch.setenv("ALLOW_INSECURE_SECRET", "1")
    assert auth._secret() != auth._secret() and len(auth._secret()) >= 32


def test_forged_cookie_with_old_default_secret_rejected(client):
    """PoC1：用曾经写死的密钥伪造管理员会话，必须被拒绝并跳转登录。"""
    client.cookies.clear()
    forged = URLSafeTimedSerializer("dev-secret-change-me", salt="session").dumps(
        {"uid": 1, "role": "admin", "name": "evil", "pw": "x" * 16})
    client.cookies.set("session", forged)
    r = client.get("/export")
    assert r.status_code == 303 and r.headers["location"].startswith("/login")
    r = client.get("/")
    assert r.status_code == 303 and r.headers["location"].startswith("/login")


def test_tampered_role_in_cookie_rejected(client):
    """把自己的会话改成 admin 也不行：签名校验 + 与数据库实时比对。"""
    login(client)
    client.post("/users", data={"username": "tom", "password": "tom123456", "role": "intern"})
    login(client, "tom", "tom123456")
    from app import auth
    payload = auth.serializer.loads(client.cookies["session"])
    payload["role"] = "admin"
    tampered = auth.serializer.dumps(payload)
    client.cookies.clear()
    client.cookies.set("session", tampered)
    r = client.get("/users")
    assert r.status_code == 303 and r.headers["location"].startswith("/login")


def test_role_change_invalidates_old_session(client):
    """管理员把某人降级为实习生后，对方手上的旧 Cookie 立即失效。"""
    login(client)
    client.post("/users", data={"username": "amy", "password": "amy123456", "role": "planner"})
    from app import models
    login(client, "amy", "amy123456")
    assert client.get("/documents").status_code == 200
    with models.SessionLocal() as s:
        u = s.query(models.User).filter_by(username="amy").one()
        u.role = "intern"
        s.commit()
    r = client.get("/documents")
    assert r.status_code == 303 and r.headers["location"].startswith("/login")


def test_password_change_invalidates_old_session(client):
    login(client)
    client.post("/users", data={"username": "bob", "password": "bob123456", "role": "planner"})
    login(client, "bob", "bob123456")
    old = client.cookies["session"]
    r = client.post("/password", data={"old": "bob123456", "new": "newpass1", "new2": "newpass1"})
    assert "%E5%B7%B2%E4%BF%AE%E6%94%B9" in r.headers["location"]
    client.cookies.clear()
    client.cookies.set("session", old)
    r = client.get("/")  # 旧 Cookie 作废
    assert r.status_code == 303 and r.headers["location"].startswith("/login")


# ---------- 2. Cookie 属性 ----------
def test_cookie_flags(client, monkeypatch):
    r = client.post("/login", data={"username": "admin", "password": "admin123"})
    raw = r.headers["set-cookie"]
    assert "HttpOnly" in raw and "SameSite=lax" in raw and "Path=/" in raw
    from app import auth
    monkeypatch.delenv("SECURE_COOKIE", raising=False)
    assert auth.secure_cookie() is True          # 默认开启（生产 HTTPS）
    monkeypatch.setenv("SECURE_COOKIE", "0")
    assert auth.secure_cookie() is False         # 仅演示环境显式关闭


# ---------- 3. 开放重定向 ----------
@pytest.mark.parametrize("evil", [
    "https://evil.example/phish", "//evil.example/phish", "http://evil.example",
    "\\\\evil.example", "javascript:alert(1)",
])
def test_open_redirect_blocked(client, evil):
    """PoC3：登录后不得跳转到站外，否则可做钓鱼跳板。"""
    client.cookies.clear()
    r = client.post("/login", data={"username": "admin", "password": "admin123", "next": evil})
    assert r.headers["location"] == "/", (evil, r.headers["location"])


def test_internal_next_still_works(client):
    client.cookies.clear()
    r = client.post("/login", data={"username": "admin", "password": "admin123", "next": "/history"})
    assert r.headers["location"] == "/history"


# ---------- 4. 敏感信息脱敏 ----------
def test_intern_cannot_see_contacts_in_source_text(client):
    """PoC2：实习生页面任何位置都不得出现完整手机号/邮箱，包括“录入时的原文”。"""
    login(client)
    from app import models
    with models.SessionLocal() as s:
        e = models.Expert(name="机密专家", org="某院", phone="13912345678", email="a@b.com",
                          wechat="wx_secret", note="内部备注：报价很高",
                          source_text="报告人：机密专家 某院 13912345678 a@b.com 微信 wx_secret")
        s.add(e)
        s.commit()
        eid = e.id
    client.post("/users", data={"username": "kid", "password": "kid123456", "role": "intern"})
    login(client, "kid", "kid123456")
    for url in (f"/expert/{eid}", "/", "/?q=机密", "/ask?q=机密专家"):
        h = client.get(url, follow_redirects=True).text
        for leak in ("13912345678", "a@b.com", "wx_secret", "内部备注：报价很高"):
            assert leak not in h, (url, leak)
    h = client.get(f"/expert/{eid}", follow_redirects=True).text
    assert "139****78" in h and "录入时的原文" not in h
    login(client)  # 管理员仍然看得到
    h = client.get(f"/expert/{eid}", follow_redirects=True).text
    assert "13912345678" in h and "录入时的原文" in h


def test_intern_blocked_from_admin_routes(client):
    login(client)
    client.post("/users", data={"username": "kid2", "password": "kid123456", "role": "intern"})
    login(client, "kid2", "kid123456")
    for url in ("/export", "/import", "/users", "/trash", "/duplicates", "/history", "/documents"):
        assert client.get(url).status_code == 403, url
    for url, data in [("/expert/save", {"name": "x"}), ("/users", {"username": "z", "password": "zzzzzz"})]:
        assert client.post(url, data=data).status_code == 403, url


# ---------- 5. 登录限速 ----------
def test_login_rate_limited(client):
    from app import auth
    auth._fails.clear()
    for _ in range(auth.MAX_TRIES):
        r = client.post("/login", data={"username": "admin", "password": "wrong"})
        assert "%E9%94%99%E8%AF%AF" in r.headers["location"]  # 错误
    r = client.post("/login", data={"username": "admin", "password": "wrong"})
    assert "%E6%AC%A1%E6%95%B0%E8%BF%87%E5%A4%9A" in r.headers["location"]  # 次数过多
    r = client.post("/login", data={"username": "admin", "password": "admin123"})
    assert r.headers["location"] != "/"  # 锁定期间正确密码也挡住
    auth._fails.clear()
    login(client)


# ---------- 6. 导出留痕 ----------
def test_export_is_audited(client):
    login(client)
    assert client.get("/export").status_code == 200
    h = client.get("/history", follow_redirects=True).text
    assert "导出全库" in h and "（全库）" in h


# ---------- 7. 大模型外发脱敏 ----------
def test_llm_payload_is_redacted():
    from app import extract
    raw = "张伟 北京大学 13800001111 zw@pku.edu.cn 微信: zw_doc88"
    out = extract.redact(raw)
    for leak in ("13800001111", "zw@pku.edu.cn", "zw_doc88"):
        assert leak not in out
    assert "张伟" in out and "北京大学" in out  # 非敏感内容保留，抽取仍可用


def test_deploy_config_has_no_weak_defaults():
    root = os.path.join(os.path.dirname(__file__), "..")
    compose = open(os.path.join(root, "docker-compose.yml"), encoding="utf8").read()
    assert "please-change-me" not in compose
    assert "SECRET_KEY: ${SECRET_KEY:?" in compose      # 未设置则拒绝启动
    assert '"127.0.0.1:8000:8000"' in compose            # 不直接暴露到公网


# ---------- 8. 页面可见范围（共享 vs 私有）----------
def test_shared_pages_scope(client):
    """操作历史/回收站/关注/会议是全团队共享；只有私有分组按人隔离。"""
    login(client)
    client.post("/users", data={"username": "p1", "password": "p1123456", "role": "planner"})
    client.post("/users", data={"username": "p2", "password": "p2123456", "role": "planner"})
    # p1 建专家并操作
    login(client, "p1", "p1123456")
    client.post("/expert/save", data={"name": "共享甲", "org": "某院"})
    import re as _re
    eid = _re.search(r'/expert/(\d+)"', client.get("/?q=共享甲", follow_redirects=True).text).group(1)
    client.post(f"/expert/{eid}/focus", data={"level": "core", "note": "p1 标的"})
    client.post("/groups", data={"name": "p1私有组", "is_public": ""})
    # p2 能看到 p1 的操作历史和关注分级，但看不到 p1 的私有组
    login(client, "p2", "p2123456")
    h = client.get("/history", follow_redirects=True).text
    assert "共享甲" in h and "p1" in h, "操作历史应对全团队可见"
    assert "共享甲" in client.get("/focus", follow_redirects=True).text
    assert "p1私有组" not in client.get("/groups", follow_redirects=True).text
    # 回收站只给管理员
    assert client.get("/trash").status_code == 403
    login(client)
    assert client.get("/trash").status_code == 200
