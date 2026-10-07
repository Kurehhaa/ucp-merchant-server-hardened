"""MCP tool bindings."""

from __future__ import annotations

import pytest

from tests.ap2_helpers import SHIPPING_ADDRESS, AgentSimulator
from ucp_merchant.catalog.products import ProductCatalog
from ucp_merchant.catalog.search import CatalogSearch
from ucp_merchant.checkout.session import CheckoutSessionManager
from ucp_merchant.config import UCPMerchantSettings
from ucp_merchant.mcp_binding.tools import TOOL_DEFINITIONS, MCPToolHandler
from ucp_merchant.orders.lifecycle import OrderManager
from ucp_merchant.payments.ap2_verification import MandateVerifier
from ucp_merchant.payments.keys import KeyManager


class Harness:
    def __init__(self, *, require_mandate: bool = True) -> None:
        self.settings = UCPMerchantSettings(environment="testing")
        self.catalog = ProductCatalog()
        self.checkout = CheckoutSessionManager(self.settings, self.catalog)
        self.orders = OrderManager()
        self.keys = KeyManager()
        self.verifier = MandateVerifier(self.settings, self.keys)
        self.handler = MCPToolHandler(
            checkout_manager=self.checkout,
            catalog_search=CatalogSearch(self.catalog),
            catalog=self.catalog,
            order_manager=self.orders,
            mandate_verifier=self.verifier,
            require_mandate=require_mandate,
        )
        self.agent = AgentSimulator(self.settings.merchant_id)
        self.agent.key_id = self.keys.register_public_key(self.agent.public_jwk).key_id

    async def ready_checkout(self) -> str:
        created = await self.handler.execute(
            "create_checkout", {"items": [{"product_id": "kb-001", "quantity": 1}]}
        )
        session_id = created.result["id"]
        await self.handler.execute(
            "update_checkout", {"session_id": session_id, "shipping_address": SHIPPING_ADDRESS}
        )
        selected = await self.handler.execute(
            "select_shipping",
            {"session_id": session_id, "shipping_option_id": "shipping_standard"},
        )
        assert selected.result["state"] == "ready_for_complete"
        return session_id

    def mandate(self, session_id: str) -> dict:
        session = self.checkout.get_session(session_id)
        return self.agent.cart_mandate(session.model_dump(mode="json")).model_dump(mode="json")


@pytest.fixture
def harness() -> Harness:
    return Harness()


class TestToolCatalog:
    def test_definitions_match_the_handlers(self, harness):
        names = {tool.name for tool in harness.handler.get_tool_definitions()}
        assert names == {tool.name for tool in TOOL_DEFINITIONS}
        assert {"search_products", "create_checkout", "complete_checkout", "get_order"} <= names

    async def test_every_advertised_tool_has_a_handler(self, harness):
        for tool in TOOL_DEFINITIONS:
            result = await harness.handler.execute(tool.name, {})
            assert "Unknown tool" not in (result.error or "")

    async def test_unknown_tool(self, harness):
        result = await harness.handler.execute("rm_rf", {})
        assert result.success is False
        assert "Unknown tool" in result.error


class TestShoppingTools:
    async def test_search_products(self, harness):
        result = await harness.handler.execute("search_products", {"query": "keyboard", "limit": 2})
        assert result.success
        assert 0 < len(result.result["products"]) <= 2

    async def test_get_product(self, harness):
        ok = await harness.handler.execute("get_product", {"product_id": "laptop-001"})
        assert ok.success and ok.result["id"] == "laptop-001"
        missing = await harness.handler.execute("get_product", {"product_id": "nope"})
        assert missing.success is False and "not found" in missing.error

    async def test_checkout_flow_and_discount(self, harness):
        session_id = await harness.ready_checkout()
        discounted = await harness.handler.execute(
            "apply_discount", {"session_id": session_id, "code": "SAVE10"}
        )
        assert discounted.success
        assert discounted.result["applied_discount"]["code"] == "SAVE10"
        assert discounted.result["discount_amount"]["amount"] > 0

    async def test_invalid_discount_is_a_tool_error(self, harness):
        session_id = await harness.ready_checkout()
        result = await harness.handler.execute(
            "apply_discount", {"session_id": session_id, "code": "NOPE"}
        )
        assert result.success is False and result.error

    async def test_missing_arguments_are_reported_not_raised(self, harness):
        result = await harness.handler.execute("get_order", {})
        assert result.success is False


class TestCompleteCheckout:
    async def test_requires_a_mandate(self, harness):
        session_id = await harness.ready_checkout()
        result = await harness.handler.execute("complete_checkout", {"session_id": session_id})
        assert result.success is False
        assert "cart_mandate is required" in result.error
        assert harness.orders.list_orders() == []

    async def test_valid_mandate_creates_the_order(self, harness):
        session_id = await harness.ready_checkout()
        result = await harness.handler.execute(
            "complete_checkout",
            {"session_id": session_id, "cart_mandate": harness.mandate(session_id)},
        )
        assert result.success, result.error
        assert result.result["session"]["state"] == "completed"
        order_id = result.result["order"]["id"]
        fetched = await harness.handler.execute("get_order", {"order_id": order_id})
        assert fetched.success and fetched.result["id"] == order_id

    async def test_tampered_mandate_is_rejected(self, harness):
        session_id = await harness.ready_checkout()
        mandate = harness.mandate(session_id)
        mandate["amount"]["amount"] = 0.01
        result = await harness.handler.execute(
            "complete_checkout", {"session_id": session_id, "cart_mandate": mandate}
        )
        assert result.success is False
        assert harness.orders.list_orders() == []

    async def test_mandate_is_released_when_order_creation_fails(self, harness, monkeypatch):
        session_id = await harness.ready_checkout()
        mandate = harness.mandate(session_id)
        original = harness.orders.create_order

        def failing(session):
            raise RuntimeError("database down")

        monkeypatch.setattr(harness.orders, "create_order", failing)
        first = await harness.handler.execute(
            "complete_checkout", {"session_id": session_id, "cart_mandate": mandate}
        )
        assert first.success is False and "database down" in first.error

        # The agent can retry with the very same mandate once the fault is gone.
        monkeypatch.setattr(harness.orders, "create_order", original)
        retry = await harness.handler.execute(
            "complete_checkout", {"session_id": session_id, "cart_mandate": mandate}
        )
        assert retry.success, retry.error

    async def test_mandate_can_be_made_optional(self):
        relaxed = Harness(require_mandate=False)
        session_id = await relaxed.ready_checkout()
        result = await relaxed.handler.execute("complete_checkout", {"session_id": session_id})
        assert result.success, result.error


class TestConfiguration:
    def test_required_mandate_needs_a_verifier(self):
        harness = Harness()
        with pytest.raises(ValueError, match="needs a mandate_verifier"):
            MCPToolHandler(
                checkout_manager=harness.checkout,
                catalog_search=CatalogSearch(harness.catalog),
                catalog=harness.catalog,
                order_manager=harness.orders,
                mandate_verifier=None,
                require_mandate=True,
            )
