import io, openpyxl
from app import importer, extract, search, auth
from app.models import Expert, DuplicateCandidate, Participation


def xlsx(rows):
    wb = openpyxl.Workbook(); ws = wb.active
    ws.append(importer.EXCEL_COLS)
    for r in rows: ws.append(r)
    b = io.BytesIO(); wb.save(b); return b.getvalue()


def test_import_dedup_and_duplicate_candidates(s):
    data = xlsx([
        ["张伟", "北大", "教授", "ADC", "13800001111", "", "", "", "肿瘤,ADC", ""],
        ["张伟", "交大", "副教授", "CAR-T", "", "", "", "", "肿瘤", ""],
        ["李娜", "药大", "", "", "", "", "", "", "", ""],
    ])
    r = importer.import_excel(s, data, "a.xlsx")
    assert r == {"created": 3, "updated": 0, "pending_dup": 1}
    r = importer.import_excel(s, data, "a.xlsx")  # 重复导入 → 全部更新
    assert r["created"] == 0 and r["updated"] == 3 and s.query(Expert).count() == 3
    from app import history
    logs = history.recent(s)
    assert len(logs) == 3 and all(c.action == "import" for c in logs)  # 无变化的更新不记录


def test_history_diff(s):
    from app import history
    importer.import_excel(s, xlsx([["张伟", "北大", "教授", "", "", "", "", "", "肿瘤", ""]]), "a.xlsx")
    importer.import_excel(s, xlsx([["张伟", "北大", "主任医师", "ADC", "", "", "", "", "肿瘤,ADC", ""]]), "b.xlsx")
    c = history.recent(s)[0]
    assert c.action == "import" and c.diff["title"] == ["教授", "主任医师"] and c.diff["tags"] == ["肿瘤", "ADC, 肿瘤"]
    assert "name" not in c.diff


def test_merge(s):
    importer.import_excel(s, xlsx([
        ["张伟", "北大", "教授", "", "", "", "", "", "肿瘤", ""],
        ["张伟", "", "", "ADC", "13800001111", "", "", "", "ADC", ""]]), "a.xlsx")
    a, b = s.query(Expert).order_by(Expert.id).all()
    s.add(Participation(expert_id=b.id, meeting="2024大会")); s.commit()
    keep = importer.merge_experts(s, a, b, actor="tester")
    from app.models import live
    from app import history
    assert live(s.query(Expert)).count() == 1 and s.query(Expert).count() == 2  # 被合并方进回收站
    assert b.deleted_at is not None and b.deleted_by == "tester"
    acts = [c.action for c in history.for_expert(s, keep.id)]
    assert "merge" in acts and "import" in acts
    importer.restore(s, b, "tester"); s.commit()
    assert live(s.query(Expert)).count() == 2 and s.query(DuplicateCandidate).filter_by(status="pending").count() == 1
    importer.purge(s, b, "tester"); s.commit()
    assert s.query(Expert).count() == 1 and history.for_expert(s, b.id)[0].action == "purge"
    assert keep.org == "北大" and keep.field == "ADC" and keep.phone == "13800001111"
    assert {t.name for t in keep.tags} == {"肿瘤", "ADC"}
    assert [m.meeting for m in keep.meetings] == ["2024大会"]


def test_export_roundtrip(s):
    importer.import_excel(s, xlsx([["王强", "恒瑞", "", "", "", "", "", "", "小分子", ""]]), "x")
    wb = openpyxl.load_workbook(io.BytesIO(importer.export_excel(s)))
    rows = list(wb.active.iter_rows(values_only=True))
    assert rows[0][0] == "姓名" and rows[1][0] == "王强" and rows[1][8] == "小分子"


def test_mask():
    assert importer.mask("13800001111") == "138****11"
    assert importer.mask("abc") == "****"
    assert importer.mask("") == ""


def test_password():
    h = auth.hash_password("admin123")
    assert auth.verify_password("admin123", h) and not auth.verify_password("x", h)


def test_redact():
    t = extract.redact("张伟 13800001111 zw@pku.edu.cn 微信: zw_doc88")
    assert "13800001111" not in t and "zw@pku" not in t and "zw_doc88" not in t


def test_rule_extract():
    text = """09:00 开幕致辞  张伟 北京大学肿瘤医院 主任医师
09:30 ADC药物临床进展  李娜，中国药科大学 教授 ln@cpu.edu.cn
主持人：王强（恒瑞医药）研发副总裁"""
    c = extract.rule_extract(text)
    by = {x["name"]: x for x in c}
    assert set(by) == {"张伟", "李娜", "王强"}
    assert by["张伟"]["org"] == "北京大学肿瘤医院" and by["张伟"]["title"] == "主任医师"
    assert by["李娜"]["email"] == "ln@cpu.edu.cn"
    assert by["王强"]["source_text"].startswith("主持人")
    assert extract.rule_extract("赵敏 复旦大学附属中山医院 副主任医师")[0]["title"] == "副主任医师"


