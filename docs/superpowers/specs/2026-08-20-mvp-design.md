# 专家智库 MVP 设计（2026-08-20）

## 范围
M1（档案库）全部 + M2 文档解析审核流。不做向量检索、回收站、操作日志、版本历史、会议独立表、企微/SSO。

## 决策
1. 登录：用户名密码 + itsdangerous 签名 Cookie；pbkdf2 哈希；首次启动自动建 admin/admin123。
2. 角色：admin / planner / intern。intern 看不到手机、邮箱、微信、备注；只有 admin 能导入、导出、合并、管理用户。
3. AI 抽取可插拔：有 `LLM_API_KEY` 走 LLM，否则规则抽取；两者都进审核页，人工确认才入库。
4. 发给 LLM 前对手机/邮箱/微信打码；手机/邮箱本地规则抽。
5. 去重："姓名+单位"全等才更新；仅同名 → 新建 + 写入 `duplicate_candidate`；admin 合并或标记非同人。
6. 每条 AI 录入的专家记录 `source`（文件名）与 `source_text`（原文片段）。
7. SQLite；`scripts/backup.py` 每日复制 db。

## 结构
```
app/
  models.py    ORM: User, Expert, Tag, Participation, Document, DuplicateCandidate
  auth.py      密码哈希、会话、require_role
  importer.py  Excel 导入/导出、去重
  extract.py   文本提取(PDF/Word/PPT/TXT) + LLM/规则抽取 + 打码
  search.py    自然语言拆解 + 打分排序 + 推荐依据
  main.py      路由 + 模板
tests/         pytest
```

## 验收
1. 未登录跳转登录页
2. intern 脱敏且不能导入
3. 导入示例 Excel → 同名张伟进待处理 → 合并成功
4. 上传 PDF → 审核页 → 确认生成专家且带来源
5. 自然语言搜索返回带依据的排序
6. 导出 Excel 可打开
7. pytest 全绿
