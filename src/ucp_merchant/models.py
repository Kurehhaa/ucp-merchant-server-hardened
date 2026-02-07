"""Pydantic models for the UCP Merchant Server.

Covers the full domain: products, checkout sessions, orders, UCP discovery,
AP2 payment mandates, capability negotiation, and MCP tool bindings.
"""

from __future__ import annotations

import enum
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Common value objects
# ---------------------------------------------------------------------------


class Address(BaseModel):
    """Postal address."""

    full_name: str
    line1: str
    line2: str | None = None
    city: str
    state: str
    postal_code: str
    country: str = "US"
    phone: str | None = None


class Money(BaseModel):
    """Monetary amount with currency."""

    amount: float
    currency: str = "USD"


# ---------------------------------------------------------------------------
# Product catalog
# ---------------------------------------------------------------------------


class ProductCategory(str, enum.Enum):
    """Product category taxonomy."""

    LAPTOPS = "laptops"
    KEYBOARDS = "keyboards"
    MICE = "mice"
    MONITORS = "monitors"
    ACCESSORIES = "accessories"


class Product(BaseModel):
    """A product in the merchant catalog."""

    id: str
    name: str
    description: str
    price: Money
    category: ProductCategory
    brand: str
    stock: int
    image_url: str
    specs: dict[str, str] = Field(default_factory=dict)
    rating: float = 0.0
    review_count: int = 0
    created_at: datetime = Field(default_factory=datetime.utcnow)


class SearchResult(BaseModel):
    """Paginated product search result."""

    products: list[Product]
    total: int
    offset: int
    limit: int
    query: str | None = None


# ---------------------------------------------------------------------------
# Checkout
# ---------------------------------------------------------------------------


class LineItem(BaseModel):
    """A single item in a checkout session."""

    product_id: str
    product_name: str
    quantity: int = 1
    unit_price: Money
    total_price: Money | None = None

    def model_post_init(self, __context: Any) -> None:
        """Compute line-item total if not provided."""
        if self.total_price is None:
            self.total_price = Money(
                amount=round(self.unit_price.amount * self.quantity, 2),
                currency=self.unit_price.currency,
            )


class ShippingOption(BaseModel):
    """A shipping method offered by the merchant."""

    id: str
    name: str
    description: str
    price: Money
    estimated_days_min: int
    estimated_days_max: int


class DiscountCode(BaseModel):
    """A validated discount / coupon code."""

    code: str
    description: str
    discount_type: str  # "percentage" | "fixed" | "free_shipping"
    value: float  # percentage (0-100) or fixed dollar amount
    max_discount: float | None = None  # cap for percentage discounts
    min_order: float | None = None  # minimum subtotal


class AppliedDiscount(BaseModel):
    """Record of a discount applied to a checkout session."""

    code: str
    description: str
    discount_type: str
    discount_amount: float


class CheckoutState(str, enum.Enum):
    """Checkout session lifecycle states."""

    INCOMPLETE = "incomplete"
    REQUIRES_ESCALATION = "requires_escalation"
    READY_FOR_COMPLETE = "ready_for_complete"
    COMPLETED = "completed"
    ABANDONED = "abandoned"


class CheckoutSession(BaseModel):
    """A full checkout session with state tracking."""

    id: str
    state: CheckoutState = CheckoutState.INCOMPLETE
    line_items: list[LineItem] = Field(default_factory=list)
    shipping_address: Address | None = None
    billing_address: Address | None = None
    selected_shipping: ShippingOption | None = None
    applied_discount: AppliedDiscount | None = None

    # Computed totals
    subtotal: Money = Field(default_factory=lambda: Money(amount=0.0))
    tax: Money = Field(default_factory=lambda: Money(amount=0.0))
    shipping_cost: Money = Field(default_factory=lambda: Money(amount=0.0))
    discount_amount: Money = Field(default_factory=lambda: Money(amount=0.0))
    total: Money = Field(default_factory=lambda: Money(amount=0.0))

    # Metadata
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    completed_at: datetime | None = None
    order_id: str | None = None

    # Escalation tracking
    escalation_reasons: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Orders
# ---------------------------------------------------------------------------


class OrderState(str, enum.Enum):
    """Order lifecycle states."""

    CONFIRMED = "confirmed"
    PROCESSING = "processing"
    SHIPPED = "shipped"
    DELIVERED = "delivered"
    CANCELLED = "cancelled"


class OrderStatusEvent(BaseModel):
    """An event in the order's tracking history."""

    event_type: str
    message: str
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    metadata: dict[str, Any] = Field(default_factory=dict)


class Order(BaseModel):
    """A completed order."""

    id: str
    checkout_session_id: str
    state: OrderState = OrderState.CONFIRMED
    line_items: list[LineItem] = Field(default_factory=list)
    shipping_address: Address | None = None
    billing_address: Address | None = None
    selected_shipping: ShippingOption | None = None
    applied_discount: AppliedDiscount | None = None

    # Totals (copied from completed checkout)
    subtotal: Money = Field(default_factory=lambda: Money(amount=0.0))
    tax: Money = Field(default_factory=lambda: Money(amount=0.0))
    shipping_cost: Money = Field(default_factory=lambda: Money(amount=0.0))
    discount_amount: Money = Field(default_factory=lambda: Money(amount=0.0))
    total: Money = Field(default_factory=lambda: Money(amount=0.0))

    # Tracking
    tracking_number: str | None = None
    carrier: str | None = None
    tracking_url: str | None = None
    history: list[OrderStatusEvent] = Field(default_factory=list)

    # Timestamps
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    shipped_at: datetime | None = None
    delivered_at: datetime | None = None
    cancelled_at: datetime | None = None


