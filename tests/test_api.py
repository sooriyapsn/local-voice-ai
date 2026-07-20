"""Tests for the FastAPI app: token minting + static frontend serving."""

from __future__ import annotations

import base64
import json
import pathlib
import tempfile
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from local_voice_ai.api import build_app
from local_voice_ai.config import Config


def _decode_jwt_payload(token: str) -> dict:
    payload_b64 = token.split(".")[1]
    return json.loads(base64.urlsafe_b64decode(payload_b64 + "==").decode())


@pytest.fixture
def cfg(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> Config:
    monkeypatch.setenv("LIVEKIT_API_KEY", "devkey")
    monkeypatch.setenv("LIVEKIT_API_SECRET", "secret-secret-secret-thirty-two-chars")
    monkeypatch.setenv("LIVEKIT_URL", "ws://127.0.0.1:7880")
    monkeypatch.setenv("PARENT_SETTINGS_PATH", str(tmp_path / "parent-settings.json"))
    monkeypatch.setenv("PARENT_PIN", "1234")
    return Config.from_env()


@pytest.fixture
def client(cfg: Config) -> TestClient:
    return TestClient(build_app(cfg))


class TestHealth:
    def test_healthz_returns_ok(self, client: TestClient) -> None:
        r = client.get("/healthz")
        assert r.status_code == 200
        assert r.json() == {"status": "ok"}


class TestStatus:
    def test_no_provider_reports_ready(self, client: TestClient) -> None:
        # Without a supervisor (tests, bare API) the stack is trivially ready.
        r = client.get("/api/status")
        assert r.status_code == 200
        assert r.json() == {
            "ready": True,
            "children": [],
            "wake_word": False,
            "languages": ["en"],
            "time_limit_minutes": 30,
        }

    def test_wake_word_flag_surfaces(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("WAKE_WORD", "1")
        client = TestClient(build_app(Config.from_env()))
        assert client.get("/api/status").json()["wake_word"] is True

    def test_indic_languages_only_offered_when_enabled(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("ENABLE_INDIC_TTS", "1")
        client = TestClient(build_app(Config.from_env()))
        assert client.get("/api/status").json()["languages"] == ["en", "te", "mr"]

    def test_reports_children_not_ready(self, cfg: Config) -> None:
        children = [
            {"name": "llama", "ready": False, "running": True, "restarts": 0},
            {"name": "kokoro", "ready": True, "running": True, "restarts": 0},
        ]
        client = TestClient(build_app(cfg, status_provider=lambda: children))
        data = client.get("/api/status").json()
        assert data["ready"] is False
        assert data["children"] == children

    def test_ready_when_all_children_ready(self, cfg: Config) -> None:
        children = [
            {"name": "llama", "ready": True, "running": True, "restarts": 0},
            {"name": "agent", "ready": True, "running": True, "restarts": 1},
        ]
        client = TestClient(build_app(cfg, status_provider=lambda: children))
        assert client.get("/api/status").json()["ready"] is True


class TestConnectionDetails:
    def test_mints_token_with_empty_body(self, client: TestClient) -> None:
        r = client.post("/api/connection-details", json={})
        assert r.status_code == 200
        data = r.json()
        assert set(data) == {"serverUrl", "roomName", "participantName", "participantToken"}
        assert data["serverUrl"] == "ws://127.0.0.1:7880"
        assert data["roomName"].startswith("voice_assistant_room_")
        assert data["participantName"] == "user"

    def test_jwt_carries_correct_issuer_and_grants(self, client: TestClient) -> None:
        r = client.post("/api/connection-details", json={})
        payload = _decode_jwt_payload(r.json()["participantToken"])
        assert payload["iss"] == "devkey"
        assert payload["sub"].startswith("voice_assistant_user_")
        assert "video" in payload
        # AccessToken.with_grants serializes VideoGrants with camelCase keys.
        video = payload["video"]
        assert video.get("roomJoin") is True
        assert video.get("canPublish") is True
        assert video.get("canSubscribe") is True

    def test_token_has_expiry(self, client: TestClient) -> None:
        r = client.post("/api/connection-details", json={})
        payload = _decode_jwt_payload(r.json()["participantToken"])
        # 15-minute TTL → exp - nbf should be 900s.
        assert payload["exp"] - payload["nbf"] == 900

    def test_agent_dispatch_included_when_requested(self, client: TestClient) -> None:
        r = client.post(
            "/api/connection-details",
            json={"room_config": {"agents": [{"agent_name": "my-agent"}]}},
        )
        assert r.status_code == 200
        payload = _decode_jwt_payload(r.json()["participantToken"])
        assert "roomConfig" in payload

    def test_missing_agent_name_does_not_attach_room_config(self, client: TestClient) -> None:
        r = client.post("/api/connection-details", json={})
        payload = _decode_jwt_payload(r.json()["participantToken"])
        assert "roomConfig" not in payload

    def test_character_metadata_still_dispatches_the_unnamed_worker(
        self, client: TestClient
    ) -> None:
        # Regression test: attaching a RoomConfiguration (for character/language
        # metadata) must not opt the room out of LiveKit's default dispatch to
        # an unnamed worker — that requires an explicit agent_name="" dispatch
        # entry alongside the metadata, or the agent never joins the room.
        r = client.post(
            "/api/connection-details",
            json={"character": "red", "language": "en"},
        )
        payload = _decode_jwt_payload(r.json()["participantToken"])
        agents = payload["roomConfig"]["agents"]
        assert len(agents) == 1
        # Proto3 JSON omits string fields at their default ("") value, so an
        # unnamed dispatch serializes as {} rather than {"agentName": ""}.
        assert agents[0].get("agentName", "") == ""
        metadata = json.loads(payload["roomConfig"]["metadata"])
        assert metadata == {"character": "red", "language": "en"}

    def test_malformed_body_still_returns_a_token(self, client: TestClient) -> None:
        # The Next.js route swallowed JSON errors silently; ours should too.
        r = client.post("/api/connection-details", content=b"not json")
        assert r.status_code == 200

    def test_each_call_produces_a_fresh_room(self, client: TestClient) -> None:
        rooms = {client.post("/api/connection-details", json={}).json()["roomName"] for _ in range(8)}
        # Random ints in [0, 9999] → collisions are statistically possible but rare;
        # we want at least most of the rooms to be unique.
        assert len(rooms) >= 6


class TestStaticFrontend:
    @pytest.fixture
    def frontend_dir(self) -> Iterator[pathlib.Path]:
        with tempfile.TemporaryDirectory() as td:
            out = pathlib.Path(td)
            (out / "index.html").write_text("<h1>HOME</h1>")
            (out / "favicon.ico").write_bytes(b"\x00\x00")
            (out / "_next").mkdir()
            (out / "_next" / "static.js").write_text("// stub")
            yield out

    @pytest.fixture
    def client(self, monkeypatch: pytest.MonkeyPatch, frontend_dir: pathlib.Path) -> TestClient:
        monkeypatch.setenv("LIVEKIT_API_KEY", "devkey")
        monkeypatch.setenv("LIVEKIT_API_SECRET", "secret-secret-secret-thirty-two-chars")
        monkeypatch.setenv("FRONTEND_DIR", str(frontend_dir))
        return TestClient(build_app(Config.from_env()))

    def test_serves_index(self, client: TestClient) -> None:
        r = client.get("/")
        assert r.status_code == 200
        assert "HOME" in r.text

    def test_serves_static_asset(self, client: TestClient) -> None:
        r = client.get("/_next/static.js")
        assert r.status_code == 200
        assert "stub" in r.text

    def test_spa_fallback_for_unknown_route(self, client: TestClient) -> None:
        r = client.get("/some/client-side/route")
        assert r.status_code == 200
        assert "HOME" in r.text

    def test_api_route_still_wins_over_spa_fallback(self, client: TestClient) -> None:
        r = client.post("/api/connection-details", json={})
        assert r.status_code == 200
        assert "participantToken" in r.json()

    def test_healthz_still_wins_over_spa_fallback(self, client: TestClient) -> None:
        r = client.get("/healthz")
        assert r.status_code == 200
        assert r.json() == {"status": "ok"}


class TestParentSettings:
    def test_verify_pin_rejects_wrong_pin(self, client: TestClient) -> None:
        r = client.post("/api/parent/verify-pin", json={"pin": "0000"})
        assert r.status_code == 200
        assert r.json() == {"ok": False}

    def test_verify_pin_accepts_correct_pin(self, client: TestClient) -> None:
        r = client.post("/api/parent/verify-pin", json={"pin": "1234"})
        assert r.json() == {"ok": True}

    def test_get_settings_requires_pin_header(self, client: TestClient) -> None:
        r = client.get("/api/parent/settings")
        assert r.status_code == 401

    def test_get_settings_returns_defaults(self, client: TestClient) -> None:
        r = client.get("/api/parent/settings", headers={"X-Parent-Pin": "1234"})
        assert r.status_code == 200
        assert r.json() == {"time_limit_minutes": 30, "story_title": "", "story_text": ""}

    def test_save_settings_requires_correct_pin(self, client: TestClient) -> None:
        r = client.post("/api/parent/settings", json={"pin": "wrong", "time_limit_minutes": 60})
        assert r.status_code == 401

    def test_save_settings_persists_and_rejects_out_of_range_limit(
        self, client: TestClient
    ) -> None:
        r = client.post(
            "/api/parent/settings",
            json={
                "pin": "1234",
                "time_limit_minutes": 999,
                "story_title": "Lesson",
                "story_text": "Once upon a time...",
            },
        )
        assert r.status_code == 200
        # 999 is outside the 5-180 range, so it's ignored and the default sticks.
        assert r.json()["time_limit_minutes"] == 30
        assert r.json()["story_text"] == "Once upon a time..."

        r2 = client.get("/api/parent/settings", headers={"X-Parent-Pin": "1234"})
        assert r2.json()["story_title"] == "Lesson"

    def test_status_reflects_saved_time_limit(self, client: TestClient) -> None:
        client.post("/api/parent/settings", json={"pin": "1234", "time_limit_minutes": 45})
        assert client.get("/api/status").json()["time_limit_minutes"] == 45

    def test_upload_pdf_requires_correct_pin(self, client: TestClient) -> None:
        r = client.post(
            "/api/parent/upload-pdf",
            data={"pin": "wrong"},
            files={"file": ("story.pdf", b"not a real pdf", "application/pdf")},
        )
        assert r.status_code == 401

    def test_connection_details_carries_saved_story_in_room_metadata(
        self, client: TestClient
    ) -> None:
        client.post(
            "/api/parent/settings",
            json={"pin": "1234", "time_limit_minutes": 30, "story_text": "The tortoise and hare"},
        )
        r = client.post("/api/connection-details", json={"character": "red"})
        assert r.status_code == 200
        payload = _decode_jwt_payload(r.json()["participantToken"])
        metadata = json.loads(payload["roomConfig"]["metadata"])
        assert metadata["story"] == "The tortoise and hare"
