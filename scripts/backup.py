"""每日备份：把数据库和 uploads/ 复制到 backups/YYYYMMDD/，保留最近 30 天。
Windows 计划任务 / Linux cron 每天调用一次: python scripts/backup.py

两种数据库都支持：
  - 不设 DATABASE_URL  → SQLite 在线备份 API（运行中也安全），产出 experts.db
  - 设了 DATABASE_URL  → 调用 pg_dump -Fc，产出 experts.dump（用 pg_restore 还原）
"""
import os, shutil, sqlite3, subprocess, sys
from datetime import datetime, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

DB = os.getenv("DB_PATH", os.path.join(ROOT, "experts.db"))
DB_URL = os.getenv("DATABASE_URL", "").strip()
OUT = os.path.join(os.getenv("BACKUP_DIR", os.path.join(ROOT, "backups")), datetime.now().strftime("%Y%m%d"))
KEEP_DAYS = 30

os.makedirs(OUT, exist_ok=True)
try:
    os.chmod(os.path.dirname(OUT), 0o700)  # 备份含全部机密名单，仅属主可读
    os.chmod(OUT, 0o700)
except OSError:
    pass


def backup_sqlite():
    # 用 sqlite 在线备份 API，运行中也安全
    src = sqlite3.connect(DB)
    dst = sqlite3.connect(os.path.join(OUT, "experts.db"))
    src.backup(dst)
    dst.close(); src.close()
    return "experts.db"


def backup_postgres():
    """pg_dump 自定义格式（-Fc，带压缩，可用 pg_restore 选择性还原）。
    连接参数走 PGPASSWORD/URL 环境变量，不出现在进程命令行里（ps 能看到命令行）。"""
    from sqlalchemy.engine import make_url
    from app.models import normalize_url
    url = make_url(normalize_url(DB_URL))
    target = os.path.join(OUT, "experts.dump")
    env = dict(os.environ)
    if url.password:
        env["PGPASSWORD"] = url.password
    cmd = ["pg_dump", "-Fc", "-Z", "6", "-f", target,
           "-h", url.host or "localhost", "-p", str(url.port or 5432),
           "-U", url.username or "postgres", url.database]
    try:
        subprocess.run(cmd, check=True, env=env, capture_output=True)
    except FileNotFoundError:
        sys.exit("找不到 pg_dump。容器内请用 postgres 服务执行：\n"
                 "  docker compose exec -T db pg_dump -Fc -U expert experts > 备份.dump")
    except subprocess.CalledProcessError as e:
        sys.exit(f"pg_dump 失败: {e.stderr.decode('utf8', 'replace')[:500]}")
    try:
        os.chmod(target, 0o600)
    except OSError:
        pass
    return "experts.dump"


name = backup_postgres() if DB_URL else backup_sqlite()

up = os.getenv("UPLOAD_DIR", os.path.join(ROOT, "uploads"))
if os.path.isdir(up):
    shutil.copytree(up, os.path.join(OUT, "uploads"), dirs_exist_ok=True)
# 清理过期
cutoff = (datetime.now() - timedelta(days=KEEP_DAYS)).strftime("%Y%m%d")
for d in os.listdir(os.path.dirname(OUT)):
    if d.isdigit() and d < cutoff:
        shutil.rmtree(os.path.join(os.path.dirname(OUT), d), ignore_errors=True)
print(f"backup -> {OUT} ({name})")
