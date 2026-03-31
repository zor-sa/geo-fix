"""mitmproxy addon for geo-signal spoofing.

Rewrites Accept-Language headers on all requests.
Injects full JS payload (timezone, language, geolocation, permissions, WebRTC)
into all HTML responses with CSP nonce.
"""

import json
import logging
import os
import random
import re
import secrets
import threading
import time
from pathlib import Path

from mitmproxy import http

from .presets import CountryPreset, is_target_domain

logger = logging.getLogger("geo-fix.proxy")

# Load JS payload template once at module level
_JS_TEMPLATE_PATH = Path(__file__).parent / "inject.js"
_JS_TEMPLATE = _JS_TEMPLATE_PATH.read_text(encoding="utf-8")

# Max response size for JS injection (5MB)
MAX_INJECT_SIZE = 5 * 1024 * 1024


def _find_inject_position(html_text: str) -> int:
    """Find the best position to inject a script tag.

    Priority: after <head>, after <html>, after <!DOCTYPE>, or 0.
    Uses re.search with IGNORECASE to avoid creating a full lowercase copy.
    """
    # Try <head> first
    m = re.search(r"<head[\s>]", html_text, re.IGNORECASE)
    if m is not None:
        close = html_text.find(">", m.start())
        if close != -1:
            return close + 1

    # Fallback: after <html>
    m = re.search(r"<html[\s>]", html_text, re.IGNORECASE)
    if m is not None:
        close = html_text.find(">", m.start())
        if close != -1:
            return close + 1

    # Fallback: after <!DOCTYPE>
    m = re.search(r"<!doctype[\s>]", html_text, re.IGNORECASE)
    if m is not None:
        close = html_text.find(">", m.start())
        if close != -1:
            return close + 1

    return 0


def _build_js_payload(preset: CountryPreset) -> str:
    """Build the JS payload with preset values substituted."""
    langs = ",".join([preset.language] + [
        l.split(";")[0].strip() for l in preset.accept_language.split(",")
        if l.split(";")[0].strip() and l.split(";")[0].strip() != preset.language
    ])

    js = _JS_TEMPLATE
    js = js.replace("'__GF_TIMEZONE__'", f"'{preset.timezone}'")
    js = js.replace("'__GF_LAT__'", f"'{preset.latitude}'")
    js = js.replace("'__GF_LON__'", f"'{preset.longitude}'")
    js = js.replace("'__GF_LANG__'", f"'{preset.language}'")
    js = js.replace("'__GF_LANGS__'", f"'{langs}'")
    return js



def _has_restrictive_csp(csp_value: str) -> bool:
    """Return True if CSP has script-src 'none' (sole source) or require-trusted-types-for 'script'.

    Parses directives by splitting on ';' to avoid false positives from
    substring matches that span multiple directives.
    """
    if not csp_value:
        return False
    for directive in csp_value.split(";"):
        tokens = directive.strip().lower().split()
        if not tokens:
            continue
        name = tokens[0]
        if name == "require-trusted-types-for" and "'script'" in tokens[1:]:
            return True
        if name == "script-src" and tokens[1:] == ["'none'"]:
            return True
    return False


def _generate_nonce() -> str:
    """Generate a cryptographically random nonce for CSP."""
    return secrets.token_urlsafe(16)


def _modify_csp(csp_value: str, nonce: str) -> str:
    """Add nonce to script-src directive in CSP header.

    If no script-src exists, adds one based on default-src.
    Preserves all other directives unchanged.
    """
    nonce_value = f"'nonce-{nonce}'"

    directives = [d.strip() for d in csp_value.split(";") if d.strip()]
    new_directives = []
    has_script_src = False

    for directive in directives:
        parts = directive.split()
        if not parts:
            continue
        name = parts[0].lower()

        if name == "script-src":
            has_script_src = True
            # Append nonce to existing script-src
            new_directives.append(f"{directive} {nonce_value}")
        else:
            new_directives.append(directive)

    if not has_script_src:
        # Check if default-src exists, create script-src from it
        # Filter out unsafe tokens that should not be promoted to script-src
        _unsafe_tokens = {"'unsafe-inline'", "'unsafe-eval'", "'unsafe-hashes'"}
        for directive in directives:
            parts = directive.split()
            if parts and parts[0].lower() == "default-src":
                filtered = [t for t in parts[1:] if t.lower() not in _unsafe_tokens]
                sources = " ".join(filtered)
                new_directives.append(f"script-src {sources} {nonce_value}".strip())
                has_script_src = True
                break

        if not has_script_src:
            # No script-src or default-src — add minimal nonce-only policy
            new_directives.append(f"script-src {nonce_value}")

    return "; ".join(new_directives)


