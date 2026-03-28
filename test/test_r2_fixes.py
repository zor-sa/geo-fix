"""Tests for security-hardening-r2 fixes (R-4, R-5, R-7, W-1)."""

import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from src.system_config import cleanup, ProxyState, _list_firewall_rules_by_prefix


class TestCleanupReturnsFailures:
    """R-4: cleanup() returns list of failures."""

    @patch("src.system_config.delete_state")
    @patch("src.system_config.remove_firewall_rules")
    @patch("src.system_config.unset_firefox_proxy")
    @patch("src.system_config.unset_wininet_proxy")
    @patch("src.system_config.delete_session_tmpdir")
    @patch("src.system_config.uninstall_ca_cert")
    def test_returns_empty_on_success(self, *mocks):
        state = ProxyState(pid=1, preset_code="US", timestamp="now")
        result = cleanup(state)
        assert result == []

    @patch("src.system_config.delete_state")
    @patch("src.system_config.remove_firewall_rules")
    @patch("src.system_config.unset_firefox_proxy")
    @patch("src.system_config.unset_wininet_proxy")
    @patch("src.system_config.delete_session_tmpdir")
    @patch("src.system_config.uninstall_ca_cert", side_effect=OSError("certutil failed"))
    def test_returns_failures_on_cert_error(self, *mocks):
        state = ProxyState(pid=1, preset_code="US", timestamp="now")
        result = cleanup(state)
        assert len(result) == 1
        assert "CA cert" in result[0]

    @patch("src.system_config.delete_state")
    @patch("src.system_config.remove_firewall_rules", side_effect=OSError("netsh error"))
    @patch("src.system_config.unset_firefox_proxy")
    @patch("src.system_config.unset_wininet_proxy", side_effect=OSError("registry error"))
    @patch("src.system_config.delete_session_tmpdir")
    @patch("src.system_config.uninstall_ca_cert")
    def test_returns_multiple_failures(self, *mocks):
        state = ProxyState(pid=1, preset_code="US", timestamp="now")
        result = cleanup(state)
        assert len(result) == 2

    @patch("src.system_config.delete_state")
    @patch("src.system_config.remove_firewall_rules")
    @patch("src.system_config.unset_firefox_proxy")
    @patch("src.system_config.unset_wininet_proxy")
    @patch("src.system_config.delete_session_tmpdir")
    @patch("src.system_config.uninstall_ca_cert")
    def test_state_file_always_deleted_even_on_failures(self, mock_cert, mock_tmpdir,
                                                         mock_proxy, mock_ff, mock_fw, mock_del):
        mock_cert.side_effect = OSError("fail")
        state = ProxyState(pid=1, preset_code="US", timestamp="now")
        cleanup(state)
        mock_del.assert_called_once()


class TestFirewallByPrefix:
    """R-7: list and remove rules by prefix."""

    def test_parse_netsh_output(self):
        fake_output = MagicMock()
        fake_output.stdout = """
Rule Name:                            geo-fix-webrtc-chrome-udp-3478
Enabled:                              Yes
Direction:                            Out

Rule Name:                            geo-fix-webrtc-msedge-udp-5349
Enabled:                              Yes

Rule Name:                            SomeOtherRule
Enabled:                              Yes
"""
        fake_output.returncode = 0
        with patch("src.system_config.subprocess.run", return_value=fake_output):
            rules = _list_firewall_rules_by_prefix("geo-fix-webrtc")
        assert len(rules) == 2
        assert "geo-fix-webrtc-chrome-udp-3478" in rules
        assert "geo-fix-webrtc-msedge-udp-5349" in rules
        assert "SomeOtherRule" not in rules

    def test_returns_empty_on_subprocess_error(self):
        with patch("src.system_config.subprocess.run", side_effect=OSError("fail")):
            rules = _list_firewall_rules_by_prefix("geo-fix-webrtc")
        assert rules == []


class TestVpnMonitor:
    """R-5: VPN monitoring basics."""

    def test_check_vpn_status_returns_enum(self):
        from src.health_check import check_vpn_status, VpnStatus
        result = check_vpn_status()
        assert isinstance(result, VpnStatus)
