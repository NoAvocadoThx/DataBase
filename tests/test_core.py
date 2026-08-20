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


def test_merge(s):
    importer.import_excel(s, xlsx([
        ["张伟", "北大", "教授", "", "", "", "", "", "肿瘤", ""],
        ["张伟", "", "", "ADC", "13800001111", "", "", "", "ADC", ""]]), "a.xlsx")
    a, b = s.query(Expert).order_by(Expert.id).all()
    s.add(Participation(expert_id=b.id, meeting="2024大会")); s.commit()
    keep = importer.merge_experts(s, a, b)
    assert s.query(Expert).count() == 1
    assert keep.org == "北大" and keep.field == "ADC" and keep.phone == "13800001111"
    assert {t.name for t in keep.tags} == {"肿瘤", "ADC"}
    assert [m.meeting for m in keep.meetings] == ["2024大会"]
    assert s.query(DuplicateCandidate).first().status == "merged"


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
