from datetime import UTC, datetime, timedelta

from app import memory as mem


def test_tokens_chinese_bigram_and_latin():
    tokens = mem.tokens("我喜欢喝手冲 coffee")
    assert "喜欢" in tokens
    assert "手冲" in tokens
    assert "coffee" in tokens


def test_dice_matches_shared_terms():
    assert mem.dice("咖啡", "用户喜欢手冲咖啡") > 0
    assert mem.dice("咖啡", "今天天气不错") == 0


def test_recency_decays():
    now = datetime.now(UTC)
    fresh = now.isoformat(timespec="seconds")
    old = (now - timedelta(days=30)).isoformat(timespec="seconds")
    assert mem.recency(fresh, now) > 0.99
    assert mem.recency(old, now) < mem.recency(fresh, now)


def test_retrieve_ranks_relevant_and_updates_access(store):
    store.add_memory("fact", "用户喜欢手冲咖啡，不加糖。", importance=6)
    store.add_memory("fact", "用户在做一个知识库客服工作台。", importance=6)
    store.add_memory("insight", "解释路径比结论更能建立信任。", importance=9)
    results = mem.retrieve(store, "咖啡", k=2)
    assert results[0]["content"].startswith("用户喜欢手冲咖啡")
    assert results[0]["relevance"] > 0
    updated = store.get_memory(results[0]["id"])
    assert updated["access_count"] == 1


def test_importance_and_recency_affect_score():
    now = datetime.now(UTC)
    base = {"content": "无关内容", "ts": now.isoformat(), "last_access": now.isoformat()}
    low = mem.score_memory({**base, "importance": 2}, "咖啡", now)
    high = mem.score_memory({**base, "importance": 9}, "咖啡", now)
    assert high["score"] > low["score"]


def test_best_match_finds_duplicate(store):
    store.add_memory("fact", "用户喜欢手冲咖啡，不加糖。", importance=6)
    match = mem.best_match(store, "fact", "用户喜欢手冲咖啡，不加糖")
    assert match is not None
    assert match["similarity"] > 0.8
    assert mem.best_match(store, "preference", "用户喜欢手冲咖啡，不加糖") is None
