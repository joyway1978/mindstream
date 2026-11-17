from datetime import UTC, datetime


def test_thought_crud_and_recent_order(store):
    first = store.add_thought("think", "第一个想法", importance=6)
    second = store.add_thought("reflect", "第二个想法", importance=8)
    assert first["id"] < second["id"]
    thoughts = store.recent_thoughts(2)
    assert [t["content"] for t in thoughts] == ["第一个想法", "第二个想法"]
    updated = store.update_thought(first["id"], content="改过的想法", meta={"x": 1})
    assert updated["content"] == "改过的想法"
    assert updated["meta"] == {"x": 1}


def test_messages_and_proactive_count(store):
    today = datetime.now(UTC).date().isoformat()
    store.add_message("user", "你好")
    store.add_message("agent", "我在", proactive=True)
    store.add_message("agent", "又想起一件事", proactive=True)
    assert store.count_proactive_since(today) == 2
    messages = store.recent_messages(10)
    assert [m["role"] for m in messages] == ["user", "agent", "agent"]
    assert messages[1]["proactive"] is True


def test_questions_lifecycle(store):
    question = store.add_question("主动和打扰的边界在哪？")
    assert store.open_questions()[0]["text"] == question["text"]
    store.touch_question(question["id"])
    assert store.open_questions()[0]["heat"] == 2
    store.resolve_question(question["id"], "想清楚了")
    assert store.open_questions() == []
    assert store.get_question(question["id"])["status"] == "resolved"


def test_state_roundtrip(store):
    store.set_state("focus", "记忆如何改变一个人")
    store.set_state("interests", ["信任", "节奏"])
    assert store.get_state("focus") == "记忆如何改变一个人"
    assert store.get_all_state()["interests"] == ["信任", "节奏"]


def test_stats(store):
    store.add_thought("think", "a")
    store.add_memory("fact", "b")
    store.add_message("user", "c")
    store.add_question("d?")
    stats = store.stats()
    assert stats["thoughts"] == 1
    assert stats["memories"] == 1
    assert stats["messages"] == 1
    assert stats["open_questions"] == 1
