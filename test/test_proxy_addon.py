"""Tests for mitmproxy addon."""

from unittest.mock import MagicMock, PropertyMock

import pytest

from src.presets import PRESETS, CountryPreset
from src.proxy_addon import GeoFixAddon, FlowCleanup, _find_inject_position, _build_js_payload, _build_geo_only_payload, _generate_nonce, _modify_csp, _has_restrictive_csp


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
            def __init__(self, host, path, headers, method='GET'):
                self.host = host
                self.path = path
                self.url = f"https://{host}{path}"
                self.headers = headers
                self.method = method

        class FakeFlow:
            def __init__(self, request, response):
                self.request = request
                self.response = response

        def _make(host="www.google.com", path="/", content_type="text/html; charset=utf-8",
                  body="<html><head><title>Test</title></head><body>Hi</body></html>",
                  status_code=200, method='GET'):
            req_headers = FakeHeaders({"Accept-Language": "ru-RU,ru;q=0.9"})
            resp_headers = FakeHeaders({"content-type": content_type})
            request = FakeRequest(host, path, req_headers, method=method)
            response = FakeResponse(status_code, resp_headers, body)
            return FakeFlow(request, response)
        return _make

    def test_request_rewrites_accept_language(self, addon, make_flow):
        flow = make_flow()
        addon.request(flow)
        assert flow.request.headers["Accept-Language"] == "en-US,en;q=0.9"

    def test_request_rewrites_accept_language_non_target_domain(self, addon, make_flow):
        flow = make_flow(host="example.com")
        flow.request.headers["Accept-Language"] = "ru-RU"
        addon.request(flow)
        assert flow.request.headers["Accept-Language"] == "en-US,en;q=0.9"

    def test_response_injects_js_for_target_domain(self, addon, make_flow):
        flow = make_flow()
        addon.response(flow)
        assert "<script nonce=" in flow.response.text
        assert "getTimezoneOffset" in flow.response.text

    def test_response_skips_non_target_domain(self, addon, make_flow):
        """Non-target domains now get geo-only injection (has getCurrentPosition, no getTimezoneOffset)."""
        flow = make_flow(host="example.com")
        addon.response(flow)
        assert "getCurrentPosition" in flow.response.text
        assert "getTimezoneOffset" not in flow.response.text

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

    # --- Geolocation API intercept tests ---

    def test_request_intercepts_geolocation_api(self, addon, make_flow):
        """POST to googleapis.com/geolocation → response set with lat/lng matching preset."""
        flow = make_flow(host="www.googleapis.com", path="/geolocation/v1/geolocate", method="POST")
        flow.response = None
        addon.request(flow)
        assert flow.response is not None
        import json
        body = json.loads(flow.response.text)
        assert body["location"]["lat"] == addon.preset.latitude
        assert body["location"]["lng"] == addon.preset.longitude

    def test_request_intercept_accuracy_randomized(self, addon, make_flow):
        """20 runs: accuracy always 40-80, at least 2 distinct values."""
        import json
        accuracies = set()
        for _ in range(20):
            flow = make_flow(host="www.googleapis.com", path="/geolocation/v1/geolocate", method="POST")
            flow.response = None
            addon.request(flow)
            body = json.loads(flow.response.text)
            acc = body["accuracy"]
            assert 40 <= acc <= 80, f"accuracy out of range: {acc}"
            accuracies.add(acc)
        assert len(accuracies) >= 2, "accuracy should vary across runs"

    def test_request_intercept_wrong_host_skipped(self, addon, make_flow):
        """maps.googleapis.com → no intercept (response stays None)."""
        flow = make_flow(host="maps.googleapis.com", path="/geolocation/v1/geolocate", method="POST")
        flow.response = None
        addon.request(flow)
        assert flow.response is None

    def test_request_intercept_wrong_path_skipped(self, addon, make_flow):
        """Wrong path → no intercept."""
        flow = make_flow(host="www.googleapis.com", path="/maps/v1/geocode", method="POST")
        flow.response = None
        addon.request(flow)
        assert flow.response is None

    def test_request_intercept_wrong_method_skipped(self, addon, make_flow):
        """GET → no intercept."""
        flow = make_flow(host="www.googleapis.com", path="/geolocation/v1/geolocate", method="GET")
        flow.response = None
        addon.request(flow)
        assert flow.response is None

    def test_request_intercept_error_logs_and_passes_through(self, addon, make_flow):
        """mock random.randint to raise → logged, passed through (response stays None)."""
        from unittest.mock import patch
        flow = make_flow(host="www.googleapis.com", path="/geolocation/v1/geolocate", method="POST")
        flow.response = None
        with patch("src.proxy_addon.random.randint", side_effect=RuntimeError("boom")), \
             patch("src.proxy_addon.logger") as mock_logger:
            addon.request(flow)
            mock_logger.error.assert_called_once()
        assert flow.response is None

    # --- Response injection tests ---

    def test_response_injects_geo_only_on_non_target_domain(self, addon, make_flow):
        """Non-target HTML → has getCurrentPosition, no getTimezoneOffset."""
        flow = make_flow(host="example.com")
        addon.response(flow)
        assert "getCurrentPosition" in flow.response.text
        assert "getTimezoneOffset" not in flow.response.text

    def test_response_injects_full_payload_on_target_domain(self, addon, make_flow):
        """Target domain → still has getTimezoneOffset (regression check)."""
        flow = make_flow(host="www.google.com")
        addon.response(flow)
        assert "getTimezoneOffset" in flow.response.text

    def test_response_skips_injection_on_script_src_none_csp(self, addon, make_flow):
        """script-src 'none' CSP on non-target → skip injection entirely."""
        flow = make_flow(host="example.com")
        flow.response.headers["content-security-policy"] = "script-src 'none'"
        original_text = flow.response.text
        addon.response(flow)
        assert flow.response.text == original_text

    def test_response_skips_injection_on_require_trusted_types_csp(self, addon, make_flow):
        """require-trusted-types-for 'script' CSP on non-target → skip injection."""
        flow = make_flow(host="example.com")
        flow.response.headers["content-security-policy"] = "require-trusted-types-for 'script'"
        original_text = flow.response.text
        addon.response(flow)
        assert flow.response.text == original_text

    def test_response_injects_on_normal_csp_non_target(self, addon, make_flow):
        """Normal CSP on non-target → geo-only injection proceeds."""
        flow = make_flow(host="example.com")
        flow.response.headers["content-security-policy"] = "default-src 'self'"
        addon.response(flow)
        assert "getCurrentPosition" in flow.response.text


