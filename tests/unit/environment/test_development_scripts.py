"""Development checks must work with the workstation's normal web proxy."""
import importlib.util
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from threading import Thread


def test_webdriver_requests_bypass_web_proxy(monkeypatch):
    monkeypatch.setenv("http_proxy", "http://127.0.0.1:1")
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:1")
    monkeypatch.setenv("no_proxy", "")
    monkeypatch.setenv("NO_PROXY", "")

    path = Path(__file__).resolve().parents[3] / "scripts/_webdriver.py"
    spec = importlib.util.spec_from_file_location("skyseed_test_webdriver", path)
    webdriver = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(webdriver)

    class StatusHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"value": {"ready": true}}')

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), StatusHandler)
    thread = Thread(target=server.serve_forever)
    thread.start()
    try:
        port = server.server_address[1]
        assert webdriver._request(f"http://127.0.0.1:{port}/status") == {"ready": True}
    finally:
        server.shutdown()
        thread.join()
        server.server_close()
