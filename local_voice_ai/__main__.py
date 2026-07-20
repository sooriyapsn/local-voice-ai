"""Entry point: ``python -m local_voice_ai [serve|download-models|console]``.

The default ``serve`` command:
  1. Builds child specs based on the config (skipping any service whose base
     URL is external).
  2. Spawns all children, waits for readiness.
  3. Starts the FastAPI app (token route + static frontend) on the same loop.
  4. Blocks on SIGTERM/SIGINT, then shuts everything down cleanly.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from pathlib import Path

import uvicorn

from .api import build_app, prewarm_character_previews
from .config import Config
from .supervisor import ChildSpec, Supervisor, configure_logging

logger = logging.getLogger("main")

# Strong references for fire-and-forget background tasks (e.g. the character
# preview prewarm) — the event loop only holds a weak reference to tasks, so
# without this they could be garbage-collected mid-flight.
_background_tasks: set[asyncio.Task] = set()


def _llama_cache_dir(env: dict[str, str]) -> Path:
    """The legacy llama.cpp download cache, mirroring its
    fs_get_cache_directory() precedence given the env we pass the child."""
    if env.get("LLAMA_CACHE"):
        return Path(env["LLAMA_CACHE"])
    if env.get("XDG_CACHE_HOME"):
        return Path(env["XDG_CACHE_HOME"]) / "llama.cpp"
    return Path.home() / ".cache" / "llama.cpp"


def _hf_hub_dir(env: dict[str, str]) -> Path:
    """The Hugging Face hub cache current llama-server downloads into."""
    if env.get("HF_HOME"):
        return Path(env["HF_HOME"]) / "hub"
    if env.get("XDG_CACHE_HOME"):
        return Path(env["XDG_CACHE_HOME"]) / "huggingface" / "hub"
    return Path.home() / ".cache" / "huggingface" / "hub"


def _llama_repo_cached(repo: str, env: dict[str, str]) -> bool:
    """Best-effort check for whether a --hf-repo model is already downloaded, so
    we can start --offline automatically after the first successful run.

    Checks both cache layouts llama-server has used:
      - HF hub (current): ``hub/models--<org>--<repo>/snapshots/*/<file>.gguf``
      - legacy: ``llama.cpp/manifest=<org>=<repo>=<tag>.json`` + flat ggufs

    Intentionally conservative: a false miss just means we don't add --offline
    (unchanged network path), while we only claim "cached" for this exact
    repo/quant so a newly-changed repo still downloads.
    """
    spec, tag = [*repo.rsplit(":", 1), "latest"][:2]

    # HF hub layout. A :quant tag selects a file whose name contains the tag.
    hub_repo = _hf_hub_dir(env) / f"models--{spec.replace('/', '--')}"
    if hub_repo.is_dir():
        pattern = f"*{tag}*.gguf" if tag != "latest" else "*.gguf"
        if any(hub_repo.glob(f"snapshots/*/{pattern}")):
            return True

    # Legacy layout.
    cache = _llama_cache_dir(env)
    if not cache.is_dir():
        return False
    manifest = cache / f"manifest={spec.replace('/', '=')}={tag}.json"
    if manifest.is_file():
        return True
    prefix = spec.replace("/", "_")
    return any(p.suffix == ".gguf" for p in cache.glob(f"{prefix}*.gguf"))


def _dir_size(path: Path) -> int:
    """Total bytes under ``path`` (0 if missing). Cheap: /models holds few files."""
    total = 0
    if path.is_dir():
        for p in path.rglob("*"):
            try:
                if p.is_file():
                    total += p.stat().st_size
            except OSError:
                continue
    return total


def _hub_repo_dir(repo: str) -> str:
    """HF hub cache dir name for a repo id (tag/quant suffix stripped)."""
    return "models--" + repo.split(":", 1)[0].replace("/", "--")


def make_status_provider(supervisor: Supervisor, cfg: Config):
    """Wrap ``supervisor.status()`` with per-child download detail.

    Model downloads dominate first boot, so while a child isn't ready we
    report how many bytes its models occupy on disk. Totals aren't knowable
    up front (llama resolves the quant at runtime), so this is a growing
    byte count rather than a fake percentage.
    """
    hub = _hf_hub_dir(dict(os.environ))
    repo_for_child = {
        "llama": _hub_repo_dir(cfg.llama_hf_repo),
        "nemotron": _hub_repo_dir(cfg.nemotron_model_name),
        "whisper": _hub_repo_dir(cfg.whisper_model),
        "kokoro": _hub_repo_dir("hexgrad/Kokoro-82M"),
    }

    def status() -> list[dict[str, object]]:
        children = supervisor.status()
        for child in children:
            repo = repo_for_child.get(str(child["name"]))
            if child["ready"] or repo is None:
                continue
            size = _dir_size(hub / repo)
            if size > 1_000_000:  # only meaningful once a download has begun
                child["detail"] = (
                    f"{size / 1e9:.1f} GB" if size >= 1e9 else f"{size / 1e6:.0f} MB"
                )
        return children

    return status


