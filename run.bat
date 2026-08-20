@echo off
cd /d %~dp0
rem 本机开发：随机会话密钥 + 允许 HTTP Cookie。生产请改用 .env 中的 SECRET_KEY
set ALLOW_INSECURE_SECRET=1
set SECURE_COOKIE=0
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
