# 部署与上线

专家名单含机密个人信息（手机、微信、邮箱、内部备注）。本文分两条路径：

| | 演示环境 | 正式环境 |
|---|---|---|
| 目的 | 给企业点、收反馈、要样本数据 | 日常使用 |
| 访问方式 | 公网 IP + HTTP | 域名 + HTTPS |
| 数据 | **只放假数据**（`scripts/seed_demo.py`） | 真实名单 |
| 前置条件 | 无 | 域名 ICP 备案（1–3 周，企业主体办） |
| 耗时 | 10 分钟 | 备案等待 + 30 分钟 |

> **红线：真实专家名单只能进正式环境（HTTPS）。** 演示环境是明文 HTTP，同一网络能抓包拿到会话和数据。

---

## 路径 A：演示环境（10 分钟）

### A1. 买服务器
- 2 核 4G、40G 盘、Ubuntu 22.04/24.04，离企业近的地域
- 安全组开 **22**（SSH）、**80**（HTTP）
- **不要**开 8000（应用端口已绑定 `127.0.0.1`，只能经反代访问）
- 更严一点：把 80 的来源限制为企业办公网出口 IP

### A2. 装依赖
```bash
apt update && apt install -y docker.io docker-compose-v2 git caddy
systemctl enable --now docker
```

### A3. 部署
```bash
cd /opt
git clone https://github.com/NoAvocadoThx/DataBase.git expert   # 私有库会要 GitHub 账号/token
cd expert
cat > .env <<EOF
SECRET_KEY=$(openssl rand -hex 32)
SECURE_COOKIE=0
LLM_API_KEY=
EOF
chmod 600 .env
docker compose up -d --build
curl -I http://127.0.0.1:8000/login   # 200 = 启动成功
```

`SECURE_COOKIE=0` 是演示环境**专用**：没有 HTTPS 时浏览器不会发送 secure Cookie，不关掉就登录不上。正式环境**必须删掉这一行**。

### A4. 反向代理
```bash
cat > /etc/caddy/Caddyfile <<'EOF'
:80 {
    reverse_proxy 127.0.0.1:8000
}
EOF
systemctl reload caddy
```
打开 `http://公网IP`。

### A5. 灌假数据、建账号
```bash
docker compose exec web python scripts/seed_demo.py 500
```
浏览器登录 admin / admin123 → 立即改密码 → 用户管理里给企业建三个账号（管理员/策划/实习生各一个，让他们体会权限差异）。

### A6. 演示期注意
- 不导入任何真实数据
- 演示结束后：`docker compose down` 停掉，或者直接释放服务器
- 别把演示地址往外发

---

## 路径 B：正式环境

### B1. 域名与备案（最先启动，最慢）
企业用**他们的营业执照**申请域名并做 ICP 备案（主体必须是企业，不是你）。备案期间可以并行做其它准备。域名解析 A 记录指向服务器公网 IP。

### B2. 服务器与安全组
- 安全组开 **22 / 80 / 443**，不开 8000
- 22 端口来源限制为你的固定 IP；条件允许时 80/443 也限制为企业出口 IP
- 建议禁用 SSH 密码登录，改用密钥

### B3. 部署
```bash
cd /opt && git clone https://github.com/NoAvocadoThx/DataBase.git expert && cd expert
cat > .env <<EOF
SECRET_KEY=$(openssl rand -hex 32)
LLM_API_KEY=                     # 企业提供，或留空（走规则抽取）
LLM_URL=https://api.deepseek.com/chat/completions
LLM_MODEL=deepseek-chat
EOF
chmod 600 .env
docker compose up -d --build
```

**不要设 `SECURE_COOKIE`**（默认就是开启，只在 HTTPS 下发送 Cookie）。
**不要设弱 `SECRET_KEY`**：少于 32 字符或缺失，程序会拒绝启动并提示——这是故意的。

### B4. HTTPS
```bash
cat > /etc/caddy/Caddyfile <<'EOF'
expert.公司域名.com {
    reverse_proxy 127.0.0.1:8000
    header {
        Strict-Transport-Security "max-age=31536000"
        X-Content-Type-Options "nosniff"
        X-Frame-Options "DENY"
        Referrer-Policy "same-origin"
    }
}
EOF
systemctl reload caddy
```
Caddy 自动申请并续期证书。打开 `https://expert.公司域名.com` 确认小锁图标。

