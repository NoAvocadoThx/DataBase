"""同写意专家智库 MVP — 单文件后端
运行: uvicorn app.main:app --reload
"""
import io, json, os, re
from datetime import datetime
from typing import Optional

import httpx, openpyxl
from fastapi import FastAPI, Request, UploadFile, File, Form, Depends
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import (create_engine, Column, Integer, String, Text, DateTime,
                        ForeignKey, Table, or_)
from sqlalchemy.orm import declarative_base, relationship, sessionmaker, Session

BASE_DIR = os.path.dirname(__file__)
engine = create_engine(f"sqlite:///{os.path.join(BASE_DIR, '..', 'experts.db')}",
                       connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(bind=engine)
Base = declarative_base()

# ---------- 数据模型 ----------
expert_tag = Table("expert_tag", Base.metadata,
                   Column("expert_id", ForeignKey("expert.id"), primary_key=True),
                   Column("tag_id", ForeignKey("tag.id"), primary_key=True))


class Expert(Base):
    __tablename__ = "expert"
    id = Column(Integer, primary_key=True)
    name = Column(String(64), nullable=False, index=True)
    org = Column(String(128), index=True)          # 当前主要单位
    title = Column(String(128))                    # 职务/职称
    field = Column(String(256))                    # 研究方向
    phone = Column(String(32))                     # 敏感
    email = Column(String(128))                    # 敏感
    wechat = Column(String(64))                    # 敏感
    bio = Column(Text)                             # 简介
    note = Column(Text)                            # 内部备注(敏感)
    source = Column(String(256))                   # 来源(文件名/人工)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)
    tags = relationship("Tag", secondary=expert_tag, back_populates="experts")
    meetings = relationship("Participation", back_populates="expert",
                            cascade="all, delete-orphan")


class Tag(Base):
    __tablename__ = "tag"
    id = Column(Integer, primary_key=True)
    name = Column(String(64), unique=True, nullable=False)
    category = Column(String(32), default="专业能力")  # 基础属性/专业能力/合作历史/行业影响力
    experts = relationship("Expert", secondary=expert_tag, back_populates="tags")


class Participation(Base):
    """专家-会议合作历史。会议先用字符串，后续再拆独立会议表。"""
    __tablename__ = "participation"
    id = Column(Integer, primary_key=True)
    expert_id = Column(ForeignKey("expert.id"))
    meeting = Column(String(128))
    year = Column(Integer)
    role = Column(String(64))       # 主席/报告人/嘉宾
    topic = Column(String(256))     # 报告主题
    expert = relationship("Expert", back_populates="meetings")


Base.metadata.create_all(engine)


def db():
    s = SessionLocal()
    try:
        yield s
    finally:
        s.close()


# ---------- 应用 ----------
app = FastAPI(title="专家智库 MVP")
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))

# 极简角色: 通过 ?role=admin|planner|intern 模拟。正式版换成登录。
SENSITIVE_ROLES = {"admin", "planner"}


def mask(v: Optional[str]) -> str:
    if not v:
        return ""
    return v[:3] + "****" + v[-2:] if len(v) > 5 else "****"


def view(expert: Expert, role: str) -> dict:
    d = {c.name: getattr(expert, c.name) for c in Expert.__table__.columns}
    d["tags"] = expert.tags
    d["meetings"] = expert.meetings
    if role not in SENSITIVE_ROLES:
        for k in ("phone", "email", "wechat"):
            d[k] = mask(d[k])
        d["note"] = "（无权限查看）"
    return d


# ---------- Excel 导入 ----------
EXCEL_COLS = ["姓名", "单位", "职务", "研究方向", "手机", "邮箱", "微信", "简介", "标签", "备注"]


def get_or_create_tag(s: Session, name: str) -> Tag:
    name = name.strip()
    t = s.query(Tag).filter_by(name=name).first()
    if not t:
        t = Tag(name=name)
        s.add(t)
        s.flush()
    return t


