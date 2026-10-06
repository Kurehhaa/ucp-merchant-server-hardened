"""Configuration management for the UCP Merchant Server.

Inherits shared settings from the common library and adds merchant-specific
configuration for UCP discovery, AP2 payments, and MCP tool bindings.
"""

from __future__ import annotations

from common.config import Settings as BaseSettings


class UCPMerchantSettings(BaseSettings):
    """UCP Merchant Server configuration.

    All values can be overridden via environment variables or a ``.env`` file.
    """

    # Service identity
    service_name: str = "ucp-merchant-server"
    service_version: str = "0.1.0"
    host: str = "0.0.0.0"
    port: int = 8011

    # Merchant information
    merchant_name: str = "TechVault Electronics"
    merchant_domain: str = "techvault.example.com"
    merchant_id: str = "merchant_techvault_001"

    # UCP specification
    ucp_spec_version: str = "2026-01-11"
    ucp_base_url: str = "http://localhost:8011"

    # AP2 payment mandates
    ap2_enabled: bool = True
    ap2_mandate_ttl_seconds: int = 3600  # TTL of demo-generated mandates
    # Reject mandates that live longer than this (issued_at -> expires_at).
    ap2_max_mandate_ttl_seconds: int = 86400
    # Checkout cannot be completed without a valid, unused cart mandate.
    ap2_require_mandate: bool = True
    # Enables the endpoints that generate keys / sign mandates server-side.
    # They hold private keys in memory, so keep this off outside demos.
    ap2_demo_mode: bool = False

    # MCP tool bindings
    mcp_enabled: bool = True

    # Tax configuration
    tax_rate: float = 0.0875  # 8.75%

    # Webhook settings
    webhook_timeout_seconds: float = 10.0
    webhook_max_retries: int = 3
    # Allow webhook URLs that are plain http or point at private / loopback
    # addresses.  Needed for local demos only: in any shared deployment this
    # lets a caller make the server send requests to its internal network.
    webhook_allow_private_hosts: bool = False

    # API security
    # Key required (header ``X-API-Key``) for merchant-admin endpoints: order
    # management, webhooks, key listing.  When empty those endpoints are
    # disabled rather than left open.
    admin_api_key: str = ""
    # Comma-separated browser origins allowed to call the API cross-origin.
    # Shopping agents are server-side clients and do not need CORS, so the
    # default allows none.
    cors_allow_origins: str = ""
    # Include exception text in 500 responses (never enable in production).
    expose_error_details: bool = False

    @property
    def cors_origins(self) -> list[str]:
        """``cors_allow_origins`` as a clean list."""
        return [o.strip() for o in self.cors_allow_origins.split(",") if o.strip()]


def get_settings() -> UCPMerchantSettings:
    """Return a settings instance (reads from env / .env on each call)."""
    return UCPMerchantSettings()
