"""FastAPI app served from the supervisor process.

Two responsibilities:
  1. ``POST /api/connection-details`` — mints a LiveKit access token. This is
     the Python port of ``frontend/app/api/connection-details/route.ts``.
  2. ``GET /*`` — serves the statically-exported Next.js frontend, when
     ``Config.frontend_dir`` is set.
"""

from __future__ import annotations

import json
import logging
import os
import random
from collections.abc import Callable
from datetime import timedelta
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from livekit import api as lk_api

from .characters import CHARACTERS, get_character
from .config import Config

logger = logging.getLogger("api")

# Character intro lines are fixed strings (see characters.py), so the audio
# never changes — synthesizing it fresh on every page load was pure waste.
# Cached under the same volume used for model weights (HF_HOME), so it
# survives container restarts: only the very first request per character
# ever pays for real TTS synthesis.
_PREVIEW_CACHE_DIR = Path(os.getenv("HF_HOME", "/tmp")) / "character-previews"


async def _synthesize_and_cache_preview(cfg: Config, character_id: str, language: str = "en") -> bytes:
    character = get_character(character_id)
    cache_path = _PREVIEW_CACHE_DIR / f"{character.id}.{language}.wav"
    if cache_path.is_file():
        return cache_path.read_bytes()

    if language in ("te", "mr") and cfg.indic_tts_enabled:
        tts_base_url = cfg.indic_tts_base_url
        voice = language
    else:
        tts_base_url = cfg.tts_base_url
        voice = character.tts_voice

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            f"{tts_base_url}/audio/speech",
            json={
                "input": character.intro_line_for(language),
                "voice": voice,
                "response_format": "wav",
            },
        )
        resp.raise_for_status()

    try:
        _PREVIEW_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cache_path.write_bytes(resp.content)
    except OSError:
        logger.warning("could not cache voice preview for %s/%s", character.id, language)

    return resp.content


async def prewarm_character_previews(cfg: Config) -> None:
    """Pre-synthesize every character's intro line once, so even the very
    first person to open the app (empty cache, fresh /models volume) doesn't
    wait on Kokoro/indic_tts — see _serve() in __main__.py, called once
    children are ready. Best-effort: a failure here just means the picker's
    first load falls back to synthesizing on demand."""
    languages = ["en", "te", "mr"] if cfg.indic_tts_enabled else ["en"]
    for character in CHARACTERS.values():
        for language in languages:
            try:
                await _synthesize_and_cache_preview(cfg, character.id, language)
                logger.info("pre-warmed voice preview: %s/%s", character.id, language)
            except httpx.HTTPError:
                logger.exception(
                    "failed to pre-warm voice preview for %s/%s", character.id, language
                )


def _mint_token(
    cfg: Config,
    agent_name: str | None,
    character: str | None = None,
    language: str | None = None,
) -> dict[str, Any]:
    participant_name = "user"
    participant_identity = f"voice_assistant_user_{random.randint(0, 9999)}"
    room_name = f"voice_assistant_room_{random.randint(0, 9999)}"

    token = (
        lk_api.AccessToken(cfg.livekit_api_key, cfg.livekit_api_secret)
        .with_identity(participant_identity)
        .with_name(participant_name)
        .with_ttl(timedelta(minutes=15))
        .with_grants(
            lk_api.VideoGrants(
                room=room_name,
                room_join=True,
                can_publish=True,
                can_publish_data=True,
                can_subscribe=True,
            )
        )
    )

    # The picked character/language ride along as room metadata so the agent
    # (which is dispatched into this room, not called directly) can read them
    # via ctx.job.room.metadata — see agent.py::my_agent. Attaching a
    # RoomConfiguration at all (even just for metadata) opts the room out of
    # LiveKit's implicit "dispatch to any unnamed worker" default, so an
    # explicit dispatch entry — agent_name="" matches our unnamed worker —
    # must always be included alongside it, or the agent never joins.
    room_metadata = {k: v for k, v in {"character": character, "language": language}.items() if v}
    if agent_name or room_metadata:
        token = token.with_room_config(
            lk_api.RoomConfiguration(
                agents=[lk_api.RoomAgentDispatch(agent_name=agent_name or "")],
                metadata=json.dumps(room_metadata) if room_metadata else "",
            )
        )

    return {
        "serverUrl": cfg.public_livekit_url,
        "roomName": room_name,
        "participantName": participant_name,
        "participantToken": token.to_jwt(),
    }


def build_app(
    cfg: Config,
    status_provider: Callable[[], list[dict[str, Any]]] | None = None,
) -> FastAPI:
    app = FastAPI(title="local-voice-ai", version="0.1.0")

    @app.get("/api/status")
    async def status() -> dict[str, Any]:
        """Per-child readiness, polled by the frontend's first-boot splash.

        The web server starts before the children are ready (first boot can
        spend a long time downloading model weights), so this is how the UI
        knows whether the stack is usable yet.
        """
        children = status_provider() if status_provider is not None else []
        return {
            "ready": all(c["ready"] for c in children),
            "children": children,
            # Lets the frontend hint "say the wake phrase" when enabled.
            "wake_word": cfg.wake_word,
            # Lets the language picker only offer Telugu/Marathi when the
            # (optional, off-by-default) indic_tts child is actually running.
            "languages": ["en", "te", "mr"] if cfg.indic_tts_enabled else ["en"],
        }

    @app.post("/api/connection-details")
    async def connection_details(request: Request) -> JSONResponse:
        try:
            body = await request.json()
        except Exception:
            body = {}

        agent_name: str | None = None
        try:
            agent_name = body.get("room_config", {}).get("agents", [{}])[0].get("agent_name")
        except (AttributeError, IndexError, TypeError):
            agent_name = None

        character = body.get("character") if isinstance(body.get("character"), str) else None
        language = body.get("language") if isinstance(body.get("language"), str) else None

        try:
            data = _mint_token(cfg, agent_name, character, language)
        except Exception as exc:
            logger.exception("token minting failed")
            raise HTTPException(status_code=500, detail=str(exc)) from exc

        return JSONResponse(data, headers={"Cache-Control": "no-store"})

    @app.post("/api/preview-voice")
    async def preview_voice(request: Request) -> Response:
        """Lets the character-picker screen play each character's intro line
        before a call starts, by proxying straight to the TTS child — there's
        no LiveKit room at that point yet to route audio through.

        Each character's intro line is a fixed string, so its audio is cached
        to disk after the first synthesis (see _PREVIEW_CACHE_DIR) instead of
        re-synthesizing on every page load.
        """
        try:
            body = await request.json()
        except Exception:
            body = {}

        character = get_character(body.get("character") if isinstance(body, dict) else None)
        language = body.get("language") if isinstance(body.get("language"), str) else "en"

        try:
            audio = await _synthesize_and_cache_preview(cfg, character.id, language)
        except httpx.HTTPError as exc:
            logger.exception("voice preview failed")
            raise HTTPException(status_code=502, detail=str(exc)) from exc

        return Response(content=audio, media_type="audio/wav")

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    if cfg.frontend_dir:
        # SPA-style: serve static export, falling back to index.html for unknown paths.
        static = StaticFiles(directory=cfg.frontend_dir, html=True)

        @app.get("/{path:path}")
        async def spa(path: str, request: Request) -> Any:
            try:
                return await static.get_response(path or "index.html", request.scope)
            except Exception:
                return FileResponse(f"{cfg.frontend_dir}/index.html")

    return app
