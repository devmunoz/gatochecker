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
from urllib.parse import parse_qs, parse_qsl, urlencode, urlparse
from urllib.request import Request, urlopen

DEBUG = "--debug" in sys.argv or "-v" in sys.argv or os.environ.get("DEBUG") in ("1", "true", "True")
def get_config_file():
    p = Path(os.environ.get("GATO_CONFIG", Path(__file__).with_name("config.json")))
    if p.is_dir():
        return p / "config.json"
    return p

LEGACY_HOSTS_FILE = Path(__file__).with_name("hosts.json")
FFMPEG = shutil.which("ffmpeg") or "ffmpeg"
CAPS_CACHE = {}
DETECTIONS = {}
SNAP_CACHE = {}
HOST_LOCKS = {}
HOST_LOCKS_GUARD = threading.Lock()
YOLO_LOCK = threading.Lock()
YOLO_MODEL = None
ML_QUEUE = {}
ML_COND = threading.Condition()

try:
    from PIL import Image
    import torch
    from ultralytics import RTDETR, YOLO
    if os.environ.get("DEVICE"):
        DEVICE = os.environ["DEVICE"]
    elif torch.cuda.is_available():
        DEVICE = "cuda"
    elif torch.backends.mps.is_available():
        DEVICE = "mps"
    else:
        DEVICE = "cpu"
except ImportError:
    venv_py = Path(__file__).with_name(".venv") / "bin" / "python"
    if venv_py.exists() and Path(sys.executable).resolve() != venv_py.resolve():
        os.execv(str(venv_py), [str(venv_py), *sys.argv])
    YOLO = None
    RTDETR = None
    DEVICE = "cpu"

DEFAULT_MODEL = "yolo11s.pt" if DEVICE == "cpu" else "rtdetr-l.pt"
MODEL_FILE = Path(__file__).with_name(os.environ.get("YOLO_MODEL", DEFAULT_MODEL))
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


def enqueue_ml(host, raw_bytes):
    with ML_COND:
        ML_QUEUE[host] = raw_bytes
        prev = DETECTIONS.get(host, {"cat": False, "cats": []})
        DETECTIONS[host] = {**prev, "pending": True}
        ML_COND.notify()


def _ml_worker():
    while True:
        with ML_COND:
            while not ML_QUEUE:
                ML_COND.wait()
            host = next(iter(ML_QUEUE))
            raw = ML_QUEUE.pop(host)
        try:
            cats = detect_cats(raw, host=host)
            with ML_COND:
                still_queued = host in ML_QUEUE
            DETECTIONS[host] = {"cat": bool(cats), "cats": cats, "pending": still_queued, "ts": int(time.time() * 1000)}
        except Exception as e:
            if DEBUG:
                sys.stderr.write(f"[ML {host}] error: {e}\n")
            DETECTIONS[host] = {**DETECTIONS.get(host, {"cat": False, "cats": []}), "pending": False}


threading.Thread(target=_ml_worker, daemon=True).start()


def fetch_camera_snapshot(host):
    with HOST_LOCKS_GUARD:
        lock = HOST_LOCKS.setdefault(host, threading.Lock())
    with lock:
        now = time.time()
        cached = SNAP_CACHE.get(host)
        if cached and (now - cached[0]) < 2.0:
            return cached[1], cached[2]
        snap_url = get_host_caps(host)["default_snapshot"]
        req = Request(snap_url, headers={"User-Agent": "GatoChecker/1.0"})
        with urlopen(req, timeout=15) as resp:
            raw = resp.read()
            ctype = resp.headers.get("Content-Type", "image/jpeg")
        SNAP_CACHE[host] = (time.time(), raw, ctype)
        enqueue_ml(host, raw)
        return raw, ctype
DEFAULT_SNAP_PATH = "/cgi-bin/snapshot.sh?res=high"
DEFAULT_STREAM_PATH = "/ch0_1.h264"

def clean_snap_path(path):
    if not path:
        return DEFAULT_SNAP_PATH
    ps = urlparse(path)
    if "watermark" not in ps.query.lower():
        return path
    pairs = [(k, v) for k, v in parse_qsl(ps.query, keep_blank_values=True) if k.lower() != "watermark"]
    q = urlencode(pairs)
    prefix = f"{ps.scheme}://{ps.netloc}" if ps.netloc else ""
    base = f"{prefix}{ps.path}" if (prefix or ps.path) else path.split("?")[0]
    return f"{base}?{q}" if q else base


