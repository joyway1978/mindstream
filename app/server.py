"""FastAPI 服务：状态快照、对话、控制与 SSE 实时事件。"""

from __future__ import annotations

import asyncio
import json
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .agent import Mind
from .config import Settings
from .llm import LLMError, build_llm
from .store import Store

STATIC_DIR = Path(__file__).resolve().parent / "static"
log = logging.getLogger("mindstream.server")


class ChatIn(BaseModel):
    text: str = Field(default="", max_length=8000)


class ControlIn(BaseModel):
    action: str
    value: Any = None


def sse(event: dict) -> str:
    return "data: " + json.dumps(event, ensure_ascii=False) + "\n\n"


def create_app(
    settings: Settings | None = None,
    *,
    llm=None,
    start_background: bool = True,
) -> FastAPI:
    settings = settings or Settings.from_env()
    store = Store(settings.db_path)
    mind = Mind(settings, store, llm or build_llm(settings))

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if start_background:
            await mind.start()
        try:
            yield
        finally:
            await mind.stop()
            store.close()

    app = FastAPI(title="Mindstream", lifespan=lifespan)
    app.state.mind = mind
    app.state.store = store
    app.state.settings = settings

    @app.get("/api/health")
    async def health():
        return mind.health()

    @app.get("/api/state")
    async def state():
        return mind.snapshot()

    @app.get("/api/thoughts")
    async def thoughts(limit: int = 40):
        return {"thoughts": store.recent_thoughts(limit)}

    @app.get("/api/memories")
    async def memories(limit: int = 50):
        return {"memories": store.recent_memories(limit)}

    @app.post("/api/chat")
    async def chat(body: ChatIn):
        try:
            return await mind.chat(body.text)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except LLMError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        except Exception as exc:  # 不让单次请求把服务打挂
            log.exception("chat failed")
            raise HTTPException(status_code=500, detail=f"内部错误：{exc}") from exc

    @app.post("/api/control")
    async def control(body: ControlIn):
        try:
            flags = await mind.control(body.action, body.value)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"ok": True, "flags": flags}

    @app.get("/api/events")
    async def events(request: Request):
        queue = mind.subscribe()

        async def generator():
            try:
                yield sse({"type": "hello", "state": mind.snapshot()})
                while True:
                    if await request.is_disconnected():
                        break
                    try:
                        event = await asyncio.wait_for(queue.get(), timeout=15.0)
                        yield sse(event)
                    except TimeoutError:
                        yield ": ping\n\n"
            finally:
                mind.unsubscribe(queue)

        return StreamingResponse(
            generator(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache, no-transform",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    @app.get("/")
    async def index():
        file = STATIC_DIR / "index.html"
        if not file.is_file():
            return JSONResponse({"error": "前端文件缺失"}, status_code=500)
        return FileResponse(file, headers={"Cache-Control": "no-cache"})

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    return app
