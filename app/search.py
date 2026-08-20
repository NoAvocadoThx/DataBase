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


def candidates(s: Session, parsed: dict):
    """先在数据库里用 LIKE / 标签 / 单位 缩小候选集（任一条件命中即为候选），再到 Python 打分。"""
    from sqlalchemy import or_
    from sqlalchemy.orm import selectinload
    conds = []
    for k in parsed.get("keywords", []):
        if k:
            like = f"%{k}%"
            topic_ids = s.query(Participation.expert_id).filter(Participation.topic.like(like))
            conds += [Expert.field.like(like), Expert.bio.like(like), Expert.title.like(like),
                      Expert.id.in_(topic_ids)]
    if parsed.get("tags"):
        from .models import expert_tag
        tag_ids = s.query(expert_tag.c.expert_id).join(Tag, Tag.id == expert_tag.c.tag_id).filter(Tag.name.in_(parsed["tags"]))
        conds.append(Expert.id.in_(tag_ids))
    if parsed.get("org"):
        conds.append(Expert.org.like(f"%{parsed['org']}%"))
    q = live(s.query(Expert)).options(selectinload(Expert.tags), selectinload(Expert.meetings))
    if conds:
        q = q.filter(or_(*conds))
    elif parsed.get("need_meeting"):
        q = q.filter(Expert.id.in_(s.query(Participation.expert_id)))
    else:
        return []
    return q.all()


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
