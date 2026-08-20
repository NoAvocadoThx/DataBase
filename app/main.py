"""同写意专家智库 MVP — 路由层。运行: python -m uvicorn app.main:app --reload"""
import json, os, re, shutil
from datetime import datetime
from urllib.parse import quote

from fastapi import Depends, FastAPI, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy import or_
from sqlalchemy.orm import Session

from . import auth, extract, importer, search
from .auth import ADMIN, ANY, EDITOR
from .models import (Document, DuplicateCandidate, Expert, Participation, ROLES, SessionLocal,
                     Tag, UPLOAD_DIR, User, init_db)

init_db()
with SessionLocal() as _s:
    auth.ensure_admin(_s)

app = FastAPI(title="专家智库 MVP")
templates = Jinja2Templates(directory=os.path.join(os.path.dirname(__file__), "templates"))


def db():
    s = SessionLocal()
    try:
        yield s
    finally:
        s.close()


@app.middleware("http")
async def session_mw(request: Request, call_next):
    request.state.session = auth.read_session(request)
    resp = await call_next(request)
    if resp.status_code == 401:
        return RedirectResponse(f"/login?next={quote(str(request.url.path))}", status_code=303)
    return resp


def render(name: str, request: Request, **ctx):
    ctx.update(request=request, sess=request.state.session,
               can_sensitive=auth.can_see_sensitive((request.state.session or {}).get("role", "")))
    return templates.TemplateResponse(name, ctx)


def back(url: str, msg: str = "") -> RedirectResponse:
    sep = "&" if "?" in url else "?"
    return RedirectResponse(url + (f"{sep}msg={quote(msg)}" if msg else ""), status_code=303)


def view(e: Expert, role: str) -> dict:
    d = {c.name: getattr(e, c.name) for c in Expert.__table__.columns}
    d["tags"], d["meetings"] = e.tags, e.meetings
    if not auth.can_see_sensitive(role):
        for k in ("phone", "email", "wechat"):
            d[k] = importer.mask(d[k])
        d["note"] = ""
    return d


# ---------- 登录 ----------
@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request, next: str = "/", msg: str = ""):
    return render("login.html", request, next=next, msg=msg)


@app.post("/login")
def login(request: Request, s: Session = Depends(db), username: str = Form(...),
          password: str = Form(...), next: str = Form("/")):
    u = s.query(User).filter_by(username=username).first()
    if not u or not auth.verify_password(password, u.password_hash):
        return back("/login", "用户名或密码错误")
    resp = RedirectResponse(next or "/", status_code=303)
    resp.set_cookie(auth.COOKIE, auth.make_session_cookie(u), httponly=True, max_age=auth.MAX_AGE)
    return resp


@app.get("/logout")
def logout():
    resp = RedirectResponse("/login", status_code=303)
    resp.delete_cookie(auth.COOKIE)
    return resp


@app.post("/password")
def change_password(request: Request, s: Session = Depends(db), sess=Depends(ANY),
                    old: str = Form(...), new: str = Form(...)):
    u = s.get(User, sess["uid"])
    if not auth.verify_password(old, u.password_hash):
        return back("/", "原密码错误")
    u.password_hash = auth.hash_password(new)
    s.commit()
    return back("/", "密码已修改")


# ---------- 专家 ----------
@app.get("/", response_class=HTMLResponse)
def index(request: Request, s: Session = Depends(db), sess=Depends(ANY),
          q: str = "", tag: str = "", org: str = "", msg: str = ""):
    query = s.query(Expert)
    if q:
        like = f"%{q}%"
        query = query.filter(or_(Expert.name.like(like), Expert.org.like(like), Expert.title.like(like),
                                 Expert.field.like(like), Expert.bio.like(like)))
    if org:
        query = query.filter(Expert.org.like(f"%{org}%"))
    if tag:
        query = query.filter(Expert.tags.any(Tag.name == tag))
    experts = query.order_by(Expert.updated_at.desc()).all()
    pend = s.query(DuplicateCandidate).filter_by(status="pending").count()
    docs = s.query(Document).filter_by(status="pending").count()
    return render("index.html", request, q=q, tag=tag, org=org, msg=msg,
                  experts=[view(e, sess["role"]) for e in experts],
                  tags=s.query(Tag).order_by(Tag.name).all(), total=s.query(Expert).count(),
                  pending_dup=pend, pending_docs=docs)


@app.get("/expert/new", response_class=HTMLResponse)
def expert_new(request: Request, s: Session = Depends(db), sess=Depends(EDITOR)):
    return render("expert_form.html", request, e=None, all_tags=s.query(Tag).order_by(Tag.name).all())


@app.get("/expert/{eid}", response_class=HTMLResponse)
def detail(eid: int, request: Request, s: Session = Depends(db), sess=Depends(ANY), msg: str = ""):
    e = s.get(Expert, eid)
    if not e:
        return back("/", "专家不存在")
    return render("detail.html", request, e=view(e, sess["role"]), msg=msg,
                  all_tags=s.query(Tag).order_by(Tag.name).all())


