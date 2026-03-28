"""Integration tests for security-hardening features.

Tests real system operations on Windows (cert store, registry, tmpdir, watchdog).
Cross-platform tests use real filesystem but mock Windows-only APIs.
"""

import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

import pytest

WIN_ONLY = pytest.mark.skipif(sys.platform != "win32", reason="Windows only")


# === Per-session CA lifecycle (T-1) ===

class TestSessionCaLifecycle:
    """Real tmpdir creation and cleanup."""

    def test_create_session_tmpdir_real_directory(self):
        from src.system_config import create_session_tmpdir
        tmpdir = create_session_tmpdir()
        try:
            assert Path(tmpdir).is_dir()
            assert "geo-fix-" in Path(tmpdir).name
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_delete_session_tmpdir_removes_with_contents(self):
        from src.system_config import create_session_tmpdir, delete_session_tmpdir
        tmpdir = create_session_tmpdir()
        # Add fake CA key
        (Path(tmpdir) / "mitmproxy-ca.pem").write_text("FAKE PRIVATE KEY")
        (Path(tmpdir) / "mitmproxy-ca-cert.pem").write_text("FAKE CERT")

        delete_session_tmpdir(tmpdir)
        assert not Path(tmpdir).exists()

    @WIN_ONLY
    def test_session_tmpdir_acl_restricted(self):
        """AC-1.4: tmpdir ACL restricted to current user."""
        from src.system_config import create_session_tmpdir
        tmpdir = create_session_tmpdir()
        try:
            result = subprocess.run(
                ["icacls", tmpdir], capture_output=True, text=True, timeout=10
            )
            username = os.environ.get("USERNAME", "")
            assert username in result.stdout
            # Should not have broad access groups
            for bad_group in ["Everyone", "BUILTIN\\Users", "Authenticated Users"]:
                assert bad_group not in result.stdout, f"Unexpected ACL: {bad_group}"
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    @WIN_ONLY
    def test_install_uninstall_ca_cert_real(self):
        """AC-1.1, AC-1.3: install with confdir, get thumbprint, uninstall by thumbprint."""
        from src.system_config import create_session_tmpdir, install_ca_cert, uninstall_ca_cert

        tmpdir = create_session_tmpdir()
        try:
            # Need mitmproxy to generate CA in this confdir
            import asyncio
            from mitmproxy.options import Options
            from mitmproxy.tools.dump import DumpMaster
            import threading

            ready = threading.Event()
            def run_proxy():
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                opts = Options(listen_host="127.0.0.1", listen_port=18099, confdir=tmpdir)
                master = DumpMaster(opts)
                ready.set()
                try:
                    loop.run_until_complete(master.run())
                except Exception:
                    pass

            t = threading.Thread(target=run_proxy, daemon=True)
            t.start()
            ready.wait(timeout=10)
            time.sleep(2)  # Let mitmproxy generate CA

            cert_file = Path(tmpdir) / "mitmproxy-ca-cert.pem"
            if not cert_file.exists():
                pytest.skip("mitmproxy did not generate CA cert")

            # Install
            thumbprint = install_ca_cert(tmpdir)
            assert thumbprint is not None, "install_ca_cert should return thumbprint"
            assert len(thumbprint) == 40, f"Thumbprint should be 40 hex chars: {thumbprint}"

            # Verify cert is in store
            verify = subprocess.run(
                ["certutil", "-store", "-user", "Root"],
                capture_output=True, text=True, timeout=10
            )
            assert thumbprint.lower() in verify.stdout.lower().replace(" ", ""), \
                "Cert thumbprint not found in store after install"

            # Uninstall by thumbprint
            uninstall_ca_cert(thumbprint=thumbprint)

            # Verify cert is gone
            verify2 = subprocess.run(
                ["certutil", "-store", "-user", "Root"],
                capture_output=True, text=True, timeout=10
            )
            assert thumbprint.lower() not in verify2.stdout.lower().replace(" ", ""), \
                "Cert still in store after uninstall"
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)


