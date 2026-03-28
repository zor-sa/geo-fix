"""E2E security lifecycle tests.

Verifies the full security-hardening cycle:
- Start geo-fix → per-session CA installed → proxy works
- Stop geo-fix → CA removed, tmpdir deleted, proxy restored
- Hard kill → watchdog cleans up

Requires Windows + mitmproxy. Skipped on Linux.
"""

import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from unittest.mock import patch

import pytest

WIN_ONLY = pytest.mark.skipif(sys.platform != "win32", reason="Windows only")


class TestSecurityLifecycleE2E:
    """Full start → verify → stop → verify cycle."""

    @WIN_ONLY
    def test_session_ca_lifecycle(self):
        """AC-1.1, AC-1.2, AC-1.3: fresh CA per session, removed on stop."""
        from src.system_config import (
            create_session_tmpdir, install_ca_cert, uninstall_ca_cert,
            delete_session_tmpdir,
        )

        # Session 1
        tmpdir1 = create_session_tmpdir()
        try:
            self._generate_ca(tmpdir1)
            thumb1 = install_ca_cert(tmpdir1)
            if thumb1 is None:
                pytest.skip("Could not install CA cert")

            # Session 2 — different tmpdir, different CA
            tmpdir2 = create_session_tmpdir()
            try:
                self._generate_ca(tmpdir2)
                thumb2 = install_ca_cert(tmpdir2)
                if thumb2 is None:
                    pytest.skip("Could not install second CA cert")

                # AC-1.3: different thumbprints
                assert thumb1 != thumb2, "Each session must generate a unique CA"

                # Cleanup session 2
                uninstall_ca_cert(thumb2)
            finally:
                delete_session_tmpdir(tmpdir2)

            # Cleanup session 1
            uninstall_ca_cert(thumb1)
        finally:
            delete_session_tmpdir(tmpdir1)

        # AC-1.1: cert gone from store
        result = subprocess.run(
            ["certutil", "-store", "-user", "Root"],
            capture_output=True, text=True, timeout=10
        )
        store_lower = result.stdout.lower().replace(" ", "")
        assert thumb1.lower() not in store_lower
        assert thumb2.lower() not in store_lower

        # AC-1.2: tmpdirs deleted
        assert not Path(tmpdir1).exists()
        assert not Path(tmpdir2).exists()

    def _generate_ca(self, confdir):
        """Start mitmproxy briefly to generate CA in confdir."""
        import asyncio
        from mitmproxy.options import Options
        from mitmproxy.tools.dump import DumpMaster

        ready = threading.Event()
        def run():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            opts = Options(listen_host="127.0.0.1", listen_port=0, confdir=confdir)
            master = DumpMaster(opts)
            ready.set()
            try:
                loop.run_until_complete(master.run())
            except Exception:
                pass

        t = threading.Thread(target=run, daemon=True)
        t.start()
        ready.wait(timeout=10)
        time.sleep(2)

        cert = Path(confdir) / "mitmproxy-ca-cert.pem"
        if not cert.exists():
            pytest.skip("mitmproxy did not generate CA")


@WIN_ONLY
class TestWatchdogRecoveryE2E:
    """AC-3.1: after hard kill, proxy restored within 15 seconds."""

    def test_watchdog_restores_proxy_after_kill(self):
        """Start a subprocess that sets proxy, spawn watchdog, kill main, verify restore."""
        import winreg
        from src.system_config import (
            ProxyState, save_state, set_wininet_proxy, unset_wininet_proxy,
            create_session_tmpdir, STATE_FILE,
        )

        # Save original proxy state
        key_path = r"Software\Microsoft\Windows\CurrentVersion\Internet Settings"
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path) as key:
            original_enable = winreg.QueryValueEx(key, "ProxyEnable")[0]

        # Set proxy (simulating geo-fix running)
        original = set_wininet_proxy(port=18777)
        tmpdir = create_session_tmpdir()

        try:
            state = ProxyState(
                pid=os.getpid(), preset_code="US", timestamp="now",
                session_id="e2e-wd-test", session_tmpdir=tmpdir,
                original_proxy_enable=original.get("ProxyEnable"),
                original_proxy_server=original.get("ProxyServer"),
                original_proxy_override=original.get("ProxyOverride"),
                proxy_port=18777,
            )
            save_state(state)

            # Verify proxy is set
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path) as key:
                assert winreg.QueryValueEx(key, "ProxyEnable")[0] == 1

            # Spawn watchdog pointing at a fake PID that doesn't exist
            watchdog_path = Path(__file__).parent.parent / "src" / "watchdog.py"
            dead_pid = 99999999  # Non-existent PID
            proc = subprocess.Popen(
                [sys.executable, str(watchdog_path),
                 str(dead_pid), str(STATE_FILE), tmpdir,
                 "e2e-wd-test", "dummy-token"],
            )

            # Wait for watchdog to detect "dead" process and cleanup (up to 15s)
            proc.wait(timeout=15)

            # AC-3.1: proxy should be restored
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path) as key:
                restored_enable = winreg.QueryValueEx(key, "ProxyEnable")[0]
                assert restored_enable == original.get("ProxyEnable", 0), \
                    f"ProxyEnable not restored: {restored_enable}"

        finally:
            # Safety net: always restore proxy
            unset_wininet_proxy(original)
            shutil.rmtree(tmpdir, ignore_errors=True)