def normalize_cam_entry(item):
    if isinstance(item, str):
        h = item.strip()
        return {"host": h, "snapshot": DEFAULT_SNAP_PATH, "stream": DEFAULT_STREAM_PATH} if h else None
    if isinstance(item, dict):
        h = str(item.get("host", "")).strip()
        if not h:
            return None
        raw_snap = str(item.get("snapshot", "")).strip() or DEFAULT_SNAP_PATH
        snap = clean_snap_path(raw_snap)
        stream = str(item.get("stream", "")).strip() or DEFAULT_STREAM_PATH
        return {"host": h, "snapshot": snap, "stream": stream}
    return None


def resolve_cam_url(host, val, scheme="http"):
    val = (val or "").strip()
    if val.startswith(("http://", "https://", "rtsp://", "rtsps://")):
        return val
    base_host = urlparse(f"http://{host}").hostname or host if scheme == "rtsp" else host
    if not val.startswith(("/", ":")):
        val = "/" + val
    return f"{scheme}://{base_host}{val}"


def get_config():
    target = get_config_file()
    if not target.exists() and LEGACY_HOSTS_FILE.exists() and not LEGACY_HOSTS_FILE.is_dir():
        target = LEGACY_HOSTS_FILE
    if not target.exists() or target.is_dir():
        return {"refresh": 10000, "min_conf": 18, "hosts": []}
    try:
        raw = json.loads(target.read_text())
        raw_list = raw if isinstance(raw, list) else raw.get("hosts", [])
        hosts = [c for c in (normalize_cam_entry(x) for x in raw_list) if c]
        if isinstance(raw, list):
            return {"refresh": 10000, "min_conf": 18, "hosts": hosts}
        return {
            "refresh": int(raw.get("refresh", 10000)),
            "min_conf": int(raw.get("min_conf", 18)),
            "hosts": hosts,
        }
    except Exception:
        return {"refresh": 10000, "min_conf": 18, "hosts": []}


def get_hosts():
    return [c["host"] for c in get_config()["hosts"]]


def get_cam_entry(host):
    for c in get_config()["hosts"]:
        if c["host"] == host:
            return c
    return {"host": host, "snapshot": DEFAULT_SNAP_PATH, "stream": DEFAULT_STREAM_PATH}


def get_host_caps(host):
    cam = get_cam_entry(host)
    cache_key = (host, cam["snapshot"], cam["stream"])
    if cache_key in CAPS_CACHE:
        return CAPS_CACHE[cache_key]
    rtsp_host = urlparse(f"http://{host}").hostname or host
    custom_snap = resolve_cam_url(host, cam["snapshot"], "http")
    custom_stream = resolve_cam_url(host, cam["stream"], "rtsp")
    is_default_yi = (cam["snapshot"] == DEFAULT_SNAP_PATH and cam["stream"] in (DEFAULT_STREAM_PATH, "/ch0_0.h264"))
    if is_default_yi:
        try:
            req = Request(f"http://{host}/cgi-bin/links.sh", headers={"User-Agent": "GatoChecker/1.0"})
            with urlopen(req, timeout=4) as resp:
                links = json.loads(resp.read().decode())
            hi_snap = f"http://{host}/cgi-bin/snapshot.sh?res=high" if "high_res_snapshot" in links else None
            lo_snap = f"http://{host}/cgi-bin/snapshot.sh?res=low" if "low_res_snapshot" in links else None
            hi_path = urlparse(links["high_res_stream"]).path if "high_res_stream" in links else None
            lo_path = urlparse(links["low_res_stream"]).path if "low_res_stream" in links else None
            hi_stream = f"rtsp://{rtsp_host}{hi_path}" if hi_path else None
            lo_stream = f"rtsp://{rtsp_host}{lo_path}" if lo_path else None
            caps = {
                "high_res_snapshot": hi_snap or custom_snap,
                "low_res_snapshot": lo_snap,
                "high_res_stream": hi_stream or custom_stream,
                "low_res_stream": lo_stream,
                "default_snapshot": custom_snap,
                "default_stream": custom_stream,
            }
            CAPS_CACHE[cache_key] = caps
            return caps
        except Exception:
            pass
    caps = {
        "high_res_snapshot": custom_snap,
        "low_res_snapshot": None,
        "high_res_stream": custom_stream,
        "low_res_stream": None,
        "default_snapshot": custom_snap,
        "default_stream": custom_stream,
    }
    CAPS_CACHE[cache_key] = caps
    return caps
