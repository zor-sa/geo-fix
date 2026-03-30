"""Integration test: mitmproxy works after CA key files are deleted (Task 1).

Verifies that once mitmproxy loads the CA key into memory, deleting the key
files from disk does not break proxy functionality.
"""

import asyncio
import socket
import threading
import time

import pytest

from src.presets import PRESETS
from src.proxy_addon import GeoFixAddon
from src.system_config import delete_ca_key_files
from test.conftest import get_free_port


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


def _start_proxy_with_confdir(confdir: str, port: int):
    """Start a minimal mitmproxy Master with a real confdir containing CA keys.

    Returns (thread, master, loop).
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
    startup_error = []

    def run():
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            loop_ref["loop"] = loop

            opts = Options(
                listen_host="127.0.0.1",
                listen_port=port,
                confdir=confdir,
            )
            master = Master(opts, event_loop=loop)
            addon = GeoFixAddon(PRESETS["US"])
            master.addons.add(
                Core(), Proxyserver(), NextLayer(), TlsConfig(),
                KeepServing(), addon,
            )
            master_ref["master"] = master
            started.set()
        except Exception as e:
            startup_error.append(str(e))
            started.set()
            return

        try:
            loop.run_until_complete(master.run())
        except (Exception, SystemExit):
            pass

    thread = threading.Thread(target=run, daemon=True, name="test-mitmproxy-ca")
    thread.start()
    started.wait(timeout=10)

    if startup_error:
        pytest.fail(f"Proxy startup error: {startup_error[0]}")

    if not _wait_for_port("127.0.0.1", port, timeout=15):
        pytest.fail(f"Proxy port {port} never opened")

    return thread, master_ref.get("master"), loop_ref.get("loop")


def _shutdown_proxy(master, loop, thread=None):
    """Shut down the proxy master, releasing the listening port."""
    if master:
        try:
            master.shutdown()
        except Exception as e:
            print(f"shutdown error: {e}")
    if thread:
        thread.join(timeout=10)


class TestMitmproxyAfterKeyDeletion:
    """Integration: proxy remains functional after CA key files deleted from disk."""

    def test_mitmproxy_works_after_key_deletion(self, tmp_path):
        """Start real mitmproxy with confdir, delete key files, verify proxy still works.

        mitmproxy loads the CA private key into CertStore.default_privatekey at
        startup and never re-reads from disk. Deleting the key files while the
        proxy is running must not affect its ability to accept connections.
        """
        port = get_free_port()
        confdir = str(tmp_path)

        # Start proxy — mitmproxy generates CA files in confdir at startup
        thread, master, loop = _start_proxy_with_confdir(confdir, port)

        try:
            # Verify CA key files were created by mitmproxy
            from pathlib import Path
            ca_key = Path(confdir) / "mitmproxy-ca.pem"
            assert ca_key.exists(), "mitmproxy should have created CA key"

            # Delete key files while proxy is running
            delete_ca_key_files(confdir)
            assert not ca_key.exists(), "CA key should be deleted"

            # Proxy must still accept CONNECT (TLS) connections — key is in memory
            sock = socket.create_connection(("127.0.0.1", port), timeout=10)
            try:
                sock.sendall(
                    b"CONNECT example.com:443 HTTP/1.1\r\n"
                    b"Host: example.com:443\r\n\r\n"
                )
                sock.settimeout(10)
                response = sock.recv(4096).decode("utf-8", errors="replace")
                assert "200" in response, (
                    f"Proxy should accept CONNECT after key deletion, got: {response}"
                )
            finally:
                sock.close()
        finally:
            _shutdown_proxy(master, loop, thread)
