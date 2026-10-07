"""Checkout state machine with validated transitions.

States and their meaning::

    INCOMPLETE           required data is missing (items, shipping address,
                         shipping method).  The agent can fix this itself by
                         updating the session; see ``session.missing_fields``.
    REQUIRES_ESCALATION  the cart is not blocked by missing data but a human
                         buyer must step in (for example an item is no longer
                         available in the requested quantity); see
                         ``session.escalation_reasons``.
    READY_FOR_COMPLETE   everything is present and nothing needs a human.
    COMPLETED / ABANDONED  terminal.

The first three states are *derived* from the session contents, so every move
between them is allowed in both directions: removing the shipping address from
a ready cart sends it back to ``INCOMPLETE``.  Only COMPLETED and ABANDONED
are final.

:meth:`CheckoutStateMachine.evaluate_readiness` computes the state the
session should be in; :meth:`CheckoutStateMachine.sync` applies it.
"""

from __future__ import annotations

import structlog

from ucp_merchant.models import CheckoutSession, CheckoutState
from ucp_merchant.utils import utcnow

logger = structlog.get_logger(__name__)

_OPEN_STATES = {
    CheckoutState.INCOMPLETE,
    CheckoutState.REQUIRES_ESCALATION,
    CheckoutState.READY_FOR_COMPLETE,
}

# Valid state transitions -- edges in the lifecycle graph.
_VALID_TRANSITIONS: dict[CheckoutState, set[CheckoutState]] = {
    CheckoutState.INCOMPLETE: {
        CheckoutState.REQUIRES_ESCALATION,
        CheckoutState.READY_FOR_COMPLETE,
        CheckoutState.ABANDONED,
    },
    CheckoutState.REQUIRES_ESCALATION: {
        CheckoutState.READY_FOR_COMPLETE,
        CheckoutState.INCOMPLETE,
        CheckoutState.ABANDONED,
    },
    CheckoutState.READY_FOR_COMPLETE: {
        CheckoutState.COMPLETED,
        CheckoutState.INCOMPLETE,
        CheckoutState.REQUIRES_ESCALATION,
        CheckoutState.ABANDONED,
    },
    CheckoutState.COMPLETED: set(),  # terminal
    CheckoutState.ABANDONED: set(),  # terminal
}


class InvalidTransitionError(Exception):
    """Raised when a state transition is not permitted."""

    def __init__(self, current: CheckoutState, target: CheckoutState) -> None:
        self.current = current
        self.target = target
        super().__init__(f"Cannot transition from {current.value!r} to {target.value!r}")


class CheckoutStateMachine:
    """Validates and executes checkout session state transitions."""

    def transition(
        self,
        session: CheckoutSession,
        target_state: CheckoutState,
    ) -> CheckoutSession:
        """Move *session* to *target_state*.

        Raises
        ------
        InvalidTransitionError
            If the transition is not permitted by the lifecycle graph.
        """
        allowed = _VALID_TRANSITIONS.get(session.state, set())
        if target_state not in allowed:
            raise InvalidTransitionError(session.state, target_state)

        previous = session.state
        session.state = target_state
        now = utcnow()
        session.updated_at = now
        if target_state == CheckoutState.COMPLETED:
            session.completed_at = now

        logger.info(
            "checkout_state_transition",
            session_id=session.id,
            previous=previous.value,
            new=target_state.value,
        )
        return session

    @staticmethod
    def missing_requirements(session: CheckoutSession) -> list[str]:
        """List the data a session still lacks before it can be completed."""
        missing: list[str] = []
        if not session.line_items:
            missing.append("No items in cart")
        if session.shipping_address is None:
            missing.append("Shipping address required")
        if session.selected_shipping is None:
            missing.append("Shipping method not selected")
        return missing

    def evaluate_readiness(
        self,
        session: CheckoutSession,
        escalation_reasons: list[str] | None = None,
    ) -> CheckoutState:
        """Determine the state the session *should* be in.

        Decision order:

        1. COMPLETED / ABANDONED are final -- returned unchanged.
        2. Any *escalation_reasons* (a human must decide) -- REQUIRES_ESCALATION.
        3. Any missing data -- INCOMPLETE.
        4. Otherwise -- READY_FOR_COMPLETE.

        This does not mutate the session; use :meth:`sync` to apply the result.
        """
        if session.state not in _OPEN_STATES:
            return session.state
        if escalation_reasons:
            return CheckoutState.REQUIRES_ESCALATION
        if self.missing_requirements(session):
            return CheckoutState.INCOMPLETE
        return CheckoutState.READY_FOR_COMPLETE

    def sync(
        self,
        session: CheckoutSession,
        escalation_reasons: list[str] | None = None,
    ) -> CheckoutSession:
        """Refresh ``missing_fields`` / ``escalation_reasons`` and the state.

        Safe to call after any change to the session.  It never silently
        ignores an invalid transition: the graph allows every move between
        the open states, so a failure here would be a programming error.
        """
        if session.state not in _OPEN_STATES:
            return session

        session.missing_fields = self.missing_requirements(session)
        session.escalation_reasons = list(escalation_reasons or [])

        target = self.evaluate_readiness(session, session.escalation_reasons)
        if target != session.state:
            self.transition(session, target)
        return session
