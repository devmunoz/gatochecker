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
docker run -d --name gatochecker -p 8000:8000 -v $(pwd)/config.json:/app/config.json gatochecker
```

### Option B: Local Python (Mac / Linux)

```sh
uv venv --python 3.12 .venv && uv pip install -r requirements.txt
# or: python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt
python3 server.py [port] [--debug]
```

Open `http://127.0.0.1:8000` in your browser.