### B5. 备份
```bash
crontab -e
# 每天凌晨 3 点
0 3 * * * cd /opt/expert && docker compose exec -T web python scripts/backup.py >> /var/log/expert-backup.log 2>&1
```
备份在 `/opt/expert/data/backups/日期/`，脚本已自动设为 `700` 权限（只有属主可读）。**备份里是全部机密名单**：
- 同步到对象存储时开启服务端加密，存储桶设为私有
- 不要下载到个人电脑长期存放；临时排查用完即删

### B6. 导入真实数据
以上全部就绪、确认地址栏是 `https://` 之后，才导入真实名单。

---

## 数据库：SQLite 还是 PostgreSQL

默认用 SQLite（单文件 `data/experts.db`），零运维。**什么时候该换 PostgreSQL：**

| 情况 | 建议 |
|---|---|
| 几个人用、数据几千条 | 继续用 SQLite |
| **同时在线十几人以上**（100 人日活基本一定会撞上） | 换 PostgreSQL |
| 列表页/分面在高峰期明显变慢，日志里出现 `QueuePool limit ... timed out` 或 `database is locked` | 换 PostgreSQL |
| 想做异地只读副本、按小时 PITR 恢复 | 换 PostgreSQL |

实测（3000 条数据、20 并发混合读写，单进程 uvicorn）：SQLite 和 PostgreSQL 差别不大；
换到 **1 万条**（即当前真实数据量）后差距拉开——总耗时 PG 11.9s / SQLite 40.4s，
列表页 p95 PG 2.8s / SQLite 16.4s。原因是 SQLite 的读事务会长时间占住连接，把连接池耗光。

切换靠一个环境变量，代码不用改：

```
DATABASE_URL=postgresql+psycopg://expert:密码@db:5432/experts
```
**不设 `DATABASE_URL` 就还是 SQLite**（继续读 `DB_PATH`），本地开发、跑测试都不受影响。

连接池参数（只对 PostgreSQL 生效，默认值按 100 人日活估的）：
`DB_POOL_SIZE=20`（常驻连接）、`DB_MAX_OVERFLOW=30`（峰值额外连接）、`DB_POOL_TIMEOUT=30`、
`DB_POOL_RECYCLE=1800`。峰值最多占 50 个连接，PostgreSQL 默认 `max_connections=100`，够用。

### 用 docker compose 起 PostgreSQL

```bash
cat >> .env <<'EOF'
POSTGRES_PASSWORD=用 openssl rand -hex 24 生成
POSTGRES_USER=expert
POSTGRES_DB=experts
DATABASE_URL=postgresql+psycopg://expert:上面那个密码@db:5432/experts
EOF
chmod 600 .env
docker compose --profile pg up -d --build
docker compose ps            # db 要是 healthy
```

- `db` 服务放在 `pg` profile 里：**不加 `--profile pg` 就还是原来的单容器 SQLite 跑法**，两种方式都保留。
- `db` 不映射端口，只有 compose 网络内的 `web` 能连，公网碰不到。
- 数据在命名卷 `pgdata` 里，`docker compose down` 不会删；真要删得 `docker volume rm`。
- 建库时带了 `--lc-collate=C`：让中文和英文按码位排序，**与 SQLite 一致**，否则表头按姓名/单位排序的结果两边会不一样。

### 把现有 SQLite 数据搬过去

```bash
docker compose --profile pg up -d db          # 只起数据库
docker compose run --rm web python scripts/migrate_to_pg.py     --sqlite /srv/data/experts.db     --pg "postgresql+psycopg://expert:密码@db:5432/experts"
```

脚本会把 **专家、标签、专家-标签关联、会议、合作记录、分组、分组成员、用户、疑似重复、
上传文档、修改历史 change_log、访问日志 access_log** 全部搬过去，**保留原来的主键 ID 和外键关系**，
最后打印每张表源库/目标库的条数对比，对不上会以退出码 1 结束。

- **幂等**：按主键判断，目标库已有同 ID 的行直接跳过。重复跑只会打印"跳过 N 行"，不会重复插入。
- 迁完会重置各表的自增序列（`setval`），否则应用新增第一条就会撞主键冲突。
- 文档正文里的 NUL 字节（`\x00`，PDF 抽取偶尔会带）会被去掉——PostgreSQL 的 text 类型存不了。
- 迁完再改 `.env` 里的 `DATABASE_URL`，然后 `docker compose --profile pg up -d`。
- **先别删 SQLite 文件**，跑一周确认没问题再归档。

### 备份（PostgreSQL）

`scripts/backup.py` 自动识别：设了 `DATABASE_URL` 就用 `pg_dump -Fc`（自定义格式、带压缩），
输出 `backups/日期/experts.dump`，权限 600；没设就还是 SQLite 在线备份。cron 不用改：

