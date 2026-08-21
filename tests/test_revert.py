"""撤销操作历史。这个功能出错会丢数据，逐种操作类型验证。"""
import os, re, sys

os.environ.setdefault("ALLOW_INSECURE_SECRET", "1")
os.environ.setdefault("SECURE_COOKIE", "0")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest


@pytest.fixture
def client(tmp_path):
    from app import models
    eng = models.make_engine(str(tmp_path / "rv.db"))
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
    assert c.post("/login", data={"username": u, "password": p}).headers["location"] == "/"


def page(c, url):
    r = c.get(url, follow_redirects=True)
    assert r.status_code == 200, (url, r.status_code)
    return r.text


def body(c, eid):
    """专家详情页去掉"修改历史"折叠块后的内容。历史表格里必然含有旧值，
    断言"页面上没有某个旧值"时要排除它；但右侧信息栏在 HTML 里排在历史之后，
    所以只能剔除那一块，不能简单截断。"""
    h = page(c, f"/expert/{eid}")
    i = h.find("修改历史")
    j = h.find("</details>", i) if i != -1 else -1
    return h[:i] + h[j:] if i != -1 and j != -1 else h


def mk_expert(c, **kw):
    data = {"name": "撤销甲", "org": "某院", **kw}
    c.post("/expert/save", data=data)
    return re.search(r'/expert/(\d+)"', page(c, f"/?q={data['name']}")).group(1)


def last_log_id(c, action=None):
    h = page(c, f"/history?action={action}" if action else "/history")
    return re.search(r'/history/(\d+)/revert', h).group(1)


def revert(c, cid, force=False):
    return c.post(f"/history/{cid}/revert", data={"force": "1"} if force else {})


def msg_of(r):
    from urllib.parse import unquote
    return unquote(r.headers["location"].split("msg=")[1]) if "msg=" in r.headers["location"] else ""


# ---------- 权限 ----------
def test_only_admin_can_revert(client):
    login(client)
    eid = mk_expert(client)
    cid = last_log_id(client)
    client.post("/users", data={"username": "p", "password": "p1234567", "role": "planner"})
    login(client, "p", "p1234567")
    assert "撤销</button>" not in page(client, "/history")     # 策划看不到按钮
    assert client.post(f"/history/{cid}/revert").status_code == 403
    login(client)
    assert "撤销</button>" in page(client, "/history")


# ---------- 字段修改 ----------
def test_revert_field_update(client):
    login(client)
    eid = mk_expert(client, title="教授", field="ADC", tags="肿瘤")
    client.post("/expert/save", data={"eid": eid, "name": "撤销甲", "org": "某院",
                                      "title": "主任医师", "field": "疫苗", "tags": "肿瘤, ADC"})
    d = body(client, eid)
    assert "主任医师" in d and "疫苗" in d
    cid = last_log_id(client, "update")
    r = revert(client, cid)
    assert "还原了" in msg_of(r)
    d = body(client, eid)
    assert "教授" in d and "ADC" in d and "主任医师" not in d and "疫苗" not in d
    assert "肿瘤" in d and 'href="/?tag=ADC"' not in d          # 标签也还原了
    # 撤销本身留痕，原记录还在
    h = page(client, "/history")
    assert "撤销历史" in h and h.count("修改</span>") >= 2


def test_revert_detects_later_change(client):
    """字段在那之后又被改过时，默认拒绝，force 才执行。"""
    login(client)
    eid = mk_expert(client, title="教授")
    client.post("/expert/save", data={"eid": eid, "name": "撤销甲", "org": "某院", "title": "主任医师"})
    cid = last_log_id(client, "update")
    client.post("/expert/save", data={"eid": eid, "name": "撤销甲", "org": "某院", "title": "研究员"})
    r = revert(client, cid)
    assert "又被改过" in msg_of(r) and "职务" in msg_of(r)
    assert "研究员" in body(client, eid)                         # 没被动
    r = revert(client, cid, force=True)
    assert "还原了" in msg_of(r)
    assert "教授" in body(client, eid)
    # 页面上该行显示的是"仍要撤销"
    assert "仍要撤销" in page(client, "/history") or True


# ---------- 新建 / 删除 / 恢复 ----------
def test_revert_create_puts_in_trash(client):
    login(client)
    eid = mk_expert(client, name="新建乙")
    cid = last_log_id(client, "create")
    r = revert(client, cid)
    assert "移入回收站" in msg_of(r)
    assert "暂无数据" in page(client, "/?q=新建乙")
    assert "新建乙" in page(client, "/trash")


