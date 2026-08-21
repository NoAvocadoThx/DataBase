"""修改历史：记录谁在何时对哪位专家做了什么，修改类记录保存字段级 旧值→新值。"""
import json
from datetime import datetime, timedelta

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text
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
        q = q.filter(ChangeLog.expert_name.like(f"%{name}%"))
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
        q = q.filter(AccessLog.expert_name.like(f"%{name}%"))
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
