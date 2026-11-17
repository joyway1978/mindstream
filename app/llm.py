"""OpenAI 兼容的流式 LLM 客户端，零 SDK 依赖。"""

from __future__ import annotations

import asyncio
import inspect
import json
import random
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import httpx

Callback = Callable[[str], Any] | None


class LLMError(RuntimeError):
    def __init__(self, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


@dataclass
class StreamResult:
    content: str = ""
    reasoning: str = ""
    usage: dict = field(default_factory=dict)
    model: str = ""


def _chat_url(base: str) -> str:
    base = base.rstrip("/")
    if base.endswith("/chat/completions"):
        return base
    if base.endswith("/v1"):
        return base + "/chat/completions"
    return base + "/v1/chat/completions"


async def _emit(cb: Callback, text: str) -> None:
    if not cb or not text:
        return
    result = cb(text)
    if inspect.isawaitable(result):
        await result


class LLMClient:
    """一次请求一个流；失败按 429/5xx/网络错误指数退避重试。"""

    def __init__(self, settings) -> None:
        self.settings = settings
        self.url = _chat_url(settings.base_url)
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=self.settings.request_timeout)
        return self._client

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    def _headers(self) -> dict:
        if not self.settings.api_key:
            raise LLMError("未配置 API Key：请在 .env 中设置 LLM_API_KEY")
        return {
            "Authorization": f"Bearer {self.settings.api_key}",
            "Content-Type": "application/json",
        }

    def _payload(
        self,
        messages: list[dict],
        *,
        temperature: float,
        max_tokens: int,
        json_mode: bool,
        stream: bool,
    ) -> dict:
        payload: dict[str, Any] = {
            "model": self.settings.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": stream,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        return payload

    async def stream_chat(
        self,
        messages: list[dict],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
        json_mode: bool = False,
        on_reasoning: Callback = None,
        on_content: Callback = None,
    ) -> StreamResult:
        client = await self._get_client()
        payload = self._payload(
            messages,
            temperature=self.settings.temperature_think if temperature is None else temperature,
            max_tokens=self.settings.think_max_tokens if max_tokens is None else max_tokens,
            json_mode=json_mode,
            stream=True,
        )
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                result = await self._stream_once(
                    client, payload, on_reasoning=on_reasoning, on_content=on_content
                )
                if result.content.strip():
                    return result
                # 推理模型的 reasoning 可能吃满预算，导致 content 为空。
                # 翻倍预算重试一次；重试不再回放思考链，避免前端重复。
                if payload["max_tokens"] >= 3200:
                    return result
                payload["max_tokens"] = min(payload["max_tokens"] * 2, 3200)
                on_reasoning = None
                on_content = None
                last_error = LLMError("模型只输出了推理内容，content 为空")
                continue
            except LLMError as exc:
                if exc.status == 400 and json_mode and "response_format" in str(exc):
                    # 个别网关不支持 response_format，降级为普通模式再试。
                    payload.pop("response_format", None)
                    json_mode = False
                    last_error = exc
                    continue
                if exc.status not in (429, 500, 502, 503, 504):
                    raise
                last_error = exc
            except (httpx.TimeoutException, httpx.NetworkError) as exc:
                last_error = exc
            if attempt < 2:
                await asyncio.sleep(1.2 * (2**attempt) + random.random() * 0.4)
        raise LLMError(f"模型请求失败：{last_error}") from last_error

    async def _stream_once(
        self,
        client: httpx.AsyncClient,
        payload: dict,
        *,
        on_reasoning: Callback,
        on_content: Callback,
    ) -> StreamResult:
        result = StreamResult()
        async with client.stream(
            "POST", self.url, headers=self._headers(), json=payload
        ) as response:
            if response.status_code >= 400:
                body = (await response.aread()).decode("utf-8", "replace")[:600]
                raise LLMError(
                    f"模型返回 HTTP {response.status_code}：{body}", status=response.status_code
                )
            async for line in response.aiter_lines():
                line = line.strip()
                if not line or line.startswith(":"):
                    continue
                if line.startswith("data:"):
                    line = line[5:].strip()
                if line == "[DONE]":
                    break
                try:
                    chunk = json.loads(line)
                except json.JSONDecodeError:
                    continue
                result.model = chunk.get("model") or result.model
                if chunk.get("usage"):
                    result.usage = chunk["usage"]
                choices = chunk.get("choices") or []
                if not choices:
                    continue
                delta = choices[0].get("delta") or {}
                reasoning = delta.get("reasoning_content") or delta.get("reasoning")
                if reasoning:
                    result.reasoning += reasoning
                    await _emit(on_reasoning, reasoning)
                content = delta.get("content")
                if content:
                    result.content += content
                    await _emit(on_content, content)
        return result

    async def chat(
        self,
        messages: list[dict],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
        json_mode: bool = False,
    ) -> str:
        result = await self.stream_chat(
            messages,
            temperature=temperature,
            max_tokens=max_tokens,
            json_mode=json_mode,
        )
        return result.content


class MockLLM:
    """无网络演示/测试用：按提示词类型返回可解析的固定输出。"""

    def __init__(self, settings=None) -> None:
        self.settings = settings
        self._tick = 0

    async def close(self) -> None:
        return None

    def _decide(self, prompt: str) -> str:
        self._tick += 1
        if "回顾" in prompt or "洞察" in prompt or "self_model" in prompt:
            return json.dumps(
                {
                    "insights": [
                        {
                            "content": "用户更在意系统为什么给出答案，而不是答案本身。",
                            "importance": 8,
                        },
                        {
                            "content": "我倾向于用提问推进思考，但提问过多会变成审问。",
                            "importance": 6,
                        },
                    ],
                    "self_model": "我是思流，一个在对话之间继续思考的心智。我好奇人如何信任一个答案，也在学习何时应该沉默。",
                    "interests": ["信任与解释", "对话节奏", "长期记忆"],
                    "focus": "怎样让主动发言真正有用，而不是打扰",
                    "resolved_questions": [],
                },
                ensure_ascii=False,
            )
        if "抽取" in prompt or "【本轮对话】" in prompt:
            return json.dumps(
                {
                    "memories": [
                        {
                            "content": "用户正在做一个会主动思考的智能体实验，记忆先用 SQLite。",
                            "kind": "fact",
                            "importance": 8,
                            "keywords": ["主动思考", "智能体", "SQLite"],
                        }
                    ],
                    "question": "",
                },
                ensure_ascii=False,
            )
        speak = self._tick % 3 == 0
        return json.dumps(
            {
                "focus": "主动思考与打扰的边界",
                "inner_voice": "我在想，一个真正有用的主动消息，应该是在用户还没意识到问题之前，替他把问题说清楚。",
                "observation": "最近几轮对话都围绕‘它为什么这么回答’，也许解释比结论更重要。",
                "importance": 7,
                "new_memory": {
                    "content": "解释路径可能比最终答案更能建立信任。",
                    "kind": "insight",
                    "importance": 7,
                    "keywords": ["解释", "信任", "答案"],
                },
                "new_question": "主动发言的价值，应该用‘被需要’还是‘被回应’来衡量？",
                "resolve_question_id": None,
                "should_speak": speak,
                "message": "我一直在想刚才那个问题：如果答案本身不够，人真正想确认的也许是它有没有认真想过。"
                if speak
                else "",
            },
            ensure_ascii=False,
        )

    async def stream_chat(
        self,
        messages: list[dict],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
        json_mode: bool = False,
        on_reasoning: Callback = None,
        on_content: Callback = None,
    ) -> StreamResult:
        prompt = "\n".join(str(m.get("content", "")) for m in messages)
        if json_mode:
            reasoning = "我先看看上下文里有哪些线索，再决定这一轮要不要留下记忆、要不要开口。"
            content = self._decide(prompt)
        else:
            reasoning = "用户在和我说话。我先接住他的意思，再想想有没有相关的记忆值得放进来。"
            content = "我听到了。你刚才说的这件事，我会在接下来的思考里继续想。你现在最想让我弄明白的是哪一部分？"
        result = StreamResult(content=content, reasoning=reasoning, model="mock")
        for i in range(0, len(reasoning), 8):
            await _emit(on_reasoning, reasoning[i : i + 8])
            await asyncio.sleep(0.01)
        for i in range(0, len(content), 10):
            await _emit(on_content, content[i : i + 10])
            await asyncio.sleep(0.01)
        result.usage = {
            "prompt_tokens": len(prompt) // 3,
            "completion_tokens": (len(reasoning) + len(content)) // 3,
            "total_tokens": (len(prompt) + len(reasoning) + len(content)) // 3,
        }
        return result

    async def chat(self, messages: list[dict], **kwargs) -> str:
        result = await self.stream_chat(messages, **kwargs)
        return result.content


def build_llm(settings):
    if settings.llm_mode == "mock":
        return MockLLM(settings)
    return LLMClient(settings)
