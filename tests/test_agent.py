import asyncio

from conftest import ScriptedLLM

from app.agent import Mind, extract_json


def make_mind(settings, store, responses):
    return Mind(settings, store, ScriptedLLM(responses))


def test_extract_json_tolerates_fences_and_prose():
    assert extract_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert extract_json('好的，这是结果：{"a": {"b": 2}} 希望有用') == {"a": {"b": 2}}
    assert extract_json("不是 JSON") is None
    assert extract_json('{"text": "含 } 花括号"}') == {"text": "含 } 花括号"}


def test_tick_creates_thought_memory_question_and_proactive_message(
    store, settings, tick_decision
):
    mind = make_mind(settings, store, [tick_decision])
    result = asyncio.run(mind.tick())
    assert result is not None
    assert result["kind"] == "think"
    assert "主动消息" in result["content"]

    memories = store.recent_memories(10)
    assert any("解释路径" in m["content"] for m in memories)
    assert store.open_questions()[0]["text"].startswith("主动发言应该")
    assert store.get_state("focus") == "主动发言的边界"

    messages = store.recent_messages(5)
    assert len(messages) == 1
    assert messages[0]["proactive"] is True
    assert store.count_proactive_since("0000") == 1
    assert store.get_state("usage")["total_tokens"] == 30
    assert mind.cooldown == settings.proactive_cooldown_ticks


def test_proactive_gate_respects_switch(store, settings, tick_decision):
    mind = make_mind(settings, store, [tick_decision, tick_decision])
    mind.proactive_enabled = False
    result = asyncio.run(mind.tick())
    assert store.recent_messages(5) == []
    assert "suppressed:off" in result["meta"]["applied"]
    assert result["meta"]["suppressed"] == "off"


def test_proactive_gate_cooldown(store, settings, tick_decision):
    mind = make_mind(settings, store, [tick_decision, tick_decision])
    asyncio.run(mind.tick())
    asyncio.run(mind.tick())
    messages = store.recent_messages(5)
    assert len(messages) == 1
    latest = store.recent_thoughts(1)[0]
    assert latest["meta"]["suppressed"] == "cooldown"


def test_unanswered_proactive_cap(store, settings, tick_decision):
    settings.proactive_max_unanswered = 1
    mind = make_mind(settings, store, [tick_decision])
    store.add_message("user", "我先离开一会儿")
    store.add_message("agent", "第一条主动消息", proactive=True)
    asyncio.run(mind.tick())
    assert store.recent_messages(5)[-1]["proactive"] is True
    assert store.recent_thoughts(1)[0]["meta"]["suppressed"] == "unanswered"


def test_low_importance_does_not_speak(store, settings, tick_decision):
    quiet = {**tick_decision, "importance": 3, "should_speak": True}
    mind = make_mind(settings, store, [quiet])
    asyncio.run(mind.tick())
    assert store.recent_messages(5) == []
    assert store.recent_thoughts(1)[0]["meta"]["suppressed"] == "low_importance"


def test_parse_failure_degrades_to_monologue(store, settings):
    mind = make_mind(settings, store, ["这不是 JSON，只是一段想法。"])
    asyncio.run(mind.tick())
    thought = store.recent_thoughts(1)[0]
    assert thought["kind"] == "think"
    assert thought["meta"]["parse_error"]
    assert store.recent_memories(5) == []
    assert store.recent_messages(5) == []


def test_chat_uses_memory_and_extracts_new_one(store, settings):
    store.add_memory("fact", "用户喜欢手冲咖啡，不加糖。", importance=8)
    store.add_message("agent", "之前聊过一次咖啡。")
    extraction = {
        "memories": [
            {
                "content": "用户早上通常喝手冲咖啡。",
                "kind": "preference",
                "importance": 7,
                "keywords": ["咖啡", "早晨"],
            }
        ],
        "question": "",
    }
    mind = make_mind(settings, store, ["你平时喝手冲咖啡，不加糖。", extraction])

    async def scenario():
        reply = await mind.chat("我平时喝什么咖啡？")
        await asyncio.sleep(0.05)
        return reply

    response = asyncio.run(scenario())
    assert "手冲咖啡" in response["reply"]

    first_call = mind.llm.calls[0]["messages"]
    context = "\n".join(m["content"] for m in first_call)
    assert "用户喜欢手冲咖啡" in context
    assert first_call[-1]["role"] == "user"
    # OpenAI 兼容接口不接受 "agent" 角色；历史必须映射成 assistant。
    assert {m["role"] for m in first_call} <= {"system", "user", "assistant"}
    assert any(m["role"] == "assistant" for m in first_call)

    messages = store.recent_messages(10)
    assert [m["role"] for m in messages] == ["agent", "user", "agent"]
    assert any(
        m["kind"] == "preference" and "手冲咖啡" in m["content"]
        for m in store.recent_memories(10)
    )
    assert mind.thinking is False


def test_reflection_updates_self_model_and_stores_insight(store, settings):
    store.add_thought("think", "用户反复追问为什么。", importance=7)
    reflection = {
        "insights": [{"content": "用户把解释当作信任的凭证。", "importance": 9}],
        "self_model": "我是思流，正在学习把解释放在结论之前。",
        "interests": ["解释", "信任"],
        "focus": "解释如何建立信任",
        "resolved_questions": [],
    }
    mind = make_mind(settings, store, [reflection])
    thought = asyncio.run(mind.reflect())
    assert thought is not None
    assert store.get_state("self_model").startswith("我是思流，正在学习")
    assert store.get_state("focus") == "解释如何建立信任"
    memories = store.recent_memories(5)
    assert any(m["kind"] == "insight" and "信任" in m["content"] for m in memories)


def test_duplicate_memory_is_merged(store, settings):
    mind = make_mind(settings, store, [])
    first, mode1 = mind.remember("用户喜欢手冲咖啡。", kind="preference", importance=5)
    second, mode2 = mind.remember("用户喜欢手冲咖啡", kind="preference", importance=8)
    assert mode1 == "new"
    assert mode2 == "merged"
    assert first["id"] == second["id"]
    assert second["importance"] == 8
    assert len(store.recent_memories(10)) == 1


def test_control_toggles_flags(store, settings):
    mind = make_mind(settings, store, [])

    async def scenario():
        await mind.control("pause")
        assert mind.paused is True
        await mind.control("proactive_off")
        assert mind.proactive_enabled is False
        await mind.control("set_interval", 120)
        assert mind.interval == 120
        await mind.control("resume")
        assert mind.paused is False

    asyncio.run(scenario())
