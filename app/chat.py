"""AI 对话：接 DeepSeek（OpenAI 兼容接口），带工具调用查专家库，流式返回。

设计要点
- 配置复用 app/extract.py 的 LLM_URL / LLM_KEY / LLM_MODEL（环境变量），不另起一套。
- 检索逻辑复用 app/search.py，本文件只负责把模型的参数翻译成 search 能懂的 parsed 结构。
- 保密：发给模型的任何内容都不含手机 / 邮箱 / 微信 / 内部备注。
  两道防线：(1) _brief() 只挑白名单字段；(2) 结果 JSON 再过一遍 extract.redact()。
- 成本：固定系统提示词放最前面（命中 DeepSeek 的 prompt 缓存），历史消息超限截断，
  单用户每分钟调用次数有上限。
"""
import json, re, time
from typing import Iterator

import httpx
from sqlalchemy import func
from sqlalchemy.orm import Session, selectinload

from . import extract as ex
from . import search
from .models import Expert, Meeting, Participation, Tag, live

TIMEOUT = 90
MAX_TOOL_ROUNDS = 4          # 一次提问最多让模型查几轮库，防止无限循环烧 token
MAX_HISTORY_MSGS = 12        # 保留的历史对话条数（不含系统提示词），超出丢弃最旧的
MAX_MSG_CHARS = 2000         # 单条用户消息字符上限
MAX_ROWS = 20                # 单次工具调用返回的最大条数（控制 prompt 长度）

RATE_LIMIT, RATE_WINDOW = 10, 60      # 每人每 60 秒最多 10 次
_calls: dict[str, list[float]] = {}

DEFAULT_CLIENT = None    # 测试用注入点：替换成假客户端就不会真的调 API


# ---------------- 频率限制 ----------------
def rate_limited(user: str) -> int:
    """返回还需等待的秒数，0 表示放行。防止误用/脚本刷爆预算。"""
    now = time.time()
    hits = [t for t in _calls.get(user, []) if now - t < RATE_WINDOW]
    _calls[user] = hits
    if len(hits) >= RATE_LIMIT:
        return int(RATE_WINDOW - (now - hits[0])) + 1
    hits.append(now)
    return 0


def reset_rate_limit(user: str = ""):
    _calls.pop(user, None) if user else _calls.clear()


# ---------------- 脱敏 ----------------
# 白名单：只有这些字段可以出境。手机 phone / 邮箱 email / 微信 wechat / 内部备注 note
# / 关注说明 focus_note / 录入原文 source_text 一律不在其中。
SAFE_FIELDS = ("id", "url", "name", "org", "title", "field", "bio", "tags", "focus", "meetings_count")


def _brief(e: Expert) -> dict:
    """专家的"可出境"简介。bio 可能被人手写进联系方式，所以再打一次码。"""
    return {"id": e.id, "url": f"/expert/{e.id}", "name": e.name or "", "org": e.org or "",
            "title": e.title or "", "field": e.field or "",
            "bio": ex.redact((e.bio or "")[:200]),
            "tags": [t.name for t in e.tags], "focus": e.focus_label,
            "meetings_count": len(e.meetings)}


def safe_json(obj) -> str:
    """工具结果 → 发给模型的字符串。最后一道防线：整串再过一次打码。"""
    return ex.redact(json.dumps(obj, ensure_ascii=False))


