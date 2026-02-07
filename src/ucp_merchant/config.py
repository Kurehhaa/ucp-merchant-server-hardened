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
    ap2_mandate_ttl_seconds: int = 3600

    # MCP tool bindings
    mcp_enabled: bool = True

    # Tax configuration
    tax_rate: float = 0.0875  # 8.75%

    # Webhook settings
    webhook_timeout_seconds: float = 10.0
    webhook_max_retries: int = 3


def get_settings() -> UCPMerchantSettings:
    """Return a settings instance (reads from env / .env on each call)."""
    return UCPMerchantSettings()
