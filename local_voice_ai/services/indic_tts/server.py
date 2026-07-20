"""Minimal OpenAI-compatible TTS server for Telugu and Marathi, backed by
Meta's MMS (Massively Multilingual Speech) VITS checkpoints via ``transformers``.

Kokoro (the default TTS) has no Telugu or Marathi support at all, and the
AI4Bharat alternatives (indic-parler-tts, IndicF5) are gated on Hugging Face
and unverified for CPU speed. MMS is public, and benchmarked on this stack at
~1.3s to synthesize a ~4s sentence on CPU — in line with Kokoro's own speed.
The tradeoff: MMS ships exactly one voice per language, so unlike Kokoro,
every character sounds the same when speaking Telugu or Marathi.

MMS ships exactly one voice per language, so a pitch shift is applied on top
per-character (see PITCH_SHIFTS) to keep Red One/Blue Bolt/Rosie sounding
distinct rather than all speaking with the same adult male voice. Cheap
linear-interpolation resampling, not a real phase-vocoder shift (near-zero
cost vs. ~3.5s for torchaudio's PitchShift on this hardware — too slow for
live conversation) — duration shifts along with pitch, which is an
acceptable trade for a short reply.

Exposes only what ``livekit.plugins.openai.TTS`` needs:
  - ``POST /v1/audio/speech``  → audio bytes (``voice`` = "{lang}-{character}",
    e.g. "te-pink"; the language selects the model, the character selects
    the pitch shift)
  - ``GET  /v1/models``         → list of available "models" (one per language)
  - ``GET  /health``            → readiness probe

Both language models are loaded once at startup and reused across requests.
"""

from __future__ import annotations

import argparse
import io
import logging
import time
from contextlib import asynccontextmanager
from typing import Optional

import numpy as np
import soundfile as sf
import torch
import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel

logger = logging.getLogger("indic_tts")
logging.basicConfig(level=logging.INFO)

MODEL_ID = "indic-tts"

LANGUAGE_REPOS = {
    "te": "facebook/mms-tts-tel",
    "mr": "facebook/mms-tts-mar",
}

# Semitones to shift the base (adult male) MMS voice per character.
PITCH_SHIFTS_SEMITONES = {
    "red": 0,  # matches the base voice already
    "blue": 4,
    "pink": 8,
}

_models: dict[str, tuple] = {}  # language -> (VitsModel, AutoTokenizer)


def _load_models() -> None:
    from transformers import AutoTokenizer, VitsModel

    for voice, repo in LANGUAGE_REPOS.items():
        logger.info("loading indic TTS model: %s (%s)", voice, repo)
        model = VitsModel.from_pretrained(repo)
        model.eval()
        tokenizer = AutoTokenizer.from_pretrained(repo)
        _models[voice] = (model, tokenizer)
        logger.info("indic TTS model ready: %s", voice)


@asynccontextmanager
async def lifespan(app: FastAPI):
    _load_models()
    yield


app = FastAPI(title="Indic TTS Server (Telugu/Marathi)", lifespan=lifespan)


class SpeechRequest(BaseModel):
    model: Optional[str] = None
    input: str
    voice: Optional[str] = None
    response_format: Optional[str] = "wav"
    speed: Optional[float] = 1.0


def _pitch_shift(audio: np.ndarray, semitones: int) -> np.ndarray:
    if semitones == 0:
        return audio
    factor = 2 ** (semitones / 12)
    n_new = max(1, int(len(audio) / factor))
    x_old = np.linspace(0, 1, len(audio))
    x_new = np.linspace(0, 1, n_new)
    return np.interp(x_new, x_old, audio).astype(np.float32)


def _parse_voice(voice: str) -> tuple[str, int]:
    """"{lang}-{character}" -> (lang, pitch shift in semitones). Bare "te"/"mr"
    (no character suffix) is also accepted, defaulting to no shift."""
    lang, _, character = voice.partition("-")
    if lang not in _models:
        raise ValueError(f"unknown language {lang!r}; expected one of {list(_models)}")
    return lang, PITCH_SHIFTS_SEMITONES.get(character, 0)


def _synthesize(text: str, voice: str) -> tuple[np.ndarray, int]:
    lang, semitones = _parse_voice(voice)
    model, tokenizer = _models[lang]
    inputs = tokenizer(text, return_tensors="pt")
    with torch.no_grad():
        waveform = model(**inputs).waveform
    audio = waveform.squeeze().cpu().numpy().astype(np.float32)
    return _pitch_shift(audio, semitones), model.config.sampling_rate


def _encode(audio: np.ndarray, sample_rate: int, fmt: str) -> tuple[bytes, str]:
    fmt = (fmt or "wav").lower()
    buf = io.BytesIO()

    if fmt in {"mp3", "opus", "aac", "flac"}:
        try:
            sf.write(buf, audio, sample_rate, format=fmt.upper())
            return buf.getvalue(), f"audio/{fmt}"
        except Exception:
            buf = io.BytesIO()  # fall through to wav

    sf.write(buf, audio, sample_rate, format="WAV", subtype="PCM_16")
    return buf.getvalue(), "audio/wav"


@app.post("/v1/audio/speech")
async def speech(req: SpeechRequest) -> Response:
    if not _models:
        raise HTTPException(status_code=503, detail="models not loaded")
    if not req.input:
        raise HTTPException(status_code=400, detail="input is required")

    voice = req.voice or "te"
    try:
        audio, sample_rate = _synthesize(req.input, voice)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("synthesis failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    data, media_type = _encode(audio, sample_rate, req.response_format or "wav")
    return Response(content=data, media_type=media_type)


@app.get("/v1/models")
async def list_models() -> JSONResponse:
    return JSONResponse(
        {
            "object": "list",
            "data": [
                {
                    "id": MODEL_ID,
                    "object": "model",
                    "created": int(time.time()),
                    "owned_by": "facebook",
                }
            ],
        }
    )


@app.get("/health")
async def health() -> dict[str, object]:
    return {"status": "ok", "models_loaded": sorted(_models)}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Indic TTS Server (Telugu/Marathi)")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8881)
    args = parser.parse_args()

    uvicorn.run(app, host=args.host, port=args.port)