# ---------------------------------------------------------------------------
# UCP discovery
# ---------------------------------------------------------------------------


class UCPCapability(BaseModel):
    """A UCP capability declared in the manifest."""

    id: str
    version: str
    description: str
    schema_url: str | None = None


class UCPExtension(BaseModel):
    """A UCP extension (e.g. fulfillment, discounts)."""

    id: str
    version: str
    description: str
    schema_url: str | None = None


class UCPPaymentHandler(BaseModel):
    """A payment handler declared in the UCP manifest."""

    id: str
    name: str
    description: str
    handler_url: str
    supported_currencies: list[str] = Field(default_factory=lambda: ["USD"])
    config: dict[str, Any] = Field(default_factory=dict)


class UCPManifest(BaseModel):
    """The ``/.well-known/ucp`` discovery document."""

    spec_version: str
    merchant_name: str
    merchant_domain: str
    merchant_id: str
    base_url: str
    capabilities: list[UCPCapability]
    extensions: list[UCPExtension]
    payment_handlers: list[UCPPaymentHandler]
    endpoints: dict[str, str]
    metadata: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Capability negotiation
# ---------------------------------------------------------------------------


class NegotiationRequest(BaseModel):
    """Agent-submitted capability profile for negotiation."""

    agent_id: str
    agent_name: str
    requested_capabilities: list[str] = Field(default_factory=list)
    requested_extensions: list[str] = Field(default_factory=list)
    supported_payment_handlers: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class NegotiationResult(BaseModel):
    """Result of server-selects capability negotiation."""

    negotiation_id: str
    agent_id: str
    agreed_capabilities: list[UCPCapability]
    agreed_extensions: list[UCPExtension]
    agreed_payment_handlers: list[UCPPaymentHandler]
    session_endpoint: str
    metadata: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# AP2 payment mandates
# ---------------------------------------------------------------------------


class MandateType(str, enum.Enum):
    """AP2 mandate types."""

    CART = "cart"
    INTENT = "intent"


class CartMandate(BaseModel):
    """AP2 cart-level payment mandate.

    A cart mandate authorises a specific amount for a specific merchant
    tied to a checkout session.
    """

    mandate_id: str
    mandate_type: MandateType = MandateType.CART
    checkout_session_id: str
    merchant_id: str
    amount: Money
    currency: str = "USD"
    issued_at: datetime = Field(default_factory=datetime.utcnow)
    expires_at: datetime | None = None
    signature: str = ""  # base64-encoded ECDSA P-256 signature
    key_id: str = ""  # ID of the signing key


class IntentConstraints(BaseModel):
    """Constraints on an intent-level mandate."""

    max_amount: Money | None = None
    allowed_merchants: list[str] = Field(default_factory=list)
    allowed_categories: list[str] = Field(default_factory=list)
    time_window_seconds: int | None = None


class IntentMandate(BaseModel):
    """AP2 intent-level payment mandate.

    An intent mandate grants broader spending authority within constraints.
    """

    mandate_id: str
    mandate_type: MandateType = MandateType.INTENT
    agent_id: str
    constraints: IntentConstraints
    issued_at: datetime = Field(default_factory=datetime.utcnow)
    expires_at: datetime | None = None
    signature: str = ""
    key_id: str = ""


class MandateVerificationResult(BaseModel):
    """Result of verifying an AP2 payment mandate."""

    valid: bool
    mandate_id: str
    mandate_type: MandateType
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    verified_at: datetime = Field(default_factory=datetime.utcnow)


class KeyPairInfo(BaseModel):
    """Metadata about a generated ECDSA P-256 key pair."""

    key_id: str
    algorithm: str = "ES256"
    curve: str = "P-256"
    created_at: datetime = Field(default_factory=datetime.utcnow)
    public_key_jwk: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Webhooks
# ---------------------------------------------------------------------------


class WebhookRegistration(BaseModel):
    """A registered webhook endpoint."""

    id: str
    url: str
    events: list[str]
    created_at: datetime = Field(default_factory=datetime.utcnow)
    active: bool = True


class WebhookPayload(BaseModel):
    """Payload delivered to a webhook endpoint."""

    webhook_id: str
    event_type: str
    order_id: str
    payload: dict[str, Any]
    delivered_at: datetime = Field(default_factory=datetime.utcnow)


# ---------------------------------------------------------------------------
# MCP tool bindings
# ---------------------------------------------------------------------------


class MCPToolDefinition(BaseModel):
    """An MCP tool definition with JSON Schema for its inputs."""

    name: str
    description: str
    inputSchema: dict[str, Any]  # noqa: N815 -- matches MCP spec field name


class MCPToolResult(BaseModel):
    """Result of executing an MCP tool."""

    tool_name: str
    success: bool
    result: Any = None
    error: str | None = None
