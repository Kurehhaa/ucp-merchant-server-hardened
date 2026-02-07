"""Shared test fixtures for UCP Merchant Server."""

import pytest

from ucp_merchant.config import UCPMerchantSettings
from ucp_merchant.api import create_app


@pytest.fixture
def settings():
    """Create test settings."""
    return UCPMerchantSettings(
        environment="testing",
    )


@pytest.fixture
def app(settings):
    """Create FastAPI app for testing."""
    return create_app(settings)
