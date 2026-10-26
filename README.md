# 🐱 gatochecker

A lightweight, zero-dependency camera dashboard and control panel for cameras running [yi-hack-Allwinner-v2](https://github.com/roleoroleo/yi-hack-Allwinner-v2).

## Origin

**gatochecker** was born out of a simple everyday problem: knowing where the cats are at home without opening each camera feed one by one.

## Features

- **Unified Snapshot Grid**: Fetches high-resolution snapshots across all configured cameras with configurable auto-refresh.
- **Vision Transformer Cat Detection**: Server-side cat detection (`RT-DETR-L` / `YOLOv11`) on every snapshot with confidence badges (`🐱 88%`), card highlight, bounding box overlays, and intelligent candidate auto-zoom.
- **Prioritize by Cat Presence**: Auto-sort toggle (`🐱 Prioritize`) sorts cameras with detected cats to the front by count and confidence.
- **Inline RTSP Live Streaming**: Toggle `▶` on any camera card (or inside the zoom modal) to stream directly in the browser via `ffmpeg`.
- **In-App Settings (`⚙️`)**: Add, remove, and reorder cameras, validate reachability & Yi-Hack firmware, and configure refresh interval and detection sensitivity directly from the web UI (`config.json`).

## Requirements

- Python 3.10+
- `requirements.txt` dependencies (`ultralytics`)
- `ffmpeg` (optional, required only for `▶` live streaming)
- One or more cameras running [yi-hack-Allwinner-v2](https://github.com/roleoroleo/yi-hack-Allwinner-v2)

## Quickstart

### Option A: Docker (Recommended for Linux / Intel N100)

With Docker Compose:
```sh
docker compose up -d
```

Or standalone `docker run`:
```sh
docker build -t gatochecker .
docker run -d --name gatochecker -p 8000:8000 -v $(pwd)/data:/app/data -e GATO_CONFIG=/app/data/config.json gatochecker
```

### Option B: Local Python (Mac / Linux)

```sh
uv venv --python 3.12 .venv && uv pip install -r requirements.txt
# or: python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt
python3 server.py [port] [--debug]
```

## Hardware Acceleration

GatoChecker automatically detects and utilizes available hardware accelerators:

### 🍏 Apple Silicon (M1 / M2 / M3 / M4 / M5)
- **Recommendation:** Run **natively** (Option B).
- **Why:** Docker Desktop for Mac runs inside a virtualized Linux VM where Apple Metal / MPS GPU passthrough is not supported. Running natively enables PyTorch's Metal Performance Shaders (`mps`) directly on the Mac GPU/Unified Memory (~150 ms inference).

### 🟢 NVIDIA GPU (Linux / Docker)
If running on a machine with an NVIDIA GeForce / RTX GPU:
1. Ensure [NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html) is installed on the host.
2. Build the Docker image with CUDA wheels:
   ```sh
   docker build --build-arg TORCH_INDEX=cuda -t gatochecker:gpu .
   ```
3. Run with GPU passthrough:
   ```sh
   docker run -d --name gatochecker --gpus all -p 8000:8000 -v $(pwd)/config.json:/app/config.json gatochecker:gpu
   ```
   Or in `docker-compose.yml`, uncomment the `TORCH_INDEX: cuda` build arg and `deploy.resources.reservations.devices` block.

### 💻 Intel N100 / Low-Power CPU (Linux / Mini-PC)
- **Recommendation:** Standard Docker Compose (`docker compose up -d`).
- The default Docker image installs PyTorch CPU-only wheels (saving ~2.5 GB of CUDA bloat) and runs efficiently under ~200 MB of RAM with minimal CPU usage.
