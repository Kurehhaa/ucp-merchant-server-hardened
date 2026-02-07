"""MCP tool definitions that wrap UCP merchant operations.

Provides eight tools that an MCP-compatible agent can discover and invoke
to interact with the merchant server.  Each tool has a JSON Schema for its
inputs and a handler function that delegates to the underlying service layer.
"""

from __future__ import annotations

from typing import Any

import structlog

from ucp_merchant.models import MCPToolDefinition, MCPToolResult

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Tool definitions (JSON Schema input descriptions)
# ---------------------------------------------------------------------------

TOOL_DEFINITIONS: list[MCPToolDefinition] = [
    MCPToolDefinition(
        name="search_products",
        description=(
            "Search the TechVault Electronics product catalog. Supports "
            "full-text search, category/brand/price filters, and sorting."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Free-text search query for product names and descriptions.",
                },
                "category": {
                    "type": "string",
                    "enum": ["laptops", "keyboards", "mice", "monitors", "accessories"],
                    "description": "Filter by product category.",
                },
                "min_price": {
                    "type": "number",
                    "description": "Minimum price filter (inclusive).",
                },
                "max_price": {
                    "type": "number",
                    "description": "Maximum price filter (inclusive).",
                },
                "brand": {
                    "type": "string",
                    "description": "Filter by brand name.",
                },
                "in_stock": {
                    "type": "boolean",
                    "description": "If true, only return products that are in stock.",
                },
                "sort_by": {
                    "type": "string",
                    "enum": ["relevance", "price_asc", "price_desc", "name", "rating"],
                    "description": "Sort order for results.",
                    "default": "relevance",
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of results.",
                    "default": 20,
                },
                "offset": {
                    "type": "integer",
                    "description": "Number of results to skip.",
                    "default": 0,
                },
            },
        },
    ),
    MCPToolDefinition(
        name="get_product",
        description="Get detailed information about a specific product by its ID.",
        inputSchema={
            "type": "object",
            "properties": {
                "product_id": {
                    "type": "string",
                    "description": "The product ID (e.g. 'laptop-001').",
                },
            },
            "required": ["product_id"],
        },
    ),
    MCPToolDefinition(
        name="create_checkout",
        description=(
            "Create a new checkout session with one or more products. "
            "Returns the session ID needed for subsequent checkout operations."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "items": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "product_id": {"type": "string"},
                            "quantity": {"type": "integer", "default": 1},
                        },
                        "required": ["product_id"],
                    },
                    "description": "Products to add to the checkout.",
                },
            },
            "required": ["items"],
        },
    ),
    MCPToolDefinition(
        name="update_checkout",
        description=(
            "Update a checkout session: add/remove items, set shipping address, "
            "or select a shipping method."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "session_id": {
                    "type": "string",
                    "description": "The checkout session ID.",
                },
                "add_items": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "product_id": {"type": "string"},
                            "quantity": {"type": "integer", "default": 1},
                        },
                        "required": ["product_id"],
                    },
                    "description": "Items to add to the cart.",
                },
                "remove_items": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Product IDs to remove from the cart.",
                },
                "shipping_address": {
                    "type": "object",
                    "properties": {
                        "full_name": {"type": "string"},
                        "line1": {"type": "string"},
                        "line2": {"type": "string"},
                        "city": {"type": "string"},
                        "state": {"type": "string"},
                        "postal_code": {"type": "string"},
                        "country": {"type": "string", "default": "US"},
                        "phone": {"type": "string"},
                    },
                    "required": ["full_name", "line1", "city", "state", "postal_code"],
                    "description": "Shipping address.",
                },
                "billing_address": {
                    "type": "object",
                    "description": "Billing address (same schema as shipping_address).",
                },
                "shipping_option_id": {
                    "type": "string",
                    "description": "ID of the shipping option to select.",
                },
                "discount_code": {
                    "type": "string",
                    "description": "Coupon code to apply.",
                },
            },
            "required": ["session_id"],
        },
    ),
    MCPToolDefinition(
        name="apply_discount",
        description="Apply a discount/coupon code to a checkout session.",
        inputSchema={
            "type": "object",
            "properties": {
                "session_id": {
                    "type": "string",
                    "description": "The checkout session ID.",
                },
                "code": {
                    "type": "string",
                    "description": "The discount code (e.g. 'SAVE10', 'SAVE20', 'FREESHIP', 'WELCOME').",
                },
            },
            "required": ["session_id", "code"],
        },
    ),
    MCPToolDefinition(
        name="select_shipping",
        description=(
            "Select a shipping method for a checkout session. "
            "Available options: shipping_standard ($5.99), shipping_express ($12.99), "
            "shipping_next_day ($24.99), shipping_free (orders over $100)."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "session_id": {
                    "type": "string",
                    "description": "The checkout session ID.",
                },
                "shipping_option_id": {
                    "type": "string",
                    "enum": [
                        "shipping_standard",
                        "shipping_express",
                        "shipping_next_day",
                        "shipping_free",
                    ],
                    "description": "The ID of the shipping option to select.",
                },
            },
            "required": ["session_id", "shipping_option_id"],
        },
    ),
    MCPToolDefinition(
        name="complete_checkout",
        description=(
            "Complete a checkout session and create an order. "
            "The session must have items, a shipping address, and a shipping method selected."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "session_id": {
                    "type": "string",
                    "description": "The checkout session ID to complete.",
                },
            },
            "required": ["session_id"],
        },
    ),
    MCPToolDefinition(
        name="get_order",
        description="Get the current status and details of an order.",
        inputSchema={
            "type": "object",
            "properties": {
                "order_id": {
                    "type": "string",
                    "description": "The order ID.",
                },
            },
            "required": ["order_id"],
        },
    ),
]