# ---------------- 工具定义（OpenAI function calling 格式）----------------
TOOLS = [
    {"type": "function", "function": {
        "name": "search_experts",
        "description": "按条件在专家库里找专家。关键词会匹配姓名/单位/职务/研究方向/简介/报告主题。",
        "parameters": {"type": "object", "properties": {
            "keywords": {"type": "array", "items": {"type": "string"},
                         "description": "研究方向或主题关键词，含英文缩写，如 ADC、CAR-T、临床"},
            "org": {"type": "string", "description": "单位名称（模糊匹配）"},
            "title": {"type": "string", "description": "职务/职称关键词，如 主任医师、教授"},
            "tags": {"type": "array", "items": {"type": "string"}, "description": "标签名，需与库中标签一致"},
            "focus_level": {"type": "string", "enum": ["core", "key", "normal", "avoid"],
                            "description": "关注分级：core 核心 / key 重点 / normal 一般 / avoid 不合作"},
            "min_meetings": {"type": "integer", "description": "至少合作过几次会议"},
            "limit": {"type": "integer", "description": "返回条数，默认 10，最多 20"}},
            "required": []}}},
    {"type": "function", "function": {
        "name": "expert_detail",
        "description": "看某一位专家的详情，含全部合作（参会）历史。按 id 或姓名查。",
        "parameters": {"type": "object", "properties": {
            "expert_id": {"type": "integer"},
            "name": {"type": "string", "description": "专家姓名"}},
            "required": []}}},
    {"type": "function", "function": {
        "name": "meeting_participants",
        "description": "看某场会议有哪些专家参加，以及他们的角色和报告主题。不给会议名则列出全部会议。",
        "parameters": {"type": "object", "properties": {
            "meeting": {"type": "string", "description": "会议名称（模糊匹配）"},
            "year": {"type": "integer", "description": "年份"}},
            "required": []}}},
    {"type": "function", "function": {
        "name": "stat_experts",
        "description": "统计类问题。overview=库规模；count=符合关键词/标签的人数；"
                       "top_collaborators=合作次数最多的专家；by_tag=各标签人数；by_year=各年份会议与参会人次。",
        "parameters": {"type": "object", "properties": {
            "metric": {"type": "string", "enum": ["overview", "count", "top_collaborators", "by_tag", "by_year"]},
            "keyword": {"type": "string", "description": "metric=count 时的关键词"},
            "tag": {"type": "string", "description": "metric=count 时的标签名"},
            "limit": {"type": "integer", "description": "返回条数，默认 10"}},
            "required": ["metric"]}}},
]


# ---------------- 工具实现（全部复用 app/search.py 的检索逻辑）----------------
def _cap(v, default: int = 10) -> int:
    try:
        return max(1, min(MAX_ROWS, int(v)))
    except (TypeError, ValueError):
        return default


def t_search_experts(s: Session, keywords=None, org="", title="", tags=None,
                     focus_level="", min_meetings=0, limit=10, **_) -> dict:
    limit = _cap(limit)
    kws = [str(k).strip() for k in (keywords or []) if str(k).strip()]
    tgs = [str(t).strip() for t in (tags or []) if str(t).strip()]
    org, title = str(org or "").strip(), str(title or "").strip()
    try:
        min_meetings = max(0, int(min_meetings or 0))
    except (TypeError, ValueError):
        min_meetings = 0

    if kws or tgs or org:
        parsed = {"keywords": kws, "tags": tgs, "org": org, "need_meeting": min_meetings > 0}
        rows = [e for e, _pts, _r in search.search(s, parsed, limit=200)]   # 复用现有打分排序
    else:
        # 无主题条件（例如"列出所有核心专家"）：走普通查询，不经打分
        rows = (live(s.query(Expert)).options(selectinload(Expert.tags), selectinload(Expert.meetings))
                .order_by(Expert.id).limit(500).all())
    if title:
        rows = [e for e in rows if title.lower() in (e.title or "").lower()]
    if focus_level:
        rows = [e for e in rows if e.focus_level == focus_level]
    if min_meetings:
        rows = [e for e in rows if len(e.meetings) >= min_meetings]
    return {"total": len(rows), "experts": [_brief(e) for e in rows[:limit]]}


