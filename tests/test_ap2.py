"""Tests for AP2 mandate verification, key registration and single-use enforcement."""

from __future__ import annotations

import base64
from datetime import UTC, datetime, timedelta, timezone

import pytest
from httpx import ASGITransport, AsyncClient

from tests.ap2_helpers import (
    SHIPPING_ADDRESS,
    AgentSimulator,
    complete_with_mandate,
    create_ready_session,
)
from ucp_merchant.api import create_app
from ucp_merchant.config import UCPMerchantSettings
from ucp_merchant.models import (
    CheckoutSession,
    IntentConstraints,
    IntentMandate,
    MandateType,
    Money,
)
from ucp_merchant.payments.ap2_verification import (
    MandateRejected,
    MandateReplayGuard,
    canonical_amount,
    compute_cart_hash,
    sign_intent_mandate,
)

VERIFY_CART = "/api/v1/payments/verify/cart"
VERIFY_INTENT = "/api/v1/payments/verify/intent"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_app(**overrides):
    application = create_app(UCPMerchantSettings(environment="testing", **overrides))
    # Same workaround as tests/test_api.py (OrderTracker structlog kwarg clash).
    application.state.order_manager._tracker = None
    return application


@pytest.fixture
def app():
    return _make_app()


@pytest.fixture
async def client(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


@pytest.fixture
async def agent(app, client):
    registered = AgentSimulator(merchant_id=app.state.settings.merchant_id)
    await registered.register(client)
    return registered


@pytest.fixture
async def session_json(client):
    return await create_ready_session(client)


def _errors(resp) -> list[str]:
    assert resp.status_code == 200, resp.text
    return resp.json()["errors"]


def _has(errors: list[str], needle: str) -> bool:
    return any(needle.lower() in e.lower() for e in errors)


# ---------------------------------------------------------------------------
# Key registration
# ---------------------------------------------------------------------------


class TestKeyRegistration:
    async def test_register_returns_thumbprint_key_id(self, client):
        agent = AgentSimulator("merchant_techvault_001")
        resp = await client.post("/api/v1/payments/keys/register", json={"jwk": agent.public_jwk})
        assert resp.status_code == 200
        data = resp.json()
        assert data["key_id"].startswith("jkt_")
        assert "d" not in data["public_key_jwk"]

    async def test_registration_is_idempotent(self, client):
        agent = AgentSimulator("merchant_techvault_001")
        first = await agent.register(client)
        second = await agent.register(client)
        assert first == second
        keys = (await client.get("/api/v1/payments/keys")).json()["keys"]
        assert len(keys) == 1

    async def test_rejects_private_key_material(self, client):
        agent = AgentSimulator("merchant_techvault_001")
        jwk = {**agent.public_jwk, "d": "c2VjcmV0"}
        resp = await client.post("/api/v1/payments/keys/register", json={"jwk": jwk})
        assert resp.status_code == 400
        assert "private" in resp.json()["detail"].lower()

    @pytest.mark.parametrize(
        "patch",
        [
            {"crv": "P-384"},
            {"kty": "RSA"},
            {"alg": "RS256"},
            {"use": "enc"},
            {"x": "not base64!!"},
            {"x": "AQ"},  # decodes to 1 byte, not 32
            {"x": None},
        ],
    )
    async def test_rejects_malformed_jwk(self, client, patch):
        jwk = {**AgentSimulator("m").public_jwk, **patch}
        jwk = {k: v for k, v in jwk.items() if v is not None}
        resp = await client.post("/api/v1/payments/keys/register", json={"jwk": jwk})
        assert resp.status_code == 400

    async def test_rejects_point_not_on_curve(self, client):
        one = base64.urlsafe_b64encode((1).to_bytes(32, "big")).rstrip(b"=").decode()
        jwk = {"kty": "EC", "crv": "P-256", "x": one, "y": one}
        resp = await client.post("/api/v1/payments/keys/register", json={"jwk": jwk})
        assert resp.status_code == 400
        assert "curve" in resp.json()["detail"]

    async def test_server_never_holds_a_registered_agents_private_key(self, app, client):
        agent = AgentSimulator("merchant_techvault_001")
        key_id = await agent.register(client)
        with pytest.raises(KeyError):
            app.state.key_manager.get_private_key(key_id)

    async def test_unregistered_key_is_rejected(self, client, session_json):
        stranger = AgentSimulator("merchant_techvault_001")
        stranger.key_id = "jkt_never_registered"
        mandate = stranger.cart_mandate(session_json)
        resp = await client.post(VERIFY_CART, json={"mandate": mandate.model_dump(mode="json")})
        assert _has(_errors(resp), "signature")


# ---------------------------------------------------------------------------
# What the signature covers
# ---------------------------------------------------------------------------


class TestSignatureCoverage:
    async def _verify(self, client, mandate_json, session_id=None):
        body = {"mandate": mandate_json}
        if session_id:
            body["session_id"] = session_id
        return await client.post(VERIFY_CART, json=body)

    async def test_valid_mandate_passes(self, client, agent, session_json):
        mandate = agent.cart_mandate(session_json).model_dump(mode="json")
        resp = await self._verify(client, mandate, session_json["id"])
        assert resp.json()["valid"] is True, resp.json()

    @pytest.mark.parametrize(
        "field, tamper",
        [
            ("expires_at", lambda m: m.update(
                expires_at=(datetime.fromisoformat(m["expires_at"]) + timedelta(hours=2)).isoformat()
            )),
            ("issued_at", lambda m: m.update(
                issued_at=(datetime.fromisoformat(m["issued_at"]) - timedelta(minutes=1)).isoformat()
            )),
            ("amount", lambda m: m.update(amount={"amount": 1.0, "currency": "USD"})),
            ("cart_hash", lambda m: m.update(cart_hash="0" * 64)),
            ("mandate_id", lambda m: m.update(mandate_id="mandate_forged")),
            ("checkout_session_id", lambda m: m.update(checkout_session_id="cs_other")),
        ],
    )
    async def test_tampering_with_any_signed_field_breaks_the_signature(
        self, client, agent, session_json, field, tamper
    ):
        mandate = agent.cart_mandate(session_json).model_dump(mode="json")
        tamper(mandate)
        resp = await self._verify(client, mandate)
        assert _has(_errors(resp), "invalid ecdsa"), f"tampering with {field} was not detected"

    async def test_signature_from_a_different_key_is_rejected(self, client, agent, session_json):
        impostor = AgentSimulator(agent.merchant_id)
        await impostor.register(client)
        mandate = impostor.cart_mandate(session_json)
        mandate.key_id = agent.key_id  # claim to be the honest agent
        resp = await self._verify(client, mandate.model_dump(mode="json"))
        assert _has(_errors(resp), "invalid ecdsa")

    async def test_missing_signature_is_rejected(self, client, agent, session_json):
        mandate = agent.cart_mandate(session_json, sign=False)
        resp = await self._verify(client, mandate.model_dump(mode="json"))
        assert _has(_errors(resp), "invalid ecdsa")

    async def test_wrong_merchant_is_rejected(self, client, agent, session_json):
        mandate = agent.cart_mandate(session_json, merchant_id="merchant_someone_else")
        assert _has(_errors(await self._verify(client, mandate.model_dump(mode="json"))), "merchant")

    @pytest.mark.parametrize("zone", [UTC, timezone(timedelta(hours=5))])
    async def test_timezone_aware_timestamps_are_accepted(self, client, agent, session_json, zone):
        mandate = agent.cart_mandate(session_json)
        as_json = mandate.model_dump(mode="json")
        for field in ("issued_at", "expires_at"):
            moment = datetime.fromisoformat(as_json[field]).replace(tzinfo=UTC)
            as_json[field] = moment.astimezone(zone).isoformat()
        resp = await self._verify(client, as_json, session_json["id"])
        assert resp.status_code == 200
        assert resp.json()["valid"] is True, resp.json()


# ---------------------------------------------------------------------------
# Validity window
# ---------------------------------------------------------------------------


class TestValidityWindow:
    async def _errors_for(self, client, agent, session_json, **kwargs):
        mandate = agent.cart_mandate(session_json, **kwargs)
        return _errors(
            await client.post(VERIFY_CART, json={"mandate": mandate.model_dump(mode="json")})
        )

    async def test_expiry_is_required(self, client, agent, session_json):
        assert _has(await self._errors_for(client, agent, session_json, expires_at=None), "expiry")

    async def test_expired_mandate_is_rejected(self, client, agent, session_json):
        past = datetime.now(UTC).replace(tzinfo=None) - timedelta(hours=1)
        errors = await self._errors_for(
            client, agent, session_json, issued_at=past, ttl_seconds=60
        )
        assert _has(errors, "expired")

    async def test_overlong_lifetime_is_rejected(self, client, agent, session_json):
        errors = await self._errors_for(client, agent, session_json, ttl_seconds=3 * 86400)
        assert _has(errors, "lifetime exceeds")

    async def test_future_dated_mandate_is_rejected(self, client, agent, session_json):
        future = datetime.now(UTC).replace(tzinfo=None) + timedelta(hours=1)
        assert _has(
            await self._errors_for(client, agent, session_json, issued_at=future),
            "future",
        )

    async def test_expiry_before_issue_is_rejected(self, client, agent, session_json):
        errors = await self._errors_for(client, agent, session_json, ttl_seconds=-30)
        assert _has(errors, "after issued_at")


# ---------------------------------------------------------------------------
# Binding to the cart
# ---------------------------------------------------------------------------


class TestCartBinding:
    async def _complete(self, client, mandate):
        return await client.post(
            f"/api/v1/checkout/{mandate.checkout_session_id}/complete",
            json={"cart_mandate": mandate.model_dump(mode="json")},
        )

    async def test_mandate_for_another_session_is_rejected(self, client, agent, session_json):
        other = await create_ready_session(client)
        mandate = agent.cart_mandate(other)
        mandate_for_wrong_session = mandate.model_copy(
            update={"checkout_session_id": session_json["id"]}
        )
        resp = await self._complete(client, mandate_for_wrong_session)
        assert resp.status_code == 402

    async def test_changing_the_shipping_address_after_signing_is_rejected(
        self, client, agent, session_json
    ):
        mandate = agent.cart_mandate(session_json)
        await client.patch(
            f"/api/v1/checkout/{session_json['id']}",
            json={"shipping_address": {**SHIPPING_ADDRESS, "line1": "666 Attacker Ave"}},
        )
        resp = await self._complete(client, mandate)
        assert resp.status_code == 402
        assert _has(resp.json()["detail"]["errors"], "cart contents changed")
        state = (await client.get(f"/api/v1/checkout/{session_json['id']}")).json()["state"]
        assert state != "completed"

    async def test_adding_items_after_signing_is_rejected(self, client, agent, session_json):
        mandate = agent.cart_mandate(session_json)
        await client.patch(
            f"/api/v1/checkout/{session_json['id']}",
            json={"add_items": [{"product_id": "laptop-001", "quantity": 1}]},
        )
        resp = await self._complete(client, mandate)
        assert resp.status_code == 402
        assert _has(resp.json()["detail"]["errors"], "mismatch") or _has(
            resp.json()["detail"]["errors"], "cart contents changed"
        )

    async def test_mandate_without_cart_hash_is_rejected(self, client, agent, session_json):
        mandate = agent.cart_mandate(session_json, cart_hash="")
        resp = await self._complete(client, mandate)
        assert resp.status_code == 402
        assert _has(resp.json()["detail"]["errors"], "cart_hash")

    def test_cart_hash_ignores_item_order_but_not_contents(self, session_json):
        session = CheckoutSession.model_validate(session_json)
        baseline = compute_cart_hash(session)

        reordered = session.model_copy(deep=True)
        reordered.line_items = list(reversed(reordered.line_items))
        assert compute_cart_hash(reordered) == baseline

        moved = session.model_copy(deep=True)
        moved.shipping_address.city = "Elsewhere"
        assert compute_cart_hash(moved) != baseline

        bigger = session.model_copy(deep=True)
        bigger.line_items[0].quantity += 1
        assert compute_cart_hash(bigger) != baseline

    def test_canonical_amount_is_stable(self):
        assert canonical_amount(99.9) == "99.90"
        assert canonical_amount(1604.59) == "1604.59"
        assert canonical_amount(0.1 + 0.2) == "0.30"
        assert canonical_amount(2.675) == "2.68"


# ---------------------------------------------------------------------------
# Checkout completion policy
# ---------------------------------------------------------------------------


class TestCompletionPolicy:
    async def test_completion_without_mandate_is_refused(self, client, session_json):
        resp = await client.post(f"/api/v1/checkout/{session_json['id']}/complete")
        assert resp.status_code == 402
        assert resp.json()["detail"]["error"] == "payment_mandate_required"

    async def test_completion_with_valid_mandate_creates_order(self, client, agent, session_json):
        resp = await complete_with_mandate(client, agent, session_json["id"])
        assert resp.status_code == 200, resp.text
        assert resp.json()["session"]["state"] == "completed"
        assert resp.json()["order"]["state"] == "confirmed"

    async def test_rejected_mandate_leaves_session_completable(self, client, agent, session_json):
        mandate = agent.cart_mandate(session_json)
        forged = mandate.model_dump(mode="json")
        forged["amount"] = {"amount": 0.01, "currency": "USD"}
        resp = await client.post(
            f"/api/v1/checkout/{session_json['id']}/complete", json={"cart_mandate": forged}
        )
        assert resp.status_code == 402
        assert resp.json()["detail"]["error"] == "payment_mandate_rejected"

        # The honest mandate still works afterwards.
        resp = await client.post(
            f"/api/v1/checkout/{session_json['id']}/complete",
            json={"cart_mandate": mandate.model_dump(mode="json")},
        )
        assert resp.status_code == 200

    async def test_incomplete_cart_is_refused_without_burning_the_mandate(
        self, app, client, agent
    ):
        created = await client.post(
            "/api/v1/checkout", json={"items": [{"product_id": "laptop-001", "quantity": 1}]}
        )
        incomplete = created.json()
        mandate = agent.cart_mandate(incomplete)
        resp = await client.post(
            f"/api/v1/checkout/{incomplete['id']}/complete",
            json={"cart_mandate": mandate.model_dump(mode="json")},
        )
        assert resp.status_code == 400
        guard = app.state.mandate_verifier.replay_guard
        assert not guard.is_consumed(mandate.key_id, mandate.mandate_id)

    async def test_unknown_session_is_404(self, client):
        resp = await client.post("/api/v1/checkout/cs_missing/complete")
        assert resp.status_code == 404

    async def test_mandate_requirement_can_be_disabled(self):
        application = _make_app(ap2_require_mandate=False)
        async with AsyncClient(
            transport=ASGITransport(app=application), base_url="http://test"
        ) as c:
            session = await create_ready_session(c)
            resp = await c.post(f"/api/v1/checkout/{session['id']}/complete")
            assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Single use / replay
# ---------------------------------------------------------------------------


class TestSingleUse:
    async def test_used_mandate_is_reported_as_used(self, client, agent, session_json):
        mandate = agent.cart_mandate(session_json)
        as_json = mandate.model_dump(mode="json")
        before = await client.post(VERIFY_CART, json={"mandate": as_json})
        assert before.json()["valid"] is True

        resp = await client.post(
            f"/api/v1/checkout/{session_json['id']}/complete", json={"cart_mandate": as_json}
        )
        assert resp.status_code == 200

        after = await client.post(VERIFY_CART, json={"mandate": as_json})
        assert after.json()["valid"] is False
        assert _has(after.json()["errors"], "already been used")

    async def test_replaying_a_completed_checkout_is_refused(self, client, agent, session_json):
        mandate = agent.cart_mandate(session_json).model_dump(mode="json")
        url = f"/api/v1/checkout/{session_json['id']}/complete"
        assert (await client.post(url, json={"cart_mandate": mandate})).status_code == 200
        assert (await client.post(url, json={"cart_mandate": mandate})).status_code in (400, 409)
        orders = (await client.get("/api/v1/orders")).json()["orders"]
        assert len(orders) == 1

    async def test_authorize_checkout_twice_is_a_409(self, app, client, agent, session_json):
        verifier = app.state.mandate_verifier
        session = app.state.checkout_manager.get_session(session_json["id"])
        mandate = agent.cart_mandate(session_json)

        verifier.authorize_checkout(session, mandate)
        with pytest.raises(MandateRejected) as excinfo:
            verifier.authorize_checkout(session, mandate)
        assert excinfo.value.status_code == 409

    async def test_released_mandate_can_be_used_again(self, app, client, agent, session_json):
        verifier = app.state.mandate_verifier
        session = app.state.checkout_manager.get_session(session_json["id"])
        mandate = agent.cart_mandate(session_json)

        verifier.authorize_checkout(session, mandate)
        verifier.release_authorization(mandate)
        verifier.authorize_checkout(session, mandate)  # does not raise

    async def test_invalid_mandate_is_a_402(self, app, client, agent, session_json):
        verifier = app.state.mandate_verifier
        session = app.state.checkout_manager.get_session(session_json["id"])
        mandate = agent.cart_mandate(session_json, sign=False)
        with pytest.raises(MandateRejected) as excinfo:
            verifier.authorize_checkout(session, mandate)
        assert excinfo.value.status_code == 402

    def test_guard_scopes_ids_per_key(self):
        guard = MandateReplayGuard()
        expiry = datetime.now(UTC).replace(tzinfo=None) + timedelta(minutes=5)
        assert guard.consume("key_a", "m1", expiry) is True
        assert guard.consume("key_a", "m1", expiry) is False
        # Someone else reusing the same mandate_id under their own key cannot burn it.
        assert guard.consume("key_b", "m1", expiry) is True

    def test_guard_forgets_expired_entries(self):
        guard = MandateReplayGuard()
        long_ago = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=1)
        assert guard.consume("key_a", "m1", long_ago) is True
        assert guard.is_consumed("key_a", "m1") is False