class TestHasRestrictiveCSP:
    """Unit tests for _has_restrictive_csp helper."""

    def test_has_restrictive_csp_detects_script_src_none(self):
        assert _has_restrictive_csp("script-src 'none'") is True

    def test_has_restrictive_csp_detects_require_trusted_types(self):
        assert _has_restrictive_csp("require-trusted-types-for 'script'") is True

    def test_has_restrictive_csp_returns_false_for_normal(self):
        assert _has_restrictive_csp("default-src 'self'; script-src 'self' 'unsafe-inline'") is False

    def test_has_restrictive_csp_case_insensitive_script_src_none(self):
        assert _has_restrictive_csp("Script-Src 'None'") is True

    def test_has_restrictive_csp_script_src_none_mixed_with_others(self):
        """script-src 'none' 'self' is NOT considered restrictive (mixed with other sources)."""
        assert _has_restrictive_csp("script-src 'none' 'self'") is False

    def test_has_restrictive_csp_empty_string(self):
        assert _has_restrictive_csp("") is False


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

    def test_flowcleanup_websocket_end_clears_content(self, cleanup_addon):
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

    def test_flowcleanup_ordering_after_geofixaddon(self):
        """Verify FlowCleanup is added after GeoFixAddon in the addon chain in main.py."""
        import inspect
        import src.main as main_module
        source = inspect.getsource(main_module._start_mitmproxy)
        geo_pos = source.find("GeoFixAddon")
        cleanup_pos = source.find("FlowCleanup")
        assert geo_pos != -1, "GeoFixAddon not found in _start_mitmproxy"
        assert cleanup_pos != -1, "FlowCleanup not found in _start_mitmproxy"
        assert geo_pos < cleanup_pos, "FlowCleanup must be added after GeoFixAddon"


