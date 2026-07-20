<div align="center">
  <h1>Story Teller</h1>
  <p>A self-hosted, local-only voice storyteller for kids — three characters to pick from, English/Telugu/Marathi, and a PIN-gated parent dashboard for time limits and custom lessons.</p>
  <p>Real-time voice AI — STT, LLM, TTS — running entirely in <strong>one container</strong> on your own network, supervised by a single Python parent process. Powered by <a href="https://docs.livekit.io/agents">LiveKit Agents</a>.</p>
</div>

## Overview

Everything runs as managed children of one Python supervisor (`python -m local_voice_ai serve`):

- **LiveKit server** (Go binary subprocess) for WebRTC signaling — skipped if `LIVEKIT_URL` points at LiveKit Cloud.
- **llama.cpp** (`llama-server` binary subprocess) for the LLM — default model is Gemma 4 E2B (quantization-aware-trained 4-bit, ~2.6 GB); swap it with `LLAMA_HF_REPO=org/repo:quant`. Skipped if `LLAMA_BASE_URL` points elsewhere.
- **Nemotron STT** or **Whisper (faster-whisper)** — Python uvicorn child, OpenAI-compatible.
- **Kokoro TTS** — Python uvicorn child, OpenAI-compatible.
- **LiveKit Agents worker** — the orchestrator child.
- **FastAPI** in the supervisor itself, serving `POST /api/connection-details` (token minting) and the statically-exported Next.js frontend.

Children speak HTTP only over `127.0.0.1`. The image exposes four ports: `8080` (web), `7880`, `7881`, `7882/udp` (LiveKit WebRTC, only if running locally).

## System requirements

