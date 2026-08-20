import os, sys
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
