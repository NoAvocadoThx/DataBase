# 同写意专家智库 MVP

内部使用的专家档案库：Excel / 会议资料导入 → AI 抽取 → 人工审核入库 → 按条件或自然语言找专家。

## 运行

```
pip install -r requirements.txt
python -m uvicorn app.main:app --reload        # 或双击 run.bat
```
打开 http://127.0.0.1:8000 ，默认账号 **admin / admin123**，登录后在首页底部修改密码。

可选环境变量（见 `.env.example`）：
- `SECRET_KEY`：会话签名密钥，上线必须改
- `LLM_API_KEY` / `LLM_URL` / `LLM_MODEL`：OpenAI 兼容的大模型接口（默认 DeepSeek）。不设则资料抽取走规则、检索走关键词，功能完整可用

## 功能

| 模块 | 说明 | 权限 |
|---|---|---|
| 专家列表 / 详情 | 关键词、单位、标签筛选；合作历史 | 所有人（实习生手机/邮箱/微信脱敏，看不到备注） |
| 新增 / 编辑专家 | 表单录入，标签自动补全 | 策划、管理员 |
| 资料录入 | 上传 PDF / Word / PPT / TXT → 抽取候选专家 → 审核页逐条确认 → 入库（保存来源文件与原文片段，可同时登记为某会议的合作记录） | 策划、管理员 |
| Excel 导入 / 导出 | 模板见 `sample/专家导入模板示例.xlsx` | 管理员 |
| 疑似重复 | 同名专家自动进队列；一键合并（补空字段、标签并集、合作历史迁移）或标记不同人 | 管理员 |
| 找专家 | 自然语言 → 拆条件 → 打分排序 → 每位专家附推荐依据 | 所有人 |
| 用户管理 | 三级角色 admin / planner / intern | 管理员 |

去重规则：**姓名 + 单位**完全一致视为同一人（更新），仅同名进疑似重复。

AI 安全：发送给大模型前，手机 / 邮箱 / 微信号已正则打码；联系方式在本地用规则抽取。AI 结果一律进审核页，不直接写库。

## 备份

`python scripts/backup.py` 把数据库和上传文件复制到 `backups/日期/`，保留 30 天。加到 Windows 计划任务或 cron 每日执行。

## 测试

```
python -m pytest tests -q
```
`tests/test_core.py` 单元测试（导入去重、合并、脱敏、抽取、检索排序），`tests/test_acceptance.py` 端到端验收（登录 → 导入 → 重复合并 → 权限 → 上传 PDF 审核入库 → 自然语言检索 → 导出 → 编辑删除）。

## 目录

```
app/models.py    数据模型        app/importer.py  Excel 导入导出、去重合并
app/auth.py      登录与权限      app/extract.py   文件转文本、AI/规则抽取、打码
app/search.py    自然语言检索    app/main.py      路由
app/templates/   页面            scripts/backup.py 备份
docs/superpowers/specs/  设计记录    V1-MVP范围确认书.md  给企业的范围文件
```

## 后续（未做）

向量语义检索（pgvector）、会议独立表与 Session、操作日志、回收站、版本历史、企业微信登录、移动端、专家 CRM。
