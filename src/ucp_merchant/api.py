"""FastAPI application for the UCP Merchant Server.

Exposes REST endpoints for:
- UCP discovery (``/.well-known/ucp``) and capability negotiation
- Product catalog search and browsing
- Checkout session lifecycle (create, update, complete, abandon)
- UCP extension composition (fulfillment / discounts)
- AP2 payment mandate verification with ECDSA P-256 crypto
- Order lifecycle with SSE tracking
- Webhook registration and management
- MCP tool bindings wrapping UCP operations
"""

from __future__ import annotations

import json
from typing import Any

import structlog
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse

from common import ErrorResponse, HealthResponse

from ucp_merchant.catalog.products import ProductCatalog
from ucp_merchant.catalog.search import CatalogSearch
from ucp_merchant.checkout.session import CheckoutSessionManager, SessionNotFoundError
from ucp_merchant.checkout.state_machine import InvalidTransitionError
from ucp_merchant.config import UCPMerchantSettings, get_settings
from ucp_merchant.discovery.manifest import build_ucp_manifest
from ucp_merchant.discovery.negotiation import NegotiationEngine
from ucp_merchant.mcp_binding.tools import MCPToolHandler
from ucp_merchant.models import (
    CartMandate,
    IntentMandate,
    NegotiationRequest,
)
from ucp_merchant.orders.lifecycle import (
    InvalidOrderStateError,
    OrderManager,
    OrderNotFoundError,
)
from ucp_merchant.orders.tracking import OrderTracker
from ucp_merchant.orders.webhooks import WebhookManager
from ucp_merchant.payments.ap2_verification import MandateVerifier
from ucp_merchant.payments.keys import KeyManager

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Request / response body models
# ---------------------------------------------------------------------------


class CreateCheckoutRequest(BaseModel):
    """Body for POST /api/v1/checkout."""

    items: list[dict[str, Any]] = Field(
        ...,
        description="List of {product_id, quantity} dicts.",
    )


class UpdateCheckoutRequest(BaseModel):
    """Body for PATCH /api/v1/checkout/{session_id}."""

    add_items: list[dict[str, Any]] | None = None
    remove_items: list[str] | None = None
    shipping_address: dict[str, Any] | None = None
    billing_address: dict[str, Any] | None = None
    shipping_option_id: str | None = None
    discount_code: str | None = None


class VerifyCartMandateRequest(BaseModel):
    """Body for POST /api/v1/payments/verify/cart."""

    mandate: CartMandate
    expected_total: float | None = None


class VerifyIntentMandateRequest(BaseModel):
    """Body for POST /api/v1/payments/verify/intent."""

    mandate: IntentMandate
    merchant_id: str | None = None
    requested_amount: float | None = None


class CreateTestMandateRequest(BaseModel):
    """Body for POST /api/v1/payments/test-mandate."""

    session_id: str
    key_id: str


class CreateTestIntentMandateRequest(BaseModel):
    """Body for POST /api/v1/payments/test-intent-mandate."""

    agent_id: str
    key_id: str
    max_amount: float = 1000.0
    allowed_merchants: list[str] | None = None
    time_window_seconds: int = 3600


class RegisterWebhookRequest(BaseModel):
    """Body for POST /api/v1/webhooks."""

    url: str
    events: list[str] = Field(default_factory=lambda: ["*"])


class MCPExecuteRequest(BaseModel):
    """Body for POST /api/v1/mcp/tools/{tool_name}/execute."""

    arguments: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Application factory
# ---------------------------------------------------------------------------


