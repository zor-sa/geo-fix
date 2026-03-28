"""Tests for setup wizard fixes (Task 10: security-hardening)."""

import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from src.setup_wizard import _run_console_wizard, mark_setup_complete, SETUP_COMPLETE_FILE


class TestConsoleWizard:
    def test_prompts_firewall(self, monkeypatch, capsys, tmp_path):
        monkeypatch.setattr("src.setup_wizard.SETUP_COMPLETE_FILE", tmp_path / ".done")
        monkeypatch.setattr("builtins.input", lambda _: "n")
        _run_console_wizard()
        output = capsys.readouterr().out
        assert "файрвол" in output.lower() or "Файрвол" in output

    def test_installs_firewall_on_yes(self, monkeypatch, capsys, tmp_path):
        monkeypatch.setattr("src.setup_wizard.SETUP_COMPLETE_FILE", tmp_path / ".done")
        monkeypatch.setattr("builtins.input", lambda _: "y")
        with patch("src.setup_wizard.create_firewall_rules", return_value=True) as mock_fw:
            _run_console_wizard()
            mock_fw.assert_called_once()

    def test_skips_firewall_on_no(self, monkeypatch, capsys, tmp_path):
        monkeypatch.setattr("src.setup_wizard.SETUP_COMPLETE_FILE", tmp_path / ".done")
        monkeypatch.setattr("builtins.input", lambda _: "n")
        with patch("src.setup_wizard.create_firewall_rules") as mock_fw:
            _run_console_wizard()
            mock_fw.assert_not_called()

    def test_shows_dns_instructions(self, monkeypatch, capsys, tmp_path):
        monkeypatch.setattr("src.setup_wizard.SETUP_COMPLETE_FILE", tmp_path / ".done")
        monkeypatch.setattr("builtins.input", lambda _: "n")
        _run_console_wizard()
        output = capsys.readouterr().out
        assert "chrome://settings/security" in output
        assert "about:preferences#privacy" in output

    def test_all_three_steps_present(self, monkeypatch, capsys, tmp_path):
        monkeypatch.setattr("src.setup_wizard.SETUP_COMPLETE_FILE", tmp_path / ".done")
        prompts = []
        monkeypatch.setattr("builtins.input", lambda p: (prompts.append(p), "n")[1])
        _run_console_wizard()
        output = capsys.readouterr().out
        all_text = output + " ".join(prompts)
        assert "Шаг 1" in all_text
        assert "Шаг 2" in all_text
        assert "Шаг 3" in all_text


class TestWizardNoDirectCaInstall:
    def test_no_install_ca_cert_import(self):
        """setup_wizard.py should not import install_ca_cert."""
        import importlib
        source = Path(__file__).parent.parent.parent / "src" / "setup_wizard.py"
        content = source.read_text()
        assert "install_ca_cert" not in content

    def test_no_lambda_skip(self):
        """Skip button should not be a lambda with mark_setup_complete."""
        source = Path(__file__).parent.parent.parent / "src" / "setup_wizard.py"
        content = source.read_text()
        assert "lambda" not in content or "mark_setup_complete" not in content.split("lambda")[1].split("\n")[0] if "lambda" in content else True