def _inject_script(flow: http.HTTPFlow, html_text: str, inject_pos: int, payload: str) -> str:
    """Inject a script tag with a fresh nonce into the HTML and update CSP if present.

    Modifies flow.response.text in-place (via the mitmproxy setter).
    Also rewrites the content-security-policy header when present.
    """
    nonce = _generate_nonce()
    script_tag = f'\n<script nonce="{nonce}">{payload}</script>\n'
    modified_html = html_text[:inject_pos] + script_tag + html_text[inject_pos:]
    flow.response.text = modified_html
    if "content-security-policy" in flow.response.headers:
        original_csp = flow.response.headers["content-security-policy"]
        flow.response.headers["content-security-policy"] = _modify_csp(original_csp, nonce)
    return nonce


class GeoFixAddon:
    """mitmproxy addon that spoofs geo-signals via header rewriting and JS injection."""

    def __init__(self, preset: CountryPreset):
        self._lock = threading.Lock()
        self._preset = preset
        self._js_payload = _build_js_payload(preset)
        self._last_flow_time: float = 0.0

    @property
    def preset(self) -> CountryPreset:
        with self._lock:
            return self._preset

    def switch_preset(self, preset: CountryPreset) -> None:
        """Thread-safe country preset switch."""
        with self._lock:
            self._preset = preset
            self._js_payload = _build_js_payload(preset)
            logger.info("Switched to preset: %s", preset.code)

    def request(self, flow: http.HTTPFlow) -> None:
        """Rewrite Accept-Language header on ALL requests passing through proxy."""
        with self._lock:
            self._last_flow_time = time.monotonic()  # track ALL traffic for idle guard
            accept_lang = self._preset.accept_language

        # Geolocation API intercept
        if (flow.request.host == 'www.googleapis.com' and
                flow.request.path == '/geolocation/v1/geolocate' and
                flow.request.method == 'POST'):
            try:
                with self._lock:
                    lat = self._preset.latitude
                    lon = self._preset.longitude
                accuracy = random.randint(40, 80)
                body = json.dumps({"location": {"lat": lat, "lng": lon}, "accuracy": accuracy})
                flow.response = http.Response.make(200, body.encode(), {"Content-Type": "application/json"})
                flow.request.headers["Accept-Language"] = accept_lang
                logger.info("Intercepted geolocation API request → fake response (accuracy=%d)", accuracy)
                return
            except Exception as e:
                logger.error("Geolocation API intercept failed: %s", e)

        flow.request.headers["Accept-Language"] = accept_lang

    def response(self, flow: http.HTTPFlow) -> None:
        """Inject full JS payload into all HTML responses."""
        if flow.response is None:
            return

        # Common HTML gating
        content_type = flow.response.headers.get("content-type", "")
        if "text/html" not in content_type.lower():
            return

        if flow.response.content and len(flow.response.content) > MAX_INJECT_SIZE:
            return

        if flow.response.status_code != 200:
            return

        try:
            html_text = flow.response.text
        except (ValueError, UnicodeDecodeError):
            return

        if not html_text:
            return

        inject_pos = _find_inject_position(html_text)

        host = flow.request.host

        # Skip injection on restrictive CSP (script-src 'none' or require-trusted-types)
        csp = flow.response.headers.get("content-security-policy", "")
        if csp and _has_restrictive_csp(csp):
            logger.debug("Skipping injection on %s — restrictive CSP", host)
            return

        with self._lock:
            js_payload = self._js_payload
        nonce = _inject_script(flow, html_text, inject_pos, js_payload)
        logger.debug("Injected JS into %s (nonce: %s)", flow.request.url, nonce[:8])


class FlowCleanup:
    """Addon that clears flow content after processing to reduce memory/GC pressure.

    Must be registered last in the addon chain (after GeoFixAddon).
    """

    def response(self, flow: http.HTTPFlow) -> None:
        """Clear request/response bodies after processing."""
        flow.request.content = b""
        if flow.response is not None:
            flow.response.content = b""

    def error(self, flow: http.HTTPFlow) -> None:
        """Clear request body for errored flows."""
        flow.request.content = b""

    def websocket_message(self, flow: http.HTTPFlow) -> None:
        """Trim WebSocket message history to last 1 message."""
        if flow.websocket is None:
            return
        if flow.websocket.messages:
            flow.websocket.messages[:] = flow.websocket.messages[-1:]

    def websocket_end(self, flow: http.HTTPFlow) -> None:
        """Clear flow content when WebSocket connection ends."""
        flow.request.content = b""
        if flow.response is not None:
            flow.response.content = b""
        if flow.websocket is not None:
            flow.websocket.messages.clear()
