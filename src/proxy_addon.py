"""mitmproxy addon for geo-signal spoofing.

Rewrites Accept-Language headers on all requests.
For target domains: injects JS payload with CSP nonce into HTML responses.
"""

import logging
import os
import secrets
import threading
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
    """
    lower = html_text.lower()

    # Try <head> first
    idx = lower.find("<head")
    if idx != -1:
        close = html_text.find(">", idx)
        if close != -1:
            return close + 1

    # Fallback: after <html>
    idx = lower.find("<html")
    if idx != -1:
        close = html_text.find(">", idx)
        if close != -1:
            return close + 1

    # Fallback: after <!DOCTYPE>
    idx = lower.find("<!doctype")
    if idx != -1:
        close = html_text.find(">", idx)
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


class GeoFixAddon:
    """mitmproxy addon that spoofs geo-signals via header rewriting and JS injection."""

    def __init__(self, preset: CountryPreset):
        self._lock = threading.Lock()
        self._preset = preset
        self._js_payload = _build_js_payload(preset)

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
        """Rewrite Accept-Language header on target domain requests only."""
        if not is_target_domain(flow.request.host):
            return
        with self._lock:
            accept_lang = self._preset.accept_language
        flow.request.headers["Accept-Language"] = accept_lang

    def response(self, flow: http.HTTPFlow) -> None:
        """For target domains: inject JS payload with CSP nonce into HTML responses."""
        if flow.response is None:
            return

        # Check if target domain
        host = flow.request.host
        if not is_target_domain(host):
            return

        # Only inject into HTML responses
        content_type = flow.response.headers.get("content-type", "")
        if "text/html" not in content_type.lower():
            return

        # Skip large responses
        if flow.response.content and len(flow.response.content) > MAX_INJECT_SIZE:
            return

        # Skip non-200 responses
        if flow.response.status_code != 200:
            return

        try:
            # mitmproxy auto-decodes gzip/brotli when accessing .text
            html_text = flow.response.text
        except (ValueError, UnicodeDecodeError):
            return

        if not html_text:
            return

        # Find injection point
        inject_pos = _find_inject_position(html_text)

        # Generate nonce and build script tag
        nonce = _generate_nonce()
        with self._lock:
            js_payload = self._js_payload

        script_tag = f'\n<script nonce="{nonce}">{js_payload}</script>\n'

        # Inject JS
        modified_html = html_text[:inject_pos] + script_tag + html_text[inject_pos:]
        flow.response.text = modified_html

        # Modify enforcing CSP header only (not report-only)
        if "content-security-policy" in flow.response.headers:
            original_csp = flow.response.headers["content-security-policy"]
            flow.response.headers["content-security-policy"] = _modify_csp(original_csp, nonce)

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
