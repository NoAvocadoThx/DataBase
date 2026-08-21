"""同写意专家智库 MVP — 路由层。运行: python -m uvicorn app.main:app --reload"""
import json, os, re, shutil
from datetime import datetime
from urllib.parse import quote

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy import or_
from sqlalchemy.orm import Session

from . import auth, extract, history, importer, search
from .auth import ADMIN, ANY, EDITOR
from .models import (Document, DuplicateCandidate, Expert, ExpertGroup, FOCUS_LEVELS, FOCUS_ORDER,
                     MEETING_STATUS, Meeting, Participation, ROLES, SessionLocal, Tag, UPLOAD_DIR,
                     User, csort, init_db, live, visible_groups)

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
    sess = auth.read_session(request)
    if sess:  # 用户被删除、改角色或改密码后，旧 Cookie 立即失效
        with SessionLocal() as s:
            u = s.get(User, sess.get("uid"))
            if not u or u.role != sess.get("role") or u.password_hash[:16] != sess.get("pw"):
                sess = None
    request.state.session = sess
    resp = await call_next(request)
    if resp.status_code == 401:
        return RedirectResponse(f"/login?next={quote(auth.safe_next(str(request.url.path)))}", status_code=303)
    return resp


def sidebar_counts() -> dict:
    with SessionLocal() as s:
        return dict(pending_dup=s.query(DuplicateCandidate).filter_by(status="pending").count(),
                    trash=s.query(Expert).filter(Expert.deleted_at.isnot(None)).count(),
                    pending_docs=s.query(Document).filter_by(status="pending").count())


def render(name: str, request: Request, **ctx):
    sess = request.state.session
    if sess:
        for k, v in sidebar_counts().items():
            ctx.setdefault(k, v)
    ctx.update(request=request, sess=sess, can_sensitive=auth.can_see_sensitive((sess or {}).get("role", "")))
    return templates.TemplateResponse(name, ctx)


def client_ip(request: Request) -> str:
    """取真实来源 IP。经 Caddy 反代时用 X-Forwarded-For 的第一段。"""
    xff = request.headers.get("x-forwarded-for", "")
    if xff:
        return xff.split(",")[0].strip()
    return request.client.host if request.client else ""


def back(url: str, msg: str = "") -> RedirectResponse:
    sep = "&" if "?" in url else "?"
    return RedirectResponse(url + (f"{sep}msg={quote(msg)}" if msg else ""), status_code=303)


def view(e: Expert, role: str) -> dict:
    d = {c.name: getattr(e, c.name) for c in Expert.__table__.columns}
    d["tags"], d["meetings"], d["groups"] = e.tags, e.meetings, e.groups
    d["focus_label"] = e.focus_label
    if not auth.can_see_sensitive(role):
        for k in ("phone", "email", "wechat"):
            d[k] = importer.mask(d[k])
        d["note"] = ""
        d["source_text"] = ""  # 录入原文常含手机/邮箱，不能绕过脱敏
    return d


# ---------- 登录 ----------
@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request, next: str = "/", msg: str = ""):
    return render("login.html", request, next=next, msg=msg)


@app.post("/login")
def login(request: Request, s: Session = Depends(db), username: str = Form(...),
          password: str = Form(...), next: str = Form("/")):
    key = f"{request.client.host if request.client else '?'}|{username}"
    if (wait := auth.login_blocked(key)):
        return back("/login", f"尝试次数过多，请 {wait // 60 + 1} 分钟后再试")
    u = s.query(User).filter_by(username=username).first()
    if not u or not auth.verify_password(password, u.password_hash):
        auth.record_fail(key)
        return back("/login", "用户名或密码错误")
    auth.clear_fails(key)
    resp = RedirectResponse(auth.safe_next(next), status_code=303)
    auth.set_session_cookie(resp, u)
    return resp


@app.get("/logout")
def logout():
    resp = RedirectResponse("/login", status_code=303)
    resp.delete_cookie(auth.COOKIE, path="/")
    return resp


@app.get("/account", response_class=HTMLResponse)
def account(request: Request, s: Session = Depends(db), sess=Depends(ANY), msg: str = ""):
    return render("account.html", request, user=s.get(User, sess["uid"]), msg=msg)


@app.post("/password")
def change_password(request: Request, s: Session = Depends(db), sess=Depends(ANY),
                    old: str = Form(...), new: str = Form(...), new2: str = Form("")):
    u = s.get(User, sess["uid"])
    if not auth.verify_password(old, u.password_hash):
        return back("/account", "原密码错误")
    if new2 and new != new2:
        return back("/account", "两次输入的新密码不一致")
    if len(new) < 6:
        return back("/account", "新密码至少 6 位")
    u.password_hash = auth.hash_password(new)
    s.commit()
    resp = back("/account", "密码已修改")
    auth.set_session_cookie(resp, u)  # 换新会话，旧 Cookie 作废
    return resp


# ---------- 专家 ----------
PAGE_SIZE = 50


def sort_expr(s: Session, key: str):
    """列排序表达式。tags=首个标签名，meetings=合作次数。
    字符串列走 csort()：PG 默认 collation 排中文/英文大小写与 SQLite 不同，统一按码位排。"""
    from sqlalchemy import func, select
    from .models import expert_tag
    if key == "tags":
        return (select(func.min(csort(Tag.name, s))).select_from(expert_tag)
                .join(Tag, Tag.id == expert_tag.c.tag_id)
                .where(expert_tag.c.expert_id == Expert.id).scalar_subquery())
    if key == "meetings":
        return select(func.count(Participation.id)).where(Participation.expert_id == Expert.id).scalar_subquery()
    col = {"name": Expert.name, "org": Expert.org, "title": Expert.title, "field": Expert.field,
           "phone": Expert.phone, "created": Expert.created_at}.get(key, Expert.updated_at)
    return csort(col, s) if key in ("name", "org", "title", "field", "phone") else col


