"""Tests for Windows Location Services registry control."""

import sys
from unittest.mock import MagicMock, call, patch

import pytest


LOCATION_KEY_PATH = (
    r"SOFTWARE\Microsoft\Windows\CurrentVersion\DeviceAccess\Global"
    r"\{BFA794E4-F964-4FDB-90F6-51056BFE4B44}"
)
LOCATION_VALUE_NAME = "Value"


class TestDisableLocationServices:
    def _make_winreg(self, query_value="Allow"):
        """Build a mock winreg module."""
        winreg = MagicMock()
        winreg.HKEY_CURRENT_USER = 0x80000001
        winreg.KEY_READ = 0x20019
        winreg.KEY_SET_VALUE = 0x0002
        winreg.REG_SZ = 1

        mock_key = MagicMock()
        mock_key.__enter__ = MagicMock(return_value=mock_key)
        mock_key.__exit__ = MagicMock(return_value=False)
        winreg.CreateKeyEx.return_value = mock_key

        if isinstance(query_value, Exception):
            winreg.QueryValueEx.side_effect = query_value
        else:
            winreg.QueryValueEx.return_value = (query_value, 1)

        return winreg, mock_key

    def test_disable_reads_before_writing(self):
        """QueryValueEx must be called before SetValueEx."""
        from src.system_config import disable_location_services

        winreg, mock_key = self._make_winreg("Allow")
        call_order = []
        winreg.QueryValueEx.side_effect = lambda *a, **kw: (
            call_order.append("query") or ("Allow", 1)
        )
        winreg.SetValueEx.side_effect = lambda *a, **kw: call_order.append("set")

        with patch.dict("sys.modules", {"winreg": winreg}), \
             patch("sys.platform", "win32"):
            disable_location_services()

        assert call_order.index("query") < call_order.index("set"), (
            "QueryValueEx must precede SetValueEx"
        )
        hive_arg = winreg.CreateKeyEx.call_args[0][0]
        assert hive_arg == winreg.HKEY_CURRENT_USER, (
            "CreateKeyEx must use HKEY_CURRENT_USER"
        )

    def test_disable_returns_original_allow(self):
        """When registry has 'Allow', returns 'Allow' and writes 'Deny'."""
        from src.system_config import disable_location_services

        winreg, mock_key = self._make_winreg("Allow")

        with patch.dict("sys.modules", {"winreg": winreg}), \
             patch("sys.platform", "win32"):
            result = disable_location_services()

        assert result == "Allow"
        winreg.SetValueEx.assert_called_once()
        args = winreg.SetValueEx.call_args[0]
        assert args[-1] == "Deny"
        hive_arg = winreg.CreateKeyEx.call_args[0][0]
        assert hive_arg == winreg.HKEY_CURRENT_USER, (
            "CreateKeyEx must use HKEY_CURRENT_USER"
        )

    def test_disable_key_not_found_returns_none(self):
        """When QueryValueEx raises FileNotFoundError, returns None but still writes 'Deny'."""
        from src.system_config import disable_location_services

        winreg, mock_key = self._make_winreg(FileNotFoundError())

        with patch.dict("sys.modules", {"winreg": winreg}), \
             patch("sys.platform", "win32"):
            result = disable_location_services()

        assert result is None
        winreg.SetValueEx.assert_called_once()
        args = winreg.SetValueEx.call_args[0]
        assert args[-1] == "Deny"
        hive_arg = winreg.CreateKeyEx.call_args[0][0]
        assert hive_arg == winreg.HKEY_CURRENT_USER, (
            "CreateKeyEx must use HKEY_CURRENT_USER"
        )

    def test_disable_registry_error_graceful(self):
        """When CreateKeyEx raises OSError, returns None without raising."""
        from src.system_config import disable_location_services

        winreg = MagicMock()
        winreg.HKEY_CURRENT_USER = 0x80000001
        winreg.KEY_READ = 0x20019
        winreg.KEY_SET_VALUE = 0x0002
        winreg.CreateKeyEx.side_effect = OSError("Access denied")

        with patch.dict("sys.modules", {"winreg": winreg}), \
             patch("sys.platform", "win32"):
            result = disable_location_services()

        assert result is None
        winreg.SetValueEx.assert_not_called()

    def test_non_windows_disable_noop(self, monkeypatch):
        """On non-Windows platform, returns None and never touches winreg."""
        from src.system_config import disable_location_services

        monkeypatch.setattr(sys, "platform", "linux")

        winreg = MagicMock()
        with patch.dict("sys.modules", {"winreg": winreg}):
            result = disable_location_services()

        assert result is None
        winreg.OpenKey.assert_not_called()


