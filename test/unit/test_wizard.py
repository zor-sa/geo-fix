"""Tests for setup wizard (simplified — no firewall step)."""

import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from src.setup_wizard import _run_console_wizard, mark_setup_complete, SETUP_COMPLETE_FILE


class TestConsoleWizard:
    def test_shows_cert_info(self, monkeypatch, capsys, tmp_path):
        monkeypatch.setattr("src.setup_wizard.SETUP_COMPLETE_FILE", tmp_path / ".done")
        _run_console_wizard()
        output = capsys.readouterr().out
        assert "сертификат" in output.lower()

    def test_shows_dns_instructions(self, monkeypatch, capsys, tmp_path):
        monkeypatch.setattr("src.setup_wizard.SETUP_COMPLETE_FILE", tmp_path / ".done")
        _run_console_wizard()
        output = capsys.readouterr().out
        assert "chrome://settings/security" in output
        assert "about:preferences#privacy" in output

    def test_both_steps_present(self, monkeypatch, capsys, tmp_path):
        monkeypatch.setattr("src.setup_wizard.SETUP_COMPLETE_FILE", tmp_path / ".done")
        _run_console_wizard()
        output = capsys.readouterr().out
        assert "Шаг 1" in output
        assert "Шаг 2" in output

    def test_no_firewall_step(self, monkeypatch, capsys, tmp_path):
        """Wizard should not mention firewall — WebRTC uses relay mode now."""
        monkeypatch.setattr("src.setup_wizard.SETUP_COMPLETE_FILE", tmp_path / ".done")
        _run_console_wizard()
        output = capsys.readouterr().out
        assert "файрвол" not in output.lower()


class TestWizardNoDirectCaInstall:
    def test_no_install_ca_cert_import(self):
        """setup_wizard.py should not import install_ca_cert."""
        source = Path(__file__).parent.parent.parent / "src" / "setup_wizard.py"
        content = source.read_text(encoding="utf-8")
        assert "install_ca_cert" not in content

    def test_no_lambda_skip(self):
        """Skip button should not be a lambda with mark_setup_complete."""
        source = Path(__file__).parent.parent.parent / "src" / "setup_wizard.py"
        content = source.read_text(encoding="utf-8")
        for line in content.split("\n"):
            if "lambda" in line and "mark_setup_complete" in line:
                pytest.fail(f"Found lambda with mark_setup_complete: {line.strip()}")
