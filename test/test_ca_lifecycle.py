"""Tests for per-session CA lifecycle (Task 1: security-hardening)."""

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch, call

import pytest

from src.system_config import (
    ProxyState,
    create_session_tmpdir,
    delete_session_tmpdir,
    install_ca_cert,
    uninstall_ca_cert,
)


class TestCreateSessionTmpdir:
    def test_creates_directory(self):
        tmpdir = create_session_tmpdir()
        try:
            assert tmpdir is not None
            assert Path(tmpdir).is_dir()
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_directory_has_prefix(self):
        tmpdir = create_session_tmpdir()
        try:
            assert "geo-fix-" in Path(tmpdir).name
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    @pytest.mark.skipif(sys.platform != "win32", reason="Windows-only ACL test")
    def test_acl_restricted_to_current_user(self):
        tmpdir = create_session_tmpdir()
        try:
            result = subprocess.run(
                ["icacls", tmpdir], capture_output=True, text=True
            )
            username = os.environ.get("USERNAME", "")
            assert username in result.stdout
            # Should not have BUILTIN\Users or Everyone
            assert "Everyone" not in result.stdout
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)


class TestInstallCaCert:
    def test_returns_thumbprint_on_success(self, tmp_path):
        """Mock certutil to return fake output; verify thumbprint is parsed."""
        # Create a fake cert file
        cert_file = tmp_path / "mitmproxy-ca-cert.pem"
        cert_file.write_text("fake cert")

        fake_add_result = MagicMock(returncode=0, stdout="CertUtil: -addstore OK", stderr="")
        fake_dump_result = MagicMock(
            returncode=0,
            stdout="Cert Hash(sha1): ab cd ef 01 23 45 67 89 ab cd ef 01 23 45 67 89 ab cd ef 01\nSubject: mitmproxy",
            stderr="",
        )

        with patch("src.system_config.subprocess.run") as mock_run:
            mock_run.side_effect = [fake_add_result, fake_dump_result]
            thumbprint = install_ca_cert(str(tmp_path))

        assert thumbprint == "abcdef0123456789abcdef0123456789abcdef01"

    def test_returns_none_when_cert_missing(self, tmp_path):
        """No cert file -> returns None."""
        result = install_ca_cert(str(tmp_path))
        assert result is None

    def test_returns_none_on_certutil_failure(self, tmp_path):
        cert_file = tmp_path / "mitmproxy-ca-cert.pem"
        cert_file.write_text("fake cert")

        fake_result = MagicMock(returncode=1, stdout="", stderr="error")
        with patch("src.system_config.subprocess.run", return_value=fake_result):
            result = install_ca_cert(str(tmp_path))
        assert result is None


class TestUninstallCaCert:
    def test_uses_thumbprint_when_provided(self):
        thumbprint = "abcdef0123456789abcdef0123456789abcdef01"
        with patch("src.system_config.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            uninstall_ca_cert(thumbprint=thumbprint)

        mock_run.assert_called_once()
        cmd = mock_run.call_args[0][0]
        assert cmd == ["certutil", "-delstore", "-user", "Root", thumbprint]

    def test_falls_back_to_name_when_no_thumbprint(self):
        with patch("src.system_config.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            uninstall_ca_cert(thumbprint=None)

        mock_run.assert_called_once()
        cmd = mock_run.call_args[0][0]
        assert cmd == ["certutil", "-delstore", "-user", "Root", "mitmproxy"]


class TestDeleteSessionTmpdir:
    def test_removes_existing_directory(self):
        tmpdir = tempfile.mkdtemp(prefix="geo-fix-test-")
        assert Path(tmpdir).exists()
        delete_session_tmpdir(tmpdir)
        assert not Path(tmpdir).exists()

    def test_noop_when_none(self):
        # Should not raise
        delete_session_tmpdir(None)

    def test_noop_when_already_gone(self):
        # Should not raise
        delete_session_tmpdir("/nonexistent/path/geo-fix-test")


class TestProxyStateNewFields:
    def test_new_fields_default_none(self):
        state = ProxyState(pid=1, preset_code="US", timestamp="now")
        assert state.session_tmpdir is None
        assert state.ca_thumbprint is None
        assert state.session_id is None

    def test_round_trip_with_session_fields(self):
        state = ProxyState(
            pid=1,
            preset_code="US",
            timestamp="now",
            session_id="abc-123",
            session_tmpdir="/tmp/geo-fix-abc",
            ca_thumbprint="deadbeef",
        )
        json_str = state.to_json()
        restored = ProxyState.from_json(json_str)
        assert restored.session_id == "abc-123"
        assert restored.session_tmpdir == "/tmp/geo-fix-abc"
        assert restored.ca_thumbprint == "deadbeef"

    def test_json_includes_new_fields(self):
        state = ProxyState(
            pid=1, preset_code="US", timestamp="now",
            session_id="test-id", ca_thumbprint="thumb123",
        )
        data = json.loads(state.to_json())
        assert "session_id" in data
        assert "ca_thumbprint" in data
        assert "session_tmpdir" in data
