"""Simple static server for Automate Pro React SPA."""
import os
from http.server import HTTPServer, SimpleHTTPRequestHandler


class SPAHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory="react-dist", **kwargs)

    def do_GET(self):
        path = self.path.lstrip("/")
        full = os.path.join("react-dist", path)
        if path and os.path.isfile(full):
            return super().do_GET()
        # SPA fallback — serve index.html for any unmatched route
        self.path = "/index.html"
        return super().do_GET()


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 3000))
    server = HTTPServer(("0.0.0.0", port), SPAHandler)
    print(f"Serving at http://0.0.0.0:{port}")
    server.serve_forever()
