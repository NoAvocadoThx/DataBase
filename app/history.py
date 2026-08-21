"""修改历史：记录谁在何时对哪位专家做了什么，修改类记录保存字段级 旧值→新值。"""
import json
from datetime import datetime, timedelta

from sqlalchemy import Column, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Session

from .models import Base, Expert

TRACKED = ("name", "org", "title", "field", "phone", "email", "wechat", "bio", "note", "source", "source_text")
LABELS = {"name": "姓名", "org": "单位", "title": "职务", "field": "研究方向", "phone": "手机", "email": "邮箱",
          "wechat": "微信", "bio": "简介", "note": "备注", "source": "来源", "source_text": "原文", "tags": "标签"}
ACTIONS = {"create": "新建", "update": "修改", "delete": "删除", "restore": "恢复", "purge": "彻底删除",
           "merge": "合并", "import": "Excel导入", "approve": "审核入库", "meeting_add": "添加合作记录",
           "meeting_del": "删除合作记录", "export": "导出全库",
           "focus": "调整关注分级", "group_add": "加入分组", "group_del": "移出分组",
           "meeting_new": "新建会议", "meeting_edit": "修改会议"}


class ChangeLog(Base):
    __tablename__ = "change_log"
    id = Column(Integer, primary_key=True)
    expert_id = Column(Integer, index=True)                 # 不设外键：专家彻底删除后历史仍保留
    expert_name = Column(String(64), default="")
    action = Column(String(16), nullable=False, index=True)
    actor = Column(String(64), default="", index=True)
    diff_json = Column(Text, default="{}")                  # {"字段": [旧, 新]} 或任意上下文
    summary = Column(String(256), default="")
    created_at = Column(DateTime, default=datetime.now, index=True)

    @property
    def diff(self) -> dict:
        try:
            return json.loads(self.diff_json or "{}")
        except ValueError:
            return {}

    @property
    def is_diff(self) -> bool:
        """diff 是否为 {字段: [旧, 新]} 形式（否则是完整快照或上下文）。"""
        d = self.diff
        return bool(d) and all(isinstance(v, list) and len(v) == 2 for v in d.values())

    @property
    def action_label(self) -> str:
        return ACTIONS.get(self.action, self.action)


def snapshot(e: Expert) -> dict:
    d = {k: getattr(e, k) or "" for k in TRACKED}
    d["tags"] = ", ".join(sorted(t.name for t in e.tags))
    return d


def diff_of(before: dict, after: dict) -> dict:
    return {k: [before.get(k, ""), after.get(k, "")] for k in after if before.get(k, "") != after.get(k, "")}


def log(s: Session, actor: str, action: str, e: Expert | None, diff: dict | None = None,
        summary: str = "", expert_id: int | None = None, expert_name: str = ""):
    s.add(ChangeLog(expert_id=e.id if e else expert_id, expert_name=e.name if e else expert_name,
                    action=action, actor=actor or "", summary=summary,
                    diff_json=json.dumps(diff or {}, ensure_ascii=False)))


def log_update(s: Session, actor: str, e: Expert, before: dict, action: str = "update", summary: str = "") -> bool:
    """比较快照并记录；无变化返回 False。"""
    d = diff_of(before, snapshot(e))
    if not d:
        return False
    log(s, actor, action, e, d, summary)
    return True


def for_expert(s: Session, expert_id: int, limit: int = 100) -> list[ChangeLog]:
    return (s.query(ChangeLog).filter_by(expert_id=expert_id)
            .order_by(ChangeLog.created_at.desc(), ChangeLog.id.desc()).limit(limit).all())


def filtered(s: Session, actor: str = "", action: str = "", name: str = "", date_from: str = "", date_to: str = ""):
    """返回带筛选条件的 Query（未执行），供分页。"""
    q = s.query(ChangeLog)
    if actor:
        q = q.filter(ChangeLog.actor == actor)
    if action:
        q = q.filter(ChangeLog.action == action)
    if name:
        q = q.filter(ChangeLog.expert_name.ilike(f"%{name}%"))
    if date_from:
        try:
            q = q.filter(ChangeLog.created_at >= datetime.strptime(date_from, "%Y-%m-%d"))
        except ValueError:
            pass
    if date_to:
        try:
            q = q.filter(ChangeLog.created_at < datetime.strptime(date_to, "%Y-%m-%d") + timedelta(days=1))
        except ValueError:
            pass
    return q.order_by(ChangeLog.created_at.desc(), ChangeLog.id.desc())


