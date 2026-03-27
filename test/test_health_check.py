"""Tests for health check module."""

import os

import pytest

from src.health_check import (
    VpnStatus,
    _is_pid_running,
    acquire_instance_lock,
    check_proxy_running,
    release_instance_lock,
    PID_FILE,
)


class TestVpnStatus:
    def test_enum_values(self):
        assert VpnStatus.DETECTED.value == "vpn_detected"
        assert VpnStatus.NOT_DETECTED.value == "no_vpn"
        assert VpnStatus.UNKNOWN.value == "unknown"


class TestCheckProxyRunning:
    def test_port_not_listening(self):
        # Use a port that's definitely not in use
        assert check_proxy_running("127.0.0.1", 19999) is False


class TestPidRunning:
    def test_current_process_is_running(self):
        assert _is_pid_running(os.getpid()) is True

    def test_nonexistent_pid(self):
        # PID 99999999 is very unlikely to exist
        assert _is_pid_running(99999999) is False


class TestInstanceLock:
    def test_acquire_and_release(self, tmp_path, monkeypatch):
        pid_file = tmp_path / ".geo-fix.pid"
        monkeypatch.setattr("src.health_check.PID_FILE", pid_file)

        assert acquire_instance_lock() is True
        assert pid_file.exists()
        assert int(pid_file.read_text().strip()) == os.getpid()

        release_instance_lock()
        assert not pid_file.exists()

    def test_second_acquire_fails(self, tmp_path, monkeypatch):
        pid_file = tmp_path / ".geo-fix.pid"
        monkeypatch.setattr("src.health_check.PID_FILE", pid_file)

        # Write current PID (simulating running instance)
        pid_file.write_text(str(os.getpid()))

        # Second acquire should fail (same PID is running)
        assert acquire_instance_lock() is False

    def test_stale_pid_cleaned_up(self, tmp_path, monkeypatch):
        pid_file = tmp_path / ".geo-fix.pid"
        monkeypatch.setattr("src.health_check.PID_FILE", pid_file)

        # Write a non-existent PID
        pid_file.write_text("99999999")

        # Should succeed because the old PID is not running
        assert acquire_instance_lock() is True

    def test_corrupt_pid_file(self, tmp_path, monkeypatch):
        pid_file = tmp_path / ".geo-fix.pid"
        monkeypatch.setattr("src.health_check.PID_FILE", pid_file)

        pid_file.write_text("not-a-number")

        # Should handle gracefully and acquire
        assert acquire_instance_lock() is True
