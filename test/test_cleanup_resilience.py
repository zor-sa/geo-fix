"""Tests for robust cleanup with retry, startup check, and fallback (Task 2, R-4)."""

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch, call

import pytest

from src.system_config import (
    CLEANUP_LABEL_CA_CERT,
    CLEANUP_LABEL_SESSION_TMPDIR,
    CLEANUP_LABEL_PROXY,
    CLEANUP_LABEL_FIREFOX,
    CLEANUP_LABEL_FIREWALL,
    _CLEANUP_RETRY_DELAY,
)


@pytest.fixture
def pending_file(tmp_path):
    """Provide a temporary cleanup_pending.json path."""
    return tmp_path / "geo-fix" / "cleanup_pending.json"


@pytest.fixture
def mock_state():
    """Create a minimal ProxyState-like object for cleanup."""
    state = MagicMock()
    state.ca_thumbprint = "abc123"
    state.session_tmpdir = "/tmp/geo-fix-test"
    state.original_proxy_enable = 0
    state.original_proxy_server = ""
    state.original_proxy_override = ""
    state.firefox_prefs_modified = True
    state.firefox_prefs_backup = "/tmp/backup.js"
    return state


class TestCleanupRetriesOnFailure:
    """cleanup() retries each failed step once after 3-second delay."""

    @patch("src.system_config.time.sleep")
    @patch("src.system_config.delete_state")
    @patch("src.system_config.remove_firewall_rules")
    @patch("src.system_config.unset_firefox_proxy")
    @patch("src.system_config.unset_wininet_proxy")
    @patch("src.system_config.delete_session_tmpdir")
    @patch("src.system_config.uninstall_ca_cert")
    def test_cleanup_retries_on_failure(
        self, mock_ca, mock_tmpdir, mock_proxy, mock_firefox,
        mock_firewall, mock_del_state, mock_sleep, mock_state
    ):
        """Mock one cleanup step to raise on first call, succeed on second.
        Verify it is called twice and failures list is empty."""
        mock_ca.side_effect = [OSError("certutil busy"), None]

        from src.system_config import cleanup
        failures = cleanup(mock_state)

        assert mock_ca.call_count == 2
        mock_sleep.assert_called_with(_CLEANUP_RETRY_DELAY)
        assert failures == []

    @patch("src.system_config.time.sleep")
    @patch("src.system_config.delete_state")
    @patch("src.system_config.remove_firewall_rules")
    @patch("src.system_config.unset_firefox_proxy")
    @patch("src.system_config.unset_wininet_proxy")
    @patch("src.system_config.delete_session_tmpdir")
    @patch("src.system_config.uninstall_ca_cert")
    def test_cleanup_returns_label_when_both_attempts_fail(
        self, mock_ca, mock_tmpdir, mock_proxy, mock_firefox,
        mock_firewall, mock_del_state, mock_sleep, mock_state
    ):
        """When a step fails both attempts, its label is in the returned list."""
        mock_ca.side_effect = OSError("permanently broken")

        from src.system_config import cleanup
        failures = cleanup(mock_state)

        assert mock_ca.call_count == 2
        assert CLEANUP_LABEL_CA_CERT in failures


class TestCleanupReturnsEmptyOnSuccess:
    """cleanup() returns empty list when all steps succeed."""

    @patch("src.system_config.time.sleep")
    @patch("src.system_config.delete_state")
    @patch("src.system_config.remove_firewall_rules")
    @patch("src.system_config.unset_firefox_proxy")
    @patch("src.system_config.unset_wininet_proxy")
    @patch("src.system_config.delete_session_tmpdir")
    @patch("src.system_config.uninstall_ca_cert")
    def test_cleanup_returns_empty_on_success(
        self, mock_ca, mock_tmpdir, mock_proxy, mock_firefox,
        mock_firewall, mock_del_state, mock_sleep, mock_state
    ):
        """All steps succeed; verify cleanup() returns []."""
        from src.system_config import cleanup
        failures = cleanup(mock_state)

        assert failures == []
        mock_sleep.assert_not_called()


class TestCleanupWritesPendingJson:
    """write_cleanup_pending() writes failed operations to JSON file."""

    def test_cleanup_writes_pending_json_on_failure(self, pending_file):
        """Verify cleanup_pending.json is written with correct labels."""
        from src.system_config import write_cleanup_pending

        failed_ops = [CLEANUP_LABEL_CA_CERT, CLEANUP_LABEL_FIREWALL]
        with patch("src.system_config.CLEANUP_PENDING_FILE", pending_file):
            write_cleanup_pending(failed_ops)

        assert pending_file.exists()
        data = json.loads(pending_file.read_text())
        assert data == [CLEANUP_LABEL_CA_CERT, CLEANUP_LABEL_FIREWALL]


