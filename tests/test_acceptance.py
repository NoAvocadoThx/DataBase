"""端到端验收：按 docs/superpowers/specs 中的 7 条标准。"""
import io, os, re, sys

import openpyxl, pytest

ROOT = os.path.dirname(os.path.dirname(__file__))
sys.path.insert(0, ROOT)


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    from app import models
    eng = models.make_engine(str(tmp_path_factory.mktemp("db") / "acc.db"))
    models.engine = eng
    models.SessionLocal.configure(bind=eng)
    models.init_db(eng)
    from fastapi.testclient import TestClient
    from app.main import app
    from app import auth
    with models.SessionLocal() as s:
        auth.ensure_admin(s)
    return TestClient(app, follow_redirects=False)


def login(c, u, p):
    c.cookies.clear()
    r = c.post("/login", data={"username": u, "password": p})
    assert r.status_code == 303 and r.headers["location"] == "/"


def page(c, url):
    r = c.get(url, follow_redirects=True)
    assert r.status_code == 200, (url, r.status_code)
    return r.text


def test_1_login_required(client):
    r = client.get("/")
    assert r.status_code == 303 and r.headers["location"].startswith("/login")
    r = client.post("/login", data={"username": "admin", "password": "wrong"})
    assert "%E9%94%99%E8%AF%AF" in r.headers["location"]  # "错误"
    login(client, "admin", "admin123")
    assert "专家列表" in page(client, "/")


def test_3_import_and_duplicates(client):
    login(client, "admin", "admin123")
    with open(os.path.join(ROOT, "sample", "专家导入模板示例.xlsx"), "rb") as f:
        r = client.post("/import", files={"file": ("e.xlsx", f)})
    assert "%E6%96%B0%E5%A2%9E%205" in r.headers["location"]  # 新增 5
    html = page(client, "/duplicates")
    assert html.count("同一人，保留 A") == 1 and "北京大学肿瘤医院" in html and "上海交通大学医学院" in html
    # 标记为不同人（两个张伟确实是两人）
    did = re.search(r'/duplicates/(\d+)/distinct', html).group(1)
    client.post(f"/duplicates/{did}/distinct")
    assert "没有待处理的疑似重复" in page(client, "/duplicates")
    # 再手工新增一个 北大 张伟 的重复 → 合并
    r = client.post("/expert/save", data={"name": "张伟", "org": "北京大学肿瘤医院-乳腺中心", "field": "新增方向", "tags": "新标签"})
    html = page(client, "/duplicates")
    assert html.count("同一人，保留") == 4  # 与两个已有张伟各一对
    m = re.search(r'action="/duplicates/(\d+)/merge"><input type="hidden" name="keep" value="(\d+)"', html)
    r = client.post(f"/duplicates/{m.group(1)}/merge", data={"keep": m.group(2)})
    assert "%E5%B7%B2%E5%90%88%E5%B9%B6" in r.headers["location"]  # 已合并
    assert "没有待处理的疑似重复" in page(client, "/duplicates")
    d = page(client, "/?tag=新标签")
    assert d.count("<td><a href=\"/expert/") == 1 and "乳腺中心" not in d  # 标签并入保留方，被合并方已删除


def test_2_intern_masking_and_permissions(client):
    login(client, "admin", "admin123")
    client.post("/users", data={"username": "tom", "password": "tom123456", "role": "intern"})
    login(client, "tom", "tom123456")
    html = page(client, "/")
    assert "138****11" in html and "13800001111" not in html
    eid = re.search(r'/expert/(\d+)"', html).group(1)
    d = page(client, f"/expert/{eid}")
    assert "内部备注" not in d and "编辑" not in d
    assert client.get("/import").status_code == 403
    assert client.get("/documents").status_code == 403
    assert client.get("/export").status_code == 403
    login(client, "admin", "admin123")
    assert "13800001111" in page(client, "/")


