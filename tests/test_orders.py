"""Order lifecycle and real-time tracking (SSE)."""

from __future__ import annotations

import asyncio
import contextlib

import pytest
import structlog
from httpx import ASGITransport, AsyncClient

from tests.ap2_helpers import ADMIN_HEADERS, ADMIN_KEY, SHIPPING_ADDRESS
from ucp_merchant.api import create_app
from ucp_merchant.catalog.products import ProductCatalog
from ucp_merchant.checkout.session import CheckoutSessionManager
from ucp_merchant.config import UCPMerchantSettings
from ucp_merchant.models import OrderState, OrderStatusEvent
from ucp_merchant.orders.lifecycle import (
    InvalidOrderStateError,
    OrderManager,
    OrderNotFoundError,
)
from ucp_merchant.orders.tracking import OrderTracker


def _order(manager: OrderManager):
    """Complete a checkout and turn it into an order."""
    checkout = CheckoutSessionManager(UCPMerchantSettings(environment="testing"), ProductCatalog())
    session = checkout.create_session([{"product_id": "kb-001", "quantity": 1}])
    checkout.update_session(
        session.id,
        {"shipping_address": SHIPPING_ADDRESS, "shipping_option_id": "shipping_standard"},
    )
    return manager.create_order(checkout.complete_session(session.id))


async def _wait_for_subscribers(tracker: OrderTracker, order_id: str, count: int = 1) -> None:
    for _ in range(200):
        if tracker.active_subscriptions.get(order_id, 0) >= count:
            return
        await asyncio.sleep(0.005)
    raise AssertionError("subscriber never connected")


async def _collect(tracker: OrderTracker, order_id: str, **kwargs) -> list[str]:
    return [event.event_type async for event in tracker.subscribe(order_id, **kwargs)]


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------


class TestOrderLifecycle:
    def test_new_order_starts_confirmed_with_history(self):
        order = _order(OrderManager())
        assert order.state == OrderState.CONFIRMED
        assert [e.event_type for e in order.history] == ["order_confirmed"]

    def test_advance_walks_the_whole_lifecycle(self):
        manager = OrderManager()
        order = _order(manager)
        states = [manager.advance_state(order.id).state for _ in range(3)]
        assert states == [OrderState.PROCESSING, OrderState.SHIPPED, OrderState.DELIVERED]
        final = manager.get_order(order.id)
        assert final.tracking_number  # assigned on shipping
        assert [e.event_type for e in final.history][-1] == "order_delivered"

    def test_delivered_order_cannot_advance_or_cancel(self):
        manager = OrderManager()
        order = _order(manager)
        for _ in range(3):
            manager.advance_state(order.id)
        with pytest.raises(InvalidOrderStateError):
            manager.advance_state(order.id)
        with pytest.raises(InvalidOrderStateError):
            manager.cancel_order(order.id)

    def test_cancelled_order_is_final(self):
        manager = OrderManager()
        order = _order(manager)
        cancelled = manager.cancel_order(order.id)
        assert cancelled.state == OrderState.CANCELLED
        assert cancelled.cancelled_at is not None
        with pytest.raises(InvalidOrderStateError):
            manager.advance_state(order.id)
        with pytest.raises(InvalidOrderStateError):
            manager.cancel_order(order.id)

    def test_shipped_order_cannot_be_cancelled(self):
        manager = OrderManager()
        order = _order(manager)
        manager.simulate_fulfillment(order.id)
        assert manager.get_order(order.id).state == OrderState.SHIPPED
        with pytest.raises(InvalidOrderStateError):
            manager.cancel_order(order.id)

    def test_simulate_fulfillment_fast_forwards_and_is_idempotent(self):
        manager = OrderManager()
        order = _order(manager)
        shipped = manager.simulate_fulfillment(order.id)
        assert shipped.state == OrderState.SHIPPED
        assert shipped.tracking_number
        # Calling it again changes nothing.
        again = manager.simulate_fulfillment(order.id)
        assert again.state == OrderState.SHIPPED
        assert again.tracking_number == shipped.tracking_number

    def test_simulate_fulfillment_rejects_cancelled_orders(self):
        manager = OrderManager()
        order = _order(manager)
        manager.cancel_order(order.id)
        with pytest.raises(InvalidOrderStateError):
            manager.simulate_fulfillment(order.id)

    def test_unknown_order(self):
        manager = OrderManager()
        for call in (manager.get_order, manager.advance_state, manager.cancel_order):
            with pytest.raises(OrderNotFoundError):
                call("ord_missing")

    def test_list_orders(self):
        manager = OrderManager()
        first, second = _order(manager), _order(manager)
        assert {o.id for o in manager.list_orders()} == {first.id, second.id}