SORT_DEFAULT_DIR = {"updated": "desc", "created": "desc", "meetings": "desc"}


def paginate(query, page: int, size: int = PAGE_SIZE):
    total = query.order_by(None).count()
    pages = max(1, -(-total // size))
    page = min(max(1, page), pages)
    return query.offset((page - 1) * size).limit(size).all(), total, page, pages


ORG_TYPES = {  # 单位类型分面：按单位名关键词归类
    "hospital": ("医院", ["医院", "医学中心", "诊所"]),
    "academy": ("高校 / 科研院所", ["大学", "学院", "研究所", "研究院", "科学院"]),
    "company": ("药企 / 企业", ["公司", "集团", "制药", "药业", "生物", "医药", "科技"]),
    "regulator": ("监管 / 协会", ["药监", "药审", "卫健", "协会", "学会", "中心"]),
}
COOP_BUCKETS = {"0": "未合作", "1-2": "低频 (1–2)", "3": "高频 (3+)"}


def org_type_cond(key: str):
    # ilike 而不是 like：PG 的 LIKE 对 ASCII 大小写敏感，SQLite 不敏感。统一用 ilike
    # 保证 "ADC"/"adc" 这类英文关键词两库行为一致（中文无大小写，不受影响）。
    words = ORG_TYPES[key][1]
    return or_(*[Expert.org.ilike(f"%{w}%") for w in words])


def coop_cond(key: str):
    from sqlalchemy import func, select
    cnt = select(func.count(Participation.id)).where(Participation.expert_id == Expert.id).scalar_subquery()
    return {"0": cnt == 0, "1-2": cnt.between(1, 2), "3": cnt >= 3}[key]


def apply_filters(s: Session, f: dict):
    query = live(s.query(Expert))
    for kw in f["q"].split():  # 多个关键词 = AND，每个词匹配任一字段（ilike：大小写不敏感，两库一致）
        like = f"%{kw}%"
        query = query.filter(or_(Expert.name.ilike(like), Expert.org.ilike(like), Expert.title.ilike(like),
                                 Expert.field.ilike(like), Expert.bio.ilike(like)))
    if f["org"]:
        query = query.filter(Expert.org.ilike(f"%{f['org']}%"))
    if f["title"]:
        query = query.filter(Expert.title.ilike(f"%{f['title']}%"))
    if f["field"]:
        query = query.filter(Expert.field.ilike(f"%{f['field']}%"))
    for tname in [x for x in importer.split_tags(f["tag"]) if x]:  # 多标签 = AND
        query = query.filter(Expert.tags.any(Tag.name == tname))
    if f["meeting"] == "yes":
        query = query.filter(Expert.meetings.any())
    elif f["meeting"] == "no":
        query = query.filter(~Expert.meetings.any())
    if f["org_type"] in ORG_TYPES:
        query = query.filter(org_type_cond(f["org_type"]))
    if f["coop"] in COOP_BUCKETS:
        query = query.filter(coop_cond(f["coop"]))
    if f.get("focus") in FOCUS_LEVELS:
        query = query.filter(Expert.focus_level == f["focus"])
    if f.get("group"):
        query = query.filter(Expert.groups.any(ExpertGroup.id == int(f["group"])))
    return query


def top_tag_names(s: Session, limit: int = 10) -> list[str]:
    """全库最常用的标签名。分面列表用它固定顺序——如果按"当前结果"排，
    点一个标签会把整列表重排，用户刚才在看的选项就找不到了。"""
    from sqlalchemy import func
    from .models import expert_tag
    return [n for (n, _) in (s.query(Tag.name, func.count(expert_tag.c.expert_id))
                             .join(expert_tag, Tag.id == expert_tag.c.tag_id)
                             .group_by(Tag.name)
                             .order_by(func.count(expert_tag.c.expert_id).desc(), csort(Tag.name, s))
                             .limit(limit))]


def facet_counts(s: Session, f: dict) -> dict:
    """分面计数：每个分面在"去掉自身条件"的结果集上统计，这样点选后其他选项仍可见。
    各分面的条目和顺序保持固定，只有数字随筛选变化。"""
    from sqlalchemy import func
    from .models import expert_tag
    base_no_org = apply_filters(s, {**f, "org_type": ""}).order_by(None)
    base_no_coop = apply_filters(s, {**f, "coop": ""}).order_by(None)
    base_no_focus = apply_filters(s, {**f, "focus": ""}).order_by(None)
    base_no_tag = apply_filters(s, {**f, "tag": ""}).order_by(None)
    org_types = [(k, label, base_no_org.filter(org_type_cond(k)).count()) for k, (label, _) in ORG_TYPES.items()]
    coop = [(k, label, base_no_coop.filter(coop_cond(k)).count()) for k, label in COOP_BUCKETS.items()]
    focus = [(k, FOCUS_LEVELS[k], base_no_focus.filter(Expert.focus_level == k).count()) for k in FOCUS_ORDER]

    names = top_tag_names(s)
    for picked in importer.split_tags(f.get("tag", "")):   # 选中的标签即使不在前十也要留在列表里
        if picked and picked not in names:
            names.append(picked)
    ids = base_no_tag.with_entities(Expert.id).subquery()
    counts = dict(s.query(Tag.name, func.count(expert_tag.c.expert_id))
                  .join(expert_tag, Tag.id == expert_tag.c.tag_id)
                  .filter(Tag.name.in_(names), expert_tag.c.expert_id.in_(s.query(ids.c.id)))
                  .group_by(Tag.name).all())
    top_tags = [(n, counts.get(n, 0)) for n in names]
    return dict(org_types=org_types, coop=coop, tags=top_tags, focus=focus)


@app.get("/", response_class=HTMLResponse)
def index(request: Request, s: Session = Depends(db), sess=Depends(ANY), q: str = "", tag: str = "",
          org: str = "", title: str = "", field: str = "", meeting: str = "", org_type: str = "", coop: str = "",
          focus: str = "", group: str = "", sort: str = "updated", dir: str = "", page: int = 1, msg: str = ""):
    f = dict(q=q.strip(), tag=tag.strip(), org=org.strip(), title=title.strip(), field=field.strip(),
             meeting=meeting, org_type=org_type, coop=coop, focus=focus,
             group=group if group.isdigit() else "")
    query = apply_filters(s, f)
    dir = dir if dir in ("asc", "desc") else SORT_DEFAULT_DIR.get(sort, "asc")
    expr = sort_expr(s, sort)
    query = query.order_by(expr.desc().nullslast() if dir == "desc" else expr.asc().nullsfirst(), Expert.id.desc())
    experts, found, page, pages = paginate(query, page)
    params = {k: v for k, v in {**f, "sort": sort, "dir": dir}.items() if v}
    filters = {k: v for k, v in params.items() if k not in ("sort", "dir")}
    gname = ""
    if f["group"]:
        g = s.get(ExpertGroup, int(f["group"]))
        gname = g.name if g else ""
    labels = dict(q="关键词", tag="标签", org="单位", title="职务", field="研究方向",
                  meeting={"yes": "有合作", "no": "无合作"}.get(meeting, meeting),
                  org_type=ORG_TYPES.get(org_type, ("", []))[0], coop=COOP_BUCKETS.get(coop, coop),
                  focus=FOCUS_LEVELS.get(focus, focus), group=gname)
    chips = [(k, labels[k], v if k in ("q", "tag", "org", "title", "field") else "") for k, v in filters.items()]
    return render("index.html", request, msg=msg, f={**f, "sort": sort, "dir": dir}, params=params, filters=filters,
                  chips=chips, facets=facet_counts(s, f), groups=visible_groups(s, sess).all(),
                  experts=[view(e, sess["role"]) for e in experts], found=found, page=page, pages=pages,
                  tags=s.query(Tag).order_by(csort(Tag.name, s)).all(), total=live(s.query(Expert)).count())


@app.get("/expert/new", response_class=HTMLResponse)
def expert_new(request: Request, s: Session = Depends(db), sess=Depends(EDITOR)):
    return render("expert_form.html", request, e=None, all_tags=s.query(Tag).order_by(csort(Tag.name, s)).all())


@app.get("/expert/{eid}", response_class=HTMLResponse)
def detail(eid: int, request: Request, s: Session = Depends(db), sess=Depends(ANY), msg: str = ""):
    e = s.get(Expert, eid)
    if not e:
        return back("/", "专家不存在")
    from collections import Counter
    ms = sorted(e.meetings, key=lambda m: (m.year or 0), reverse=True)
    roles = Counter(m.role.strip() for m in ms if m.role and m.role.strip())
    stats = dict(count=len(ms), latest=(ms[0].year if ms else None),
                 first=(ms[-1].year if ms else None),
                 top_role=(roles.most_common(1)[0][0] if roles else ""),
                 topics=[m.topic for m in ms if m.topic][:3])
    history.log_access(s, sess["name"], "view", expert=e, ip=client_ip(request),
                       detail="含敏感字段" if auth.can_see_sensitive(sess["role"]) else "已脱敏")
    s.commit()
    return render("detail.html", request, e=view(e, sess["role"]), msg=msg, deleted=e.deleted_at,
                  ms=ms, stats=stats,
                  expert=e, focus_levels=FOCUS_LEVELS, focus_order=FOCUS_ORDER,
                  meetings=s.query(Meeting).order_by(Meeting.year.desc(), Meeting.name).all(),
                  my_groups=visible_groups(s, sess).all(),
                  all_tags=s.query(Tag).order_by(csort(Tag.name, s)).all(),
                  logs=history.for_expert(s, eid) if auth.can_see_sensitive(sess["role"]) else [],
                  labels=history.LABELS)


@app.get("/expert/{eid}/edit", response_class=HTMLResponse)
def expert_edit(eid: int, request: Request, s: Session = Depends(db), sess=Depends(EDITOR)):
    e = s.get(Expert, eid)
    return render("expert_form.html", request, e=e, all_tags=s.query(Tag).order_by(csort(Tag.name, s)).all())


@app.post("/expert/save")
def expert_save(request: Request, s: Session = Depends(db), sess=Depends(EDITOR),
                eid: str = Form(""), name: str = Form(...), org: str = Form(""), title: str = Form(""),
                field: str = Form(""), phone: str = Form(""), email: str = Form(""), wechat: str = Form(""),
                bio: str = Form(""), note: str = Form(""), tags: str = Form("")):
    if eid:
        e = s.get(Expert, int(eid))
        before = history.snapshot(e)
    else:
        e = Expert(name=name.strip(), org=org.strip(), source=f"人工录入({sess['name']})")
        s.add(e)
        s.flush()
    for k, v in dict(name=name, org=org, title=title, field=field, phone=phone, email=email,
                     wechat=wechat, bio=bio, note=note).items():
        setattr(e, k, v.strip())
    e.tags = [importer.get_or_create_tag(s, t) for t in importer.split_tags(tags)]
    if eid:
        changed = history.log_update(s, sess["name"], e, before)
        msg = "已保存" if changed else "没有改动"
    else:
        importer.register_duplicates(s, e)
        history.log(s, sess["name"], "create", e, history.snapshot(e), "人工录入")
        msg = "已新建"
    s.commit()
    return back(f"/expert/{e.id}", msg)


@app.post("/expert/{eid}/delete")
def expert_delete(eid: int, s: Session = Depends(db), sess=Depends(ADMIN)):
    e = s.get(Expert, eid)
    if e and not e.deleted_at:
        importer.soft_delete(s, e, sess["name"], "手动删除")
        s.commit()
    return back("/", "已移入回收站，可在“回收站”恢复")


@app.get("/trash", response_class=HTMLResponse)
def trash(request: Request, s: Session = Depends(db), sess=Depends(ADMIN), msg: str = ""):
    items = s.query(Expert).filter(Expert.deleted_at.isnot(None)).order_by(Expert.deleted_at.desc()).all()
    return render("trash.html", request, items=items, msg=msg)


@app.post("/expert/{eid}/restore")
def expert_restore(eid: int, s: Session = Depends(db), sess=Depends(ADMIN)):
    e = s.get(Expert, eid)
    if e and e.deleted_at:
        importer.restore(s, e, sess["name"])
        s.commit()
    return back("/trash", f"已恢复 {e.name}" if e else "不存在")


@app.post("/expert/{eid}/purge")
def expert_purge(eid: int, s: Session = Depends(db), sess=Depends(ADMIN)):
    e = s.get(Expert, eid)
    if e and e.deleted_at:  # 只能彻底删除回收站里的
        importer.purge(s, e, sess["name"])
        s.commit()
    return back("/trash", "已彻底删除（历史记录保留）")


@app.post("/expert/{eid}/focus")
def set_focus(eid: int, s: Session = Depends(db), sess=Depends(EDITOR),
              level: str = Form(""), note: str = Form("")):
    e = s.get(Expert, eid)
    if not e:
        return back("/", "专家不存在")
    old = (FOCUS_LEVELS.get(e.focus_level, "未分级"), e.focus_note or "")
    e.focus_level = level if level in FOCUS_LEVELS else ""
    e.focus_note = note.strip()[:256]
    new = (FOCUS_LEVELS.get(e.focus_level, "未分级"), e.focus_note)
    if old != new:
        history.log(s, sess["name"], "focus", e,
                    {"关注分级": [old[0], new[0]], "关注说明": [old[1], new[1]]})
    s.commit()
    return back(f"/expert/{eid}", "已更新关注分级")


@app.get("/focus", response_class=HTMLResponse)
def focus_page(request: Request, s: Session = Depends(db), sess=Depends(ANY), msg: str = ""):
    """重点关注：按分级分栏展示，核心/重点在前。"""
    buckets = []
    for k in FOCUS_ORDER:
        rows = live(s.query(Expert)).filter(Expert.focus_level == k).order_by(Expert.updated_at.desc()).all()
        buckets.append((k, FOCUS_LEVELS[k], [view(e, sess["role"]) for e in rows]))
    unset = live(s.query(Expert)).filter((Expert.focus_level == "") | Expert.focus_level.is_(None)).count()
    return render("focus.html", request, buckets=buckets, unset=unset, msg=msg)


@app.get("/groups", response_class=HTMLResponse)
def groups_page(request: Request, s: Session = Depends(db), sess=Depends(ANY), msg: str = ""):
    return render("groups.html", request, msg=msg, groups=visible_groups(s, sess).all())


@app.post("/groups")
def group_create(s: Session = Depends(db), sess=Depends(EDITOR), name: str = Form(...),
                 description: str = Form(""), is_public: str = Form("")):
    name = name.strip()
    if not name:
        return back("/groups", "分组名不能为空")
    g = ExpertGroup(name=name, description=description.strip()[:256],
                    is_public=1 if is_public else 0, owner=sess["name"])
    s.add(g)
    s.commit()
    return back(f"/groups/{g.id}", f"已创建分组「{name}」")


def _group_or_403(s: Session, gid: int, sess: dict, need_write: bool = False) -> ExpertGroup:
    g = s.get(ExpertGroup, gid)
    if not g:
        raise HTTPException(404, "分组不存在")
    if not g.is_public and g.owner != sess["name"]:
        raise HTTPException(403, "这是他人的私有分组")
    if need_write and sess["role"] == "intern":
        raise HTTPException(403, "无权限")
    return g


@app.get("/groups/{gid}", response_class=HTMLResponse)
def group_detail(gid: int, request: Request, s: Session = Depends(db), sess=Depends(ANY), msg: str = ""):
    g = _group_or_403(s, gid, sess)
    rows = [e for e in g.experts if not e.deleted_at]
    return render("group_detail.html", request, g=g, msg=msg,
                  experts=[view(e, sess["role"]) for e in rows], mine=g.owner == sess["name"])


@app.post("/groups/{gid}/edit")
def group_edit(gid: int, s: Session = Depends(db), sess=Depends(EDITOR), name: str = Form(...),
               description: str = Form(""), is_public: str = Form("")):
    g = _group_or_403(s, gid, sess, need_write=True)
    if g.owner != sess["name"] and sess["role"] != "admin":
        return back(f"/groups/{gid}", "只有创建者或管理员能修改分组")
    g.name, g.description = name.strip() or g.name, description.strip()[:256]
    g.is_public = 1 if is_public else 0
    s.commit()
    return back(f"/groups/{gid}", "已保存")


@app.post("/groups/{gid}/delete")
def group_delete(gid: int, s: Session = Depends(db), sess=Depends(EDITOR)):
    g = _group_or_403(s, gid, sess, need_write=True)
    if g.owner != sess["name"] and sess["role"] != "admin":
        return back(f"/groups/{gid}", "只有创建者或管理员能删除分组")
    name = g.name
    g.experts = []
    s.delete(g)
    s.commit()
    return back("/groups", f"已删除分组「{name}」（专家本身不受影响）")


@app.post("/expert/{eid}/groups")
def expert_groups(eid: int, request: Request, s: Session = Depends(db), sess=Depends(EDITOR),
                  gid: str = Form(...), action: str = Form("add")):
    e = s.get(Expert, eid)
    g = _group_or_403(s, int(gid), sess, need_write=True)
    if not e:
        return back("/", "专家不存在")
    if action == "add" and e not in g.experts:
        g.experts.append(e)
        history.log(s, sess["name"], "group_add", e, {}, f"加入分组「{g.name}」")
    elif action == "del" and e in g.experts:
        g.experts.remove(e)
        history.log(s, sess["name"], "group_del", e, {}, f"移出分组「{g.name}」")
    s.commit()
    return back(request.headers.get("referer", f"/expert/{eid}").split("?")[0] or f"/expert/{eid}")


@app.get("/meetings", response_class=HTMLResponse)
def meetings_page(request: Request, s: Session = Depends(db), sess=Depends(ANY),
                  view_mode: str = "list", month: str = "", year: str = "", status: str = "", msg: str = ""):
    from calendar import Calendar
    from datetime import date, timedelta
    from sqlalchemy import func
    view_mode = view_mode if view_mode in ("list", "month", "year") else "list"
    q = s.query(Meeting)
    if year.isdigit():
        q = q.filter(Meeting.year == int(year))
    if status in MEETING_STATUS:
        q = q.filter(Meeting.status == status)
    rows = sorted(q.all(), key=lambda m: m.sort_key, reverse=True)
    counts = dict(s.query(Participation.meeting_id, func.count(Participation.id))
                  .group_by(Participation.meeting_id).all())
    years = [y for (y,) in s.query(Meeting.year).distinct().order_by(Meeting.year.desc()) if y]
    today = date.today()
    ctx = dict(meetings=rows, counts=counts, years=years, statuses=MEETING_STATUS,
               f=dict(year=year, status=status), view_mode=view_mode, today=today)

    if view_mode == "list":
        upcoming = sorted([m for m in rows if m.is_upcoming], key=lambda m: m.sort_key)
        ctx.update(upcoming=upcoming, past=[m for m in rows if not m.is_upcoming])

    elif view_mode == "month":
        try:
            y, mo = (int(x) for x in month.split("-"))
            cur = date(y, mo, 1)
        except (ValueError, AttributeError):
            cur = today.replace(day=1)
        weeks = Calendar(firstweekday=0).monthdatescalendar(cur.year, cur.month)
        dated = [m for m in rows if m.start_date]
        grid = [[(d, [m for m in dated if m.covers(d)]) for d in wk] for wk in weeks]
        prev = (cur - timedelta(days=1)).replace(day=1)
        nxt = (cur.replace(day=28) + timedelta(days=7)).replace(day=1)
        in_month = sorted([m for m in dated if m.start_date.year == cur.year
                           and m.start_date.month == cur.month], key=lambda m: m.sort_key)
        nearest = None
        if not in_month and dated:  # 当月没会议时，指路到最近的有会议的月份
            after = sorted([m for m in dated if m.start_date >= cur], key=lambda m: m.start_date)
            before = sorted([m for m in dated if m.start_date < cur], key=lambda m: m.start_date, reverse=True)
            pick = after[0] if after else (before[0] if before else None)
            if pick:
                nearest = (f"{pick.start_date:%Y-%m}", f"{pick.start_date:%Y 年 %-m 月}".replace(" 0", " ")
                           if os.name != "nt" else f"{pick.start_date.year} 年 {pick.start_date.month} 月", pick)
        ctx.update(grid=grid, cur=cur, prev=f"{prev:%Y-%m}", next=f"{nxt:%Y-%m}",
                   undated=[m for m in rows if not m.start_date], in_month=in_month, nearest=nearest)

    else:  # year
        by_year = {}
        for m in rows:
            by_year.setdefault(m.year or "未填年份", []).append(m)
        ctx.update(by_year=by_year)
    return render("meetings.html", request, msg=msg, **ctx)


@app.post("/meetings")
def meeting_create(s: Session = Depends(db), sess=Depends(EDITOR), name: str = Form(...),
                   year: str = Form(""), start_date: str = Form(""), end_date: str = Form(""),
                   location: str = Form(""), status: str = Form("planned")):
    name = name.strip()
    if not name:
        return back("/meetings", "会议名称不能为空")
    m = Meeting(name=name, year=int(year) if year.strip().isdigit() else None,
                start_date=_date(start_date), end_date=_date(end_date),
                location=location.strip(), status=status if status in MEETING_STATUS else "planned")
    if m.start_date and not m.year:
        m.year = m.start_date.year
    s.add(m)
    s.flush()
    history.log(s, sess["name"], "meeting_new", None, {}, f"新建会议「{name}」",
                expert_id=None, expert_name="（会议）")
    s.commit()
    return back(f"/meetings/{m.id}", f"已创建会议「{name}」")


def _date(v: str):
    from datetime import datetime as _dt
    try:
        return _dt.strptime(v.strip(), "%Y-%m-%d").date()
    except (ValueError, AttributeError):
        return None


@app.get("/meetings/{mid}", response_class=HTMLResponse)
def meeting_detail(mid: int, request: Request, s: Session = Depends(db), sess=Depends(ANY), msg: str = ""):
    m = s.get(Meeting, mid)
    if not m:
        return back("/meetings", "会议不存在")
    rows = [(p, view(p.expert, sess["role"])) for p in m.participations if p.expert and not p.expert.deleted_at]
    rows.sort(key=lambda x: (x[0].role or "zzz", x[1]["name"]))
    return render("meeting_detail.html", request, m=m, rows=rows, msg=msg, statuses=MEETING_STATUS)


@app.post("/meetings/{mid}/edit")
def meeting_edit(mid: int, s: Session = Depends(db), sess=Depends(EDITOR), name: str = Form(...),
                 year: str = Form(""), start_date: str = Form(""), end_date: str = Form(""),
                 location: str = Form(""), status: str = Form(""), note: str = Form("")):
    m = s.get(Meeting, mid)
    if not m:
        return back("/meetings", "会议不存在")
    before = f"{m.name} {m.year or ''} {m.when} {m.location} {m.status_label}"
    m.name = name.strip() or m.name
    m.year = int(year) if year.strip().isdigit() else None
    m.start_date, m.end_date = _date(start_date), _date(end_date)
    if m.start_date and not m.year:
        m.year = m.start_date.year
    m.location, m.note = location.strip(), note.strip()
    if status in MEETING_STATUS:
        m.status = status
    s.query(Participation).filter_by(meeting_id=m.id).update({"meeting": m.name, "year": m.year})
    after = f"{m.name} {m.year or ''} {m.when} {m.location} {m.status_label}"
    if before != after:
        history.log(s, sess["name"], "meeting_edit", None, {"会议": [before, after]}, "",
                    expert_id=None, expert_name="（会议）")
    s.commit()
    return back(f"/meetings/{mid}", "已保存")


@app.post("/meetings/{mid}/delete")
def meeting_delete(mid: int, s: Session = Depends(db), sess=Depends(ADMIN)):
    m = s.get(Meeting, mid)
    if not m:
        return back("/meetings", "会议不存在")
    if m.participations:
        return back(f"/meetings/{mid}", f"还有 {len(m.participations)} 条参会记录，先移除后再删除会议")
    name = m.name
    s.delete(m)
    s.commit()
    return back("/meetings", f"已删除会议「{name}」")


@app.get("/access-log", response_class=HTMLResponse)
def access_log_page(request: Request, s: Session = Depends(db), sess=Depends(ADMIN), actor: str = "",
                    action: str = "", name: str = "", date_from: str = "", date_to: str = "", page: int = 1):
    """谁看过哪位专家。仅管理员可见——这本身就是敏感信息。"""
    query = history.access_filtered(s, actor=actor, action=action, name=name,
                                    date_from=date_from, date_to=date_to)
    logs, found, page, pages = paginate(query, page)
    params = {k: v for k, v in dict(actor=actor, action=action, name=name,
                                    date_from=date_from, date_to=date_to).items() if v}
    return render("access_log.html", request, logs=logs, actions=history.ACCESS_ACTIONS,
                  actors=history.access_actors(s), found=found, page=page, pages=pages, params=params,
                  f=dict(actor=actor, action=action, name=name, date_from=date_from, date_to=date_to),
                  dedup=history.DEDUP_MINUTES)


@app.get("/history", response_class=HTMLResponse)
def history_page(request: Request, s: Session = Depends(db), sess=Depends(EDITOR), actor: str = "",
                 action: str = "", name: str = "", date_from: str = "", date_to: str = "", page: int = 1):
    query = history.filtered(s, actor=actor, action=action, name=name, date_from=date_from, date_to=date_to)
    logs, found, page, pages = paginate(query, page)
    params = {k: v for k, v in dict(actor=actor, action=action, name=name, date_from=date_from, date_to=date_to).items() if v}
    return render("history.html", request, logs=logs, labels=history.LABELS, actions=history.ACTIONS,
                  actors=history.actors(s), f=dict(actor=actor, action=action, name=name, date_from=date_from,
                  date_to=date_to), params=params, found=found, page=page, pages=pages)


def find_or_create_meeting(s: Session, name: str, year, actor: str) -> Meeting:
    """按 名称+年份 找会议，没有就新建（导入和手工录入共用，避免同名会议散落）。"""
    name = (name or "").strip()
    if not name:
        return None
    m = s.query(Meeting).filter_by(name=name, year=year).first()
    if not m:
        m = Meeting(name=name, year=year, status="done")
        s.add(m)
        s.flush()
        history.log(s, actor, "meeting_new", None, {}, f"新建会议「{name}」{year or ''}",
                    expert_id=None, expert_name="（会议）")
    return m


@app.post("/expert/{eid}/meeting")
def add_meeting(eid: int, s: Session = Depends(db), sess=Depends(EDITOR), meeting: str = Form(""),
                meeting_id: str = Form(""), year: str = Form(""), mrole: str = Form(""), topic: str = Form("")):
    yr = int(year) if year.strip().isdigit() else None
    if meeting_id.isdigit():
        mt = s.get(Meeting, int(meeting_id))
    else:
        mt = find_or_create_meeting(s, meeting, yr, sess["name"])
    if not mt:
        return back(f"/expert/{eid}", "请选择或填写会议名称")
    p = Participation(expert_id=eid, meeting_id=mt.id, meeting=mt.name, year=mt.year,
                      role=mrole.strip(), topic=topic.strip())
    s.add(p)
    history.log(s, sess["name"], "meeting_add", s.get(Expert, eid),
                {"会议": mt.name, "年份": mt.year, "角色": p.role, "主题": p.topic})
    s.commit()
    return back(f"/expert/{eid}")


@app.post("/meeting/{mid}/delete")
def del_meeting(mid: int, s: Session = Depends(db), sess=Depends(EDITOR)):
    m = s.get(Participation, mid)
    eid = m.expert_id
    history.log(s, sess["name"], "meeting_del", m.expert,
                {"会议": m.meeting_name, "年份": m.year, "角色": m.role, "主题": m.topic})
    s.delete(m)
    s.commit()
    return back(f"/expert/{eid}")


# ---------- 导入 / 导出 ----------
@app.get("/import", response_class=HTMLResponse)
def import_page(request: Request, sess=Depends(ADMIN), msg: str = ""):
    return render("import.html", request, cols=importer.EXCEL_COLS, msg=msg)


@app.post("/import")
async def import_post(s: Session = Depends(db), sess=Depends(ADMIN), file: UploadFile = File(...)):
    res = importer.import_excel(s, await file.read(), file.filename, sess["name"])
    msg = res.get("error") or f"新增 {res['created']}，更新 {res['updated']}，待处理疑似重复 {res['pending_dup']}"
    return back("/import", msg)


@app.get("/export")
def export(s: Session = Depends(db), sess=Depends(ADMIN)):
    data = importer.export_excel(s)
    n = live(s.query(Expert)).count()
    history.log(s, sess["name"], "export", None, {}, f"导出全部 {n} 位专家",
                expert_id=None, expert_name="（全库）")
    history.log_access(s, sess["name"], "export", detail=f"导出 {n} 位专家")
    s.commit()
    fn = quote(f"专家库导出_{datetime.now():%Y%m%d}.xlsx")
    return Response(data, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": f"attachment; filename*=UTF-8''{fn}"})


# ---------- 疑似重复 ----------
@app.get("/duplicates", response_class=HTMLResponse)
def duplicates(request: Request, s: Session = Depends(db), sess=Depends(ADMIN), msg: str = ""):
    items = [d for d in s.query(DuplicateCandidate).filter_by(status="pending")
             if not d.expert_a.deleted_at and not d.expert_b.deleted_at]
    return render("duplicates.html", request, items=items, msg=msg)


@app.post("/duplicates/{did}/merge")
def dup_merge(did: int, s: Session = Depends(db), sess=Depends(ADMIN), keep: int = Form(...)):
    d = s.get(DuplicateCandidate, did)
    a, b = d.expert_a, d.expert_b
    keep_e, drop_e = (a, b) if keep == a.id else (b, a)
    importer.merge_experts(s, keep_e, drop_e, sess["name"])
    return back("/duplicates", f"已合并到 {keep_e.name}（{keep_e.org}）")


@app.post("/duplicates/{did}/distinct")
def dup_distinct(did: int, s: Session = Depends(db), sess=Depends(ADMIN)):
    s.get(DuplicateCandidate, did).status = "distinct"
    s.commit()
    return back("/duplicates", "已标记为不同人")


# ---------- 资料上传与审核 ----------
@app.get("/documents", response_class=HTMLResponse)
def documents(request: Request, s: Session = Depends(db), sess=Depends(EDITOR), msg: str = "",
              status: str = ""):
    q = s.query(Document)
    if status in ("pending", "reviewed", "failed"):
        q = q.filter(Document.status == status)
    docs = q.order_by(Document.created_at.desc(), Document.id.desc()).all()
    counts = {k: s.query(Document).filter_by(status=k).count() for k in ("pending", "reviewed", "failed")}
    return render("documents.html", request, docs=docs, msg=msg, llm_on=extract.llm_enabled(),
                  counts=counts, f=dict(status=status), max_batch=MAX_BATCH)


ALLOWED_DOC_EXT = (".pdf", ".docx", ".pptx", ".txt")
MAX_BATCH = 30


@app.post("/documents")
async def upload_doc(request: Request, s: Session = Depends(db), sess=Depends(EDITOR),
                     files: list[UploadFile] = File(...)):
    """批量上传：逐份处理，单份失败不影响其他；内容相同的重复文件直接跳过。"""
    import hashlib
    files = [f for f in files if f and f.filename]
    if not files:
        return back("/documents", "请选择文件")
    if len(files) > MAX_BATCH:
        return back("/documents", f"一次最多上传 {MAX_BATCH} 份，请分批")
    batch = f"{datetime.now():%Y%m%d%H%M%S}"
    ok, dup, bad, cand_total, first_id = [], [], [], 0, None
    for uf in files:
        name = uf.filename
        if os.path.splitext(name)[1].lower() not in ALLOWED_DOC_EXT:
            bad.append(f"{name}（格式不支持）")
            continue
        raw = await uf.read()
        digest = hashlib.sha256(raw).hexdigest()
        existing = s.query(Document).filter_by(sha256=digest).first()
        if existing:
            dup.append(f"{name} → 与已上传的「{existing.filename}」内容相同")
            continue
        safe = re.sub(r"[^\w.一-龥-]", "_", name)
        path = os.path.join(UPLOAD_DIR, f"{batch}_{len(ok) + len(bad):02d}_{safe}")
        try:
            with open(path, "wb") as fh:
                fh.write(raw)
            text = extract.file_to_text(path)
            cands, how = extract.extract_experts(text)
        except Exception as ex:            # 单份出错不打断整批
            bad.append(f"{name}（解析失败: {type(ex).__name__}）")
            s.add(Document(filename=name, path=path, text="", uploaded_by=sess["name"],
                           extracted_json="[]", status="failed", sha256=digest,
                           method=f"失败: {type(ex).__name__}", batch=batch))
            continue
        doc = Document(filename=name, path=path, text=text, uploaded_by=sess["name"],
                       extracted_json=json.dumps(cands, ensure_ascii=False),
                       sha256=digest, method=how, batch=batch)
        s.add(doc)
        s.flush()
        ok.append(name)
        cand_total += len(cands)
        first_id = first_id or doc.id
    s.commit()

    parts = []
    if ok:
        parts.append(f"成功 {len(ok)} 份，共提取 {cand_total} 位候选")
    if dup:
        parts.append(f"跳过重复 {len(dup)} 份")
    if bad:
        parts.append(f"失败 {len(bad)} 份：{'；'.join(bad[:3])}{'…' if len(bad) > 3 else ''}")
    msg = " · ".join(parts) or "没有可处理的文件"
    if len(ok) == 1 and not dup and not bad:
        return back(f"/documents/{first_id}", f"已提取 {cand_total} 位候选，请审核")
    return back("/documents", msg)


@app.get("/documents/{did}", response_class=HTMLResponse)
def review_doc(did: int, request: Request, s: Session = Depends(db), sess=Depends(EDITOR), msg: str = ""):
    doc = s.get(Document, did)
    cands = json.loads(doc.extracted_json or "[]")
    for c in cands:  # 标出库里已有的同名专家，便于判断是更新还是新建
        c["existing"] = [f"{e.name}（{e.org}）" for e in live(s.query(Expert)).filter_by(name=c["name"])]
    history.log_access(s, sess["name"], "doc_view", detail=doc.filename, ip=client_ip(request))
    s.commit()
    pending = s.query(Document).filter(Document.status == "pending", Document.id != doc.id)
    if doc.batch:  # 同一批次的优先，方便批量上传后连续审核
        pending = pending.order_by((Document.batch != doc.batch), Document.id)
    nxt = pending.first()
    left = s.query(Document).filter_by(status="pending").count()
    return render("review.html", request, doc=doc, cands=cands, msg=msg, fields=extract.FIELDS,
                  nxt=nxt, left=left)


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
        e, _ = importer.upsert_expert(s, d, doc.filename, sess["name"], "approve")
        if meeting:
            mt = find_or_create_meeting(s, meeting, int(year) if year.isdigit() else None, sess["name"])
            m = Participation(expert_id=e.id, meeting_id=mt.id, meeting=mt.name, year=mt.year,
                              topic=d.get("topic", "").strip(), role=form.get(f"role_{i}", "").strip())
            s.add(m)
            history.log(s, sess["name"], "meeting_add", e,
                        {"会议": mt.name, "年份": mt.year, "角色": m.role, "主题": m.topic}, f"来源: {doc.filename}")
        n += 1
    doc.status = "reviewed"
    s.commit()
    nxt = s.query(Document).filter(Document.status == "pending",
                                   Document.batch == doc.batch).order_by(Document.id).first() \
        or s.query(Document).filter_by(status="pending").order_by(Document.id).first()
    if nxt:
        return back(f"/documents/{nxt.id}", f"已入库 {n} 位专家，继续审核下一份")
    return back("/documents", f"已入库 {n} 位专家，全部审核完毕")


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
        results = [(view(e, sess["role"]), pts, reasons) for e, pts, reasons in search.search(s, parsed, limit=50)]
        history.log_access(s, sess["name"], "search", detail=f"{q[:80]} → {len(results)} 位", ip=client_ip(request))
        s.commit()
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


@app.post("/users/{uid}/reset")
def user_reset(uid: int, s: Session = Depends(db), sess=Depends(ADMIN), password: str = Form(...)):
    u = s.get(User, uid)
    if not u:
        return back("/users", "用户不存在")
    if len(password) < 6:
        return back("/users", "密码至少 6 位")
    u.password_hash = auth.hash_password(password)
    s.commit()
    return back("/users", f"已重置 {u.username} 的密码")


@app.post("/users/{uid}/delete")
def user_del(uid: int, s: Session = Depends(db), sess=Depends(ADMIN)):
    if uid == sess["uid"]:
        return back("/users", "不能删除自己")
    u = s.get(User, uid)
    if u:
        s.delete(u)
        s.commit()
    return back("/users", "已删除")
