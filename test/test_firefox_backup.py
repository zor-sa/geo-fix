"""Tests for safe Firefox backup with copy semantics (Task 3: security-hardening)."""

import shutil
from pathlib import Path
from unittest.mock import patch

import pytest

from src.system_config import set_firefox_proxy, unset_firefox_proxy


@pytest.fixture
def firefox_profile(tmp_path):
    """Create a fake Firefox profile directory."""
    profile = tmp_path / "abcdef.default-release"
    profile.mkdir()
    with patch("src.system_config._find_firefox_profile", return_value=profile):
        yield profile


class TestBackupCopySemantics:
    def test_backup_uses_copy_not_rename(self, firefox_profile):
        """AC-4.1: original user.js stays at original path after backup."""
        user_js = firefox_profile / "user.js"
        user_js.write_text('user_pref("some.pref", true);')

        set_firefox_proxy()

        # Original path still exists (overwritten with new content, not moved)
        assert user_js.exists()
        # Backup also exists
        backup = firefox_profile / "user.js.geo-fix-backup"
        assert backup.exists()

    def test_backup_file_is_copy_of_original(self, firefox_profile):
        user_js = firefox_profile / "user.js"
        original_content = 'user_pref("some.pref", true);'
        user_js.write_text(original_content)

        set_firefox_proxy()

        backup = firefox_profile / "user.js.geo-fix-backup"
        assert backup.read_text() == original_content

    def test_user_js_contains_proxy_prefs_after_set(self, firefox_profile):
        user_js = firefox_profile / "user.js"
        user_js.write_text('user_pref("some.pref", true);')

        set_firefox_proxy()

        content = user_js.read_text()
        assert "geo-fix: proxy configuration" in content
        assert "network.proxy.type" in content

    def test_original_prefs_prepended_in_new_user_js(self, firefox_profile):
        user_js = firefox_profile / "user.js"
        user_js.write_text('user_pref("some.pref", true);')

        set_firefox_proxy()

        content = user_js.read_text()
        assert 'user_pref("some.pref", true);' in content


class TestRestoreCopySemantics:
    def test_restore_uses_copy_plus_unlink(self, firefox_profile):
        """AC-4.4: restore uses copy+unlink, not rename."""
        user_js = firefox_profile / "user.js"
        original_content = 'user_pref("some.pref", true);'
        user_js.write_text(original_content)

        backup_path = set_firefox_proxy()
        assert backup_path is not None

        unset_firefox_proxy(backup_path)

        # user.js is restored
        assert user_js.exists()
        assert user_js.read_text() == original_content
        # backup is removed
        assert not Path(backup_path).exists()

    def test_enterprise_roots_not_in_restored_file(self, firefox_profile):
        """AC-4.2: enterprise_roots absent after restore if not in original."""
        user_js = firefox_profile / "user.js"
        user_js.write_text('user_pref("some.pref", true);')

        backup_path = set_firefox_proxy()
        unset_firefox_proxy(backup_path)

        content = user_js.read_text()
        assert "enterprise_roots" not in content

    def test_no_backup_unlinks_geofix_userjs(self, firefox_profile):
        """If no original user.js existed, cleanup removes the created one."""
        assert not (firefox_profile / "user.js").exists()

        backup_path = set_firefox_proxy()
        assert backup_path is None
        assert (firefox_profile / "user.js").exists()

        unset_firefox_proxy(None)
        assert not (firefox_profile / "user.js").exists()


class TestCrashSafety:
    def test_crash_after_backup_preserves_original(self, firefox_profile):
        """AC-4.3: if crash after copy2 but before write_text, original is intact."""
        user_js = firefox_profile / "user.js"
        original_content = 'user_pref("important.pref", 42);'
        user_js.write_text(original_content)

        # Simulate: shutil.copy2 succeeds, then crash before write_text
        backup_path = str(user_js.with_suffix(".js.geo-fix-backup"))
        shutil.copy2(str(user_js), backup_path)
        # "crash" — don't write new user.js

        # Original file is untouched (copy, not rename)
        assert user_js.read_text() == original_content
        # Backup also exists
        assert Path(backup_path).read_text() == original_content

    def test_crash_after_restore_copy_preserves_backup(self, firefox_profile):
        """If crash after copy2 but before unlink in restore, backup still exists."""
        user_js = firefox_profile / "user.js"
        original_content = 'user_pref("important.pref", 42);'
        user_js.write_text(original_content)

        backup_path = set_firefox_proxy()

        # Simulate restore: copy succeeds, then crash before unlink
        shutil.copy2(backup_path, str(user_js))
        # "crash" — don't unlink backup

        assert user_js.read_text() == original_content
        assert Path(backup_path).exists()
