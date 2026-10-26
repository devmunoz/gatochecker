#!/usr/bin/env python3
from concurrent.futures import ThreadPoolExecutor
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading
import time
from urllib.parse import parse_qs, urlparse
from urllib.request import Request, urlopen

DEBUG = "--debug" in sys.argv or "-v" in sys.argv or os.environ.get("DEBUG") in ("1", "true", "True")
CONFIG_FILE = Path(os.environ.get("GATO_CONFIG", Path(__file__).with_name("config.json")))
LEGACY_HOSTS_FILE = Path(__file__).with_name("hosts.json")
MODEL_FILE = Path(__file__).with_name(os.environ.get("YOLO_MODEL", "rtdetr-l.pt"))
FFMPEG = shutil.which("ffmpeg") or "ffmpeg"
CAPS_CACHE = {}
DETECTIONS = {}
YOLO_LOCK = threading.Lock()
YOLO_MODEL = None

try:
    from PIL import Image
    import torch
    from ultralytics import RTDETR, YOLO
    DEVICE = "mps" if torch.backends.mps.is_available() else "cpu"
except ImportError:
    venv_py = Path(__file__).with_name(".venv") / "bin" / "python"
    if venv_py.exists() and Path(sys.executable).resolve() != venv_py.resolve():
        os.execv(str(venv_py), [str(venv_py), *sys.argv])
    YOLO = None
    DEVICE = "cpu"


def _overlap(b1, b2):
    x1, y1 = max(b1[0], b2[0]), max(b1[1], b2[1])
    x2, y2 = min(b1[0] + b1[2], b2[0] + b2[2]), min(b1[1] + b1[3], b2[1] + b2[3])
    inter = max(0.0, x2 - x1) * max(0.0, y2 - y1)
    if inter <= 0:
        return 0.0
    return inter / min(b1[2] * b1[3], b2[2] * b2[3])
def detect_cats(raw_bytes, host=""):
    global YOLO_MODEL
    if YOLO is None:
        return []
    t0 = time.time()
    is_rtdetr = "rtdetr" in MODEL_FILE.name.lower()
    # ponytail: global lock around model inference; move to process pool if >10 cameras refresh simultaneously
    with YOLO_LOCK:
        if YOLO_MODEL is None:
            YOLO_MODEL = RTDETR(str(MODEL_FILE)) if is_rtdetr else YOLO(str(MODEL_FILE))
        img = Image.open(io.BytesIO(raw_bytes))
        w_img, h_img = img.size
        min_conf = get_config().get("min_conf", 18) / 100.0
        conf_thresh = 0.07 if DEBUG else min_conf
        scales = (640, 960) if is_rtdetr else (1280,)
        results = [
            YOLO_MODEL.predict(img, conf=0.07, imgsz=sz, device=DEVICE, verbose=False)[0]
            for sz in scales
        ]
        raw_cats = []
        near_cats = []
        others = []
        zoom_centers = []
        for idx, res in enumerate(results):
            for b in res.boxes:
                cls_id = int(b.cls.item())
                conf = float(b.conf.item())
                name = YOLO_MODEL.names[cls_id]
                x, y, w, h = b.xywhn[0].tolist()
                box = [round(max(0.0, x - w / 2), 4), round(max(0.0, y - h / 2), 4), round(w, 4), round(h, 4)]
                if cls_id == 15 and conf >= min_conf:
                    raw_cats.append({"conf": round(conf, 2), "box": box})
                elif cls_id in (14, 15, 16, 21, 77) and conf >= 0.08:
                    if idx == 0:
                        near_cats.append(f"{name} {round(conf * 100)}%")
                    if not any(abs(x - cx) < 0.20 and abs(y - cy) < 0.20 for cx, cy, _ in zoom_centers):
                        zoom_centers.append((x, y, conf))
                elif DEBUG and idx == 0 and conf >= 0.30:
                    others.append(f"{name} {round(conf * 100)}%")

        # Stage 2: Zoom into top animal/plush candidates not yet confirmed as cats
        for cx, cy, _ in sorted(zoom_centers, key=lambda c: c[2], reverse=True)[:2]:
            if any(k["box"][0] <= cx <= k["box"][0] + k["box"][2] and k["box"][1] <= cy <= k["box"][1] + k["box"][3] for k in raw_cats):
                continue
            win = 0.55
            x1 = max(0.0, min(1.0 - win, cx - win / 2))
            y1 = max(0.0, min(1.0 - win, cy - win / 2))
            crop = img.crop((int(x1 * w_img), int(y1 * h_img), int((x1 + win) * w_img), int((y1 + win) * h_img)))
            for sz in scales:
                zres = YOLO_MODEL.predict(crop, conf=min_conf, imgsz=sz, device=DEVICE, verbose=False)[0]
                for zb in zres.boxes:
                    if int(zb.cls.item()) == 15:
                        zconf = float(zb.conf.item())
                        zx, zy, zw, zh = zb.xywhn[0].tolist()
                        gx = max(0.0, x1 + (zx - zw / 2) * win)
                        gy = max(0.0, y1 + (zy - zh / 2) * win)
                        raw_cats.append({
                            "conf": round(zconf, 2),
                            "box": [round(gx, 4), round(gy, 4), round(zw * win, 4), round(zh * win, 4)],
                        })
    elapsed = round((time.time() - t0) * 1000, 1)
    cats = []
    for cand in sorted(raw_cats, key=lambda c: c["conf"], reverse=True):
        if not any(_overlap(cand["box"], kept["box"]) > 0.15 for kept in cats):
            cats.append(cand)
    if DEBUG:
        cat_str = ", ".join([f"{round(c['conf'] * 100)}%" for c in cats]) if cats else "none"
        near_str = f" | candidates: {', '.join(near_cats)}" if near_cats else ""
        other_str = f" | scene: {', '.join(others[:6])}" if others else ""
        sys.stderr.write(f"[ML {host}] {len(cats)} cat(s) [{cat_str}] in {elapsed}ms ({DEVICE}){near_str}{other_str}\n")
    return cats