# ---------------------------------------------------------------------------
# Tracker
# ---------------------------------------------------------------------------


class TestOrderTracker:
    def test_notify_without_subscribers_does_not_raise(self):
        """Regression: logging with an `event=` kwarg used to raise TypeError."""
        OrderTracker().notify_update("ord_1", OrderStatusEvent(event_type="x", message="m"))

    async def test_subscriber_receives_events_until_delivered(self):
        tracker = OrderTracker()
        manager = OrderManager(tracker=tracker)
        order = _order(manager)

        collector = asyncio.create_task(_collect(tracker, order.id))
        await _wait_for_subscribers(tracker, order.id)
        for _ in range(3):
            manager.advance_state(order.id)

        assert await asyncio.wait_for(collector, 2) == [
            "order_processing",
            "order_shipped",
            "order_delivered",
        ]
        assert tracker.active_subscriptions == {}

    async def test_cancellation_ends_the_stream(self):
        tracker = OrderTracker()
        manager = OrderManager(tracker=tracker)
        order = _order(manager)
        collector = asyncio.create_task(_collect(tracker, order.id))
        await _wait_for_subscribers(tracker, order.id)
        manager.cancel_order(order.id)
        assert await asyncio.wait_for(collector, 2) == ["order_cancelled"]

    async def test_every_subscriber_gets_every_event(self):
        tracker = OrderTracker()
        manager = OrderManager(tracker=tracker)
        order = _order(manager)
        collectors = [asyncio.create_task(_collect(tracker, order.id)) for _ in range(3)]
        await _wait_for_subscribers(tracker, order.id, count=3)
        assert tracker.active_subscriptions == {order.id: 3}
        manager.cancel_order(order.id)
        results = await asyncio.wait_for(asyncio.gather(*collectors), 2)
        assert results == [["order_cancelled"]] * 3

    async def test_events_for_other_orders_are_not_delivered(self):
        tracker = OrderTracker()
        manager = OrderManager(tracker=tracker)
        mine, other = _order(manager), _order(manager)
        collector = asyncio.create_task(_collect(tracker, mine.id))
        await _wait_for_subscribers(tracker, mine.id)
        manager.cancel_order(other.id)  # must not reach `mine`
        manager.cancel_order(mine.id)
        assert await asyncio.wait_for(collector, 2) == ["order_cancelled"]

    async def test_stream_closes_after_timeout_and_cleans_up(self):
        tracker = OrderTracker()
        assert await asyncio.wait_for(_collect(tracker, "ord_idle", timeout=0.05), 2) == []
        assert tracker.active_subscriptions == {}

    async def test_disconnect_all_closes_streams(self):
        tracker = OrderTracker()
        collector = asyncio.create_task(_collect(tracker, "ord_1", timeout=5))
        await _wait_for_subscribers(tracker, "ord_1")
        tracker.disconnect_all("ord_1")
        assert await asyncio.wait_for(collector, 2) == []

    async def test_abandoned_subscriber_is_removed(self):
        tracker = OrderTracker()
        stream = tracker.subscribe("ord_1", timeout=5)
        waiting = asyncio.create_task(stream.__anext__())
        await _wait_for_subscribers(tracker, "ord_1")
        waiting.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await waiting
        await stream.aclose()
        assert tracker.active_subscriptions == {}

    def test_full_queue_drops_events_without_raising(self):
        tracker = OrderTracker()
        queue: asyncio.Queue[OrderStatusEvent | None] = asyncio.Queue(maxsize=1)
        event = OrderStatusEvent(event_type="order_processing", message="m")
        queue.put_nowait(event)
        tracker._subscribers["ord_1"].append(queue)
        with structlog.testing.capture_logs() as logs:
            tracker.notify_update("ord_1", event)
        assert any(log["event"] == "subscriber_queue_full" for log in logs)

    def test_broken_tracker_never_breaks_an_order_change(self):
        class BrokenTracker:
            def notify_update(self, order_id, event):
                raise RuntimeError("boom")

        manager = OrderManager(tracker=BrokenTracker())
        with structlog.testing.capture_logs() as logs:
            order = _order(manager)
            assert manager.advance_state(order.id).state == OrderState.PROCESSING
        assert any(log["event"] == "tracker_push_failed" for log in logs)


# ---------------------------------------------------------------------------
# SSE endpoint
# ---------------------------------------------------------------------------