class TestRestoreLocationServices:
    def _make_winreg(self):
        winreg = MagicMock()
        winreg.HKEY_CURRENT_USER = 0x80000001
        winreg.KEY_SET_VALUE = 0x0002
        winreg.REG_SZ = 1

        mock_key = MagicMock()
        mock_key.__enter__ = MagicMock(return_value=mock_key)
        mock_key.__exit__ = MagicMock(return_value=False)
        winreg.OpenKey.return_value = mock_key
        return winreg, mock_key

    def test_restore_writes_original_allow(self):
        """restore('Allow') writes 'Allow' back to registry."""
        from src.system_config import restore_location_services

        winreg, mock_key = self._make_winreg()

        with patch.dict("sys.modules", {"winreg": winreg}), \
             patch("sys.platform", "win32"):
            restore_location_services("Allow")

        winreg.SetValueEx.assert_called_once()
        args = winreg.SetValueEx.call_args[0]
        assert args[-1] == "Allow"
        winreg.DeleteValue.assert_not_called()
        hive_arg = winreg.OpenKey.call_args[0][0]
        assert hive_arg == winreg.HKEY_CURRENT_USER, (
            "OpenKey must use HKEY_CURRENT_USER"
        )

    def test_restore_writes_original_deny(self):
        """restore('Deny') writes 'Deny' back to registry."""
        from src.system_config import restore_location_services

        winreg, mock_key = self._make_winreg()

        with patch.dict("sys.modules", {"winreg": winreg}), \
             patch("sys.platform", "win32"):
            restore_location_services("Deny")

        winreg.SetValueEx.assert_called_once()
        args = winreg.SetValueEx.call_args[0]
        assert args[-1] == "Deny"

    def test_restore_none_deletes_key(self):
        """restore(None) calls DeleteValue instead of SetValueEx."""
        from src.system_config import restore_location_services

        winreg, mock_key = self._make_winreg()

        with patch.dict("sys.modules", {"winreg": winreg}), \
             patch("sys.platform", "win32"):
            restore_location_services(None)

        winreg.DeleteValue.assert_called_once()
        winreg.SetValueEx.assert_not_called()

    def test_restore_invalid_value_defaults_to_deny(self):
        """restore('garbage') logs a warning and writes 'Deny'."""
        from src.system_config import restore_location_services

        winreg, mock_key = self._make_winreg()

        with patch.dict("sys.modules", {"winreg": winreg}), \
             patch("sys.platform", "win32"), \
             patch("src.system_config.logger") as mock_logger:
            restore_location_services("garbage")

        mock_logger.warning.assert_called()
        winreg.SetValueEx.assert_called_once()
        args = winreg.SetValueEx.call_args[0]
        assert args[-1] == "Deny"

    def test_non_windows_restore_noop(self, monkeypatch):
        """On non-Windows platform, returns immediately without any registry calls."""
        from src.system_config import restore_location_services

        monkeypatch.setattr(sys, "platform", "linux")

        winreg = MagicMock()
        with patch.dict("sys.modules", {"winreg": winreg}):
            restore_location_services("Allow")

        winreg.OpenKey.assert_not_called()
        winreg.SetValueEx.assert_not_called()
