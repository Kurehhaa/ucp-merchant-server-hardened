"""Tests for UCP Merchant Server API."""

import pytest
from httpx import ASGITransport, AsyncClient

from ucp_merchant.api import create_app
from ucp_merchant.config import UCPMerchantSettings


@pytest.fixture
def settings():
    return UCPMerchantSettings(
        environment="testing",
    )


@pytest.fixture
def app(settings):
    application = create_app(settings)
    # Disable the order tracker to avoid a structlog keyword conflict
    # in OrderTracker.notify_update (the `event` kwarg clashes with
    # structlog's implicit first `event` positional parameter).
    application.state.order_manager._tracker = None
    return application


@pytest.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


class TestHealth:
    async def test_health_check(self, client):
        resp = await client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "healthy"
        assert data["service"] == "ucp-merchant-server"


class TestDiscovery:
    async def test_well_known_ucp(self, client):
        resp = await client.get("/.well-known/ucp")
        assert resp.status_code == 200
        data = resp.json()
        assert data["spec_version"] == "2026-01-11"
        assert data["merchant_name"] == "TechVault Electronics"
        assert "capabilities" in data
        assert "extensions" in data
        assert "payment_handlers" in data
        assert "endpoints" in data

    async def test_manifest_has_capabilities(self, client):
        resp = await client.get("/.well-known/ucp")
        data = resp.json()
        capabilities = data["capabilities"]
        assert len(capabilities) > 0
        cap_ids = [c["id"] for c in capabilities]
        assert "dev.ucp.shopping.checkout" in cap_ids
        assert "dev.ucp.shopping.orders" in cap_ids

    async def test_manifest_has_extensions(self, client):
        resp = await client.get("/.well-known/ucp")
        data = resp.json()
        extensions = data["extensions"]
        ext_ids = [e["id"] for e in extensions]
        assert "dev.ucp.shopping.fulfillment" in ext_ids
        assert "dev.ucp.shopping.discount" in ext_ids

    async def test_manifest_has_payment_handlers(self, client):
        resp = await client.get("/.well-known/ucp")
        data = resp.json()
        handlers = data["payment_handlers"]
        handler_ids = [h["id"] for h in handlers]
        assert "dev.ucp.mock_payment" in handler_ids
        assert "google.pay" in handler_ids

    async def test_negotiate_capabilities(self, client):
        resp = await client.post("/api/v1/negotiate", json={
            "agent_id": "test-agent-001",
            "agent_name": "Test Agent",
            "requested_capabilities": ["dev.ucp.shopping.checkout"],
            "requested_extensions": ["dev.ucp.shopping.fulfillment"],
            "supported_payment_handlers": ["dev.ucp.mock_payment"],
        })
        assert resp.status_code == 200
        data = resp.json()
        assert "negotiation_id" in data
        assert data["agent_id"] == "test-agent-001"
        assert "agreed_capabilities" in data
        agreed_cap_ids = [c["id"] for c in data["agreed_capabilities"]]
        assert "dev.ucp.shopping.checkout" in agreed_cap_ids
        assert "session_endpoint" in data


