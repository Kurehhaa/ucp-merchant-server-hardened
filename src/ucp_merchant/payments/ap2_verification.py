"""AP2 mandate verification with ECDSA P-256 cryptography.

Verification model
------------------
* An agent signs a mandate with a private key it keeps, and registers the
  matching *public* key with the merchant (:class:`KeyManager`).  The merchant
  never needs, and never accepts, the agent's private key.
* The signature covers every field that grants authority: identity, amount,
  currency, validity window and -- for cart mandates -- a digest of the exact
  cart (items, total, shipping method and address).  Changing any of those
  after signing invalidates the signature.
* A cart mandate is single use.  :meth:`MandateVerifier.authorize_checkout`
  verifies it against the checkout session and atomically marks it consumed,
  so a captured mandate cannot be replayed.

Canonical signing format (version 1)
------------------------------------
The signed bytes are the UTF-8 encoding of a JSON object with sorted keys and
no insignificant whitespace.  Amounts are fixed two-decimal strings and
timestamps are UTC, formatted ``YYYY-MM-DDTHH:MM:SS.ffffffZ``.  See
:func:`cart_signing_payload`, :func:`intent_signing_payload` and
:func:`compute_cart_hash` for the exact fields.  Signatures are base64url
encoded ECDSA-SHA256 (DER) values.
"""

from __future__ import annotations

import base64
import hashlib
import json
import threading
import uuid
from datetime import UTC, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import TYPE_CHECKING, Any

import structlog
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec

from ucp_merchant.models import (
    CartMandate,
    CheckoutSession,
    CheckoutState,
    IntentConstraints,
    IntentMandate,
    MandateType,
    MandateVerificationResult,
    Money,
)

if TYPE_CHECKING:
    from ucp_merchant.config import UCPMerchantSettings
    from ucp_merchant.payments.keys import KeyManager

logger = structlog.get_logger(__name__)

SIGNING_FORMAT_VERSION = 1

# Tolerance for clocks that disagree slightly between agent and merchant.
_CLOCK_SKEW = timedelta(seconds=60)


# ---------------------------------------------------------------------------
# Canonical encoding helpers
# ---------------------------------------------------------------------------


def _utcnow() -> datetime:
    """Naive UTC "now" (the models store naive UTC datetimes)."""
    return datetime.now(UTC).replace(tzinfo=None)


def _to_naive_utc(value: datetime) -> datetime:
    """Normalise a possibly timezone-aware datetime to naive UTC."""
    if value.tzinfo is None:
        return value
    return value.astimezone(UTC).replace(tzinfo=None)


