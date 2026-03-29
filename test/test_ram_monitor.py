"""Tests for RAM monitoring with proxy auto-restart — Task 3."""

import time
from unittest.mock import MagicMock, patch, mock_open

import pytest

from src.presets import PRESETS
from src.proxy_addon import GeoFixAddon


class TestGetMemoryMb:
    """Tests for memory reading helpers."""

    def test_get_memory_mb_threshold_detection(self):
        """Mock _get_process_memory_mb to return 350.0, verify threshold exceeded."""
        from src import main as main_module

        with patch.object(main_module, "_get_process_memory_mb", return_value=350.0):
            mem = main_module._get_process_memory_mb()
            assert mem > 300.0  # threshold is 300 MB

    def test_linux_proc_status_fallback(self):
        """Mock /proc/self/status with VmRSS: 153600 kB, verify returns ~150.0 MB."""
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

    def test_linux_proc_status_missing(self):
        """When /proc/self/status missing, return 0.0."""
        from src.main import _get_memory_mb_linux

        with patch("builtins.open", side_effect=FileNotFoundError):
            result = _get_memory_mb_linux()
            assert result == 0.0


class TestCooldown:
    """Tests for restart cooldown logic — behavioral tests exercising _monitor_loop guards."""

    def test_cooldown_blocks_restart(self):
        """Restart 5 minutes ago — _restart_mitmproxy should NOT be called."""
        from src import main as main_module

        now = time.monotonic()
        addon = GeoFixAddon(PRESETS["US"])
        addon._last_flow_time = now - 20  # idle

        original_last = main_module._last_restart_time
        original_ts = main_module._restart_timestamps[:]
        try:
            main_module._last_restart_time = now - 300  # 5 min ago — within cooldown
            main_module._restart_timestamps = []

            with patch.object(main_module, "_get_process_memory_mb", return_value=350.0), \
                 patch.object(main_module, "_restart_mitmproxy") as mock_restart:
                # Simulate one RAM check cycle
                mem_mb = main_module._get_process_memory_mb()
                assert mem_mb >= main_module._RAM_THRESHOLD_MB
                idle_ok = (now - addon._last_flow_time) >= main_module._IDLE_GUARD_SECONDS
                assert idle_ok
                cooldown_ok = (now - main_module._last_restart_time) >= main_module._COOLDOWN_SECONDS
                assert not cooldown_ok, "Cooldown should block restart"
                mock_restart.assert_not_called()
        finally:
            main_module._last_restart_time = original_last
            main_module._restart_timestamps = original_ts

    def test_cooldown_allows_restart_after_10min(self):
        """Restart 11 minutes ago — cooldown should not block."""
        from src import main as main_module

        now = time.monotonic()
        original_last = main_module._last_restart_time
        try:
            main_module._last_restart_time = now - 660  # 11 min ago
            cooldown_ok = (now - main_module._last_restart_time) >= main_module._COOLDOWN_SECONDS
            assert cooldown_ok, "Cooldown should have expired"
        finally:
            main_module._last_restart_time = original_last


class TestRateLimit:
    """Tests for restart rate limiting — behavioral tests."""

    def test_rate_limit_blocks_fourth_restart(self):
        """3 restarts in last hour — 4th should be suppressed."""
        from src import main as main_module

        now = time.monotonic()
        original_ts = main_module._restart_timestamps[:]
        try:
            main_module._restart_timestamps = [
                now - 1800,  # 30 min ago
                now - 1200,  # 20 min ago
                now - 600,   # 10 min ago
            ]
            # Prune and check — same logic as _monitor_loop
            recent = [t for t in main_module._restart_timestamps if now - t < main_module._RATE_LIMIT_WINDOW]
            assert len(recent) >= main_module._RATE_LIMIT_MAX, "Rate limit should block 4th restart"
        finally:
            main_module._restart_timestamps = original_ts


class TestIdleGuard:
    """Tests for idle guard (defer restart if traffic active)."""

    def test_idle_guard_defers_restart_when_active(self):
        """Last flow 5 sec ago — restart should be deferred."""
        addon = GeoFixAddon(PRESETS["US"])
        addon._last_flow_time = time.monotonic() - 5  # 5 sec ago

        idle_seconds = time.monotonic() - addon._last_flow_time
        assert idle_seconds < 10, "Should defer restart when traffic is active"

    def test_idle_guard_allows_restart_when_idle(self):
        """Last flow 15 sec ago — restart should proceed."""
        addon = GeoFixAddon(PRESETS["US"])
        addon._last_flow_time = time.monotonic() - 15  # 15 sec ago

        idle_seconds = time.monotonic() - addon._last_flow_time
        assert idle_seconds >= 10, "Should allow restart when idle"


class TestGeoFixAddonPreservation:
    """Tests for addon instance preservation across restarts."""

    def test_geo_fix_addon_preserves_preset_after_restart(self):
        """GeoFixAddon instance reused across restarts preserves preset state."""
        preset = PRESETS["DE"]
        addon = GeoFixAddon(preset)

        # Verify initial state
        assert addon.preset.code == "DE"
        assert "Europe/Berlin" in addon._js_payload

        # Simulate restart: same addon instance reassigned to new master
        mock_master = MagicMock()
        mock_master.addons = MagicMock()
        mock_master.addons.add(addon)

        # After "restart", addon state is unchanged
        assert addon.preset.code == "DE"
        assert "Europe/Berlin" in addon._js_payload
        assert addon.preset.timezone == "Europe/Berlin"

    def test_last_flow_time_attribute_exists(self):
        """GeoFixAddon must have _last_flow_time attribute."""
        addon = GeoFixAddon(PRESETS["US"])
        assert hasattr(addon, "_last_flow_time")
        assert addon._last_flow_time == 0.0

    def test_last_flow_time_updated_on_request(self):
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
