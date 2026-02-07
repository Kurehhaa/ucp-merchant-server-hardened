"""Order lifecycle management.

Handles order creation from completed checkout sessions, state advancement
through the fulfillment pipeline, cancellation, and simulated fulfillment
for demo purposes.
"""

from __future__ import annotations

import random
import string
import uuid
from datetime import datetime

import structlog

from ucp_merchant.models import (
    CheckoutSession,
    CheckoutState,
    Order,
    OrderState,
    OrderStatusEvent,
)

logger = structlog.get_logger(__name__)

# Valid forward transitions in the order lifecycle.
_ORDER_TRANSITIONS: dict[OrderState, OrderState] = {
    OrderState.CONFIRMED: OrderState.PROCESSING,
    OrderState.PROCESSING: OrderState.SHIPPED,
    OrderState.SHIPPED: OrderState.DELIVERED,
}

_CARRIERS = ["FedEx", "UPS", "USPS", "DHL"]


def _generate_tracking_number() -> str:
    """Generate a realistic-looking tracking number."""
    prefix = random.choice(["1Z", "92", "JD", "94"])
    digits = "".join(random.choices(string.digits, k=16))
    return f"{prefix}{digits}"


class OrderError(Exception):
    """Base error for order operations."""


class OrderNotFoundError(OrderError):
    """Raised when an order ID does not exist."""

    def __init__(self, order_id: str) -> None:
        self.order_id = order_id
        super().__init__(f"Order not found: {order_id!r}")


class InvalidOrderStateError(OrderError):
    """Raised when an order state transition is not permitted."""