def actors(s: Session) -> list[str]:
    return [a for (a,) in s.query(ChangeLog.actor).distinct().order_by(ChangeLog.actor) if a]


def recent(s: Session, limit: int = 200) -> list[ChangeLog]:
    return s.query(ChangeLog).order_by(ChangeLog.created_at.desc(), ChangeLog.id.desc()).limit(limit).all()


# ---------------- 访问留痕（谁看了谁）----------------
# 与"修改历史"分开：修改历史记录数据变化，访问日志记录"看过"这件事。
# 混在一起会把修改记录淹没，而且查看量远大于修改量。
class AccessLog(Base):
    __tablename__ = "access_log"
    id = Column(Integer, primary_key=True)
    actor = Column(String(64), default="", index=True)
    action = Column(String(24), nullable=False, index=True)   # view / export / search / doc_view
    expert_id = Column(Integer, index=True)
    expert_name = Column(String(64), default="")
    detail = Column(String(256), default="")
    ip = Column(String(64), default="")
    created_at = Column(DateTime, default=datetime.now, index=True)


ACCESS_ACTIONS = {"view": "查看专家", "export": "导出全库", "search": "检索", "doc_view": "查看资料原文",
                  "chat": "AI 对话"}
DEDUP_MINUTES = 30   # 同一人短时间内反复看同一位专家只记一条，避免刷新刷出上千行


def log_access(s: Session, actor: str, action: str, *, expert=None, detail: str = "", ip: str = "",
               dedup: bool = True):
    """dedup=False 用于每条都要留痕的场景（如 AI 对话，每次提问内容都不同，合并会丢信息）。"""
    now = datetime.now()
    eid = expert.id if expert is not None else None
    recent = (s.query(AccessLog)
              .filter(AccessLog.actor == actor, AccessLog.action == action,
                      AccessLog.expert_id.is_(eid) if eid is None else AccessLog.expert_id == eid,
                      AccessLog.created_at >= now - timedelta(minutes=DEDUP_MINUTES))
              .order_by(AccessLog.id.desc()).first()) if dedup else None
    if recent:
        recent.created_at = now      # 只更新时间，不新增行
        return
    s.add(AccessLog(actor=actor or "", action=action, expert_id=eid,
                    expert_name=(expert.name if expert is not None else ""),
                    detail=detail[:256], ip=ip[:64]))


def access_filtered(s: Session, actor: str = "", action: str = "", name: str = "",
                    date_from: str = "", date_to: str = ""):
    q = s.query(AccessLog)
    if actor:
        q = q.filter(AccessLog.actor == actor)
    if action:
        q = q.filter(AccessLog.action == action)
    if name:
        q = q.filter(AccessLog.expert_name.ilike(f"%{name}%"))
    for v, op in ((date_from, "from"), (date_to, "to")):
        if not v:
            continue
        try:
            d = datetime.strptime(v, "%Y-%m-%d")
        except ValueError:
            continue
        q = q.filter(AccessLog.created_at >= d) if op == "from" else \
            q.filter(AccessLog.created_at < d + timedelta(days=1))
    return q.order_by(AccessLog.created_at.desc(), AccessLog.id.desc())


def access_actors(s: Session) -> list[str]:
    return [a for (a,) in s.query(AccessLog.actor).distinct().order_by(AccessLog.actor) if a]


# ---------------- 撤销（revert）----------------
# 三条原则：
# 1. 撤销本身是一次新的操作，会再写一条历史；绝不删除或修改原记录——审计链不能断。
# 2. 字段在那之后又被改过时，默认拒绝并说明，避免把更新的值悄悄盖掉（可强制执行）。
# 3. 做不到的（彻底删除、合并）明确说明原因，不给一个点了没反应的按钮。

