"""Tests for install.bat hash verification (Task 9: security-hardening)."""

from pathlib import Path

import pytest

INSTALL_BAT = Path(__file__).parent.parent.parent / "install.bat"


class TestInstallBatParsing:
    @pytest.fixture(autouse=True)
    def _load(self):
        self.content = INSTALL_BAT.read_text(encoding="utf-8")

    def test_python_zip_hash_variable_present(self):
        assert "PYTHON_ZIP_HASH=" in self.content

    def test_pip_hash_variable_present(self):
        assert "PIP_HASH=" in self.content

    def test_pip_url_is_versioned(self):
        """PIP_URL should NOT be the rolling bootstrap URL."""
        assert "bootstrap.pypa.io/get-pip.py" not in self.content.replace(
            "bootstrap.pypa.io/pip/", "VERSIONED"
        )
        assert "bootstrap.pypa.io/pip/" in self.content

    def test_hash_verification_url_comment_present(self):
        assert "python.org/downloads/release" in self.content

    def test_python_hash_verification_block(self):
        assert "Get-FileHash" in self.content
        assert "PYTHON_ZIP_HASH" in self.content

    def test_pip_hash_verification_block(self):
        assert "PIP_HASH" in self.content

    def test_hash_mismatch_exits_nonzero(self):
        assert "exit /b 2" in self.content

    def test_hash_mismatch_deletes_file(self):
        # After Python hash fail
        assert 'del "%INSTALL_DIR%\\python-embed.zip"' in self.content
        # After pip hash fail
        assert 'del "%INSTALL_DIR%\\get-pip.py"' in self.content

    def test_launcher_readonly(self):
        assert 'attrib +R' in self.content
        assert 'geo-fix.bat' in self.content
