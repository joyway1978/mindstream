"""心智方向（Profile）：使命、自我模型、兴趣、种子问题与内容边界。"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Profile:
    id: str
    name: str
    en: str
    tagline: str
    mission: str
    self_model: str
    interests: tuple[str, ...]
    seed_focus: str
    seed_questions: tuple[str, ...]
    rules: tuple[str, ...]

    @property
    def fingerprint(self) -> str:
        return hashlib.sha256(self.id.encode("utf-8")).hexdigest()[:12]


def _text(data: dict, key: str, default: str = "") -> str:
    value = data.get(key, default)
    return str(value).strip() if value is not None else default


def _list(data: dict, key: str) -> tuple[str, ...]:
    value = data.get(key) or []
    if isinstance(value, str):
        value = [value]
    return tuple(str(item).strip() for item in value if str(item).strip())


def profile_from_dict(data: object) -> Profile:
    if not isinstance(data, dict):
        raise TypeError("profile 必须是 JSON 对象")
    profile_id = _text(data, "id")
    if not profile_id:
        raise ValueError("profile 缺少 id")
    mission = _text(data, "mission")
    if not mission:
        raise ValueError("profile 缺少 mission")
    return Profile(
        id=profile_id,
        name=_text(data, "name", "思流") or "思流",
        en=_text(data, "en", "Mindstream") or "Mindstream",
        tagline=_text(data, "tagline", "在对话之间，继续思考。"),
        mission=mission,
        self_model=_text(data, "self_model") or mission,
        interests=_list(data, "interests"),
        seed_focus=_text(data, "seed_focus") or mission[:60],
        seed_questions=_list(data, "seed_questions"),
        rules=_list(data, "rules"),
    )


def load_profile(path: str | Path) -> Profile:
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"找不到 profile 文件：{path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"profile 不是合法 JSON：{path}：{exc}") from exc
    return profile_from_dict(data)
