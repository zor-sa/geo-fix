"""Tests for port selection logic (Task 5: security-hardening)."""

import socket
from unittest.mock import patch

import pytest

from src.main import _select_port, PROXY_HOST


class TestSelectPort:
    def test_uses_requested_when_free(self):
        # Find a free port
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind((PROXY_HOST, 0))
            free_port = s.getsockname()[1]
        # Now it's free — request it
        result = _select_port(free_port)
        assert result == free_port

    def test_auto_selects_when_requested_occupied(self):
        # Hold a port
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind((PROXY_HOST, 0))
            held_port = s.getsockname()[1]
            result = _select_port(held_port)
            assert result != held_port
            assert result > 0

    def test_default_8080_when_free(self):
        # Try to bind 8080 to check if free
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind((PROXY_HOST, 8080))
            # 8080 is free
            result = _select_port(None)
            assert result == 8080
        except OSError:
            pytest.skip("Port 8080 is occupied")

    def test_auto_selects_when_default_occupied(self):
        # Hold port 8080
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                s.bind((PROXY_HOST, 8080))
                result = _select_port(None)
                assert result != 8080
                assert result > 0
        except OSError:
            # Port 8080 already occupied by something else
            result = _select_port(None)
            assert result != 8080
            assert result > 0
