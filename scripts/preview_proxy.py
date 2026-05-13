#!/usr/bin/env python3
"""Tiny reverse proxy from localhost:8765 → https://kibbutznik.org.

For local visual audit only — not for production. Used to point Claude
Preview at the deployed site since the preview tool requires a localhost
target. Strips encoding headers, follows redirects, rewrites no URLs
(relative paths still work because the host is virtual)."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import urllib.request
import urllib.error
import sys

TARGET = "https://kibbutznik.org"
PORT = 8765

SKIP_RESP_HEADERS = {"transfer-encoding", "content-encoding", "connection",
                      "content-length", "strict-transport-security",
                      "x-frame-options", "content-security-policy"}


class Proxy(BaseHTTPRequestHandler):
    def _proxy(self, method):
        url = TARGET + self.path
        body = None
        length = int(self.headers.get("Content-Length") or 0)
        if length > 0:
            body = self.rfile.read(length)
        req_headers = {}
        for h in self.headers:
            if h.lower() in ("host", "connection", "accept-encoding"):
                continue
            req_headers[h] = self.headers[h]
        req_headers["User-Agent"] = "Claude-Preview-Audit"
        req_headers["Accept-Encoding"] = "identity"
        req = urllib.request.Request(url, data=body, method=method, headers=req_headers)
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                self.send_response(r.status)
                for h, v in r.getheaders():
                    if h.lower() in SKIP_RESP_HEADERS:
                        continue
                    self.send_header(h, v)
                self.end_headers()
                self.wfile.write(r.read())
        except urllib.error.HTTPError as e:
            self.send_response(e.code)
            self.end_headers()
            try:
                self.wfile.write(e.read())
            except Exception:
                pass
        except Exception as e:
            self.send_error(502, f"proxy error: {e}")

    def do_GET(self): self._proxy("GET")
    def do_POST(self): self._proxy("POST")
    def do_HEAD(self): self._proxy("HEAD")
    def log_message(self, fmt, *args): sys.stdout.write("[proxy] " + fmt % args + "\n")


if __name__ == "__main__":
    print(f"[proxy] kibbutznik.org → http://127.0.0.1:{PORT}", flush=True)
    ThreadingHTTPServer(("127.0.0.1", PORT), Proxy).serve_forever()
