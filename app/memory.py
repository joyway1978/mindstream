"""记忆检索：中文 bigram + 英文词的确定性相关性，配合重要性与时间衰减。"""

from __future__ import annotations

import math
import re
from datetime import UTC, datetime

CJK_RUN = re.compile(r"[\u4e00-\u9fff]+")
WORD = re.compile(r"[a-zA-Z0-9_]{2,}")
STOP = {
    "the", "and", "for", "with", "that", "this", "you", "are", "was", "have",
    "但是", "然后", "所以", "因为", "就是", "一个", "这个", "那个", "我们", "你们",
    "他们", "自己", "什么", "怎么", "已经", "还是", "可以", "没有", "如果", "不是",
}


def tokens(text: str) -> list[str]:
    """中文按字符 bigram（单字补 unigram），英文/数字按词。"""
    text = (text or "").lower()
    out: list[str] = []
    for run in CJK_RUN.findall(text):
        if len(run) == 1:
            out.append(run)
        else:
            out.extend(run[i : i + 2] for i in range(len(run) - 1))
    out.extend(WORD.findall(text))
    return [token for token in out if token not in STOP]


def dice(query: str, text: str) -> float:
    """Dice 系数，对短查询更宽容；结果 0..1。"""
    q = set(tokens(query))
    d = set(tokens(text))
    if not q or not d:
        return 0.0
    return 2 * len(q & d) / (len(q) + len(d))


def recency(ts: str, now: datetime | None = None) -> float:
    """0.995^小时，与 Generative Agents 同量级；越近越接近 1。"""
    now = now or datetime.now(UTC)
    try:
        moment = datetime.fromisoformat(ts)
    except (TypeError, ValueError):
        return 0.5
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    hours = max(0.0, (now - moment).total_seconds() / 3600)
    return math.pow(0.995, hours)


def score_memory(
    memory: dict,
    query: str,
    now: datetime | None = None,
    *,
    w_relevance: float = 0.50,
    w_importance: float = 0.30,
    w_recency: float = 0.20,
) -> dict:
    text = memory.get("content", "")
    tags = " ".join(memory.get("tags") or [])
    relevance = max(dice(query, text), dice(query, text + " " + tags)) if query else 0.0
    importance = max(0.0, min(10.0, float(memory.get("importance", 5)))) / 10.0
    fresh = recency(memory.get("last_access") or memory.get("ts", ""), now)
    total = w_relevance * relevance + w_importance * importance + w_recency * fresh
    return {
        "score": round(total, 4),
        "relevance": round(relevance, 4),
        "importance": round(importance, 4),
        "recency": round(fresh, 4),
    }


def retrieve(store, query: str, k: int = 6, *, candidates: int = 500) -> list[dict]:
    """返回带分数拆解的 top-k，并更新命中记忆的访问时间。"""
    now = datetime.now(UTC)
    memories = store.memory_candidates(candidates)
    ranked = []
    for memory in memories:
        parts = score_memory(memory, query, now)
        ranked.append({**memory, **parts})
    ranked.sort(key=lambda item: item["score"], reverse=True)
    top = ranked[: max(0, k)]
    store.bump_memory_access([item["id"] for item in top])
    return top


def best_match(store, kind: str, content: str, *, threshold: float = 0.82) -> dict | None:
    """写入去重：同 kind 下找最相似的一条。"""
    best = None
    best_score = 0.0
    for memory in store.memory_candidates(500):
        if memory.get("kind") != kind:
            continue
        similarity = dice(content, memory.get("content", ""))
        if similarity > best_score:
            best, best_score = memory, similarity
    if best is not None and best_score >= threshold:
        best = {**best, "similarity": round(best_score, 3)}
        return best
    return None