def t_expert_detail(s: Session, expert_id=None, name="", **_) -> dict:
    e = None
    if expert_id:
        try:
            e = s.get(Expert, int(expert_id))
        except (TypeError, ValueError):
            e = None
        if e is not None and e.deleted_at:
            e = None
    if e is None and str(name or "").strip():
        nm = str(name).strip()
        hits = live(s.query(Expert)).filter(Expert.name.like(f"%{nm}%")).limit(10).all()
        if not hits:
            return {"error": f"库里没有找到叫“{nm}”的专家"}
        if len(hits) > 1:
            return {"note": f"有 {len(hits)} 位同名/近名专家，请让用户确认是哪一位",
                    "matches": [_brief(x) for x in hits]}
        e = hits[0]
    if e is None:
        return {"error": "请提供 expert_id 或 name"}
    d = _brief(e)
    d["meetings"] = [{"meeting": p.meeting_name, "year": p.year, "role": p.role or "",
                      "topic": p.topic or ""} for p in
                     sorted(e.meetings, key=lambda p: (p.year or 0), reverse=True)]
    return d


def t_meeting_participants(s: Session, meeting="", year=None, **_) -> dict:
    q = s.query(Meeting)
    if str(meeting or "").strip():
        q = q.filter(Meeting.name.like(f"%{str(meeting).strip()}%"))
    if year:
        try:
            q = q.filter(Meeting.year == int(year))
        except (TypeError, ValueError):
            pass
    ms = q.order_by(Meeting.year.desc().nullslast(), Meeting.name).limit(30).all()
    if not ms:
        return {"error": "没有匹配的会议"}
    if len(ms) > 1 and not str(meeting or "").strip():
        return {"meetings": [{"id": m.id, "name": m.name, "year": m.year, "when": m.when,
                              "location": m.location or "", "status": m.status_label,
                              "participants_count": len(m.participations)} for m in ms]}
    out = []
    for m in ms[:3]:
        rows = [p for p in m.participations if p.expert and not p.expert.deleted_at]
        out.append({"id": m.id, "name": m.name, "year": m.year, "when": m.when,
                    "location": m.location or "", "status": m.status_label,
                    "participants_count": len(rows),
                    "participants": [dict(_brief(p.expert), role=p.role or "", topic=p.topic or "")
                                     for p in rows[:50]]})
    return out[0] if len(out) == 1 else {"meetings": out}


def t_stat_experts(s: Session, metric="overview", keyword="", tag="", limit=10, **_) -> dict:
    limit = _cap(limit)
    if metric == "overview":
        return {"专家总数": live(s.query(Expert)).count(),
                "会议总数": s.query(Meeting).count(),
                "参会记录数": s.query(Participation).count(),
                "标签数": s.query(Tag).count()}
    if metric == "count":
        parsed = {"keywords": [keyword] if keyword else [], "tags": [tag] if tag else [],
                  "org": "", "need_meeting": False}
        if not (keyword or tag):
            return {"error": "count 需要 keyword 或 tag"}
        rows = search.search(s, parsed, limit=10000)
        return {"条件": {"关键词": keyword, "标签": tag}, "人数": len(rows),
                "样例": [_brief(e) for e, _p, _r in rows[:5]]}
    if metric == "top_collaborators":
        sub = (s.query(Participation.expert_id, func.count(Participation.id).label("n"))
               .group_by(Participation.expert_id).subquery())
        rows = (live(s.query(Expert, sub.c.n)).join(sub, sub.c.expert_id == Expert.id)
                .options(selectinload(Expert.tags), selectinload(Expert.meetings))
                .order_by(sub.c.n.desc(), Expert.name).limit(limit).all())
        return {"top": [dict(_brief(e), meetings_count=n) for e, n in rows]}
    if metric == "by_tag":
        from .models import expert_tag
        rows = (s.query(Tag.name, func.count(expert_tag.c.expert_id))
                .join(expert_tag, expert_tag.c.tag_id == Tag.id)
                .group_by(Tag.name).order_by(func.count(expert_tag.c.expert_id).desc())
                .limit(limit).all())
        return {"tags": [{"标签": n, "人数": c} for n, c in rows]}
    if metric == "by_year":
        rows = (s.query(Meeting.year, func.count(Meeting.id)).group_by(Meeting.year)
                .order_by(Meeting.year.desc()).limit(limit).all())
        pc = dict(s.query(Participation.year, func.count(Participation.id))
                  .group_by(Participation.year).all())
        return {"years": [{"年份": y, "会议数": c, "参会人次": pc.get(y, 0)} for y, c in rows if y]}
    return {"error": f"不支持的统计口径 {metric}"}