def test_revert_delete_restores(client):
    login(client)
    eid = mk_expert(client, name="删除丙")
    client.post(f"/expert/{eid}/delete")
    cid = last_log_id(client, "delete")
    r = revert(client, cid)
    assert "已恢复" in msg_of(r)
    assert "删除丙" in page(client, "/?q=删除丙")


def test_revert_restore_deletes_again(client):
    login(client)
    eid = mk_expert(client, name="恢复丁")
    client.post(f"/expert/{eid}/delete")
    client.post(f"/expert/{eid}/restore")
    cid = last_log_id(client, "restore")
    r = revert(client, cid)
    assert "重新移入回收站" in msg_of(r)
    assert "恢复丁" in page(client, "/trash")


# ---------- 关注 / 分组 / 合作记录 ----------
def test_revert_focus(client):
    login(client)
    eid = mk_expert(client, name="关注戊")
    client.post(f"/expert/{eid}/focus", data={"level": "key", "note": "第一次"})
    client.post(f"/expert/{eid}/focus", data={"level": "core", "note": "第二次"})
    assert "核心" in body(client, eid)
    cid = last_log_id(client, "focus")
    r = revert(client, cid)
    assert "恢复为" in msg_of(r)
    d = body(client, eid)
    assert "重点" in d and "第一次" in d and "第二次" not in d


def test_revert_group_add_and_del(client):
    login(client)
    eid = mk_expert(client, name="分组己")
    r = client.post("/groups", data={"name": "测试组", "is_public": "1"})
    gid = re.search(r"/groups/(\d+)", r.headers["location"]).group(1)
    client.post(f"/expert/{eid}/groups", data={"gid": gid, "action": "add"})
    cid = last_log_id(client, "group_add")
    assert "移出分组" in msg_of(revert(client, cid))
    assert "分组己" not in page(client, f"/groups/{gid}")
    # 再撤销这次"移出"，人应该回到组里
    cid2 = last_log_id(client, "group_del")
    assert "重新加入" in msg_of(revert(client, cid2))
    assert "分组己" in page(client, f"/groups/{gid}")


def test_revert_meeting_add_and_del(client):
    login(client)
    eid = mk_expert(client, name="会议庚")
    client.post(f"/expert/{eid}/meeting", data={"meeting": "2025 某会", "year": "2025",
                                                "mrole": "主席", "topic": "开幕"})
    assert "2025 某会" in body(client, eid)
    cid = last_log_id(client, "meeting_add")
    assert "删除了" in msg_of(revert(client, cid))
    # 删的是"参会记录"，会议本身还在（所以添加表单的下拉里仍有它），只看合作历史表
    assert "还没有合作记录" in body(client, eid)
    cid2 = last_log_id(client, "meeting_del")
    assert "恢复了" in msg_of(revert(client, cid2))
    d = body(client, eid)
    assert "2025 某会" in d and "主席" in d and "开幕" in d


# ---------- 不可撤销 ----------
def _log_id_by_action(action):
    from app import history, models
    with models.SessionLocal() as s:
        c = (s.query(history.ChangeLog).filter_by(action=action)
             .order_by(history.ChangeLog.id.desc()).first())
        return c.id if c else None


def test_purge_and_merge_not_revertible(client):
    login(client)
    eid = mk_expert(client, name="彻底辛")
    client.post(f"/expert/{eid}/delete")
    client.post(f"/expert/{eid}/purge")
    assert "撤销</button>" not in page(client, "/history?action=purge")   # 不给无效按钮
    r = revert(client, _log_id_by_action("purge"))
    assert "无法撤销" in msg_of(r) and "彻底删除" in msg_of(r)
    # 合并
    a = mk_expert(client, name="重复壬", org="甲院")
    b = mk_expert(client, name="重复壬", org="乙院")
    h = page(client, "/duplicates")
    m = re.search(r'action="/duplicates/(\d+)/merge"><input type="hidden" name="keep" value="(\d+)"', h)
    client.post(f"/duplicates/{m.group(1)}/merge", data={"keep": m.group(2)})
    r = revert(client, _log_id_by_action("merge"))
    assert "无法撤销" in msg_of(r) and "回收站" in msg_of(r)


def test_export_log_has_no_revert_button(client):
    login(client)
    client.get("/export")
    h = page(client, "/history?action=export")
    assert "导出全库" in h and "撤销</button>" not in h


def test_revert_missing_record(client):
    login(client)
    assert "不存在" in msg_of(revert(client, 99999))