def get_config():
    target = CONFIG_FILE
    if not target.exists() and LEGACY_HOSTS_FILE.exists():
        target = LEGACY_HOSTS_FILE
    if not target.exists():
        return {"refresh": 10000, "min_conf": 18, "hosts": []}
    try:
        raw = json.loads(target.read_text())
        if isinstance(raw, list):
            return {"refresh": 10000, "min_conf": 18, "hosts": raw}
        return {
            "refresh": int(raw.get("refresh", 10000)),
            "min_conf": int(raw.get("min_conf", 18)),
            "hosts": raw.get("hosts", []),
        }
    except Exception:
        return {"refresh": 10000, "min_conf": 18, "hosts": []}

def get_hosts():
    return get_config()["hosts"]

def get_host_caps(host):
    if host in CAPS_CACHE:
        return CAPS_CACHE[host]
    rtsp_host = urlparse(f"http://{host}").hostname or host
    try:
        req = Request(f"http://{host}/cgi-bin/links.sh", headers={"User-Agent": "GatoChecker/1.0"})
        with urlopen(req, timeout=5) as resp:
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
        if parsed.path in ("/config.json", "/hosts.json"):
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            return self.wfile.write(json.dumps(get_config()).encode())
        if parsed.path == "/validate":
            params = parse_qs(parsed.query)
            host = params.get("host", [""])[0].strip()
            if not host:
                self.send_response(400)
                self.end_headers()
                return self.wfile.write(b'{"ok":false,"error":"Host is required"}')
            info = {}
            try:
                req = Request(f"http://{host}/cgi-bin/status.json", headers={"User-Agent": "GatoChecker/1.0"})
                with urlopen(req, timeout=3.5) as resp:
                    data = json.loads(resp.read().decode())
                    info = {
                        "ok": True,
                        "name": data.get("name", "yi-hack"),
                        "hostname": data.get("hostname", host),
                        "model": data.get("model_suffix", ""),
                        "fw": data.get("fw_version", ""),
                    }
            except Exception:
                try:
                    req = Request(f"http://{host}/cgi-bin/links.sh", headers={"User-Agent": "GatoChecker/1.0"})
                    with urlopen(req, timeout=3.5) as resp:
                        data = json.loads(resp.read().decode())
                        if "high_res_snapshot" in data or "low_res_stream" in data:
                            info = {"ok": True, "name": "yi-hack", "hostname": host}
                        else:
                            info = {"ok": False, "error": "Not a Yi-Hack camera (unexpected response)"}
                except Exception as e:
                    info = {"ok": False, "error": f"Unreachable ({e})"}
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            return self.wfile.write(json.dumps(info).encode())
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
                req = Request(snap_url, headers={"User-Agent": "GatoChecker/1.0"})
                with urlopen(req, timeout=15) as resp:
                    raw = resp.read()
                    ctype = resp.headers.get("Content-Type", "image/jpeg")
            except Exception as e:
                sys.stderr.write(f"Snapshot error for {host} ({snap_url}): {e}\n")
                return self.send_error(502, f"Camera unreachable: {e}")
            cats = detect_cats(raw, host=host)
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
        if self.path not in ("/config.json", "/hosts.json"):
            return self.send_error(404)
        try:
            raw = self.rfile.read(int(self.headers.get("Content-Length", 0)))
            data = json.loads(raw)
            assert isinstance(data, dict) and isinstance(data.get("hosts"), list)
            hosts = [str(h).strip() for h in data["hosts"] if str(h).strip()]
            refresh = int(data.get("refresh", 10000))
            min_conf = max(5, min(95, int(data.get("min_conf", 18))))
        except Exception:
            return self.send_error(400, "Invalid config")

        CONFIG_FILE.write_text(json.dumps({"refresh": refresh, "min_conf": min_conf, "hosts": hosts}, indent=2) + "\n")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"ok":true}')


if __name__ == "__main__":
    port_args = [a for a in sys.argv[1:] if not a.startswith("-")]
    port = int(port_args[0]) if port_args else 8000
    print(f"GatoChecker listening on http://127.0.0.1:{port}" + (" [ML DEBUG ON]" if DEBUG else ""))
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()
