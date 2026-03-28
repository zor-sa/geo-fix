"""Tests for CSP hardening (Task 8: security-hardening)."""

from unittest.mock import MagicMock

import pytest

from src.proxy_addon import _modify_csp


class TestModifyCSP:
    def test_nonce_appended_to_existing_script_src(self):
        csp = "script-src 'self' https://cdn.example.com"
        result = _modify_csp(csp, "test-nonce")
        assert "'nonce-test-nonce'" in result
        assert "'self'" in result
        assert "https://cdn.example.com" in result

    def test_unsafe_inline_not_promoted_from_default_src(self):
        """AC-7.1: unsafe-inline in default-src NOT copied to derived script-src."""
        csp = "default-src 'self' 'unsafe-inline'"
        result = _modify_csp(csp, "nonce123")
        # script-src should not contain unsafe-inline
        parts = result.split(";")
        script_src = [p for p in parts if "script-src" in p][0]
        assert "'unsafe-inline'" not in script_src

    def test_unsafe_eval_not_promoted_from_default_src(self):
        csp = "default-src 'self' 'unsafe-eval'"
        result = _modify_csp(csp, "nonce123")
        parts = result.split(";")
        script_src = [p for p in parts if "script-src" in p][0]
        assert "'unsafe-eval'" not in script_src

    def test_unsafe_hashes_not_promoted_from_default_src(self):
        csp = "default-src 'self' 'unsafe-hashes'"
        result = _modify_csp(csp, "nonce123")
        parts = result.split(";")
        script_src = [p for p in parts if "script-src" in p][0]
        assert "'unsafe-hashes'" not in script_src

    def test_safe_default_src_tokens_preserved(self):
        csp = "default-src 'self' https: *.example.com"
        result = _modify_csp(csp, "nonce123")
        parts = result.split(";")
        script_src = [p for p in parts if "script-src" in p][0]
        assert "'self'" in script_src
        assert "https:" in script_src
        assert "*.example.com" in script_src

    def test_no_default_src_minimal_nonce_only(self):
        csp = "img-src 'self'; style-src 'self'"
        result = _modify_csp(csp, "nonce123")
        assert "script-src 'nonce-nonce123'" in result

    def test_other_directives_preserved(self):
        """AC-7.3: non-script directives unchanged."""
        csp = "img-src 'self'; style-src https:; frame-src 'none'; script-src 'self'"
        result = _modify_csp(csp, "nonce123")
        assert "img-src 'self'" in result
        assert "style-src https:" in result
        assert "frame-src 'none'" in result

    def test_existing_script_src_unsafe_inline_preserved(self):
        """If script-src already has unsafe-inline, don't remove it."""
        csp = "script-src 'self' 'unsafe-inline'"
        result = _modify_csp(csp, "nonce123")
        assert "'unsafe-inline'" in result
        assert "'nonce-nonce123'" in result

    def test_case_insensitive_unsafe_filtering(self):
        csp = "default-src 'self' 'UNSAFE-INLINE' 'Unsafe-Eval'"
        result = _modify_csp(csp, "nonce123")
        parts = result.split(";")
        script_src = [p for p in parts if "script-src" in p][0]
        assert "UNSAFE-INLINE" not in script_src.upper().replace("'NONCE-", "")
        assert "'self'" in script_src
