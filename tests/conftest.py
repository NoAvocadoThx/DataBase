import os, sys
os.environ.setdefault("ALLOW_INSECURE_SECRET", "1")  # 测试用随机会话密钥
os.environ.setdefault("SECURE_COOKIE", "0")          # TestClient 走 http
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import pytest
from sqlalchemy.orm import sessionmaker
from app.models import Base, make_engine

@pytest.fixture
def s(tmp_path):
    eng = make_engine(str(tmp_path / "t.db"))
    Base.metadata.create_all(eng)
    sess = sessionmaker(bind=eng)()
    yield sess
    sess.close()
