# UCP Merchant Server

[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Docker](https://img.shields.io/badge/Docker-ready-2496ED.svg?logo=docker)](Dockerfile)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115+-009688.svg?logo=fastapi)](https://fastapi.tiangolo.com)
[![UCP](https://img.shields.io/badge/UCP-2026--01--11-FF6F00.svg)](https://ucp.dev)

**Shopify-style UCP-compliant merchant server** implementing Google's [Universal Commerce Protocol](https://ucp.dev). Any AI agent can discover, browse, negotiate capabilities, and purchase from this merchant -- just like how Shopify integrates with Google AI Mode and Gemini.

> **What is UCP?** The Universal Commerce Protocol is an open standard co-developed by Google, Shopify, Etsy, Wayfair, Target, and Walmart for AI-agent-driven commerce. It defines how merchants expose their stores so AI agents can shop autonomously.

---

## What This Project Demonstrates

| Concept | Implementation |
|---------|---------------|
| **UCP Discovery** | `/.well-known/ucp` manifest with capabilities, extensions, payment handlers |
| **Capability Negotiation** | Server-selects from intersection of agent + merchant capabilities |
| **Checkout State Machine** | `incomplete` -> `requires_escalation` -> `ready_for_complete` -> `completed` |
| **UCP Extensions** | Fulfillment (shipping options) + Discounts (coupon codes) via JSON Schema composition |
| **AP2 Payment Verification** | Real ECDSA P-256 cryptographic mandate signing & verification |
| **Order Lifecycle** | `confirmed` -> `processing` -> `shipped` -> `delivered` with SSE tracking |
| **MCP Tool Bindings** | All UCP operations exposed as MCP tools for LLM consumption |

---

## Quick Start

### Docker (Recommended)

```bash
git clone https://github.com/samuelvinay91/ucp-merchant-server.git
cd ucp-merchant-server

docker build -t ucp-merchant-server .
docker run -p 8011:8000 ucp-merchant-server
```

The API will be available at **http://localhost:8011**. Docs at **http://localhost:8011/docs**.

### Local Development

```bash
git clone https://github.com/samuelvinay91/ucp-merchant-server.git
cd ucp-merchant-server

python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

python -m ucp_merchant.main
```

---

## UCP Workflow: How AI Agents Shop Here

```
1. Agent discovers merchant:  GET /.well-known/ucp
2. Agent negotiates:          POST /api/v1/negotiate
3. Agent browses catalog:     GET /api/v1/catalog/products?q=laptop
4. Agent creates checkout:    POST /api/v1/checkout/sessions
5. Agent fills details:       PUT /api/v1/checkout/sessions/{id}
   - Sets shipping address
   - Selects shipping option
   - Applies discount code
6. State machine evaluates:   incomplete -> ready_for_complete
7. AP2 mandate verification:  POST /api/v1/payments/verify-mandate
8. Agent completes purchase:  POST /api/v1/checkout/sessions/{id}/complete
9. Agent tracks order:        GET /api/v1/orders/{id}/stream (SSE)
```

---

## API Reference

### Discovery & Negotiation

```bash
# Discover merchant capabilities
curl http://localhost:8011/.well-known/ucp

# Negotiate capabilities with the merchant
curl -X POST http://localhost:8011/api/v1/negotiate \
  -H "Content-Type: application/json" \
  -d '{
    "agent_capabilities": ["dev.ucp.shopping.checkout"],
    "agent_extensions": ["dev.ucp.shopping.fulfillment"],
    "agent_payment_handlers": ["dev.ucp.mock_payment"]
  }'
```

### Catalog

```bash
# Search products
curl "http://localhost:8011/api/v1/catalog/products?q=keyboard&category=Keyboards&sort_by=price_asc"

# Get product details
curl http://localhost:8011/api/v1/catalog/products/{product_id}

# List categories
curl http://localhost:8011/api/v1/catalog/categories
```

### Checkout (UCP State Machine)

```bash
# Create checkout session
curl -X POST http://localhost:8011/api/v1/checkout/sessions \
  -H "Content-Type: application/json" \
  -d '{"line_items": [{"product_id": "laptop-001", "quantity": 1}]}'

# Update session (set address, shipping, discount)
curl -X PUT http://localhost:8011/api/v1/checkout/sessions/{id} \
  -H "Content-Type: application/json" \
  -d '{
    "shipping_address": {"line1": "123 Main St", "city": "SF", "state": "CA", "postal_code": "94105", "country": "US"},
    "shipping_option_id": "standard",
    "discount_code": "SAVE10"
  }'

# Complete checkout
curl -X POST http://localhost:8011/api/v1/checkout/sessions/{id}/complete
```

### AP2 Payments (ECDSA P-256 Crypto)

```bash
# Generate key pair
curl -X POST http://localhost:8011/api/v1/payments/keys

# Create test mandate (for demo)
curl -X POST http://localhost:8011/api/v1/payments/test-mandate \
  -d '{"session_id": "...", "key_id": "..."}'

# Verify mandate signature
curl -X POST http://localhost:8011/api/v1/payments/verify-mandate \
  -d '{"mandate_type": "cart", "merchant_id": "...", "amount": "99.99", "signature": "..."}'
```

### Orders

```bash
# Get order
curl http://localhost:8011/api/v1/orders/{order_id}

# Stream order updates (SSE)
curl -N http://localhost:8011/api/v1/orders/{order_id}/stream

# Simulate fulfillment (testing)
curl -X POST http://localhost:8011/api/v1/testing/simulate-fulfillment/{order_id}
```

---

## Checkout State Machine

```
                    +------------+
                    | INCOMPLETE |  (missing address or shipping)
                    +-----+------+
                          |
              +-----------+-----------+
              |                       |
    +---------v----------+   +--------v-----------+
    | REQUIRES_ESCALATION|   | READY_FOR_COMPLETE |
    | (needs human input)|   | (all fields filled)|
    +---------+----------+   +--------+-----------+
              |                       |
              +-----------+-----------+
                          |
                   +------v------+
                   |  COMPLETED  |
                   +------+------+
                          |
                   +------v------+
                   |    ORDER    | -> confirmed -> processing -> shipped -> delivered
                   +-------------+
```

---

## Testing

```bash
pytest tests/ -v
pytest tests/ -v --cov=src/ucp_merchant
```

---

## Project Structure

```
ucp-merchant-server/
├── src/ucp_merchant/
│   ├── main.py                    # Entry point
│   ├── api.py                     # FastAPI routes (all endpoints)
│   ├── config.py                  # Settings
│   ├── models.py                  # All Pydantic models
│   ├── discovery/
│   │   ├── manifest.py            # /.well-known/ucp generation
│   │   └── negotiation.py         # Capability negotiation
│   ├── catalog/
│   │   ├── products.py            # Product catalog (30 items)
│   │   └── search.py              # Search, filter, sort, paginate
│   ├── checkout/
│   │   ├── session.py             # Checkout session CRUD
│   │   ├── state_machine.py       # UCP checkout states
│   │   ├── extensions.py          # Fulfillment + Discounts
│   │   └── totals.py              # Tax, shipping, discount calc
│   ├── payments/
│   │   ├── keys.py                # ECDSA P-256 key management
│   │   └── ap2_verification.py    # AP2 mandate verification
│   ├── orders/
│   │   ├── lifecycle.py           # Order state management
│   │   ├── tracking.py            # SSE order tracking
│   │   └── webhooks.py            # Webhook notifications
│   └── mcp_binding/
│       └── tools.py               # MCP tool definitions
├── tests/
├── k8s/
├── Dockerfile
├── docker-compose.yml
└── pyproject.toml
```

---

## Key Technologies

- **[UCP Specification](https://ucp.dev)** - Universal Commerce Protocol (2026-01-11)
- **[AP2 Protocol](https://ap2-protocol.org)** - Agent Payments Protocol with ECDSA P-256
- **[FastAPI](https://fastapi.tiangolo.com)** - High-performance API framework
- **[MCP](https://modelcontextprotocol.io)** - Model Context Protocol tool bindings
- **[cryptography](https://cryptography.io)** - ECDSA P-256 signing/verification

---

## License

MIT License - see [LICENSE](LICENSE) for details.