def _startup_line(children: list[dict[str, object]]) -> str:
    """One compact line per poll: ``llama … 1.2 GB | nemotron ✓ | …``"""
    parts = []
    for c in children:
        mark = "✓" if c["ready"] else "…"
        detail = f" {c['detail']}" if c.get("detail") else ""
        parts.append(f"{c['name']} {mark}{detail}")
    return " | ".join(parts)


def _build_specs(cfg: Config) -> list[ChildSpec]:
    specs: list[ChildSpec] = []
    py = sys.executable

    # --- LiveKit server (Go binary) ----------------------------------
    if cfg.manage_livekit:
        livekit_bin = os.getenv("LIVEKIT_BIN", "livekit-server")
        specs.append(
            ChildSpec(
                name="livekit",
                argv=[
                    livekit_bin,
                    "--dev",
                    # Signaling/HTTP listener only — RTC transport ports
                    # (below) bind broadly regardless of --bind, verified
                    # empirically against this livekit-server build, so
                    # restricting this one to loopback under HTTPS (Caddy
                    # then claims the external port) doesn't affect them.
                    "--bind", cfg.livekit_bind_host,
                    "--port", str(cfg.livekit_internal_port),
                    # livekit-server's RTC TCP port flag is the dotted config
                    # key --rtc.tcp_port (there is no --rtc-port flag).
                    "--rtc.tcp_port", str(cfg.livekit_rtc_port),
                    # Pin the WebRTC UDP media port so it matches the published
                    # container port, and advertise a host-reachable ICE address.
                    # Without --node-ip the dev server auto-detects the container
                    # IP (e.g. 172.x.x.x), which a browser on the host cannot
                    # reach, so media never connects and the room never joins.
                    "--udp-port", str(cfg.livekit_udp_port),
                    "--node-ip", cfg.livekit_node_ip,
                ],
                ready_url=None,  # LiveKit dev server has no consistent /health
                ready_timeout=30.0,
            )
        )

    # --- llama.cpp server (C++ binary) -------------------------------
    if cfg.manage_llama:
        llama_bin = os.getenv("LLAMA_BIN", "llama-server")
        llama_env = {
            "HF_HOME": os.getenv("HF_HOME", "/models"),
            "XDG_CACHE_HOME": os.getenv("XDG_CACHE_HOME", "/models"),
        }
        # A local .gguf path loads directly (no Hugging Face lookup); otherwise
        # resolve from the HF repo. --offline forces cache-only startup so a
        # previously-downloaded model runs with no network. (issue #9)
        if cfg.llama_model_path:
            model_argv = ["-m", cfg.llama_model_path]
        else:
            model_argv = ["--hf-repo", cfg.llama_hf_repo]
        # LLAMA_OFFLINE, when set, wins; otherwise auto-enable --offline once the
        # model is cached so restarts work with no internet, while the first run
        # is still free to download.
        if cfg.llama_offline is not None:
            offline = cfg.llama_offline
        elif cfg.llama_model_path:
            offline = False  # -m needs no network regardless
        else:
            offline = _llama_repo_cached(cfg.llama_hf_repo, llama_env)
            if offline:
                logger.info("llama: %s found in cache; starting --offline", cfg.llama_hf_repo)
        specs.append(
            ChildSpec(
                name="llama",
                argv=[
                    llama_bin,
                    "--host", "127.0.0.1",
                    "--port", str(cfg.llama_bind_port),
                    *model_argv,
                    *(["--offline"] if offline else []),
                    "--alias", cfg.llama_model_alias,
                    "--ctx-size", str(cfg.llama_ctx_size),
                    "--n-gpu-layers", str(cfg.llama_n_gpu_layers),
                    # Voice agent: thinking models (e.g. gemma-4) must answer
                    # directly — reasoning tokens are seconds of dead air
                    # before TTS gets any text.
                    "--reasoning", "off",
                ],
                env=llama_env,
                ready_url=f"http://127.0.0.1:{cfg.llama_bind_port}/v1/models",
                ready_timeout=900.0,  # first-run model download can be slow
            )
        )

    # --- STT (Nemotron or Whisper) -----------------------------------
    if cfg.manage_stt:
        if cfg.stt_provider == "whisper":
            specs.append(
                ChildSpec(
                    name="whisper",
                    argv=[
                        py, "-m", "local_voice_ai.services.whisper.server",
                        "--host", "127.0.0.1",
                        "--port", str(cfg.stt_bind_port),
                    ],
                    env={
                        "WHISPER_MODEL": cfg.whisper_model,
                        "DEVICE": cfg.device,
                    },
                    ready_url=f"http://127.0.0.1:{cfg.stt_bind_port}/health",
                    ready_timeout=600.0,
                )
            )
        else:
            specs.append(
                ChildSpec(
                    name="nemotron",
                    argv=[
                        py, "-m", "local_voice_ai.services.nemotron.server",
                        "--host", "127.0.0.1",
                        "--port", str(cfg.stt_bind_port),
                    ],
                    env={
                        "NEMOTRON_MODEL_NAME": cfg.nemotron_model_name,
                        "NEMOTRON_MODEL_ID": cfg.nemotron_model_id,
                        "PYTORCH_ENABLE_MPS_FALLBACK": "1",
                    },
                    ready_url=f"http://127.0.0.1:{cfg.stt_bind_port}/health",
                    ready_timeout=600.0,
                )
            )

    # --- TTS (Kokoro) ------------------------------------------------
    if cfg.manage_tts:
        specs.append(
            ChildSpec(
                name="kokoro",
                argv=[
                    py, "-m", "local_voice_ai.services.kokoro.server",
                    "--host", "127.0.0.1",
                    "--port", str(cfg.tts_bind_port),
                ],
                ready_url=f"http://127.0.0.1:{cfg.tts_bind_port}/v1/models",
                ready_timeout=600.0,
            )
        )

    # --- Indic TTS (Telugu/Marathi, optional) -------------------------
    if cfg.indic_tts_enabled:
        specs.append(
            ChildSpec(
                name="indic_tts",
                argv=[
                    py, "-m", "local_voice_ai.services.indic_tts.server",
                    "--host", "127.0.0.1",
                    "--port", str(cfg.indic_tts_bind_port),
                ],
                ready_url=f"http://127.0.0.1:{cfg.indic_tts_bind_port}/v1/models",
                # Two full model loads (Telugu + Marathi); measured ~11s and
                # ~21s respectively on this hardware, but first-run download
                # can add much more.
                ready_timeout=600.0,
            )
        )

    # --- Caddy (HTTPS front door, LAN-only local CA) ------------------
    if cfg.enable_https:
        specs.append(
            ChildSpec(
                name="caddy",
                argv=[
                    os.getenv("CADDY_BIN", "caddy"),
                    "run",
                    "--config", os.getenv("CADDY_CONFIG", "/app/Caddyfile"),
                    "--adapter", "caddyfile",
                ],
                env={
                    # Caddy's local CA + issued certs live here, persisted
                    # via the /data volume so they survive restarts —
                    # otherwise every restart mints a new CA and every
                    # device would need re-trusting it again.
                    "XDG_DATA_HOME": "/data",
                    "WEB_EXTERNAL_PORT": str(cfg.web_port),
                    "WEB_INTERNAL_PORT": str(cfg.web_bind_port),
                    "LIVEKIT_EXTERNAL_PORT": str(cfg.livekit_bind_port),
                    "LIVEKIT_INTERNAL_PORT": str(cfg.livekit_internal_port),
                },
                # Caddy has no simple unauthenticated health endpoint with
                # `admin off`; give it a moment to bind and issue its
                # internal-CA cert on first run, then treat it as up.
                ready_url=None,
                ready_timeout=30.0,
            )
        )

    # --- Agent worker ------------------------------------------------
    specs.append(
        ChildSpec(
            name="agent",
            argv=[py, "-m", "local_voice_ai.agent", "start"],
            env=cfg.agent_env(),
            ready_url=None,
            ready_timeout=30.0,
        )
    )

    return specs


