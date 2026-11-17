from conftest import ScriptedLLM
from fastapi.testclient import TestClient

from app.server import create_app


def test_state_chat_and_control(settings):
    llm = ScriptedLLM(
        [
            "我听见了。你想先从哪里开始？",
            {"memories": [], "question": ""},
        ]
    )
    app = create_app(settings, llm=llm, start_background=False)
    with TestClient(app) as client:
        health = client.get("/api/health").json()
        assert health["ok"] is True
        assert health["model"] == settings.model

        snapshot = client.get("/api/state").json()
        assert snapshot["identity"]["name"] == "思流"
        assert snapshot["settings"]["key_configured"] is True
        assert "api_key" not in snapshot["settings"]

        response = client.post("/api/chat", json={"text": "你好"})
        assert response.status_code == 200
        assert "听见" in response.json()["reply"]

        snapshot = client.get("/api/state").json()
        assert len(snapshot["messages"]) >= 2
        assert snapshot["messages"][0]["role"] == "user"

        paused = client.post("/api/control", json={"action": "pause"}).json()
        assert paused["flags"]["paused"] is True
        resumed = client.post("/api/control", json={"action": "resume"}).json()
        assert resumed["flags"]["paused"] is False

        bad = client.post("/api/control", json={"action": "nope"})
        assert bad.status_code == 400

        empty = client.post("/api/chat", json={"text": "   "})
        assert empty.status_code == 400


def test_index_and_static(settings):
    app = create_app(settings, llm=ScriptedLLM([]), start_background=False)
    with TestClient(app) as client:
        index = client.get("/")
        assert index.status_code == 200
        assert "思流" in index.text
        css = client.get("/static/style.css")
        assert css.status_code == 200
        assert "--amber" in css.text
        js = client.get("/static/app.js")
        assert js.status_code == 200
        assert "EventSource" in js.text
