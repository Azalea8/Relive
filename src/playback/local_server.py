"""Local HTTP server serving cache/videos/ for mpv playback."""
import os
import socket
import threading
from http.server import HTTPServer, SimpleHTTPRequestHandler

from src.logger import get as _log

log = _log("httpd")

_PORT = 18888
_server: HTTPServer | None = None
_server_thread: threading.Thread | None = None
HTTPServer.allow_reuse_address = False

class _Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=_ROOT, **kwargs)

    def log_message(self, fmt, *args):
        log.debug("HTTP %s", fmt % args)

    def handle_one_request(self):
        try:
            super().handle_one_request()
        except ConnectionResetError:
            pass


_ROOT = ""


def start(root_dir: str) -> int:
    """Start HTTP server serving *root_dir*. Returns the port."""
    global _ROOT, _server, _server_thread
    _ROOT = os.path.abspath(root_dir)
    os.makedirs(_ROOT, exist_ok=True)

    port = _PORT
    while True:
        try:
            _server = HTTPServer(("127.0.0.1", port), _Handler)
            break
        except OSError as e:
            log.info("port %d failed: %s", port, e)
            port += 1

    _server_thread = threading.Thread(target=_server.serve_forever, daemon=True)
    _server_thread.start()
    log.info("HTTP server started on http://127.0.0.1:%d serving %s", port, _ROOT)
    return port


def stop():
    global _server
    if _server:
        _server.shutdown()
        _server.server_close()
        _server = None
        log.info("HTTP server stopped")
