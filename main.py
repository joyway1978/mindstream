"""启动入口：python main.py [--mock] [--host 127.0.0.1] [--port 8765]"""

from __future__ import annotations

import argparse
import sys

import uvicorn

from app.config import Settings
from app.server import create_app


def main() -> None:
    parser = argparse.ArgumentParser(description="思流 Mindstream")
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--mock", action="store_true", help="不调用模型，用内置 Mock 演示交互")
    parser.add_argument("--tick", type=float, default=None, help="思考间隔秒数")
    args = parser.parse_args()

    settings = Settings.from_env()
    if args.host:
        settings.host = args.host
    if args.port:
        settings.port = args.port
    if args.tick:
        settings.tick_interval = max(5.0, args.tick)
    if args.mock:
        settings.llm_mode = "mock"

    if settings.llm_mode != "mock" and not settings.api_key:
        print("⚠️  未找到 LLM_API_KEY，请在 .env 中配置，或使用 --mock 启动。", file=sys.stderr)
        sys.exit(2)

    print(f"思流 Mindstream → http://{settings.host}:{settings.port}")
    print(f"模型 {settings.model} · 模式 {settings.llm_mode} · tick {settings.tick_interval:.0f}s")
    uvicorn.run(
        create_app(settings),
        host=settings.host,
        port=settings.port,
        log_level="info",
        access_log=False,
    )


if __name__ == "__main__":
    main()