Everything below is CPU-only by default; a GPU is optional (see [GPU (NVIDIA)](#gpu-nvidia)). Numbers marked "measured" come from `docker stats` on a real running instance with every child warm (STT + LLM + Kokoro TTS + Telugu/Marathi TTS all loaded at once) — that's the ceiling, not the typical moment-to-moment load. Numbers marked "estimated" are reasoned from the architecture (four concurrent inference services), not lab-tested against a range of hardware, since this project only runs on one dev machine.

**Hardware**

| Resource | Minimum | Recommended |
| --- | --- | --- |
| CPU | 4 cores, x86_64 with **AVX2** (any Intel/AMD from ~2013 onward) or Apple Silicon | 8+ cores — llama.cpp runs several parallel inference slots, and STT/LLM/TTS all do real work on the same turn |
| RAM | 8 GB (English-only, `STT_PROVIDER=whisper` with a small model) | 16 GB — measured ~11.5 GB RSS with Nemotron STT + Telugu/Marathi TTS all loaded |
| Disk | 20 GB free | 25 GB+ — ~6.5 GB Docker image, plus ~8 GB model weights (English-only: Nemotron + Kokoro + the default LLM) growing to ~10 GB with `ENABLE_INDIC_TTS=1` (adds Telugu + Marathi models) |
| Network | None required — fully offline after first-boot model download | — |

GPU acceleration (`docker-compose.gpu.yml`) needs an NVIDIA GPU with the [container toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html) installed; VRAM needs scale with how much of the LLM you offload (`LLAMA_N_GPU_LAYERS`, default `999` = full offload for the ~2.6 GB default model).

**Software**

- Docker Engine + the Compose plugin (`docker compose`, not the standalone `docker-compose`) — this is the only supported path; see [Local development](#local-development-no-docker) for the non-Docker alternative.
- Linux or macOS. On Apple Silicon the prebuilt image runs natively but CPU-only (Docker Desktop's VM has no Metal passthrough) — for GPU inference on a Mac, use [Local development](#local-development-no-docker) instead, where `llama-server` picks up Metal automatically.
- amd64 and arm64 images are both published; no architecture-specific setup needed either way.

## Getting started

Run the prebuilt image (amd64 + arm64):

```bash
docker run --rm -it \
  -p 8080:8080 -p 7880:7880 -p 7882:7882/udp \
  -v story-teller-models:/models \
  ghcr.io/sooriyapsn/story-teller:latest
```

Or build from source (also the path for GPU builds):

```bash
docker compose up --build
```

Open <http://localhost:8080>. The first boot downloads the Nemotron + LLM weights — the page shows per-service progress with download sizes, and the terminal logs a compact status heartbeat plus an unmissable “ready” banner when everything is up. Weights are cached in the `models` volume, so later boots are fast and work offline.

### GPU (NVIDIA)

```bash
docker compose -f docker-compose.yml -f docker-compose.gpu.yml up --build
```

The overlay swaps in the CUDA llama.cpp binary + CUDA torch wheels, grants the
GPU to the container, and offloads the whole LLM (`LLAMA_N_GPU_LAYERS=999`,
override to partially offload). Requires the [NVIDIA container toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html) —
verify with `docker run --gpus all ubuntu nvidia-smi`.

### Apple Silicon

The prebuilt image runs natively (arm64), but **CPU-only** — Docker on macOS is a
VM with no Metal access. For GPU (Metal) inference, run bare-metal via
[Local development](#local-development-no-docker) below, where `llama-server`
picks up Metal automatically.

## HTTPS

Off by default (plain HTTP, matching `docker run`/`docker compose up` above). For
a LAN deployment (laptop + a tablet on the same Wi-Fi — no public domain needed),
set `ENABLE_HTTPS=1` in `.env` and rebuild:

```bash
ENABLE_HTTPS=1  # add to .env, then:
docker compose up --build
```

This adds [Caddy](https://caddyserver.com/) as another supervised child, running
its own local CA and terminating TLS in front of the web/API traffic and LiveKit's
signaling connection — both on the *same* port numbers as before (`WEB_PORT`,
`LIVEKIT_BIND_PORT`), just now speaking `https`/`wss`. Nothing else about your
setup changes: same URLs, same ports, same `docker compose` commands.

**What HTTPS does and doesn't cover here:** voice audio itself is already
end-to-end encrypted regardless (WebRTC always encrypts media via DTLS-SRTP) —
what this actually protects is the web page, the API, and the parent PIN, which
otherwise travel as plain HTTP on your home Wi-Fi. The WebRTC media port
(`LIVEKIT_UDP_PORT`, default `7882/udp`) can't be hidden behind a reverse proxy
either way — that's inherent to how WebRTC works, not a Caddy limitation — so it
stays published as plain UDP, same as always. The RTC-over-TCP fallback port
(`7881`) is dropped from external publishing in HTTPS mode, since it's rarely
needed on a healthy LAN once the browser is already reaching everything else
over `wss`.

**One-time step per device:** because this is a local CA (not a real, publicly
trusted one — there's no domain to validate against), each browser will show a
"not secure" warning until you trust Caddy's root certificate once:

```bash
docker compose exec app cat /data/caddy/pki/authorities/local/root.crt
```

Copy that output to each device (laptop, tablet) and add it to the OS/browser's
trusted root certificates. After that, `https://<this-machine's-address>:8091`
(or your `WEB_PORT`) shows a normal, trusted padlock on every device — no more
warnings, and no per-restart re-trust needed (the CA persists in the `caddy_data`
volume).

If you'd rather have a real, publicly-trusted certificate with zero manual trust
steps anywhere, that needs a domain name pointed at this machine and port `443`
reachable from the internet — a materially different (and, for a home app,
usually unnecessary) setup than what's documented here.

## Swapping in cloud providers

Each service has a single "manage" decision driven by its base URL — point it at a remote endpoint and the local subprocess is skipped:

| Goal                              | Set                                                                                  |
| --------------------------------- | ------------------------------------------------------------------------------------ |
| Use LiveKit Cloud                 | `LIVEKIT_URL=wss://your-project.livekit.cloud` (+ `LIVEKIT_API_KEY` / `…_SECRET`)   |
| Use OpenAI for the LLM            | `LLAMA_BASE_URL=https://api.openai.com/v1`, `LLAMA_MODEL=gpt-4o-mini`, `LLAMA_API_KEY=sk-…` |
| Use a remote OpenAI-compatible STT| `STT_BASE_URL=…`, `STT_MODEL=…`, `STT_API_KEY=…`                                     |
| Use a remote OpenAI-compatible TTS| `TTS_BASE_URL=…`, `TTS_API_KEY=…`                                                    |

The supervisor logs which children it manages on startup.

## Local development (no Docker)

Requires Python 3.11+, plus the `livekit-server` and `llama-server` binaries on
your PATH (macOS: `brew install livekit llama.cpp`).

```bash
# Python side
uv pip install -e ".[ml,dev]"
python -m local_voice_ai serve

# Frontend side, in another shell (only needed if you're editing the UI)
cd frontend && pnpm install && pnpm run dev
```

## Architecture

```
┌──────────────────────── single container ────────────────────────┐
│  python -m local_voice_ai serve                                  │
│  │                                                                │
│  ├── child: livekit-server     (skipped if LIVEKIT_URL external) │
│  ├── child: llama-server       (skipped if LLAMA_BASE_URL ext.)  │
│  ├── child: nemotron | whisper (skipped if STT_BASE_URL ext.)    │
│  ├── child: kokoro             (skipped if TTS_BASE_URL ext.)    │
│  ├── child: caddy              (only if ENABLE_HTTPS=1)          │
│  ├── child: livekit-agents worker                                │
│  └── in-process: FastAPI on :8080                                 │
│        ├── POST /api/connection-details  (token minting)         │
│        ├── GET  /api/status              (per-child readiness)   │
│        └── GET  /*                       (static frontend)       │
└───────────────────────────────────────────────────────────────────┘
```

## Project structure

```
.
├─ local_voice_ai/         # Python package: supervisor + agent + services
│  ├─ __main__.py          # python -m local_voice_ai serve
│  ├─ supervisor.py        # async process supervisor
│  ├─ config.py            # env-driven config + manage-X flags
│  ├─ api.py               # FastAPI: token route, status, static frontend
│  ├─ agent.py             # LiveKit Agents worker
│  ├─ wakeword.py          # optional "hey livekit" gate for the agent
│  ├─ caddy/Caddyfile      # HTTPS front door (ENABLE_HTTPS=1 only)
│  └─ services/
│     ├─ nemotron/server.py
│     ├─ whisper/server.py
│     └─ kokoro/server.py
├─ frontend/               # Next.js (configured for static export)
├─ tests/                  # pytest suite
├─ Dockerfile              # multi-stage build
├─ docker-compose.yml      # one service (CPU default)
├─ docker-compose.gpu.yml  # NVIDIA overlay: CUDA build + GPU reservation
├─ .github/workflows/      # CI: tests + multi-arch image publish to GHCR
└─ pyproject.toml          # one Python package, one venv
```

## Environment variables

See `.env` for the full list. The most important ones:

- `LIVEKIT_URL`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET` — local-default; override for cloud.
- `LLAMA_BASE_URL`, `LLAMA_MODEL`, `LLAMA_HF_REPO`, `LLAMA_N_GPU_LAYERS`
- `LLAMA_OFFLINE` — offline LLM startup. Auto by default: once the model is cached, it starts with no internet (skips the Hugging Face lookup); the first run still downloads. Set `LLAMA_OFFLINE=1` to force it, or `0` to always re-check. `LLAMA_MODEL_PATH=/models/…​.gguf` loads a local file directly instead.
- `WAKE_WORD=1` — the agent joins deaf and only starts listening after it hears **“Hey LiveKit”** (on-device detection via [livekit-wakeword](https://github.com/livekit/livekit-wakeword), model baked into the image). `WAKE_WORD_THRESHOLD` (default `0.5`) tunes sensitivity; scores are logged at DEBUG for calibration.
- `STT_PROVIDER` (`nemotron`|`whisper`), `STT_BASE_URL`, `STT_MODEL`; `WHISPER_MODEL` picks the faster-whisper model for the whisper provider.
- `TTS_BASE_URL`, `TTS_VOICE`
- `WEB_PORT` (default `8080`)
- `ENABLE_HTTPS=1` — fronts the web/API and LiveKit signaling with Caddy + a local CA (see [HTTPS](#https)). `PARENT_PIN` (default `1234`) gates the parent settings panel.
- `MANAGE_LIVEKIT`, `MANAGE_LLAMA`, `MANAGE_STT`, `MANAGE_TTS` — explicit overrides for the auto-detected "is the URL external?" logic.

## Credits

- LiveKit: <https://livekit.io/>
- LiveKit Agents: <https://docs.livekit.io/agents/>
- NVIDIA Nemotron Speech: <https://huggingface.co/nvidia/nemotron-speech-streaming-en-0.6b>
- llama.cpp: <https://github.com/ggml-org/llama.cpp>
- Gemma 4 (default LLM, Unsloth QAT GGUF): <https://huggingface.co/unsloth/gemma-4-E2B-it-qat-GGUF>
- Kokoro TTS: <https://github.com/hexgrad/kokoro>
- faster-whisper (Whisper fallback): <https://github.com/SYSTRAN/faster-whisper>
- livekit-wakeword ("hey livekit" detection): <https://github.com/livekit/livekit-wakeword>
