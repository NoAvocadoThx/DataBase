"""AI 对话：接 DeepSeek（OpenAI 兼容接口），带工具调用查专家库，流式返回。

设计要点
- 配置复用 app/extract.py 的 LLM_URL / LLM_KEY / LLM_MODEL（环境变量），不另起一套。
- 检索逻辑复用 app/search.py，本文件只负责把模型的参数翻译成 search 能懂的 parsed 结构。
- 保密：发给模型的任何内容都不含手机 / 邮箱 / 微信 / 内部备注。
  两道防线：(1) _brief() 只挑白名单字段；(2) 结果 JSON 再过一遍 extract.redact()。
- 防编造：回答里的每个 /expert/{id} 都要在本轮工具结果里出现过，否则摘掉链接并当面警示。
- 话题限定：调用前先做一次零成本的规则判断，明显无关的问题直接拒答，不发给大模型。
- 成本：固定系统提示词放最前面（命中 DeepSeek 的 prompt 缓存），历史消息超限截断，
  按角色限流，每次调用记录 token 用量并估算花费。
"""
import json, re, time
from datetime import date, datetime
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

DEFAULT_CLIENT = None        # 测试用注入点：替换成假客户端就不会真的调 API


# ---------------- 单价与成本 ----------------
# 元 / 百万 token。**价格以 DeepSeek 官网当日为准，变价时只改这三行。**
# 缓存命中的输入远便宜于未命中，所以必须分开算，否则成本估不准。
PRICE_CACHE_HIT = 0.5
PRICE_CACHE_MISS = 2.0
PRICE_OUTPUT = 8.0

EMPTY_USAGE = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0,
               "cache_hit_tokens": 0, "cache_miss_tokens": 0}
# DeepSeek 的字段名叫 prompt_cache_hit_tokens / prompt_cache_miss_tokens，
# 别的 OpenAI 兼容网关可能给 prompt_tokens_details.cached_tokens，两种都认。
CACHE_KEYS = {"cache_hit_tokens": ("prompt_cache_hit_tokens", "cache_hit_tokens"),
              "cache_miss_tokens": ("prompt_cache_miss_tokens", "cache_miss_tokens")}


def norm_usage(u: dict) -> dict:
    """把接口返回的 usage 归一化成内部字段名。"""
    out = dict(EMPTY_USAGE)
    if not isinstance(u, dict):
        return out
    for k in ("prompt_tokens", "completion_tokens", "total_tokens"):
        out[k] = int(u.get(k) or 0)
    for mine, theirs in CACHE_KEYS.items():
        for name in theirs:
            if u.get(name) is not None:
                out[mine] = int(u[name] or 0)
                break
    det = u.get("prompt_tokens_details") or {}
    if not out["cache_hit_tokens"] and isinstance(det, dict) and det.get("cached_tokens"):
        out["cache_hit_tokens"] = int(det["cached_tokens"])
    if out["cache_hit_tokens"] and not out["cache_miss_tokens"]:
        out["cache_miss_tokens"] = max(0, out["prompt_tokens"] - out["cache_hit_tokens"])
    if not out["total_tokens"]:
        out["total_tokens"] = out["prompt_tokens"] + out["completion_tokens"]
    return out


def estimate_cost(u: dict) -> float:
    """按 token 估算这次提问花了多少钱（元）。
    接口没返回缓存字段时，把输入全算成未命中——宁可估贵，不要低估预算。"""
    hit = u.get("cache_hit_tokens") or 0
    miss = u.get("cache_miss_tokens") or 0
    if not hit and not miss:
        miss = u.get("prompt_tokens") or 0
    out = u.get("completion_tokens") or 0
    return round((hit * PRICE_CACHE_HIT + miss * PRICE_CACHE_MISS + out * PRICE_OUTPUT) / 1_000_000, 6)


def add_usage(acc: dict, u: dict) -> dict:
    """把一轮的 usage 累加进总账（一次提问可能跨多轮工具调用）。"""
    for k in EMPTY_USAGE:
        acc[k] = (acc.get(k) or 0) + (u.get(k) or 0)
    return acc


