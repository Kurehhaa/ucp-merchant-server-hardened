"""UCP capability negotiation engine.

Implements server-selects negotiation: the merchant computes the intersection
of its own capabilities with those requested by the agent and returns the
agreed-upon set of capabilities, extensions, and payment handlers.
"""

from __future__ import annotations

import uuid

import structlog

from ucp_merchant.config import UCPMerchantSettings
from ucp_merchant.discovery.manifest import build_ucp_manifest
from ucp_merchant.models import (
    NegotiationRequest,
    NegotiationResult,
    UCPCapability,
    UCPExtension,
    UCPManifest,
    UCPPaymentHandler,
)

logger = structlog.get_logger(__name__)


class NegotiationEngine:
    """Server-selects capability negotiation.

    The engine loads the merchant manifest once and reuses it across
    negotiation requests.
    """

    def __init__(self, settings: UCPMerchantSettings) -> None:
        self._settings = settings
        self._manifest: UCPManifest = build_ucp_manifest(settings)

    # -- public API ----------------------------------------------------------

    def negotiate(self, request: NegotiationRequest) -> NegotiationResult:
        """Negotiate capabilities with an agent.

        Parameters
        ----------
        request:
            The agent's capability profile including requested capabilities,
            extensions, and supported payment handlers.

        Returns
        -------
        NegotiationResult
            The intersection of merchant and agent capabilities.
        """
        negotiation_id = f"neg_{uuid.uuid4().hex[:12]}"

        agreed_capabilities = self._intersect_capabilities(
            request.requested_capabilities
        )
        agreed_extensions = self._intersect_extensions(
            request.requested_extensions
        )
        agreed_payment_handlers = self._intersect_payment_handlers(
            request.supported_payment_handlers
        )

        # If the agent requested nothing specific, offer everything
        if not request.requested_capabilities:
            agreed_capabilities = list(self._manifest.capabilities)
        if not request.requested_extensions:
            agreed_extensions = list(self._manifest.extensions)
        if not request.supported_payment_handlers:
            agreed_payment_handlers = list(self._manifest.payment_handlers)

        result = NegotiationResult(
            negotiation_id=negotiation_id,
            agent_id=request.agent_id,
            agreed_capabilities=agreed_capabilities,
            agreed_extensions=agreed_extensions,
            agreed_payment_handlers=agreed_payment_handlers,
            session_endpoint=(
                f"{self._settings.ucp_base_url}/api/v1/checkout"
            ),
            metadata={
                "merchant_name": self._settings.merchant_name,
                "spec_version": self._settings.ucp_spec_version,
            },
        )

        logger.info(
            "negotiation_complete",
            negotiation_id=negotiation_id,
            agent_id=request.agent_id,
            capabilities=len(agreed_capabilities),
            extensions=len(agreed_extensions),
            payment_handlers=len(agreed_payment_handlers),
        )

        return result

    # -- private helpers -----------------------------------------------------

    def _intersect_capabilities(
        self, requested: list[str]
    ) -> list[UCPCapability]:
        """Return merchant capabilities whose IDs appear in *requested*."""
        requested_set = set(requested)
        return [
            cap
            for cap in self._manifest.capabilities
            if cap.id in requested_set
        ]

    def _intersect_extensions(
        self, requested: list[str]
    ) -> list[UCPExtension]:
        """Return merchant extensions whose IDs appear in *requested*."""
        requested_set = set(requested)
        return [
            ext
            for ext in self._manifest.extensions
            if ext.id in requested_set
        ]

    def _intersect_payment_handlers(
        self, requested: list[str]
    ) -> list[UCPPaymentHandler]:
        """Return merchant payment handlers whose IDs appear in *requested*."""
        requested_set = set(requested)
        return [
            ph
            for ph in self._manifest.payment_handlers
            if ph.id in requested_set
        ]