REVERT_UNSUPPORTED = {
    "purge": "数据已彻底删除，无法恢复",
    "merge": "合并涉及标签、合作记录的迁移，无法一键还原；请到回收站恢复被合并方后手工调整",
    "export": "导出不改动数据，无需撤销",
    "meeting_new": "新建会议请到会议页面处理",
    "meeting_edit": "会议信息修改请到会议页面处理",
}
FIELD_ACTIONS = ("update", "import", "approve")   # 这三种可能是字段修改，也可能是新建


def revert_blocker(s: Session, c: "ChangeLog") -> str | None:
    """返回不能撤销的原因；None 表示可以撤销。"""
    if c.action in REVERT_UNSUPPORTED:
        return REVERT_UNSUPPORTED[c.action]
    if c.expert_id is None:
        return "这条记录没有关联到具体专家"
    e = s.get(Expert, c.expert_id)
    if e is None:
        return "该专家已被彻底删除"
    if c.action in FIELD_ACTIONS and not c.is_diff:
        return None          # 新建型：撤销 = 移入回收站
    if c.action in ("create",) or c.is_diff:
        return None
    if c.action in ("delete", "restore", "focus", "group_add", "group_del",
                    "meeting_add", "meeting_del"):
        return None
    return "这种操作暂不支持撤销"


def revert_conflicts(s: Session, c: "ChangeLog") -> list[str]:
    """字段修改类：找出"当前值已不等于当时改成的值"的字段，说明期间又被人改过。"""
    if not c.is_diff:
        return []
    e = s.get(Expert, c.expert_id)
    if e is None:
        return []
    now = snapshot(e)
    out = []
    for k, pair in c.diff.items():
        key = k if k in TRACKED or k == "tags" else None
        if key is None:               # 关注分级等用中文键，单独处理
            continue
        if now.get(key, "") != (pair[1] or ""):
            out.append(LABELS.get(key, key))
    return out


def apply_revert(s: Session, c: "ChangeLog", actor: str) -> str:
    """执行撤销，返回给用户看的说明。调用前必须先过 revert_blocker。"""
    from . import importer                      # 延迟导入避免循环依赖
    from .models import ExpertGroup, FOCUS_LEVELS, Participation
    e = s.get(Expert, c.expert_id)
    label = ACTIONS.get(c.action, c.action)
    before = snapshot(e)

    # 新建型（新建 / 导入或审核时新建）→ 移入回收站
    if c.action == "create" or (c.action in FIELD_ACTIONS and not c.is_diff):
        if e.deleted_at:
            return f"「{e.name}」已在回收站中，无需撤销"
        importer.soft_delete(s, e, actor, f"撤销{label}操作（历史 #{c.id}）")
        return f"已撤销{label}：「{e.name}」移入回收站，可再恢复"

    if c.action == "delete":
        if not e.deleted_at:
            return f"「{e.name}」当前未被删除，无需撤销"
        importer.restore(s, e, actor)
        return f"已撤销删除：「{e.name}」已恢复"

    if c.action == "restore":
        if e.deleted_at:
            return f"「{e.name}」当前已在回收站，无需撤销"
        importer.soft_delete(s, e, actor, f"撤销恢复操作（历史 #{c.id}）")
        return f"已撤销恢复：「{e.name}」重新移入回收站"

    if c.action == "focus":
        d = c.diff
        old_label = (d.get("关注分级") or ["", ""])[0]
        rev = {v: k for k, v in FOCUS_LEVELS.items()}
        e.focus_level = rev.get(old_label, "")
        e.focus_note = (d.get("关注说明") or ["", ""])[0]
        log(s, actor, "focus", e,
            {"关注分级": [FOCUS_LEVELS.get(rev.get(old_label, ""), "未分级"), old_label or "未分级"]},
            f"撤销历史 #{c.id}")
        return f"已撤销关注分级调整：恢复为「{old_label or '未分级'}」"

    if c.action in ("group_add", "group_del"):
        d = c.diff
        gid, name = d.get("分组ID"), d.get("分组", "")
        g = s.get(ExpertGroup, gid) if gid else s.query(ExpertGroup).filter_by(name=name).first()
        if not g:
            return f"分组「{name or '?'}」已不存在，无法撤销"
        if c.action == "group_add":
            if e in g.experts:
                g.experts.remove(e)
            log(s, actor, "group_del", e, {"分组": g.name, "分组ID": g.id},
                f"撤销加入分组「{g.name}」（历史 #{c.id}）")
            return f"已撤销：「{e.name}」移出分组「{g.name}」"
        if e not in g.experts:
            g.experts.append(e)
        log(s, actor, "group_add", e, {"分组": g.name, "分组ID": g.id},
            f"撤销移出分组「{g.name}」（历史 #{c.id}）")
        return f"已撤销：「{e.name}」重新加入分组「{g.name}」"

    if c.action in ("meeting_add", "meeting_del"):
        d = c.diff
        name, year = d.get("会议", ""), d.get("年份")
        role, topic = d.get("角色", "") or "", d.get("主题", "") or ""
        if c.action == "meeting_add":
            p = (s.query(Participation)
                 .filter_by(expert_id=e.id, meeting=name, year=year, role=role, topic=topic).first())
            if not p:
                return "这条合作记录已不存在，无需撤销"
            s.delete(p)
            log(s, actor, "meeting_del", e, d, f"撤销添加合作记录（历史 #{c.id}）")
            return f"已撤销：删除了「{name}」的合作记录"
        from .models import Meeting
        mt = s.query(Meeting).filter_by(name=name, year=year).first()
        s.add(Participation(expert_id=e.id, meeting_id=mt.id if mt else None,
                            meeting=name, year=year, role=role, topic=topic))
        log(s, actor, "meeting_add", e, d, f"撤销删除合作记录（历史 #{c.id}）")
        return f"已撤销：恢复了「{name}」的合作记录"

    # 字段修改类：把每个字段还原成当时的旧值
    changed = []
    for k, pair in c.diff.items():
        old = pair[0] or ""
        if k == "tags":
            e.tags = [importer.get_or_create_tag(s, t) for t in importer.split_tags(old)]
            changed.append(LABELS["tags"])
        elif k in TRACKED:
            setattr(e, k, old)
            changed.append(LABELS.get(k, k))
    if not changed:
        return "这条记录没有可还原的字段"
    log_update(s, actor, e, before, "update", f"撤销历史 #{c.id}（{label}）")
    return f"已撤销{label}：还原了 {'、'.join(changed)}"
