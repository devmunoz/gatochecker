# 🐱 gatochecker

A lightweight, zero-dependency camera dashboard and control panel for cameras running [yi-hack-Allwinner-v2](https://github.com/roleoroleo/yi-hack-Allwinner-v2).

## Origin

**gatochecker** was born out of a simple everyday problem: knowing where the cats are at home without opening each camera feed one by one.

## Features

- **Unified Snapshot Grid**: Fetches high-resolution snapshots across all configured cameras with configurable auto-refresh.
- **Inline RTSP Live Streaming**: Toggle `▶ RTSP` on any camera card (or inside the zoom modal) to stream directly in the browser via `ffmpeg`.
- **Drag-and-Drop Reordering**: Reorder cameras directly in the main grid or inside the settings modal.
- **In-App Settings (`⚙️`)**: Add, remove, and reorder cameras and configure the default refresh interval directly from the web UI.

## Requirements

- Python 3
- `ffmpeg` (optional, required only for `▶ RTSP` live streaming)
- One or more cameras running [yi-hack-Allwinner-v2](https://github.com/roleoroleo/yi-hack-Allwinner-v2)

## Usage

```sh
python3 server.py [port]
```

Then open `http://127.0.0.1:8000` in your browser.
