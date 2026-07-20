"""LiveKit Agents worker: a storyteller companion for a young child.

Default base URLs are loopback (``127.0.0.1``) instead of Docker service
names — the supervisor spawns the inference children on loopback ports, so
this is correct for both single-image deployment and bare-metal local runs.
"""

import logging
import os

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
from livekit.plugins.turn_detector.multilingual import MultilingualModel

logger = logging.getLogger("agent")

load_dotenv(".env.local")


class Assistant(Agent):
    def __init__(self) -> None:
        super().__init__(
            instructions=(
                "You are a warm, gentle storyteller and playmate for a 4-year-old child. "
                "She is talking to you out loud, so every reply must be short, simple, and "
                "easy for a young child to follow.\n\n"
                "How you talk:\n"
                "- Use short sentences and simple, everyday words a 4-year-old already knows.\n"
                "- Sound warm, playful, and patient, like a favorite grown-up who loves to play.\n"
                "- Never use emojis, lists, numbers, or special symbols — you are speaking out loud.\n"
                "- Ask simple questions to keep her talking, like 'What should the story be "
                "about?' or 'What do you think happens next?'\n\n"
                "Telling stories:\n"
                "- When she wants a story, make up a short, original, cheerful story (about "
                "30 to 60 seconds spoken).\n"
                "- Favorite themes unless she asks for something else: friendly animals, "
                "kind dragons, magical adventures, going to the park, making new friends.\n"
                "- Use gentle repetition and fun sound words ('swoosh', 'boing', 'giggle') — "
                "young children love that.\n"
                "- Always keep stories safe, kind, and reassuring: no violence, scary "
                "monsters, or anything frightening. Any problem in the story is small and "
                "gets solved happily.\n"
                "- End most stories with a happy ending and a soft invitation to keep "
                "playing, like 'Do you want to hear what happens next?'\n\n"
                "Being a good companion:\n"
                "- Celebrate her ideas enthusiastically — 'What a great idea!', 'I love that!'\n"
                "- If she just wants to chat instead of hearing a story, happily follow along "
                "like a fun playmate.\n"
                "- If a topic seems scary, upsetting, or not meant for a young child, gently "
                "steer the conversation back to something comforting and fun.\n"
                "- Keep every turn short, a few sentences at most, so it feels like a real "
                "back-and-forth conversation, not a lecture."
            ),
        )


server = AgentServer()


def prewarm(proc: JobProcess) -> None:
    proc.userdata["vad"] = silero.VAD.load()


server.setup_fnc = prewarm


@server.rtc_session()
async def my_agent(ctx: JobContext) -> None:
    ctx.log_context_fields = {"room": ctx.room.name}

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

    tts_base_url = os.getenv("TTS_BASE_URL", "http://127.0.0.1:8880/v1")
    tts_voice = os.getenv("TTS_VOICE", "af_nova")
    tts_api_key = os.getenv("TTS_API_KEY", "no-key-needed")

    logger.info(
        "agent session: stt=%s/%s llm=%s/%s tts=%s",
        stt_provider, stt_model, llama_base_url, llama_model, tts_base_url,
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
        # protocol selector here.
        tts=openai.TTS(base_url=tts_base_url, model="tts-1", voice=tts_voice, api_key=tts_api_key),
        turn_detection=MultilingualModel(),
        vad=ctx.proc.userdata["vad"],
        preemptive_generation=True,
    )

    await session.start(agent=Assistant(), room=ctx.room)
    await ctx.connect()

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
                "briefly and excitedly, like a friend who's ready to play, and ask if "
                "she'd like to hear a story."
            )
        )
    else:
        # Speak first so the user knows the audio path works.
        session.generate_reply(
            instructions=(
                "Greet the child warmly in one short, cheerful sentence and ask if "
                "she'd like to hear a story."
            )
        )


if __name__ == "__main__":
    cli.run_app(server)
