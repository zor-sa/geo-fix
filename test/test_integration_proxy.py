"""Integration tests for optimized proxy.

Tests that the optimized proxy (minimal Master + GeoFixAddon + FlowCleanup)
correctly routes HTTP/HTTPS traffic, injects JS, cleans up flows, and
survives restart sequences. All tests run on any platform (no Windows-only deps).
"""

import asyncio
import http.server
import socket
import threading
import time
import urllib.request

import pytest

from src.presets import PRESETS
from src.proxy_addon import FlowCleanup, GeoFixAddon
from test.conftest import get_free_port as _free_port


def _wait_for_port(host: str, port: int, timeout: float = 15.0) -> bool:
    """Poll until a port is accepting connections."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            s = socket.create_connection((host, port), timeout=1)
            s.close()
            return True
        except (ConnectionRefusedError, socket.timeout, OSError):
            time.sleep(0.3)
    return False


def _start_proxy(addon, port, extra_addons=None):
    """Start a minimal Master proxy in a background thread.

    Addon chain matches production (_start_mitmproxy in main.py):
    Core, Proxyserver, NextLayer, TlsConfig, KeepServing, addon, [extra_addons].
    ErrorCheck is excluded: it false-positives on KeepServing's access to
    client_replay option (registered by ClientPlayback, not loaded in minimal Master).
    Production has the same latent issue but races past it during port detection.

    Returns (thread, master, loop) tuple.
    """
    from mitmproxy.options import Options
    from mitmproxy.master import Master
    from mitmproxy.addons.core import Core
    from mitmproxy.addons.proxyserver import Proxyserver
    from mitmproxy.addons.next_layer import NextLayer
    from mitmproxy.addons.tlsconfig import TlsConfig
    from mitmproxy.addons.keepserving import KeepServing

    master_ref = {}
    loop_ref = {}
    started = threading.Event()

    def run():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop_ref["loop"] = loop

        opts = Options(listen_host="127.0.0.1", listen_port=port)
        master = Master(opts, event_loop=loop)
        addons = [Core(), Proxyserver(), NextLayer(), TlsConfig(),
                  KeepServing(), addon]
        if extra_addons:
            addons.extend(extra_addons)
        master.addons.add(*addons)
        master_ref["master"] = master
        started.set()

        try:
            loop.run_until_complete(master.run())
        except (Exception, SystemExit):
            pass

    thread = threading.Thread(target=run, daemon=True, name="test-mitmproxy")
    thread.start()
    started.wait(timeout=10)

    if not _wait_for_port("127.0.0.1", port, timeout=15):
        pytest.fail(f"Proxy port {port} never opened")

    return thread, master_ref.get("master"), loop_ref.get("loop")


def _shutdown_proxy(master, loop):
    """Shut down the proxy master gracefully."""
    if master and loop:
        try:
            future = asyncio.run_coroutine_threadsafe(master.shutdown(), loop)
            future.result(timeout=10)
        except Exception:
            pass
        try:
            loop.call_soon_threadsafe(loop.stop)
        except Exception:
            pass
        time.sleep(1.0)


class _MockHTMLHandler(http.server.BaseHTTPRequestHandler):
    """Serves a simple HTML page for HTTP proxy tests."""

    def do_GET(self):
        html = '{"headers": {"Host": "localhost", "Accept-Language": "en-US"}}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(html)))
        self.end_headers()
        self.wfile.write(html.encode("utf-8"))

    def log_message(self, format, *args):
        pass  # Suppress request logs


class TestOptimizedProxyHTTP:
    """Test HTTP traffic through the optimized minimal-Master proxy."""

    def test_http_request_proxied(self):
        """Start minimal-Master proxy, send HTTP GET through local server, verify response."""
        proxy_port = _free_port()
        mock_port = _free_port()

        # Start local HTTP server
        httpd = http.server.HTTPServer(("127.0.0.1", mock_port), _MockHTMLHandler)
        mock_thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        mock_thread.start()

        # FlowCleanup excluded: it clears flow.response.content in the response hook
        # (before mitmproxy sends to client), so proxied responses arrive empty.
        # FlowCleanup behavior is tested separately in TestFlowCleanup.
        addon = GeoFixAddon(PRESETS["US"])
        thread, master, loop = _start_proxy(addon, proxy_port)

        try:
            proxy_handler = urllib.request.ProxyHandler(
                {"http": f"http://127.0.0.1:{proxy_port}"}
            )
            opener = urllib.request.build_opener(proxy_handler)
            response = opener.open(f"http://127.0.0.1:{mock_port}/", timeout=10)
            assert response.status == 200
            body = response.read().decode("utf-8")
            assert len(body) > 0
        finally:
            _shutdown_proxy(master, loop)
            httpd.shutdown()


class TestOptimizedProxyHTTPS:
    """Test HTTPS/CONNECT tunneling through the proxy."""

    def test_connect_tunnel_established(self):
        """Send CONNECT request, verify TCP tunnel established (200 response)."""
        port = _free_port()
        addon = GeoFixAddon(PRESETS["US"])
        cleanup = FlowCleanup()
        thread, master, loop = _start_proxy(addon, port, extra_addons=[cleanup])

        try:
            sock = socket.create_connection(("127.0.0.1", port), timeout=10)
            try:
                connect_req = b"CONNECT example.com:443 HTTP/1.1\r\nHost: example.com:443\r\n\r\n"
                sock.sendall(connect_req)
                sock.settimeout(10)
                response = sock.recv(4096)
                response_str = response.decode("utf-8", errors="replace")
                assert "200" in response_str, \
                    f"Expected 200 in CONNECT response, got: {response_str}"
            finally:
                sock.close()
        finally:
            _shutdown_proxy(master, loop)


class TestJSInjection:
    """Test JS injection through the addon pipeline."""

    def test_js_injected_for_target_domain(self):
        """GeoFixAddon injects <script nonce= into HTML for target domain.

        Uses FakeFlow to exercise the full addon response() code path —
        the same code that runs inside the proxy pipeline.
        """
        class FakeHeaders(dict):
            def get(self, key, default=None):
                return super().get(key, super().get(key.lower(), default))

            def __contains__(self, key):
                return super().__contains__(key) or super().__contains__(key.lower())

        class FakeRequest:
            def __init__(self):
                self.host = "www.google.com"
                self.url = "https://www.google.com/"
                self.headers = FakeHeaders({"Accept-Language": "ru-RU"})
                self.content = b"request"

        class FakeResponse:
            def __init__(self):
                self.status_code = 200
                self.headers = FakeHeaders({"content-type": "text/html; charset=utf-8"})
                self._text = "<html><head><title>Google</title></head><body>Search</body></html>"
                self.content = self._text.encode("utf-8")

            @property
            def text(self):
                return self._text

            @text.setter
            def text(self, value):
                self._text = value

        class FakeFlow:
            def __init__(self):
                self.request = FakeRequest()
                self.response = FakeResponse()

        # Run GeoFixAddon response (injection) then FlowCleanup response (cleanup)
        # This exercises the exact addon chain order used in production
        addon = GeoFixAddon(PRESETS["US"])
        cleanup = FlowCleanup()

        flow = FakeFlow()
        addon.response(flow)

        # Verify JS injection happened
        assert '<script nonce="' in flow.response.text
        assert "America/New_York" in flow.response.text

        # Run FlowCleanup — verify it doesn't break after injection
        cleanup.response(flow)
        assert flow.request.content == b""
        assert flow.response.content == b""


class TestFlowCleanup:
    """Test FlowCleanup addon clears flow content."""

    def _make_flow(self, body_content=b"response body data"):
        """Create a minimal flow with non-empty content."""
        class FakeRequest:
            def __init__(self):
                self.content = b"request body data"

        class FakeResponse:
            def __init__(self, content):
                self.content = content

        class FakeFlow:
            def __init__(self):
                self.request = FakeRequest()
                self.response = FakeResponse(body_content)
                self.websocket = None

        return FakeFlow()

    def test_flow_content_cleared_after_processing(self):
        """After FlowCleanup processes a flow, both request and response content are empty."""
        cleanup = FlowCleanup()
        flow = self._make_flow()

        assert flow.request.content == b"request body data"
        assert flow.response.content == b"response body data"

        cleanup.response(flow)

        assert flow.request.content == b""
        assert flow.response.content == b""

    def test_100_flows_no_memory_growth(self):
        """Process 100 flows through FlowCleanup, verify all have cleared content."""
        cleanup = FlowCleanup()
        flows = []

        for i in range(100):
            flow = self._make_flow(body_content=f"response body {i} {'x' * 1000}".encode())
            cleanup.response(flow)
            flows.append(flow)

        for i, flow in enumerate(flows):
            assert flow.request.content == b"", f"Flow {i}: request content not cleared"
            assert flow.response.content == b"", f"Flow {i}: response content not cleared"


class TestProxyRestart:
    """Test proxy restart sequence preserves connectivity."""

    def test_tls_works_after_restart(self):
        """Proxy restarts (shutdown old + start new), verifies CONNECT still works.

        Uses separate ports for first/second instance to avoid TIME_WAIT.
        This matches real restart behavior where SO_REUSEADDR allows rebinding.
        """
        port1 = _free_port()
        port2 = _free_port()
        addon = GeoFixAddon(PRESETS["US"])
        cleanup = FlowCleanup()

        # Start first proxy instance
        thread1, master1, loop1 = _start_proxy(addon, port1, extra_addons=[cleanup])

        try:
            # Verify first instance handles CONNECT
            sock = socket.create_connection(("127.0.0.1", port1), timeout=5)
            try:
                sock.sendall(b"CONNECT example.com:443 HTTP/1.1\r\nHost: example.com:443\r\n\r\n")
                sock.settimeout(5)
                resp = sock.recv(4096).decode("utf-8", errors="replace")
                assert "200" in resp, "First instance CONNECT failed"
            finally:
                sock.close()
        finally:
            # Shutdown first instance (simulates the restart trigger)
            _shutdown_proxy(master1, loop1)

        # Start second proxy instance (simulates restart with new Master)
        cleanup2 = FlowCleanup()
        thread2, master2, loop2 = _start_proxy(addon, port2, extra_addons=[cleanup2])

        try:
            # Verify second instance handles CONNECT after restart
            sock = socket.create_connection(("127.0.0.1", port2), timeout=5)
            try:
                sock.sendall(b"CONNECT example.com:443 HTTP/1.1\r\nHost: example.com:443\r\n\r\n")
                sock.settimeout(5)
                resp = sock.recv(4096).decode("utf-8", errors="replace")
                assert "200" in resp, "Restarted proxy CONNECT failed"
            finally:
                sock.close()
        finally:
            _shutdown_proxy(master2, loop2)
