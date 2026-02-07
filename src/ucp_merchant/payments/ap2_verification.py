"""AP2 mandate verification with ECDSA P-256 cryptography.

Provides verification for both cart-level and intent-level payment mandates
as specified by the AP2 section of the Universal Commerce Protocol.
Also includes a helper to create signed test mandates for demos.
"""

from __future__ import annotations

import base64
import json
import uuid
from datetime import datetime, timedelta

import structlog
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec, utils

from ucp_merchant.config import UCPMerchantSettings
from ucp_merchant.models import (
    CartMandate,
    CheckoutSession,
    IntentMandate,
    MandateType,
    MandateVerificationResult,
    Money,
)
from ucp_merchant.payments.keys import KeyManager

logger = structlog.get_logger(__name__)


def _build_cart_signing_payload(mandate: CartMandate) -> bytes:
    """Build the canonical byte string that is signed for a cart mandate.

    The payload is a deterministic JSON encoding of the mandate's
    business-critical fields.
    """
    payload = {
        "mandate_id": mandate.mandate_id,
        "checkout_session_id": mandate.checkout_session_id,
        "merchant_id": mandate.merchant_id,
        "amount": mandate.amount.amount,
        "currency": mandate.currency,
        "issued_at": mandate.issued_at.isoformat(),
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _build_intent_signing_payload(mandate: IntentMandate) -> bytes:
    """Build the canonical byte string that is signed for an intent mandate."""
    payload = {
        "mandate_id": mandate.mandate_id,
        "agent_id": mandate.agent_id,
        "max_amount": (
            mandate.constraints.max_amount.amount
            if mandate.constraints.max_amount
            else None
        ),
        "allowed_merchants": sorted(mandate.constraints.allowed_merchants),
        "issued_at": mandate.issued_at.isoformat(),
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


class MandateVerifier:
    """Verifies AP2 payment mandates using ECDSA P-256 signatures.

    Parameters
    ----------
    settings:
        Merchant configuration (used to validate merchant_id).
    key_manager:
        Key store for looking up signing / verification keys.
    """

    def __init__(
        self,
        settings: UCPMerchantSettings,
        key_manager: KeyManager,
    ) -> None:
        self._settings = settings
        self._key_manager = key_manager

    # -- cart mandates -------------------------------------------------------

    def verify_cart_mandate(
        self,
        mandate: CartMandate,
        expected_total: float | None = None,
    ) -> MandateVerificationResult:
        """Verify a cart-level payment mandate.

        Checks performed:

        1. Signature validity (ECDSA P-256).
        2. Mandate not expired.
        3. Merchant ID matches.
        4. Amount matches the checkout total (if *expected_total* is given).

        Parameters
        ----------
        mandate:
            The cart mandate to verify.
        expected_total:
            Optional expected checkout total.  If provided, the mandate
            amount must match within a 1-cent tolerance.

        Returns
        -------
        MandateVerificationResult
        """
        errors: list[str] = []
        warnings: list[str] = []

        # 1. Signature
        sig_valid = self._verify_signature(
            mandate.key_id,
            mandate.signature,
            _build_cart_signing_payload(mandate),
        )
        if not sig_valid:
            errors.append("Invalid ECDSA P-256 signature")

        # 2. Expiry
        if mandate.expires_at and datetime.utcnow() > mandate.expires_at:
            errors.append(
                f"Mandate expired at {mandate.expires_at.isoformat()}"
            )
        elif mandate.expires_at is None:
            warnings.append("Mandate has no expiry -- consider setting one")

        # 3. Merchant ID
        if mandate.merchant_id != self._settings.merchant_id:
            errors.append(
                f"Merchant ID mismatch: expected {self._settings.merchant_id!r}, "
                f"got {mandate.merchant_id!r}"
            )

        # 4. Amount
        if expected_total is not None:
            diff = abs(mandate.amount.amount - expected_total)
            if diff > 0.01:
                errors.append(
                    f"Amount mismatch: mandate={mandate.amount.amount:.2f}, "
                    f"expected={expected_total:.2f}"
                )

        return MandateVerificationResult(
            valid=len(errors) == 0,
            mandate_id=mandate.mandate_id,
            mandate_type=MandateType.CART,
            errors=errors,
            warnings=warnings,
        )

    # -- intent mandates -----------------------------------------------------

    def verify_intent_mandate(
        self,
        mandate: IntentMandate,
        merchant_id: str | None = None,
        requested_amount: float | None = None,
    ) -> MandateVerificationResult:
        """Verify an intent-level payment mandate.

        Checks performed:

        1. Signature validity.
        2. Mandate not expired.
        3. Constraint checks: max_amount, allowed_merchants, time_window.

        Parameters
        ----------
        mandate:
            The intent mandate to verify.
        merchant_id:
            If provided, verify the merchant is in the allowed list.
        requested_amount:
            If provided, verify it does not exceed max_amount.

        Returns
        -------
        MandateVerificationResult
        """
        errors: list[str] = []
        warnings: list[str] = []

        # 1. Signature
        sig_valid = self._verify_signature(
            mandate.key_id,
            mandate.signature,
            _build_intent_signing_payload(mandate),
        )
        if not sig_valid:
            errors.append("Invalid ECDSA P-256 signature")

        # 2. Expiry
        if mandate.expires_at and datetime.utcnow() > mandate.expires_at:
            errors.append(
                f"Mandate expired at {mandate.expires_at.isoformat()}"
            )

        # 3. Time window
        constraints = mandate.constraints
        if constraints.time_window_seconds is not None:
            window_end = mandate.issued_at + timedelta(
                seconds=constraints.time_window_seconds
            )
            if datetime.utcnow() > window_end:
                errors.append(
                    f"Time window of {constraints.time_window_seconds}s has elapsed"
                )

        # 4. Max amount
        if (
            requested_amount is not None
            and constraints.max_amount is not None
            and requested_amount > constraints.max_amount.amount
        ):
            errors.append(
                f"Requested amount {requested_amount:.2f} exceeds "
                f"max_amount {constraints.max_amount.amount:.2f}"
            )

        # 5. Allowed merchants
        check_merchant = merchant_id or self._settings.merchant_id
        if (
            constraints.allowed_merchants
            and check_merchant not in constraints.allowed_merchants
        ):
            errors.append(
                f"Merchant {check_merchant!r} not in allowed list: "
                f"{constraints.allowed_merchants}"
            )

        return MandateVerificationResult(
            valid=len(errors) == 0,
            mandate_id=mandate.mandate_id,
            mandate_type=MandateType.INTENT,
            errors=errors,
            warnings=warnings,
        )

    # -- test helpers --------------------------------------------------------

    def create_test_mandate(
        self,
        session: CheckoutSession,
        key_id: str,
    ) -> CartMandate:
        """Create a properly signed test cart mandate for demo purposes.

        Parameters
        ----------
        session:
            The checkout session to bind the mandate to.
        key_id:
            The key pair to sign with.

        Returns
        -------
        CartMandate
            A signed mandate that will pass :meth:`verify_cart_mandate`.
        """
        now = datetime.utcnow()
        mandate = CartMandate(
            mandate_id=f"mandate_{uuid.uuid4().hex[:12]}",
            mandate_type=MandateType.CART,
            checkout_session_id=session.id,
            merchant_id=self._settings.merchant_id,
            amount=Money(
                amount=session.total.amount,
                currency=session.total.currency,
            ),
            currency=session.total.currency,
            issued_at=now,
            expires_at=now + timedelta(seconds=self._settings.ap2_mandate_ttl_seconds),
            key_id=key_id,
        )

        # Sign
        payload = _build_cart_signing_payload(mandate)
        private_key = self._key_manager.get_private_key(key_id)
        raw_signature = private_key.sign(
            payload,
            ec.ECDSA(hashes.SHA256()),
        )
        mandate.signature = base64.urlsafe_b64encode(raw_signature).decode("ascii")

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
        """Create a signed test intent mandate for demo purposes.

        Parameters
        ----------
        agent_id:
            The agent issuing the mandate.
        key_id:
            The key pair to sign with.
        max_amount:
            Maximum authorised amount.
        allowed_merchants:
            Merchant IDs allowed to charge against this mandate.
        time_window_seconds:
            Validity window in seconds.

        Returns
        -------
        IntentMandate
            A signed intent mandate.
        """
        from ucp_merchant.models import IntentConstraints

        now = datetime.utcnow()
        merchants = allowed_merchants or [self._settings.merchant_id]

        mandate = IntentMandate(
            mandate_id=f"intent_{uuid.uuid4().hex[:12]}",
            mandate_type=MandateType.INTENT,
            agent_id=agent_id,
            constraints=IntentConstraints(
                max_amount=Money(amount=max_amount),
                allowed_merchants=merchants,
                time_window_seconds=time_window_seconds,
            ),
            issued_at=now,
            expires_at=now + timedelta(seconds=time_window_seconds),
            key_id=key_id,
        )

        payload = _build_intent_signing_payload(mandate)
        private_key = self._key_manager.get_private_key(key_id)
        raw_signature = private_key.sign(
            payload,
            ec.ECDSA(hashes.SHA256()),
        )
        mandate.signature = base64.urlsafe_b64encode(raw_signature).decode("ascii")

        logger.info(
            "test_intent_mandate_created",
            mandate_id=mandate.mandate_id,
            agent_id=agent_id,
            max_amount=max_amount,
        )
        return mandate

    # -- private helpers -----------------------------------------------------

    def _verify_signature(
        self,
        key_id: str,
        signature_b64: str,
        payload: bytes,
    ) -> bool:
        """Verify an ECDSA P-256 signature.

        Returns ``True`` if valid, ``False`` otherwise (including for missing
        keys or malformed signatures).
        """
        if not signature_b64:
            return False

        try:
            private_key = self._key_manager.get_private_key(key_id)
            public_key = private_key.public_key()
        except KeyError:
            logger.warning("verification_key_not_found", key_id=key_id)
            return False

        try:
            # Decode the base64url signature
            # Add padding if needed
            padded = signature_b64 + "=" * (4 - len(signature_b64) % 4)
            raw_signature = base64.urlsafe_b64decode(padded)

            public_key.verify(
                raw_signature,
                payload,
                ec.ECDSA(hashes.SHA256()),
            )
            return True
        except (InvalidSignature, ValueError, Exception) as exc:
            logger.debug(
                "signature_verification_failed",
                key_id=key_id,
                error=str(exc),
            )
            return False