@app.get("/expert/{eid}/edit", response_class=HTMLResponse)
def expert_edit(eid: int, request: Request, s: Session = Depends(db), sess=Depends(EDITOR)):
    e = s.get(Expert, eid)
    return render("expert_form.html", request, e=e, all_tags=s.query(Tag).order_by(Tag.name).all())


@app.post("/expert/save")
def expert_save(request: Request, s: Session = Depends(db), sess=Depends(EDITOR),
                eid: str = Form(""), name: str = Form(...), org: str = Form(""), title: str = Form(""),
                field: str = Form(""), phone: str = Form(""), email: str = Form(""), wechat: str = Form(""),
                bio: str = Form(""), note: str = Form(""), tags: str = Form("")):
    if eid:
        e = s.get(Expert, int(eid))
    else:
        e = Expert(name=name.strip(), org=org.strip(), source=f"人工录入({sess['name']})")
        s.add(e)
        s.flush()
    for k, v in dict(name=name, org=org, title=title, field=field, phone=phone, email=email,
                     wechat=wechat, bio=bio, note=note).items():
        setattr(e, k, v.strip())
    e.tags = [importer.get_or_create_tag(s, t) for t in importer.split_tags(tags)]
    if not eid:
        importer.register_duplicates(s, e)
    s.commit()
    return back(f"/expert/{e.id}", "已保存")


@app.post("/expert/{eid}/delete")
def expert_delete(eid: int, s: Session = Depends(db), sess=Depends(ADMIN)):
    e = s.get(Expert, eid)
    if e:
        s.query(DuplicateCandidate).filter(
            (DuplicateCandidate.expert_a_id == eid) | (DuplicateCandidate.expert_b_id == eid)).delete()
        s.delete(e)
        s.commit()
    return back("/", "已删除")


@app.post("/expert/{eid}/meeting")
def add_meeting(eid: int, s: Session = Depends(db), sess=Depends(EDITOR), meeting: str = Form(...),
                year: str = Form(""), mrole: str = Form(""), topic: str = Form("")):
    s.add(Participation(expert_id=eid, meeting=meeting.strip(), role=mrole.strip(), topic=topic.strip(),
                        year=int(year) if year.strip().isdigit() else None))
    s.commit()
    return back(f"/expert/{eid}")


@app.post("/meeting/{mid}/delete")
def del_meeting(mid: int, s: Session = Depends(db), sess=Depends(EDITOR)):
    m = s.get(Participation, mid)
    eid = m.expert_id
    s.delete(m)
    s.commit()
    return back(f"/expert/{eid}")


# ---------- 导入 / 导出 ----------
@app.get("/import", response_class=HTMLResponse)
def import_page(request: Request, sess=Depends(ADMIN), msg: str = ""):
    return render("import.html", request, cols=importer.EXCEL_COLS, msg=msg)


@app.post("/import")
async def import_post(s: Session = Depends(db), sess=Depends(ADMIN), file: UploadFile = File(...)):
    res = importer.import_excel(s, await file.read(), file.filename)
    msg = res.get("error") or f"新增 {res['created']}，更新 {res['updated']}，待处理疑似重复 {res['pending_dup']}"
    return back("/import", msg)


