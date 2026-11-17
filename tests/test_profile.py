import json
from pathlib import Path

import pytest
from conftest import ScriptedLLM

from app import prompts
from app.agent import Mind
from app.config import Settings
from app.profile import load_profile, profile_from_dict

ROOT = Path(__file__).resolve().parent.parent


def test_doomsday_profile_is_loaded_and_used_in_prompts():
    profile = load_profile(ROOT / "profiles" / "doomsday.json")
    assert profile.id == "doomsday-weapons-v1"
    assert "武器" in profile.mission
    assert any("爆炸物" in rule for rule in profile.rules)
    assert any("远程武器" in interest for interest in profile.interests)

    system = prompts.thinker_system(profile)
    assert profile.mission in system
    assert "不可越过的边界" in system
    assert "不提供制造爆炸物" in system
    chat = prompts.chat_system(profile)
    assert profile.mission in chat
    assert "不做危险操作指导" in chat


def test_mind_seeds_profile_only_once(store, settings):
    mind = Mind(settings, store, ScriptedLLM([]))
    assert store.get_state("focus") == mind.profile.seed_focus
    assert store.get_state("profile_id") == mind.profile.id
    seeded = store.open_questions(50)
    assert {q["text"] for q in seeded} == set(mind.profile.seed_questions)

    # 演进后的状态在同一个 profile 重启时不能被覆盖。
    store.set_state("focus", "演化后的关注")
    Mind(settings, store, ScriptedLLM([]))
    assert store.get_state("focus") == "演化后的关注"
    assert len(store.open_questions(50)) == len(seeded)


def test_custom_profile_path(store, tmp_path):
    custom = tmp_path / "custom.json"
    custom.write_text(
        json.dumps(
            {
                "id": "custom-v1",
                "name": "测试心智",
                "mission": "只思考如何在城市里种菜。",
                "seed_focus": "阳台光照",
                "seed_questions": ["朝北的阳台能种什么？"],
                "rules": ["不讨论农药滥用。"],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    settings = Settings(data_dir=tmp_path, profile_path=custom, api_key="x")
    mind = Mind(settings, store, ScriptedLLM([]))
    assert mind.profile.name == "测试心智"
    assert store.get_state("focus") == "阳台光照"
    assert [q["text"] for q in store.open_questions(10)] == ["朝北的阳台能种什么？"]


def test_profile_validation():
    with pytest.raises(ValueError):
        profile_from_dict({"name": "缺少使命"})
    with pytest.raises(TypeError):
        profile_from_dict(["not", "an", "object"])  # type: ignore[arg-type]
    with pytest.raises(FileNotFoundError):
        load_profile(ROOT / "profiles" / "missing.json")
