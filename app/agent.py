"""思流内核：常驻思考循环、对话、记忆形成、反思与主动性门控。"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from typing import Any

from . import memory as mem
from . import prompts
from .profile import load_profile
from .store import Store, local_date, utc_now

log = logging.getLogger("mindstream")

def clamp(value: Any, low: float, high: float, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return max(low, min(high, number))


def extract_json(text: str) -> dict | None:
    """从模型输出中提取第一个平衡的 JSON 对象；容忍代码块与前后解释。"""
    if not text:
        return None
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```\s*$", "", text)
    try:
        value = json.loads(text)
        return value if isinstance(value, dict) else None
    except json.JSONDecodeError:
        pass
    start = text.find("{")
    while start != -1:
        depth = 0
        in_string = False
        escaped = False
        for index in range(start, len(text)):
            char = text[index]
            if in_string:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    in_string = False
                continue
            if char == '"':
                in_string = True
            elif char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    try:
                        value = json.loads(text[start : index + 1])
                        if isinstance(value, dict):
                            return value
                    except json.JSONDecodeError:
                        break
        start = text.find("{", start + 1)
    return None


class Mind:
    def __init__(self, settings, store: Store, llm) -> None:
        self.settings = settings
        self.store = store
        self.llm = llm
        self.profile = load_profile(settings.profile_path)

        self._subs: set[asyncio.Queue] = set()
        self._lock = asyncio.Lock()
        self._task: asyncio.Task | None = None
        self._stopping = False

        self.paused = False
        self.proactive_enabled = bool(settings.proactive)
        self.thinking = False
        self.interval = float(settings.tick_interval)
        self.ticks = 0
        self.idle_ticks = 0
        self.cooldown = 0
        self.ticks_since_user = 0
        self.ticks_since_proactive: int | None = None
        self.consecutive_errors = 0
        self.last_tick_at: str | None = None
        self.last_error: str | None = None
        self.thoughts_since_reflect = 0
        self.started_at = utc_now()

        self._ensure_state()

    # ---------- 生命周期 ----------

    def _ensure_state(self) -> None:
        # 方向切换时重新播种自我模型、兴趣与种子问题；同一方向重启则保留演化结果。
        if self.store.get_state("profile_id") != self.profile.id:
            self.store.set_state("self_model", self.profile.self_model)
            self.store.set_state("interests", list(self.profile.interests))
            self.store.set_state("focus", self.profile.seed_focus)
            existing = {q["text"] for q in self.store.open_questions(200)}
            for question in self.profile.seed_questions:
                if question not in existing:
                    self.store.add_question(question)
            self.store.set_state("profile_id", self.profile.id)
        if self.store.get_state("usage") is None:
            self.store.set_state("usage", self._empty_usage())

    def _empty_usage(self) -> dict:
        return {
            "date": local_date(),
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
            "calls": 0,
        }

    async def start(self) -> None:
        if self._task is not None:
            return
        self._stopping = False
        self._task = asyncio.create_task(self._loop(), name="mindstream-tick")

    async def stop(self) -> None:
        self._stopping = True
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            except Exception:  # 停止阶段不让收尾异常冒泡
                log.debug("mind task stopped with error", exc_info=True)
            self._task = None
        close = getattr(self.llm, "close", None)
        if close:
            await close()

    async def _loop(self) -> None:
        await asyncio.sleep(1.0)
        while not self._stopping:
            if self.paused or self.thinking:
                await asyncio.sleep(1.0)
                continue
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # 循环本身不能死
                log.exception("tick loop error")
                self.last_error = str(exc)
                await self._record_error("思考循环异常", exc)
                self._backoff_on_error()
            await asyncio.sleep(self.interval)

    # ---------- 事件总线 ----------

    def subscribe(self) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=400)
        self._subs.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        self._subs.discard(queue)

    def publish(self, event: dict) -> None:
        payload = {"ts": utc_now(), **event}
        for queue in list(self._subs):
            try:
                if queue.full():
                    try:
                        queue.get_nowait()
                    except asyncio.QueueEmpty:
                        pass
                queue.put_nowait(payload)
            except Exception:  # noqa: BLE001 - 慢客户端不应影响广播
                self._subs.discard(queue)

    async def _emit_status(self) -> None:
        self.publish({"type": "status", **self.flags()})

    def flags(self) -> dict:
        return {
            "thinking": self.thinking,
            "paused": self.paused,
            "proactive_enabled": self.proactive_enabled,
            "interval": round(self.interval, 1),
            "ticks": self.ticks,
            "idle_ticks": self.idle_ticks,
            "cooldown": self.cooldown,
            "unanswered_proactive": self.store.count_unanswered_proactive(),
            "consecutive_errors": self.consecutive_errors,
            "last_tick_at": self.last_tick_at,
            "last_error": self.last_error,
        }

    def _state_event(self) -> dict:
        return {
            "type": "state",
            "mission": self.profile.mission,
            "flags": self.flags(),
            "focus": self.store.get_state("focus", ""),
            "self_model": self.store.get_state("self_model", ""),
            "interests": self.store.get_state("interests", []),
            "questions": self.store.open_questions(10),
            "stats": self.store.stats(),
            "usage": self.store.get_state("usage", self._empty_usage()),
        }

    def snapshot(self) -> dict:
        thoughts = self.store.recent_thoughts(40)
        for thought in thoughts:
            thought["reasoning"] = (thought.get("reasoning") or "")[:2500]
        return {
            "type": "snapshot",
            "identity": {
                "name": self.profile.name,
                "en": self.profile.en,
                "tagline": self.profile.tagline,
                "mission": self.profile.mission,
            },
            "settings": self.settings.public_view(),
            "flags": self.flags(),
            "focus": self.store.get_state("focus", ""),
            "self_model": self.store.get_state("self_model", ""),
            "interests": self.store.get_state("interests", []),
            "messages": self.store.recent_messages(80),
            "thoughts": thoughts,
            "memories": self.store.recent_memories(24),
            "questions": self.store.open_questions(10),
            "stats": self.store.stats(),
            "usage": self.store.get_state("usage", self._empty_usage()),
        }

    # ---------- 上下文与记忆 ----------

    def _context(self, query: str, *, thought_limit: int = 8) -> dict:
        state = self.store.get_all_state()
        questions = self.store.open_questions(8)
        focus = state.get("focus") or ""
        if not query:
            query = " ".join(
                [focus, *[q["text"] for q in questions[:3]], state.get("self_model", "")[:80]]
            )
        retrieved = mem.retrieve(
            self.store,
            query,
            self.settings.retrieval_k,
            candidates=self.settings.retrieval_candidates,
        )
        return {
            "self_model": state.get("self_model", ""),
            "focus": focus,
            "interests": state.get("interests", []),
            "open_questions": questions,
            "recent_thoughts": self.store.recent_thoughts(thought_limit),
            "recent_messages": self.store.recent_messages(8),
            "memories": retrieved,
            "ticks_since_user": self.ticks_since_user,
            "ticks_since_proactive": self.ticks_since_proactive,
            "proactive_today": self.store.count_proactive_today(),
            "proactive_cap": self.settings.proactive_daily_cap,
            "unanswered_proactive": self.store.count_unanswered_proactive(),
            "proactive_max_unanswered": self.settings.proactive_max_unanswered,
            "proactive_enabled": self.proactive_enabled,
        }

    def remember(
        self,
        content: str,
        *,
        kind: str = "fact",
        importance: float = 5,
        keywords: list[str] | None = None,
        source_ids: list[int] | None = None,
    ) -> tuple[dict, str]:
        content = (content or "").strip()
        if not content:
            raise ValueError("empty memory")
        existing = mem.best_match(self.store, kind, content)
        if existing:
            merged_sources = sorted(set(existing.get("source_ids", [])) | set(source_ids or []))
            merged_tags = sorted(set(existing.get("tags", [])) | set(keywords or []))
            if importance > float(existing.get("importance", 5)):
                memory = self.store.update_memory(
                    existing["id"],
                    content=content,
                    importance=importance,
                    source_ids=merged_sources,
                    tags=merged_tags,
                    last_access=utc_now(),
                )
            else:
                memory = self.store.update_memory(
                    existing["id"], source_ids=merged_sources, tags=merged_tags
                )
            assert memory is not None
            self.store.bump_memory_access([memory["id"]])
            self.publish({"type": "memory_added", "memory": memory, "mode": "merged"})
            return memory, "merged"
        memory = self.store.add_memory(
            kind or "fact",
            content,
            importance=clamp(importance, 1, 10, 5),
            source_ids=source_ids,
            tags=keywords,
        )
        self.publish({"type": "memory_added", "memory": memory, "mode": "new"})
        return memory, "new"

    # ---------- tick ----------

    async def tick(self, force: bool = False) -> dict | None:
        if self.paused and not force:
            return None
        if self._lock.locked():
            return None
        async with self._lock:
            self.thinking = True
            self.ticks += 1
            self.ticks_since_user += 1
            self.idle_ticks += 1
            if self.cooldown > 0:
                self.cooldown -= 1
            if self.ticks_since_proactive is not None:
                self.ticks_since_proactive += 1
            await self._emit_status()
            self.publish({"type": "tick_start", "tick": self.ticks})
            try:
                result = await self._run_think()
                self.consecutive_errors = 0
                self.last_error = None
                self.store.set_state("ticks_total", self.ticks)
                self.last_tick_at = utc_now()
                self._maybe_backoff()
                return result
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # tick 失败要落库并继续
                log.exception("tick failed")
                self.last_error = str(exc)
                self.consecutive_errors += 1
                await self._record_error("这一轮思考失败了", exc)
                self._backoff_on_error()
                return None
            finally:
                self.thinking = False
                await self._emit_status()
                self.publish(self._state_event())

    async def _run_think(self) -> dict | None:
        context = self._context("", thought_limit=10)
        messages = [
            {"role": "system", "content": prompts.thinker_system(self.profile)},
            {"role": "user", "content": prompts.think_user_prompt(context)},
        ]
        thought = self.store.add_thought("think", "", importance=5, meta={"trigger": "tick"})
        self.publish({"type": "thought_start", "thought": thought})

        async def on_reasoning(text: str) -> None:
            self.publish(
                {"type": "thought_delta", "id": thought["id"], "channel": "reasoning", "text": text}
            )

        result = await self.llm.stream_chat(
            messages,
            temperature=self.settings.temperature_think,
            max_tokens=self.settings.think_max_tokens,
            json_mode=True,
            on_reasoning=on_reasoning,
        )
        self._update_usage(result.usage)
        decision = extract_json(result.content)
        meta: dict[str, Any] = {
            "trigger": "tick",
            "tokens": result.usage or {},
            "raw_ok": bool(decision),
        }
        if decision is None:
            meta["parse_error"] = (result.content or "")[:500]
            content = (result.content or "").strip()[:400] or "这一轮没有形成清晰的想法。"
            importance = 3
            final = self.store.update_thought(
                thought["id"],
                content=content,
                reasoning=(result.reasoning or result.content)[:8000],
                importance=importance,
                meta=meta,
            )
            self.publish({"type": "thought_done", "thought": final})
            self.publish(
                {
                    "type": "parse_error",
                    "id": thought["id"],
                    "message": "模型没有返回合法 JSON，本轮只保留了独白。",
                }
            )
            return None

        inner_voice = str(decision.get("inner_voice") or "").strip()
        observation = str(decision.get("observation") or "").strip()
        content = inner_voice or observation or (result.content or "").strip()[:300]
        importance = clamp(decision.get("importance"), 1, 10, 5)
        focus = str(decision.get("focus") or "").strip()[:60]
        applied: list[str] = []

        if focus:
            self.store.set_state("focus", focus)

        new_memory = decision.get("new_memory")
        if isinstance(new_memory, dict) and str(new_memory.get("content") or "").strip():
            try:
                keywords = new_memory.get("keywords")
                if isinstance(keywords, str):
                    keywords = [keywords]
                memory, mode = self.remember(
                    str(new_memory["content"]),
                    kind=str(new_memory.get("kind") or "fact"),
                    importance=clamp(new_memory.get("importance"), 1, 10, importance),
                    keywords=[str(k)[:20] for k in (keywords or [])][:6],
                    source_ids=[thought["id"]],
                )
                applied.append(f"memory:{memory['id']}:{mode}")
            except Exception as exc:  # noqa: BLE001 - 单条记忆失败不影响全局
                log.warning("remember failed: %s", exc)

        question_text = str(decision.get("new_question") or "").strip()
        if question_text:
            duplicate = next(
                (
                    q
                    for q in self.store.open_questions(20)
                    if mem.dice(question_text, q["text"]) > 0.75
                ),
                None,
            )
            if duplicate:
                self.store.touch_question(duplicate["id"])
                applied.append(f"question:{duplicate['id']}:touched")
            else:
                question = self.store.add_question(question_text[:200])
                applied.append(f"question:{question['id']}:new")
                self.publish({"type": "question_added", "question": question})

        resolve_id = decision.get("resolve_question_id")
        if isinstance(resolve_id, (int, str)):
            try:
                resolve_id_int = int(resolve_id)
                self.store.resolve_question(resolve_id_int, "在思考中想清楚了")
                applied.append(f"resolved:{resolve_id_int}")
            except (TypeError, ValueError):
                pass

        message = str(decision.get("message") or "").strip()
        speak, reason = self._speak_gate(decision.get("should_speak"), message, importance)
        if speak:
            sent = self.store.add_message(
                "agent",
                message,
                proactive=True,
                meta={"thought_id": thought["id"], "importance": importance, "trigger": "tick"},
            )
            self.publish({"type": "proactive", "message": sent})
            self.cooldown = self.settings.proactive_cooldown_ticks
            self.ticks_since_proactive = 0
            self.idle_ticks = 0
            applied.append("spoke")
        elif decision.get("should_speak"):
            meta["suppressed"] = reason
            applied.append(f"suppressed:{reason}")

        meta["applied"] = applied
        meta["decision"] = {
            key: decision.get(key)
            for key in ("focus", "importance", "new_question", "should_speak", "observation")
        }
        final = self.store.update_thought(
            thought["id"],
            content=content[:1000],
            reasoning=(result.reasoning or "")[:8000],
            importance=importance,
            meta=meta,
        )
        self.publish({"type": "thought_done", "thought": final})
        self.publish(self._state_event())

        self.thoughts_since_reflect += 1
        if self.thoughts_since_reflect >= self.settings.reflect_every_ticks:
            await self.reflect()
        return final

    def _speak_gate(self, wants: Any, message: str, importance: float) -> tuple[bool, str]:
        if not wants:
            return False, "declined"
        if not self.proactive_enabled:
            return False, "off"
        if self.cooldown > 0:
            return False, "cooldown"
        if (
            self.store.count_unanswered_proactive()
            >= self.settings.proactive_max_unanswered
        ):
            return False, "unanswered"
        if self.store.count_proactive_today() >= self.settings.proactive_daily_cap:
            return False, "daily_cap"
        if importance < self.settings.min_speak_importance:
            return False, "low_importance"
        if not (4 <= len(message) <= 300):
            return False, "bad_length"
        return True, "ok"

    def _maybe_backoff(self) -> None:
        if self.idle_ticks < self.settings.idle_backoff_after:
            return
        current = self.interval
        target = min(
            max(current * self.settings.idle_backoff_factor, current + 5),
            self.settings.max_tick_interval,
        )
        self.interval = round(target, 1)

    def _backoff_on_error(self) -> None:
        self.interval = min(max(self.interval * 2, 60.0), self.settings.max_tick_interval)

    async def _record_error(self, title: str, exc: Exception) -> None:
        text = f"{title}：{str(exc)[:300]}"
        thought = self.store.add_thought(
            "error", text, importance=2, meta={"error": str(exc)[:800]}
        )
        self.publish({"type": "thought_done", "thought": thought})
        self.publish({"type": "error", "message": text})

    def _update_usage(self, usage: dict | None) -> None:
        if not usage:
            return
        current = self.store.get_state("usage", self._empty_usage())
        today = local_date()
        if current.get("date") != today:
            current = self._empty_usage()
        current["calls"] = int(current.get("calls", 0)) + 1
        for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
            current[key] = int(current.get(key, 0)) + int(usage.get(key, 0) or 0)
        self.store.set_state("usage", current)

    # ---------- 对话 ----------

    async def chat(self, text: str) -> dict:
        text = (text or "").strip()
        if not text:
            raise ValueError("消息不能为空")
        if len(text) > 4000:
            text = text[:4000]

        async with self._lock:
            self.thinking = True
            self.ticks_since_user = 0
            self.idle_ticks = 0
            self.interval = float(self.settings.tick_interval)
            await self._emit_status()
            try:
                user_message = self.store.add_message("user", text)
                self.publish({"type": "user_message", "message": user_message})

                context = self._context(text, thought_limit=6)
                history = self.store.recent_messages(12)
                messages = [
                    {"role": "system", "content": prompts.chat_system(self.profile)},
                    {"role": "system", "content": prompts.chat_context_message(context)},
                    *[
                        {
                            "role": "assistant" if m["role"] == "agent" else "user",
                            "content": m["content"],
                        }
                        for m in history
                    ],
                ]
                thought = self.store.add_thought(
                    "chat",
                    "",
                    importance=5,
                    meta={"trigger": "chat", "user_message_id": user_message["id"]},
                )
                stream_id = f"chat-{time.time_ns()}"
                self.publish({"type": "thought_start", "thought": thought})
                self.publish(
                    {"type": "chat_start", "id": stream_id, "reply_to": user_message["id"]}
                )

                async def on_reasoning(chunk: str) -> None:
                    self.publish(
                        {
                            "type": "thought_delta",
                            "id": thought["id"],
                            "channel": "reasoning",
                            "text": chunk,
                        }
                    )

                async def on_content(chunk: str) -> None:
                    self.publish({"type": "chat_delta", "id": stream_id, "text": chunk})

                try:
                    result = await self.llm.stream_chat(
                        messages,
                        temperature=self.settings.temperature_chat,
                        max_tokens=self.settings.chat_max_tokens,
                        on_reasoning=on_reasoning,
                        on_content=on_content,
                    )
                except Exception as exc:
                    self.last_error = str(exc)
                    self.consecutive_errors += 1
                    failed = self.store.update_thought(
                        thought["id"],
                        kind="error",
                        content="这次回复没有完成。",
                        importance=2,
                        meta={"trigger": "chat", "error": str(exc)[:800]},
                    )
                    self.publish({"type": "thought_done", "thought": failed})
                    self.publish({"type": "chat_done", "id": stream_id, "error": str(exc)})
                    self.publish({"type": "error", "message": f"回复失败：{str(exc)[:300]}"})
                    raise

                self._update_usage(result.usage)
                reply = (result.content or "").strip() or "……"
                summary = (result.reasoning or "").strip()[:600] or "我在组织回答。"
                final_thought = self.store.update_thought(
                    thought["id"],
                    content=summary,
                    reasoning=(result.reasoning or "")[:8000],
                    importance=5,
                    meta={"trigger": "chat", "tokens": result.usage or {}},
                )
                self.publish({"type": "thought_done", "thought": final_thought})

                agent_message = self.store.add_message(
                    "agent",
                    reply,
                    meta={
                        "reply_to": user_message["id"],
                        "stream_id": stream_id,
                        "tokens": result.usage or {},
                    },
                )
                self.publish({"type": "chat_done", "id": stream_id, "message": agent_message})
                self.publish(self._state_event())
            finally:
                self.thinking = False
                await self._emit_status()

        asyncio.create_task(self._extract_turn(text, reply, user_message["id"]))
        return {"reply": reply, "message": agent_message, "thought_id": thought["id"]}

    async def _extract_turn(self, user_text: str, reply: str, user_message_id: int) -> None:
        try:
            messages = [
                {
                    "role": "system",
                    "content": prompts.identity(self.profile)
                    + "\n\n现在只做一件事：从一轮对话里抽取长期记忆。只输出 JSON。",
                },
                {"role": "user", "content": prompts.extract_user_prompt(user_text, reply)},
            ]
            raw = await self.llm.chat(
                messages, temperature=0.2, max_tokens=600, json_mode=True
            )
            data = extract_json(raw)
            if not data:
                return
            saved: list[str] = []
            items = data.get("memories")
            if isinstance(items, list):
                for item in items[:2]:
                    if not isinstance(item, dict) or not str(item.get("content") or "").strip():
                        continue
                    keywords = item.get("keywords")
                    if isinstance(keywords, str):
                        keywords = [keywords]
                    memory, mode = self.remember(
                        str(item["content"]),
                        kind=str(item.get("kind") or "fact"),
                        importance=clamp(item.get("importance"), 1, 10, 6),
                        keywords=[str(k)[:20] for k in (keywords or [])][:6],
                        source_ids=[user_message_id],
                    )
                    saved.append(f"{memory['id']}:{mode}")
            question = str(data.get("question") or "").strip()
            if question:
                duplicate = next(
                    (
                        q
                        for q in self.store.open_questions(20)
                        if mem.dice(question, q["text"]) > 0.75
                    ),
                    None,
                )
                if duplicate:
                    self.store.touch_question(duplicate["id"])
                else:
                    added = self.store.add_question(question[:200])
                    self.publish({"type": "question_added", "question": added})
            if saved:
                thought = self.store.add_thought(
                    "remember",
                    "把这轮对话里值得留下的东西记了下来。",
                    importance=6,
                    meta={"memories": saved, "trigger": "chat_extract"},
                )
                self.publish({"type": "thought_done", "thought": thought})
                self.publish(self._state_event())
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - 后台任务失败只记录
            log.warning("memory extraction failed: %s", exc)

    # ---------- 反思 ----------

    async def reflect_guarded(self) -> dict | None:
        """手动反思：与 tick/chat 共用思考锁，避免并发改状态。"""
        if self._lock.locked():
            self.publish({"type": "error", "message": "正在思考或回复中，稍后再反思。"})
            return None
        async with self._lock:
            self.thinking = True
            await self._emit_status()
            try:
                return await self.reflect()
            finally:
                self.thinking = False
                await self._emit_status()
                self.publish(self._state_event())

    async def reflect(self) -> dict | None:
        context = self._context(
            self.store.get_state("focus", ""), thought_limit=self.settings.reflection_thoughts
        )
        messages = [
            {"role": "system", "content": prompts.reflect_system(self.profile)},
            {"role": "user", "content": prompts.reflect_user_prompt(context)},
        ]
        try:
            result = await self.llm.stream_chat(
                messages,
                temperature=0.6,
                max_tokens=self.settings.reflect_max_tokens,
                json_mode=True,
            )
        except Exception as exc:  # noqa: BLE001 - 反思失败不应影响主循环
            log.warning("reflection failed: %s", exc)
            self.publish({"type": "error", "message": f"反思失败：{str(exc)[:200]}"})
            return None
        self._update_usage(result.usage)
        data = extract_json(result.content)
        if not data:
            log.warning("reflection returned no JSON: %r", (result.content or "")[:300])
            self.publish(
                {
                    "type": "error",
                    "message": "反思没有返回可解析的 JSON，本轮已跳过（预算可能被推理占满）。",
                }
            )
            return None

        insights = data.get("insights") if isinstance(data.get("insights"), list) else []
        stored: list[dict] = []
        for item in insights[:3]:
            if not isinstance(item, dict) or not str(item.get("content") or "").strip():
                continue
            try:
                memory, _ = self.remember(
                    str(item["content"]),
                    kind="insight",
                    importance=clamp(item.get("importance"), 1, 10, 7),
                    keywords=["反思"],
                    source_ids=[],
                )
                stored.append(memory)
            except Exception as exc:  # noqa: BLE001 - 单条洞察失败不影响反思
                log.warning("store insight failed: %s", exc)

        if data.get("self_model"):
            self.store.set_state("self_model", str(data["self_model"])[:400])
        interests = data.get("interests")
        if isinstance(interests, list) and interests:
            self.store.set_state("interests", [str(i)[:30] for i in interests[:6]])
        if data.get("focus"):
            self.store.set_state("focus", str(data["focus"])[:60])
        for question_id in data.get("resolved_questions") or []:
            try:
                self.store.resolve_question(int(question_id), "反思时得到了答案")
            except (TypeError, ValueError):
                continue

        self.thoughts_since_reflect = 0
        text = "；".join(str(i.get("content", ""))[:120] for i in insights[:2]) or "这一轮反思没有形成新的洞察。"
        thought = self.store.add_thought(
            "reflect",
            "反思：" + text,
            reasoning=(result.reasoning or "")[:4000],
            importance=clamp(
                max((clamp(i.get("importance"), 1, 10, 7) for i in insights), default=6), 1, 10, 6
            ),
            meta={"insights": [m["content"] for m in stored], "trigger": "reflection"},
        )
        self.publish({"type": "thought_done", "thought": thought})
        self.publish(
            {
                "type": "reflection",
                "insights": [m["content"] for m in stored],
                "self_model": self.store.get_state("self_model", ""),
                "focus": self.store.get_state("focus", ""),
            }
        )
        self.publish(self._state_event())
        return thought

    # ---------- 控制 ----------

    async def control(self, action: str, value: Any = None) -> dict:
        action = (action or "").strip().lower()
        if action == "pause":
            self.paused = True
        elif action == "resume":
            self.paused = False
            self.interval = float(self.settings.tick_interval)
            self.idle_ticks = 0
        elif action == "proactive_on":
            self.proactive_enabled = True
        elif action == "proactive_off":
            self.proactive_enabled = False
        elif action == "set_interval":
            self.interval = max(5.0, min(600.0, clamp(value, 5, 600, self.settings.tick_interval)))
            self.idle_ticks = 0
        elif action == "tick_now":
            asyncio.create_task(self.tick(force=True))
        elif action == "reflect_now":
            asyncio.create_task(self.reflect_guarded())
        else:
            raise ValueError(f"未知操作：{action}")
        await self._emit_status()
        self.publish(self._state_event())
        return self.flags()

    def health(self) -> dict:
        return {
            "ok": True,
            "model": self.settings.model,
            "llm_mode": self.settings.llm_mode,
            "key_configured": bool(self.settings.api_key),
            "db": str(self.store.path),
            "profile": self.profile.id,
            "ticks": self.ticks,
            "last_error": self.last_error,
        }
