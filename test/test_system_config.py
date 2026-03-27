"""Tests for system configuration module."""

import json

import pytest

from src.system_config import ProxyState, delete_state, load_state, save_state


class TestProxyState:
    def test_to_json(self):
        state = ProxyState(pid=1234, preset_code="US", timestamp="2026-03-27T12:00:00")
        data = json.loads(state.to_json())
        assert data["pid"] == 1234
        assert data["preset_code"] == "US"

    def test_from_json_valid(self):
        data = '{"pid": 1234, "preset_code": "US", "timestamp": "2026-03-27"}'
        state = ProxyState.from_json(data)
        assert state.pid == 1234
        assert state.preset_code == "US"

    def test_from_json_rejects_unknown_fields(self):
        data = '{"pid": 1234, "preset_code": "US", "timestamp": "2026-03-27", "evil_field": "hack"}'
        with pytest.raises(ValueError, match="Unknown fields"):
            ProxyState.from_json(data)

    def test_from_json_invalid_json(self):
        with pytest.raises(json.JSONDecodeError):
            ProxyState.from_json("not json")

    def test_defaults(self):
        state = ProxyState(pid=1, preset_code="DE", timestamp="now")
        assert state.original_proxy_enable is None
        assert state.firewall_rules_created is False
        assert state.firefox_prefs_modified is False


class TestStateFile:
    def test_save_and_load(self, tmp_path, monkeypatch):
        state_file = tmp_path / "state.json"
        monkeypatch.setattr("src.system_config.STATE_FILE", state_file)

        state = ProxyState(pid=999, preset_code="GB", timestamp="2026-03-27")
        save_state(state)

        loaded = load_state()
        assert loaded is not None
        assert loaded.pid == 999
        assert loaded.preset_code == "GB"

    def test_load_missing_file(self, tmp_path, monkeypatch):
        monkeypatch.setattr("src.system_config.STATE_FILE", tmp_path / "nonexistent.json")
        assert load_state() is None

    def test_load_corrupt_file(self, tmp_path, monkeypatch):
        state_file = tmp_path / "state.json"
        state_file.write_text("corrupt data")
        monkeypatch.setattr("src.system_config.STATE_FILE", state_file)
        assert load_state() is None

    def test_delete_state(self, tmp_path, monkeypatch):
        state_file = tmp_path / "state.json"
        state_file.write_text("{}")
        monkeypatch.setattr("src.system_config.STATE_FILE", state_file)
        delete_state()
        assert not state_file.exists()

    def test_atomic_write(self, tmp_path, monkeypatch):
        state_file = tmp_path / "state.json"
        monkeypatch.setattr("src.system_config.STATE_FILE", state_file)

        state = ProxyState(pid=1, preset_code="US", timestamp="now")
        save_state(state)

        # Temp file should not exist after atomic rename
        assert not (state_file.with_suffix(".tmp")).exists()
        assert state_file.exists()
