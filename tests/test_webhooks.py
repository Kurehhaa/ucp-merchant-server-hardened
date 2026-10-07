"""Webhook registration and delivery (no real network: httpx.MockTransport)."""

from __future__ import annotations

import json

import httpx
import pytest

from tests.ap2_helpers import public_resolver
from ucp_merchant.orders.webhooks import WebhookManager

URL = "https://hooks.example.com/orders"


@pytest.fixture
def http(monkeypatch):
    """Route the manager's HTTP calls to a handler; records every request."""
    state = {"requests": [], "handler": lambda request: httpx.Response(200)}
    real_client = httpx.AsyncClient

    def handler(request: httpx.Request) -> httpx.Response:
        state["requests"].append(request)
        return state["handler"](request)

    def factory(**kwargs):
        return real_client(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr("ucp_merchant.orders.webhooks.httpx.AsyncClient", factory)
    return state


def _manager(**kwargs) -> WebhookManager:
    return WebhookManager(resolver=public_resolver, **kwargs)


class TestRegistration:
    def test_register_and_list(self):
        manager = _manager()
        registration = manager.register_webhook(URL, ["order_shipped"])
        assert registration.id.startswith("wh_")
        assert registration.active
        assert [w.id for w in manager.list_webhooks()] == [registration.id]

    def test_unsupported_event_is_rejected(self):
        with pytest.raises(ValueError, match="Unsupported event types"):
            _manager().register_webhook(URL, ["order_exploded"])

    def test_wildcard_subscribes_to_everything(self):
        assert _manager().register_webhook(URL, ["*"]).events == ["*"]

    def test_unregister(self):
        manager = _manager()
        registration = manager.register_webhook(URL, ["*"])
        assert manager.unregister_webhook(registration.id) is True
        assert manager.list_webhooks()[0].active is False
        assert manager.unregister_webhook("wh_unknown") is False


class TestDelivery:
    async def test_posts_signed_envelope_to_the_url(self, http):
        manager = _manager()
        registration = manager.register_webhook(URL, ["order_shipped"])

        results = await manager.notify("ord_1", "order_shipped", {"tracking": "T123"})

        assert results[0]["success"] is True
        assert results[0]["status_code"] == 200
        (request,) = http["requests"]
        assert str(request.url) == URL
        assert request.headers["X-Webhook-Id"] == registration.id
        assert request.headers["X-Event-Type"] == "order_shipped"
        body = json.loads(request.content)
        assert body["order_id"] == "ord_1"
        assert body["event_type"] == "order_shipped"
        assert body["payload"] == {"tracking": "T123"}

    async def test_only_matching_subscriptions_are_called(self, http):
        manager = _manager()
        manager.register_webhook("https://a.example.com/h", ["order_shipped"])
        manager.register_webhook("https://b.example.com/h", ["order_delivered"])
        manager.register_webhook("https://c.example.com/h", ["*"])

        await manager.notify("ord_1", "order_shipped", {})

        assert sorted(r.url.host for r in http["requests"]) == ["a.example.com", "c.example.com"]

    async def test_inactive_webhooks_are_skipped(self, http):
        manager = _manager()
        registration = manager.register_webhook(URL, ["*"])
        manager.unregister_webhook(registration.id)
        assert await manager.notify("ord_1", "order_shipped", {}) == []
        assert http["requests"] == []

    async def test_retries_until_success(self, http):
        calls = iter([500, 500, 200])
        http["handler"] = lambda request: httpx.Response(next(calls))
        manager = _manager(max_retries=3)
        manager.register_webhook(URL, ["*"])

        (result,) = await manager.notify("ord_1", "order_shipped", {})

        assert result["success"] is True
        assert result["attempts"] == 3

    async def test_gives_up_after_max_retries_without_leaking_the_body(self, http):
        http["handler"] = lambda request: httpx.Response(500, text="internal secret: hunter2")
        manager = _manager(max_retries=2)
        manager.register_webhook(URL, ["*"])

        (result,) = await manager.notify("ord_1", "order_shipped", {})

        assert result["success"] is False
        assert result["attempts"] == 2
        assert result["error"] == "HTTP 500"
        assert "hunter2" not in json.dumps(manager.get_delivery_log())

    async def test_redirects_are_not_followed(self, http):
        http["handler"] = lambda request: httpx.Response(
            302, headers={"Location": "http://169.254.169.254/latest/meta-data/"}
        )
        manager = _manager(max_retries=1)
        manager.register_webhook(URL, ["*"])

        (result,) = await manager.notify("ord_1", "order_shipped", {})

        assert result["success"] is False
        assert result["error"] == "HTTP 302"
        assert [r.url.host for r in http["requests"]] == ["hooks.example.com"]

    async def test_timeout_is_reported_and_retried(self, http):
        def slow(request):
            raise httpx.ReadTimeout("too slow", request=request)

        http["handler"] = slow
        manager = _manager(timeout=2.5, max_retries=2)
        manager.register_webhook(URL, ["*"])

        (result,) = await manager.notify("ord_1", "order_shipped", {})

        assert result["error"] == "Timeout after 2.5s"
        assert result["attempts"] == 2

    async def test_connection_error_is_reported_and_retried(self, http):
        def refuse(request):
            raise httpx.ConnectError("refused", request=request)

        http["handler"] = refuse
        manager = _manager(max_retries=2)
        manager.register_webhook(URL, ["*"])

        (result,) = await manager.notify("ord_1", "order_shipped", {})

        assert result["error"].startswith("Connection error")
        assert result["attempts"] == 2

    async def test_unexpected_errors_are_not_retried(self, http):
        def broken(request):
            raise RuntimeError("bug")

        http["handler"] = broken
        manager = _manager(max_retries=3)
        manager.register_webhook(URL, ["*"])

        (result,) = await manager.notify("ord_1", "order_shipped", {})

        assert result["attempts"] == 1
        assert result["error"].startswith("Unexpected error")

    async def test_delivery_log_keeps_recent_results(self, http):
        manager = _manager()
        manager.register_webhook(URL, ["*"])
        for index in range(3):
            await manager.notify(f"ord_{index}", "order_shipped", {})
        log = manager.get_delivery_log(limit=2)
        assert len(log) == 2
        assert all(entry["success"] for entry in log)