# ---------------- 频率限制（按角色）----------------
# 管理员不限；策划给足；实习生保持较低。企业要调就改这一处。
RATE_LIMITS = {
    "admin":   {"minute": 0,   "day": 0},     # 0 = 不限
    "planner": {"minute": 30,  "day": 200},
    "intern":  {"minute": 10,  "day": 60},
}
DEFAULT_LIMIT = RATE_LIMITS["intern"]         # 角色不认识时按最严的算
RATE_WINDOW = 60

_calls: dict[str, list[float]] = {}           # 每分钟滑动窗口
_daily: dict[str, tuple[str, int]] = {}       # user -> (日期, 当日次数)


def limits_for(role: str) -> dict:
    return RATE_LIMITS.get(role, DEFAULT_LIMIT)


def rate_limited(user: str, role: str = "intern") -> str:
    """返回拒绝提示语；空串表示放行。防止误用或脚本刷爆预算。"""
    lim = limits_for(role)
    now, today = time.time(), date.today().isoformat()
    if lim.get("day"):
        d, n = _daily.get(user, (today, 0))
        if d != today:
            d, n = today, 0
        if n >= lim["day"]:
            _daily[user] = (d, n)
            return f"今天的提问次数已用完（每天最多 {lim['day']} 次），请明天再试或联系管理员。"
        _daily[user] = (d, n + 1)
    if lim.get("minute"):
        hits = [t for t in _calls.get(user, []) if now - t < RATE_WINDOW]
        _calls[user] = hits
        if len(hits) >= lim["minute"]:
            wait = int(RATE_WINDOW - (now - hits[0])) + 1
            return f"每分钟最多 {lim['minute']} 次提问，请稍后再试（约 {wait} 秒后恢复）。"
        hits.append(now)
    return ""


def reset_rate_limit(user: str = ""):
    if user:
        _calls.pop(user, None)
        _daily.pop(user, None)
    else:
        _calls.clear()
        _daily.clear()


# ---------------- 话题限定（调用前的零成本判断）----------------
# 思路：先看有没有本领域的词，有就直接放行（这样"帮我给这位专家写封邀请函""把他的简介
# 翻译一下"这类沾边需求不会被误杀）；只有在一个领域词都没有、且明确命中无关模式时才拒答。
# 原则是**宁可放过，不要误拒**——放过一条最多多花一分钱，误拒一条同事就不信这个工具了。
DOMAIN_WORDS = (
    "专家", "教授", "医生", "医师", "院士", "学者", "老师", "讲者", "报告人", "嘉宾", "主持",
    "会议", "年会", "论坛", "峰会", "研讨", "大会", "参会", "参加过", "出席", "合作",
    "单位", "机构", "医院", "大学", "学院", "研究所", "研究院", "药企", "公司", "科室",
    "研究方向", "方向", "领域", "标签", "分组", "名单", "邀请", "关注", "分级", "核心", "重点",
    "库里", "数据库", "专家库", "几位", "多少位", "多少人", "排名", "统计", "检索", "筛选",
    "找人", "找几", "履历", "简介", "职务", "职称", "谁参加", "有谁",
    "adc", "car-t", "cart", "pd-1", "pd-l1", "mrna", "protac", "crispr", "cmc", "gcp",
    "临床", "药物", "新药", "肿瘤", "免疫", "疫苗", "双抗", "抗体", "靶点", "细胞治疗",
    "基因治疗", "注册", "审评", "药理", "真实世界", "罕见病", "生物统计", "医药", "制药",
)

