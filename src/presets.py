"""Country presets for geo-signal spoofing.

Each preset contains consistent geolocation data for a country:
timezone, coordinates, language, and HTTP Accept-Language header.
"""

from dataclasses import dataclass
from typing import Dict, List


@dataclass(frozen=True)
class CountryPreset:
    """Immutable country preset with all geo-signals."""
    code: str           # ISO 3166-1 alpha-2 (e.g., "US")
    name_ru: str        # Display name in Russian
    timezone: str       # IANA timezone (e.g., "America/New_York")
    latitude: float
    longitude: float
    language: str       # BCP47 language tag (e.g., "en-US")
    accept_language: str  # HTTP Accept-Language header value


PRESETS: Dict[str, CountryPreset] = {
    "US": CountryPreset(
        code="US",
        name_ru="США",
        timezone="America/New_York",
        latitude=38.8951,
        longitude=-77.0364,
        language="en-US",
        accept_language="en-US,en;q=0.9",
    ),
    "DE": CountryPreset(
        code="DE",
        name_ru="Германия",
        timezone="Europe/Berlin",
        latitude=52.5200,
        longitude=13.4050,
        language="de-DE",
        accept_language="de-DE,de;q=0.9,en;q=0.8",
    ),
    "NL": CountryPreset(
        code="NL",
        name_ru="Нидерланды",
        timezone="Europe/Amsterdam",
        latitude=52.3676,
        longitude=4.9041,
        language="nl-NL",
        accept_language="nl-NL,nl;q=0.9,en;q=0.8",
    ),
    "GB": CountryPreset(
        code="GB",
        name_ru="Великобритания",
        timezone="Europe/London",
        latitude=51.5074,
        longitude=-0.1278,
        language="en-GB",
        accept_language="en-GB,en;q=0.9",
    ),
}

# Domains where JS injection is applied (CSP nonce + script injection).
# Accept-Language header rewriting applies to ALL domains.
TARGET_DOMAINS: List[str] = [
    ".google.com",
    ".googleapis.com",
    ".gstatic.com",
    ".google.co.uk",
    ".google.de",
    ".google.nl",
]


def is_target_domain(host: str) -> bool:
    """Check if a hostname matches any target domain pattern."""
    host = host.lower()
    for domain in TARGET_DOMAINS:
        if host.endswith(domain) or host == domain.lstrip("."):
            return True
    return False


def get_preset(code: str) -> CountryPreset:
    """Get a preset by country code. Raises KeyError if not found."""
    code = code.upper().strip()
    if code not in PRESETS:
        valid = ", ".join(sorted(PRESETS.keys()))
        raise KeyError(f"Unknown country code '{code}'. Valid codes: {valid}")
    return PRESETS[code]
