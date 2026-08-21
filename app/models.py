"""数据模型。"""
import os
from datetime import datetime

from sqlalchemy import (Column, Date, DateTime, ForeignKey, Integer, String, Table, Text,
                        create_engine)
from sqlalchemy.orm import declarative_base, relationship, sessionmaker

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.getenv("DB_PATH", os.path.join(BASE_DIR, "experts.db"))
UPLOAD_DIR = os.getenv("UPLOAD_DIR", os.path.join(BASE_DIR, "uploads"))

Base = declarative_base()

expert_tag = Table("expert_tag", Base.metadata,
                   Column("expert_id", ForeignKey("expert.id"), primary_key=True),
                   Column("tag_id", ForeignKey("tag.id"), primary_key=True))

ROLES = ("admin", "planner", "intern")
SENSITIVE_ROLES = {"admin", "planner"}

# 重点关注分级：让策划快速找到需要重点维护的专家。空值 = 未分级
FOCUS_LEVELS = {"core": "核心", "key": "重点", "normal": "一般", "avoid": "不合作"}
FOCUS_ORDER = ["core", "key", "normal", "avoid"]

group_expert = Table("group_expert", Base.metadata,
                     Column("group_id", ForeignKey("expert_group.id"), primary_key=True),
                     Column("expert_id", ForeignKey("expert.id"), primary_key=True))


class User(Base):
    __tablename__ = "user"
    id = Column(Integer, primary_key=True)
    username = Column(String(64), unique=True, nullable=False)
    password_hash = Column(String(256), nullable=False)
    role = Column(String(16), default="intern")
    display_name = Column(String(64))


class Expert(Base):
    __tablename__ = "expert"
    id = Column(Integer, primary_key=True)
    name = Column(String(64), nullable=False, index=True)
    org = Column(String(128), default="", index=True)
    title = Column(String(128), default="")
    field = Column(String(256), default="")
    phone = Column(String(32), default="")
    email = Column(String(128), default="")
    wechat = Column(String(64), default="")
    bio = Column(Text, default="")
    note = Column(Text, default="")
    source = Column(String(256), default="")       # 来源文件/人工
    source_text = Column(Text, default="")         # 来源原文片段
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)
    deleted_at = Column(DateTime, index=True)              # 软删除（回收站）
    deleted_by = Column(String(64), default="")
    focus_level = Column(String(16), default="", index=True)   # 重点关注分级
    focus_note = Column(String(256), default="")               # 为什么关注（内部）
    tags = relationship("Tag", secondary=expert_tag, back_populates="experts")
    meetings = relationship("Participation", back_populates="expert",
                            cascade="all, delete-orphan")
    groups = relationship("ExpertGroup", secondary=group_expert, back_populates="experts")

    @property
    def focus_label(self) -> str:
        return FOCUS_LEVELS.get(self.focus_level, "")

    def searchable_text(self) -> str:
        return " ".join(filter(None, [self.name, self.org, self.title, self.field, self.bio]
                               + [f"{m.meeting} {m.topic or ''}" for m in self.meetings]))


class Tag(Base):
    __tablename__ = "tag"
    id = Column(Integer, primary_key=True)
    name = Column(String(64), unique=True, nullable=False)
    category = Column(String(32), default="专业能力")
    experts = relationship("Expert", secondary=expert_tag, back_populates="tags")


MEETING_STATUS = {"planned": "筹备中", "confirmed": "已确定", "done": "已举办", "cancelled": "已取消"}


class Meeting(Base):
    """会议实体。以前会议只是合作记录里的一个字符串，无法按时间看、无法统一改名。"""
    __tablename__ = "meeting"
    id = Column(Integer, primary_key=True)
    name = Column(String(128), nullable=False, index=True)
    year = Column(Integer, index=True)
    start_date = Column(Date, index=True)
    end_date = Column(Date)
    location = Column(String(128), default="")
    status = Column(String(16), default="done", index=True)
    note = Column(Text, default="")
    created_at = Column(DateTime, default=datetime.now)
    participations = relationship("Participation", back_populates="meeting_obj")

    @property
    def status_label(self) -> str:
        return MEETING_STATUS.get(self.status, self.status or "")

    @property
    def when(self) -> str:
        if self.start_date and self.end_date and self.end_date != self.start_date:
            return f"{self.start_date:%Y-%m-%d} ~ {self.end_date:%m-%d}"
        if self.start_date:
            return f"{self.start_date:%Y-%m-%d}"
        return str(self.year or "")

    @property
    def sort_key(self):
        from datetime import date
        return self.start_date or (date(self.year, 1, 1) if self.year else date(1900, 1, 1))

    @property
    def last_day(self):
        return self.end_date or self.start_date

    @property
    def is_upcoming(self) -> bool:
        """未来或正在进行的会议（按结束日期算，进行中的也算"将来"）。无日期时按状态判断。"""
        from datetime import date
        if self.last_day:
            return self.last_day >= date.today() and self.status != "cancelled"
        return self.status in ("planned", "confirmed")

    @property
    def days_away(self):
        from datetime import date
        return (self.start_date - date.today()).days if self.start_date else None

    def covers(self, d) -> bool:
        return bool(self.start_date and self.start_date <= d <= (self.end_date or self.start_date))