# ---------------------------------------------------------------------------
# MCP surface must not be a way around the policy
# ---------------------------------------------------------------------------


class TestMcpCompletion:
    URL = "/api/v1/mcp/tools/complete_checkout/execute"

    async def test_mcp_completion_requires_a_mandate(self, client, session_json):
        resp = await client.post(self.URL, json={"arguments": {"session_id": session_json["id"]}})
        body = resp.json()
        assert body["success"] is False
        assert "cart_mandate" in body["error"]
        state = (await client.get(f"/api/v1/checkout/{session_json['id']}")).json()["state"]
        assert state == "ready_for_complete"

    async def test_mcp_completion_rejects_forged_mandate(self, client, agent, session_json):
        forged = agent.cart_mandate(session_json).model_dump(mode="json")
        forged["amount"] = {"amount": 0.01, "currency": "USD"}
        resp = await client.post(
            self.URL, json={"arguments": {"session_id": session_json["id"], "cart_mandate": forged}}
        )
        assert resp.json()["success"] is False

    async def test_mcp_completion_with_valid_mandate_works_once(self, client, agent, session_json):
        mandate = agent.cart_mandate(session_json).model_dump(mode="json")
        args = {"session_id": session_json["id"], "cart_mandate": mandate}
        first = await client.post(self.URL, json={"arguments": args})
        assert first.json()["success"] is True, first.json()
        second = await client.post(self.URL, json={"arguments": args})
        assert second.json()["success"] is False


