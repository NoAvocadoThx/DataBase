import os, sys
os.environ.setdefault("ALLOW_INSECURE_SECRET", "1")  # 测试用随机会话密钥
os.environ.setdefault("SECURE_COOKIE", "0")          # TestClient 走 http
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import pytest
from sqlalchemy.orm import sessionmaker
from app.models import Base, make_engine


def make_test_engine(path):
    """默认用 SQLite 临时文件；设了 TEST_DATABASE_URL 就把整套测试跑在 PostgreSQL 上。
    PG 是共享库（不像 SQLite 每个 tmp_path 一个文件），每个 fixture 先 drop_all 再重建，
    保证各测试之间互不干扰。"""
    from app import history  # noqa: F401  让 change_log / access_log 也进 metadata
    url = os.getenv("TEST_DATABASE_URL", "").strip()
    if not url:
        return make_engine(str(path))
    eng = make_engine(url=url)
    Base.metadata.drop_all(eng)
    return eng


@pytest.fixture
def s(tmp_path):
    eng = make_test_engine(tmp_path / "t.db")
    Base.metadata.create_all(eng)
    sess = sessionmaker(bind=eng)()
    yield sess
    sess.close()