class Handler(SimpleHTTPRequestHandler):
    def _send_json(self, payload, status=200):
        try:
            body = json.dumps(payload).encode() if not isinstance(payload, bytes) else payload
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionError):
            pass

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path in ("/config.json", "/hosts.json"):
            return self._send_json(get_config())
        if parsed.path == "/validate":
            params = parse_qs(parsed.query)
            host = params.get("host", [""])[0].strip()
            snap_param = params.get("snapshot", [DEFAULT_SNAP_PATH])[0].strip() or DEFAULT_SNAP_PATH
            if not host:
                return self._send_json({"ok": False, "error": "Host is required"}, status=400)
            info = {}
            status_data = None
            links_data = None
            try:
                req = Request(f"http://{host}/cgi-bin/status.json", headers={"User-Agent": "GatoChecker/1.0"})
                with urlopen(req, timeout=3.0) as resp:
                    status_data = json.loads(resp.read().decode())
            except Exception:
                pass
            try:
                req = Request(f"http://{host}/cgi-bin/links.sh", headers={"User-Agent": "GatoChecker/1.0"})
                with urlopen(req, timeout=3.0) as resp:
                    links_data = json.loads(resp.read().decode())
            except Exception:
                pass
            if status_data or (isinstance(links_data, dict) and any(k in links_data for k in ("high_res_snapshot", "low_res_snapshot", "high_res_stream", "low_res_stream"))):
                disc_snap = DEFAULT_SNAP_PATH
                disc_stream = DEFAULT_STREAM_PATH
                if isinstance(links_data, dict):
                    raw_snap = links_data.get("high_res_snapshot") or links_data.get("low_res_snapshot")
                    if raw_snap:
                        ps = urlparse(raw_snap)
                        disc_snap = clean_snap_path(f"{ps.path}?{ps.query}" if ps.query else (ps.path or DEFAULT_SNAP_PATH))
                    raw_stream = links_data.get("low_res_stream") or links_data.get("high_res_stream")
                    if raw_stream:
                        disc_stream = urlparse(raw_stream).path or DEFAULT_STREAM_PATH
                info = {
                    "ok": True,
                    "is_yihack": True,
                    "name": (status_data or {}).get("name", "yi-hack"),
                    "hostname": (status_data or {}).get("hostname", host),
                    "model": (status_data or {}).get("model_suffix", ""),
                    "fw": (status_data or {}).get("fw_version", ""),
                    "snapshot": disc_snap,
                    "stream": disc_stream,
                }
            else:
                snap_url = resolve_cam_url(host, snap_param, "http")
                try:
                    req = Request(snap_url, headers={"User-Agent": "GatoChecker/1.0"})
                    with urlopen(req, timeout=5) as resp:
                        ctype = resp.headers.get("Content-Type", "")
                        head = resp.read(64)
                        if "image" in ctype.lower() or head.startswith((b"\xff\xd8", b"\x89PNG")):
                            info = {"ok": True, "is_yihack": False, "name": "camera", "hostname": host}
                        else:
                            info = {"ok": False, "error": f"Endpoint did not return an image ({ctype or 'unknown'})"}
                except Exception as e:
                    info = {"ok": False, "error": f"Unreachable ({e})"}
            return self._send_json(info)
        if parsed.path == "/capabilities":
            hosts = get_hosts()
            with ThreadPoolExecutor(max_workers=max(1, len(hosts))) as pool:
                caps = dict(zip(hosts, pool.map(get_host_caps, hosts)))
            return self._send_json(caps)
        if parsed.path == "/detections":
            return self._send_json(DETECTIONS)
        if parsed.path == "/snapshot":
            params = parse_qs(parsed.query)
            host = params.get("host", [""])[0]
            if host not in get_hosts():
                return self.send_error(403, "Unknown host")
            try:
                raw, ctype = fetch_camera_snapshot(host)
            except Exception as e:
                sys.stderr.write(f"Snapshot error for {host}: {e}\n")
                try:
                    return self.send_error(502, f"Camera unreachable: {e}")
                except (BrokenPipeError, ConnectionError):
                    return
            try:
                self.send_response(200)
                self.send_header("Content-Type", ctype)
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                return self.wfile.write(raw)
            except (BrokenPipeError, ConnectionError):
                return
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
            hosts = [c for c in (normalize_cam_entry(h) for h in data["hosts"]) if c]
            refresh = int(data.get("refresh", 10000))
            min_conf = max(5, min(95, int(data.get("min_conf", 18))))
        except Exception:
            return self.send_error(400, "Invalid config")

        CAPS_CACHE.clear()
        target = get_config_file()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps({"refresh": refresh, "min_conf": min_conf, "hosts": hosts}, indent=2) + "\n")
        return self._send_json({"ok": True})


if __name__ == "__main__":
    port_args = [a for a in sys.argv[1:] if not a.startswith("-")]
    port = int(port_args[0]) if port_args else 8000
    print(f"GatoChecker listening on http://127.0.0.1:{port} [model={MODEL_FILE.name} device={DEVICE}]" + (" [ML DEBUG ON]" if DEBUG else ""))
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()