# ---------------------------------------------------------------------------
# Intent mandates
# ---------------------------------------------------------------------------


def _intent(agent: AgentSimulator, **overrides) -> IntentMandate:
    now = datetime.now(UTC).replace(tzinfo=None)
    fields = {
        "mandate_id": "intent_test_1",
        "mandate_type": MandateType.INTENT,
        "agent_id": "agent-under-test",
        "constraints": IntentConstraints(
            max_amount=Money(amount=500.0),
            allowed_merchants=[agent.merchant_id],
            allowed_categories=["keyboards"],
            time_window_seconds=3600,
        ),
        "issued_at": now,
        "expires_at": now + timedelta(hours=1),
    }
    fields.update(overrides)
    mandate = IntentMandate(**fields)
    return sign_intent_mandate(mandate, agent.private_key, agent.key_id)


class TestIntentMandates:
    async def _verify(self, client, mandate_json, **extra):
        return await client.post(VERIFY_INTENT, json={"mandate": mandate_json, **extra})

    async def test_valid_intent_mandate_passes(self, client, agent):
        resp = await self._verify(client, _intent(agent).model_dump(mode="json"))
        assert resp.json()["valid"] is True, resp.json()

    @pytest.mark.parametrize(
        "tamper",
        [
            lambda m: m["constraints"].update(time_window_seconds=10**7),
            lambda m: m["constraints"].update(allowed_categories=["laptops", "keyboards"]),
            lambda m: m["constraints"].update(max_amount={"amount": 50000.0, "currency": "USD"}),
            lambda m: m["constraints"].update(allowed_merchants=[]),
            lambda m: m.update(agent_id="someone-else"),
            lambda m: m.update(
                expires_at=(datetime.fromisoformat(m["expires_at"]) + timedelta(hours=5)).isoformat()
            ),
        ],
    )
    async def test_tampering_with_any_constraint_breaks_the_signature(
        self, client, agent, tamper
    ):
        mandate = _intent(agent).model_dump(mode="json")
        tamper(mandate)
        resp = await self._verify(client, mandate)
        assert _has(resp.json()["errors"], "invalid ecdsa")

    async def test_expiry_is_required(self, client, agent):
        mandate = _intent(agent, expires_at=None).model_dump(mode="json")
        assert _has((await self._verify(client, mandate)).json()["errors"], "expiry")

    async def test_amount_over_limit_is_rejected(self, client, agent):
        mandate = _intent(agent).model_dump(mode="json")
        resp = await self._verify(client, mandate, requested_amount=500.01)
        assert _has(resp.json()["errors"], "exceeds")

    async def test_amount_at_limit_is_accepted(self, client, agent):
        mandate = _intent(agent).model_dump(mode="json")
        resp = await self._verify(client, mandate, requested_amount=500.0)
        assert resp.json()["valid"] is True


