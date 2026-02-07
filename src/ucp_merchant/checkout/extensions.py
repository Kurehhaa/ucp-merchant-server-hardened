"""UCP extension implementations: Fulfillment and Discounts.

Fulfillment provides shipping options with conditional free shipping.
Discounts validates and applies coupon codes.
"""

from __future__ import annotations

from datetime import datetime

import structlog

from ucp_merchant.models import (
    AppliedDiscount,
    CheckoutSession,
    DiscountCode,
    Money,
    ShippingOption,
)

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Fulfillment extension
# ---------------------------------------------------------------------------

_SHIPPING_OPTIONS: list[ShippingOption] = [
    ShippingOption(
        id="shipping_standard",
        name="Standard Shipping",
        description="Reliable ground shipping, delivered in 5-7 business days.",
        price=Money(amount=5.99),
        estimated_days_min=5,
        estimated_days_max=7,
    ),
    ShippingOption(
        id="shipping_express",
        name="Express Shipping",
        description="Fast shipping with tracking, delivered in 2-3 business days.",
        price=Money(amount=12.99),
        estimated_days_min=2,
        estimated_days_max=3,
    ),
    ShippingOption(
        id="shipping_next_day",
        name="Next Day Delivery",
        description="Premium overnight delivery for urgent orders. Order by 2 PM.",
        price=Money(amount=24.99),
        estimated_days_min=1,
        estimated_days_max=1,
    ),
    ShippingOption(
        id="shipping_free",
        name="Free Economy Shipping",
        description="Free shipping on orders over $100. Delivered in 7-10 business days.",
        price=Money(amount=0.00),
        estimated_days_min=7,
        estimated_days_max=10,
    ),
]

# Minimum subtotal to qualify for free shipping.
_FREE_SHIPPING_THRESHOLD = 100.00


class FulfillmentExtension:
    """UCP Fulfillment extension -- provides shipping options.

    Free Economy Shipping is only offered when the cart subtotal exceeds
    the $100 threshold.
    """

    def get_shipping_options(self, subtotal: float) -> list[ShippingOption]:
        """Return available shipping options for the given *subtotal*.

        Parameters
        ----------
        subtotal:
            The cart subtotal (before tax / discount).

        Returns
        -------
        list[ShippingOption]
            Shipping methods the customer may choose from.
        """
        options = [
            opt
            for opt in _SHIPPING_OPTIONS
            if opt.id != "shipping_free" or subtotal >= _FREE_SHIPPING_THRESHOLD
        ]
        return options

    def get_option_by_id(self, option_id: str) -> ShippingOption | None:
        """Look up a shipping option by its ID."""
        for opt in _SHIPPING_OPTIONS:
            if opt.id == option_id:
                return opt
        return None

    def apply_shipping(
        self, session: CheckoutSession, option_id: str
    ) -> CheckoutSession:
        """Set the selected shipping option on the session.

        Parameters
        ----------
        session:
            The checkout session to update.
        option_id:
            The ID of the chosen shipping option.

        Returns
        -------
        CheckoutSession
            The updated session.

        Raises
        ------
        ValueError
            If *option_id* is unknown or the session does not qualify.
        """
        option = self.get_option_by_id(option_id)
        if option is None:
            raise ValueError(f"Unknown shipping option: {option_id!r}")

        # Enforce free-shipping eligibility
        subtotal = session.subtotal.amount if session.subtotal else 0.0
        if option.id == "shipping_free" and subtotal < _FREE_SHIPPING_THRESHOLD:
            raise ValueError(
                f"Free shipping requires a subtotal of at least "
                f"${_FREE_SHIPPING_THRESHOLD:.2f} (current: ${subtotal:.2f})"
            )

        session.selected_shipping = option
        session.shipping_cost = option.price
        session.updated_at = datetime.utcnow()

        logger.info(
            "shipping_selected",
            session_id=session.id,
            option_id=option_id,
            option_name=option.name,
        )
        return session


# ---------------------------------------------------------------------------
# Discount extension
# ---------------------------------------------------------------------------

_DISCOUNT_CODES: dict[str, DiscountCode] = {
    "SAVE10": DiscountCode(
        code="SAVE10",
        description="10% off your entire order",
        discount_type="percentage",
        value=10.0,
        max_discount=None,
        min_order=None,
    ),
    "SAVE20": DiscountCode(
        code="SAVE20",
        description="20% off (max $50 discount)",
        discount_type="percentage",
        value=20.0,
        max_discount=50.0,
        min_order=None,
    ),
    "FREESHIP": DiscountCode(
        code="FREESHIP",
        description="Free shipping on any order",
        discount_type="free_shipping",
        value=0.0,
        max_discount=None,
        min_order=None,
    ),
    "WELCOME": DiscountCode(
        code="WELCOME",
        description="15% off your first order",
        discount_type="percentage",
        value=15.0,
        max_discount=None,
        min_order=None,
    ),
}


class DiscountExtension:
    """UCP Discount extension -- validates and applies coupon codes."""

    def validate_code(self, code: str) -> DiscountCode:
        """Validate a discount code.

        Parameters
        ----------
        code:
            The coupon code (case-insensitive).

        Returns
        -------
        DiscountCode
            The validated discount definition.

        Raises
        ------
        ValueError
            If the code is unknown or invalid.
        """
        normalised = code.strip().upper()
        discount = _DISCOUNT_CODES.get(normalised)
        if discount is None:
            raise ValueError(f"Invalid discount code: {code!r}")
        return discount

    def apply_discount(
        self, session: CheckoutSession, code: str
    ) -> CheckoutSession:
        """Apply a discount code to the checkout session.

        Parameters
        ----------
        session:
            The checkout session.
        code:
            The coupon code to apply.

        Returns
        -------
        CheckoutSession
            The session with ``applied_discount`` set.

        Raises
        ------
        ValueError
            If the code is invalid or the order does not meet requirements.
        """
        discount = self.validate_code(code)

        subtotal = session.subtotal.amount if session.subtotal else 0.0

        # Enforce minimum order
        if discount.min_order is not None and subtotal < discount.min_order:
            raise ValueError(
                f"Code {discount.code!r} requires a minimum order of "
                f"${discount.min_order:.2f} (current: ${subtotal:.2f})"
            )

        # Calculate the actual discount amount
        discount_amount = 0.0
        if discount.discount_type == "percentage":
            discount_amount = round(subtotal * (discount.value / 100.0), 2)
            if discount.max_discount is not None:
                discount_amount = min(discount_amount, discount.max_discount)
        elif discount.discount_type == "fixed":
            discount_amount = round(min(discount.value, subtotal), 2)
        elif discount.discount_type == "free_shipping":
            discount_amount = 0.0  # handled at totals level

        session.applied_discount = AppliedDiscount(
            code=discount.code,
            description=discount.description,
            discount_type=discount.discount_type,
            discount_amount=discount_amount,
        )
        session.updated_at = datetime.utcnow()

        logger.info(
            "discount_applied",
            session_id=session.id,
            code=discount.code,
            discount_type=discount.discount_type,
            discount_amount=discount_amount,
        )
        return session
