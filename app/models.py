"""数据模型。"""
import os
from datetime import datetime

from sqlalchemy import (Column, DateTime, ForeignKey, Integer, String, Table, Text,
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
    tags = relationship("Tag", secondary=expert_tag, back_populates="experts")
    meetings = relationship("Participation", back_populates="expert",
                            cascade="all, delete-orphan")

    def searchable_text(self) -> str:
        return " ".join(filter(None, [self.name, self.org, self.title, self.field, self.bio]
                               + [f"{m.meeting} {m.topic or ''}" for m in self.meetings]))


class Tag(Base):
    __tablename__ = "tag"
    id = Column(Integer, primary_key=True)
    name = Column(String(64), unique=True, nullable=False)
    category = Column(String(32), default="专业能力")
    experts = relationship("Expert", secondary=expert_tag, back_populates="tags")


class Participation(Base):
    __tablename__ = "participation"
    id = Column(Integer, primary_key=True)
    expert_id = Column(ForeignKey("expert.id"), nullable=False, index=True)
    meeting = Column(String(128), nullable=False)
    year = Column(Integer)
    role = Column(String(64), default="")
    topic = Column(String(256), default="")
    expert = relationship("Expert", back_populates="meetings")


class Document(Base):
    """上传的资料文件及 AI 抽取结果（待审核）。"""
    __tablename__ = "document"
    id = Column(Integer, primary_key=True)
    filename = Column(String(256), nullable=False)
    path = Column(String(512), nullable=False)
    text = Column(Text, default="")
    extracted_json = Column(Text, default="[]")    # list[dict] 候选专家
    status = Column(String(16), default="pending")  # pending / reviewed
    uploaded_by = Column(String(64), default="")
    created_at = Column(DateTime, default=datetime.now)


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
