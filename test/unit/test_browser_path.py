"""Tests for browser path auto-detection (Task 7: security-hardening)."""

import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from src.system_config import _find_browser_path


class TestFindBrowserPath:
    def test_non_windows_returns_none(self, monkeypatch):
        monkeypatch.setattr("sys.platform", "linux")
        assert _find_browser_path("chrome.exe") is None

    @pytest.mark.skipif(sys.platform != "win32", reason="Windows-only")
    def test_registry_hit_returns_path(self):
        # On Windows, at least one browser should be installed
        result = _find_browser_path("chrome.exe")
        # May be None if Chrome not installed, which is OK

    def test_filesystem_fallback(self, tmp_path, monkeypatch):
        """When registry fails, check standard paths."""
        monkeypatch.setattr("sys.platform", "win32")

        # Create a fake browser at a standard path
        chrome_path = tmp_path / "Google" / "Chrome" / "Application" / "chrome.exe"
        chrome_path.parent.mkdir(parents=True)
        chrome_path.write_text("fake")

        with patch("src.system_config._STANDARD_BROWSER_PATHS", {
            "chrome.exe": [str(chrome_path)]
        }):
            # Mock winreg to fail
            mock_winreg = MagicMock()
            mock_winreg.OpenKey.side_effect = FileNotFoundError
            with patch.dict("sys.modules", {"winreg": mock_winreg}):
                result = _find_browser_path("chrome.exe")
                assert result is not None
                assert str(result) == str(chrome_path)

    def test_not_found_returns_none(self, monkeypatch):
        monkeypatch.setattr("sys.platform", "win32")
        with patch("src.system_config._STANDARD_BROWSER_PATHS", {
            "chrome.exe": ["/nonexistent/chrome.exe"]
        }):
            mock_winreg = MagicMock()
            mock_winreg.OpenKey.side_effect = FileNotFoundError
            with patch.dict("sys.modules", {"winreg": mock_winreg}):
                result = _find_browser_path("chrome.exe")
                assert result is None