# === DPAPI State File (T-9) ===

class TestDpapiStateReal:
    """Real DPAPI encrypt/decrypt on Windows, real file ops everywhere."""

    def test_state_file_not_readable_as_json(self, tmp_path, monkeypatch):
        """AC-9.1: state file is not plaintext JSON."""
        from src.system_config import ProxyState, save_state
        import src.system_config as sc

        state_file = tmp_path / ".geo-fix-state.bin"
        monkeypatch.setattr("src.system_config.STATE_FILE", state_file)

        save_state(ProxyState(pid=1, preset_code="US", timestamp="now"))

        raw = state_file.read_bytes()
        if sys.platform == "win32":
            # On Windows, DPAPI encrypts — should NOT be valid JSON
            try:
                json.loads(raw)
                pytest.fail("State file is readable as JSON — not encrypted")
            except (json.JSONDecodeError, UnicodeDecodeError):
                pass  # Expected
        # On non-Windows, passthrough is acceptable (logged warning)

    @WIN_ONLY
    def test_dpapi_roundtrip_real(self):
        """AC-9.5: user-scope DPAPI encryption works."""
        from src.system_config import _dpapi_encrypt, _dpapi_decrypt

        plaintext = b"sensitive state data"
        encrypted = _dpapi_encrypt(plaintext)
        assert encrypted != plaintext, "DPAPI should encrypt on Windows"

        decrypted = _dpapi_decrypt(encrypted)
        assert decrypted == plaintext

    @WIN_ONLY
    def test_dpapi_tampered_rejected_real(self):
        """AC-9.3: tampered ciphertext rejected."""
        from src.system_config import _dpapi_encrypt, _dpapi_decrypt

        encrypted = _dpapi_encrypt(b"test data")
        tampered = bytearray(encrypted)
        tampered[len(tampered) // 2] ^= 0xFF

        with pytest.raises(OSError, match="CryptUnprotectData"):
            _dpapi_decrypt(bytes(tampered))


# === Port selection (T-6) ===

class TestPortSelectionReal:
    """Real socket binding tests."""

    def test_auto_select_avoids_occupied_port(self):
        """AC-6.1: auto-select when port occupied."""
        import socket
        from src.main import _select_port

        # Hold a port
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind(("127.0.0.1", 0))
            held_port = s.getsockname()[1]

            selected = _select_port(held_port)
            assert selected != held_port
            assert selected > 0

    @WIN_ONLY
    def test_registry_uses_selected_port(self, tmp_path, monkeypatch):
        """AC-6.2: registry gets the actual port."""
        import winreg
        from src.system_config import set_wininet_proxy, unset_wininet_proxy

        original = set_wininet_proxy(port=19999)
        try:
            key_path = r"Software\Microsoft\Windows\CurrentVersion\Internet Settings"
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path) as key:
                server = winreg.QueryValueEx(key, "ProxyServer")[0]
                assert "19999" in server, f"Port 19999 not in registry: {server}"
        finally:
            unset_wininet_proxy(original)


# === Watchdog (T-3) ===