OFF_TOPIC = [
    re.compile(r"写(一)?(段|个|点|些)?(代码|程序|脚本|函数)|python|javascript|c\+\+|golang"
               r"|sql\s*语句|正则表达式|报错|堆栈|debug|编译|leetcode|算法题"),
    re.compile(r"翻译成|译成(英|中|日|法)文|translate\b|英译|中译"),
    re.compile(r"天气|气温|下雨|台风|雾霾|股票|股价|大盘|基金|彩票|汇率|比分|球赛|世界杯"),
    re.compile(r"你是(什么|哪个|哪家)?(模型|ai|人工智能|机器人)|你是谁|你叫什么"
               r"|chatgpt|gpt-?[0-9]|通义|文心|kimi|你的(训练|参数|版本|提示词)"),
    re.compile(r"讲(个|一个)?笑话|陪我聊|聊聊天|唱首歌|写首诗|菜谱|做菜|减肥|健身"
               r"|旅游|攻略|电影|电视剧|游戏|星座|算命"),
    re.compile(r"写(一)?(篇|份)?(作文|小说|散文|文案|周报|日报|年终总结|演讲稿|检讨|朋友圈|简历)"),
    re.compile(r"今天(是)?(几号|星期几)|现在几点|明天(是)?星期"),
    re.compile(r"^[\s\d\+\-\*/×÷\(\)\.=]+$"),
]

REFUSE = ("我只负责这个专家库，帮不上这个忙。可以问我这类问题：\n\n"
          "- 帮我找 3 位做 ADC 临床的专家\n"
          "- 张伟参加过哪些会议\n"
          "- 做双抗的专家一共有多少位\n"
          "- 2026 年会都有谁参加\n\n"
          "（这条问题没有发送给大模型，不产生费用。）")


def off_topic(q: str) -> str:
    """明显与专家库无关时返回拒答文案；空串表示放行。"""
    low = (q or "").strip().lower()
    if not low:
        return ""
    if any(w in low for w in DOMAIN_WORDS):
        return ""
    for pat in OFF_TOPIC:
        if pat.search(low):
            return REFUSE
    return ""


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

ID_IN_RESULT = re.compile(r"/expert/(\d+)")


def run_tool(s: Session, name: str, args_json: str) -> tuple[str, set[str]]:
    """执行一个工具调用。返回 (可安全发给模型的字符串, 结果里出现过的专家 id)。
    这些 id 就是"模型有资格引用"的白名单，用于事后核对它有没有编造。"""
    fn = DISPATCH.get(name)
    if not fn:
        return safe_json({"error": f"未知工具 {name}"}), set()
    try:
        args = json.loads(args_json or "{}")
        if not isinstance(args, dict):
            args = {}
    except ValueError:
        return safe_json({"error": "参数不是合法 JSON"}), set()
    try:
        out = safe_json(fn(s, **args))
    except Exception as e:                      # 单个工具出错不该让整轮对话崩掉
        return safe_json({"error": f"查询失败: {type(e).__name__}"}), set()
    return out, set(ID_IN_RESULT.findall(out))


# ---------------- 防编造校验 ----------------
LINK_RE = re.compile(r"\[([^\]\n]{1,60})\]\(/expert/(\d+)\)")
BARE_RE = re.compile(r"/expert/(\d+)")
FAKE_MARK = "（⚠ 库中无此记录，链接已移除）"
FAKE_WARN = "⚠ 上面标注的条目在专家库里查不到，是模型自己编的，请勿采信；其余带链接的条目可以点开核实。"


def verify_answer(answer: str, allowed: set[str]) -> tuple[str, list[str]]:
    """把回答里"不在本轮工具结果内"的专家链接摘掉并当面标注。
    返回 (处理后的回答, 编造的 id 列表)。没编造时原样返回。"""
    fake: list[str] = []

    def fix_link(m):
        if m.group(2) in allowed:
            return m.group(0)
        fake.append(m.group(2))
        return f"{m.group(1)}{FAKE_MARK}"       # 只留姓名，链接不给出去

    fixed = LINK_RE.sub(fix_link, answer or "")

    def fix_bare(m):                            # 没写成 Markdown 的裸链接同样处理
        if m.group(1) in allowed:
            return m.group(0)
        fake.append(m.group(1))
        return FAKE_MARK

    fixed = BARE_RE.sub(fix_bare, fixed)
    return fixed, sorted(set(fake), key=int)


