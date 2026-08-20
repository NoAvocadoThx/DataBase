FROM python:3.11-slim
WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
COPY scripts ./scripts
# 数据全部放 /srv/data（数据库、上传文件、备份），用卷挂载持久化
ENV DB_PATH=/srv/data/experts.db UPLOAD_DIR=/srv/data/uploads BACKUP_DIR=/srv/data/backups
RUN mkdir -p /srv/data
EXPOSE 8000
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