@pytest.fixture
async def sse_client():
    # sse-starlette keeps a process-wide anyio Event; reset it per test loop.
    from sse_starlette.sse import AppStatus

    AppStatus.should_exit_event = None
    application = create_app(UCPMerchantSettings(environment="testing", admin_api_key=ADMIN_KEY))
    transport = ASGITransport(app=application)
    async with AsyncClient(
        transport=transport, base_url="http://test", headers=ADMIN_HEADERS
    ) as client:
        yield application, client


class TestTrackEndpoint:
    async def test_unknown_order_is_404(self, sse_client):
        _, client = sse_client
        assert (await client.get("/api/v1/orders/ord_missing/track")).status_code == 404

    async def test_stream_delivers_status_events(self, sse_client):
        application, client = sse_client
        manager = application.state.order_manager
        order = _order(manager)

        request = asyncio.create_task(client.get(f"/api/v1/orders/{order.id}/track"))
        await _wait_for_subscribers(manager._tracker, order.id)
        for _ in range(3):
            manager.advance_state(order.id)
        response = await asyncio.wait_for(request, 5)

        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        for expected in ("order_processing", "order_shipped", "order_delivered"):
            assert f"event: {expected}" in response.text


# ---------------------------------------------------------------------------
# Atomic completion: no completed checkout without an order
# ---------------------------------------------------------------------------


class TestAtomicCompletion:
    def _ready(self):
        checkout = CheckoutSessionManager(
            UCPMerchantSettings(environment="testing"), ProductCatalog()
        )
        session = checkout.create_session([{"product_id": "kb-001", "quantity": 1}])
        checkout.update_session(
            session.id,
            {"shipping_address": SHIPPING_ADDRESS, "shipping_option_id": "shipping_standard"},
        )
        return checkout, session.id

    def test_session_and_order_are_committed_together(self):
        checkout, session_id = self._ready()
        orders = OrderManager()
        session, order = checkout.complete_with_order(session_id, orders.create_order)
        assert session.state.value == "completed"
        assert session.order_id == order.id
        assert checkout.get_session(session_id).order_id == order.id
        assert orders.get_order(order.id).checkout_session_id == session_id

    def test_failed_order_creation_leaves_the_session_untouched(self):
        checkout, session_id = self._ready()

        def failing(session):
            raise RuntimeError("database down")

        with pytest.raises(RuntimeError):
            checkout.complete_with_order(session_id, failing)

        stored = checkout.get_session(session_id)
        assert stored.state.value == "ready_for_complete"
        assert stored.order_id is None
        assert stored.completed_at is None

    def test_checkout_can_be_retried_after_a_failure(self):
        checkout, session_id = self._ready()
        with pytest.raises(RuntimeError):
            checkout.complete_with_order(
                session_id, lambda s: (_ for _ in ()).throw(RuntimeError())
            )
        orders = OrderManager()
        session, order = checkout.complete_with_order(session_id, orders.create_order)
        assert session.state.value == "completed"
        assert len(orders.list_orders()) == 1
        assert orders.get_order(order.id)

    def test_cannot_complete_twice(self):
        checkout, session_id = self._ready()
        orders = OrderManager()
        checkout.complete_with_order(session_id, orders.create_order)
        from ucp_merchant.checkout.state_machine import InvalidTransitionError

        with pytest.raises(InvalidTransitionError):
            checkout.complete_with_order(session_id, orders.create_order)
        assert len(orders.list_orders()) == 1

    async def test_api_failure_keeps_checkout_and_mandate_usable(self, monkeypatch):
        from tests.ap2_helpers import AgentSimulator, create_ready_session

        application = create_app(UCPMerchantSettings(environment="testing"))
        transport = ASGITransport(app=application, raise_app_exceptions=False)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            agent = AgentSimulator(application.state.settings.merchant_id)
            await agent.register(client)
            session = await create_ready_session(client)
            session_id = session["id"]
            mandate = agent.cart_mandate(session).model_dump(mode="json")

            manager = application.state.order_manager
            original = manager.create_order

            def failing(completed_session):
                raise RuntimeError("database down")

            monkeypatch.setattr(manager, "create_order", failing)
            failed = await client.post(
                f"/api/v1/checkout/{session_id}/complete", json={"cart_mandate": mandate}
            )
            assert failed.status_code == 500
            assert "database down" not in failed.text
            state = (await client.get(f"/api/v1/checkout/{session_id}")).json()["state"]
            assert state == "ready_for_complete"

            # Same mandate, same checkout: works once the fault is fixed.
            monkeypatch.setattr(manager, "create_order", original)
            retry = await client.post(
                f"/api/v1/checkout/{session_id}/complete", json={"cart_mandate": mandate}
            )
            assert retry.status_code == 200, retry.text
            assert retry.json()["session"]["state"] == "completed"
            assert retry.json()["order"]["id"] == retry.json()["session"]["order_id"]