# ---------------- AI 对话存档（仅管理员可见）----------------
# 与 AccessLog 分工：AccessLog 记"谁在什么时候问了什么"（全库留痕的一部分），
# 这里额外存 AI 的完整回答。回答里会带出专家姓名和单位，所以只给管理员看。
class ChatArchive(Base):
    __tablename__ = "chat_archive"
    id = Column(Integer, primary_key=True)
    actor = Column(String(64), default="", index=True)
    question = Column(Text, default="")
    answer = Column(Text, default="")
    tools = Column(String(256), default="")        # 本轮用到的工具，逗号分隔
    expert_ids = Column(String(256), default="")   # 回答里引用到的专家 id，逗号分隔
    fabricated = Column(String(256), default="")   # 模型编造的、库里没有的 id；空 = 正常
    blocked = Column(String(32), default="")       # 非空表示没发给大模型（如 offtopic）
    ip = Column(String(64), default="")
    created_at = Column(DateTime, default=datetime.now, index=True)
    # ---- DeepSeek 用量（一次提问可能跨多轮工具调用，这里是累加值）----
    rounds = Column(Integer, default=0)                  # 实际调了几次大模型
    prompt_tokens = Column(Integer, default=0)
    completion_tokens = Column(Integer, default=0)
    total_tokens = Column(Integer, default=0)
    cache_hit_tokens = Column(Integer, default=0)        # DeepSeek 特有：命中 prompt 缓存的输入
    cache_miss_tokens = Column(Integer, default=0)
    cost = Column(Float, default=0.0)                    # 估算花费（元）

    @property
    def is_clean(self) -> bool:
        return not self.fabricated and not self.blocked

    @property
    def cache_rate(self) -> float:
        hit = self.cache_hit_tokens or 0
        tot = hit + (self.cache_miss_tokens or 0)
        return hit / tot * 100 if tot else 0.0


