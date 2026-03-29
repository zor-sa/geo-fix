"""Tests for RAM monitoring with proxy auto-restart — Task 3."""

import time
from unittest.mock import MagicMock, patch, mock_open, call

import pytest

from src.presets import PRESETS
from src.proxy_addon import GeoFixAddon


class TestGetMemoryMb:
    """Tests for memory reading helpers."""

    def test_get_memory_mb_linux_parses_vmrss(self):
        """_get_memory_mb_linux parses VmRSS from /proc/self/status correctly."""
        from src.main import _get_memory_mb_linux

        proc_content = (
            "Name:\tpython\n"
            "VmPeak:\t 500000 kB\n"
            "VmSize:\t 400000 kB\n"
            "VmRSS:\t  153600 kB\n"
            "VmSwap:\t      0 kB\n"
        )
        with patch("builtins.open", mock_open(read_data=proc_content)):
            result = _get_memory_mb_linux()
            assert abs(result - 150.0) < 1.0  # 153600 kB = 150 MB

    def test_get_memory_mb_linux_missing_proc(self):
        """When /proc/self/status missing, return 0.0."""
        from src.main import _get_memory_mb_linux

        with patch("builtins.open", side_effect=FileNotFoundError):
            result = _get_memory_mb_linux()
            assert result == 0.0

    def test_get_process_memory_mb_dispatches_linux(self):
        """On non-Windows, _get_process_memory_mb calls _get_memory_mb_linux."""
        from src import main as main_module

        with patch.object(main_module, "_get_memory_mb_linux", return_value=150.0) as mock_linux, \
             patch("src.main.sys") as mock_sys:
            mock_sys.platform = "linux"
            result = main_module._get_process_memory_mb()
            mock_linux.assert_called_once()
            assert result == 150.0


class TestShouldRestart:
    """Tests for _should_restart guard logic — exercises actual production code."""

    def test_below_threshold_blocks(self):
        """Memory below 300MB — should not restart."""
        from src.main import _should_restart

        should, reason = _should_restart(
            mem_mb=200.0, last_flow_time=0.0,
            last_restart_time=0.0, restart_timestamps=[], now=time.monotonic()
        )
        assert should is False
        assert "below_threshold" in reason

    def test_threshold_exceeded_all_guards_pass(self):
        """Memory above threshold, all guards clear — should restart."""
        from src.main import _should_restart

        now = time.monotonic()
        should, reason = _should_restart(
            mem_mb=350.0, last_flow_time=now - 20,  # idle 20s
            last_restart_time=now - 700,  # cooldown expired
            restart_timestamps=[], now=now
        )
        assert should is True
        assert reason == ""

    def test_idle_guard_blocks_when_traffic_active(self):
        """Last flow 5 sec ago — idle guard should block restart."""
        from src.main import _should_restart

        now = time.monotonic()
        should, reason = _should_restart(
            mem_mb=350.0, last_flow_time=now - 5,
            last_restart_time=0.0, restart_timestamps=[], now=now
        )
        assert should is False
        assert "traffic_active" in reason

    def test_idle_guard_allows_when_idle(self):
        """Last flow 15 sec ago — idle guard should not block."""
        from src.main import _should_restart

        now = time.monotonic()
        should, reason = _should_restart(
            mem_mb=350.0, last_flow_time=now - 15,
            last_restart_time=0.0, restart_timestamps=[], now=now
        )
        assert should is True

    def test_cooldown_blocks_within_10min(self):
        """Restart 5 min ago — cooldown should block."""
        from src.main import _should_restart

        now = time.monotonic()
        should, reason = _should_restart(
            mem_mb=350.0, last_flow_time=now - 20,
            last_restart_time=now - 300,  # 5 min ago
            restart_timestamps=[], now=now
        )
        assert should is False
        assert "cooldown" in reason

    def test_cooldown_allows_after_10min(self):
        """Restart 11 min ago — cooldown should not block."""
        from src.main import _should_restart

        now = time.monotonic()
        should, reason = _should_restart(
            mem_mb=350.0, last_flow_time=now - 20,
            last_restart_time=now - 660,  # 11 min ago
            restart_timestamps=[], now=now
        )
        assert should is True

    def test_rate_limit_blocks_fourth_restart(self):
        """3 restarts in last hour — 4th should be blocked."""
        from src.main import _should_restart

        now = time.monotonic()
        timestamps = [now - 1800, now - 1200, now - 600]  # 3 in last hour
        should, reason = _should_restart(
            mem_mb=350.0, last_flow_time=now - 20,
            last_restart_time=now - 660,  # cooldown expired
            restart_timestamps=timestamps, now=now
        )
        assert should is False
        assert "rate_limit" in reason

    def test_rate_limit_ignores_old_timestamps(self):
        """Old timestamps (>1 hour) should be pruned and not count."""
        from src.main import _should_restart

        now = time.monotonic()
        timestamps = [now - 7200, now - 5400, now - 3700]  # all >1 hour ago
        should, reason = _should_restart(
            mem_mb=350.0, last_flow_time=now - 20,
            last_restart_time=now - 660,
            restart_timestamps=timestamps, now=now
        )
        assert should is True


