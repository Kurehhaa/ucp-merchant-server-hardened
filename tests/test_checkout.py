"""Tests for checkout state machine and session management."""

import pytest

from ucp_merchant.catalog.products import ProductCatalog
from ucp_merchant.checkout.extensions import DiscountExtension, FulfillmentExtension
from ucp_merchant.checkout.session import CheckoutSessionManager, SessionNotFoundError
from ucp_merchant.checkout.state_machine import (
    CheckoutStateMachine,
    InvalidTransitionError,
)
from ucp_merchant.checkout.totals import TotalsCalculator
from ucp_merchant.config import UCPMerchantSettings
from ucp_merchant.models import (
    Address,
    CheckoutSession,
    CheckoutState,
    LineItem,
    Money,
)


@pytest.fixture
def settings():
    return UCPMerchantSettings(environment="testing")


@pytest.fixture
def catalog():
    return ProductCatalog()


@pytest.fixture
def session_manager(settings, catalog):
    return CheckoutSessionManager(settings=settings, catalog=catalog)


class TestStateMachine:
    def test_valid_transition_incomplete_to_requires_escalation(self):
        sm = CheckoutStateMachine()
        session = CheckoutSession(id="test", state=CheckoutState.INCOMPLETE)
        result = sm.transition(session, CheckoutState.REQUIRES_ESCALATION)
        assert result.state == CheckoutState.REQUIRES_ESCALATION

    def test_valid_transition_ready_to_completed(self):
        sm = CheckoutStateMachine()
        session = CheckoutSession(id="test", state=CheckoutState.READY_FOR_COMPLETE)
        result = sm.transition(session, CheckoutState.COMPLETED)
        assert result.state == CheckoutState.COMPLETED
        assert result.completed_at is not None

    def test_invalid_transition_completed_to_incomplete(self):
        sm = CheckoutStateMachine()
        session = CheckoutSession(id="test", state=CheckoutState.COMPLETED)
        with pytest.raises(InvalidTransitionError):
            sm.transition(session, CheckoutState.INCOMPLETE)

    def test_any_non_terminal_state_can_be_abandoned(self):
        sm = CheckoutStateMachine()
        for state in [
            CheckoutState.INCOMPLETE,
            CheckoutState.REQUIRES_ESCALATION,
            CheckoutState.READY_FOR_COMPLETE,
        ]:
            session = CheckoutSession(id="test", state=state)
            result = sm.transition(session, CheckoutState.ABANDONED)
            assert result.state == CheckoutState.ABANDONED

    def test_evaluate_readiness_empty_session(self):
        sm = CheckoutStateMachine()
        session = CheckoutSession(id="test")
        result = sm.evaluate_readiness(session)
        assert result == CheckoutState.INCOMPLETE

    def test_evaluate_readiness_with_items_only(self):
        sm = CheckoutStateMachine()
        session = CheckoutSession(
            id="test",
            line_items=[
                LineItem(
                    product_id="p1",
                    product_name="Test Product",
                    quantity=1,
                    unit_price=Money(amount=100.0),
                )
            ],
        )
        # Missing data the agent can still supply is INCOMPLETE, not an escalation.
        result = sm.evaluate_readiness(session)
        assert result == CheckoutState.INCOMPLETE

    def test_evaluate_readiness_all_fields_present(self):
        sm = CheckoutStateMachine()
        from ucp_merchant.models import ShippingOption

        session = CheckoutSession(
            id="test",
            line_items=[
                LineItem(
                    product_id="p1",
                    product_name="Test Product",
                    quantity=1,
                    unit_price=Money(amount=100.0),
                )
            ],
            shipping_address=Address(
                full_name="Test User",
                line1="123 Main St",
                city="SF",
                state="CA",
                postal_code="94105",
            ),
            selected_shipping=ShippingOption(
                id="shipping_standard",
                name="Standard",
                description="Standard shipping",
                price=Money(amount=5.99),
                estimated_days_min=5,
                estimated_days_max=7,
            ),
        )
        result = sm.evaluate_readiness(session)
        assert result == CheckoutState.READY_FOR_COMPLETE

    def test_evaluate_readiness_terminal_state_sticky(self):
        sm = CheckoutStateMachine()
        session = CheckoutSession(id="test", state=CheckoutState.COMPLETED)
        result = sm.evaluate_readiness(session)
        assert result == CheckoutState.COMPLETED