def import_excel(s: Session, data: bytes, filename: str) -> dict:
    wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True)
    ws = wb.active
    rows = list(ws.iter_rows(values_only=True))
    header = [str(h).strip() if h else "" for h in rows[0]]
    idx = {h: i for i, h in enumerate(header)}
    if "姓名" not in idx:
        return {"error": "缺少“姓名”列，模板列名: " + " / ".join(EXCEL_COLS)}
    created = updated = dup = 0

    def cell(r, col):
        i = idx.get(col)
        v = r[i] if i is not None and i < len(r) else None
        return str(v).strip() if v is not None else ""

    for r in rows[1:]:
        name, org = cell(r, "姓名"), cell(r, "单位")
        if not name:
            continue
        # 去重规则: 姓名+单位 命中 → 更新; 仅同名不同单位 → 新建并标记疑似重复
        e = s.query(Expert).filter_by(name=name, org=org).first()
        if e:
            updated += 1
        else:
            if s.query(Expert).filter_by(name=name).first():
                dup += 1
            e = Expert(name=name, org=org)
            s.add(e)
            created += 1
        e.title, e.field = cell(r, "职务"), cell(r, "研究方向")
        e.phone, e.email, e.wechat = cell(r, "手机"), cell(r, "邮箱"), cell(r, "微信")
        e.bio, e.note, e.source = cell(r, "简介"), cell(r, "备注"), filename
        tags = [t for t in re.split(r"[,，;；/、\s]+", cell(r, "标签")) if t]
        e.tags = [get_or_create_tag(s, t) for t in tags]
    s.commit()
    return {"created": created, "updated": updated, "possible_dup": dup}


# ---------- 自然语言检索 ----------
LLM_URL = os.getenv("LLM_URL", "https://api.deepseek.com/chat/completions")
LLM_KEY = os.getenv("LLM_API_KEY", "")
LLM_MODEL = os.getenv("LLM_MODEL", "deepseek-chat")


def parse_query_with_llm(q: str, all_tags: list[str]) -> dict:
    """把自然语言拆成结构化条件。无 key 时降级为关键词切分。"""
    if not LLM_KEY:
        words = [w for w in re.split(r"[\s，,。的和与及]+", q) if len(w) >= 2]
        return {"keywords": words, "tags": [t for t in all_tags if t in q],
                "org": "", "explain": "未配置大模型，使用关键词匹配"}
    prompt = f"""你是专家库检索助手。把用户问题拆成 JSON，不要输出其他内容:
{{"keywords": [研究方向/主题关键词], "tags": [只能从给定标签中选], "org": "单位名或空串", "explain": "一句话说明你理解的需求"}}
可选标签: {all_tags}
用户问题: {q}"""
    try:
        r = httpx.post(LLM_URL, timeout=30,
                       headers={"Authorization": f"Bearer {LLM_KEY}"},
                       json={"model": LLM_MODEL, "temperature": 0,
                             "messages": [{"role": "user", "content": prompt}]})
        txt = r.json()["choices"][0]["message"]["content"]
        return json.loads(re.search(r"\{.*\}", txt, re.S).group())
    except Exception as ex:  # 模型出错不阻塞检索
        return {"keywords": [q], "tags": [], "org": "", "explain": f"模型解析失败({ex})，按原文匹配"}


def search(s: Session, keywords: list[str], tags: list[str], org: str) -> list[tuple[Expert, list[str]]]:
    """命中条目越多排越前，返回 (专家, 命中理由列表)。"""
    results = []
    for e in s.query(Expert).all():
        reasons = []
        hay = " ".join(filter(None, [e.field, e.bio, e.title] +
                               [m.topic or "" for m in e.meetings]))
        for k in keywords:
            if k and k in hay:
                reasons.append(f"资料中提及“{k}”")
        enames = {t.name for t in e.tags}
        for t in tags:
            if t in enames:
                reasons.append(f"标签匹配“{t}”")
        if org and e.org and org in e.org:
            reasons.append(f"单位匹配“{org}”")
        if reasons:
            results.append((e, reasons))
    results.sort(key=lambda x: -len(x[1]))
    return results


