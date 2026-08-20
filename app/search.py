"""自然语言找专家：拆条件 → 打分 → 推荐依据。"""
import json, re

import httpx
from sqlalchemy.orm import Session

from . import extract as ex
from .models import Expert, Participation, Tag, live

STOP = set("的 和 与 及 或 找 请 帮我 帮 我 一下 专家 老师 推荐 几位 几个 做 从事 研究 方向 领域 有 没有 过 参加 参与 我们 会议 的人".split())


PREFIX = ("帮我找", "帮我", "请找", "找做", "找", "做", "从事", "研究", "推荐", "寻找", "有没有", "需要", "想要", "要")
SUFFIX = ("的专家", "专家", "老师", "教授", "方向的", "方向", "领域的", "领域", "相关的", "相关", "的人", "的")


def _clean(p: str) -> str:
    changed = True
    while changed and p:
        changed = False
        for w in sorted(PREFIX, key=len, reverse=True):
            if p.startswith(w) and len(p) > len(w):
                p, changed = p[len(w):], True
        for w in sorted(SUFFIX, key=len, reverse=True):
            if p.endswith(w) and len(p) > len(w):
                p, changed = p[:-len(w)], True
    return p


def _keywords(q: str) -> list[str]:
    parts = re.split(r"[\s，,。、；;？?！!的和与及]+", q)
    out = []
    for p in parts:
        p = _clean(p.strip())
        if any(w in p for w in ("参加过", "参会", "合作过", "我们会议", "我们的会议")):
            continue  # 交给 need_meeting 处理
        if len(p) >= 2 and p not in STOP and p not in out:
            out.append(p)
    # 英文缩写单独拿出来 (ADC / CAR-T / PD-1)
    out += [w for w in re.findall(r"[A-Za-z][A-Za-z0-9-]{1,}", q) if w not in out]
    return out


def parse_query(q: str, all_tags: list[str]) -> dict:
    base = {"keywords": _keywords(q), "tags": [t for t in all_tags if t and t in q],
            "org": "", "need_meeting": any(w in q for w in ("参加过", "参会", "合作过", "我们会议")),
            "explain": "关键词匹配（未配置大模型）"}
    if not ex.llm_enabled():
        return base
    prompt = f"""你是专家库检索助手。把用户问题拆成 JSON，不要输出其他内容:
{{"keywords": [研究方向/主题关键词，含英文缩写], "tags": [只能从给定标签中选], "org": "单位名或空串",
 "need_meeting": 是否要求参加过我们的会议(true/false), "explain": "一句话复述需求"}}
可选标签: {all_tags}
用户问题: {q}"""
    try:
        r = httpx.post(ex.LLM_URL, timeout=30, headers={"Authorization": f"Bearer {ex.LLM_KEY}"},
                       json={"model": ex.LLM_MODEL, "temperature": 0,
                             "messages": [{"role": "user", "content": prompt}]})
        d = json.loads(re.search(r"\{.*\}", r.json()["choices"][0]["message"]["content"], re.S).group())
        d.setdefault("keywords", []), d.setdefault("tags", []), d.setdefault("org", "")
        d.setdefault("need_meeting", False), d.setdefault("explain", "")
        d["tags"] = [t for t in d["tags"] if t in all_tags]
        return d
    except Exception as e:
        base["explain"] = f"模型解析失败({type(e).__name__})，改用关键词匹配"
        return base


def score(e: Expert, parsed: dict) -> tuple[int, list[str]]:
    reasons, pts = [], 0
    hay = e.searchable_text()
    for k in parsed.get("keywords", []):
        if k and k.lower() in hay.lower():
            where = "研究方向" if k.lower() in (e.field or "").lower() else \
                    "报告主题" if any(k.lower() in (m.topic or "").lower() for m in e.meetings) else "资料"
            reasons.append(f"{where}提及“{k}”")
            pts += 2
    names = {t.name for t in e.tags}
    for t in parsed.get("tags", []):
        if t in names:
            reasons.append(f"标签“{t}”")
            pts += 3
    org = parsed.get("org") or ""
    if org and e.org and org in e.org:
        reasons.append(f"单位“{e.org}”")
        pts += 2
    topical = bool(parsed.get("keywords") or parsed.get("tags") or org)
    if parsed.get("need_meeting"):
        if e.meetings:
            if reasons or not topical:  # 有主题条件时，仅凭"参加过会议"不算命中
                reasons.append(f"参加过 {len(e.meetings)} 次会议")
                pts += 2
        else:
            pts -= 1
    return pts, reasons


PREFILTER = 1000  # SQL 粗排后进入精算的条数上限（是返回条数的 20 倍，留足余量避免边界并列被截断）


def score_sql(parsed: dict):
    """在 SQL 里算一个"乐观分"用于粗排：权重与 Python score() 一致，但不扣分、不要求
    其他命中，保证 SQL 分 >= 精算分，粗排不会漏掉本该入选的人。返回 None 表示无条件可用。"""
    from sqlalchemy import case, func, or_, select
    from .models import expert_tag
    terms = []
    for k in parsed.get("keywords", []):
        if not k:
            continue
        like = f"%{k}%"
        hit = or_(Expert.name.like(like), Expert.org.like(like), Expert.title.like(like),
                  Expert.field.like(like), Expert.bio.like(like),
                  Expert.id.in_(select(Participation.expert_id).where(Participation.topic.like(like))))
        terms.append(case((hit, 2), else_=0))
    if parsed.get("tags"):
        tag_hits = (select(func.count(expert_tag.c.tag_id))
                    .select_from(expert_tag.join(Tag, Tag.id == expert_tag.c.tag_id))
                    .where(expert_tag.c.expert_id == Expert.id, Tag.name.in_(parsed["tags"]))
                    .scalar_subquery())
        terms.append(tag_hits * 3)
    if parsed.get("org"):
        terms.append(case((Expert.org.like(f"%{parsed['org']}%"), 2), else_=0))
    if parsed.get("need_meeting"):
        has_meeting = select(func.count(Participation.id)).where(
            Participation.expert_id == Expert.id).scalar_subquery()
        terms.append(case((has_meeting > 0, 2), else_=0))
    if not terms:
        return None
    expr = terms[0]
    for t in terms[1:]:
        expr = expr + t
    return expr


def candidates(s: Session, parsed: dict, prefilter: int = PREFILTER):
    """数据库里算分 → 取分最高的 prefilter 条 → 只对这些加载标签和合作记录。
    以前是把所有"沾边"的人（可能占全库 40%）全捞进内存，一万条以上会明显变慢。"""
    from sqlalchemy.orm import selectinload
    expr = score_sql(parsed)
    if expr is None:
        return []
    return (live(s.query(Expert)).filter(expr > 0)
            .order_by(expr.desc(), Expert.id).limit(prefilter)
            .options(selectinload(Expert.tags), selectinload(Expert.meetings)).all())


def search(s: Session, parsed: dict, limit: int = 50) -> list[tuple[Expert, int, list[str]]]:
    out = []
    for e in candidates(s, parsed):
        pts, reasons = score(e, parsed)
        if reasons and pts > 0:
            out.append((e, pts, reasons))
    out.sort(key=lambda x: (-x[1], x[0].name))
    return out[:limit]


def all_tag_names(s: Session) -> list[str]:
    return [t.name for t in s.query(Tag).order_by(Tag.name)]