class TestCheckoutSession:
    def test_create_session(self, session_manager):
        line_items = [{"product_id": "laptop-001", "quantity": 1}]
        session = session_manager.create_session(line_items)
        assert session.id.startswith("cs_")
        assert len(session.line_items) == 1
        assert session.subtotal.amount > 0

    def test_create_session_with_multiple_items(self, session_manager):
        line_items = [
            {"product_id": "laptop-001", "quantity": 1},
            {"product_id": "kb-001", "quantity": 2},
        ]
        session = session_manager.create_session(line_items)
        assert len(session.line_items) == 2

    def test_get_session(self, session_manager):
        session = session_manager.create_session([{"product_id": "laptop-001", "quantity": 1}])
        retrieved = session_manager.get_session(session.id)
        assert retrieved.id == session.id

    def test_get_nonexistent_session(self, session_manager):
        with pytest.raises(SessionNotFoundError):
            session_manager.get_session("nonexistent")

    def test_update_address(self, session_manager):
        session = session_manager.create_session([{"product_id": "laptop-001", "quantity": 1}])
        updated = session_manager.update_session(
            session.id,
            {
                "shipping_address": {
                    "full_name": "Test User",
                    "line1": "123 Main St",
                    "city": "SF",
                    "state": "CA",
                    "postal_code": "94105",
                    "country": "US",
                }
            },
        )
        assert updated.shipping_address is not None
        assert updated.shipping_address.city == "SF"

    def test_state_transitions_to_ready(self, session_manager):
        session = session_manager.create_session([{"product_id": "laptop-001", "quantity": 1}])

        # Add address
        session_manager.update_session(
            session.id,
            {
                "shipping_address": {
                    "full_name": "Test User",
                    "line1": "123 Main St",
                    "city": "SF",
                    "state": "CA",
                    "postal_code": "94105",
                    "country": "US",
                }
            },
        )

        # Select shipping
        updated = session_manager.update_session(
            session.id, {"shipping_option_id": "shipping_standard"}
        )
        assert updated.state == CheckoutState.READY_FOR_COMPLETE

    def test_complete_session(self, session_manager):
        session = session_manager.create_session([{"product_id": "laptop-001", "quantity": 1}])
        session_manager.update_session(
            session.id,
            {
                "shipping_address": {
                    "full_name": "Test User",
                    "line1": "123 Main St",
                    "city": "SF",
                    "state": "CA",
                    "postal_code": "94105",
                    "country": "US",
                }
            },
        )
        session_manager.update_session(session.id, {"shipping_option_id": "shipping_standard"})

        completed = session_manager.complete_session(session.id)
        assert completed.state == CheckoutState.COMPLETED
        assert completed.order_id is not None

    def test_cannot_complete_incomplete(self, session_manager):
        session = session_manager.create_session([{"product_id": "laptop-001", "quantity": 1}])
        with pytest.raises(InvalidTransitionError):
            session_manager.complete_session(session.id)

    def test_abandon_session(self, session_manager):
        session = session_manager.create_session([{"product_id": "laptop-001", "quantity": 1}])
        abandoned = session_manager.abandon_session(session.id)
        assert abandoned.state == CheckoutState.ABANDONED

    def test_cannot_update_completed_session(self, session_manager):
        session = session_manager.create_session([{"product_id": "laptop-001", "quantity": 1}])
        session_manager.update_session(
            session.id,
            {
                "shipping_address": {
                    "full_name": "Test User",
                    "line1": "123 Main St",
                    "city": "SF",
                    "state": "CA",
                    "postal_code": "94105",
                    "country": "US",
                }
            },
        )
        session_manager.update_session(session.id, {"shipping_option_id": "shipping_standard"})
        session_manager.complete_session(session.id)

        with pytest.raises(ValueError, match="terminal state"):
            session_manager.update_session(session.id, {"discount_code": "SAVE10"})


class TestFulfillmentExtension:
    def test_get_shipping_options(self):
        ext = FulfillmentExtension()
        options = ext.get_shipping_options(subtotal=50.0)
        assert len(options) >= 3
        names = [o.name for o in options]
        assert "Standard Shipping" in names
        assert "Express Shipping" in names

    def test_free_shipping_threshold(self):
        ext = FulfillmentExtension()
        # Below threshold - no free shipping
        options_below = ext.get_shipping_options(subtotal=50.0)
        free_below = [o for o in options_below if o.price.amount == 0.0]
        assert len(free_below) == 0

        # Above threshold - free shipping available
        options_above = ext.get_shipping_options(subtotal=150.0)
        free_above = [o for o in options_above if o.price.amount == 0.0]
        assert len(free_above) >= 1

    def test_get_option_by_id(self):
        ext = FulfillmentExtension()
        option = ext.get_option_by_id("shipping_standard")
        assert option is not None
        assert option.name == "Standard Shipping"

    def test_get_option_by_id_not_found(self):
        ext = FulfillmentExtension()
        option = ext.get_option_by_id("nonexistent")
        assert option is None


