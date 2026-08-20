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
           "meeting_del": "删除合作记录"}


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
