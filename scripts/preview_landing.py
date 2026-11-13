"""Local preview of the public site with live data.

Serves the LOCAL working copy at the same paths production uses —

    /               → landing/welcome.html
    /kbz/viewer/…   → viewer/
    /app/…          → app/
    everything else → landing/

— and proxies the remaining /kbz/* API calls to production, so live
widgets (highlights, the HUD numbers, the viewer's feed) render with real
data while every byte of HTML/CSS/JS comes from your checkout.

Read-only on purpose: non-GET requests are refused, so pressing Play,
Pause or Turbo in a preview can never change the live simulation.
WebSockets aren't proxied either; the viewer falls back to polling.
"""
import http.server
import os
import urllib.request

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UPSTREAM = "https://kibbutznik.org"
LOCAL_PREFIXES = [
    ("/kbz/viewer/", os.path.join(REPO, "viewer")),
    ("/app/", os.path.join(REPO, "app")),
]
LANDING = os.path.join(REPO, "landing")


class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *a, **k):
        super().__init__(*a, directory=LANDING, **k)

    def translate_path(self, path):
        clean = path.split("?", 1)[0].split("#", 1)[0]
        for prefix, root in LOCAL_PREFIXES:
            if clean.startswith(prefix):
                rel = clean[len(prefix):] or "index.html"
                return os.path.join(root, rel)
        return super().translate_path(path)

    def do_GET(self):
        path, _, query = self.path.partition("?")
        if path.startswith("/kbz/") and not path.startswith("/kbz/viewer/"):
            return self._proxy()
        if path in ("/", ""):
            self.path = "/welcome.html" + ("?" + query if query else "")
        return super().do_GET()

    def _proxy(self):
        try:
            with urllib.request.urlopen(UPSTREAM + self.path, timeout=15) as r:
                body = r.read()
                ctype = r.headers.get("Content-Type", "application/json")
                status = r.status
        except urllib.error.HTTPError as e:
            body, ctype, status = e.read(), e.headers.get("Content-Type", "text/plain"), e.code
        except Exception as e:  # noqa: BLE001 — preview only
            return self.send_error(502, str(e))
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _refuse(self):
        self.send_error(405, "Preview is read-only — it never writes to production.")

    do_POST = do_PUT = do_PATCH = do_DELETE = _refuse

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()


if __name__ == "__main__":
    http.server.ThreadingHTTPServer(("127.0.0.1", 8766), Handler).serve_forever()
