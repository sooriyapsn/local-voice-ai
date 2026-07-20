"""LiveKit Agents worker: a storyteller companion for a young child.

Default base URLs are loopback (``127.0.0.1``) instead of Docker service
names — the supervisor spawns the inference children on loopback ports, so
this is correct for both single-image deployment and bare-metal local runs.
"""

import json
import logging
import os
import threading
import time

import httpx
from dotenv import load_dotenv
from livekit.agents import (
    Agent,
    AgentServer,
    AgentSession,
    JobContext,
    JobProcess,
    cli,
)
from livekit.plugins import openai, silero
from livekit.plugins.turn_detector.english import EnglishModel

from .characters import CHARACTERS, Character, get_character, instructions_for

logger = logging.getLogger("agent")

load_dotenv(".env.local")


class Assistant(Agent):
    def __init__(self, character: Character, language: str = "en", custom_story: str = "") -> None:
        super().__init__(instructions=instructions_for(character, language, custom_story))


server = AgentServer()


def _warm_up_llm() -> None:
    """Prime llama-server's prompt cache with each character's system prompt.

    Measured on a persona-length system prompt: a cold KV cache costs ~3-6s
    of prompt processing before the first token, entirely from re-reading the
    system prompt — llama-server caches prompt prefixes per slot, so sending
    each one once here means whichever character the child picks, the real
    first greeting only pays for the few new tokens in the actual request.
    Skipped for non-loopback LLAMA_BASE_URL (a remote/cloud LLM has no local
    cold-cache problem and this would just spend real API calls for nothing).
    """
    llama_base_url = os.getenv("LLAMA_BASE_URL", "http://127.0.0.1:11434/v1")
    if not any(host in llama_base_url for host in ("127.0.0.1", "localhost")):
        return

    llama_model = os.getenv("LLAMA_MODEL", "gemma-4-e2b")
    llama_api_key = os.getenv("LLAMA_API_KEY", "no-key-needed")

    deadline = time.monotonic() + 180.0
    for character in CHARACTERS.values():
        while time.monotonic() < deadline:
            try:
                httpx.post(
                    f"{llama_base_url}/chat/completions",
                    json={
                        "model": llama_model,
                        "messages": [
                            {"role": "system", "content": character.instructions},
                            {"role": "user", "content": "Hi"},
                        ],
                        "max_tokens": 1,
                    },
                    headers={"Authorization": f"Bearer {llama_api_key}"},
                    timeout=30.0,
                ).raise_for_status()
                logger.info("llm warm-up complete: %s", character.id)
                break
            except httpx.HTTPError:
                time.sleep(1.0)
        else:
            logger.warning("llm warm-up gave up waiting for the LLM server")
            return


def prewarm(proc: JobProcess) -> None:
    proc.userdata["vad"] = silero.VAD.load()
    # Warming up all 3 characters can take well past livekit-agents' own
    # process-pool init timeout (each cold llama-server request costs
    # several seconds — see _warm_up_llm's docstring), which was causing
    # prewarmed worker processes to be killed and endlessly respawned before
    # ever reporting ready. Run it on a background thread instead: VAD
    # loading alone is fast enough that the pool handshake completes
    # normally, and the LLM cache still ends up warm shortly after.
    threading.Thread(target=_warm_up_llm, daemon=True).start()


server.setup_fnc = prewarm


