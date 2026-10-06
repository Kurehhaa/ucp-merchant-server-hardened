"""API security: admin access, webhook SSRF protection, CORS, error handling."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from tests.ap2_helpers import ADMIN_HEADERS, ADMIN_KEY, public_resolver
from ucp_merchant.api import create_app
from ucp_merchant.config import UCPMerchantSettings
from ucp_merchant.orders.webhooks import (
    WebhookManager,
    WebhookURLError,
    validate_webhook_url,
)

ADMIN_ROUTES = [
    ("GET", "/api/v1/payments/keys"),
    ("GET", "/api/v1/orders"),
    ("POST", "/api/v1/orders/ord_x/advance"),
    ("POST", "/api/v1/orders/ord_x/cancel"),
    ("POST", "/api/v1/orders/ord_x/simulate-fulfillment"),
    ("GET", "/api/v1/webhooks"),
    ("POST", "/api/v1/webhooks"),
]

PUBLIC_ROUTES = [
    ("GET", "/health"),
    ("GET", "/.well-known/ucp"),
    ("GET", "/api/v1/catalog/search"),
    ("GET", "/api/v1/catalog/categories"),
]


def _app(**overrides):
    overrides.setdefault("environment", "testing")
    application = create_app(UCPMerchantSettings(**overrides))
    application.state.webhook_manager._resolver = public_resolver
    return application


def _client(application, **kwargs):
    kwargs.setdefault("raise_app_exceptions", True)
    transport = ASGITransport(
        app=application, raise_app_exceptions=kwargs.pop("raise_app_exceptions")
    )
    return AsyncClient(transport=transport, base_url="http://test", **kwargs)


# ---------------------------------------------------------------------------
# Admin key
# ---------------------------------------------------------------------------


class TestAdminAccess:
    @pytest.mark.parametrize(("method", "path"), ADMIN_ROUTES)
    async def test_disabled_when_no_key_is_configured(self, method, path):
        async with _client(_app()) as c:
            resp = await c.request(method, path, json={"url": "https://a.example/h"})
        assert resp.status_code == 403
        assert "ADMIN_API_KEY" in resp.json()["detail"]

    @pytest.mark.parametrize(("method", "path"), ADMIN_ROUTES)
    async def test_disabled_even_if_a_key_is_sent(self, method, path):
        async with _client(_app(), headers=ADMIN_HEADERS) as c:
            resp = await c.request(method, path, json={"url": "https://a.example/h"})
        assert resp.status_code == 403

    @pytest.mark.parametrize(("method", "path"), ADMIN_ROUTES)
    async def test_missing_key_is_rejected(self, method, path):
        async with _client(_app(admin_api_key=ADMIN_KEY)) as c:
            resp = await c.request(method, path, json={"url": "https://a.example/h"})
        assert resp.status_code == 401
        assert resp.headers["www-authenticate"] == "ApiKey"

    @pytest.mark.parametrize("wrong", ["", "wrong", ADMIN_KEY + "x", ADMIN_KEY[:-1]])
    async def test_wrong_key_is_rejected(self, wrong):
        async with _client(
            _app(admin_api_key=ADMIN_KEY), headers={"X-API-Key": wrong}
        ) as c:
            resp = await c.get("/api/v1/orders")
        assert resp.status_code == 401

    async def test_correct_key_is_accepted(self):
        async with _client(_app(admin_api_key=ADMIN_KEY), headers=ADMIN_HEADERS) as c:
            resp = await c.get("/api/v1/orders")
        assert resp.status_code == 200

    @pytest.mark.parametrize(("method", "path"), PUBLIC_ROUTES)
    async def test_public_routes_need_no_key(self, method, path):
        async with _client(_app(admin_api_key=ADMIN_KEY)) as c:
            resp = await c.request(method, path)
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Webhook URL validation (SSRF)
# ---------------------------------------------------------------------------


def _resolving_to(address: str):
    def resolver(host: str, port: int):
        return [(2, 1, 6, "", (address, port))]

    return resolver


class TestWebhookUrlValidation:
    def test_public_https_url_is_accepted(self):
        validate_webhook_url("https://hooks.example.com/orders", resolver=public_resolver)

    @pytest.mark.parametrize(
        "address",
        [
            "127.0.0.1",  # loopback
            "10.0.0.5",  # private
            "172.16.3.4",
            "192.168.1.10",
            "169.254.169.254",  # cloud metadata / link-local
            "100.64.0.1",  # carrier-grade NAT
            "0.0.0.0",  # unspecified
            "224.0.0.1",  # multicast
            "::1",  # IPv6 loopback
            "fe80::1%eth0",  # IPv6 link-local with zone id
            "fd00::1",  # IPv6 unique local
            "::ffff:127.0.0.1",  # IPv4-mapped loopback
            "::ffff:169.254.169.254",
        ],
    )
    def test_non_public_addresses_are_rejected(self, address):
        with pytest.raises(WebhookURLError, match="non-public"):
            validate_webhook_url("https://evil.example.com/x", resolver=_resolving_to(address))

    def test_any_private_address_among_several_is_rejected(self):
        def resolver(host, port):
            return [(2, 1, 6, "", ("93.184.216.34", port)), (2, 1, 6, "", ("10.0.0.1", port))]

        with pytest.raises(WebhookURLError):
            validate_webhook_url("https://mixed.example.com/x", resolver=resolver)

    @pytest.mark.parametrize(
        "url",
        [
            "https://127.0.0.1/hook",
            "https://[::1]/hook",
            "https://169.254.169.254/latest/meta-data/",
            "https://10.1.2.3:8443/hook",
        ],
    )
    def test_literal_private_ips_are_rejected_without_dns(self, url):
        # Uses the real resolver: IP literals resolve locally, no network needed.
        with pytest.raises(WebhookURLError):
            validate_webhook_url(url)

    def test_localhost_name_is_rejected(self):
        with pytest.raises(WebhookURLError):
            validate_webhook_url("https://localhost/hook")

    @pytest.mark.parametrize(
        ("url", "message"),
        [
            ("http://hooks.example.com/x", "https"),
            ("ftp://hooks.example.com/x", "http or https"),
            ("file:///etc/passwd", "http or https"),
            ("javascript:alert(1)", "http or https"),
            ("https:///no-host", "host"),
            ("https://user:pass@hooks.example.com/x", "credentials"),
            ("https://hooks.example.com:notaport/x", "Invalid"),
        ],
    )
    def test_malformed_or_unsafe_urls_are_rejected(self, url, message):
        with pytest.raises(WebhookURLError, match=message):
            validate_webhook_url(url, resolver=public_resolver)

    def test_unresolvable_host_is_rejected(self):
        def resolver(host, port):
            raise OSError("no such host")

        with pytest.raises(WebhookURLError, match="could not be resolved"):
            validate_webhook_url("https://nope.example.com/x", resolver=resolver)

    def test_private_hosts_allowed_only_when_explicitly_enabled(self):
        validate_webhook_url("http://127.0.0.1:9000/hook", allow_private_hosts=True)
        with pytest.raises(WebhookURLError, match="http or https"):
            validate_webhook_url("ftp://127.0.0.1/x", allow_private_hosts=True)
        with pytest.raises(WebhookURLError, match="credentials"):
            validate_webhook_url("http://u:p@127.0.0.1/x", allow_private_hosts=True)

    async def test_register_endpoint_returns_400_for_internal_targets(self):
        async with _client(_app(admin_api_key=ADMIN_KEY), headers=ADMIN_HEADERS) as c:
            resp = await c.post(
                "/api/v1/webhooks", json={"url": "https://169.254.169.254/latest"}
            )
            assert resp.status_code == 400
            assert "non-public" in resp.json()["detail"]
            assert (await c.get("/api/v1/webhooks")).json()["total"] == 0

    async def test_register_endpoint_accepts_public_url(self):
        async with _client(_app(admin_api_key=ADMIN_KEY), headers=ADMIN_HEADERS) as c:
            resp = await c.post("/api/v1/webhooks", json={"url": "https://hooks.example.com/o"})
        assert resp.status_code == 200

    async def test_delivery_is_blocked_if_dns_changes_after_registration(self):
        """DNS rebinding: a host that was public at registration turns private."""
        manager = WebhookManager(resolver=public_resolver)
        manager.register_webhook("https://rebind.example.com/hook", ["*"])
        manager._resolver = _resolving_to("127.0.0.1")

        results = await manager.notify("ord_1", "order_confirmed", {})

        assert len(results) == 1
        assert results[0]["success"] is False
        assert results[0]["attempts"] == 0
        assert results[0]["error"].startswith("Blocked:")


# ---------------------------------------------------------------------------
# CORS
# ---------------------------------------------------------------------------


class TestCors:
    ORIGIN = "https://shop.example.com"

    async def test_no_origin_is_allowed_by_default(self):
        async with _client(_app()) as c:
            resp = await c.get("/health", headers={"Origin": self.ORIGIN})
        assert "access-control-allow-origin" not in resp.headers

    async def test_configured_origin_is_allowed_without_wildcard(self):
        async with _client(_app(cors_allow_origins=self.ORIGIN)) as c:
            resp = await c.get("/health", headers={"Origin": self.ORIGIN})
        assert resp.headers["access-control-allow-origin"] == self.ORIGIN
        assert resp.headers["access-control-allow-credentials"] == "true"

    async def test_other_origin_is_not_allowed(self):
        async with _client(_app(cors_allow_origins=self.ORIGIN)) as c:
            resp = await c.get("/health", headers={"Origin": "https://evil.example.com"})
        assert "access-control-allow-origin" not in resp.headers

    async def test_wildcard_never_comes_with_credentials(self):
        async with _client(_app(cors_allow_origins="*")) as c:
            resp = await c.get("/health", headers={"Origin": self.ORIGIN})
        assert resp.headers["access-control-allow-origin"] == "*"
        assert "access-control-allow-credentials" not in resp.headers

    def test_origin_list_parsing(self):
        settings = UCPMerchantSettings(cors_allow_origins=" https://a.example , ,https://b.example ")
        assert settings.cors_origins == ["https://a.example", "https://b.example"]
        assert UCPMerchantSettings(cors_allow_origins="").cors_origins == []


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


class TestErrorHandling:
    SECRET = "db password is hunter2"

    @staticmethod
    def _break_catalog(application, message):
        def boom(*args, **kwargs):
            raise RuntimeError(message)

        application.state.catalog.get = boom

    async def test_internal_error_details_are_not_leaked(self):
        application = _app()
        self._break_catalog(application, self.SECRET)
        async with _client(application, raise_app_exceptions=False) as c:
            resp = await c.get("/api/v1/catalog/products/laptop-001")
        assert resp.status_code == 500
        assert self.SECRET not in resp.text
        assert "reference:" in resp.json()["detail"]

    async def test_reference_ids_differ_between_errors(self):
        application = _app()
        self._break_catalog(application, self.SECRET)
        async with _client(application, raise_app_exceptions=False) as c:
            first = (await c.get("/api/v1/catalog/products/laptop-001")).json()["detail"]
            second = (await c.get("/api/v1/catalog/products/laptop-001")).json()["detail"]
        assert first != second

    async def test_details_can_be_exposed_for_local_debugging(self):
        application = _app(expose_error_details=True)
        self._break_catalog(application, self.SECRET)
        async with _client(application, raise_app_exceptions=False) as c:
            resp = await c.get("/api/v1/catalog/products/laptop-001")
        assert resp.status_code == 500
        assert self.SECRET in resp.json()["detail"]

    async def test_expected_errors_keep_their_messages(self):
        async with _client(_app()) as c:
            resp = await c.get("/api/v1/catalog/products/does-not-exist")
        assert resp.status_code == 404
        assert "does-not-exist" in resp.json()["detail"]