async def _serve(cfg: Config) -> int:
    specs = _build_specs(cfg)
    supervisor = Supervisor(specs)

    logger.info(
        "supervisor managing %d children (livekit=%s llama=%s stt=%s tts=%s indic_tts=%s)",
        len(specs),
        cfg.manage_livekit, cfg.manage_llama, cfg.manage_stt, cfg.manage_tts,
        cfg.indic_tts_enabled,
    )

    status_provider = make_status_provider(supervisor, cfg)
    app = build_app(cfg, status_provider=status_provider)
    uv_config = uvicorn.Config(
        app,
        host=cfg.web_bind_host,
        port=cfg.web_bind_port,
        log_level=cfg.log_level.lower(),
        access_log=False,
    )
    uv_server = uvicorn.Server(uv_config)

    # Start the web server BEFORE the children: first boot can spend a long
    # time downloading model weights, and the frontend polls /api/status to
    # show per-child progress instead of a dead page. run_until_signal also
    # starts now so SIGTERM/SIGINT during a slow startup aborts cleanly (the
    # stop event makes each pending readiness wait raise).
    web_task = asyncio.create_task(uv_server.serve(), name="web")
    sup_task = asyncio.create_task(supervisor.run_until_signal(), name="supervisor")
    startup_task = asyncio.create_task(supervisor.start_all(), name="startup")

    async def _report_startup() -> None:
        # A compact heartbeat so `docker compose up` shows startup at a glance
        # instead of a wall of interleaved child logs.
        while True:
            await asyncio.sleep(10)
            logger.info("starting: %s", _startup_line(status_provider()))

    reporter_task = asyncio.create_task(_report_startup(), name="startup-reporter")

    done, _ = await asyncio.wait(
        {web_task, sup_task, startup_task}, return_when=asyncio.FIRST_COMPLETED
    )
    reporter_task.cancel()

    if startup_task in done and startup_task.exception() is not None:
        logger.error("startup failed; shutting down", exc_info=startup_task.exception())
        uv_server.should_exit = True
        await supervisor.shutdown()
        for task in (web_task, sup_task):
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
        return 1

    if not startup_task.done():
        # web or supervisor exited first (signal during startup, port clash…)
        startup_task.cancel()
        try:
            await startup_task
        except (asyncio.CancelledError, Exception):
            pass
    elif startup_task in done:
        # Fire-and-forget: warms the character-intro voice cache so the
        # picker screen's first-ever load doesn't wait on Kokoro (see
        # api.py::prewarm_character_previews). Not awaited — must not delay
        # the "ready" banner below. The event loop only holds a weak
        # reference to tasks, so a strong one is kept in _background_tasks
        # (with a done-callback to drop it again) or this could be
        # garbage-collected mid-flight.
        preview_prewarm_task = asyncio.create_task(
            prewarm_character_previews(cfg), name="preview-prewarm"
        )
        _background_tasks.add(preview_prewarm_task)
        preview_prewarm_task.add_done_callback(_background_tasks.discard)

        # The line first-time users are looking for — make it unmissable.
        scheme = "https" if cfg.enable_https else "http"
        logger.info(
            "\n\n"
            "  ┌────────────────────────────────────────────────┐\n"
            "  │                                                │\n"
            "  │   ✅  story-teller is ready                    │\n"
            "  │                                                │\n"
            "  │   👉  Open  %s://localhost:%-5d             │\n"
            "  │       and click “Start call”                   │\n"
            "  │                                                │\n"
            "  └────────────────────────────────────────────────┘\n",
            scheme,
            cfg.web_port,
        )
        done, _ = await asyncio.wait(
            {web_task, sup_task}, return_when=asyncio.FIRST_COMPLETED
        )

    # Whatever finished first triggers a coordinated shutdown.
    uv_server.should_exit = True
    if not sup_task.done():
        await supervisor.shutdown()
    for task in (web_task, sup_task):
        if not task.done():
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass

    return 0


