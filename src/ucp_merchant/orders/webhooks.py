"""Webhook notification system for order events.

Allows external services to register webhook URLs and receive HTTP POST
notifications when order events occur.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

import httpx
import structlog

from ucp_merchant.models import WebhookPayload, WebhookRegistration

logger = structlog.get_logger(__name__)

# Event types that can be subscribed to.
SUPPORTED_EVENTS = [
    "order_confirmed",
    "order_processing",
    "order_shipped",
    "order_delivered",
    "order_cancelled",
]


class WebhookManager:
    """Manages webhook registrations and delivers event notifications.

    Parameters
    ----------
    timeout:
        HTTP request timeout in seconds for webhook deliveries.
    max_retries:
        Maximum delivery attempts per notification.
    """

    def __init__(
        self,
        timeout: float = 10.0,
        max_retries: int = 3,
    ) -> None:
        self._registrations: dict[str, WebhookRegistration] = {}
        self._timeout = timeout
        self._max_retries = max_retries
        self._delivery_log: list[dict[str, Any]] = []

    # -- registration --------------------------------------------------------

    def register_webhook(
        self,
        url: str,
        events: list[str],
    ) -> WebhookRegistration:
        """Register a new webhook endpoint.

        Parameters
        ----------
        url:
            The URL to POST event payloads to.
        events:
            List of event types to subscribe to.  Use ``["*"]`` for all events.

        Returns
        -------
        WebhookRegistration

        Raises
        ------
        ValueError
            If *events* contains unsupported event types (unless ``"*"``).
        """
        if events != ["*"]:
            invalid = [e for e in events if e not in SUPPORTED_EVENTS]
            if invalid:
                raise ValueError(
                    f"Unsupported event types: {invalid}. "
                    f"Supported: {SUPPORTED_EVENTS}"
                )

        webhook_id = f"wh_{uuid.uuid4().hex[:12]}"
        registration = WebhookRegistration(
            id=webhook_id,
            url=url,
            events=events,
            created_at=datetime.utcnow(),
            active=True,
        )
        self._registrations[webhook_id] = registration

        logger.info(
            "webhook_registered",
            webhook_id=webhook_id,
            url=url,
            events=events,
        )
        return registration

    def unregister_webhook(self, webhook_id: str) -> bool:
        """Deactivate a webhook registration.

        Returns ``True`` if the webhook existed and was deactivated.
        """
        reg = self._registrations.get(webhook_id)
        if reg is None:
            return False
        reg.active = False
        return True

    def list_webhooks(self) -> list[WebhookRegistration]:
        """Return all webhook registrations (active and inactive)."""
        return list(self._registrations.values())

    # -- notification --------------------------------------------------------

    async def notify(
        self,
        order_id: str,
        event_type: str,
        payload: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """Deliver an event notification to all matching webhook endpoints.

        Parameters
        ----------
        order_id:
            The order that generated the event.
        event_type:
            The event type (e.g. ``"order_shipped"``).
        payload:
            Arbitrary event data to include in the POST body.

        Returns
        -------
        list[dict]
            Delivery results for each matching webhook.
        """
        results: list[dict[str, Any]] = []

        for reg in self._registrations.values():
            if not reg.active:
                continue
            if reg.events != ["*"] and event_type not in reg.events:
                continue

            webhook_payload = WebhookPayload(
                webhook_id=reg.id,
                event_type=event_type,
                order_id=order_id,
                payload=payload,
                delivered_at=datetime.utcnow(),
            )

            result = await self._deliver(reg, webhook_payload)
            results.append(result)

        return results

    async def _deliver(
        self,
        registration: WebhookRegistration,
        payload: WebhookPayload,
    ) -> dict[str, Any]:
        """Attempt to deliver a payload to a webhook URL with retries."""
        delivery_result: dict[str, Any] = {
            "webhook_id": registration.id,
            "url": registration.url,
            "event_type": payload.event_type,
            "attempts": 0,
            "success": False,
            "status_code": None,
            "error": None,
        }

        for attempt in range(1, self._max_retries + 1):
            delivery_result["attempts"] = attempt
            try:
                async with httpx.AsyncClient(timeout=self._timeout) as client:
                    response = await client.post(
                        registration.url,
                        json=payload.model_dump(mode="json"),
                        headers={
                            "Content-Type": "application/json",
                            "X-Webhook-Id": registration.id,
                            "X-Event-Type": payload.event_type,
                        },
                    )
                    delivery_result["status_code"] = response.status_code
                    if response.is_success:
                        delivery_result["success"] = True
                        break
                    else:
                        delivery_result["error"] = (
                            f"HTTP {response.status_code}: {response.text[:200]}"
                        )
            except httpx.TimeoutException:
                delivery_result["error"] = f"Timeout after {self._timeout}s"
            except httpx.ConnectError as exc:
                delivery_result["error"] = f"Connection error: {exc}"
            except Exception as exc:
                delivery_result["error"] = f"Unexpected error: {exc}"
                break  # Don't retry on unexpected errors

            logger.warning(
                "webhook_delivery_retry",
                webhook_id=registration.id,
                attempt=attempt,
                error=delivery_result["error"],
            )

        self._delivery_log.append(delivery_result)

        if delivery_result["success"]:
            logger.info(
                "webhook_delivered",
                webhook_id=registration.id,
                url=registration.url,
                event_type=payload.event_type,
            )
        else:
            logger.error(
                "webhook_delivery_failed",
                webhook_id=registration.id,
                url=registration.url,
                error=delivery_result["error"],
            )

        return delivery_result

    def get_delivery_log(self, limit: int = 50) -> list[dict[str, Any]]:
        """Return the most recent delivery results."""
        return self._delivery_log[-limit:]
