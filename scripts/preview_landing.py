"""Local preview for the landing site: serves landing/ as static files and
proxies /kbz/* to production so live widgets render with real data."""
import http.server
import urllib.request

ROOT = "/Users/uriee/claude/kbz/landing"
UPSTREAM = "https://kibbutznik.org"


class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *a, **k):
        super().__init__(*a, directory=ROOT, **k)

    def do_GET(self):
        if self.path.startswith("/kbz/"):
            try:
                with urllib.request.urlopen(UPSTREAM + self.path, timeout=10) as r:
                    body = r.read()
                    ctype = r.headers.get("Content-Type", "application/json")
                self.send_response(200)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            except Exception as e:  # noqa: BLE001 — preview only
                self.send_error(502, str(e))
            return
        path, _, query = self.path.partition("?")
        if path in ("/", ""):
            self.path = "/welcome.html" + ("?" + query if query else "")
        return super().do_GET()

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()


if __name__ == "__main__":
    http.server.ThreadingHTTPServer(("127.0.0.1", 8766), Handler).serve_forever()