```bash
0 3 * * * cd /opt/expert && docker compose exec -T web python scripts/backup.py >> /var/log/expert-backup.log 2>&1
```

手工备份 / 还原：
```bash
# 备份
docker compose exec -T db pg_dump -Fc -U expert experts > 专家库_$(date +%F).dump
# 还原到空库（-c 先删同名对象）
docker compose exec -T db pg_restore -c -U expert -d experts < 专家库_2026-08-20.dump
# 只看内容不还原
docker compose exec -T db pg_restore -l < 专家库_2026-08-20.dump
```
备份文件里是全部机密名单，异地副本必须加密、存储桶设私有，参照 B5 的要求。

---

## 上线检查清单（逐项打勾，全绿再交付）

**代码与配置**
- [ ] GitHub 仓库为 **Private**
- [ ] `.env` 里 `SECRET_KEY` 由 `openssl rand -hex 32` 生成，权限 600
- [ ] 正式环境**没有** `SECURE_COOKIE=0`
- [ ] `docker compose config` 检查端口是 `127.0.0.1:8000:8000`

**访问控制**
- [ ] admin 默认密码 `admin123` 已改
- [ ] 每人一个账号，按最小权限给角色（能只读就给实习生）
- [ ] 离职/换岗立即在用户管理里删号（旧会话会立刻失效）
- [ ] 安全组未开放 8000；22 端口来源受限

**传输与存储**
- [ ] 域名已备案，HTTPS 生效，浏览器显示小锁
- [ ] 备份 cron 跑过一次，`data/backups/` 里有文件且权限 700（PG 模式下是 `experts.dump`）
- [ ] 备份异地副本已加密
- [ ] 用 PostgreSQL 时：`.env` 里 `POSTGRES_PASSWORD` 已设且非弱口令；`docker compose config` 确认 `db` 服务没有映射端口
- [ ] 用 PostgreSQL 时：`migrate_to_pg.py` 的条数对比全 OK，且抽查几位专家的标签/合作记录/分组都在

**大模型**
- [ ] 与企业书面确认：可否调用第三方大模型 API
- [ ] 若使用：确认发送前已脱敏（手机/邮箱/微信打码，代码已实现）
- [ ] `LLM_API_KEY` 只写在 `.env`，不进 git

**验证（部署后实测一遍）**
- [ ] 未登录访问首页 → 跳转登录页
- [ ] 用实习生账号登录 → 看不到完整手机号、看不到内部备注、进不了导入/导出/用户管理
- [ ] 连错 8 次密码 → 被锁定 5 分钟
- [ ] 导出一次 Excel → 操作历史里出现"导出全库"记录
- [ ] 删除一位测试专家 → 回收站里能恢复

---

## 日常运维

**更新代码**
```bash
cd /opt/expert && git pull && docker compose up -d --build
```
数据库字段变化会自动迁移，数据在 `data/` 卷里不受影响。

**排错**
```bash
docker compose logs -f web     # 应用日志
docker compose logs -f db      # 数据库日志（PG 模式）
docker compose ps              # 容器状态
systemctl status caddy         # 反代与证书
```

**忘记密码**：管理员在"用户"页重置；管理员自己忘了，进容器执行：
```bash
docker compose exec web python -c "
from app.models import SessionLocal, User
from app.auth import hash_password
s = SessionLocal(); u = s.query(User).filter_by(username='admin').one()
u.password_hash = hash_password('新密码'); s.commit(); print('已重置')"
```

## 安全设计说明（给企业 IT 看的）

- **认证**：用户名+密码，密码用 PBKDF2-SHA256 200,000 轮加盐哈希存储，不可逆
- **会话**：签名 Cookie，HttpOnly + Secure + SameSite=Lax，12 小时过期；改角色或改密码后旧会话立即失效
- **权限**：三级角色，敏感字段（手机/微信/邮箱/内部备注/录入原文）对实习生脱敏；导入、导出、用户管理、回收站仅管理员
- **审计**：每次新增、修改（字段级旧值→新值）、删除、恢复、合并、导出都有记录，可按操作人/类型/日期筛选
- **删除**：软删除进回收站，可恢复；彻底删除需二次确认，操作历史仍保留
- **数据出境**：调用第三方大模型前，手机、邮箱、微信号已正则打码；联系方式在本地用规则抽取，不经过模型
- **传输**：全站 HTTPS，HSTS 一年
- **备份**：每日全量，保留 30 天，目录权限 700

> 本系统由 AI 辅助扫描做过一轮安全审计并修复了发现的问题，但不等同于专业渗透测试。处理机密个人信息的系统，建议正式运行前再请第三方做一次安全测评。