def canonical_timestamp(value: datetime) -> str:
    """Format *value* as a UTC timestamp with fixed microsecond precision."""
    return _to_naive_utc(value).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def canonical_amount(value: float) -> str:
    """Format a money amount as a fixed two-decimal string (e.g. ``"99.90"``)."""
    return str(Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def _cents(value: float) -> int:
    """Convert a money amount to integer cents for exact comparison."""
    return int((Decimal(str(value)) * 100).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def _canonical_json(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


# ---------------------------------------------------------------------------
# What gets signed
# ---------------------------------------------------------------------------


def compute_cart_hash(session: CheckoutSession) -> str:
    """Digest of the cart contents a mandate authorises.

    Covers the line items, total, currency, shipping method and shipping
    address, so a mandate cannot be reused for a different cart or a
    different delivery destination.
    """
    address = session.shipping_address
    body: dict[str, Any] = {
        "v": SIGNING_FORMAT_VERSION,
        "currency": session.total.currency,
        "total": canonical_amount(session.total.amount),
        "items": sorted(
            (
                {
                    "product_id": item.product_id,
                    "quantity": item.quantity,
                    "unit_price": canonical_amount(item.unit_price.amount),
                }
                for item in session.line_items
            ),
            key=lambda i: (i["product_id"], i["unit_price"], i["quantity"]),
        ),
        "shipping_option_id": (session.selected_shipping.id if session.selected_shipping else None),
        "shipping_address": (
            None
            if address is None
            else {
                field: getattr(address, field) or ""
                for field in ("line1", "line2", "city", "state", "postal_code", "country")
            }
        ),
    }
    return hashlib.sha256(_canonical_json(body)).hexdigest()


def cart_signing_payload(mandate: CartMandate) -> bytes:
    """Canonical bytes that are signed for a cart mandate."""
    return _canonical_json(
        {
            "v": SIGNING_FORMAT_VERSION,
            "type": "cart",
            "mandate_id": mandate.mandate_id,
            "checkout_session_id": mandate.checkout_session_id,
            "merchant_id": mandate.merchant_id,
            "amount": canonical_amount(mandate.amount.amount),
            "currency": mandate.currency,
            "cart_hash": mandate.cart_hash,
            "issued_at": canonical_timestamp(mandate.issued_at),
            "expires_at": (canonical_timestamp(mandate.expires_at) if mandate.expires_at else None),
        }
    )


def intent_signing_payload(mandate: IntentMandate) -> bytes:
    """Canonical bytes that are signed for an intent mandate."""
    constraints = mandate.constraints
    max_amount = constraints.max_amount
    return _canonical_json(
        {
            "v": SIGNING_FORMAT_VERSION,
            "type": "intent",
            "mandate_id": mandate.mandate_id,
            "agent_id": mandate.agent_id,
            "max_amount": canonical_amount(max_amount.amount) if max_amount else None,
            "max_amount_currency": max_amount.currency if max_amount else None,
            "allowed_merchants": sorted(constraints.allowed_merchants),
            "allowed_categories": sorted(constraints.allowed_categories),
            "time_window_seconds": constraints.time_window_seconds,
            "issued_at": canonical_timestamp(mandate.issued_at),
            "expires_at": (canonical_timestamp(mandate.expires_at) if mandate.expires_at else None),
        }
    )


def _sign(private_key: ec.EllipticCurvePrivateKey, payload: bytes) -> str:
    raw_signature = private_key.sign(payload, ec.ECDSA(hashes.SHA256()))
    return base64.urlsafe_b64encode(raw_signature).decode("ascii")


def sign_cart_mandate(
    mandate: CartMandate, private_key: ec.EllipticCurvePrivateKey, key_id: str
) -> CartMandate:
    """Sign *mandate* in place with the agent's private key (agent-side helper)."""
    mandate.key_id = key_id
    mandate.signature = _sign(private_key, cart_signing_payload(mandate))
    return mandate


def sign_intent_mandate(
    mandate: IntentMandate, private_key: ec.EllipticCurvePrivateKey, key_id: str
) -> IntentMandate:
    """Sign *mandate* in place with the agent's private key (agent-side helper)."""
    mandate.key_id = key_id
    mandate.signature = _sign(private_key, intent_signing_payload(mandate))
    return mandate


# ---------------------------------------------------------------------------
# Replay protection
# ---------------------------------------------------------------------------


class MandateRejected(ValueError):
    """Raised when a mandate may not be used to complete a checkout.

    ``status_code`` is the HTTP status the API layer should answer with:
    402 for a missing / invalid authorisation, 409 for a replayed one.
    """

    def __init__(self, status_code: int, errors: list[str]) -> None:
        self.status_code = status_code
        self.errors = errors
        super().__init__("; ".join(errors))


class MandateReplayGuard:
    """Remembers consumed mandates so each one can be used only once.

    Entries are keyed by ``(key_id, mandate_id)`` rather than ``mandate_id``
    alone: otherwise anyone could burn somebody else's mandate by registering
    their own key and submitting a mandate that reuses its ID.  Entries are
    dropped once the mandate has expired, because expired mandates are
    rejected anyway.  The store is in memory, so it does not survive a restart
    and is not shared between processes.
    """

    def __init__(self) -> None:
        self._used: dict[tuple[str, str], datetime] = {}
        self._lock = threading.Lock()

    def consume(self, key_id: str, mandate_id: str, expires_at: datetime) -> bool:
        """Mark a mandate as used.  Returns ``False`` if it was already used."""
        now = _utcnow()
        key = (key_id, mandate_id)
        with self._lock:
            self._purge(now)
            if key in self._used:
                return False
            self._used[key] = _to_naive_utc(expires_at) + _CLOCK_SKEW
            return True

    def is_consumed(self, key_id: str, mandate_id: str) -> bool:
        with self._lock:
            self._purge(_utcnow())
            return (key_id, mandate_id) in self._used

    def release(self, key_id: str, mandate_id: str) -> None:
        """Undo :meth:`consume` (used when the checkout failed afterwards)."""
        with self._lock:
            self._used.pop((key_id, mandate_id), None)

    def _purge(self, now: datetime) -> None:
        for key in [k for k, retain_until in self._used.items() if retain_until < now]:
            del self._used[key]


# ---------------------------------------------------------------------------
# Verifier
# ---------------------------------------------------------------------------


class MandateVerifier:
    """Verifies AP2 payment mandates using ECDSA P-256 signatures.

    Parameters
    ----------
    settings:
        Merchant configuration (merchant ID, mandate lifetime limits).
    key_manager:
        Store of registered agent public keys.
    replay_guard:
        Single-use registry; a fresh in-memory one is created by default.
    """

    def __init__(
        self,
        settings: UCPMerchantSettings,
        key_manager: KeyManager,
        replay_guard: MandateReplayGuard | None = None,
    ) -> None:
        self._settings = settings
        self._key_manager = key_manager
        self._replay_guard = replay_guard or MandateReplayGuard()

    @property
    def replay_guard(self) -> MandateReplayGuard:
        return self._replay_guard

    # -- cart mandates -------------------------------------------------------

    def verify_cart_mandate(
        self,
        mandate: CartMandate,
        expected_total: float | None = None,
        session: CheckoutSession | None = None,
        check_replay: bool = True,
    ) -> MandateVerificationResult:
        """Verify a cart-level payment mandate without consuming it.

        Checks performed:

        1. Signature validity against the registered public key.
        2. Validity window: ``expires_at`` is required, not passed, not
           further from ``issued_at`` than the configured maximum.
        3. Merchant ID matches this merchant.
        4. The mandate is bound to a cart (``cart_hash``).
        5. If *session* is given: same session, same total (to the cent) and
           the cart digest still matches the session's current contents.
           Otherwise, if *expected_total* is given, only the amount is
           compared.
        6. The mandate has not been used already (if *check_replay*).
        """
        errors: list[str] = []
        warnings: list[str] = []

        if not self._verify_signature(
            mandate.key_id, mandate.signature, cart_signing_payload(mandate)
        ):
            errors.append("Invalid ECDSA P-256 signature")

        self._check_validity_window(mandate.issued_at, mandate.expires_at, errors)

        if mandate.merchant_id != self._settings.merchant_id:
            errors.append(
                f"Merchant ID mismatch: expected {self._settings.merchant_id!r}, "
                f"got {mandate.merchant_id!r}"
            )

        if mandate.amount.currency != mandate.currency:
            errors.append("Currency mismatch between mandate amount and mandate currency")

        if not mandate.cart_hash:
            errors.append("Mandate is not bound to a cart (cart_hash is missing)")

        if session is not None:
            self._check_cart_binding(mandate, session, errors)
        elif expected_total is not None and _cents(mandate.amount.amount) != _cents(expected_total):
            errors.append(
                f"Amount mismatch: mandate={mandate.amount.amount:.2f}, "
                f"expected={expected_total:.2f}"
            )

        if check_replay and self._replay_guard.is_consumed(mandate.key_id, mandate.mandate_id):
            errors.append("Mandate has already been used")

        return MandateVerificationResult(
            valid=len(errors) == 0,
            mandate_id=mandate.mandate_id,
            mandate_type=MandateType.CART,
            errors=errors,
            warnings=warnings,
        )

    def authorize_checkout(
        self, session: CheckoutSession, mandate: CartMandate
    ) -> MandateVerificationResult:
        """Verify *mandate* for *session* and consume it, atomically.

        Call :meth:`release_authorization` if completing the checkout fails
        afterwards, so the agent can retry with the same mandate.

        Raises
        ------
        MandateRejected
            402 if the mandate is invalid for this session, 409 if it was
            already used or the session can no longer be completed.
        """
        if session.state in (CheckoutState.COMPLETED, CheckoutState.ABANDONED):
            raise MandateRejected(409, [f"Checkout session is {session.state.value}"])

        result = self.verify_cart_mandate(mandate, session=session, check_replay=False)
        if not result.valid or mandate.expires_at is None:
            raise MandateRejected(402, result.errors or ["Mandate is not valid"])

        if not self._replay_guard.consume(mandate.key_id, mandate.mandate_id, mandate.expires_at):
            raise MandateRejected(409, ["Mandate has already been used"])

        logger.info(
            "mandate_consumed",
            mandate_id=mandate.mandate_id,
            session_id=session.id,
            key_id=mandate.key_id,
        )
        return result

    def release_authorization(self, mandate: CartMandate) -> None:
        """Make a consumed mandate usable again (checkout failed afterwards)."""
        self._replay_guard.release(mandate.key_id, mandate.mandate_id)

    # -- intent mandates -----------------------------------------------------

    def verify_intent_mandate(
        self,
        mandate: IntentMandate,
        merchant_id: str | None = None,
        requested_amount: float | None = None,
    ) -> MandateVerificationResult:
        """Verify an intent-level payment mandate.

        Checks performed:

        1. Signature validity (covers every constraint).
        2. Validity window (``expires_at`` required).
        3. Constraint checks: time window, max amount, allowed merchants.
        """
        errors: list[str] = []
        warnings: list[str] = []

        if not self._verify_signature(
            mandate.key_id, mandate.signature, intent_signing_payload(mandate)
        ):
            errors.append("Invalid ECDSA P-256 signature")

        self._check_validity_window(mandate.issued_at, mandate.expires_at, errors)

        constraints = mandate.constraints
        if constraints.time_window_seconds is not None:
            window_end = _to_naive_utc(mandate.issued_at) + timedelta(
                seconds=constraints.time_window_seconds
            )
            if _utcnow() > window_end:
                errors.append(f"Time window of {constraints.time_window_seconds}s has elapsed")

        if (
            requested_amount is not None
            and constraints.max_amount is not None
            and _cents(requested_amount) > _cents(constraints.max_amount.amount)
        ):
            errors.append(
                f"Requested amount {requested_amount:.2f} exceeds "
                f"max_amount {constraints.max_amount.amount:.2f}"
            )

        check_merchant = merchant_id or self._settings.merchant_id
        if constraints.allowed_merchants and check_merchant not in constraints.allowed_merchants:
            errors.append(
                f"Merchant {check_merchant!r} not in allowed list: {constraints.allowed_merchants}"
            )

        return MandateVerificationResult(
            valid=len(errors) == 0,
            mandate_id=mandate.mandate_id,
            mandate_type=MandateType.INTENT,
            errors=errors,
            warnings=warnings,
        )

    # -- demo helpers (server holds the private key) -------------------------

    def create_test_mandate(self, session: CheckoutSession, key_id: str) -> CartMandate:
        """Create a signed cart mandate for demos.

        Requires a *demo* key from :meth:`KeyManager.generate_key_pair`; keys
        that an agent registered have no private half on the server.
        """
        now = _utcnow()
        mandate = CartMandate(
            mandate_id=f"mandate_{uuid.uuid4().hex[:12]}",
            mandate_type=MandateType.CART,
            checkout_session_id=session.id,
            merchant_id=self._settings.merchant_id,
            amount=Money(amount=session.total.amount, currency=session.total.currency),
            currency=session.total.currency,
            cart_hash=compute_cart_hash(session),
            issued_at=now,
            expires_at=now + timedelta(seconds=self._settings.ap2_mandate_ttl_seconds),
            key_id=key_id,
        )
        sign_cart_mandate(mandate, self._key_manager.get_private_key(key_id), key_id)

        logger.info(
            "test_mandate_created",
            mandate_id=mandate.mandate_id,
            session_id=session.id,
            amount=mandate.amount.amount,
        )
        return mandate

    def create_test_intent_mandate(
        self,
        agent_id: str,
        key_id: str,
        max_amount: float = 1000.0,
        allowed_merchants: list[str] | None = None,
        time_window_seconds: int = 3600,
    ) -> IntentMandate:
        """Create a signed intent mandate for demos (see :meth:`create_test_mandate`)."""
        now = _utcnow()
        mandate = IntentMandate(
            mandate_id=f"intent_{uuid.uuid4().hex[:12]}",
            mandate_type=MandateType.INTENT,
            agent_id=agent_id,
            constraints=IntentConstraints(
                max_amount=Money(amount=max_amount),
                allowed_merchants=allowed_merchants or [self._settings.merchant_id],
                time_window_seconds=time_window_seconds,
            ),
            issued_at=now,
            expires_at=now + timedelta(seconds=time_window_seconds),
            key_id=key_id,
        )
        sign_intent_mandate(mandate, self._key_manager.get_private_key(key_id), key_id)

        logger.info(
            "test_intent_mandate_created",
            mandate_id=mandate.mandate_id,
            agent_id=agent_id,
            max_amount=max_amount,
        )
        return mandate

    # -- private helpers -----------------------------------------------------

    def _check_validity_window(
        self,
        issued_at: datetime,
        expires_at: datetime | None,
        errors: list[str],
    ) -> None:
        now = _utcnow()
        issued = _to_naive_utc(issued_at)

        if issued > now + _CLOCK_SKEW:
            errors.append("Mandate issued_at is in the future")

        if expires_at is None:
            errors.append("Mandate must carry an expiry (expires_at)")
            return

        expires = _to_naive_utc(expires_at)
        if now > expires:
            errors.append(f"Mandate expired at {expires.isoformat()}")
        if expires <= issued:
            errors.append("Mandate expires_at must be after issued_at")
        elif expires - issued > timedelta(seconds=self._settings.ap2_max_mandate_ttl_seconds):
            errors.append(
                "Mandate lifetime exceeds the maximum of "
                f"{self._settings.ap2_max_mandate_ttl_seconds}s"
            )

    @staticmethod
    def _check_cart_binding(
        mandate: CartMandate, session: CheckoutSession, errors: list[str]
    ) -> None:
        if mandate.checkout_session_id != session.id:
            errors.append("Mandate was issued for a different checkout session")

        if (
            _cents(mandate.amount.amount) != _cents(session.total.amount)
            or mandate.currency != session.total.currency
        ):
            errors.append(
                f"Amount mismatch: mandate={mandate.amount.amount:.2f} {mandate.currency}, "
                f"session total={session.total.amount:.2f} {session.total.currency}"
            )

        if mandate.cart_hash and mandate.cart_hash != compute_cart_hash(session):
            errors.append("Cart contents changed since the mandate was signed")

    def _verify_signature(self, key_id: str, signature_b64: str, payload: bytes) -> bool:
        """Verify an ECDSA P-256 signature against a registered public key.

        Returns ``True`` if valid, ``False`` otherwise (including for unknown
        keys or malformed signatures).
        """
        if not signature_b64 or not key_id:
            return False

        try:
            public_key = self._key_manager.get_public_key(key_id)
        except KeyError:
            logger.warning("verification_key_not_found", key_id=key_id)
            return False

        try:
            raw_signature = base64.urlsafe_b64decode(
                signature_b64 + "=" * (-len(signature_b64) % 4)
            )
            public_key.verify(raw_signature, payload, ec.ECDSA(hashes.SHA256()))
            return True
        except (InvalidSignature, ValueError) as exc:
            logger.debug("signature_verification_failed", key_id=key_id, error=str(exc))
            return False
