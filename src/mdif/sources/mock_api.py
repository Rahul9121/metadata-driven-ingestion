"""Tiny local mock HTTP API used by the demo pipeline.

Behaviour:
- fails the first N calls with 503
- returns 429 once
- serves one malformed page
- then returns clean product JSON
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any


PRODUCTS = [
    {"product_id": 1, "sku": "SKU-1", "name": "Widget", "price": 19.99},
    {"product_id": 2, "sku": "SKU-2", "name": "Gadget", "price": 49.5},
    {"product_id": 3, "sku": "SKU-3", "name": "Doohickey", "price": 5.0},
    {"product_id": 4, "sku": "SKU-4", "name": "Thingamajig", "price": 120.0},
]


class _State:
    def __init__(self, fail_first: int = 2):
        self.lock = threading.Lock()
        self.calls = 0
        self.fail_first = fail_first
        self.sent_429 = False
        self.sent_malformed = False


def make_handler(state: _State):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:  # noqa: A003
            return

        def do_GET(self) -> None:  # noqa: N802
            if self.path.rstrip("/") != "/products":
                self.send_response(404)
                self.end_headers()
                self.wfile.write(b'{"error":"not found"}')
                return

            with state.lock:
                state.calls += 1
                call = state.calls
                if call <= state.fail_first:
                    self.send_response(503)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(b'{"error":"temporarily unavailable"}')
                    return
                if not state.sent_429:
                    state.sent_429 = True
                    self.send_response(429)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Retry-After", "1")
                    self.end_headers()
                    self.wfile.write(b'{"error":"rate limited 429"}')
                    return
                if not state.sent_malformed:
                    state.sent_malformed = True
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(b'{"malformed": true, "data": "not-a-list"}')
                    return

            body = json.dumps({"data": PRODUCTS}).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    return Handler


class MockAPIServer:
    def __init__(self, host: str = "127.0.0.1", port: int = 8765, fail_first: int = 2):
        self.host = host
        self.port = port
        self.state = _State(fail_first=fail_first)
        self._httpd: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        handler = make_handler(self.state)
        self._httpd = ThreadingHTTPServer((self.host, self.port), handler)
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if self._httpd:
            self._httpd.shutdown()
            self._httpd.server_close()
        if self._thread:
            self._thread.join(timeout=2)

    def __enter__(self) -> MockAPIServer:
        self.start()
        return self

    def __exit__(self, *args: Any) -> None:
        self.stop()