class TestFindInjectPositionCPU:
    """TDD anchor tests for CPU optimization of _find_inject_position (task 4)."""

    def test_mixed_case_head(self):
        """<Head> (mixed case) returns position right after closing >."""
        html = "<html><Head><title>Test</title></Head></html>"
        pos = _find_inject_position(html)
        assert pos == len("<html><Head>")
        assert html[pos - 1] == ">"

    def test_uppercase_doctype(self):
        """<!DOCTYPE html> uppercased returns position after > of doctype."""
        html = "<!DOCTYPE html><body>content</body>"
        pos = _find_inject_position(html)
        assert pos == len("<!DOCTYPE html>")

    def test_head_with_lang_attr(self):
        """<head lang="en"> returns position after the closing >."""
        html = '<html><head lang="en"><title>Test</title></head></html>'
        pos = _find_inject_position(html)
        assert html[pos - 1] == ">"
        assert pos == len('<html><head lang="en">')


class TestAcceptLanguageAllDomains:
    """Accept-Language must be rewritten for ALL domains, not just targets."""

    @pytest.fixture
    def addon(self):
        return GeoFixAddon(PRESETS["US"])

    @pytest.fixture
    def make_flow(self):
        class FakeHeaders(dict):
            def __contains__(self, key):
                return super().__contains__(key.lower()) or super().__contains__(key)
            def get(self, key, default=None):
                return super().get(key, super().get(key.lower(), default))
            def __getitem__(self, key):
                try:
                    return super().__getitem__(key)
                except KeyError:
                    return super().__getitem__(key.lower())

        class FakeRequest:
            def __init__(self, host, headers):
                self.host = host
                self.url = f"https://{host}/"
                self.headers = headers

        class FakeFlow:
            def __init__(self, request):
                self.request = request
                self.response = None

        def _make(host="example.com"):
            req_headers = FakeHeaders({"Accept-Language": "ru-RU,ru;q=0.9"})
            request = FakeRequest(host, req_headers)
            return FakeFlow(request)
        return _make

    def test_non_target_domain_gets_accept_language_rewritten(self, addon, make_flow):
        """Non-target domain (example.com) must still get Accept-Language rewritten."""
        flow = make_flow(host="example.com")
        addon.request(flow)
        assert flow.request.headers["Accept-Language"] == "en-US,en;q=0.9"

    def test_random_domain_gets_accept_language_rewritten(self, addon, make_flow):
        """Completely random domain must get Accept-Language rewritten."""
        flow = make_flow(host="some-random-site.org")
        addon.request(flow)
        assert flow.request.headers["Accept-Language"] == "en-US,en;q=0.9"

    def test_target_domain_still_gets_accept_language_rewritten(self, addon, make_flow):
        """Target domain (google.com) also gets Accept-Language rewritten."""
        flow = make_flow(host="www.google.com")
        addon.request(flow)
        assert flow.request.headers["Accept-Language"] == "en-US,en;q=0.9"


class TestGeoOnlyPayloadContent:
    """Verify geo-only payload contains geolocation + permissions overrides, not other overrides."""

    def test_geo_only_payload_contains_geolocation_override(self):
        preset = PRESETS["US"]
        payload = _build_geo_only_payload(preset)
        assert "getCurrentPosition" in payload
        assert "watchPosition" in payload
        assert "clearWatch" in payload

    def test_geo_only_payload_contains_permissions_query_override(self):
        preset = PRESETS["US"]
        payload = _build_geo_only_payload(preset)
        assert "permissions" in payload
        assert "geolocation" in payload
        assert "granted" in payload

    def test_geo_only_payload_scopes_permissions_to_geolocation(self):
        """permissions.query override must check name === 'geolocation' before returning granted."""
        preset = PRESETS["US"]
        payload = _build_geo_only_payload(preset)
        # The guard must exist: only geolocation gets the fake response
        assert "name" in payload
        assert "'geolocation'" in payload or '"geolocation"' in payload

    def test_geo_only_payload_excludes_timezone(self):
        preset = PRESETS["US"]
        payload = _build_geo_only_payload(preset)
        assert "getTimezoneOffset" not in payload
        assert "DateTimeFormat" not in payload

    def test_geo_only_payload_excludes_language(self):
        preset = PRESETS["US"]
        payload = _build_geo_only_payload(preset)
        assert "navigator.language" not in payload
        assert "GF_LANG" not in payload

    def test_geo_only_payload_excludes_webrtc(self):
        preset = PRESETS["US"]
        payload = _build_geo_only_payload(preset)
        assert "RTCPeerConnection" not in payload

    def test_geo_only_payload_contains_preset_coordinates(self):
        preset = PRESETS["US"]
        payload = _build_geo_only_payload(preset)
        assert str(preset.latitude) in payload
        assert str(preset.longitude) in payload
