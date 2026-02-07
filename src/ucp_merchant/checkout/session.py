"""Checkout session management.

Coordinates the catalog, extensions, state machine, and totals calculator
to manage the full checkout lifecycle from creation to completion.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

import structlog

from ucp_merchant.catalog.products import ProductCatalog
from ucp_merchant.checkout.extensions import DiscountExtension, FulfillmentExtension
from ucp_merchant.checkout.state_machine import CheckoutStateMachine
from ucp_merchant.checkout.totals import TotalsCalculator
from ucp_merchant.config import UCPMerchantSettings
from ucp_merchant.models import (
    Address,
    CheckoutSession,
    CheckoutState,
    LineItem,
    Money,
)

logger = structlog.get_logger(__name__)


class SessionNotFoundError(Exception):
    """Raised when a checkout session ID does not exist."""

    def __init__(self, session_id: str) -> None:
        self.session_id = session_id
        super().__init__(f"Checkout session not found: {session_id!r}")


class CheckoutSessionManager:
    """Manages checkout sessions through their full lifecycle.

    Each mutating operation automatically recalculates totals and
    re-evaluates the session's readiness state.
    """

    def __init__(
        self,
        settings: UCPMerchantSettings,
        catalog: ProductCatalog,
    ) -> None:
        self._settings = settings
        self._catalog = catalog
        self._sessions: dict[str, CheckoutSession] = {}
        self._state_machine = CheckoutStateMachine()
        self._totals = TotalsCalculator(settings)
        self._fulfillment = FulfillmentExtension()
        self._discounts = DiscountExtension()

    # -- public accessors ----------------------------------------------------

    @property
    def fulfillment(self) -> FulfillmentExtension:
        """Expose the fulfillment extension for the API layer."""
        return self._fulfillment

    @property
    def discounts(self) -> DiscountExtension:
        """Expose the discount extension for the API layer."""
        return self._discounts

    # -- CRUD ----------------------------------------------------------------

    def create_session(
        self,
        line_items: list[dict[str, Any]] | None = None,
    ) -> CheckoutSession:
        """Create a new checkout session, optionally with initial items.

        Parameters
        ----------
        line_items:
            Optional list of dicts with ``product_id`` and ``quantity`` keys.

        Returns
        -------
        CheckoutSession
            The newly created session.
        """
        session_id = f"cs_{uuid.uuid4().hex[:16]}"
        session = CheckoutSession(id=session_id)

        if line_items:
            for item_spec in line_items:
                session = self._add_line_item(
                    session,
                    product_id=item_spec["product_id"],
                    quantity=item_spec.get("quantity", 1),
                )

        session = self._recalculate(session)
        self._sessions[session_id] = session

        logger.info(
            "checkout_session_created",
            session_id=session_id,
            items=len(session.line_items),
        )
        return session

    def get_session(self, session_id: str) -> CheckoutSession:
        """Retrieve a session by its ID.

        Raises
        ------
        SessionNotFoundError
            If the session does not exist.
        """
        session = self._sessions.get(session_id)
        if session is None:
            raise SessionNotFoundError(session_id)
        return session

    def update_session(
        self,
        session_id: str,
        updates: dict[str, Any],
    ) -> CheckoutSession:
        """Apply a set of updates to an existing session.

        Supported update keys:

        - ``add_items``: list of ``{product_id, quantity}`` dicts.
        - ``remove_items``: list of ``product_id`` strings.
        - ``shipping_address``: dict matching :class:`Address` fields.
        - ``billing_address``: dict matching :class:`Address` fields.
        - ``shipping_option_id``: ID of a :class:`ShippingOption`.
        - ``discount_code``: coupon code string.

        Parameters
        ----------
        session_id:
            The session to update.
        updates:
            Key/value pairs describing the changes.

        Returns
        -------
        CheckoutSession
            The updated session with recalculated totals and state.

        Raises
        ------
        SessionNotFoundError
            If the session does not exist.
        ValueError
            If an update is invalid (bad product, ineligible discount, etc.).
        """
        session = self.get_session(session_id)

        if session.state in (CheckoutState.COMPLETED, CheckoutState.ABANDONED):
            raise ValueError(
                f"Cannot update session in terminal state: {session.state.value!r}"
            )

        # Add items
        for item_spec in updates.get("add_items", []):
            session = self._add_line_item(
                session,
                product_id=item_spec["product_id"],
                quantity=item_spec.get("quantity", 1),
            )

        # Remove items
        for product_id in updates.get("remove_items", []):
            session.line_items = [
                li for li in session.line_items if li.product_id != product_id
            ]

        # Shipping address
        if "shipping_address" in updates:
            session.shipping_address = Address(**updates["shipping_address"])

        # Billing address
        if "billing_address" in updates:
            session.billing_address = Address(**updates["billing_address"])

        # Recalculate before shipping / discount (they depend on subtotal)
        session = self._totals.calculate(session)

        # Shipping option
        if "shipping_option_id" in updates:
            session = self._fulfillment.apply_shipping(
                session, updates["shipping_option_id"]
            )

        # Discount code
        if "discount_code" in updates:
            session = self._discounts.apply_discount(
                session, updates["discount_code"]
            )

        session = self._recalculate(session)
        self._sessions[session_id] = session

        logger.info(
            "checkout_session_updated",
            session_id=session_id,
            state=session.state.value,
            total=session.total.amount,
        )
        return session

    def complete_session(self, session_id: str) -> CheckoutSession:
        """Attempt to complete a checkout session.

        The session must be in READY_FOR_COMPLETE state.  On success the
        session transitions to COMPLETED and an ``order_id`` is assigned.

        Returns
        -------
        CheckoutSession
            The completed session (with ``order_id`` populated).

        Raises
        ------
        SessionNotFoundError
            If the session does not exist.
        InvalidTransitionError
            If the session is not ready for completion.
        """
        session = self.get_session(session_id)

        # If the session isn't explicitly READY_FOR_COMPLETE, re-evaluate
        if session.state != CheckoutState.READY_FOR_COMPLETE:
            evaluated = self._state_machine.evaluate_readiness(session)
            if evaluated != CheckoutState.READY_FOR_COMPLETE:
                from ucp_merchant.checkout.state_machine import (
                    InvalidTransitionError,
                )

                raise InvalidTransitionError(session.state, CheckoutState.COMPLETED)
            self._state_machine.transition(session, CheckoutState.READY_FOR_COMPLETE)

        self._state_machine.transition(session, CheckoutState.COMPLETED)

        # Assign a placeholder order_id; the OrderManager will create the
        # full order object and may override this.
        session.order_id = f"ord_{uuid.uuid4().hex[:16]}"
        self._sessions[session_id] = session

        logger.info(
            "checkout_session_completed",
            session_id=session_id,
            order_id=session.order_id,
            total=session.total.amount,
        )
        return session

    def abandon_session(self, session_id: str) -> CheckoutSession:
        """Mark a session as abandoned.

        Parameters
        ----------
        session_id:
            The session to abandon.

        Returns
        -------
        CheckoutSession
            The abandoned session.
        """
        session = self.get_session(session_id)

        if session.state in (CheckoutState.COMPLETED, CheckoutState.ABANDONED):
            return session

        self._state_machine.transition(session, CheckoutState.ABANDONED)
        self._sessions[session_id] = session

        logger.info("checkout_session_abandoned", session_id=session_id)
        return session

    # -- private helpers -----------------------------------------------------

    def _add_line_item(
        self,
        session: CheckoutSession,
        product_id: str,
        quantity: int = 1,
    ) -> CheckoutSession:
        """Add a product to the session's line items."""
        product = self._catalog.get(product_id)
        if product is None:
            raise ValueError(f"Product not found: {product_id!r}")
        if product.stock < quantity:
            raise ValueError(
                f"Insufficient stock for {product.name!r}: "
                f"requested {quantity}, available {product.stock}"
            )

        # Check if product already in cart -- update quantity
        for item in session.line_items:
            if item.product_id == product_id:
                item.quantity += quantity
                item.total_price = Money(
                    amount=round(item.unit_price.amount * item.quantity, 2),
                    currency=item.unit_price.currency,
                )
                return session

        line_item = LineItem(
            product_id=product.id,
            product_name=product.name,
            quantity=quantity,
            unit_price=product.price,
        )
        session.line_items.append(line_item)
        return session

    def _recalculate(self, session: CheckoutSession) -> CheckoutSession:
        """Recalculate totals and re-evaluate state."""
        session = self._totals.calculate(session)
        evaluated_state = self._state_machine.evaluate_readiness(session)
        if evaluated_state != session.state:
            try:
                self._state_machine.transition(session, evaluated_state)
            except Exception:
                # If the transition isn't valid from the current state,
                # log and leave the state as-is.
                logger.debug(
                    "state_evaluation_skipped",
                    session_id=session.id,
                    current=session.state.value,
                    evaluated=evaluated_state.value,
                )
        return session
