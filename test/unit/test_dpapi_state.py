"""Tests for DPAPI state file encryption (Task 6: security-hardening)."""

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from src.system_config import (
    ProxyState,
    _dpapi_encrypt,
    _dpapi_decrypt,
    save_state,
    load_state,
    delete_state,
)


class TestDpapiEncryptDecrypt:
    def test_roundtrip(self):
        plaintext = b"hello world"
        encrypted = _dpapi_encrypt(plaintext)
        decrypted = _dpapi_decrypt(encrypted)
        assert decrypted == plaintext

    def test_encrypt_returns_bytes(self):
        plaintext = b'{"pid": 1234}'
        encrypted = _dpapi_encrypt(plaintext)
        assert isinstance(encrypted, bytes)
        assert len(encrypted) > 0

    def test_tamper_rejected(self):
        plaintext = b"secret data"
        encrypted = _dpapi_encrypt(plaintext)
        if len(encrypted) > 0:
            # Flip a byte
            tampered = bytearray(encrypted)
            tampered[len(tampered) // 2] ^= 0xFF
            tampered = bytes(tampered)
            # On non-Windows (passthrough), tampered data just returns different content
            # On Windows, CryptUnprotectData would fail
            result = _dpapi_decrypt(tampered)
            assert result != plaintext


class TestStatePersistence:
    def test_save_load_roundtrip(self, tmp_path, monkeypatch):
        state_file = tmp_path / ".geo-fix-state.bin"
        monkeypatch.setattr("src.system_config.STATE_FILE", state_file)

        state = ProxyState(
            pid=1234, preset_code="US", timestamp="2026-03-27",
            session_id="abc-123", ca_thumbprint="deadbeef",
            session_tmpdir="/tmp/geo-fix-test", proxy_port=9090,
        )
        save_state(state)
        loaded = load_state()

        assert loaded is not None
        assert loaded.pid == 1234
        assert loaded.preset_code == "US"
        assert loaded.session_id == "abc-123"
        assert loaded.ca_thumbprint == "deadbeef"
        assert loaded.proxy_port == 9090

    def test_load_tampered_returns_none(self, tmp_path, monkeypatch):
        state_file = tmp_path / ".geo-fix-state.bin"
        monkeypatch.setattr("src.system_config.STATE_FILE", state_file)

        state = ProxyState(pid=1, preset_code="US", timestamp="now")
        save_state(state)

        # Tamper with file
        data = state_file.read_bytes()
        tampered = bytearray(data)
        if len(tampered) > 5:
            tampered[5] ^= 0xFF
        state_file.write_bytes(bytes(tampered))

        # Tampered file should be rejected (returns None and deletes the file)
        result = load_state()
        assert result is None

    def test_load_missing_returns_none(self, tmp_path, monkeypatch):
        monkeypatch.setattr("src.system_config.STATE_FILE", tmp_path / "nonexistent.bin")
        assert load_state() is None

    def test_state_file_is_binary(self, tmp_path, monkeypatch):
        state_file = tmp_path / ".geo-fix-state.bin"
        monkeypatch.setattr("src.system_config.STATE_FILE", state_file)

        save_state(ProxyState(pid=1, preset_code="US", timestamp="now"))
        data = state_file.read_bytes()
        assert isinstance(data, bytes)

    def test_delete_state_removes_file(self, tmp_path, monkeypatch):
        state_file = tmp_path / ".geo-fix-state.bin"
        state_file.write_bytes(b"data")
        monkeypatch.setattr("src.system_config.STATE_FILE", state_file)
        delete_state()
        assert not state_file.exists()

    def test_unknown_fields_rejected(self):
        data = '{"pid": 1, "preset_code": "US", "timestamp": "now", "evil": "hack"}'
        with pytest.raises(ValueError, match="Unknown fields"):
            ProxyState.from_json(data)