def test_4_document_upload_review_approve(client, tmp_path):
    import fitz
    pdf = tmp_path / "agenda.pdf"
    doc = fitz.open()
    pg = doc.new_page()
    text = ("2025 创新药临床开发峰会 议程\n"
            "09:00 开幕致辞  张伟 北京大学肿瘤医院 主任医师\n"
            "09:30 ADC 药物临床进展  李娜，中国药科大学 教授 ln@cpu.edu.cn\n"
            "10:30 报告人：赵敏 复旦大学附属中山医院 副主任医师 13512345678\n"
            "主持人：王强（恒瑞医药）研发副总裁\n")
    pg.insert_text((50, 72), text, fontname="china-s", fontsize=11)
    doc.save(str(pdf))
    login(client, "admin", "admin123")
    with open(pdf, "rb") as f:
        r = client.post("/documents", files={"file": ("议程.pdf", f, "application/pdf")})
    loc = r.headers["location"]
    assert re.match(r"/documents/\d+", loc) and "%E5%80%99%E9%80%89" in loc  # 候选
    did = re.search(r"/documents/(\d+)", loc).group(1)
    html = page(client, f"/documents/{did}")
    for n in ("张伟", "李娜", "赵敏", "王强"):
        assert f'value="{n}"' in html
    assert 'value="ln@cpu.edu.cn"' in html and 'value="13512345678"' in html
    assert "库中已有同名" in html  # 张伟 / 李娜 已在库
    count = int(re.search(r'name="count" value="(\d+)"', html).group(1))
    form = {"count": str(count), "meeting": "2025 创新药临床开发峰会", "year": "2025"}
    names = re.findall(r'name="name_(\d+)" value="([^"]*)"', html)
    for i, n in names:
        form[f"accept_{i}"] = "on"
        for k in ("name", "org", "title", "field", "email", "phone", "topic", "source_text"):
            form[f"{k}_{i}"] = re.search(rf'name="{k}_{i}" value="([^"]*)"', html).group(1)
        if n == "赵敏":
            form[f"tags_{i}"] = "肿瘤, 临床研究"
            form[f"topic_{i}"] = "ADC 真实世界研究"
    r = client.post(f"/documents/{did}/approve", data=form)
    assert "%E5%B7%B2%E5%85%A5%E5%BA%93%204" in r.headers["location"]  # 已入库 4
    html = page(client, "/?q=赵敏")
    eid = re.search(r'/expert/(\d+)"', html).group(1)
    d = page(client, f"/expert/{eid}")
    assert "复旦大学附属中山医院" in d and "议程.pdf" in d and "原文：" in d
    assert "2025 创新药临床开发峰会" in d and "ADC 真实世界研究" in d
    assert "已入库" in page(client, "/documents")


def test_5_natural_language_search(client):
    login(client, "tom", "tom123456")
    html = page(client, "/ask?q=找做ADC临床研究、参加过我们会议的肿瘤专家")
    rows = re.findall(r'<td>(\d)</td><td><a href="/expert/\d+">([^<]+)</a>', html)
    assert rows and rows[0][1] in ("张伟", "赵敏")
    assert "参加过" in html and "标签“ADC”" in html
    assert "没有匹配" in page(client, "/ask?q=量子计算")


def test_6_export(client):
    login(client, "admin", "admin123")
    r = client.get("/export")
    assert r.status_code == 200
    wb = openpyxl.load_workbook(io.BytesIO(r.content))
    rows = list(wb.active.iter_rows(values_only=True))
    assert rows[0][0] == "姓名" and len(rows) >= 7


def test_7_edit_and_delete(client):
    login(client, "admin", "admin123")
    html = page(client, "/?q=王强")
    eid = re.search(r'/expert/(\d+)"', html).group(1)
    client.post("/expert/save", data={"eid": eid, "name": "王强", "org": "恒瑞医药", "title": "首席科学家", "tags": "工业界"})
    assert "首席科学家" in page(client, f"/expert/{eid}")
    client.post(f"/expert/{eid}/delete")
    assert "暂无数据" in page(client, "/?q=王强")


def test_8_history_and_trash(client):
    login(client, "admin", "admin123")
    client.post("/expert/save", data={"name": "测试专家", "org": "测试医院", "title": "教授", "tags": "A"})
    html = page(client, "/?q=测试专家")
    eid = re.search(r'/expert/(\d+)"', html).group(1)
    client.post("/expert/save", data={"eid": eid, "name": "测试专家", "org": "测试医院", "title": "主任医师", "tags": "A, B"})
    r = client.post("/expert/save", data={"eid": eid, "name": "测试专家", "org": "测试医院", "title": "主任医师", "tags": "A, B"})
    assert "%E6%B2%A1%E6%9C%89%E6%94%B9%E5%8A%A8" in r.headers["location"]  # 没有改动 → 不记录
    client.post(f"/expert/{eid}/meeting", data={"meeting": "测试会", "year": "2024"})
    d = page(client, f"/expert/{eid}")
    assert "修改历史" in d and "新建" in d and "添加合作记录" in d
    assert "line-through\">教授</span> → <span style=\"color:#166534\">主任医师" in d
    assert "line-through\">A</span> → <span style=\"color:#166534\">A, B" in d
    # 全局历史
    h = page(client, "/history")
    assert "测试专家" in h and "主任医师" in h
    # 删除 → 回收站 → 列表不见 → 恢复
    r = client.post(f"/expert/{eid}/delete")
    assert "%E5%9B%9E%E6%94%B6%E7%AB%99" in r.headers["location"]  # 回收站
    assert "暂无数据" in page(client, "/?q=测试专家")
    assert "没有匹配" in page(client, "/ask?q=测试专家")
    t = page(client, "/trash")
    assert "测试专家" in t and "恢复" in t
    assert "该专家已于" in page(client, f"/expert/{eid}")
    client.post(f"/expert/{eid}/restore")
    assert "测试专家" in page(client, "/?q=测试专家") and "测试专家" not in page(client, "/trash")
    d = page(client, f"/expert/{eid}")
    assert "从回收站恢复" in d and d.count("<span class=\"tag\"") >= 6  # 多条历史
    # 彻底删除必须先在回收站
    client.post(f"/expert/{eid}/purge")
    assert "测试专家" in page(client, "/?q=测试专家")
    client.post(f"/expert/{eid}/delete"); client.post(f"/expert/{eid}/purge")
    assert "暂无数据" in page(client, "/?q=测试专家") and "测试专家" not in page(client, "/trash")
    assert "彻底删除" in page(client, "/history")  # 历史保留
    # 实习生看不到历史
    login(client, "admin", "admin123")
    client.post("/users", data={"username": "tom2", "password": "tom123456", "role": "intern"})
    login(client, "tom2", "tom123456")
    assert client.get("/history").status_code == 403 and client.get("/trash").status_code == 403