class OrderManager:
    """Manages the order lifecycle after checkout completion.

    Parameters
    ----------
    tracker:
        Optional :class:`OrderTracker` instance for pushing status events
        to SSE subscribers.
    """

    def __init__(self, tracker: object | None = None) -> None:
        self._orders: dict[str, Order] = {}
        self._tracker = tracker  # will be set after init to break circular dep

    def set_tracker(self, tracker: object) -> None:
        """Inject the order tracker after construction."""
        self._tracker = tracker

    # -- creation ------------------------------------------------------------

    def create_order(self, session: CheckoutSession) -> Order:
        """Create an order from a completed checkout session.

        Parameters
        ----------
        session:
            A checkout session in COMPLETED state.

        Returns
        -------
        Order
            The newly created order in CONFIRMED state.

        Raises
        ------
        ValueError
            If the session is not in COMPLETED state.
        """
        if session.state != CheckoutState.COMPLETED:
            raise ValueError(
                f"Cannot create order from session in state {session.state.value!r}"
            )

        order_id = session.order_id or f"ord_{uuid.uuid4().hex[:16]}"
        now = datetime.utcnow()

        order = Order(
            id=order_id,
            checkout_session_id=session.id,
            state=OrderState.CONFIRMED,
            line_items=list(session.line_items),
            shipping_address=session.shipping_address,
            billing_address=session.billing_address,
            selected_shipping=session.selected_shipping,
            applied_discount=session.applied_discount,
            subtotal=session.subtotal,
            tax=session.tax,
            shipping_cost=session.shipping_cost,
            discount_amount=session.discount_amount,
            total=session.total,
            created_at=now,
            updated_at=now,
            history=[
                OrderStatusEvent(
                    event_type="order_confirmed",
                    message="Order has been confirmed and is awaiting processing.",
                    timestamp=now,
                    metadata={"order_id": order_id},
                )
            ],
        )

        self._orders[order_id] = order
        self._push_event(order_id, order.history[-1])

        logger.info(
            "order_created",
            order_id=order_id,
            session_id=session.id,
            total=order.total.amount,
        )
        return order

    # -- retrieval -----------------------------------------------------------

    def get_order(self, order_id: str) -> Order:
        """Retrieve an order by its ID.

        Raises
        ------
        OrderNotFoundError
        """
        order = self._orders.get(order_id)
        if order is None:
            raise OrderNotFoundError(order_id)
        return order

    def list_orders(self) -> list[Order]:
        """Return all orders, most recent first."""
        return sorted(
            self._orders.values(),
            key=lambda o: o.created_at,
            reverse=True,
        )

    # -- state management ----------------------------------------------------

    def advance_state(self, order_id: str) -> Order:
        """Advance the order to the next state in the lifecycle.

        Transitions: CONFIRMED -> PROCESSING -> SHIPPED -> DELIVERED.

        When transitioning to SHIPPED, a tracking number and carrier are
        automatically assigned.

        Returns
        -------
        Order
            The updated order.

        Raises
        ------
        OrderNotFoundError
        InvalidOrderStateError
        """
        order = self.get_order(order_id)

        if order.state == OrderState.CANCELLED:
            raise InvalidOrderStateError(
                f"Cannot advance cancelled order {order_id!r}"
            )

        next_state = _ORDER_TRANSITIONS.get(order.state)
        if next_state is None:
            raise InvalidOrderStateError(
                f"Order {order_id!r} is already in terminal state {order.state.value!r}"
            )

        now = datetime.utcnow()
        order.state = next_state
        order.updated_at = now

        # State-specific side effects
        if next_state == OrderState.SHIPPED:
            order.tracking_number = _generate_tracking_number()
            order.carrier = random.choice(_CARRIERS)
            order.tracking_url = (
                f"https://tracking.example.com/{order.carrier.lower()}/"
                f"{order.tracking_number}"
            )
            order.shipped_at = now
            event = OrderStatusEvent(
                event_type="order_shipped",
                message=(
                    f"Order has been shipped via {order.carrier}. "
                    f"Tracking: {order.tracking_number}"
                ),
                timestamp=now,
                metadata={
                    "tracking_number": order.tracking_number,
                    "carrier": order.carrier,
                    "tracking_url": order.tracking_url,
                },
            )
        elif next_state == OrderState.PROCESSING:
            event = OrderStatusEvent(
                event_type="order_processing",
                message="Order is being prepared for shipment.",
                timestamp=now,
                metadata={"order_id": order_id},
            )
        elif next_state == OrderState.DELIVERED:
            order.delivered_at = now
            event = OrderStatusEvent(
                event_type="order_delivered",
                message="Order has been delivered successfully.",
                timestamp=now,
                metadata={"order_id": order_id},
            )
        else:
            event = OrderStatusEvent(
                event_type=f"order_{next_state.value}",
                message=f"Order state changed to {next_state.value}.",
                timestamp=now,
            )

        order.history.append(event)
        self._orders[order_id] = order
        self._push_event(order_id, event)

        logger.info(
            "order_state_advanced",
            order_id=order_id,
            new_state=next_state.value,
        )
        return order

    def cancel_order(self, order_id: str) -> Order:
        """Cancel an order.

        Only orders in CONFIRMED or PROCESSING state can be cancelled.

        Raises
        ------
        OrderNotFoundError
        InvalidOrderStateError
        """
        order = self.get_order(order_id)

        if order.state not in (OrderState.CONFIRMED, OrderState.PROCESSING):
            raise InvalidOrderStateError(
                f"Cannot cancel order in state {order.state.value!r}"
            )

        now = datetime.utcnow()
        order.state = OrderState.CANCELLED
        order.cancelled_at = now
        order.updated_at = now

        event = OrderStatusEvent(
            event_type="order_cancelled",
            message="Order has been cancelled.",
            timestamp=now,
            metadata={"order_id": order_id},
        )
        order.history.append(event)
        self._orders[order_id] = order
        self._push_event(order_id, event)

        logger.info("order_cancelled", order_id=order_id)
        return order

    def simulate_fulfillment(self, order_id: str) -> Order:
        """Fast-forward an order to SHIPPED state with a tracking number.

        This is a convenience method for demos.  If the order is in
        CONFIRMED state it will first advance to PROCESSING, then to SHIPPED.

        Raises
        ------
        OrderNotFoundError
        InvalidOrderStateError
        """
        order = self.get_order(order_id)

        if order.state == OrderState.CANCELLED:
            raise InvalidOrderStateError(
                f"Cannot simulate fulfillment for cancelled order {order_id!r}"
            )

        # Advance through intermediate states
        if order.state == OrderState.CONFIRMED:
            order = self.advance_state(order_id)
        if order.state == OrderState.PROCESSING:
            order = self.advance_state(order_id)

        return order

    # -- private helpers -----------------------------------------------------

    def _push_event(self, order_id: str, event: OrderStatusEvent) -> None:
        """Push an event to the tracker if available."""
        if self._tracker is not None:
            try:
                # The tracker's notify_update is sync; it enqueues for SSE.
                self._tracker.notify_update(order_id, event)  # type: ignore[attr-defined]
            except Exception:
                logger.debug(
                    "tracker_push_failed",
                    order_id=order_id,
                    event=event.event_type,
                )
