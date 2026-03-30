"""Tests for VPN monitor and watchdog health check logic."""

import sys
from unittest.mock import MagicMock, patch

import pytest

from src.health_check import VpnStatus


class TestVpnMonitor:
    """Tests for VPN disconnect/reconnect detection in _monitor_tick."""

    @patch("src.main.check_vpn_status", return_value=VpnStatus.NOT_DETECTED)
    def test_vpn_monitor_detects_disconnect(self, mock_vpn, capsys):
        from src.main import _monitor_tick

        # First tick: transition from None (initial) -> NOT_DETECTED
        last_vpn = _monitor_tick(VpnStatus.DETECTED)
        assert last_vpn == VpnStatus.NOT_DETECTED
        captured = capsys.readouterr()
        assert "VPN отключён" in captured.err

    @patch("src.main.check_vpn_status", return_value=VpnStatus.NOT_DETECTED)
    def test_vpn_monitor_no_duplicate_notification(self, mock_vpn, capsys):
        from src.main import _monitor_tick

        # First tick: DETECTED -> NOT_DETECTED (should warn)
        last_vpn = _monitor_tick(VpnStatus.DETECTED)
        captured1 = capsys.readouterr()
        assert "VPN отключён" in captured1.err

        # Second tick: NOT_DETECTED -> NOT_DETECTED (should NOT warn again)
        last_vpn = _monitor_tick(last_vpn)
        captured2 = capsys.readouterr()
        assert "VPN отключён" not in captured2.err

    @patch("src.main.check_vpn_status")
    def test_vpn_monitor_detects_restore(self, mock_vpn, capsys):
        from src.main import _monitor_tick

        # First tick: NOT_DETECTED
        mock_vpn.return_value = VpnStatus.NOT_DETECTED
        last_vpn = _monitor_tick(VpnStatus.DETECTED)
        capsys.readouterr()  # clear

        # Second tick: DETECTED (restore)
        mock_vpn.return_value = VpnStatus.DETECTED
        last_vpn = _monitor_tick(last_vpn)
        captured = capsys.readouterr()
        assert "VPN восстановлен" in captured.err


    @patch("src.main.check_vpn_status")
    def test_vpn_unknown_preserves_last_state(self, mock_vpn, capsys):
        from src.main import _monitor_tick

        # Disconnect first
        mock_vpn.return_value = VpnStatus.NOT_DETECTED
        last_vpn = _monitor_tick(VpnStatus.DETECTED)
        capsys.readouterr()  # clear

        # UNKNOWN tick — last_vpn should stay NOT_DETECTED
        mock_vpn.return_value = VpnStatus.UNKNOWN
        last_vpn = _monitor_tick(last_vpn)
        assert last_vpn == VpnStatus.NOT_DETECTED
        captured = capsys.readouterr()
        assert "VPN отключён" not in captured.err
        assert "VPN восстановлен" not in captured.err

        # Restore — should trigger message because last_vpn is still NOT_DETECTED
        mock_vpn.return_value = VpnStatus.DETECTED
        last_vpn = _monitor_tick(last_vpn)
        captured = capsys.readouterr()
        assert "VPN восстановлен" in captured.err


class TestWatchdogHealth:
    """Tests for watchdog death detection and respawn in _monitor_tick."""

    @patch("src.main._spawn_watchdog")
    @patch("src.main.check_vpn_status", return_value=VpnStatus.DETECTED)
    def test_watchdog_respawn_on_death(self, mock_vpn, mock_spawn):
        import src.main as main_mod

        mock_proc = MagicMock()
        mock_proc.poll.return_value = 1  # dead
        mock_proc.returncode = 1
        mock_spawn.return_value = MagicMock()  # new proc

        original = main_mod._watchdog_proc
        try:
            main_mod._watchdog_proc = mock_proc
            main_mod._session_tmpdir = "/tmp/test"
            main_mod._session_id = "test-session"
            main_mod._stop_token = "test-token"

            main_mod._monitor_tick(VpnStatus.DETECTED)

            mock_spawn.assert_called_once()
        finally:
            main_mod._watchdog_proc = original

    @patch("src.main._spawn_watchdog")
    @patch("src.main.check_vpn_status", return_value=VpnStatus.DETECTED)
    def test_watchdog_no_respawn_when_alive(self, mock_vpn, mock_spawn):
        import src.main as main_mod

        mock_proc = MagicMock()
        mock_proc.poll.return_value = None  # alive

        original = main_mod._watchdog_proc
        try:
            main_mod._watchdog_proc = mock_proc

            main_mod._monitor_tick(VpnStatus.DETECTED)

            mock_spawn.assert_not_called()
        finally:
            main_mod._watchdog_proc = original

    @patch("src.main._spawn_watchdog", side_effect=OSError("spawn failed"))
    @patch("src.main.check_vpn_status", return_value=VpnStatus.DETECTED)
    def test_watchdog_respawn_failure_continues(self, mock_vpn, mock_spawn):
        """Monitor loop must survive watchdog respawn failure."""
        import src.main as main_mod

        mock_proc = MagicMock()
        mock_proc.poll.return_value = 1  # dead
        mock_proc.returncode = 1

        original = main_mod._watchdog_proc
        try:
            main_mod._watchdog_proc = mock_proc
            main_mod._session_tmpdir = "/tmp/test"
            main_mod._session_id = "test-session"
            main_mod._stop_token = "test-token"

            # Should not raise — error is caught and logged
            result = main_mod._monitor_tick(VpnStatus.DETECTED)
            assert result == VpnStatus.DETECTED
        finally:
            main_mod._watchdog_proc = original

    @patch("src.main._spawn_watchdog")
    @patch("src.main.check_vpn_status", return_value=VpnStatus.DETECTED)
    def test_watchdog_none_skips_poll(self, mock_vpn, mock_spawn):
        """When _watchdog_proc is None (never spawned), skip poll entirely."""
        import src.main as main_mod

        original = main_mod._watchdog_proc
        try:
            main_mod._watchdog_proc = None

            main_mod._monitor_tick(VpnStatus.DETECTED)

            mock_spawn.assert_not_called()
        finally:
            main_mod._watchdog_proc = original
