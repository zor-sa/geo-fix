"""Tests for Master setup — Task 1: Replace DumpMaster with minimal Master."""

import pytest
from unittest.mock import MagicMock, patch, call


class TestMasterClassNotDumpMaster:
    """Verify Master is used, not DumpMaster."""

    def test_master_class_not_dumpmaster(self):
        """_start_mitmproxy must use mitmproxy.master.Master, not DumpMaster."""
        import src.main as main_mod
        source = open(main_mod.__file__).read()
        assert "from mitmproxy.master import Master" in source or \
               "mitmproxy.master.Master" in source
        assert "DumpMaster" not in source


class TestEssentialAddonsPresent:
    """Verify essential addons are added to master."""

    @patch("src.main.check_proxy_running", return_value=True)
    def test_essential_addons_present(self, mock_check):
        """master.addons.add must receive Core, Proxyserver, NextLayer,
        TlsConfig, KeepServing, ErrorCheck instances."""
        from mitmproxy.addons.core import Core
        from mitmproxy.addons.proxyserver import Proxyserver
        from mitmproxy.addons.next_layer import NextLayer
        from mitmproxy.addons.tlsconfig import TlsConfig
        from mitmproxy.addons.keepserving import KeepServing
        from mitmproxy.addons.errorcheck import ErrorCheck

        mock_master_instance = MagicMock()
        mock_master_cls = MagicMock(return_value=mock_master_instance)
        # Prevent actual proxy start
        mock_master_instance.run = MagicMock()

        addon = MagicMock()

        with patch("mitmproxy.master.Master", mock_master_cls), \
             patch("mitmproxy.options.Options"):
            from src.main import _start_mitmproxy
            result = _start_mitmproxy(addon, port=19999)

        # Collect all args passed to addons.add
        add_calls = mock_master_instance.addons.add.call_args_list
        all_addon_args = []
        for c in add_calls:
            all_addon_args.extend(c.args)

        addon_types = {type(a) for a in all_addon_args}
        expected = {Core, Proxyserver, NextLayer, TlsConfig, KeepServing, ErrorCheck}
        assert expected.issubset(addon_types), \
            f"Missing addons: {expected - addon_types}"


class TestGeoFixAddonAddedAfterEssentials:
    """Verify GeoFixAddon is the last addon added."""

    @patch("src.main.check_proxy_running", return_value=True)
    def test_geofixaddon_added_after_essential_addons(self, mock_check):
        """GeoFixAddon instance must be the last addon in the add() call."""
        from mitmproxy.addons.core import Core

        mock_master_instance = MagicMock()
        mock_master_cls = MagicMock(return_value=mock_master_instance)
        mock_master_instance.run = MagicMock()

        addon = MagicMock()
        addon.__class__.__name__ = "GeoFixAddon"

        with patch("mitmproxy.master.Master", mock_master_cls), \
             patch("mitmproxy.options.Options"):
            from src.main import _start_mitmproxy
            _start_mitmproxy(addon, port=19998)

        add_calls = mock_master_instance.addons.add.call_args_list
        all_addon_args = []
        for c in add_calls:
            all_addon_args.extend(c.args)

        # GeoFixAddon should be the last one
        assert all_addon_args[-1] is addon, \
            "GeoFixAddon must be the last addon added"


class TestNoDumperAddon:
    """Verify Dumper is not in the addon chain."""

    @patch("src.main.check_proxy_running", return_value=True)
    def test_no_dumper_addon(self, mock_check):
        """No Dumper addon instance should be passed to master.addons.add()."""
        mock_master_instance = MagicMock()
        mock_master_cls = MagicMock(return_value=mock_master_instance)
        mock_master_instance.run = MagicMock()

        addon = MagicMock()

        with patch("mitmproxy.master.Master", mock_master_cls), \
             patch("mitmproxy.options.Options"):
            from src.main import _start_mitmproxy
            _start_mitmproxy(addon, port=19997)

        add_calls = mock_master_instance.addons.add.call_args_list
        all_addon_args = []
        for c in add_calls:
            all_addon_args.extend(c.args)

        for a in all_addon_args:
            assert type(a).__name__ != "Dumper", \
                "Dumper addon must not be in the addon chain"


class TestReturnsTuple:
    """Verify _start_mitmproxy returns (thread, master) tuple."""

    @patch("src.main.check_proxy_running", return_value=True)
    def test_returns_thread_and_master(self, mock_check):
        """_start_mitmproxy must return (thread, master) tuple."""
        import threading

        mock_master_instance = MagicMock()
        mock_master_cls = MagicMock(return_value=mock_master_instance)
        mock_master_instance.run = MagicMock()

        addon = MagicMock()

        with patch("mitmproxy.master.Master", mock_master_cls), \
             patch("mitmproxy.options.Options"):
            from src.main import _start_mitmproxy
            result = _start_mitmproxy(addon, port=19996)

        assert isinstance(result, tuple), "Must return a tuple"
        assert len(result) == 2, "Must return (thread, master)"
        thread, master = result
        assert isinstance(thread, threading.Thread)
        assert master is mock_master_instance, "Must return the actual master instance, not None"
