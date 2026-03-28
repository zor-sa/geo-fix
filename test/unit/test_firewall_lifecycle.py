"""Tests for firewall rule lifecycle (Task 7: security-hardening)."""

from unittest.mock import patch, MagicMock

import pytest

from src.system_config import ProxyState, cleanup


class TestFirewallCleanupUnconditional:
    @patch("src.system_config.delete_state")
    @patch("src.system_config.remove_firewall_rules")
    @patch("src.system_config.unset_wininet_proxy")
    @patch("src.system_config.delete_session_tmpdir")
    @patch("src.system_config.uninstall_ca_cert")
    def test_firewall_cleanup_unconditional(self, mock_cert, mock_tmpdir, mock_proxy, mock_fw, mock_state):
        """AC-8.3: remove_firewall_rules() called without checking any flag."""
        state = ProxyState(pid=1, preset_code="US", timestamp="now")
        cleanup(state)
        mock_fw.assert_called_once()

    def test_proxy_state_no_firewall_flag(self):
        """firewall_rules_created field should not exist in ProxyState."""
        assert not hasattr(ProxyState(pid=1, preset_code="US", timestamp="now"), "firewall_rules_created")
