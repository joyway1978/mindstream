"""运行配置：环境变量 / .env 读取，全部有安全默认值。"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

DEFAULT_ENDPOINT = "https://api.deepseek.com/v1"
DEFAULT_MODEL = "deepseek-chat"


def load_dotenv(path: Path | None = None) -> None:
    """极简 .env 加载器：不覆盖已存在的环境变量，不处理复杂转义。"""
    path = path or (ROOT / ".env")
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip("'\"")
        if key and key not in os.environ:
            os.environ[key] = value


def _env(*names: str, default: str = "") -> str:
    for name in names:
        value = os.environ.get(name)
        if value is not None and value.strip():
            return value.strip()
    return default


def _float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def _bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() not in {"0", "false", "no", "off", ""}


@dataclass
class Settings:
    api_key: str = ""
    base_url: str = DEFAULT_ENDPOINT
    model: str = DEFAULT_MODEL
    data_dir: Path = field(default_factory=lambda: ROOT / "data")
    profile_path: Path = field(default_factory=lambda: ROOT / "profiles" / "doomsday.json")
    host: str = "127.0.0.1"
    port: int = 8765

    # 节律
    tick_interval: float = 30.0
    max_tick_interval: float = 300.0
    idle_backoff_after: int = 8
    idle_backoff_factor: float = 1.5

    # 主动性
    proactive: bool = True
    proactive_cooldown_ticks: int = 3
    proactive_daily_cap: int = 30
    proactive_max_unanswered: int = 2
    min_speak_importance: float = 6.0

    # 记忆 / 反思
    reflect_every_ticks: int = 6
    retrieval_k: int = 6
    retrieval_candidates: int = 500
    reflection_thoughts: int = 16

    # 模型
    think_max_tokens: int = 1600
    chat_max_tokens: int = 2000
    reflect_max_tokens: int = 1400
    temperature_think: float = 0.85
    temperature_chat: float = 0.7
    request_timeout: float = 90.0
    llm_mode: str = "auto"  # auto | mock

    def __post_init__(self) -> None:
        self.data_dir = Path(self.data_dir)
        self.db_path = self.data_dir / "mind.db"

    @classmethod
    def from_env(cls) -> Settings:
        load_dotenv()
        mode = _env("LLM_MODE", "MIND_LLM_MODE", default="auto").lower()
        return cls(
            api_key=_env("LLM_API_KEY", "OPENAI_API_KEY"),
            base_url=_env("LLM_BASE_URL", default=DEFAULT_ENDPOINT),
            model=_env("LLM_MODEL", default=DEFAULT_MODEL),
            data_dir=Path(_env("MIND_DATA_DIR", default=str(ROOT / "data"))),
            profile_path=Path(
                _env("MIND_PROFILE", default=str(ROOT / "profiles" / "doomsday.json"))
            ),
            host=_env("MIND_HOST", default="127.0.0.1"),
            port=_int("MIND_PORT", 8765),
            tick_interval=_float("MIND_TICK_INTERVAL", 30.0),
            max_tick_interval=_float("MIND_MAX_TICK_INTERVAL", 300.0),
            idle_backoff_after=_int("MIND_IDLE_BACKOFF_AFTER", 8),
            idle_backoff_factor=_float("MIND_IDLE_BACKOFF_FACTOR", 1.5),
            proactive=_bool("MIND_PROACTIVE", True),
            proactive_cooldown_ticks=_int("MIND_PROACTIVE_COOLDOWN", 3),
            proactive_daily_cap=_int("MIND_PROACTIVE_DAILY_CAP", 30),
            proactive_max_unanswered=_int("MIND_PROACTIVE_MAX_UNANSWERED", 2),
            min_speak_importance=_float("MIND_MIN_SPEAK_IMPORTANCE", 6.0),
            reflect_every_ticks=_int("MIND_REFLECT_EVERY", 6),
            retrieval_k=_int("MIND_RETRIEVAL_K", 6),
            retrieval_candidates=_int("MIND_RETRIEVAL_CANDIDATES", 500),
            reflection_thoughts=_int("MIND_REFLECTION_THOUGHTS", 16),
            think_max_tokens=_int("MIND_THINK_MAX_TOKENS", 1600),
            chat_max_tokens=_int("MIND_CHAT_MAX_TOKENS", 2000),
            reflect_max_tokens=_int("MIND_REFLECT_MAX_TOKENS", 1400),
            temperature_think=_float("MIND_TEMPERATURE_THINK", 0.85),
            temperature_chat=_float("MIND_TEMPERATURE_CHAT", 0.7),
            request_timeout=_float("MIND_REQUEST_TIMEOUT", 90.0),
            llm_mode=mode if mode in {"auto", "mock"} else "auto",
        )

    def public_view(self) -> dict:
        """可安全发送给前端的信息，不含密钥。"""
        return {
            "model": self.model,
            "endpoint": self.base_url,
            "tick_interval": self.tick_interval,
            "proactive_default": self.proactive,
            "proactive_cooldown": self.proactive_cooldown_ticks,
            "proactive_daily_cap": self.proactive_daily_cap,
            "proactive_max_unanswered": self.proactive_max_unanswered,
            "min_speak_importance": self.min_speak_importance,
            "reflect_every": self.reflect_every_ticks,
            "retrieval_k": self.retrieval_k,
            "llm_mode": self.llm_mode,
            "profile": str(self.profile_path),
            "key_configured": bool(self.api_key),
        }
