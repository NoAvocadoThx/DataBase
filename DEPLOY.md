# 部署到云服务器

目标：一台 Linux 云服务器（阿里云 / 腾讯云 / 华为云都行），企业的人用浏览器打开一个网址就能用。

## 0. 买服务器

- 配置：2 核 4G、40G 系统盘，Ubuntu 22.04 或 24.04。按量或包月，一个月几十到一百多块
- 地域：选离企业近的（北京/上海/广州）
- 安全组（防火墙）开放端口：**22**（SSH）、**80**（HTTP）、**443**（HTTPS）。**不要**直接开 8000 给公网
- 记下公网 IP，SSH 登录：`ssh root@公网IP`

## 1. 装 Docker（服务器上执行一次）

```bash
apt update && apt install -y docker.io docker-compose-v2 git caddy
systemctl enable --now docker
```

## 2. 拉代码、配置、启动

```bash
cd /opt
git clone https://github.com/NoAvocadoThx/DataBase.git expert
cd expert
cat > .env <<EOF
SECRET_KEY=$(openssl rand -hex 32)
LLM_API_KEY=                     # 有大模型 key 填这里
EOF
docker compose up -d --build
curl -I http://127.0.0.1:8000/login    # 看到 200 即启动成功
```

数据（数据库、上传文件、备份）都在 `/opt/expert/data/`，升级代码不会丢。

## 3. 用 Caddy 做反向代理（自动 HTTPS）

**有域名**（推荐，比如 `expert.公司域名.com` 解析到服务器 IP）：

```bash
cat > /etc/caddy/Caddyfile <<EOF
expert.公司域名.com {
    reverse_proxy 127.0.0.1:8000
}
EOF
systemctl reload caddy
```
Caddy 会自动申请 HTTPS 证书。打开 `https://expert.公司域名.com` 即可。

**没域名，先用 IP 看效果**：

```bash
cat > /etc/caddy/Caddyfile <<EOF
:80 {
    reverse_proxy 127.0.0.1:8000
}
EOF
systemctl reload caddy
```
打开 `http://公网IP`。只有 HTTP，**演示可以，正式用必须上域名+HTTPS**，否则密码和专家手机号是明文传输。

## 4. 每日备份（crontab）

```bash
crontab -e
# 加一行：每天凌晨 3 点
0 3 * * * cd /opt/expert && docker compose exec -T web python scripts/backup.py >> /var/log/expert-backup.log 2>&1
```
备份在 `/opt/expert/data/backups/日期/`。建议再定期把这个目录同步到对象存储（OSS/COS）或另一台机器。

## 5. 更新代码

本地 `git push` 后，服务器上：

```bash
cd /opt/expert && git pull && docker compose up -d --build
```
数据库字段变化会自动迁移，不用手动处理。

## 6. 上线前检查

- [ ] 登录后立刻改掉 admin/admin123
- [ ] `.env` 里 SECRET_KEY 已随机生成（上面的命令已做）
- [ ] 安全组没有开放 8000
- [ ] 有域名 + HTTPS
- [ ] 备份 cron 跑过一次，`data/backups/` 里有文件
- [ ] 如果只允许公司内部访问：安全组把 80/443 的来源限制为公司出口 IP，或让企业用 VPN

## 排错

```bash
docker compose logs -f web        # 看应用日志
docker compose ps                 # 容器状态
systemctl status caddy            # 反向代理状态
```
