"""Webhook notification system for order events.

Allows external services to register webhook URLs and receive HTTP POST
notifications when order events occur.
"""

from __future__ import annotations

import ipaddress
import socket
import uuid
from collections.abc import Callable
from datetime import datetime
from typing import Any
from urllib.parse import urlsplit

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


class WebhookURLError(ValueError):
    """Raised when a webhook URL is not safe to deliver to."""


# ``socket.getaddrinfo``-compatible resolver: (host, port) -> address infos.
Resolver = Callable[[str, int], list[Any]]


def _default_resolver(host: str, port: int) -> list[Any]:
    return socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)


def validate_webhook_url(
    url: str,
    *,
    allow_private_hosts: bool = False,
    resolver: Resolver = _default_resolver,
) -> None:
    """Reject webhook URLs that would make the server call somewhere unsafe.

    A webhook makes the *server* issue an HTTP request to a caller-chosen
    address (SSRF).  Unless *allow_private_hosts* is set, the URL must be
    https, carry no credentials, and every address its host resolves to must
    be publicly routable (no loopback, private, link-local -- e.g. cloud
    metadata at 169.254.169.254 -- carrier-grade NAT, multicast or reserved
    ranges).

    Raises
    ------
    WebhookURLError
        If the URL is not allowed.
    """
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError as exc:
        raise WebhookURLError(f"Invalid webhook URL: {exc}") from None

    if parts.scheme not in ("http", "https"):
        raise WebhookURLError("Webhook URL must use http or https")
    host = parts.hostname
    if not host:
        raise WebhookURLError("Webhook URL must include a host")
    if parts.username or parts.password:
        raise WebhookURLError("Webhook URL must not contain credentials")
    if allow_private_hosts:
        return
    if parts.scheme != "https":
        raise WebhookURLError("Webhook URL must use https")

    # An IP literal needs no DNS: check it directly instead of trusting a resolver.
    try:
        addresses = [str(ipaddress.ip_address(host))]
    except ValueError:
        try:
            infos = resolver(host, port or 443)
        except OSError:
            raise WebhookURLError(f"Webhook host {host!r} could not be resolved") from None
        addresses = [str(info[4][0]) for info in infos]
        if not addresses:
            raise WebhookURLError(
                f"Webhook host {host!r} could not be resolved"
            ) from None

    for address in addresses:
        ip = ipaddress.ip_address(address.split("%", 1)[0])  # drop IPv6 zone id
        if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
            ip = ip.ipv4_mapped
        if not ip.is_global or ip.is_multicast or ip.is_unspecified:
            raise WebhookURLError(
                f"Webhook host {host!r} resolves to a non-public address"
            )


class WebhookManager:
    """Manages webhook registrations and delivers event notifications.

    Parameters
    ----------
    timeout:
        HTTP request timeout in seconds for webhook deliveries.
    max_retries:
        Maximum delivery attempts per notification.
    allow_private_hosts:
        Skip the SSRF checks on webhook URLs (local demos only).
    resolver:
        Hostname resolver used by the URL check (injectable for tests).
    """

    def __init__(
        self,
        timeout: float = 10.0,
        max_retries: int = 3,
        allow_private_hosts: bool = False,
        resolver: Resolver = _default_resolver,
    ) -> None:
        self._allow_private_hosts = allow_private_hosts
        self._resolver = resolver
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
        WebhookURLError
            If *url* is not safe to deliver to (see :func:`validate_webhook_url`).
        ValueError
            If *events* contains unsupported event types (unless ``"*"``).
        """
        self._check_url(url)
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

    def _check_url(self, url: str) -> None:
        validate_webhook_url(
            url,
            allow_private_hosts=self._allow_private_hosts,
            resolver=self._resolver,
        )

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

        # DNS can change after registration: check again right before sending.
        try:
            self._check_url(registration.url)
        except WebhookURLError as exc:
            delivery_result["error"] = f"Blocked: {exc}"
            self._delivery_log.append(delivery_result)
            logger.error(
                "webhook_delivery_blocked",
                webhook_id=registration.id,
                error=delivery_result["error"],
            )
            return delivery_result

        for attempt in range(1, self._max_retries + 1):
            delivery_result["attempts"] = attempt
            try:
                async with httpx.AsyncClient(
                    timeout=self._timeout, follow_redirects=False
                ) as client:
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
                        # Status only: the body may come from an internal service.
                        delivery_result["error"] = f"HTTP {response.status_code}"
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