# ---------------------------------------------------------------------------
# Demo mode
# ---------------------------------------------------------------------------


class TestDemoMode:
    @pytest.mark.parametrize(
        "path, body",
        [
            ("/api/v1/payments/keys", None),
            ("/api/v1/payments/test-mandate", {"session_id": "x", "key_id": "y"}),
            ("/api/v1/payments/test-intent-mandate", {"agent_id": "a", "key_id": "y"}),
        ],
    )
    async def test_server_side_signing_is_off_by_default(self, client, path, body):
        resp = await client.post(path, json=body) if body else await client.post(path)
        assert resp.status_code == 404
        assert "register" in resp.json()["detail"]

    async def test_demo_flow_still_completes_a_checkout(self):
        application = _make_app(ap2_demo_mode=True)
        async with AsyncClient(
            transport=ASGITransport(app=application), base_url="http://test"
        ) as c:
            key_id = (await c.post("/api/v1/payments/keys")).json()["key_id"]
            session = await create_ready_session(c)
            mandate = (
                await c.post(
                    "/api/v1/payments/test-mandate",
                    json={"session_id": session["id"], "key_id": key_id},
                )
            ).json()
            resp = await c.post(
                f"/api/v1/checkout/{session['id']}/complete", json={"cart_mandate": mandate}
            )
            assert resp.status_code == 200, resp.text

    async def test_demo_keys_cannot_sign_for_registered_agents(self):
        application = _make_app(ap2_demo_mode=True)
        async with AsyncClient(
            transport=ASGITransport(app=application), base_url="http://test"
        ) as c:
            agent = AgentSimulator(application.state.settings.merchant_id)
            key_id = await agent.register(c)
            session = await create_ready_session(c)
            resp = await c.post(
                "/api/v1/payments/test-mandate",
                json={"session_id": session["id"], "key_id": key_id},
            )
            assert resp.status_code == 404