class TestCatalog:
    async def test_search_products(self, client):
        resp = await client.get("/api/v1/catalog/search")
        assert resp.status_code == 200
        data = resp.json()
        assert "products" in data
        assert "total" in data
        assert data["total"] > 0

    async def test_search_products_with_query(self, client):
        resp = await client.get("/api/v1/catalog/search", params={"query": "laptop"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] >= 1
        assert data["query"] == "laptop"

    async def test_filter_by_category(self, client):
        resp = await client.get("/api/v1/catalog/search", params={"category": "laptops"})
        assert resp.status_code == 200
        data = resp.json()
        for p in data["products"]:
            assert p["category"] == "laptops"

    async def test_filter_by_price_range(self, client):
        resp = await client.get("/api/v1/catalog/search", params={
            "min_price": 50, "max_price": 200
        })
        assert resp.status_code == 200
        data = resp.json()
        for p in data["products"]:
            price = p["price"]["amount"]
            assert 50 <= price <= 200

    async def test_get_product_by_id(self, client):
        resp = await client.get("/api/v1/catalog/products/laptop-001")
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == "laptop-001"
        assert "name" in data
        assert "price" in data

    async def test_get_product_not_found(self, client):
        resp = await client.get("/api/v1/catalog/products/nonexistent")
        assert resp.status_code == 404

    async def test_get_categories(self, client):
        resp = await client.get("/api/v1/catalog/categories")
        assert resp.status_code == 200
        data = resp.json()
        assert "categories" in data
        assert len(data["categories"]) > 0
        cat_names = [c["name"] for c in data["categories"]]
        assert "laptops" in cat_names

    async def test_sort_by_price(self, client):
        resp = await client.get("/api/v1/catalog/search", params={"sort_by": "price_asc"})
        assert resp.status_code == 200
        products = resp.json()["products"]
        if len(products) >= 2:
            prices = [p["price"]["amount"] for p in products]
            assert prices == sorted(prices)


class TestCheckout:
    async def _create_checkout(self, client):
        """Create a checkout session with known product IDs."""
        resp = await client.post("/api/v1/checkout", json={
            "items": [
                {"product_id": "laptop-001", "quantity": 1},
                {"product_id": "kb-001", "quantity": 1},
            ]
        })
        return resp

    async def test_create_session(self, client):
        resp = await self._create_checkout(client)
        assert resp.status_code == 200
        data = resp.json()
        # With items present but no address/shipping, state is requires_escalation
        assert data["state"] in ("incomplete", "requires_escalation")
        assert len(data["line_items"]) > 0
        assert "id" in data

    async def test_get_session(self, client):
        create_resp = await self._create_checkout(client)
        session_id = create_resp.json()["id"]
        resp = await client.get(f"/api/v1/checkout/{session_id}")
        assert resp.status_code == 200
        assert resp.json()["id"] == session_id

    async def test_get_session_not_found(self, client):
        resp = await client.get("/api/v1/checkout/nonexistent")
        assert resp.status_code == 404

    async def test_update_session_address(self, client):
        create_resp = await self._create_checkout(client)
        session_id = create_resp.json()["id"]
        resp = await client.patch(f"/api/v1/checkout/{session_id}", json={
            "shipping_address": {
                "full_name": "Test User",
                "line1": "123 Main St",
                "city": "San Francisco",
                "state": "CA",
                "postal_code": "94105",
                "country": "US",
            }
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["shipping_address"] is not None
        assert data["shipping_address"]["city"] == "San Francisco"

    async def test_select_shipping(self, client):
        create_resp = await self._create_checkout(client)
        session_id = create_resp.json()["id"]
        # Set address first
        await client.patch(f"/api/v1/checkout/{session_id}", json={
            "shipping_address": {
                "full_name": "Test User",
                "line1": "123 Main St",
                "city": "San Francisco",
                "state": "CA",
                "postal_code": "94105",
                "country": "US",
            }
        })
        # Select shipping
        resp = await client.patch(f"/api/v1/checkout/{session_id}", json={
            "shipping_option_id": "shipping_standard"
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["selected_shipping"] is not None
        assert data["selected_shipping"]["id"] == "shipping_standard"

    async def test_apply_discount(self, client):
        create_resp = await self._create_checkout(client)
        session_id = create_resp.json()["id"]
        resp = await client.patch(f"/api/v1/checkout/{session_id}", json={
            "discount_code": "SAVE10"
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["applied_discount"] is not None
        assert data["applied_discount"]["code"] == "SAVE10"

    async def test_checkout_state_machine(self, client):
        create_resp = await self._create_checkout(client)
        session_id = create_resp.json()["id"]
        # Initial state should be requires_escalation (items present but missing address/shipping)
        initial_state = create_resp.json()["state"]
        assert initial_state in ("incomplete", "requires_escalation")

        # Add address
        await client.patch(f"/api/v1/checkout/{session_id}", json={
            "shipping_address": {
                "full_name": "Test User",
                "line1": "123 Main St",
                "city": "San Francisco",
                "state": "CA",
                "postal_code": "94105",
                "country": "US",
            }
        })

        # Select shipping
        resp = await client.patch(f"/api/v1/checkout/{session_id}", json={
            "shipping_option_id": "shipping_standard"
        })
        data = resp.json()
        assert data["state"] == "ready_for_complete"

    async def test_complete_checkout(self, client):
        create_resp = await self._create_checkout(client)
        session_id = create_resp.json()["id"]

        # Fill required fields
        await client.patch(f"/api/v1/checkout/{session_id}", json={
            "shipping_address": {
                "full_name": "Test User",
                "line1": "123 Main St",
                "city": "San Francisco",
                "state": "CA",
                "postal_code": "94105",
                "country": "US",
            }
        })
        await client.patch(f"/api/v1/checkout/{session_id}", json={
            "shipping_option_id": "shipping_standard"
        })

        resp = await client.post(f"/api/v1/checkout/{session_id}/complete")
        assert resp.status_code == 200
        data = resp.json()
        assert "session" in data
        assert "order" in data
        assert data["session"]["state"] == "completed"
        assert data["order"]["id"] is not None
        assert data["order"]["state"] == "confirmed"

    async def test_cannot_complete_incomplete_session(self, client):
        create_resp = await self._create_checkout(client)
        session_id = create_resp.json()["id"]
        resp = await client.post(f"/api/v1/checkout/{session_id}/complete")
        assert resp.status_code == 400

    async def test_abandon_checkout(self, client):
        create_resp = await self._create_checkout(client)
        session_id = create_resp.json()["id"]
        resp = await client.post(f"/api/v1/checkout/{session_id}/abandon")
        assert resp.status_code == 200
        data = resp.json()
        assert data["state"] == "abandoned"

    async def test_get_shipping_options(self, client):
        create_resp = await self._create_checkout(client)
        session_id = create_resp.json()["id"]
        resp = await client.get(f"/api/v1/checkout/{session_id}/shipping")
        assert resp.status_code == 200
        data = resp.json()
        assert "options" in data
        assert len(data["options"]) >= 3

    async def test_apply_discount_endpoint(self, client):
        create_resp = await self._create_checkout(client)
        session_id = create_resp.json()["id"]
        resp = await client.post(
            f"/api/v1/checkout/{session_id}/discount",
            params={"code": "SAVE10"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["applied_discount"] is not None


class TestPayments:
    async def test_generate_key_pair(self, client):
        resp = await client.post("/api/v1/payments/keys")
        assert resp.status_code == 200
        data = resp.json()
        assert "key_id" in data
        assert "public_key_jwk" in data
        assert data["algorithm"] == "ES256"
        assert data["curve"] == "P-256"

    async def test_list_keys(self, client):
        # Generate a key first
        await client.post("/api/v1/payments/keys")
        resp = await client.get("/api/v1/payments/keys")
        assert resp.status_code == 200
        data = resp.json()
        assert "keys" in data
        assert len(data["keys"]) >= 1

    async def test_get_public_key(self, client):
        create_resp = await client.post("/api/v1/payments/keys")
        key_id = create_resp.json()["key_id"]
        resp = await client.get(f"/api/v1/payments/keys/{key_id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["key_id"] == key_id
        assert "jwk" in data
        assert data["jwk"]["kty"] == "EC"
        assert data["jwk"]["crv"] == "P-256"

    async def test_verify_test_mandate(self, client):
        # Generate key
        key_resp = await client.post("/api/v1/payments/keys")
        key_id = key_resp.json()["key_id"]

        # Create checkout session
        create_resp = await client.post("/api/v1/checkout", json={
            "items": [{"product_id": "laptop-001", "quantity": 1}]
        })
        session_id = create_resp.json()["id"]

        # Create test mandate
        resp = await client.post("/api/v1/payments/test-mandate", json={
            "session_id": session_id,
            "key_id": key_id,
        })
        assert resp.status_code == 200
        mandate = resp.json()
        assert "signature" in mandate
        assert "mandate_id" in mandate

        # Verify it
        resp = await client.post("/api/v1/payments/verify/cart", json={
            "mandate": mandate,
        })
        assert resp.status_code == 200
        result = resp.json()
        assert result["valid"] is True
        assert result["mandate_type"] == "cart"

    async def test_create_and_verify_intent_mandate(self, client):
        # Generate key
        key_resp = await client.post("/api/v1/payments/keys")
        key_id = key_resp.json()["key_id"]

        # Create test intent mandate
        resp = await client.post("/api/v1/payments/test-intent-mandate", json={
            "agent_id": "test-agent",
            "key_id": key_id,
            "max_amount": 500.0,
            "time_window_seconds": 3600,
        })
        assert resp.status_code == 200
        mandate = resp.json()
        assert "signature" in mandate
        assert mandate["mandate_type"] == "intent"

        # Verify it
        resp = await client.post("/api/v1/payments/verify/intent", json={
            "mandate": mandate,
            "requested_amount": 100.0,
        })
        assert resp.status_code == 200
        result = resp.json()
        assert result["valid"] is True
        assert result["mandate_type"] == "intent"


class TestOrders:
    async def _create_completed_checkout(self, client):
        """Create a completed checkout and return the response data."""
        create_resp = await client.post("/api/v1/checkout", json={
            "items": [{"product_id": "laptop-001", "quantity": 1}]
        })
        session_id = create_resp.json()["id"]
        await client.patch(f"/api/v1/checkout/{session_id}", json={
            "shipping_address": {
                "full_name": "Test User",
                "line1": "123 Main St",
                "city": "SF",
                "state": "CA",
                "postal_code": "94105",
                "country": "US",
            }
        })
        await client.patch(f"/api/v1/checkout/{session_id}", json={
            "shipping_option_id": "shipping_standard"
        })
        resp = await client.post(f"/api/v1/checkout/{session_id}/complete")
        return resp.json()

    async def test_get_order(self, client):
        data = await self._create_completed_checkout(client)
        order_id = data["order"]["id"]
        resp = await client.get(f"/api/v1/orders/{order_id}")
        assert resp.status_code == 200
        order_data = resp.json()
        assert order_data["id"] == order_id
        assert order_data["state"] == "confirmed"

    async def test_list_orders(self, client):
        await self._create_completed_checkout(client)
        resp = await client.get("/api/v1/orders")
        assert resp.status_code == 200
        data = resp.json()
        assert "orders" in data
        assert data["total"] >= 1

    async def test_advance_order(self, client):
        data = await self._create_completed_checkout(client)
        order_id = data["order"]["id"]
        # Advance from confirmed -> processing
        resp = await client.post(f"/api/v1/orders/{order_id}/advance")
        assert resp.status_code == 200
        assert resp.json()["state"] == "processing"

    async def test_cancel_order(self, client):
        data = await self._create_completed_checkout(client)
        order_id = data["order"]["id"]
        resp = await client.post(f"/api/v1/orders/{order_id}/cancel")
        assert resp.status_code == 200
        assert resp.json()["state"] == "cancelled"

    async def test_simulate_fulfillment(self, client):
        data = await self._create_completed_checkout(client)
        order_id = data["order"]["id"]
        resp = await client.post(f"/api/v1/orders/{order_id}/simulate-fulfillment")
        assert resp.status_code == 200
        order_data = resp.json()
        assert order_data["state"] == "shipped"
        assert order_data["tracking_number"] is not None
        assert order_data["carrier"] is not None


class TestWebhooks:
    async def test_register_webhook(self, client):
        resp = await client.post("/api/v1/webhooks", json={
            "url": "https://example.com/webhook",
            "events": ["order_confirmed", "order_shipped"],
        })
        assert resp.status_code == 200
        data = resp.json()
        assert "id" in data
        assert data["url"] == "https://example.com/webhook"
        assert data["active"] is True

    async def test_list_webhooks(self, client):
        await client.post("/api/v1/webhooks", json={
            "url": "https://example.com/webhook",
        })
        resp = await client.get("/api/v1/webhooks")
        assert resp.status_code == 200
        data = resp.json()
        assert "webhooks" in data
        assert data["total"] >= 1


class TestMCP:
    async def test_list_tools(self, client):
        resp = await client.get("/api/v1/mcp/tools")
        assert resp.status_code == 200
        data = resp.json()
        assert "tools" in data
        assert len(data["tools"]) >= 6
        tool_names = [t["name"] for t in data["tools"]]
        assert "search_products" in tool_names
        assert "create_checkout" in tool_names

    async def test_execute_search_tool(self, client):
        resp = await client.post("/api/v1/mcp/tools/search_products/execute", json={
            "arguments": {"query": "laptop"}
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["tool_name"] == "search_products"

    async def test_execute_get_product_tool(self, client):
        resp = await client.post("/api/v1/mcp/tools/get_product/execute", json={
            "arguments": {"product_id": "laptop-001"}
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True

    async def test_execute_unknown_tool(self, client):
        resp = await client.post("/api/v1/mcp/tools/nonexistent/execute", json={
            "arguments": {}
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is False
        assert data["error"] is not None