class TestDiscountExtension:
    def test_valid_code(self):
        ext = DiscountExtension()
        code = ext.validate_code("SAVE10")
        assert code is not None
        assert code.discount_type == "percentage"
        assert code.value == 10.0

    def test_invalid_code(self):
        ext = DiscountExtension()
        with pytest.raises(ValueError, match="Invalid discount code"):
            ext.validate_code("INVALID")

    def test_freeship_code(self):
        ext = DiscountExtension()
        code = ext.validate_code("FREESHIP")
        assert code is not None
        assert code.discount_type == "free_shipping"

    def test_case_insensitive(self):
        ext = DiscountExtension()
        code = ext.validate_code("save10")
        assert code.code == "SAVE10"


class TestTotalsCalculator:
    def test_calculate_with_tax(self, settings):
        settings.tax_rate = 0.10  # 10% for easy math
        calc = TotalsCalculator(settings)
        session = CheckoutSession(
            id="test",
            state=CheckoutState.INCOMPLETE,
            line_items=[
                LineItem(
                    product_id="p1",
                    product_name="Test Product",
                    quantity=1,
                    unit_price=Money(amount=100.0),
                )
            ],
        )
        result = calc.calculate(session)
        assert result.subtotal.amount == 100.0
        assert result.tax.amount == 10.0
        assert result.total.amount == 110.0

    def test_calculate_with_discount(self, settings):
        from ucp_merchant.models import AppliedDiscount

        settings.tax_rate = 0.10
        calc = TotalsCalculator(settings)
        session = CheckoutSession(
            id="test",
            state=CheckoutState.INCOMPLETE,
            line_items=[
                LineItem(
                    product_id="p1",
                    product_name="Test Product",
                    quantity=1,
                    unit_price=Money(amount=100.0),
                )
            ],
            applied_discount=AppliedDiscount(
                code="SAVE10",
                description="10% off",
                discount_type="percentage",
                discount_amount=10.0,
            ),
        )
        result = calc.calculate(session)
        assert result.subtotal.amount == 100.0
        assert result.discount_amount.amount == 10.0
        # Tax on discounted amount: (100-10) * 0.10 = 9.0
        assert result.tax.amount == 9.0
        # Total: 90 + 9 = 99.0
        assert result.total.amount == 99.0

    def test_calculate_with_shipping(self, settings):
        from ucp_merchant.models import ShippingOption

        settings.tax_rate = 0.10
        calc = TotalsCalculator(settings)
        session = CheckoutSession(
            id="test",
            state=CheckoutState.INCOMPLETE,
            line_items=[
                LineItem(
                    product_id="p1",
                    product_name="Test Product",
                    quantity=1,
                    unit_price=Money(amount=100.0),
                )
            ],
            selected_shipping=ShippingOption(
                id="shipping_standard",
                name="Standard",
                description="Standard shipping",
                price=Money(amount=5.99),
                estimated_days_min=5,
                estimated_days_max=7,
            ),
        )
        result = calc.calculate(session)
        assert result.subtotal.amount == 100.0
        assert result.shipping_cost.amount == 5.99
        # Total: 100 + 10 (tax) + 5.99 (shipping) = 115.99
        assert result.total.amount == 115.99


ADDRESS = {
    "full_name": "Test User",
    "line1": "123 Main St",
    "city": "SF",
    "state": "CA",
    "postal_code": "94105",
    "country": "US",
}


def _ready_session(manager, product_id="laptop-001", quantity=1):
    session = manager.create_session([{"product_id": product_id, "quantity": quantity}])
    return manager.update_session(
        session.id,
        {"shipping_address": ADDRESS, "shipping_option_id": "shipping_standard"},
    )


