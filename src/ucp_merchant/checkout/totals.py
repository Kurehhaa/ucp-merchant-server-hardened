"""Totals calculator for checkout sessions.

Computes subtotal, tax, shipping, discount, and grand total.  Discounts are
applied **before** tax so that tax is calculated on the discounted subtotal.
"""

from __future__ import annotations

from ucp_merchant.config import UCPMerchantSettings
from ucp_merchant.models import CheckoutSession, Money


class TotalsCalculator:
    """Recalculates all monetary totals on a checkout session."""

    def __init__(self, settings: UCPMerchantSettings) -> None:
        self._tax_rate = settings.tax_rate

    def calculate(self, session: CheckoutSession) -> CheckoutSession:
        """Recompute all totals on *session* in-place and return it.

        Calculation order:

        1. **Subtotal** -- sum of line-item totals.
        2. **Discount** -- applied before tax (percentage of subtotal, fixed
           amount, or free shipping depending on discount type).
        3. **Taxable amount** -- subtotal minus discount.
        4. **Tax** -- taxable amount multiplied by the configured tax rate.
        5. **Shipping** -- from the selected shipping option (zero if free
           shipping discount is active).
        6. **Total** -- taxable amount + tax + shipping.
        """
        currency = "USD"

        # 1. Subtotal
        subtotal = sum(
            (item.total_price.amount if item.total_price else 0.0)
            for item in session.line_items
        )
        subtotal = round(subtotal, 2)
        session.subtotal = Money(amount=subtotal, currency=currency)

        # 2. Discount
        discount_amount = 0.0
        free_shipping = False
        if session.applied_discount:
            disc = session.applied_discount
            if disc.discount_type == "percentage":
                discount_amount = disc.discount_amount
            elif disc.discount_type == "fixed":
                discount_amount = disc.discount_amount
            elif disc.discount_type == "free_shipping":
                free_shipping = True
                discount_amount = 0.0
            else:
                discount_amount = disc.discount_amount
        discount_amount = round(min(discount_amount, subtotal), 2)
        session.discount_amount = Money(amount=discount_amount, currency=currency)

        # 3. Taxable amount
        taxable = round(subtotal - discount_amount, 2)

        # 4. Tax
        tax = round(taxable * self._tax_rate, 2)
        session.tax = Money(amount=tax, currency=currency)

        # 5. Shipping
        shipping = 0.0
        if session.selected_shipping and not free_shipping:
            shipping = session.selected_shipping.price.amount
        shipping = round(shipping, 2)
        session.shipping_cost = Money(amount=shipping, currency=currency)

        # 6. Grand total
        total = round(taxable + tax + shipping, 2)
        session.total = Money(amount=total, currency=currency)

        return session