def chat_filtered(s: Session, actor: str = "", q: str = "", flag: str = "",
                  date_from: str = "", date_to: str = ""):
    """AI 对话存档的筛选 Query（未执行），供分页。q 同时搜问题和回答。"""
    from sqlalchemy import or_
    query = s.query(ChatArchive)
    if actor:
        query = query.filter(ChatArchive.actor == actor)
    if q:
        like = f"%{q}%"
        query = query.filter(or_(ChatArchive.question.ilike(like), ChatArchive.answer.ilike(like)))
    if flag == "fabricated":
        query = query.filter(ChatArchive.fabricated != "")
    elif flag == "blocked":
        query = query.filter(ChatArchive.blocked != "")
    for v, op in ((date_from, "from"), (date_to, "to")):
        if not v:
            continue
        try:
            d = datetime.strptime(v, "%Y-%m-%d")
        except ValueError:
            continue
        query = query.filter(ChatArchive.created_at >= d) if op == "from" else \
            query.filter(ChatArchive.created_at < d + timedelta(days=1))
    return query.order_by(ChatArchive.created_at.desc(), ChatArchive.id.desc())


def chat_actors(s: Session) -> list[str]:
    return [a for (a,) in s.query(ChatArchive.actor).distinct().order_by(ChatArchive.actor) if a]


def chat_usage(s: Session, since: datetime | None = None) -> dict:
    """一段时间内的 DeepSeek 用量汇总。since=None 表示全部。"""
    from sqlalchemy import func as _f
    q = s.query(_f.count(ChatArchive.id), _f.coalesce(_f.sum(ChatArchive.prompt_tokens), 0),
                _f.coalesce(_f.sum(ChatArchive.completion_tokens), 0),
                _f.coalesce(_f.sum(ChatArchive.cache_hit_tokens), 0),
                _f.coalesce(_f.sum(ChatArchive.cache_miss_tokens), 0),
                _f.coalesce(_f.sum(ChatArchive.cost), 0.0))
    q = q.filter(ChatArchive.blocked == "")          # 被拦下的没发给模型，不算用量
    if since:
        q = q.filter(ChatArchive.created_at >= since)
    n, pin, pout, hit, miss, cost = q.one()
    return {"calls": n or 0, "prompt": pin or 0, "completion": pout or 0,
            "total": (pin or 0) + (pout or 0), "hit": hit or 0, "miss": miss or 0,
            "cost": round(cost or 0.0, 4),
            "cache_rate": round((hit or 0) / (hit + miss) * 100, 1) if (hit + miss) else 0.0}


def chat_usage_by_actor(s: Session, since: datetime | None = None, limit: int = 20) -> list[dict]:
    from sqlalchemy import func as _f
    q = (s.query(ChatArchive.actor, _f.count(ChatArchive.id),
                 _f.coalesce(_f.sum(ChatArchive.total_tokens), 0),
                 _f.coalesce(_f.sum(ChatArchive.cost), 0.0))
         .filter(ChatArchive.blocked == "").group_by(ChatArchive.actor))
    if since:
        q = q.filter(ChatArchive.created_at >= since)
    rows = q.order_by(_f.coalesce(_f.sum(ChatArchive.cost), 0.0).desc()).limit(limit).all()
    return [{"actor": a or "?", "calls": n, "tokens": t, "cost": round(c or 0.0, 4)} for a, n, t, c in rows]


def chat_usage_by_day(s: Session, days: int = 14) -> list[dict]:
    from sqlalchemy import func as _f
    since = datetime.now() - timedelta(days=days)
    # 按天分组要跨库：SQLite 用 strftime，PostgreSQL 用 to_char/date()。
    # 数据量很小（14 天的对话记录），直接取出来在 Python 里聚合，省得写两套方言。
    rows = (s.query(ChatArchive.created_at, ChatArchive.total_tokens, ChatArchive.cost)
            .filter(ChatArchive.blocked == "", ChatArchive.created_at >= since).all())
    agg: dict[str, dict] = {}
    for created, tok, cost in rows:
        d = created.strftime("%Y-%m-%d")
        a = agg.setdefault(d, {"day": d, "calls": 0, "tokens": 0, "cost": 0.0})
        a["calls"] += 1
        a["tokens"] += tok or 0
        a["cost"] += cost or 0.0
    return [{**a, "cost": round(a["cost"], 4)} for a in sorted(agg.values(), key=lambda x: x["day"], reverse=True)]
