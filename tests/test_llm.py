import asyncio

from app.llm import LLMClient, StreamResult


def test_retries_with_bigger_budget_when_content_empty(settings):
    client = LLMClient(settings)
    calls = []
    payloads = []

    async def fake_stream_once(client_, payload, *, on_reasoning, on_content):
        calls.append(payload["max_tokens"])
        payloads.append(dict(payload))
        if len(calls) == 1:
            return StreamResult(content="", reasoning="推理把预算吃满了")
        return StreamResult(content='{"ok": true}', reasoning="第二次成功")

    client._stream_once = fake_stream_once  # type: ignore[method-assign]

    result = asyncio.run(
        client.stream_chat([{"role": "user", "content": "hi"}], json_mode=True)
    )
    assert result.content == '{"ok": true}'
    assert calls == [settings.think_max_tokens, min(settings.think_max_tokens * 2, 3200)]
    assert payloads[0]["response_format"] == {"type": "json_object"}
    assert payloads[0]["stream"] is True


def test_chat_url_normalization():
    from app.llm import _chat_url

    assert _chat_url("https://x/v1/chat/completions") == "https://x/v1/chat/completions"
    assert _chat_url("https://x/v1") == "https://x/v1/chat/completions"
    assert _chat_url("https://x") == "https://x/v1/chat/completions"