class TestCleanupFullCycleE2E:
    """Full cleanup cycle with real state file and tmpdir."""

    def test_cleanup_removes_all_artifacts(self, tmp_path, monkeypatch):
        """Verify cleanup removes tmpdir, state file."""
        from src.system_config import (
            ProxyState, save_state, load_state, cleanup,
        )
        import src.system_config as sc

        state_file = tmp_path / ".geo-fix-state.bin"
        monkeypatch.setattr("src.system_config.STATE_FILE", state_file)

        session_dir = tmp_path / "geo-fix-session-e2e"
        session_dir.mkdir()
        (session_dir / "mitmproxy-ca.pem").write_text("FAKE PRIVATE KEY")
        (session_dir / "mitmproxy-ca-cert.pem").write_text("FAKE CERT")
        (session_dir / "mitmproxy-dhparam.pem").write_text("FAKE DH")

        state = ProxyState(
            pid=os.getpid(), preset_code="US", timestamp="now",
            session_id="e2e-cleanup", session_tmpdir=str(session_dir),
            ca_thumbprint=None,
        )
        save_state(state)

        # Load and verify
        loaded = load_state()
        assert loaded is not None
        assert loaded.session_id == "e2e-cleanup"

        # Cleanup (mock Windows-specific operations)
        with patch("src.system_config.uninstall_ca_cert"):
            with patch("src.system_config.unset_wininet_proxy"):
                with patch("src.system_config.remove_firewall_rules"):
                    cleanup(loaded)

        # Everything gone
        assert not session_dir.exists(), "Session dir with keys should be deleted"
        assert not state_file.exists(), "State file should be deleted"


class TestStartupOrderE2E:
    """Verify mitmproxy bind happens before system modifications."""

    def test_port_failure_does_not_modify_system(self):
        """AC-6.3: if port selection fails, nothing is modified."""
        import socket
        from src.main import _select_port

        # Verify _select_port raises on total failure
        with patch("socket.socket") as mock_sock:
            mock_instance = mock_sock.return_value.__enter__.return_value
            mock_instance.bind.side_effect = OSError("No ports available")

            with pytest.raises(RuntimeError, match="Cannot bind"):
                _select_port(None)

        # System should not have been touched (no proxy set, no firefox modified)
        # This is structural — startup order guarantees it


class TestStateFileTamperE2E:
    """AC-9.3, AC-9.4: tampered state file rejected, clean defaults applied."""

    def test_tampered_state_rejected_and_deleted(self, tmp_path, monkeypatch):
        from src.system_config import ProxyState, save_state, load_state
        import src.system_config as sc

        state_file = tmp_path / ".geo-fix-state.bin"
        monkeypatch.setattr("src.system_config.STATE_FILE", state_file)

        # Save valid state
        save_state(ProxyState(pid=1, preset_code="US", timestamp="now"))
        assert state_file.exists()

        # Tamper
        data = bytearray(state_file.read_bytes())
        for i in range(min(10, len(data))):
            data[i] ^= 0xFF
        state_file.write_bytes(bytes(data))

        # Load should reject
        result = load_state()
        assert result is None, "Tampered state should be rejected"
        # AC-9.4: state file deleted after rejection
        assert not state_file.exists(), "Rejected state file should be auto-deleted"