def _download_models(cfg: Config) -> int:
    """Pre-download VAD, turn-detector, Nemotron weights so first run is warm."""
    logger.info("downloading agent prewarm models (silero VAD, turn detector)")
    # Reuse livekit-agents' built-in download-files command
    import subprocess
    rc = subprocess.call([sys.executable, "-m", "local_voice_ai.agent", "download-files"])
    if rc != 0:
        return rc

    if cfg.manage_stt and cfg.stt_provider == "nemotron":
        logger.info("downloading nemotron model %s", cfg.nemotron_model_name)
        import nemo.collections.asr as nemo_asr  # type: ignore[import]
        nemo_asr.models.ASRModel.from_pretrained(cfg.nemotron_model_name)

    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="local_voice_ai")
    sub = parser.add_subparsers(dest="cmd")
    sub.add_parser("serve", help="run the full supervised stack (default)")
    sub.add_parser("download-models", help="pre-download model weights")
    sub.add_parser("console", help="run the agent in interactive console mode")

    args = parser.parse_args(argv)
    cfg = Config.from_env()
    configure_logging(cfg.log_level)

    cmd = args.cmd or "serve"
    if cmd == "serve":
        return asyncio.run(_serve(cfg))
    if cmd == "download-models":
        return _download_models(cfg)
    if cmd == "console":
        os.execv(
            sys.executable,
            [sys.executable, "-m", "local_voice_ai.agent", "console"],
        )
    parser.error(f"unknown command: {cmd}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
