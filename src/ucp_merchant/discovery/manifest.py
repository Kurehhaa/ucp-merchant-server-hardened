"""UCP manifest generation for the ``/.well-known/ucp`` discovery endpoint.

The manifest advertises the merchant's capabilities, extensions, payment
handlers, and API endpoints so that UCP-compliant agents can automatically
discover and interact with the server.
"""

from __future__ import annotations

from ucp_merchant.config import UCPMerchantSettings
from ucp_merchant.models import (
    UCPCapability,
    UCPExtension,
    UCPManifest,
    UCPPaymentHandler,
)


def build_ucp_manifest(settings: UCPMerchantSettings) -> UCPManifest:
    """Build the full UCP discovery manifest from the server settings.

    Parameters
    ----------
    settings:
        The merchant server configuration.

    Returns
    -------
    UCPManifest
        A complete manifest suitable for serialisation at ``/.well-known/ucp``.
    """
    base = settings.ucp_base_url

    capabilities = [
        UCPCapability(
            id="dev.ucp.shopping.checkout",
            version="1.0.0",
            description=(
                "Full checkout lifecycle: create sessions, add items, "
                "set addresses, select shipping, apply discounts, and complete."
            ),
            schema_url=f"{base}/api/v1/schemas/checkout",
        ),
        UCPCapability(
            id="dev.ucp.shopping.orders",
            version="1.0.0",
            description=(
                "Order lifecycle management with real-time tracking, "
                "status updates, and fulfillment simulation."
            ),
            schema_url=f"{base}/api/v1/schemas/orders",
        ),
    ]

    extensions = [
        UCPExtension(
            id="dev.ucp.shopping.fulfillment",
            version="1.0.0",
            description=(
                "Shipping and fulfillment options including Standard, Express, "
                "Next Day, and Free shipping for qualifying orders."
            ),
            schema_url=f"{base}/api/v1/schemas/fulfillment",
        ),
        UCPExtension(
            id="dev.ucp.shopping.discount",
            version="1.0.0",
            description=(
                "Discount and coupon code support with percentage, fixed, "
                "and free-shipping discount types."
            ),
            schema_url=f"{base}/api/v1/schemas/discount",
        ),
    ]

    payment_handlers = [
        UCPPaymentHandler(
            id="dev.ucp.mock_payment",
            name="Mock Payment",
            description="Development / test payment handler that always succeeds.",
            handler_url=f"{base}/api/v1/payments/mock",
            supported_currencies=["USD"],
            config={"mode": "test", "auto_approve": True},
        ),
        UCPPaymentHandler(
            id="google.pay",
            name="Google Pay",
            description="Google Pay integration (mock configuration for demo).",
            handler_url=f"{base}/api/v1/payments/google-pay",
            supported_currencies=["USD", "EUR", "GBP"],
            config={
                "mode": "test",
                "gateway": "example",
                "gateway_merchant_id": settings.merchant_id,
            },
        ),
    ]

    endpoints = {
        "discovery": f"{base}/.well-known/ucp",
        "negotiate": f"{base}/api/v1/negotiate",
        "catalog_search": f"{base}/api/v1/catalog/search",
        "catalog_product": f"{base}/api/v1/catalog/products/{{product_id}}",
        "catalog_categories": f"{base}/api/v1/catalog/categories",
        "checkout_create": f"{base}/api/v1/checkout",
        "checkout_get": f"{base}/api/v1/checkout/{{session_id}}",
        "checkout_update": f"{base}/api/v1/checkout/{{session_id}}",
        "checkout_complete": f"{base}/api/v1/checkout/{{session_id}}/complete",
        "shipping_options": f"{base}/api/v1/checkout/{{session_id}}/shipping",
        "apply_discount": f"{base}/api/v1/checkout/{{session_id}}/discount",
        "payment_verify": f"{base}/api/v1/payments/verify",
        "payment_keys": f"{base}/api/v1/payments/keys",
        "order_get": f"{base}/api/v1/orders/{{order_id}}",
        "order_track": f"{base}/api/v1/orders/{{order_id}}/track",
        "webhooks": f"{base}/api/v1/webhooks",
        "mcp_tools": f"{base}/api/v1/mcp/tools",
        "mcp_execute": f"{base}/api/v1/mcp/tools/{{tool_name}}/execute",
    }

    return UCPManifest(
        spec_version=settings.ucp_spec_version,
        merchant_name=settings.merchant_name,
        merchant_domain=settings.merchant_domain,
        merchant_id=settings.merchant_id,
        base_url=base,
        capabilities=capabilities,
        extensions=extensions,
        payment_handlers=payment_handlers,
        endpoints=endpoints,
        metadata={
            "environment": settings.environment,
            "ap2_enabled": settings.ap2_enabled,
            "mcp_enabled": settings.mcp_enabled,
            "tax_rate": settings.tax_rate,
        },
    )