def test_9_list_filters_pagination_history_filters(client):
    login(client, "admin", "admin123")
    # 造 120 位专家，验证分页
    import openpyxl, io as _io
    wb = openpyxl.Workbook(); ws = wb.active
    ws.append(["姓名", "单位", "职务", "研究方向", "手机", "邮箱", "微信", "简介", "标签", "备注"])
    for i in range(120):
        ws.append([f"压测{i:03d}", "压测医院" if i % 2 else "压测大学", "教授" if i % 3 else "研究员",
                   "ADC" if i % 4 else "疫苗", "", "", "", "", "压测,偶数" if i % 2 == 0 else "压测", ""])
    b = _io.BytesIO(); wb.save(b)
    client.post("/import", files={"file": ("p.xlsx", b.getvalue())})
    h = page(client, "/?q=压测")
    assert "符合条件 120 位" in h and "第 1/3 页" in h and h.count("<td><a href=\"/expert/") == 50
    h = page(client, "/?q=压测&page=3")
    assert h.count("<td><a href=\"/expert/") == 20
    h = page(client, "/?q=压测&org=大学&title=教授&field=ADC&tag=压测,偶数&meeting=no&sort=name")
    n = int(re.search(r"符合条件 (\d+) 位", h).group(1))
    assert 0 < n < 60 and "压测大学" in h and "压测医院" not in h
    assert "符合条件 0 位" in page(client, "/?q=压测 不存在的词")
    # 多关键词 AND
    assert "符合条件 60 位" in page(client, "/?q=压测 医院")
    # 操作历史筛选
    h = page(client, "/history?action=import&name=压测001")
    assert "符合条件 1 条" in h and "Excel导入" in h
    assert "符合条件 0 条" in page(client, "/history?actor=nobody")
    h = page(client, "/history?date_from=2000-01-01&date_to=2000-01-02")
    assert "符合条件 0 条" in h
    h = page(client, "/history?action=import")
    assert "第 1/" in h  # 分页出现


def test_10_column_sort(client):
    login(client, "admin", "admin123")
    def names(url):
        return re.findall(r'<td><a href="/expert/\d+">([^<]+)</a>', page(client, url))
    asc = names("/?q=压测&sort=name&dir=asc"); desc = names("/?q=压测&sort=name&dir=desc")
    assert asc == sorted(asc) and desc == sorted(desc, reverse=True) and asc[0] != desc[0]
    h = page(client, "/?q=压测&sort=org&dir=asc")
    assert "单位 ▲" in h and 'sort=org&dir=desc' in h  # 再点反向
    rows = re.findall(r'<td>(\d+) 次</td>', page(client, "/?sort=meetings&dir=desc"))
    assert rows == sorted(rows, key=int, reverse=True) and int(rows[0]) >= 1
    rows = re.findall(r'<td>(\d+) 次</td>', page(client, "/?sort=meetings&dir=asc"))
    assert rows[0] == "0"
    tags_first = re.findall(r'<td>(?:<a class="tag"[^>]*>([^<]*)</a>)', page(client, "/?sort=tags&dir=asc&meeting=yes"))
    assert tags_first == sorted(tags_first)


def test_11_account_and_reset_password(client):
    login(client, "admin", "admin123")
    assert "修改我的密码" not in page(client, "/")
    assert 'href="/account"' in page(client, "/")
    client.post("/users", data={"username": "lily", "password": "lily1234", "role": "planner"})
    login(client, "lily", "lily1234")
    a = page(client, "/account")
    assert "我的账户" in a and "lily" in a and "策划" in a
    r = client.post("/password", data={"old": "wrong", "new": "abcdef1", "new2": "abcdef1"})
    assert "%E5%8E%9F%E5%AF%86%E7%A0%81%E9%94%99%E8%AF%AF" in r.headers["location"]
    r = client.post("/password", data={"old": "lily1234", "new": "abcdef1", "new2": "abcdef2"})
    assert "%E4%B8%8D%E4%B8%80%E8%87%B4" in r.headers["location"]  # 不一致
    r = client.post("/password", data={"old": "lily1234", "new": "abcdef1", "new2": "abcdef1"})
    assert "%E5%B7%B2%E4%BF%AE%E6%94%B9" in r.headers["location"]
    login(client, "lily", "abcdef1")
    assert client.get("/users").status_code == 403  # 策划不能进用户管理
    # 管理员重置
    login(client, "admin", "admin123")
    uid = re.search(r'/users/(\d+)/reset', page(client, "/users").split("lily")[1]).group(1)
    client.post(f"/users/{uid}/reset", data={"password": "reset123"})
    login(client, "lily", "reset123")
