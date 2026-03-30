"""Tests for system configuration module."""

import json
from unittest.mock import MagicMock, patch

import pytest

from src.system_config import (
    BROWSER_EXES,
    FW_RULE_PREFIX,
    STUN_PORTS,
    ProxyState,
    _list_firewall_rules_by_prefix,
    delete_state,
    load_state,
    remove_firewall_rules,
    save_state,
)


class TestProxyState:
    def test_to_json(self):
        state = ProxyState(pid=1234, preset_code="US", timestamp="2026-03-27T12:00:00")
        data = json.loads(state.to_json())
        assert data["pid"] == 1234
        assert data["preset_code"] == "US"

    def test_from_json_valid(self):
        data = '{"pid": 1234, "preset_code": "US", "timestamp": "2026-03-27"}'
        state = ProxyState.from_json(data)
        assert state.pid == 1234
        assert state.preset_code == "US"

    def test_from_json_rejects_unknown_fields(self):
        data = '{"pid": 1234, "preset_code": "US", "timestamp": "2026-03-27", "evil_field": "hack"}'
        with pytest.raises(ValueError, match="Unknown fields"):
            ProxyState.from_json(data)

    def test_from_json_invalid_json(self):
        with pytest.raises(json.JSONDecodeError):
            ProxyState.from_json("not json")

    def test_defaults(self):
        state = ProxyState(pid=1, preset_code="DE", timestamp="now")
        assert state.original_proxy_enable is None
        assert state.firefox_prefs_modified is False


class TestStateFile:
    def test_save_and_load(self, tmp_path, monkeypatch):
        state_file = tmp_path / "state.json"
        monkeypatch.setattr("src.system_config.STATE_FILE", state_file)

        state = ProxyState(pid=999, preset_code="GB", timestamp="2026-03-27")
        save_state(state)

        loaded = load_state()
        assert loaded is not None
        assert loaded.pid == 999
        assert loaded.preset_code == "GB"

    def test_load_missing_file(self, tmp_path, monkeypatch):
        monkeypatch.setattr("src.system_config.STATE_FILE", tmp_path / "nonexistent.json")
        assert load_state() is None

    def test_load_corrupt_file(self, tmp_path, monkeypatch):
        state_file = tmp_path / "state.bin"
        state_file.write_bytes(b"corrupt data")
        monkeypatch.setattr("src.system_config.STATE_FILE", state_file)
        assert load_state() is None

    def test_delete_state(self, tmp_path, monkeypatch):
        state_file = tmp_path / "state.bin"
        state_file.write_bytes(b"data")
        monkeypatch.setattr("src.system_config.STATE_FILE", state_file)
        delete_state()
        assert not state_file.exists()

    def test_atomic_write(self, tmp_path, monkeypatch):
        state_file = tmp_path / "state.json"
        monkeypatch.setattr("src.system_config.STATE_FILE", state_file)

        state = ProxyState(pid=1, preset_code="US", timestamp="now")
        save_state(state)

        # Temp file should not exist after atomic rename
        assert not (state_file.with_suffix(".tmp")).exists()
        assert state_file.exists()


# === Firewall prefix cleanup tests ===

NETSH_OUTPUT = """\
Rule Name:                            geo-fix-webrtc-chrome-udp-3478
----------------------------------------------------------------------
Enabled:                              Yes
Direction:                            Out

Rule Name:                            geo-fix-webrtc-msedge-udp-5349
----------------------------------------------------------------------
Enabled:                              Yes
Direction:                            Out

Rule Name:                            unrelated-other-rule
----------------------------------------------------------------------
Enabled:                              Yes
Direction:                            Out
"""


class TestFirewallPrefixCleanup:
    def test_list_rules_parses_netsh_output(self):
        """_list_firewall_rules_by_prefix parses netsh output and filters by prefix."""
        mock_result = MagicMock()
        mock_result.stdout = NETSH_OUTPUT

        with patch("src.system_config.subprocess.run", return_value=mock_result) as mock_run:
            rules = _list_firewall_rules_by_prefix("geo-fix-webrtc")

        mock_run.assert_called_once_with(
            ["netsh", "advfirewall", "firewall", "show", "rule", "name=all"],
            capture_output=True, text=True, timeout=30,
        )
        assert rules == [
            "geo-fix-webrtc-chrome-udp-3478",
            "geo-fix-webrtc-msedge-udp-5349",
        ]
        # "unrelated-other-rule" must be excluded
        assert "unrelated-other-rule" not in rules

    @patch("src.system_config.sys.platform", "win32")
    @patch("src.system_config._list_firewall_rules_by_prefix")
    @patch("src.system_config.subprocess.run")
    def test_remove_by_prefix_deletes_found_rules(self, mock_run, mock_list):
        """remove_firewall_rules deletes each rule found by prefix query."""
        mock_list.return_value = [
            "geo-fix-webrtc-chrome-udp-3478",
            "geo-fix-webrtc-msedge-udp-5349",
        ]

        remove_firewall_rules()

        mock_list.assert_called_once_with(FW_RULE_PREFIX)
        assert mock_run.call_count == 2
        mock_run.assert_any_call(
            ["netsh", "advfirewall", "firewall", "delete", "rule",
             "name=geo-fix-webrtc-chrome-udp-3478"],
            capture_output=True, text=True, timeout=10,
        )
        mock_run.assert_any_call(
            ["netsh", "advfirewall", "firewall", "delete", "rule",
             "name=geo-fix-webrtc-msedge-udp-5349"],
            capture_output=True, text=True, timeout=10,
        )

    @patch("src.system_config.sys.platform", "win32")
    @patch("src.system_config.subprocess.run")
    def test_remove_by_prefix_fallback_on_parse_error(self, mock_run):
        """When subprocess raises during prefix query, falls back to fixed list."""
        import subprocess as _subprocess

        def side_effect(cmd, **kwargs):
            # The "show rule" call raises TimeoutExpired, triggering fallback
            if "show" in cmd:
                raise _subprocess.TimeoutExpired(cmd, 30)
            # Delete calls succeed
            return MagicMock(returncode=0)

        mock_run.side_effect = side_effect

        remove_firewall_rules()

        # Filter out the failed "show" call — remaining are delete calls
        delete_calls = [
            c for c in mock_run.call_args_list
            if "delete" in c[0][0]
        ]
        assert len(delete_calls) == len(BROWSER_EXES) * len(STUN_PORTS)

        # Verify a known fixed-name rule is in the calls via structured args
        expected_name = f"{FW_RULE_PREFIX}-chrome-udp-3478"
        mock_run.assert_any_call(
            ["netsh", "advfirewall", "firewall", "delete", "rule",
             f"name={expected_name}"],
            capture_output=True, text=True, timeout=10,
        )