# ---------------------------------------------------------------------------
# Tool handler registry
# ---------------------------------------------------------------------------


class MCPToolHandler:
    """Executes MCP tool calls by delegating to the merchant service layer.

    Parameters
    ----------
    checkout_manager:
        The :class:`CheckoutSessionManager` instance.
    catalog_search:
        The :class:`CatalogSearch` instance.
    catalog:
        The :class:`ProductCatalog` instance.
    order_manager:
        The :class:`OrderManager` instance.
    """

    def __init__(
        self,
        checkout_manager: Any,
        catalog_search: Any,
        catalog: Any,
        order_manager: Any,
    ) -> None:
        self._checkout = checkout_manager
        self._search = catalog_search
        self._catalog = catalog
        self._orders = order_manager

    def get_tool_definitions(self) -> list[MCPToolDefinition]:
        """Return all available MCP tool definitions."""
        return list(TOOL_DEFINITIONS)

    async def execute(self, tool_name: str, arguments: dict[str, Any]) -> MCPToolResult:
        """Execute a tool by name with the given arguments.

        Parameters
        ----------
        tool_name:
            Name of the tool to execute.
        arguments:
            Tool input arguments matching the tool's JSON Schema.

        Returns
        -------
        MCPToolResult
            The execution result.
        """
        handler_map = {
            "search_products": self._search_products,
            "get_product": self._get_product,
            "create_checkout": self._create_checkout,
            "update_checkout": self._update_checkout,
            "apply_discount": self._apply_discount,
            "select_shipping": self._select_shipping,
            "complete_checkout": self._complete_checkout,
            "get_order": self._get_order,
        }

        handler = handler_map.get(tool_name)
        if handler is None:
            return MCPToolResult(
                tool_name=tool_name,
                success=False,
                error=f"Unknown tool: {tool_name!r}",
            )

        try:
            result = handler(arguments)
            logger.info("mcp_tool_executed", tool=tool_name, success=True)
            return MCPToolResult(
                tool_name=tool_name,
                success=True,
                result=result,
            )
        except Exception as exc:
            logger.warning(
                "mcp_tool_error",
                tool=tool_name,
                error=str(exc),
            )
            return MCPToolResult(
                tool_name=tool_name,
                success=False,
                error=str(exc),
            )

    # -- individual tool handlers --------------------------------------------

    def _search_products(self, args: dict[str, Any]) -> dict[str, Any]:
        result = self._search.search(
            query=args.get("query"),
            category=args.get("category"),
            min_price=args.get("min_price"),
            max_price=args.get("max_price"),
            brand=args.get("brand"),
            in_stock=args.get("in_stock"),
            sort_by=args.get("sort_by", "relevance"),
            limit=args.get("limit", 20),
            offset=args.get("offset", 0),
        )
        return result.model_dump(mode="json")

    def _get_product(self, args: dict[str, Any]) -> dict[str, Any]:
        product = self._catalog.get(args["product_id"])
        if product is None:
            raise ValueError(f"Product not found: {args['product_id']!r}")
        return product.model_dump(mode="json")

    def _create_checkout(self, args: dict[str, Any]) -> dict[str, Any]:
        session = self._checkout.create_session(
            line_items=args.get("items", [])
        )
        return session.model_dump(mode="json")

    def _update_checkout(self, args: dict[str, Any]) -> dict[str, Any]:
        session_id = args.pop("session_id")
        session = self._checkout.update_session(session_id, args)
        return session.model_dump(mode="json")

    def _apply_discount(self, args: dict[str, Any]) -> dict[str, Any]:
        session = self._checkout.update_session(
            args["session_id"],
            {"discount_code": args["code"]},
        )
        return session.model_dump(mode="json")

    def _select_shipping(self, args: dict[str, Any]) -> dict[str, Any]:
        session = self._checkout.update_session(
            args["session_id"],
            {"shipping_option_id": args["shipping_option_id"]},
        )
        return session.model_dump(mode="json")

    def _complete_checkout(self, args: dict[str, Any]) -> dict[str, Any]:
        session = self._checkout.complete_session(args["session_id"])
        # Also create the order
        order = self._orders.create_order(session)
        return {
            "session": session.model_dump(mode="json"),
            "order": order.model_dump(mode="json"),
        }

    def _get_order(self, args: dict[str, Any]) -> dict[str, Any]:
        order = self._orders.get_order(args["order_id"])
        return order.model_dump(mode="json")
