"""Tests for cleanup path completeness (Task 2: security-hardening)."""

import json
from unittest.mock import MagicMock, patch, call

import pytest

from src.system_config import ProxyState, cleanup


def _make_state(**overrides):
    defaults = dict(
        pid=1234, preset_code="US", timestamp="2026-03-27",
        session_id="test-session", session_tmpdir="/tmp/geo-fix-test",
        ca_thumbprint="abcdef0123456789",
    )
    defaults.update(overrides)
    return ProxyState(**defaults)


class TestCleanupCallsCertRemoval:
    @patch("src.system_config.delete_state")
    @patch("src.system_config.unset_wininet_proxy")
    @patch("src.system_config.delete_session_tmpdir")
    @patch("src.system_config.uninstall_ca_cert")
    def test_cleanup_calls_uninstall_cert(self, mock_cert, mock_tmpdir, mock_proxy, mock_state):
        state = _make_state(ca_thumbprint="thumb123")
        cleanup(state)
        mock_cert.assert_called_once_with("thumb123")

    @patch("src.system_config.delete_state")
    @patch("src.system_config.unset_wininet_proxy")
    @patch("src.system_config.delete_session_tmpdir")
    @patch("src.system_config.uninstall_ca_cert")
    def test_cleanup_calls_delete_tmpdir(self, mock_cert, mock_tmpdir, mock_proxy, mock_state):
        state = _make_state(session_tmpdir="/tmp/geo-fix-session")
        cleanup(state)
        mock_tmpdir.assert_called_once_with("/tmp/geo-fix-session")

    @patch("src.system_config.delete_state")
    @patch("src.system_config.unset_wininet_proxy")
    @patch("src.system_config.delete_session_tmpdir")
    @patch("src.system_config.uninstall_ca_cert")
    def test_cert_removal_before_state_deletion(self, mock_cert, mock_tmpdir, mock_proxy, mock_state):
        """AC-2.1: cert removal must happen before state file deletion."""
        call_order = []
        mock_cert.side_effect = lambda *a: call_order.append("uninstall_ca_cert")
        mock_tmpdir.side_effect = lambda *a: call_order.append("delete_session_tmpdir")
        mock_state.side_effect = lambda: call_order.append("delete_state")

        cleanup(_make_state())

        cert_idx = call_order.index("uninstall_ca_cert")
        tmpdir_idx = call_order.index("delete_session_tmpdir")
        state_idx = call_order.index("delete_state")
        assert cert_idx < state_idx, "uninstall_ca_cert must be called before delete_state"
        assert tmpdir_idx < state_idx, "delete_session_tmpdir must be called before delete_state"

    @patch("src.system_config.delete_state")
    @patch("src.system_config.unset_wininet_proxy")
    @patch("src.system_config.delete_session_tmpdir")
    @patch("src.system_config.uninstall_ca_cert")
    def test_cleanup_handles_none_thumbprint(self, mock_cert, mock_tmpdir, mock_proxy, mock_state):
        state = _make_state(ca_thumbprint=None, session_tmpdir=None)
        cleanup(state)
        mock_cert.assert_called_once_with(None)
        mock_tmpdir.assert_called_once_with(None)

    @patch("src.system_config.stateless_cleanup")
    def test_cleanup_with_no_state_calls_stateless(self, mock_stateless):
        with patch("src.system_config.load_state", return_value=None):
            cleanup(None)
        mock_stateless.assert_called_once()


class TestCleanupWithRealTmpdir:
    @patch("src.system_config.delete_state")
    @patch("src.system_config.unset_wininet_proxy")
    @patch("src.system_config.uninstall_ca_cert")
    def test_cleanup_deletes_real_tmpdir(self, mock_cert, mock_proxy, mock_state, tmp_path):
        session_dir = tmp_path / "geo-fix-session"
        session_dir.mkdir()
        (session_dir / "mitmproxy-ca.pem").write_text("fake key")

        state = _make_state(session_tmpdir=str(session_dir))
        cleanup(state)
        assert not session_dir.exists()
