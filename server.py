#!/usr/bin/env python3
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import shutil
import subprocess
import sys
from urllib.parse import parse_qs, urlparse

HOSTS_FILE = Path(__file__).with_name("hosts.json")
FFMPEG = shutil.which("ffmpeg") or "/Users/duni/.local/bin/ffmpeg"


def get_hosts():
    if not HOSTS_FILE.exists():
        return []
    raw = json.loads(HOSTS_FILE.read_text())
    return raw if isinstance(raw, list) else raw.get("hosts", [])


class Handler(SimpleHTTPRequestHandler):
    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/hosts.json" and not HOSTS_FILE.exists():
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            return self.wfile.write(b'{"refresh":10000,"hosts":[]}')
        if parsed.path != "/stream":
            return super().do_GET()
        host = parse_qs(parsed.query).get("host", [""])[0]
        if host not in get_hosts():
            return self.send_error(403, "Unknown host")
        rtsp_host = urlparse(f"http://{host}").hostname or host

        # ponytail: ffmpeg RTSP->MJPEG over HTTP; switch to mediamtx/WebRTC only if audio or 30fps+ is needed
        proc = subprocess.Popen(
            [
                FFMPEG, "-loglevel", "error", "-rtsp_transport", "tcp",
                "-i", f"rtsp://{rtsp_host}/ch0_1.h264",
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