@server.rtc_session()
async def my_agent(ctx: JobContext) -> None:
    ctx.log_context_fields = {"room": ctx.room.name}

    # The frontend's character picker sends its choice as JSON room metadata
    # (see api.py::_mint_token). ctx.job.room reflects the room as created at
    # token-mint time, so this is available before ctx.connect(). No metadata
    # (e.g. `console` mode) falls back to the default character.
    room_metadata: dict[str, str] = {}
    raw_metadata = getattr(ctx.job.room, "metadata", "") or ""
    if raw_metadata:
        try:
            room_metadata = json.loads(raw_metadata)
        except (json.JSONDecodeError, TypeError):
            logger.warning("could not parse room metadata: %r", raw_metadata)

    character_id = room_metadata.get("character")
    character = get_character(character_id)
    # Language selection (e.g. "te", "mr"). STT stays English/Nemotron
    # regardless for now — she's learning Telugu/Marathi by hearing it, not
    # necessarily speaking it yet — only the LLM's output language and TTS
    # voice change.
    language = room_metadata.get("language", "en")
    custom_story = room_metadata.get("story", "")
    logger.info("character=%s language=%s custom_story=%s", character.id, language, bool(custom_story))

    llama_model = os.getenv("LLAMA_MODEL", "gemma-4-e2b")
    llama_base_url = os.getenv("LLAMA_BASE_URL", "http://127.0.0.1:11434/v1")
    llama_api_key = os.getenv("LLAMA_API_KEY", "no-key-needed")

    stt_provider = os.getenv("STT_PROVIDER", "nemotron").lower()
    if stt_provider == "whisper":
        default_stt_base_url = "http://127.0.0.1:8000/v1"
        default_stt_model = "Systran/faster-whisper-small"
    else:
        default_stt_base_url = "http://127.0.0.1:8000/v1"
        default_stt_model = "nemotron-speech-streaming"

    stt_base_url = os.getenv("STT_BASE_URL", default_stt_base_url)
    stt_model = os.getenv("STT_MODEL", default_stt_model)
    stt_api_key = os.getenv("STT_API_KEY", "no-key-needed")

    indic_tts_enabled = os.getenv("ENABLE_INDIC_TTS", "").strip().lower() in {"1", "true", "yes", "on"}
    if language in ("te", "mr") and indic_tts_enabled:
        # MMS ships one base voice per language; indic_tts pitch-shifts it
        # per character (see services/indic_tts's PITCH_SHIFTS_SEMITONES) so
        # Red/Blue/Rosie still sound distinct.
        tts_base_url = os.getenv("INDIC_TTS_BASE_URL", "http://127.0.0.1:8881/v1")
        tts_voice = f"{language}-{character.id}"
    else:
        if language in ("te", "mr"):
            logger.warning(
                "language=%s requested but ENABLE_INDIC_TTS is off; falling back to English TTS",
                language,
            )
            language = "en"
        tts_base_url = os.getenv("TTS_BASE_URL", "http://127.0.0.1:8880/v1")
        # A character picked via the frontend always wins; TTS_VOICE only
        # matters as a fallback when there's no room metadata to pick a
        # character from (e.g. `console` mode).
        tts_voice = (
            character.tts_voice if character_id else os.getenv("TTS_VOICE", character.tts_voice)
        )
    tts_api_key = os.getenv("TTS_API_KEY", "no-key-needed")

    logger.info(
        "agent session: character=%s language=%s stt=%s/%s llm=%s/%s tts=%s/%s",
        character.id, language, stt_provider, stt_model, llama_base_url, llama_model,
        tts_base_url, tts_voice,
    )

    wake_word = os.getenv("WAKE_WORD", "").strip().lower() in {"1", "true", "yes", "on"}
    wake_word_model = os.getenv("WAKE_WORD_MODEL", "/app/models/wakeword/hey_livekit.onnx")
    wake_word_threshold = float(os.getenv("WAKE_WORD_THRESHOLD", "0.5"))

    session = AgentSession(
        stt=openai.STT(base_url=stt_base_url, model=stt_model, api_key=stt_api_key),
        llm=openai.LLM(base_url=llama_base_url, model=llama_model, api_key=llama_api_key),
        # The model name selects the wire protocol the openai TTS plugin uses:
        # only {"tts-1", "tts-1-hd"} use the raw-audio-bytes stream that the
        # Kokoro server speaks. Any other name (e.g. "kokoro") routes the plugin
        # into the gpt-4o-mini-tts SSE reader, which parses Kokoro's binary audio
        # body as text, pushes zero frames, and raises "no audio frames were
        # pushed". Kokoro ignores the model field, so "tts-1" is purely a
        # protocol selector here. response_format=wav skips Kokoro's lossy mp3
        # encode step (measured ~140ms saved per utterance, no quality cost
        # over a LAN).
        tts=openai.TTS(
            base_url=tts_base_url,
            model="tts-1",
            voice=tts_voice,
            api_key=tts_api_key,
            response_format="wav",
        ),
        # English-only model: smaller and faster than MultilingualModel, and
        # STT stays English/Nemotron regardless of the agent's reply
        # language (see the language comment above), so the child's turns
        # are always English.
        turn_detection=EnglishModel(),
        vad=ctx.proc.userdata["vad"],
        preemptive_generation=True,
    )

    await session.start(agent=Assistant(character, language, custom_story), room=ctx.room)
    await ctx.connect()

    # Restated here, not just in the system prompt: the model otherwise
    # tends to match the (English) language of this very instruction for
    # the greeting specifically, opening in English before switching to the
    # target language mid-reply.
    language_names = {"te": "Telugu", "mr": "Marathi"}
    greeting_language_hint = (
        f" Say this greeting itself in {language_names[language]}, not English."
        if language in language_names
        else ""
    )

    if wake_word:
        # Join deaf, wait for the wake phrase, then wake up and greet.
        from .wakeword import wait_for_wake_word

        session.input.set_audio_enabled(False)
        participant = await ctx.wait_for_participant()
        try:
            await wait_for_wake_word(participant, wake_word_model, wake_word_threshold)
        except Exception:
            # Fail open: a broken detector shouldn't brick the assistant.
            logger.exception("wake word detection failed; enabling audio input")
        session.input.set_audio_enabled(True)
        session.generate_reply(
            instructions=(
                "You just woke up because she said the wake phrase. Greet her very "
                f"briefly, staying fully in character as {character.name}, and ask "
                f"if she'd like to hear a story.{greeting_language_hint}"
            )
        )
    else:
        # Speak first so the user knows the audio path works.
        session.generate_reply(
            instructions=(
                "Greet the child warmly in one short, cheerful sentence, staying "
                f"fully in character as {character.name}, and ask if she'd like to "
                f"hear a story.{greeting_language_hint}"
            )
        )


if __name__ == "__main__":
    cli.run_app(server)
