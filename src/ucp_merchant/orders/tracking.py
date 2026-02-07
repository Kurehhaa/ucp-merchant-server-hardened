"""Order tracking with Server-Sent Events (SSE).

Provides a pub/sub mechanism where API consumers can subscribe to
real-time order status updates delivered as SSE streams.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from typing import AsyncIterator

import structlog

from ucp_merchant.models import OrderStatusEvent

logger = structlog.get_logger(__name__)


class OrderTracker:
    """Pub/sub hub for order status events.

    Subscribers receive :class:`OrderStatusEvent` objects via async
    iterators suitable for SSE endpoints.
    """

    def __init__(self) -> None:
        # order_id -> list of asyncio.Queue instances for active subscribers
        self._subscribers: dict[str, list[asyncio.Queue[OrderStatusEvent | None]]] = (
            defaultdict(list)
        )

    def notify_update(self, order_id: str, event: OrderStatusEvent) -> None:
        """Push an event to all active subscribers for *order_id*.

        This method is **synchronous** and safe to call from both sync and
        async contexts -- it enqueues events without awaiting.

        Parameters
        ----------
        order_id:
            The order whose subscribers should receive the event.
        event:
            The status event to broadcast.
        """
        queues = self._subscribers.get(order_id, [])
        for queue in queues:
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                logger.warning(
                    "subscriber_queue_full",
                    order_id=order_id,
                    event=event.event_type,
                )

        logger.debug(
            "event_broadcast",
            order_id=order_id,
            event=event.event_type,
            subscribers=len(queues),
        )

    async def subscribe(
        self,
        order_id: str,
        timeout: float = 300.0,
    ) -> AsyncIterator[OrderStatusEvent]:
        """Subscribe to status updates for *order_id*.

        Yields :class:`OrderStatusEvent` objects as they arrive.  The iterator
        will terminate if no event is received within *timeout* seconds, or
        when the order reaches a terminal state (delivered / cancelled).

        Parameters
        ----------
        order_id:
            The order to track.
        timeout:
            Maximum seconds to wait between events before closing.

        Yields
        ------
        OrderStatusEvent
        """
        queue: asyncio.Queue[OrderStatusEvent | None] = asyncio.Queue(maxsize=100)
        self._subscribers[order_id].append(queue)

        logger.info("subscriber_connected", order_id=order_id)

        try:
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=timeout)
                except asyncio.TimeoutError:
                    logger.info(
                        "subscriber_timeout",
                        order_id=order_id,
                        timeout=timeout,
                    )
                    break

                if event is None:
                    # Sentinel value to close the stream
                    break

                yield event

                # Terminal events end the stream
                if event.event_type in ("order_delivered", "order_cancelled"):
                    break
        finally:
            self._subscribers[order_id].remove(queue)
            if not self._subscribers[order_id]:
                del self._subscribers[order_id]
            logger.info("subscriber_disconnected", order_id=order_id)

    def disconnect_all(self, order_id: str) -> None:
        """Send a sentinel to all subscribers for *order_id*, closing their streams."""
        queues = self._subscribers.get(order_id, [])
        for queue in queues:
            try:
                queue.put_nowait(None)
            except asyncio.QueueFull:
                pass

    @property
    def active_subscriptions(self) -> dict[str, int]:
        """Return a snapshot of active subscriber counts per order."""
        return {
            order_id: len(queues)
            for order_id, queues in self._subscribers.items()
            if queues
        }
