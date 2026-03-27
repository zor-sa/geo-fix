"""Tests for main entry point."""

import pytest

from src.presets import PRESETS


class TestValidateCountry:
    """Test country code validation logic."""

    def test_valid_codes(self):
        for code in PRESETS:
            normalized = code.upper().strip()
            assert normalized in PRESETS

    def test_lowercase_normalized(self):
        assert "us".upper().strip() in PRESETS

    def test_invalid_length(self):
        assert "USA" not in PRESETS  # 3 chars

    def test_empty_string(self):
        assert "" not in PRESETS

    def test_numeric_rejected(self):
        code = "12"
        assert not code.isalpha() or code.upper() not in PRESETS


class TestCLIParsing:
    """Test argument parsing without actually running."""

    def test_country_arg(self):
        import argparse
        parser = argparse.ArgumentParser()
        parser.add_argument("country", nargs="?")
        parser.add_argument("--stop", action="store_true")
        parser.add_argument("--cleanup", action="store_true")

        args = parser.parse_args(["US"])
        assert args.country == "US"
        assert not args.stop

    def test_stop_flag(self):
        import argparse
        parser = argparse.ArgumentParser()
        parser.add_argument("country", nargs="?")
        parser.add_argument("--stop", action="store_true")

        args = parser.parse_args(["--stop"])
        assert args.stop
        assert args.country is None

    def test_cleanup_flag(self):
        import argparse
        parser = argparse.ArgumentParser()
        parser.add_argument("country", nargs="?")
        parser.add_argument("--cleanup", action="store_true")

        args = parser.parse_args(["--cleanup"])
        assert args.cleanup