@app.get("/export")
def export(s: Session = Depends(db), sess=Depends(ADMIN)):
    data = importer.export_excel(s)
    fn = quote(f"专家库导出_{datetime.now():%Y%m%d}.xlsx")
    return Response(data, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": f"attachment; filename*=UTF-8''{fn}"})


# ---------- 疑似重复 ----------
@app.get("/duplicates", response_class=HTMLResponse)
def duplicates(request: Request, s: Session = Depends(db), sess=Depends(ADMIN), msg: str = ""):
    items = s.query(DuplicateCandidate).filter_by(status="pending").all()
    return render("duplicates.html", request, items=items, msg=msg)


@app.post("/duplicates/{did}/merge")
def dup_merge(did: int, s: Session = Depends(db), sess=Depends(ADMIN), keep: int = Form(...)):
    d = s.get(DuplicateCandidate, did)
    a, b = d.expert_a, d.expert_b
    keep_e, drop_e = (a, b) if keep == a.id else (b, a)
    importer.merge_experts(s, keep_e, drop_e)
    return back("/duplicates", f"已合并到 {keep_e.name}（{keep_e.org}）")


@app.post("/duplicates/{did}/distinct")
def dup_distinct(did: int, s: Session = Depends(db), sess=Depends(ADMIN)):
    s.get(DuplicateCandidate, did).status = "distinct"
    s.commit()
    return back("/duplicates", "已标记为不同人")


# ---------- 资料上传与审核 ----------
@app.get("/documents", response_class=HTMLResponse)
def documents(request: Request, s: Session = Depends(db), sess=Depends(EDITOR), msg: str = ""):
    docs = s.query(Document).order_by(Document.created_at.desc()).all()
    return render("documents.html", request, docs=docs, msg=msg, llm_on=extract.llm_enabled())


@app.post("/documents")
async def upload_doc(s: Session = Depends(db), sess=Depends(EDITOR), file: UploadFile = File(...)):
    ext = os.path.splitext(file.filename)[1].lower()
    if ext not in (".pdf", ".docx", ".pptx", ".txt"):
        return back("/documents", "仅支持 PDF / Word(.docx) / PPT(.pptx) / TXT")
    safe = re.sub(r"[^\w.一-龥-]", "_", file.filename)
    path = os.path.join(UPLOAD_DIR, f"{datetime.now():%Y%m%d%H%M%S}_{safe}")
    with open(path, "wb") as f:
        shutil.copyfileobj(file.file, f)
    try:
        text = extract.file_to_text(path)
    except Exception as ex:
        return back("/documents", f"文件解析失败: {type(ex).__name__}")
    cands, how = extract.extract_experts(text)
    doc = Document(filename=file.filename, path=path, text=text, uploaded_by=sess["name"],
                   extracted_json=json.dumps(cands, ensure_ascii=False))
    s.add(doc)
    s.commit()
    return back(f"/documents/{doc.id}", f"已提取 {len(cands)} 位候选（方式: {how}），请审核")


@app.get("/documents/{did}", response_class=HTMLResponse)
def review_doc(did: int, request: Request, s: Session = Depends(db), sess=Depends(EDITOR), msg: str = ""):
    doc = s.get(Document, did)
    cands = json.loads(doc.extracted_json or "[]")
    for c in cands:  # 标出库里已有的同名专家，便于判断是更新还是新建
        c["existing"] = [f"{e.name}（{e.org}）" for e in s.query(Expert).filter_by(name=c["name"])]
    return render("review.html", request, doc=doc, cands=cands, msg=msg, fields=extract.FIELDS)


@app.post("/documents/{did}/approve")
async def approve_doc(did: int, request: Request, s: Session = Depends(db), sess=Depends(EDITOR)):
    form = await request.form()
    doc = s.get(Document, did)
    meeting = form.get("meeting", "").strip()
    year = form.get("year", "").strip()
    n = 0
    for i in range(int(form.get("count", 0))):
        if not form.get(f"accept_{i}"):
            continue
        d = {k: form.get(f"{k}_{i}", "") for k in extract.FIELDS}
        if not d["name"].strip():
            continue
        tags = importer.split_tags(form.get(f"tags_{i}", ""))
        d["tags"] = tags or None
        e, _ = importer.upsert_expert(s, d, doc.filename)
        if meeting:
            s.add(Participation(expert_id=e.id, meeting=meeting, topic=d.get("topic", "").strip(),
                                year=int(year) if year.isdigit() else None,
                                role=form.get(f"role_{i}", "").strip()))
        n += 1
    doc.status = "reviewed"
    s.commit()
    return back("/documents", f"已入库 {n} 位专家")


@app.post("/documents/{did}/delete")
def delete_doc(did: int, s: Session = Depends(db), sess=Depends(ADMIN)):
    doc = s.get(Document, did)
    if doc:
        try:
            os.remove(doc.path)
        except OSError:
            pass
        s.delete(doc)
        s.commit()
    return back("/documents", "已删除")


# ---------- 自然语言检索 ----------
@app.get("/ask", response_class=HTMLResponse)
def ask(request: Request, s: Session = Depends(db), sess=Depends(ANY), q: str = ""):
    parsed, results = None, []
    if q.strip():
        parsed = search.parse_query(q, search.all_tag_names(s))
        results = [(view(e, sess["role"]), pts, reasons) for e, pts, reasons in search.search(s, parsed)]
    return render("ask.html", request, q=q, parsed=parsed, results=results, llm_on=extract.llm_enabled())


# ---------- 用户管理 ----------
@app.get("/users", response_class=HTMLResponse)
def users(request: Request, s: Session = Depends(db), sess=Depends(ADMIN), msg: str = ""):
    return render("users.html", request, users=s.query(User).all(), roles=ROLES, msg=msg)


@app.post("/users")
def user_add(s: Session = Depends(db), sess=Depends(ADMIN), username: str = Form(...),
             password: str = Form(...), role: str = Form("intern"), display_name: str = Form("")):
    if s.query(User).filter_by(username=username).first():
        return back("/users", "用户名已存在")
    s.add(User(username=username, password_hash=auth.hash_password(password),
               role=role if role in ROLES else "intern", display_name=display_name))
    s.commit()
    return back("/users", "已创建")


@app.post("/users/{uid}/delete")
def user_del(uid: int, s: Session = Depends(db), sess=Depends(ADMIN)):
    if uid == sess["uid"]:
        return back("/users", "不能删除自己")
    u = s.get(User, uid)
    if u:
        s.delete(u)
        s.commit()
    return back("/users", "已删除")
