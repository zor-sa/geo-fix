"""Tests for mitmproxy addon."""

from unittest.mock import MagicMock, PropertyMock

import pytest

from src.presets import PRESETS, CountryPreset
from src.proxy_addon import GeoFixAddon, FlowCleanup, _find_inject_position, _build_js_payload, _generate_nonce, _modify_csp


class TestFindInjectPosition:
    def test_find_simple_head(self):
        html = "<html><head><title>Test</title></head></html>"
        pos = _find_inject_position(html)
        assert pos == len("<html><head>")

    def test_find_head_with_attributes(self):
        html = '<html><head lang="en"><title>Test</title></head></html>'
        pos = _find_inject_position(html)
        assert html[pos - 1] == ">"

    def test_no_head_falls_back_to_html(self):
        html = "<html><body>No head</body></html>"
        pos = _find_inject_position(html)
        assert pos == len("<html>")

    def test_no_head_no_html_falls_back_to_doctype(self):
        html = "<!DOCTYPE html><body>No head</body>"
        pos = _find_inject_position(html)
        assert pos == len("<!DOCTYPE html>")

    def test_uppercase_head(self):
        html = "<HTML><HEAD><TITLE>Test</TITLE></HEAD></HTML>"
        pos = _find_inject_position(html)
        assert pos > 0


class TestBuildJsPayload:
    def test_replaces_timezone(self):
        preset = PRESETS["US"]
        js = _build_js_payload(preset)
        assert "America/New_York" in js
        assert "__GF_TIMEZONE__" not in js

    def test_replaces_coordinates(self):
        preset = PRESETS["DE"]
        js = _build_js_payload(preset)
        assert "52.52" in js
        assert "13.405" in js

    def test_replaces_language(self):
        preset = PRESETS["GB"]
        js = _build_js_payload(preset)
        assert "en-GB" in js

    def test_all_placeholders_replaced(self):
        preset = PRESETS["NL"]
        js = _build_js_payload(preset)
        assert "__GF_" not in js


class TestGenerateNonce:
    def test_nonce_is_string(self):
        nonce = _generate_nonce()
        assert isinstance(nonce, str)
        assert len(nonce) > 10

    def test_nonces_are_unique(self):
        nonces = {_generate_nonce() for _ in range(100)}
        assert len(nonces) == 100


class TestModifyCSP:
    def test_adds_nonce_to_existing_script_src(self):
        csp = "default-src 'self'; script-src 'self' 'unsafe-inline'"
        result = _modify_csp(csp, "abc123")
        assert "'nonce-abc123'" in result
        assert "script-src 'self' 'unsafe-inline' 'nonce-abc123'" in result

    def test_preserves_other_directives(self):
        csp = "default-src 'self'; script-src 'self'; frame-ancestors 'none'; form-action 'self'"
        result = _modify_csp(csp, "nonce1")
        assert "frame-ancestors 'none'" in result
        assert "form-action 'self'" in result

    def test_creates_script_src_from_default_src(self):
        csp = "default-src 'self' https:"
        result = _modify_csp(csp, "nonce1")
        assert "script-src 'self' https: 'nonce-nonce1'" in result

    def test_no_csp_directives(self):
        csp = ""
        result = _modify_csp(csp, "nonce1")
        # Empty CSP — function adds permissive script-src with nonce
        assert isinstance(result, str)