class Participation(Base):
    __tablename__ = "participation"
    id = Column(Integer, primary_key=True)
    expert_id = Column(ForeignKey("expert.id"), nullable=False, index=True)
    meeting_id = Column(ForeignKey("meeting.id"), index=True)
    meeting = Column(String(128), nullable=False)   # 迁移前的会议名，保留做兜底
    year = Column(Integer)
    role = Column(String(64), default="")
    topic = Column(String(256), default="")
    expert = relationship("Expert", back_populates="meetings")
    meeting_obj = relationship("Meeting", back_populates="participations")

    @property
    def meeting_name(self) -> str:
        return self.meeting_obj.name if self.meeting_obj else self.meeting


class ExpertGroup(Base):
    """自定义分组：策划自己建的专家名单（如"2026 大会候选""ADC 专题库"）。
    公开组全团队可见可用；私有组只有创建者能看到。"""
    __tablename__ = "expert_group"
    id = Column(Integer, primary_key=True)
    name = Column(String(64), nullable=False)
    description = Column(String(256), default="")
    is_public = Column(Integer, default=1, index=True)
    owner = Column(String(64), default="", index=True)
    created_at = Column(DateTime, default=datetime.now)
    experts = relationship("Expert", secondary=group_expert, back_populates="groups")

    @property
    def visibility(self) -> str:
        return "公开" if self.is_public else "私有"


class Document(Base):
    """上传的资料文件及 AI 抽取结果（待审核）。"""
    __tablename__ = "document"
    id = Column(Integer, primary_key=True)
    filename = Column(String(256), nullable=False)
    path = Column(String(512), nullable=False)
    text = Column(Text, default="")
    extracted_json = Column(Text, default="[]")    # list[dict] 候选专家
    status = Column(String(16), default="pending")  # pending / reviewed / failed
    uploaded_by = Column(String(64), default="")
    created_at = Column(DateTime, default=datetime.now)
    sha256 = Column(String(64), index=True)         # 内容指纹，用于识别重复上传
    method = Column(String(32), default="")         # 抽取方式：llm / rule / 失败原因
    batch = Column(String(32), index=True)          # 同一次批量上传的标识

    @property
    def candidate_count(self) -> int:
        import json as _j
        try:
            return len(_j.loads(self.extracted_json or "[]"))
        except ValueError:
            return 0


class DuplicateCandidate(Base):
    __tablename__ = "duplicate_candidate"
    id = Column(Integer, primary_key=True)
    expert_a_id = Column(ForeignKey("expert.id"), nullable=False)
    expert_b_id = Column(ForeignKey("expert.id"), nullable=False)
    status = Column(String(16), default="pending")  # pending / merged / distinct
    created_at = Column(DateTime, default=datetime.now)
    expert_a = relationship("Expert", foreign_keys=[expert_a_id])
    expert_b = relationship("Expert", foreign_keys=[expert_b_id])


def live(query):
    """只取未删除的专家。"""
    return query.filter(Expert.deleted_at.is_(None))


def visible_groups(s, sess: dict):
    """公开组 + 自己的私有组。"""
    from sqlalchemy import or_
    return (s.query(ExpertGroup)
            .filter(or_(ExpertGroup.is_public == 1, ExpertGroup.owner == sess["name"]))
            .order_by(ExpertGroup.is_public.desc(), ExpertGroup.name))


def make_engine(path: str = DB_PATH):
    return create_engine(f"sqlite:///{path}", connect_args={"check_same_thread": False})


engine = make_engine()
SessionLocal = sessionmaker(bind=engine)


def init_db(eng=None):
    from . import history  # noqa: F401  注册 ChangeLog 表
    eng = eng or engine
    Base.metadata.create_all(eng)
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    _migrate(eng)
    _link_meetings(eng)


def _link_meetings(eng):
    """把 participation 里的会议名字符串归并成 Meeting 记录并挂上外键（幂等）。"""
    from sqlalchemy.orm import Session as _S
    with _S(eng) as s:
        rows = s.query(Participation).filter(Participation.meeting_id.is_(None)).all()
        if not rows:
            return
        cache = {(m.name, m.year): m for m in s.query(Meeting)}
        for p in rows:
            key = ((p.meeting or "").strip(), p.year)
            if not key[0]:
                continue
            m = cache.get(key)
            if not m:
                m = Meeting(name=key[0], year=key[1], status="done")
                s.add(m)
                s.flush()
                cache[key] = m
            p.meeting_id = m.id
        s.commit()


def _migrate(eng):
    """轻量迁移：模型新增的列自动 ALTER TABLE ADD COLUMN（SQLite 不支持删列，只做加列）。"""
    from sqlalchemy import inspect, text
    insp = inspect(eng)
    with eng.begin() as conn:
        for table in Base.metadata.sorted_tables:
            existing = {c["name"] for c in insp.get_columns(table.name)}
            for col in table.columns:
                if col.name not in existing:
                    ctype = col.type.compile(eng.dialect)
                    conn.execute(text(f'ALTER TABLE "{table.name}" ADD COLUMN "{col.name}" {ctype}'))
            have_idx = {i["name"] for i in insp.get_indexes(table.name)}
            for idx in table.indexes:
                if idx.name not in have_idx:
                    idx.create(conn)
