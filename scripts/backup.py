"""每日备份：复制 experts.db 与 uploads/ 到 backups/YYYYMMDD/，保留最近 30 天。
Windows 计划任务 / Linux cron 每天调用一次: python scripts/backup.py
"""
import os, shutil, sqlite3, sys
from datetime import datetime, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.getenv("DB_PATH", os.path.join(ROOT, "experts.db"))
OUT = os.path.join(ROOT, "backups", datetime.now().strftime("%Y%m%d"))
KEEP_DAYS = 30

os.makedirs(OUT, exist_ok=True)
# 用 sqlite 在线备份 API，运行中也安全
src = sqlite3.connect(DB)
dst = sqlite3.connect(os.path.join(OUT, "experts.db"))
src.backup(dst)
dst.close(); src.close()
up = os.path.join(ROOT, "uploads")
if os.path.isdir(up):
    shutil.copytree(up, os.path.join(OUT, "uploads"), dirs_exist_ok=True)
# 清理过期
cutoff = (datetime.now() - timedelta(days=KEEP_DAYS)).strftime("%Y%m%d")
for d in os.listdir(os.path.dirname(OUT)):
    if d.isdigit() and d < cutoff:
        shutil.rmtree(os.path.join(os.path.dirname(OUT), d), ignore_errors=True)
print("backup ->", OUT)