def test_search_ranking(s):
    importer.import_excel(s, xlsx([
        ["张伟", "北大", "", "ADC药物临床研究", "", "", "", "", "肿瘤,ADC", ""],
        ["陈静", "药审中心", "", "审评", "", "", "", "", "ADC", ""],
        ["李娜", "药大", "", "纳米制剂", "", "", "", "", "药剂学", ""]]), "x")
    parsed = search.parse_query("找做ADC临床研究的肿瘤专家", search.all_tag_names(s))
    res = search.search(s, parsed)
    assert [e.name for e, _, _ in res] == ["张伟", "陈静"]
    assert any("ADC" in r for r in res[0][2])


def test_search_need_meeting(s):
    importer.import_excel(s, xlsx([["张伟", "北大", "", "ADC", "", "", "", "", "ADC", ""],
                                  ["陈静", "药审", "", "ADC", "", "", "", "", "ADC", ""]]), "x")
    zw = s.query(Expert).filter_by(name="张伟").one()
    s.add(Participation(expert_id=zw.id, meeting="2024")); s.commit()
    res = search.search(s, search.parse_query("参加过我们会议的ADC专家", ["ADC"]))
    assert res[0][0].name == "张伟" and "会议" in " ".join(res[0][2])
    importer.import_excel(s, xlsx([["无关", "某院", "", "骨科", "", "", "", "", "", ""]]), "x")
    wg = s.query(Expert).filter_by(name="无关").one()
    s.add(Participation(expert_id=wg.id, meeting="2023")); s.commit()
    res = search.search(s, search.parse_query("参加过我们会议的ADC专家", ["ADC"]))
    assert [e.name for e, _, _ in res] == ["张伟", "陈静"]  # 仅参会不命中


def test_migrate_adds_missing_columns(tmp_path):
    import sqlite3
    from app.models import make_engine, init_db, Expert
    from sqlalchemy.orm import sessionmaker
    db = tmp_path / "old.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE expert (id INTEGER PRIMARY KEY, name VARCHAR(64) NOT NULL, org VARCHAR(128))")
    con.execute("INSERT INTO expert (name, org) VALUES ('老数据', '旧单位')")
    con.commit(); con.close()
    eng = make_engine(str(db)); init_db(eng)
    s = sessionmaker(bind=eng)()
    e = s.query(Expert).one()
    assert e.name == "老数据" and e.source_text is None


def test_search_prefilter_matches_full_scan(s):
    """SQL 粗排 + 精算 的结果，必须与"不截断全量精算"一致（防止提速把正确性弄丢）。"""
    from app import search
    rows = [[f"专家{i:03d}", "某院" if i % 2 else "某大学", "教授", "ADC药物" if i % 3 else "疫苗",
             "", "", "", f"从事{'ADC' if i % 3 else '疫苗'}研究", "肿瘤,ADC" if i % 4 else "疫苗", ""]
            for i in range(300)]
    importer.import_excel(s, xlsx(rows), "x.xlsx")
    for i, e in enumerate(s.query(Expert).limit(120)):
        if i % 5 == 0:
            s.add(Participation(expert_id=e.id, meeting="2025大会", year=2025, topic="ADC进展"))
    s.commit()
    for q in ["ADC 肿瘤", "参加过我们会议的ADC专家", "疫苗"]:
        parsed = search.parse_query(q, search.all_tag_names(s))
        fast = search.search(s, parsed)
        full = []
        for e in search.candidates(s, parsed, prefilter=10_000):
            pts, rs = search.score(e, parsed)
            if rs and pts > 0:
                full.append((e, pts, rs))
        full.sort(key=lambda x: (-x[1], x[0].name))
        assert [x[0].id for x in fast] == [x[0].id for x in full[:50]], q
        assert [x[1] for x in fast] == [x[1] for x in full[:50]], q


def test_search_prefilter_caps_work(s):
    """粗排上限生效：不管库多大，进入 Python 精算的条数有上界。"""
    from app import search
    importer.import_excel(s, xlsx([[f"甲{i:03d}", "某院", "", "ADC", "", "", "", "", "ADC", ""]
                                   for i in range(200)]), "x.xlsx")
    parsed = search.parse_query("ADC", search.all_tag_names(s))
    assert len(search.candidates(s, parsed, prefilter=30)) == 30
    assert len(search.search(s, parsed)) == 50  # 返回上限仍是 50