# ---------------- 系统提示词（固定内容放最前，命中 prompt 缓存）----------------
SYSTEM = """你是「同写意专家智库」的检索助手，帮医药会议主办方的同事在内部专家库里找人、查合作历史、做统计。

职责范围（严格遵守）：
只回答与本专家库有关的问题——找专家、看某位专家的档案与合作/参会历史、查某场会议的参会名单、
按方向或标签做人数统计。除此之外的一切请求（写代码、翻译、写文案、闲聊、常识问答、时事、
数学题、问你是什么模型等）一律礼貌拒绝，只回一句"我只能帮您查专家库相关的问题，比如找专家、
看某人的合作历史、统计某个方向有多少人"，不要展开，也不要顺带回答。

工作方式：
1. 凡是涉及专家、会议、数量的问题，必须先调用工具查库，不许凭记忆或想象回答。
2. 工具查不到就直说"库里没有找到"，绝不编造专家姓名、单位或参会记录。
3. 提到任何一位专家时，必须写成 Markdown 链接 [姓名](/expert/{id})，id 只能用工具真实返回过的 id。
   系统会逐个核对，编造的 id 会被当场标出并移除链接，请不要冒这个险。
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
    payload = {"model": ex.LLM_MODEL, "temperature": 0.2, "stream": True, "messages": messages,
               # 流式默认不返回 usage，必须显式要；它出现在最后一个 chunk（该 chunk 的 choices 是空数组）
               "stream_options": {"include_usage": True}}
    if tools:
        payload["tools"] = TOOLS
    c = client or DEFAULT_CLIENT or httpx
    return c.stream("POST", ex.LLM_URL, timeout=TIMEOUT,
                    headers={"Authorization": f"Bearer {ex.LLM_KEY}"}, json=payload)


def _round(messages: list[dict], tools: bool, sink: dict, client=None) -> Iterator[str]:
    """跑一轮流式请求。文本增量 yield 出去，工具调用和 usage 累积到 sink。"""
    content, calls, usage = [], {}, dict(EMPTY_USAGE)
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
                obj = json.loads(data)
            except ValueError:
                continue
            if isinstance(obj.get("usage"), dict):          # 收尾 chunk 带的用量
                add_usage(usage, norm_usage(obj["usage"]))
            choices = obj.get("choices") or []
            if not choices:                                 # usage chunk 的 choices 是空数组
                continue
            delta = choices[0].get("delta") or {}
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
    sink["usage"] = usage


def new_result() -> dict:
    return {"answer": "", "tools": [], "expert_ids": [], "fabricated": [], "blocked": "",
            "rounds": 0, "usage": dict(EMPTY_USAGE), "cost": 0.0}


def converse(s: Session, history: list, client=None, result: dict | None = None,
             skip_gate: bool = False) -> Iterator[tuple[str, str]]:
    """对话主循环。yield (类型, 内容)，类型为 delta / tool / fix / warn / refuse / error。
    result 是出参：跑完后带着回答、用量、编造情况，供路由层存档。"""
    res = result if result is not None else {}
    for k, v in new_result().items():
        res.setdefault(k, v)

    if not ex.llm_enabled():
        res["blocked"] = "no_key"
        res["answer"] = ("未配置大模型（环境变量 LLM_API_KEY 为空），AI 对话暂不可用。"
                         "你仍然可以用顶栏的「找专家」按关键词和标签检索，或在专家列表里按条件筛选。")
        yield "error", res["answer"]
        return

    if not skip_gate and (refusal := off_topic(first_question(history))):
        res["blocked"] = "offtopic"             # 压根没发给大模型，零成本
        res["answer"] = refusal
        yield "refuse", refusal
        return

    messages = build_messages(history)
    allowed: set[str] = set()
    answer_parts: list[str] = []
    try:
        for _ in range(MAX_TOOL_ROUNDS):
            sink: dict = {}
            for piece in _round(messages, True, sink, client):
                answer_parts.append(piece)
                yield "delta", piece
            res["rounds"] += 1
            add_usage(res["usage"], sink.get("usage") or {})
            if not sink.get("tool_calls"):
                if not sink.get("content"):
                    yield "error", "大模型没有返回内容，请再试一次。"
                break
            messages.append({"role": "assistant", "content": sink.get("content") or "",
                             "tool_calls": [{"id": c["id"] or f"call_{i}", "type": "function",
                                             "function": {"name": c["name"], "arguments": c["args"]}}
                                            for i, c in enumerate(sink["tool_calls"])]})
            for i, c in enumerate(sink["tool_calls"]):
                yield "tool", TOOL_HINT.get(c["name"], "正在查库…")
                res["tools"].append(c["name"])
                out, ids = run_tool(s, c["name"], c["args"])
                allowed |= ids
                messages.append({"role": "tool", "tool_call_id": c["id"] or f"call_{i}", "content": out})
        else:
            # 轮数用尽：最后再问一次，这次不给工具，逼它直接作答
            sink = {}
            for piece in _round(messages, False, sink, client):
                answer_parts.append(piece)
                yield "delta", piece
            res["rounds"] += 1
            add_usage(res["usage"], sink.get("usage") or {})
    except httpx.TimeoutException:
        yield "error", "大模型响应超时（90 秒），请把问题问得更具体一些再试。"
    except httpx.HTTPStatusError as e:
        yield "error", f"大模型接口返回错误（HTTP {e.response.status_code}），请稍后再试或联系管理员。"
    except httpx.HTTPError:
        yield "error", "连接大模型失败，请检查网络或稍后再试。"
    except Exception as e:
        yield "error", f"对话出错（{type(e).__name__}），请稍后再试。"

    raw = "".join(answer_parts)
    fixed, fake = verify_answer(raw, allowed)
    res["answer"] = fixed
    res["fabricated"] = fake
    res["expert_ids"] = sorted(set(BARE_RE.findall(fixed)), key=int)
    res["cost"] = estimate_cost(res["usage"])
    if fake:
        # 流式已经把原文吐出去了，这里让前端整段替换成核对过的版本，并挂一条警示
        yield "fix", fixed
        yield "warn", FAKE_WARN


# ---------------- SSE ----------------
def sse(kind: str, value: str) -> str:
    return "data: " + json.dumps({"t": kind, "v": value}, ensure_ascii=False) + "\n\n"


def stream_sse(s: Session, history: list, client=None, result: dict | None = None) -> Iterator[str]:
    for kind, val in converse(s, history, client, result):
        yield sse(kind, val)
    yield sse("done", "")


def first_question(history: list) -> str:
    """取最后一条用户消息，用于写日志和话题判断。"""
    for m in reversed(_sanitize(history)):
        if m["role"] == "user":
            return re.sub(r"\s+", " ", m["content"])[:500]
    return ""


# ---------------- 存档 ----------------
def archive(s: Session, actor: str, question: str, res: dict, ip: str = ""):
    """把一次问答完整存进 chat_archive（仅管理员可查）。"""
    from .history import ChatArchive
    u = res.get("usage") or {}
    s.add(ChatArchive(
        actor=actor or "", question=(question or "")[:4000], answer=(res.get("answer") or "")[:20000],
        tools=",".join(dict.fromkeys(res.get("tools") or []))[:256],
        expert_ids=",".join(res.get("expert_ids") or [])[:256],
        fabricated=",".join(res.get("fabricated") or [])[:256],
        blocked=(res.get("blocked") or "")[:32], ip=(ip or "")[:64],
        rounds=res.get("rounds") or 0,
        prompt_tokens=u.get("prompt_tokens") or 0,
        completion_tokens=u.get("completion_tokens") or 0,
        total_tokens=u.get("total_tokens") or ((u.get("prompt_tokens") or 0) + (u.get("completion_tokens") or 0)),
        cache_hit_tokens=u.get("cache_hit_tokens") or 0,
        cache_miss_tokens=u.get("cache_miss_tokens") or 0,
        cost=res.get("cost") or 0.0))


def today_start() -> datetime:
    t = datetime.now()
    return datetime(t.year, t.month, t.day)


def month_start() -> datetime:
    t = datetime.now()
    return datetime(t.year, t.month, 1)
