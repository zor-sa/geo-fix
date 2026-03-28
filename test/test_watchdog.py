"""Tests for watchdog subprocess (Task 4: security-hardening)."""

import json
import os
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from src.watchdog import (
    _check_stop_flag,
    _is_process_alive,
    run_watchdog,
    STOP_FLAG_NAME,
)
from src.system_config import ProxyState


def _make_state_json(**overrides):
    defaults = dict(
        pid=1234, preset_code="US", timestamp="2026-03-27",
        session_id="test-session-123", session_tmpdir="/tmp/geo-fix-test",
        ca_thumbprint="abcdef",
    )
    defaults.update(overrides)
    return ProxyState(**defaults).to_json()


class TestStopFlag:
    def test_returns_true_for_matching_token(self, tmp_path):
        flag = tmp_path / STOP_FLAG_NAME
        flag.write_text("secret-token")
        assert _check_stop_flag(str(tmp_path), "secret-token") is True

    def test_returns_false_for_wrong_token(self, tmp_path):
        flag = tmp_path / STOP_FLAG_NAME
        flag.write_text("wrong-token")
        assert _check_stop_flag(str(tmp_path), "correct-token") is False

    def test_returns_false_when_no_flag(self, tmp_path):
        assert _check_stop_flag(str(tmp_path), "any-token") is False


class TestIsProcessAlive:
    def test_current_process_is_alive(self):
        assert _is_process_alive(os.getpid()) is True

    def test_nonexistent_pid_is_dead(self):
        # PID 99999999 should not exist
        assert _is_process_alive(99999999) is False


class TestWatchdogLoop:
    @patch("src.watchdog.time.sleep")
    @patch("src.watchdog._is_process_alive", return_value=True)
    def test_no_cleanup_when_main_alive(self, mock_alive, mock_sleep, tmp_path):
        """Verify cleanup is NOT called when main process is alive."""
        call_count = [0]
        original_alive = mock_alive.side_effect

        def side_effect(pid):
            call_count[0] += 1
            if call_count[0] >= 3:
                # After 3 checks, write stop flag to exit loop
                (tmp_path / STOP_FLAG_NAME).write_text("test-token")
            return True

        mock_alive.side_effect = side_effect

        with patch("src.watchdog.cleanup") as mock_cleanup:
            run_watchdog(
                main_pid=12345,
                state_file=str(tmp_path / "state.json"),
                session_tmpdir=str(tmp_path),
                session_id="test-session",
                stop_token="test-token",
            )
            mock_cleanup.assert_not_called()

    @patch("src.watchdog.time.sleep")
    @patch("src.watchdog._is_process_alive", return_value=False)
    def test_calls_cleanup_on_main_death(self, mock_alive, mock_sleep, tmp_path):
        """AC-3.1 unit: cleanup called when main process dies."""
        state_file = tmp_path / "state.json"
        state_file.write_text(_make_state_json(session_id="my-session"))

        with patch("src.watchdog.load_state") as mock_load:
            mock_state = MagicMock()
            mock_state.session_id = "my-session"
            mock_load.return_value = mock_state

            with patch("src.watchdog.cleanup") as mock_cleanup:
                run_watchdog(
                    main_pid=12345,
                    state_file=str(state_file),
                    session_tmpdir=str(tmp_path),
                    session_id="my-session",
                    stop_token="token",
                )
                mock_cleanup.assert_called_once_with(mock_state)

    @patch("src.watchdog.time.sleep")
    @patch("src.watchdog._is_process_alive", return_value=False)
    def test_session_id_mismatch_no_cleanup(self, mock_alive, mock_sleep, tmp_path):
        """Verify cleanup NOT called when session IDs don't match."""
        state_file = tmp_path / "state.json"
        state_file.write_text(_make_state_json(session_id="different-session"))

        with patch("src.watchdog.load_state") as mock_load:
            mock_state = MagicMock()
            mock_state.session_id = "different-session"
            mock_load.return_value = mock_state

            with patch("src.watchdog.cleanup") as mock_cleanup:
                run_watchdog(
                    main_pid=12345,
                    state_file=str(state_file),
                    session_tmpdir=str(tmp_path),
                    session_id="my-session",
                    stop_token="token",
                )
                mock_cleanup.assert_not_called()

    @patch("src.watchdog.time.sleep")
    @patch("src.watchdog._is_process_alive", return_value=False)
    def test_session_id_match_calls_cleanup(self, mock_alive, mock_sleep, tmp_path):
        """Verify cleanup called when session IDs match."""
        with patch("src.watchdog.load_state") as mock_load:
            mock_state = MagicMock()
            mock_state.session_id = "matching-session"
            mock_load.return_value = mock_state

            with patch("src.watchdog.cleanup") as mock_cleanup:
                run_watchdog(
                    main_pid=12345,
                    state_file=str(tmp_path / "state.json"),
                    session_tmpdir=str(tmp_path),
                    session_id="matching-session",
                    stop_token="token",
                )
                mock_cleanup.assert_called_once()

    @patch("src.watchdog.time.sleep")
    def test_exits_on_stop_token(self, mock_sleep, tmp_path):
        """Verify watchdog exits cleanly when stop token is written."""
        (tmp_path / STOP_FLAG_NAME).write_text("my-stop-token")

        with patch("src.watchdog.cleanup") as mock_cleanup:
            run_watchdog(
                main_pid=os.getpid(),
                state_file=str(tmp_path / "state.json"),
                session_tmpdir=str(tmp_path),
                session_id="test",
                stop_token="my-stop-token",
            )
            mock_cleanup.assert_not_called()