class TestGeoFixAddon:
    @pytest.fixture
    def addon(self):
        return GeoFixAddon(PRESETS["US"])

    @pytest.fixture
    def make_flow(self):
        """Create a realistic flow object for testing."""
        class FakeHeaders(dict):
            """Dict-like headers that supports case-insensitive 'in' check."""
            def __contains__(self, key):
                return super().__contains__(key.lower()) or super().__contains__(key)
            def get(self, key, default=None):
                return super().get(key, super().get(key.lower(), default))
            def __getitem__(self, key):
                try:
                    return super().__getitem__(key)
                except KeyError:
                    return super().__getitem__(key.lower())

        class FakeResponse:
            def __init__(self, status_code, headers, body):
                self.status_code = status_code
                self.headers = headers
                self._text = body
                self.content = body.encode("utf-8") if body else b""

            @property
            def text(self):
                return self._text

            @text.setter
            def text(self, value):
                self._text = value
                self._text_was_set = True

        class FakeRequest:
            def __init__(self, host, path, headers):
                self.host = host
                self.url = f"https://{host}{path}"
                self.headers = headers

        class FakeFlow:
            def __init__(self, request, response):
                self.request = request
                self.response = response

        def _make(host="www.google.com", path="/", content_type="text/html; charset=utf-8",
                  body="<html><head><title>Test</title></head><body>Hi</body></html>",
                  status_code=200):
            req_headers = FakeHeaders({"Accept-Language": "ru-RU,ru;q=0.9"})
            resp_headers = FakeHeaders({"content-type": content_type})
            request = FakeRequest(host, path, req_headers)
            response = FakeResponse(status_code, resp_headers, body)
            return FakeFlow(request, response)
        return _make

    def test_request_rewrites_accept_language(self, addon, make_flow):
        flow = make_flow()
        addon.request(flow)
        assert flow.request.headers["Accept-Language"] == "en-US,en;q=0.9"

    def test_request_skips_non_target_domain(self, addon, make_flow):
        flow = make_flow(host="example.com")
        flow.request.headers["Accept-Language"] = "ru-RU"
        addon.request(flow)
        assert flow.request.headers["Accept-Language"] == "ru-RU"

    def test_response_injects_js_for_target_domain(self, addon, make_flow):
        flow = make_flow()
        addon.response(flow)
        assert "<script nonce=" in flow.response.text
        assert "getTimezoneOffset" in flow.response.text

    def test_response_skips_non_target_domain(self, addon, make_flow):
        flow = make_flow(host="example.com")
        original_text = flow.response.text
        addon.response(flow)
        assert flow.response.text == original_text

    def test_response_skips_non_html(self, addon, make_flow):
        flow = make_flow(content_type="application/json", body='{"key": "value"}')
        original_text = flow.response.text
        addon.response(flow)
        assert flow.response.text == original_text

    def test_response_skips_large_responses(self, addon, make_flow):
        body = "x" * (5 * 1024 * 1024 + 1)
        flow = make_flow(body=body)
        addon.response(flow)
        assert "<script" not in flow.response.text[:100]

    def test_response_skips_non_200(self, addon, make_flow):
        flow = make_flow(status_code=404)
        original_text = flow.response.text
        addon.response(flow)
        assert flow.response.text == original_text

    def test_switch_preset_thread_safe(self, addon):
        addon.switch_preset(PRESETS["DE"])
        assert addon.preset.code == "DE"

    def test_csp_modified_for_target(self, addon, make_flow):
        flow = make_flow()
        flow.response.headers["content-security-policy"] = "script-src 'self'"
        addon.response(flow)
        csp = flow.response.headers["content-security-policy"]
        assert "'nonce-" in csp

    def test_js_contains_correct_timezone(self, addon, make_flow):
        flow = make_flow()
        addon.response(flow)
        assert "America/New_York" in flow.response.text

    def test_switched_preset_reflected_in_injection(self, addon, make_flow):
        addon.switch_preset(PRESETS["DE"])
        flow = make_flow()
        addon.response(flow)
        assert "Europe/Berlin" in flow.response.text

    def test_accept_language_after_switch(self, addon, make_flow):
        addon.switch_preset(PRESETS["NL"])
        flow = make_flow()
        addon.request(flow)
        assert flow.request.headers["Accept-Language"] == "nl-NL,nl;q=0.9,en;q=0.8"


class TestFlowCleanup:
    @pytest.fixture
    def cleanup_addon(self):
        return FlowCleanup()

    def _make_flow(self, *, with_response=True, body=b"hello world"):
        """Create a minimal flow for FlowCleanup tests."""
        class FakeRequest:
            def __init__(self):
                self.content = b"request body data"

        class FakeResponse:
            def __init__(self, content):
                self.content = content

        class FakeFlow:
            def __init__(self, with_resp, body):
                self.request = FakeRequest()
                self.response = FakeResponse(body) if with_resp else None
                self.websocket = None

        return FakeFlow(with_response, body)

    def test_flowcleanup_response_clears_content(self, cleanup_addon):
        """response hook sets both flow.request.content and flow.response.content to empty."""
        flow = self._make_flow()
        assert flow.request.content == b"request body data"
        assert flow.response.content == b"hello world"

        cleanup_addon.response(flow)

        assert flow.request.content == b""
        assert flow.response.content == b""

    def test_flowcleanup_response_guards_none_response(self, cleanup_addon):
        """response hook handles flow.response being None."""
        flow = self._make_flow(with_response=False)
        cleanup_addon.response(flow)
        assert flow.request.content == b""

    def test_flowcleanup_error_clears_request_content(self, cleanup_addon):
        """error hook clears flow.request.content."""
        flow = self._make_flow()
        assert flow.request.content == b"request body data"

        cleanup_addon.error(flow)

        assert flow.request.content == b""

    def test_flowcleanup_websocket_message_trims_to_one(self, cleanup_addon):
        """websocket_message hook with 5 messages leaves exactly 1 (the last)."""
        flow = self._make_flow()

        class FakeWebSocket:
            def __init__(self):
                self.messages = [f"msg{i}" for i in range(5)]

        flow.websocket = FakeWebSocket()
        assert len(flow.websocket.messages) == 5

        cleanup_addon.websocket_message(flow)

        assert len(flow.websocket.messages) == 1
        assert flow.websocket.messages[0] == "msg4"

    def test_flowcleanup_websocket_message_guards_none(self, cleanup_addon):
        """websocket_message handles flow.websocket being None."""
        flow = self._make_flow()
        flow.websocket = None
        # Should not raise
        cleanup_addon.websocket_message(flow)

    def test_flowcleanup_websocket_message_empty_messages(self, cleanup_addon):
        """websocket_message handles empty messages list."""
        flow = self._make_flow()

        class FakeWebSocket:
            def __init__(self):
                self.messages = []

        flow.websocket = FakeWebSocket()
        cleanup_addon.websocket_message(flow)
        assert flow.websocket.messages == []

    def test_flowcleanup_websocket_end_removes_flow(self, cleanup_addon):
        """websocket_end clears request/response content to release memory."""
        flow = self._make_flow()

        class FakeWebSocket:
            def __init__(self):
                self.messages = ["msg1", "msg2"]

        flow.websocket = FakeWebSocket()

        cleanup_addon.websocket_end(flow)

        assert flow.request.content == b""
        assert flow.response.content == b""
        assert flow.websocket.messages == []