# ---------- 路由 ----------
@app.get("/", response_class=HTMLResponse)
def index(request: Request, s: Session = Depends(db), role: str = "intern",
          q: str = "", tag: str = "", org: str = ""):
    query = s.query(Expert)
    if q:
        like = f"%{q}%"
        query = query.filter(or_(Expert.name.like(like), Expert.org.like(like),
                                 Expert.field.like(like), Expert.bio.like(like)))
    if org:
        query = query.filter(Expert.org.like(f"%{org}%"))
    if tag:
        query = query.filter(Expert.tags.any(Tag.name == tag))
    experts = query.order_by(Expert.updated_at.desc()).all()
    return templates.TemplateResponse("index.html", dict(
        request=request, role=role, q=q, tag=tag, org=org,
        experts=[view(e, role) for e in experts],
        tags=s.query(Tag).order_by(Tag.name).all(), total=s.query(Expert).count()))


@app.get("/expert/{eid}", response_class=HTMLResponse)
def detail(eid: int, request: Request, s: Session = Depends(db), role: str = "intern"):
    e = s.get(Expert, eid)
    return templates.TemplateResponse("detail.html", dict(
        request=request, role=role, e=view(e, role),
        all_tags=s.query(Tag).order_by(Tag.name).all()))


@app.post("/expert/{eid}/tags")
def update_tags(eid: int, s: Session = Depends(db), role: str = Form("intern"),
                tags: str = Form("")):
    if role in SENSITIVE_ROLES:
        e = s.get(Expert, eid)
        names = [t for t in re.split(r"[,，;；/、\s]+", tags) if t]
        e.tags = [get_or_create_tag(s, t) for t in names]
        s.commit()
    return RedirectResponse(f"/expert/{eid}?role={role}", status_code=303)


@app.post("/expert/{eid}/meeting")
def add_meeting(eid: int, s: Session = Depends(db), role: str = Form("intern"),
                meeting: str = Form(""), year: str = Form(""), mrole: str = Form(""),
                topic: str = Form("")):
    if role in SENSITIVE_ROLES and meeting:
        s.add(Participation(expert_id=eid, meeting=meeting, role=mrole, topic=topic,
                            year=int(year) if year.isdigit() else None))
        s.commit()
    return RedirectResponse(f"/expert/{eid}?role={role}", status_code=303)


@app.get("/import", response_class=HTMLResponse)
def import_page(request: Request, role: str = "intern", result: str = ""):
    return templates.TemplateResponse("import.html", dict(
        request=request, role=role, cols=EXCEL_COLS, result=result))


@app.post("/import")
async def import_post(s: Session = Depends(db), role: str = Form("intern"),
                      file: UploadFile = File(...)):
    if role != "admin":
        return RedirectResponse(f"/import?role={role}&result=仅管理员可导入", status_code=303)
    res = import_excel(s, await file.read(), file.filename)
    msg = res.get("error") or f"新增 {res['created']}，更新 {res['updated']}，疑似同名重复 {res['possible_dup']}"
    return RedirectResponse(f"/import?role={role}&result={msg}", status_code=303)


@app.get("/ask", response_class=HTMLResponse)
def ask(request: Request, s: Session = Depends(db), role: str = "intern", q: str = ""):
    parsed, results = None, []
    if q:
        parsed = parse_query_with_llm(q, [t.name for t in s.query(Tag).all()])
        results = [(view(e, role), r) for e, r in
                   search(s, parsed.get("keywords", []), parsed.get("tags", []), parsed.get("org", ""))]
    return templates.TemplateResponse("ask.html", dict(
        request=request, role=role, q=q, parsed=parsed, results=results,
        llm_on=bool(LLM_KEY)))
