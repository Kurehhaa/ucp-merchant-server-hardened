"""Test helpers that behave like a real AP2 shopping agent.

The agent generates its own P-256 key, registers **only the public JWK** with
the merchant, and signs mandates locally.  The merchant never sees the private
key, which is the property the verification tests rely on.
"""

from __future__ import annotations

import base64
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from cryptography.hazmat.primitives.asymmetric import ec

from ucp_merchant.models import CartMandate, CheckoutSession, Money
from ucp_merchant.payments.ap2_verification import compute_cart_hash, sign_cart_mandate

if TYPE_CHECKING:
    from httpx import AsyncClient, Response

SHIPPING_ADDRESS = {
    "full_name": "Test User",
    "line1": "123 Main St",
    "city": "San Francisco",
    "state": "CA",
    "postal_code": "94105",
    "country": "US",
}


def _b64url(value: int) -> str:
    raw = value.to_bytes(32, byteorder="big")
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


class AgentSimulator:
    """A shopping agent that keeps its private key to itself."""

    def __init__(self, merchant_id: str) -> None:
        self.merchant_id = merchant_id
        self.private_key = ec.generate_private_key(ec.SECP256R1())
        self.key_id: str | None = None
        self._counter = 0

    @property
    def public_jwk(self) -> dict[str, str]:
        numbers = self.private_key.public_key().public_numbers()
        return {
            "kty": "EC",
            "crv": "P-256",
            "x": _b64url(numbers.x),
            "y": _b64url(numbers.y),
        }

    async def register(self, client: AsyncClient) -> str:
        """Register the public key with the merchant (idempotent)."""
        resp = await client.post(
            "/api/v1/payments/keys/register", json={"jwk": self.public_jwk}
        )
        assert resp.status_code == 200, resp.text
        self.key_id = resp.json()["key_id"]
        return self.key_id

    def cart_mandate(
        self,
        session_json: dict[str, Any],
        *,
        ttl_seconds: int = 300,
        issued_at: datetime | None = None,
        sign: bool = True,
        **overrides: Any,
    ) -> CartMandate:
        """Build and sign a cart mandate for a session the agent has seen."""
        assert self.key_id is not None, "register() the agent first"
        session = CheckoutSession.model_validate(session_json)
        issued = issued_at or datetime.now(UTC).replace(tzinfo=None)
        self._counter += 1
        fields: dict[str, Any] = {
            "mandate_id": f"mandate_test_{id(self)}_{self._counter}",
            "checkout_session_id": session.id,
            "merchant_id": self.merchant_id,
            "amount": Money(amount=session.total.amount, currency=session.total.currency),
            "currency": session.total.currency,
            "cart_hash": compute_cart_hash(session),
            "issued_at": issued,
            "expires_at": issued + timedelta(seconds=ttl_seconds),
            "key_id": self.key_id,
        }
        fields.update(overrides)
        mandate = CartMandate(**fields)
        if sign:
            sign_cart_mandate(mandate, self.private_key, self.key_id)
        return mandate


async def create_ready_session(
    client: AsyncClient, items: list[dict[str, Any]] | None = None
) -> dict[str, Any]:
    """Create a checkout and fill it in until it is ready to complete."""
    resp = await client.post(
        "/api/v1/checkout",
        json={"items": items or [{"product_id": "laptop-001", "quantity": 1}]},
    )
    assert resp.status_code == 200, resp.text
    session_id = resp.json()["id"]
    await client.patch(
        f"/api/v1/checkout/{session_id}", json={"shipping_address": SHIPPING_ADDRESS}
    )
    resp = await client.patch(
        f"/api/v1/checkout/{session_id}", json={"shipping_option_id": "shipping_standard"}
    )
    assert resp.json()["state"] == "ready_for_complete", resp.text
    return resp.json()


async def complete_with_mandate(
    client: AsyncClient, agent: AgentSimulator, session_id: str
) -> Response:
    """Sign a mandate for the session's current contents and complete it."""
    if agent.key_id is None:
        await agent.register(client)
    session_json = (await client.get(f"/api/v1/checkout/{session_id}")).json()
    mandate = agent.cart_mandate(session_json)
    return await client.post(
        f"/api/v1/checkout/{session_id}/complete",
        json={"cart_mandate": mandate.model_dump(mode="json")},
    )
