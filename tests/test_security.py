import socket
import unittest
from unittest.mock import patch

import httpx

from app.security import (PublicNetworkTransport, SecurityError, redact, safe_request,
                          validate_base_url, validate_url)


def answer(ip="93.184.216.34"):
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 443))]


class URLSafetyTests(unittest.TestCase):
    def test_rejects_non_public_and_ambiguous_urls(self):
        bad = ["file:///etc/passwd", "ftp://example.org/a", "http://localhost/a", "http://test.local/a",
               "http://127.0.0.1", "http://10.0.0.1", "http://172.16.1.1", "http://192.168.1.1",
               "http://169.254.169.254/latest", "http://0.0.0.0", "http://[::1]", "http://[fe80::1]",
               "http://[::ffff:127.0.0.1]", "http://224.0.0.1", "https://user:pass@example.org",
               "https://example.org:8443/x", "https://example.org/a\nHeader:bad", "https://example.org\\@localhost/x",
               "https://example.org/#fragment", "http://host.internal"]
        for url in bad:
            with self.subTest(url=url), self.assertRaises(SecurityError):
                validate_url(url, resolve=False)

    def test_all_dns_answers_must_be_public(self):
        with patch("app.security.socket.getaddrinfo", return_value=answer() + answer("127.0.0.1")):
            with self.assertRaises(SecurityError):
                validate_url("https://example.org")

    def test_dns_failures_are_safe_errors(self):
        with patch("app.security.socket.getaddrinfo", side_effect=socket.gaierror("secret internal detail")):
            with self.assertRaises(SecurityError) as ctx:
                validate_url("https://example.org")
            self.assertNotIn("secret", str(ctx.exception))

    def test_pins_dns_address_and_preserves_tls_identity(self):
        seen = []
        inner = httpx.MockTransport(lambda request: (seen.append(request) or httpx.Response(200, json={"ok": True})))
        client = httpx.Client(transport=PublicNetworkTransport(inner))
        with patch("app.security.socket.getaddrinfo", return_value=answer()) as resolve:
            response = safe_request(client, "GET", "https://example.org/test")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(resolve.call_count, 1)
        self.assertEqual(seen[0].url.host, "93.184.216.34")
        self.assertEqual(seen[0].headers["Host"], "example.org")
        self.assertEqual(seen[0].extensions["sni_hostname"], "example.org")

    def test_tls_connection_pools_are_separated_by_original_origin(self):
        transport = PublicNetworkTransport()
        client = httpx.Client(transport=transport)
        def factory(**kwargs):
            return httpx.MockTransport(lambda req: httpx.Response(200, text="ok"))
        with patch("app.security.socket.getaddrinfo", return_value=answer()), patch("app.security.httpx.HTTPTransport", side_effect=factory) as create:
            safe_request(client, "GET", "https://first.org")
            safe_request(client, "GET", "https://second.org")
            safe_request(client, "GET", "https://first.org/again")
        self.assertEqual(create.call_count, 2)
        self.assertEqual(len(transport._transports), 2)

    def test_model_base_url_requires_public_https_without_query(self):
        for url in ("http://example.org/v1", "https://127.0.0.1/v1", "https://example.org/v1?key=x"):
            with self.subTest(url=url), self.assertRaises(SecurityError):
                validate_base_url(url, resolve=False)
        self.assertEqual(validate_base_url("https://example.org/v1/", resolve=False), "https://example.org/v1")

    def test_redirect_to_private_is_rejected_before_second_call(self):
        calls = []
        client = httpx.Client(transport=httpx.MockTransport(lambda req: (calls.append(req) or httpx.Response(302, headers={"Location": "http://127.0.0.1/secret"}))))
        with self.assertRaises(SecurityError):
            safe_request(client, "GET", "https://example.org")
        self.assertEqual(len(calls), 1)

    def test_redirect_hostname_is_dns_validated_by_transport(self):
        calls = []
        inner = httpx.MockTransport(lambda req: (calls.append(req) or httpx.Response(302, headers={"Location": "https://other.org/secret"})))
        client = httpx.Client(transport=PublicNetworkTransport(inner))
        with patch("app.security.socket.getaddrinfo", side_effect=[answer(), answer("10.0.0.1")]):
            with self.assertRaises(SecurityError):
                safe_request(client, "GET", "https://example.org")
        self.assertEqual(len(calls), 1)

    def test_cross_origin_auth_headers_not_forwarded(self):
        requests = []
        def handle(request):
            requests.append(request)
            return httpx.Response(302, headers={"location": "https://other.org/x"}) if len(requests) == 1 else httpx.Response(200, text="ok")
        client = httpx.Client(transport=httpx.MockTransport(handle))
        response = safe_request(client, "GET", "https://example.org/x", headers={"Authorization": "Bearer secret", "x-api-key": "secret", "Accept": "application/json"})
        self.assertEqual(response.text, "ok")
        self.assertNotIn("authorization", requests[1].headers)
        self.assertNotIn("x-api-key", requests[1].headers)
        self.assertEqual(requests[1].headers["accept"], "application/json")

    def test_post_redirects_are_not_followed(self):
        client = httpx.Client(transport=httpx.MockTransport(lambda req: httpx.Response(307, headers={"location": "https://other.org/x"})))
        with self.assertRaises(SecurityError):
            safe_request(client, "POST", "https://example.org/x", json={"secret": "value"})

    def test_size_cap_checks_declared_and_actual_length(self):
        for headers in ({"Content-Length": "1000"}, {}):
            client = httpx.Client(transport=httpx.MockTransport(lambda req: httpx.Response(200, text="x" * 20, headers=headers)))
            with self.subTest(headers=headers), self.assertRaises(SecurityError):
                safe_request(client, "GET", "https://example.org", max_bytes=10)

    def test_secrets_are_removed_recursively(self):
        value = {"api_key": "one", "nested": [{"Authorization": "two", "error": "leaked one and Bearer three"}]}
        cleaned = redact(value, ["one"])
        self.assertEqual(cleaned["api_key"], "[redacted]")
        self.assertEqual(cleaned["nested"][0]["Authorization"], "[redacted]")
        self.assertNotIn("one", str(cleaned))
        self.assertNotIn("three", str(cleaned))


if __name__ == "__main__":
    unittest.main()