class TestStateMachineGraph:
    @pytest.mark.parametrize(
        "target",
        [
            CheckoutState.INCOMPLETE,
            CheckoutState.REQUIRES_ESCALATION,
        ],
    )
    def test_ready_can_move_back(self, target):
        sm = CheckoutStateMachine()
        session = CheckoutSession(id="t", state=CheckoutState.READY_FOR_COMPLETE)
        assert sm.transition(session, target).state == target

    @pytest.mark.parametrize("final", [CheckoutState.COMPLETED, CheckoutState.ABANDONED])
    @pytest.mark.parametrize("target", list(CheckoutState))
    def test_terminal_states_have_no_exits(self, final, target):
        sm = CheckoutStateMachine()
        session = CheckoutSession(id="t", state=final)
        with pytest.raises(InvalidTransitionError):
            sm.transition(session, target)

    def test_escalation_reasons_take_priority_over_missing_data(self):
        sm = CheckoutStateMachine()
        session = CheckoutSession(id="t")
        assert sm.evaluate_readiness(session, ["out of stock"]) == CheckoutState.REQUIRES_ESCALATION

    def test_evaluate_readiness_does_not_mutate(self):
        sm = CheckoutStateMachine()
        session = CheckoutSession(id="t")
        sm.evaluate_readiness(session, ["out of stock"])
        assert session.state == CheckoutState.INCOMPLETE
        assert session.missing_fields == []
        assert session.escalation_reasons == []


class TestSessionStateTracking:
    def test_new_empty_session_lists_what_is_missing(self, session_manager):
        session = session_manager.create_session()
        assert session.state == CheckoutState.INCOMPLETE
        assert "No items in cart" in session.missing_fields
        assert "Shipping address required" in session.missing_fields
        assert session.escalation_reasons == []

    def test_items_only_session_is_incomplete_not_escalated(self, session_manager):
        session = session_manager.create_session([{"product_id": "laptop-001", "quantity": 1}])
        assert session.state == CheckoutState.INCOMPLETE
        assert session.escalation_reasons == []
        assert session.missing_fields == [
            "Shipping address required",
            "Shipping method not selected",
        ]

    def test_filling_everything_makes_session_ready(self, session_manager):
        session = _ready_session(session_manager)
        assert session.state == CheckoutState.READY_FOR_COMPLETE
        assert session.missing_fields == []

    def test_removing_all_items_sends_ready_cart_back_to_incomplete(self, session_manager):
        session = _ready_session(session_manager)
        updated = session_manager.update_session(session.id, {"remove_items": ["laptop-001"]})
        assert updated.state == CheckoutState.INCOMPLETE
        assert updated.missing_fields == ["No items in cart"]

    def test_emptied_cart_cannot_be_completed(self, session_manager):
        """Regression: a ready cart whose items were removed used to complete."""
        session = _ready_session(session_manager)
        session_manager.update_session(session.id, {"remove_items": ["laptop-001"]})
        with pytest.raises(InvalidTransitionError):
            session_manager.complete_session(session.id)
        assert session_manager.get_session(session.id).state == CheckoutState.INCOMPLETE

    def test_cart_can_become_ready_again_after_refilling(self, session_manager):
        session = _ready_session(session_manager)
        session_manager.update_session(session.id, {"remove_items": ["laptop-001"]})
        refilled = session_manager.update_session(
            session.id, {"add_items": [{"product_id": "kb-001", "quantity": 1}]}
        )
        assert refilled.state == CheckoutState.READY_FOR_COMPLETE

    def test_stock_shortfall_requires_escalation(self, session_manager, catalog):
        session = _ready_session(session_manager)
        # Someone else buys the stock after the cart was filled in.
        catalog.update_stock("laptop-001", -catalog.get("laptop-001").stock)
        with pytest.raises(InvalidTransitionError):
            session_manager.complete_session(session.id)
        current = session_manager.get_session(session.id)
        assert current.state == CheckoutState.REQUIRES_ESCALATION
        assert any("laptop" in r.lower() for r in current.escalation_reasons)

    def test_escalation_clears_when_stock_returns(self, session_manager, catalog):
        session = _ready_session(session_manager)
        original = catalog.get("laptop-001").stock
        catalog.update_stock("laptop-001", -original)
        with pytest.raises(InvalidTransitionError):
            session_manager.complete_session(session.id)
        catalog.update_stock("laptop-001", original)
        done = session_manager.complete_session(session.id)
        assert done.state == CheckoutState.COMPLETED
        assert done.escalation_reasons == []

    def test_completed_session_cannot_be_completed_twice(self, session_manager):
        session = _ready_session(session_manager)
        session_manager.complete_session(session.id)
        with pytest.raises(InvalidTransitionError):
            session_manager.complete_session(session.id)
