"""Entry point, settings, helpers and mandate utilities."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from ucp_merchant import main as main_module
from ucp_merchant.config import UCPMerchantSettings, get_settings
from ucp_merchant.models import CartMandate, IntentConstraints, IntentMandate, MandateType, Money
from ucp_merchant.payments.mandate_models import is_mandate_expired, mandate_summary
from ucp_merchant.utils import utcnow


class TestUtcnow:
    def test_is_naive_and_current(self):
        now = utcnow()
        assert now.tzinfo is None
        assert abs(now - datetime.now(UTC).replace(tzinfo=None)) < timedelta(seconds=2)

    def test_matches_the_naive_utc_the_models_store(self):
        from ucp_merchant.models import OrderStatusEvent

        event = OrderStatusEvent(event_type="x", message="m")
        assert event.timestamp.tzinfo is None
        assert abs(event.timestamp - utcnow()) < timedelta(seconds=2)


class TestSettings:
    def test_defaults_are_the_safe_ones(self):
        settings = UCPMerchantSettings(_env_file=None)
        assert settings.admin_api_key == ""
        assert settings.ap2_require_mandate is True
        assert settings.ap2_demo_mode is False
        assert settings.webhook_allow_private_hosts is False
        assert settings.expose_error_details is False
        assert settings.cors_origins == []

    def test_environment_variables_override(self, monkeypatch):
        monkeypatch.setenv("PORT", "8000")
        monkeypatch.setenv("HOST", "127.0.0.1")
        monkeypatch.setenv("ADMIN_API_KEY", "k")
        monkeypatch.setenv("AP2_DEMO_MODE", "true")
        settings = get_settings()
        assert (settings.port, settings.host) == (8000, "127.0.0.1")
        assert settings.admin_api_key == "k"
        assert settings.ap2_demo_mode is True


class TestMain:
    def test_main_starts_uvicorn_with_the_configured_address(self, monkeypatch):
        captured = {}
        monkeypatch.setenv("PORT", "8123")
        monkeypatch.setenv("HOST", "127.0.0.1")
        monkeypatch.setattr(
            main_module.uvicorn, "run", lambda app, **kw: captured.update(app=app, **kw)
        )

        main_module.main()

        assert captured["port"] == 8123
        assert captured["host"] == "127.0.0.1"
        assert captured["app"].title.startswith("UCP Merchant Server")


def _cart_mandate(**overrides) -> CartMandate:
    now = utcnow()
    fields = {
        "mandate_id": "m1",
        "checkout_session_id": "cs_1",
        "merchant_id": "merchant",
        "amount": Money(amount=10.0),
        "currency": "USD",
        "issued_at": now,
        "expires_at": now + timedelta(hours=1),
        "key_id": "key_1",
        "signature": "sig",
    }
    fields.update(overrides)
    return CartMandate(**fields)


class TestMandateModels:
    def test_expiry(self):
        assert is_mandate_expired(_cart_mandate(expires_at=utcnow() - timedelta(seconds=1)))
        assert not is_mandate_expired(_cart_mandate())
        assert not is_mandate_expired(_cart_mandate(expires_at=None))

    def test_cart_summary(self):
        summary = mandate_summary(_cart_mandate())
        assert summary["type"] == "cart"
        assert summary["amount"] == "10.00 USD"
        assert summary["has_signature"] is True
        assert summary["checkout_session_id"] == "cs_1"

    def test_cart_summary_without_expiry_or_signature(self):
        summary = mandate_summary(_cart_mandate(expires_at=None, signature=""))
        assert summary["expires_at"] == "none"
        assert summary["has_signature"] is False

    @pytest.mark.parametrize(
        ("merchants", "expected"),
        [(["a", "b"], "a, b"), ([], "any")],
    )
    def test_intent_summary(self, merchants, expected):
        now = utcnow()
        mandate = IntentMandate(
            mandate_id="i1",
            mandate_type=MandateType.INTENT,
            agent_id="agent",
            constraints=IntentConstraints(
                max_amount=Money(amount=50.0), allowed_merchants=merchants
            ),
            issued_at=now,
            expires_at=now + timedelta(hours=1),
            key_id="key_1",
        )
        summary = mandate_summary(mandate)
        assert summary["type"] == "intent"
        assert summary["agent_id"] == "agent"
        assert summary["max_amount"] == "50.00"
        assert summary["allowed_merchants"] == expected