DISPATCH = {"search_experts": t_search_experts, "expert_detail": t_expert_detail,
            "meeting_participants": t_meeting_participants, "stat_experts": t_stat_experts}

TOOL_HINT = {"search_experts": "正在检索专家库…", "expert_detail": "正在查专家档案…",
             "meeting_participants": "正在查会议参会名单…", "stat_experts": "正在统计…"}


def run_tool(s: Session, name: str, args_json: str) -> str:
    """执行一个工具调用，返回可以安全发给模型的字符串。"""
    fn = DISPATCH.get(name)
    if not fn:
        return safe_json({"error": f"未知工具 {name}"})
    try:
        args = json.loads(args_json or "{}")
        if not isinstance(args, dict):
            args = {}
    except ValueError:
        return safe_json({"error": "参数不是合法 JSON"})
    try:
        return safe_json(fn(s, **args))
    except Exception as e:                      # 单个工具出错不该让整轮对话崩掉
        return safe_json({"error": f"查询失败: {type(e).__name__}"})


# ---------------- 系统提示词（固定内容放最前，命中 prompt 缓存）----------------
SYSTEM = """你是「同写意专家智库」的检索助手，帮医药会议主办方的同事在内部专家库里找人、查合作历史、做统计。

工作方式：
1. 凡是涉及专家、会议、数量的问题，必须先调用工具查库，不许凭记忆或想象回答。
2. 工具查不到就直说"库里没有找到"，绝不编造专家姓名、单位或参会记录。
3. 提到任何一位专家时，必须写成 Markdown 链接 [姓名](/expert/{id})，id 用工具返回的 id，方便同事点开核实。
4. 回答用中文，简洁。列人时用短列表，每人一行：链接 + 单位 + 职务 + 一句话推荐理由。
5. 找人时先给 3-5 位最匹配的，别一次铺开几十个。

你看不到也不要索取专家的手机、邮箱、微信和内部备注——这些机密信息不会出现在工具结果里。
用户需要联系方式时，请他点专家链接到详情页自行查看（是否可见由他的权限决定）。"""


def _sanitize(history: list) -> list[dict]:
    """只接受前端传回的 user / assistant 文本消息，并做长度和条数截断。
    不接受 tool / system 角色——否则前端可以伪造"工具结果"骗模型。"""
    out = []
    for m in history or []:
        if not isinstance(m, dict):
            continue
        role, content = m.get("role"), m.get("content")
        if role in ("user", "assistant") and isinstance(content, str) and content.strip():
            out.append({"role": role, "content": content.strip()[:MAX_MSG_CHARS]})
    return out[-MAX_HISTORY_MSGS:]


def build_messages(history: list) -> list[dict]:
    return [{"role": "system", "content": SYSTEM}] + _sanitize(history)


# ---------------- 与模型通信 ----------------
def _post(messages: list[dict], tools: bool, client=None):
    payload = {"model": ex.LLM_MODEL, "temperature": 0.2, "stream": True, "messages": messages}
    if tools:
        payload["tools"] = TOOLS
    c = client or DEFAULT_CLIENT or httpx
    return c.stream("POST", ex.LLM_URL, timeout=TIMEOUT,
                    headers={"Authorization": f"Bearer {ex.LLM_KEY}"}, json=payload)


