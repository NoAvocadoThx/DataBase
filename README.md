# 同写意专家智库 MVP

## 运行
```
pip install -r requirements.txt
uvicorn app.main:app --reload
```
打开 http://127.0.0.1:8000 ，右上角切换角色（admin / planner / intern）。

## 功能
- Excel 批量导入（sample/专家导入模板示例.xlsx 为模板），姓名+单位去重
- 专家列表：关键词 / 单位 / 标签 筛选
- 专家详情：标签维护、合作历史录入
- 自然语言找专家：带"推荐依据"。配置环境变量后用大模型拆解问题：
  `LLM_API_KEY=sk-xxx`（默认 DeepSeek，可用 `LLM_URL`/`LLM_MODEL` 换成任意 OpenAI 兼容接口）
- 三级角色：实习生看不到手机/邮箱/微信/备注，只有管理员能导入

## 下一步（按优先级）
1. 真正的登录（用户名密码）替代 ?role=
2. PDF/Word/PPT 上传 → LLM 抽取 → 审核页 → 入库（含来源溯源）
3. AI 自动打标签（进审核队列，不直接入库）
4. 向量语义检索（pgvector），迁移到 PostgreSQL
5. 每日自动备份