class TestStartupCleansPendingOperations:
    """check_pending_cleanup() reads and processes cleanup_pending.json on startup."""

    @patch("src.system_config.remove_firewall_rules")
    @patch("src.system_config.uninstall_ca_cert")
    def test_startup_cleans_pending_operations(
        self, mock_ca, mock_firewall, pending_file
    ):
        """Write a cleanup_pending.json with known labels; call check_pending_cleanup();
        verify operations executed and file deleted."""
        pending_file.parent.mkdir(parents=True, exist_ok=True)
        pending_file.write_text(json.dumps([CLEANUP_LABEL_CA_CERT, CLEANUP_LABEL_FIREWALL]))

        from src.system_config import check_pending_cleanup
        with patch("src.system_config.CLEANUP_PENDING_FILE", pending_file):
            check_pending_cleanup()

        mock_ca.assert_called_once()
        mock_firewall.assert_called_once()
        assert not pending_file.exists()

    def test_startup_noop_when_no_pending_file(self, pending_file):
        """check_pending_cleanup() is a no-op when file doesn't exist."""
        from src.system_config import check_pending_cleanup
        with patch("src.system_config.CLEANUP_PENDING_FILE", pending_file):
            check_pending_cleanup()
        # No exception raised, file still doesn't exist
        assert not pending_file.exists()

    @patch("src.system_config.remove_firewall_rules", side_effect=OSError("still broken"))
    @patch("src.system_config.uninstall_ca_cert")
    def test_startup_keeps_file_on_partial_failure(
        self, mock_ca, mock_firewall, pending_file
    ):
        """If some operations fail again, the file is kept with only failed labels."""
        pending_file.parent.mkdir(parents=True, exist_ok=True)
        pending_file.write_text(json.dumps([CLEANUP_LABEL_CA_CERT, CLEANUP_LABEL_FIREWALL]))

        from src.system_config import check_pending_cleanup
        with patch("src.system_config.CLEANUP_PENDING_FILE", pending_file):
            check_pending_cleanup()

        assert pending_file.exists()
        remaining = json.loads(pending_file.read_text())
        assert remaining == [CLEANUP_LABEL_FIREWALL]
        assert CLEANUP_LABEL_CA_CERT not in remaining

    def test_startup_ignores_invalid_labels(self, pending_file):
        """Invalid labels (non-string, unknown) are skipped, not dispatched."""
        pending_file.parent.mkdir(parents=True, exist_ok=True)
        pending_file.write_text(json.dumps([42, "unknown_label", None]))

        from src.system_config import check_pending_cleanup
        with patch("src.system_config.CLEANUP_PENDING_FILE", pending_file):
            check_pending_cleanup()

        # All invalid — file should be deleted
        assert not pending_file.exists()


class TestDoCleanupNotifiesUser:
    """_do_cleanup() writes pending file and prints to stderr on persistent failure."""

    @patch("src.system_config.time.sleep")
    @patch("src.system_config.delete_state")
    @patch("src.system_config.remove_firewall_rules", side_effect=OSError("blocked"))
    @patch("src.system_config.unset_firefox_proxy")
    @patch("src.system_config.unset_wininet_proxy")
    @patch("src.system_config.delete_session_tmpdir")
    @patch("src.system_config.uninstall_ca_cert")
    @patch("src.main.write_cleanup_pending")
    @patch("src.main.delete_cleanup_pending")
    @patch("src.main.load_state")
    def test_do_cleanup_notifies_user_on_persistent_failure(
        self, mock_load, mock_del_pending, mock_write_pending, mock_ca,
        mock_tmpdir, mock_proxy, mock_firefox, mock_firewall,
        mock_del_state, mock_sleep, mock_state, capsys
    ):
        """Mock cleanup() to return non-empty failures list; verify
        write_cleanup_pending() is called and failure details printed to stderr."""
        mock_firewall.side_effect = OSError("blocked")
        mock_load.return_value = mock_state

        import src.main as main_mod
        main_mod._cleanup_done = False

        with patch.object(main_mod, "_signal_watchdog_stop"), \
             patch.object(main_mod, "_remove_onlogon_task"), \
             patch.object(main_mod, "release_instance_lock"):
            main_mod._do_cleanup()

        mock_write_pending.assert_called_once()
        args = mock_write_pending.call_args[0][0]
        assert CLEANUP_LABEL_FIREWALL in args
        mock_del_pending.assert_not_called()

        captured = capsys.readouterr()
        assert "очистить" in captured.err

    @patch("src.system_config.time.sleep")
    @patch("src.system_config.delete_state")
    @patch("src.system_config.remove_firewall_rules")
    @patch("src.system_config.unset_firefox_proxy")
    @patch("src.system_config.unset_wininet_proxy")
    @patch("src.system_config.delete_session_tmpdir")
    @patch("src.system_config.uninstall_ca_cert")
    @patch("src.main.write_cleanup_pending")
    @patch("src.main.delete_cleanup_pending")
    @patch("src.main.load_state")
    def test_do_cleanup_deletes_pending_on_success(
        self, mock_load, mock_del_pending, mock_write_pending, mock_ca,
        mock_tmpdir, mock_proxy, mock_firefox, mock_firewall,
        mock_del_state, mock_sleep, mock_state
    ):
        """On successful cleanup, stale cleanup_pending.json is deleted."""
        mock_load.return_value = mock_state

        import src.main as main_mod
        main_mod._cleanup_done = False

        with patch.object(main_mod, "_signal_watchdog_stop"), \
             patch.object(main_mod, "_remove_onlogon_task"), \
             patch.object(main_mod, "release_instance_lock"):
            main_mod._do_cleanup()

        mock_write_pending.assert_not_called()
        mock_del_pending.assert_called_once()