def _round(messages: list[dict], tools: bool, sink: dict, client=None) -> Iterator[str]:
    """跑一轮流式请求。文本增量 yield 出去，工具调用累积到 sink['tool_calls']。"""
    content, calls = [], {}
    with _post(messages, tools, client) as r:
        r.raise_for_status()
        for line in r.iter_lines():
            line = line.strip() if isinstance(line, str) else line.decode().strip()
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                break
            try:
                delta = json.loads(data)["choices"][0].get("delta") or {}
            except (ValueError, KeyError, IndexError):
                continue
            if delta.get("content"):
                content.append(delta["content"])
                yield delta["content"]
            for tc in delta.get("tool_calls") or []:
                i = tc.get("index", 0)
                slot = calls.setdefault(i, {"id": "", "name": "", "args": ""})
                if tc.get("id"):
                    slot["id"] = tc["id"]
                fn = tc.get("function") or {}
                if fn.get("name"):
                    slot["name"] = fn["name"]
                if fn.get("arguments"):
                    slot["args"] += fn["arguments"]
    sink["content"] = "".join(content)
    sink["tool_calls"] = [calls[i] for i in sorted(calls)]


def converse(s: Session, history: list, client=None) -> Iterator[tuple[str, str]]:
    """对话主循环。yield (类型, 内容)，类型为 delta / tool / error。
    未配置 API key 时优雅降级，返回一句说明而不是抛异常。"""
    if not ex.llm_enabled():
        yield "error", ("未配置大模型（环境变量 LLM_API_KEY 为空），AI 对话暂不可用。"
                        "你仍然可以用顶栏的「找专家」按关键词和标签检索，或在专家列表里按条件筛选。")
        return
    messages = build_messages(history)
    try:
        for _ in range(MAX_TOOL_ROUNDS):
            sink: dict = {}
            yield from (("delta", t) for t in _round(messages, True, sink, client))
            if not sink.get("tool_calls"):
                if not sink.get("content"):
                    yield "error", "大模型没有返回内容，请再试一次。"
                return
            messages.append({"role": "assistant", "content": sink.get("content") or "",
                             "tool_calls": [{"id": c["id"] or f"call_{i}", "type": "function",
                                             "function": {"name": c["name"], "arguments": c["args"]}}
                                            for i, c in enumerate(sink["tool_calls"])]})
            for i, c in enumerate(sink["tool_calls"]):
                yield "tool", TOOL_HINT.get(c["name"], "正在查库…")
                messages.append({"role": "tool", "tool_call_id": c["id"] or f"call_{i}",
                                 "content": run_tool(s, c["name"], c["args"])})
        # 轮数用尽：最后再问一次，这次不给工具，逼它直接作答
        sink = {}
        yield from (("delta", t) for t in _round(messages, False, sink, client))
    except httpx.TimeoutException:
        yield "error", "大模型响应超时（90 秒），请把问题问得更具体一些再试。"
    except httpx.HTTPStatusError as e:
        yield "error", f"大模型接口返回错误（HTTP {e.response.status_code}），请稍后再试或联系管理员。"
    except httpx.HTTPError:
        yield "error", "连接大模型失败，请检查网络或稍后再试。"
    except Exception as e:
        yield "error", f"对话出错（{type(e).__name__}），请稍后再试。"


def sse(kind: str, value: str) -> str:
    return "data: " + json.dumps({"t": kind, "v": value}, ensure_ascii=False) + "\n\n"


def stream_sse(s: Session, history: list, client=None) -> Iterator[str]:
    for kind, val in converse(s, history, client):
        yield sse(kind, val)
    yield sse("done", "")


def first_question(history: list) -> str:
    """取最后一条用户消息，用于写访问日志。"""
    for m in reversed(_sanitize(history)):
        if m["role"] == "user":
            return re.sub(r"\s+", " ", m["content"])[:120]
    return ""
