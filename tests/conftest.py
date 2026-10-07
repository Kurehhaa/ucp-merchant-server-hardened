"""Shared test fixtures for UCP Merchant Server."""

import pytest

from ucp_merchant.api import create_app
from ucp_merchant.config import UCPMerchantSettings


@pytest.fixture(autouse=True)
def isolated_settings(monkeypatch, tmp_path):
    """Keep tests independent of the developer's own .env and environment.

    Settings read ``.env`` from the current directory and from environment
    variables, so a locally configured ADMIN_API_KEY would change behaviour
    the tests assume to be the default.
    """
    monkeypatch.chdir(tmp_path)
    for name in UCPMerchantSettings.model_fields:
        monkeypatch.delenv(name.upper(), raising=False)


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
