"""Tests for country presets module."""

import re

import pytest

from src.presets import PRESETS, TARGET_DOMAINS, CountryPreset, get_preset, is_target_domain


class TestPresetData:
    """Verify all preset data is internally consistent."""

    VALID_IANA_ZONES = {
        "America/New_York", "Europe/Berlin", "Europe/Amsterdam", "Europe/London",
    }

    # Approximate bounding boxes for capital cities (lat, lon ± 2°)
    CITY_BOUNDS = {
        "US": (36.9, 40.9, -79.0, -75.0),  # Washington DC
        "DE": (50.5, 54.5, 11.4, 15.4),     # Berlin
        "NL": (50.4, 54.4, 2.9, 6.9),       # Amsterdam
        "GB": (49.5, 53.5, -2.1, 1.9),      # London
    }

    BCP47_PATTERN = re.compile(r"^[a-z]{2}-[A-Z]{2}$")

    def test_all_four_presets_exist(self):
        assert set(PRESETS.keys()) == {"US", "DE", "NL", "GB"}

    @pytest.mark.parametrize("code", ["US", "DE", "NL", "GB"])
    def test_preset_has_all_fields(self, code):
        p = PRESETS[code]
        assert isinstance(p, CountryPreset)
        assert p.code == code
        assert p.name_ru  # non-empty Russian name
        assert p.timezone in self.VALID_IANA_ZONES
        assert isinstance(p.latitude, float)
        assert isinstance(p.longitude, float)
        assert self.BCP47_PATTERN.match(p.language)
        assert p.accept_language  # non-empty

    @pytest.mark.parametrize("code", ["US", "DE", "NL", "GB"])
    def test_coordinates_within_country(self, code):
        p = PRESETS[code]
        lat_min, lat_max, lon_min, lon_max = self.CITY_BOUNDS[code]
        assert lat_min <= p.latitude <= lat_max, f"{code} lat {p.latitude} out of bounds"
        assert lon_min <= p.longitude <= lon_max, f"{code} lon {p.longitude} out of bounds"

    @pytest.mark.parametrize("code", ["US", "DE", "NL", "GB"])
    def test_accept_language_starts_with_language(self, code):
        p = PRESETS[code]
        assert p.accept_language.startswith(p.language)

    @pytest.mark.parametrize("code", ["US", "DE", "NL", "GB"])
    def test_preset_is_frozen(self, code):
        p = PRESETS[code]
        with pytest.raises(AttributeError):
            p.code = "XX"


class TestTargetDomains:
    def test_google_com_in_targets(self):
        assert ".google.com" in TARGET_DOMAINS

    def test_googleapis_in_targets(self):
        assert ".googleapis.com" in TARGET_DOMAINS

    def test_gstatic_in_targets(self):
        assert ".gstatic.com" in TARGET_DOMAINS


class TestIsTargetDomain:
    @pytest.mark.parametrize("host", [
        "www.google.com",
        "notebooklm.google.com",
        "gemini.google.com",
        "fonts.googleapis.com",
        "ssl.gstatic.com",
        "www.google.co.uk",
    ])
    def test_target_domains_match(self, host):
        assert is_target_domain(host) is True

    @pytest.mark.parametrize("host", [
        "example.com",
        "facebook.com",
        "google.evil.com",
        "notgoogle.com",
        "bing.com",
    ])
    def test_non_target_domains_dont_match(self, host):
        assert is_target_domain(host) is False

    def test_case_insensitive(self):
        assert is_target_domain("WWW.GOOGLE.COM") is True


class TestGetPreset:
    def test_valid_code(self):
        p = get_preset("US")
        assert p.code == "US"

    def test_lowercase_code(self):
        p = get_preset("us")
        assert p.code == "US"

    def test_invalid_code_raises(self):
        with pytest.raises(KeyError, match="Unknown country code"):
            get_preset("XX")

    def test_empty_code_raises(self):
        with pytest.raises(KeyError):
            get_preset("")
