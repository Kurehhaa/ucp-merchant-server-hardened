"""AP2 mandate model re-exports and helpers.

All Pydantic models are defined in :mod:`ucp_merchant.models`.
This module provides convenience re-exports and any mandate-specific
utility functions.
"""

from __future__ import annotations

from ucp_merchant.models import (
    CartMandate,
    IntentConstraints,
    IntentMandate,
    KeyPairInfo,
    MandateType,
    MandateVerificationResult,
    Money,
)

__all__ = [
    "CartMandate",
    "IntentConstraints",
    "IntentMandate",
    "KeyPairInfo",
    "MandateType",
    "MandateVerificationResult",
    "Money",
]


def is_mandate_expired(mandate: CartMandate | IntentMandate) -> bool:
    """Check whether a mandate has expired.

    Parameters
    ----------
    mandate:
        A cart or intent mandate.

    Returns
    -------
    bool
        ``True`` if the mandate has an expiry and that expiry is in the past.
    """
    from datetime import datetime

    if mandate.expires_at is None:
        return False
    return datetime.utcnow() > mandate.expires_at


def mandate_summary(mandate: CartMandate | IntentMandate) -> dict[str, str]:
    """Return a human-readable summary dict for a mandate.

    Useful for logging and API responses.
    """
    base = {
        "mandate_id": mandate.mandate_id,
        "type": mandate.mandate_type.value,
        "issued_at": mandate.issued_at.isoformat(),
        "expires_at": mandate.expires_at.isoformat() if mandate.expires_at else "none",
        "has_signature": bool(mandate.signature),
        "key_id": mandate.key_id,
    }

    if isinstance(mandate, CartMandate):
        base["merchant_id"] = mandate.merchant_id
        base["amount"] = f"{mandate.amount.amount:.2f} {mandate.currency}"
        base["checkout_session_id"] = mandate.checkout_session_id
    elif isinstance(mandate, IntentMandate):
        base["agent_id"] = mandate.agent_id
        if mandate.constraints.max_amount:
            base["max_amount"] = f"{mandate.constraints.max_amount.amount:.2f}"
        base["allowed_merchants"] = ", ".join(
            mandate.constraints.allowed_merchants
        ) or "any"

    return base