class TestWatchdogReal:
    """Real process monitoring tests."""

    def test_watchdog_detects_dead_process(self):
        """Verify _is_process_alive returns False for dead PID."""
        from src.watchdog import _is_process_alive
        assert _is_process_alive(os.getpid()) is True
        assert _is_process_alive(99999999) is False

    def test_watchdog_stop_flag_real_filesystem(self, tmp_path):
        """Verify stop flag file mechanism works with real files."""
        from src.watchdog import _check_stop_flag, STOP_FLAG_NAME

        flag = tmp_path / STOP_FLAG_NAME
        assert _check_stop_flag(str(tmp_path), "token123") is False

        flag.write_text("token123")
        assert _check_stop_flag(str(tmp_path), "token123") is True
        assert _check_stop_flag(str(tmp_path), "wrong-token") is False

    def test_watchdog_subprocess_starts_and_stops(self, tmp_path):
        """AC-3.4: watchdog is a visible separate process."""
        from src.system_config import ProxyState, save_state
        from src.watchdog import STOP_FLAG_NAME
        import src.system_config as sc

        state_file = tmp_path / ".geo-fix-state.bin"
        original_sf = sc.STATE_FILE
        sc.STATE_FILE = state_file
        try:
            state = ProxyState(
                pid=os.getpid(), preset_code="US", timestamp="now",
                session_id="test-wd-123",
            )
            save_state(state)

            watchdog_path = Path(__file__).parent.parent / "src" / "watchdog.py"
            proc = subprocess.Popen(
                [sys.executable, str(watchdog_path),
                 str(os.getpid()), str(state_file), str(tmp_path),
                 "test-wd-123", "stop-token-abc"],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            )

            # Watchdog should be alive
            time.sleep(1)
            assert proc.poll() is None, "Watchdog died prematurely"

            # Signal stop
            (tmp_path / STOP_FLAG_NAME).write_text("stop-token-abc")
            proc.wait(timeout=10)
            assert proc.returncode == 0 or proc.returncode is None
        finally:
            sc.STATE_FILE = original_sf


# === Firefox backup (T-4) ===

class TestFirefoxBackupReal:
    """Real filesystem backup/restore cycle."""

    def test_full_backup_restore_cycle(self, tmp_path):
        """AC-4.1 through AC-4.4: complete cycle with real files."""
        from src.system_config import set_firefox_proxy, unset_firefox_proxy

        profile = tmp_path / "abc.default-release"
        profile.mkdir()
        user_js = profile / "user.js"
        original_content = 'user_pref("browser.startup.homepage", "https://example.com");\n'
        user_js.write_text(original_content)

        with patch("src.system_config._find_firefox_profile", return_value=profile):
            backup_path = set_firefox_proxy(port=8080)

            # AC-4.1: original still exists (copy, not rename)
            assert user_js.exists()
            # Backup exists
            assert backup_path is not None
            assert Path(backup_path).exists()
            # Backup is copy of original
            assert Path(backup_path).read_text() == original_content
            # user.js has proxy prefs
            assert "geo-fix: proxy configuration" in user_js.read_text()

            # Restore
            unset_firefox_proxy(backup_path)

            # AC-4.2: enterprise_roots gone
            restored = user_js.read_text()
            assert "enterprise_roots" not in restored
            # AC-4.4: backup removed
            assert not Path(backup_path).exists()
            # Content matches original
            assert restored == original_content


# === Cleanup ordering (T-2) ===

class TestCleanupOrderingReal:
    """Verify cleanup operates correctly with real state file."""

    def test_cleanup_with_real_state_file(self, tmp_path, monkeypatch):
        """Full save → cleanup cycle."""
        from src.system_config import ProxyState, save_state, load_state, cleanup
        import src.system_config as sc

        state_file = tmp_path / ".geo-fix-state.bin"
        monkeypatch.setattr("src.system_config.STATE_FILE", state_file)

        session_dir = tmp_path / "geo-fix-session"
        session_dir.mkdir()
        (session_dir / "mitmproxy-ca.pem").write_text("fake key")

        state = ProxyState(
            pid=os.getpid(), preset_code="US", timestamp="now",
            session_id="cleanup-test", session_tmpdir=str(session_dir),
            ca_thumbprint=None,  # No real cert to uninstall
        )
        save_state(state)
        assert state_file.exists()

        # Cleanup should remove tmpdir and state file
        with patch("src.system_config.uninstall_ca_cert"):
            with patch("src.system_config.unset_wininet_proxy"):
                with patch("src.system_config.remove_firewall_rules"):
                    cleanup(state)

        assert not session_dir.exists(), "Session tmpdir should be deleted"
        assert not state_file.exists(), "State file should be deleted"