def create_app(settings: UCPMerchantSettings | None = None) -> FastAPI:
    """Create and configure the FastAPI application.

    Instantiates all service-layer objects and wires them into the route
    handlers via closure.
    """
    settings = settings or get_settings()

    app = FastAPI(
        title="UCP Merchant Server -- TechVault Electronics",
        description=(
            "A UCP-compliant merchant server implementing Google's Universal "
            "Commerce Protocol with product catalog, checkout state machine, "
            "AP2 payment mandates, order lifecycle, and MCP tool bindings."
        ),
        version=settings.service_version,
        docs_url="/docs",
        redoc_url="/redoc",
    )

    # CORS
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ---- Service layer wiring ----------------------------------------------
    catalog = ProductCatalog()
    catalog_search = CatalogSearch(catalog)
    checkout_manager = CheckoutSessionManager(settings, catalog)
    negotiation_engine = NegotiationEngine(settings)
    key_manager = KeyManager()
    mandate_verifier = MandateVerifier(settings, key_manager)
    order_tracker = OrderTracker()
    order_manager = OrderManager(tracker=order_tracker)
    webhook_manager = WebhookManager(
        timeout=settings.webhook_timeout_seconds,
        max_retries=settings.webhook_max_retries,
    )
    mcp_handler = MCPToolHandler(
        checkout_manager=checkout_manager,
        catalog_search=catalog_search,
        catalog=catalog,
        order_manager=order_manager,
    )

    # Expose on app.state for testing
    app.state.settings = settings
    app.state.catalog = catalog
    app.state.checkout_manager = checkout_manager
    app.state.order_manager = order_manager
    app.state.key_manager = key_manager

    # ======================================================================
    # HEALTH
    # ======================================================================

    @app.get("/health", response_model=HealthResponse, tags=["health"])
    async def health() -> HealthResponse:
        """Health check."""
        return HealthResponse(
            status="healthy",
            service=settings.service_name,
            version=settings.service_version,
        )

    # ======================================================================
    # UCP DISCOVERY
    # ======================================================================

    @app.get("/.well-known/ucp", tags=["discovery"])
    async def ucp_discovery() -> dict[str, Any]:
        """Serve the UCP discovery manifest."""
        manifest = build_ucp_manifest(settings)
        return manifest.model_dump(mode="json")

    @app.post("/api/v1/negotiate", tags=["discovery"])
    async def negotiate_capabilities(
        request: NegotiationRequest,
    ) -> dict[str, Any]:
        """Capability negotiation -- server-selects pattern."""
        result = negotiation_engine.negotiate(request)
        return result.model_dump(mode="json")

    # ======================================================================
    # CATALOG
    # ======================================================================

    @app.get("/api/v1/catalog/search", tags=["catalog"])
    async def search_products(
        query: str | None = Query(None, description="Full-text search"),
        category: str | None = Query(None, description="Category filter"),
        min_price: float | None = Query(None, description="Minimum price"),
        max_price: float | None = Query(None, description="Maximum price"),
        brand: str | None = Query(None, description="Brand filter"),
        in_stock: bool | None = Query(None, description="Stock filter"),
        sort_by: str = Query("relevance", description="Sort order"),
        limit: int = Query(20, ge=1, le=100, description="Page size"),
        offset: int = Query(0, ge=0, description="Page offset"),
    ) -> dict[str, Any]:
        """Search and filter the product catalog."""
        result = catalog_search.search(
            query=query,
            category=category,
            min_price=min_price,
            max_price=max_price,
            brand=brand,
            in_stock=in_stock,
            sort_by=sort_by,
            limit=limit,
            offset=offset,
        )
        return result.model_dump(mode="json")

    @app.get("/api/v1/catalog/products/{product_id}", tags=["catalog"])
    async def get_product(product_id: str) -> dict[str, Any]:
        """Get a single product by ID."""
        product = catalog.get(product_id)
        if product is None:
            raise HTTPException(status_code=404, detail=f"Product not found: {product_id}")
        return product.model_dump(mode="json")

    @app.get("/api/v1/catalog/categories", tags=["catalog"])
    async def list_categories() -> dict[str, Any]:
        """List all product categories with product counts."""
        return {"categories": catalog.get_categories()}

    # ======================================================================
    # CHECKOUT
    # ======================================================================

    @app.post("/api/v1/checkout", tags=["checkout"])
    async def create_checkout(req: CreateCheckoutRequest) -> dict[str, Any]:
        """Create a new checkout session."""
        try:
            session = checkout_manager.create_session(line_items=req.items)
            return session.model_dump(mode="json")
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/v1/checkout/{session_id}", tags=["checkout"])
    async def get_checkout(session_id: str) -> dict[str, Any]:
        """Retrieve a checkout session."""
        try:
            session = checkout_manager.get_session(session_id)
            return session.model_dump(mode="json")
        except SessionNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.patch("/api/v1/checkout/{session_id}", tags=["checkout"])
    async def update_checkout(
        session_id: str, req: UpdateCheckoutRequest
    ) -> dict[str, Any]:
        """Update a checkout session (add items, set address, etc.)."""
        updates: dict[str, Any] = {}
        if req.add_items is not None:
            updates["add_items"] = req.add_items
        if req.remove_items is not None:
            updates["remove_items"] = req.remove_items
        if req.shipping_address is not None:
            updates["shipping_address"] = req.shipping_address
        if req.billing_address is not None:
            updates["billing_address"] = req.billing_address
        if req.shipping_option_id is not None:
            updates["shipping_option_id"] = req.shipping_option_id
        if req.discount_code is not None:
            updates["discount_code"] = req.discount_code

        try:
            session = checkout_manager.update_session(session_id, updates)
            return session.model_dump(mode="json")
        except SessionNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/v1/checkout/{session_id}/complete", tags=["checkout"])
    async def complete_checkout(session_id: str) -> dict[str, Any]:
        """Complete a checkout session and create an order."""
        try:
            session = checkout_manager.complete_session(session_id)
            order = order_manager.create_order(session)
            # Update the session's order_id to match
            session.order_id = order.id
            return {
                "session": session.model_dump(mode="json"),
                "order": order.model_dump(mode="json"),
            }
        except SessionNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except (InvalidTransitionError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/v1/checkout/{session_id}/abandon", tags=["checkout"])
    async def abandon_checkout(session_id: str) -> dict[str, Any]:
        """Abandon a checkout session."""
        try:
            session = checkout_manager.abandon_session(session_id)
            return session.model_dump(mode="json")
        except SessionNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    # ---- Checkout extensions -----------------------------------------------

    @app.get("/api/v1/checkout/{session_id}/shipping", tags=["checkout"])
    async def get_shipping_options(session_id: str) -> dict[str, Any]:
        """Get available shipping options for a checkout session."""
        try:
            session = checkout_manager.get_session(session_id)
        except SessionNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

        options = checkout_manager.fulfillment.get_shipping_options(
            session.subtotal.amount
        )
        return {
            "session_id": session_id,
            "subtotal": session.subtotal.amount,
            "options": [opt.model_dump(mode="json") for opt in options],
        }

    @app.post("/api/v1/checkout/{session_id}/discount", tags=["checkout"])
    async def apply_discount(
        session_id: str, code: str = Query(..., description="Discount code")
    ) -> dict[str, Any]:
        """Apply a discount code to a checkout session."""
        try:
            session = checkout_manager.update_session(
                session_id, {"discount_code": code}
            )
            return session.model_dump(mode="json")
        except SessionNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    # ======================================================================
    # PAYMENTS (AP2)
    # ======================================================================

    @app.post("/api/v1/payments/keys", tags=["payments"])
    async def generate_key_pair() -> dict[str, Any]:
        """Generate a new ECDSA P-256 key pair for mandate signing."""
        info = key_manager.generate_key_pair()
        return info.model_dump(mode="json")

    @app.get("/api/v1/payments/keys", tags=["payments"])
    async def list_keys() -> dict[str, Any]:
        """List all generated key pairs."""
        keys = key_manager.list_keys()
        return {"keys": [k.model_dump(mode="json") for k in keys]}

    @app.get("/api/v1/payments/keys/{key_id}", tags=["payments"])
    async def get_key(key_id: str) -> dict[str, Any]:
        """Get the public key JWK for a specific key pair."""
        try:
            jwk = key_manager.get_public_key_jwk(key_id)
            info = key_manager.get_key_info(key_id)
            return {
                "key_id": key_id,
                "jwk": jwk,
                "info": info.model_dump(mode="json"),
            }
        except KeyError:
            raise HTTPException(
                status_code=404, detail=f"Key not found: {key_id}"
            )

    @app.post("/api/v1/payments/verify/cart", tags=["payments"])
    async def verify_cart_mandate(req: VerifyCartMandateRequest) -> dict[str, Any]:
        """Verify an AP2 cart-level payment mandate."""
        result = mandate_verifier.verify_cart_mandate(
            mandate=req.mandate,
            expected_total=req.expected_total,
        )
        return result.model_dump(mode="json")

    @app.post("/api/v1/payments/verify/intent", tags=["payments"])
    async def verify_intent_mandate(
        req: VerifyIntentMandateRequest,
    ) -> dict[str, Any]:
        """Verify an AP2 intent-level payment mandate."""
        result = mandate_verifier.verify_intent_mandate(
            mandate=req.mandate,
            merchant_id=req.merchant_id,
            requested_amount=req.requested_amount,
        )
        return result.model_dump(mode="json")

    @app.post("/api/v1/payments/test-mandate", tags=["payments"])
    async def create_test_mandate(
        req: CreateTestMandateRequest,
    ) -> dict[str, Any]:
        """Create a signed test cart mandate for demo purposes."""
        try:
            session = checkout_manager.get_session(req.session_id)
        except SessionNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

        try:
            mandate = mandate_verifier.create_test_mandate(session, req.key_id)
            return mandate.model_dump(mode="json")
        except KeyError:
            raise HTTPException(
                status_code=404, detail=f"Key not found: {req.key_id}"
            )

    @app.post("/api/v1/payments/test-intent-mandate", tags=["payments"])
    async def create_test_intent_mandate(
        req: CreateTestIntentMandateRequest,
    ) -> dict[str, Any]:
        """Create a signed test intent mandate for demo purposes."""
        try:
            mandate = mandate_verifier.create_test_intent_mandate(
                agent_id=req.agent_id,
                key_id=req.key_id,
                max_amount=req.max_amount,
                allowed_merchants=req.allowed_merchants,
                time_window_seconds=req.time_window_seconds,
            )
            return mandate.model_dump(mode="json")
        except KeyError:
            raise HTTPException(
                status_code=404, detail=f"Key not found: {req.key_id}"
            )

    # ======================================================================
    # ORDERS
    # ======================================================================

    @app.get("/api/v1/orders", tags=["orders"])
    async def list_orders() -> dict[str, Any]:
        """List all orders."""
        orders = order_manager.list_orders()
        return {
            "orders": [o.model_dump(mode="json") for o in orders],
            "total": len(orders),
        }

    @app.get("/api/v1/orders/{order_id}", tags=["orders"])
    async def get_order(order_id: str) -> dict[str, Any]:
        """Get an order by ID."""
        try:
            order = order_manager.get_order(order_id)
            return order.model_dump(mode="json")
        except OrderNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/api/v1/orders/{order_id}/advance", tags=["orders"])
    async def advance_order(order_id: str) -> dict[str, Any]:
        """Advance the order to the next state."""
        try:
            order = order_manager.advance_state(order_id)
            return order.model_dump(mode="json")
        except OrderNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except InvalidOrderStateError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/v1/orders/{order_id}/cancel", tags=["orders"])
    async def cancel_order(order_id: str) -> dict[str, Any]:
        """Cancel an order."""
        try:
            order = order_manager.cancel_order(order_id)
            return order.model_dump(mode="json")
        except OrderNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except InvalidOrderStateError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/v1/orders/{order_id}/simulate-fulfillment", tags=["orders"])
    async def simulate_fulfillment(order_id: str) -> dict[str, Any]:
        """Fast-forward an order to SHIPPED state with tracking info."""
        try:
            order = order_manager.simulate_fulfillment(order_id)
            return order.model_dump(mode="json")
        except OrderNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except InvalidOrderStateError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/v1/orders/{order_id}/track", tags=["orders"])
    async def track_order(order_id: str) -> EventSourceResponse:
        """Stream real-time order status updates via SSE."""
        # Verify order exists
        try:
            order_manager.get_order(order_id)
        except OrderNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

        async def event_generator():  # type: ignore[no-untyped-def]
            async for event in order_tracker.subscribe(order_id):
                yield {
                    "event": event.event_type,
                    "data": json.dumps(event.model_dump(mode="json"), default=str),
                }

        return EventSourceResponse(event_generator())

    # ======================================================================
    # WEBHOOKS
    # ======================================================================

    @app.post("/api/v1/webhooks", tags=["webhooks"])
    async def register_webhook(req: RegisterWebhookRequest) -> dict[str, Any]:
        """Register a webhook endpoint for order events."""
        try:
            registration = webhook_manager.register_webhook(
                url=req.url, events=req.events
            )
            return registration.model_dump(mode="json")
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/v1/webhooks", tags=["webhooks"])
    async def list_webhooks() -> dict[str, Any]:
        """List all registered webhooks."""
        webhooks = webhook_manager.list_webhooks()
        return {
            "webhooks": [w.model_dump(mode="json") for w in webhooks],
            "total": len(webhooks),
        }

    # ======================================================================
    # MCP TOOL BINDINGS
    # ======================================================================

    @app.get("/api/v1/mcp/tools", tags=["mcp"])
    async def list_mcp_tools() -> dict[str, Any]:
        """List all available MCP tools with their input schemas."""
        tools = mcp_handler.get_tool_definitions()
        return {
            "tools": [t.model_dump(mode="json") for t in tools],
            "total": len(tools),
        }

    @app.post("/api/v1/mcp/tools/{tool_name}/execute", tags=["mcp"])
    async def execute_mcp_tool(
        tool_name: str, req: MCPExecuteRequest
    ) -> dict[str, Any]:
        """Execute an MCP tool by name."""
        result = await mcp_handler.execute(tool_name, req.arguments)
        return result.model_dump(mode="json")

    # ======================================================================
    # ERROR HANDLERS
    # ======================================================================

    @app.exception_handler(Exception)
    async def generic_exception_handler(
        request: Request, exc: Exception
    ) -> JSONResponse:
        """Catch-all error handler returning a structured error response."""
        logger.error(
            "unhandled_exception",
            error=str(exc),
            path=request.url.path,
            exc_info=True,
        )
        return JSONResponse(
            status_code=500,
            content=ErrorResponse(
                error="Internal server error",
                detail=str(exc),
                status_code=500,
            ).model_dump(),
        )

    return app
