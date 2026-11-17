import asyncio
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.llm import StreamResult
from app.store import Store


class ScriptedLLM:
    """按顺序返回预设内容；记录每次调用的 messages 便于断言。"""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def _next(self):
        return self.responses.pop(0) if self.responses else "{}"

    async def stream_chat(
        self,
        messages,
        *,
        temperature=None,
        max_tokens=None,
        json_mode=False,
        on_reasoning=None,
        on_content=None,
    ):
        self.calls.append({"messages": messages, "json_mode": json_mode})
        item = self._next()
        if isinstance(item, dict):
            content = json.dumps(item, ensure_ascii=False)
        else:
            content = str(item)
        reasoning = "脚本化思考链"
        if on_reasoning:
            result = on_reasoning(reasoning)
            if hasattr(result, "__await__"):
                await result
        if on_content:
            result = on_content(content)
            if hasattr(result, "__await__"):
                await result
        return StreamResult(
            content=content,
            reasoning=reasoning,
            usage={"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30},
            model="scripted",
        )

    async def chat(self, messages, **kwargs):
        result = await self.stream_chat(messages, **kwargs)
        return result.content

    async def close(self):
        return None


@pytest.fixture
def store(tmp_path):
    instance = Store(tmp_path / "test.db")
    yield instance
    instance.close()


@pytest.fixture
def settings(tmp_path):
    from app.config import Settings

    return Settings(
        api_key="test-key",
        data_dir=tmp_path,
        tick_interval=30,
        llm_mode="mock",
        reflect_every_ticks=99,
        proactive_cooldown_ticks=2,
        proactive_daily_cap=3,
        min_speak_importance=6,
    )


@pytest.fixture
def tick_decision():
    return {
        "focus": "主动发言的边界",
        "inner_voice": "我在想，一条主动消息只有在替用户把问题说清楚时才值得发出。",
        "observation": "用户最近总在问‘为什么’。",
        "importance": 8,
        "new_memory": {
            "content": "用户更关心解释路径而不是结论。",
            "kind": "insight",
            "importance": 8,
            "keywords": ["解释", "信任"],
        },
        "new_question": "主动发言应该用被需要还是被回应来衡量？",
        "resolve_question_id": None,
        "should_speak": True,
        "message": "我一直在想：也许你真正想确认的，是它有没有认真想过。",
    }


def run(coro):
    return asyncio.run(coro)