class TestRestartMitmproxy:
    """Tests for _restart_mitmproxy function."""

    def test_restart_success_updates_state(self):
        """Successful restart: shuts down old master, installs new CA, updates state."""
        from src.main import _restart_mitmproxy

        old_master = MagicMock()
        addon = GeoFixAddon(PRESETS["US"])
        state = MagicMock()
        state.ca_thumbprint = "old_thumbprint_abc"

        with patch("src.main._start_mitmproxy") as mock_start, \
             patch("src.main.install_ca_cert", return_value="new_thumbprint_xyz") as mock_install, \
             patch("src.main.delete_ca_key_files") as mock_del_keys, \
             patch("src.main.delete_ca_public_cert") as mock_del_cert, \
             patch("src.main.save_state") as mock_save, \
             patch("src.system_config.uninstall_ca_cert") as mock_uninstall:

            mock_new_master = MagicMock()
            mock_new_thread = MagicMock()
            mock_start.return_value = (mock_new_thread, mock_new_master)

            thread, master = _restart_mitmproxy(old_master, addon, "/tmp/test", 8080, state)

            # Old master shut down
            old_master.shutdown.assert_called_once()
            # Old CA uninstalled with old thumbprint
            mock_uninstall.assert_called_once_with(thumbprint="old_thumbprint_abc")
            # New master started
            mock_start.assert_called_once_with(addon, confdir="/tmp/test", port=8080)
            # New CA installed
            mock_install.assert_called_once_with("/tmp/test")
            # Key files deleted
            mock_del_keys.assert_called_once_with("/tmp/test")
            mock_del_cert.assert_called_once_with("/tmp/test")
            # State updated
            assert state.ca_thumbprint == "new_thumbprint_xyz"
            mock_save.assert_called_once_with(state)
            # Returns new thread and master
            assert thread is mock_new_thread
            assert master is mock_new_master

    def test_restart_ca_failure_cleans_up_keys(self):
        """When install_ca_cert returns None: keys deleted, new master shut down, returns (None, None)."""
        from src.main import _restart_mitmproxy

        old_master = MagicMock()
        addon = GeoFixAddon(PRESETS["US"])
        state = MagicMock()
        state.ca_thumbprint = "old_thumb"

        with patch("src.main._start_mitmproxy") as mock_start, \
             patch("src.main.install_ca_cert", return_value=None), \
             patch("src.main.delete_ca_key_files") as mock_del_keys, \
             patch("src.main.delete_ca_public_cert") as mock_del_cert, \
             patch("src.main.save_state") as mock_save, \
             patch("src.system_config.uninstall_ca_cert"):

            mock_new_master = MagicMock()
            mock_start.return_value = (MagicMock(), mock_new_master)

            thread, master = _restart_mitmproxy(old_master, addon, "/tmp/test", 8080, state)

            assert thread is None
            assert master is None
            # Key files still deleted on failure (security)
            mock_del_keys.assert_called_once()
            mock_del_cert.assert_called_once()
            # New master shut down
            mock_new_master.shutdown.assert_called_once()
            # State NOT updated
            mock_save.assert_not_called()

    def test_restart_shutdown_failure_returns_none(self):
        """When old master.shutdown() raises, returns (None, None) immediately."""
        from src.main import _restart_mitmproxy

        old_master = MagicMock()
        old_master.shutdown.side_effect = RuntimeError("shutdown failed")
        addon = GeoFixAddon(PRESETS["US"])
        state = MagicMock()
        state.ca_thumbprint = "thumb"

        with patch("src.main._start_mitmproxy") as mock_start:
            thread, master = _restart_mitmproxy(old_master, addon, "/tmp/test", 8080, state)

            assert thread is None
            assert master is None
            mock_start.assert_not_called()  # Never reaches new master start


class TestGeoFixAddonPreservation:
    """Tests for addon instance preservation across restarts."""

    def test_geo_fix_addon_preserves_preset_after_restart(self):
        """GeoFixAddon instance reused across restarts preserves preset state."""
        preset = PRESETS["DE"]
        addon = GeoFixAddon(preset)

        assert addon.preset.code == "DE"
        assert "Europe/Berlin" in addon._js_payload

        # Simulate restart: same addon instance reassigned to new master
        mock_master = MagicMock()
        mock_master.addons.add(addon)

        # After "restart", addon state is unchanged
        assert addon.preset.code == "DE"
        assert "Europe/Berlin" in addon._js_payload
        assert addon.preset.timezone == "Europe/Berlin"

    def test_last_flow_time_attribute_exists(self):
        """GeoFixAddon must have _last_flow_time attribute initialized to 0.0."""
        addon = GeoFixAddon(PRESETS["US"])
        assert hasattr(addon, "_last_flow_time")
        assert addon._last_flow_time == 0.0

    def test_last_flow_time_updated_on_all_requests(self):
        """request() hook updates _last_flow_time for ALL traffic (not just target domains)."""
        addon = GeoFixAddon(PRESETS["US"])
        assert addon._last_flow_time == 0.0

        # Non-target domain flow — _last_flow_time must still update
        flow = MagicMock()
        flow.request.host = "example.com"
        flow.request.headers = {"Accept-Language": "ru-RU"}

        with patch("src.proxy_addon.is_target_domain", return_value=False):
            before = time.monotonic()
            addon.request(flow)
            after = time.monotonic()

        assert addon._last_flow_time >= before
        assert addon._last_flow_time <= after
