#!/usr/bin/env python3
from concurrent.futures import ThreadPoolExecutor
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import io
import json
from pathlib import Path
import shutil
import subprocess
import sys
import threading
from urllib.parse import parse_qs, urlparse
from urllib.request import urlopen

HOSTS_FILE = Path(__file__).with_name("hosts.json")
MODEL_FILE = Path(__file__).with_name("yolov8n.pt")
FFMPEG = shutil.which("ffmpeg") or "ffmpeg"
CAPS_CACHE = {}
DETECTIONS = {}
YOLO_LOCK = threading.Lock()
YOLO_MODEL = None

try:
    from PIL import Image
    from ultralytics import YOLO
except ImportError:
    venv_py = Path(__file__).with_name(".venv") / "bin" / "python"
    if venv_py.exists() and Path(sys.executable).resolve() != venv_py.resolve():
        import os
        os.execv(str(venv_py), [str(venv_py), *sys.argv])
    YOLO = None


def detect_cats(raw_bytes):
    global YOLO_MODEL
    if YOLO is None:
        return []
    # ponytail: global lock around YOLOv8n inference; move to process pool if >10 cameras refresh simultaneously
    with YOLO_LOCK:
        if YOLO_MODEL is None:
            YOLO_MODEL = YOLO(str(MODEL_FILE))
        img = Image.open(io.BytesIO(raw_bytes))
        res = YOLO_MODEL.predict(img, classes=[15], conf=0.25, verbose=False)[0]
    cats = []
    for b in res.boxes:
        x, y, w, h = b.xywhn[0].tolist()
        cats.append({
            "conf": round(float(b.conf.item()), 2),
            "box": [round(x - w / 2, 4), round(y - h / 2, 4), round(w, 4), round(h, 4)],
        })
    return cats

def get_hosts():
    if not HOSTS_FILE.exists():
        return []
    raw = json.loads(HOSTS_FILE.read_text())
    return raw if isinstance(raw, list) else raw.get("hosts", [])


def get_host_caps(host):
    if host in CAPS_CACHE:
        return CAPS_CACHE[host]
    rtsp_host = urlparse(f"http://{host}").hostname or host
    try:
        with urlopen(f"http://{host}/cgi-bin/links.sh", timeout=2) as resp:
            links = json.loads(resp.read().decode())
        hi_snap = f"http://{host}/cgi-bin/snapshot.sh?res=high" if "high_res_snapshot" in links else None
        lo_snap = f"http://{host}/cgi-bin/snapshot.sh?res=low" if "low_res_snapshot" in links else None
        hi_path = urlparse(links["high_res_stream"]).path if "high_res_stream" in links else None
        lo_path = urlparse(links["low_res_stream"]).path if "low_res_stream" in links else None
        hi_stream = f"rtsp://{rtsp_host}{hi_path}" if hi_path else None
        lo_stream = f"rtsp://{rtsp_host}{lo_path}" if lo_path else None
        caps = {
            "high_res_snapshot": hi_snap,
            "low_res_snapshot": lo_snap,
            "high_res_stream": hi_stream,
            "low_res_stream": lo_stream,
            "default_snapshot": hi_snap or lo_snap or f"http://{host}/cgi-bin/snapshot.sh?res=high",
            "default_stream": lo_stream or hi_stream or f"rtsp://{rtsp_host}/ch0_0.h264",
        }
        CAPS_CACHE[host] = caps
        return caps
    except Exception:
        return {
            "high_res_snapshot": f"http://{host}/cgi-bin/snapshot.sh?res=high",
            "low_res_snapshot": None,
            "high_res_stream": f"rtsp://{rtsp_host}/ch0_0.h264",
            "low_res_stream": None,
            "default_snapshot": f"http://{host}/cgi-bin/snapshot.sh?res=high",
            "default_stream": f"rtsp://{rtsp_host}/ch0_0.h264",
        }

class Handler(SimpleHTTPRequestHandler):
    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/hosts.json" and not HOSTS_FILE.exists():
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            return self.wfile.write(b'{"refresh":10000,"hosts":[]}')
        if parsed.path == "/capabilities":
            hosts = get_hosts()
            with ThreadPoolExecutor(max_workers=max(1, len(hosts))) as pool:
                caps = dict(zip(hosts, pool.map(get_host_caps, hosts)))
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            return self.wfile.write(json.dumps(caps).encode())
        if parsed.path == "/detections":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            return self.wfile.write(json.dumps(DETECTIONS).encode())
        if parsed.path == "/snapshot":
            params = parse_qs(parsed.query)
            host = params.get("host", [""])[0]
            if host not in get_hosts():
                return self.send_error(403, "Unknown host")
            snap_url = get_host_caps(host)["default_snapshot"]
            try:
                with urlopen(snap_url, timeout=5) as resp:
                    raw = resp.read()
                    ctype = resp.headers.get("Content-Type", "image/jpeg")
            except Exception:
                return self.send_error(502, "Camera unreachable")
            cats = detect_cats(raw)
            DETECTIONS[host] = {"cat": bool(cats), "cats": cats}
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            return self.wfile.write(raw)
        if parsed.path != "/stream":
            return super().do_GET()
        params = parse_qs(parsed.query)
        host = params.get("host", [""])[0]
        if host not in get_hosts():
            return self.send_error(403, "Unknown host")
        caps = get_host_caps(host)
        res_pref = params.get("res", [""])[0]
        stream_url = (
            caps["high_res_stream"] if res_pref == "high" and caps["high_res_stream"]
            else caps["low_res_stream"] if res_pref == "low" and caps["low_res_stream"]
            else caps["default_stream"]
        )

        # ponytail: ffmpeg RTSP->MJPEG over HTTP; switch to mediamtx/WebRTC only if audio or 30fps+ is needed
        proc = subprocess.Popen(
            [
                FFMPEG, "-loglevel", "error", "-rtsp_transport", "tcp",
                "-i", stream_url,
                "-r", "10", "-q:v", "5", "-f", "mpjpeg", "pipe:1",
            ],
            stdout=subprocess.PIPE,
        )
        try:
            self.send_response(200)
            self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=ffmpeg")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            while chunk := proc.stdout.read1(16384):
                self.wfile.write(chunk)
                self.wfile.flush()
        except (BrokenPipeError, ConnectionError):
            pass
        finally:
            proc.kill()
            proc.wait()

    def do_PUT(self):
        if self.path != "/hosts.json":
            return self.send_error(404)
        try:
            raw = self.rfile.read(int(self.headers.get("Content-Length", 0)))
            data = json.loads(raw)
            assert isinstance(data, dict) and isinstance(data.get("hosts"), list)
            hosts = [str(h).strip() for h in data["hosts"] if str(h).strip()]
            refresh = int(data.get("refresh", 10000))
        except Exception:
            return self.send_error(400, "Invalid config")

        HOSTS_FILE.write_text(json.dumps({"refresh": refresh, "hosts": hosts}, indent=2) + "\n")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"ok":true}')


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8000
    print(f"GatoChecker listening on http://127.0.0.1:{port}")
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()
