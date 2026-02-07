"""Checkout state machine with validated transitions.

The lifecycle is::

    INCOMPLETE  ---->  REQUIRES_ESCALATION  ---->  READY_FOR_COMPLETE  ---->  COMPLETED
        |                                                |
        +--- (direct if all fields present) ------------>+
        |                                                |
        +--- (any state) ---->  ABANDONED                |

:meth:`CheckoutStateMachine.evaluate_readiness` inspects the session to
automatically determine the correct state based on which fields are filled.
"""

from __future__ import annotations

from datetime import datetime

import structlog

from ucp_merchant.models import CheckoutSession, CheckoutState

logger = structlog.get_logger(__name__)

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
        super().__init__(
            f"Cannot transition from {current.value!r} to {target.value!r}"
        )


class CheckoutStateMachine:
    """Validates and executes checkout session state transitions."""

    def transition(
        self,
        session: CheckoutSession,
        target_state: CheckoutState,
    ) -> CheckoutSession:
        """Attempt to move *session* to *target_state*.

        Parameters
        ----------
        session:
            The checkout session to transition.
        target_state:
            The desired next state.

        Returns
        -------
        CheckoutSession
            The session with its ``state`` and ``updated_at`` fields modified.

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
        session.updated_at = datetime.utcnow()

        if target_state == CheckoutState.COMPLETED:
            session.completed_at = datetime.utcnow()

        logger.info(
            "checkout_state_transition",
            session_id=session.id,
            previous=previous.value,
            new=target_state.value,
        )
        return session

    def evaluate_readiness(self, session: CheckoutSession) -> CheckoutState:
        """Determine the appropriate state based on the session's fields.

        Decision logic:

        1. If ``state`` is COMPLETED or ABANDONED -- no change.
        2. If all required fields are present -- READY_FOR_COMPLETE.
        3. If some but not all fields are present -- REQUIRES_ESCALATION with
           reasons explaining what is missing.
        4. Otherwise -- INCOMPLETE.

        Returns
        -------
        CheckoutState
            The evaluated state.  Does **not** mutate the session itself;
            the caller should use :meth:`transition` to apply the change.
        """
        # Terminal states are sticky.
        if session.state in (CheckoutState.COMPLETED, CheckoutState.ABANDONED):
            return session.state

        missing: list[str] = []

        if not session.line_items:
            missing.append("No items in cart")
        if session.shipping_address is None:
            missing.append("Shipping address required")
        if session.selected_shipping is None:
            missing.append("Shipping method not selected")

        session.escalation_reasons = missing

        if not missing:
            return CheckoutState.READY_FOR_COMPLETE

        # If at least one critical field is present we consider it "in progress"
        has_any = (
            bool(session.line_items)
            or session.shipping_address is not None
            or session.selected_shipping is not None
        )
        if has_any:
            return CheckoutState.REQUIRES_ESCALATION

        return CheckoutState.INCOMPLETE
