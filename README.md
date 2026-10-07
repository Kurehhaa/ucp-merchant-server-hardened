# UCP Merchant Server

[![CI](https://github.com/Kurehhaa/ucp-merchant-server/actions/workflows/ci.yml/badge.svg)](https://github.com/Kurehhaa/ucp-merchant-server/actions/workflows/ci.yml)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115+-009688.svg?logo=fastapi)](https://fastapi.tiangolo.com)

A demo merchant server for AI-agent shopping, modelled on the [Universal Commerce Protocol](https://ucp.dev) (UCP). An agent can discover the store, negotiate capabilities, browse a catalog, build a checkout, authorise payment with a signed [AP2](https://ap2-protocol.org) mandate, and track the order. All operations are also exposed as MCP-style tools.

> **Status: educational demo.** It takes no real payments, keeps all data in memory, and has not been validated against an official UCP conformance suite. See [Limitations](#limitations).

Based on [Skopaq-AI/ucp-merchant-server](https://github.com/Skopaq-AI/ucp-merchant-server) (MIT). See [What changed from upstream](#what-changed-from-upstream).

---

## What it demonstrates

| Concept | Implementation |
| --- | --- |
| Discovery | `/.well-known/ucp` manifest with capabilities, extensions and payment handlers |
| Capability negotiation | The server picks from the intersection of agent and merchant capabilities |
| Checkout state machine | `incomplete` / `requires_escalation` / `ready_for_complete` -> `completed` (or `abandoned`) |
| Extensions | Fulfillment (shipping options) and discounts (coupon codes) |
| AP2 payment mandates | ECDSA P-256 signed cart and intent mandates, verified against the agent's *registered public key* |
| Replay protection | Cart mandates are single use and bound to the exact cart |
| Order lifecycle | `confirmed` -> `processing` -> `shipped` -> `delivered`, with SSE tracking and webhooks |
| MCP tool bindings | UCP operations listed and executed as tools |

---

## Quick start

### Local

```bash
git clone https://github.com/<your-username>/ucp-merchant-server.git
cd ucp-merchant-server

python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\Activate.ps1
pip install -e ".[dev]"

cp .env.example .env               # Windows: copy .env.example .env
python -m ucp_merchant.main
```

The API is at <http://localhost:8011>, interactive docs at <http://localhost:8011/docs>.

Opening `http://0.0.0.0:8011` in a browser does not work; use `localhost`.

### Docker

```bash
docker compose up --build
```

The container listens on port 8000 and compose maps it to <http://localhost:8011>.

---

## Configuration

Settings are read from environment variables or a `.env` file (see [`.env.example`](.env.example)).

| Variable | Default | Purpose |
| --- | --- | --- |
| `PORT` / `HOST` | `8011` / `0.0.0.0` | Where the server listens (the Docker image sets `PORT=8000`) |
| `ADMIN_API_KEY` | *(empty)* | Key for admin endpoints, sent as `X-API-Key`. **Empty = those endpoints are disabled** |
| `AP2_REQUIRE_MANDATE` | `true` | Completing a checkout requires a valid, unused cart mandate |
| `AP2_DEMO_MODE` | `false` | Enables endpoints that generate keys and sign mandates *on the server* (demo only) |
| `AP2_MAX_MANDATE_TTL_SECONDS` | `86400` | Longest allowed mandate lifetime |
| `CORS_ALLOW_ORIGINS` | *(empty)* | Comma-separated browser origins. Empty = none |
| `WEBHOOK_ALLOW_PRIVATE_HOSTS` | `false` | Allow `http` / private-network webhook URLs (local demos only) |
| `EXPOSE_ERROR_DETAILS` | `false` | Include exception text in 500 responses (never in production) |

---

## Try it: a full purchase

This uses the server-side demo signing endpoints, so start the server with demo mode and an admin key:

```bash
AP2_DEMO_MODE=true ADMIN_API_KEY=dev-admin-key python -m ucp_merchant.main
```

```bash
B=http://localhost:8011

# 1. Discover the merchant and negotiate capabilities
curl $B/.well-known/ucp
curl -X POST $B/api/v1/negotiate -H "Content-Type: application/json" -d '{
  "agent_id": "my-shopping-agent",
  "agent_name": "My Shopping Agent",
  "requested_capabilities": ["dev.ucp.shopping.checkout"],
  "requested_extensions": ["dev.ucp.shopping.fulfillment"],
  "supported_payment_handlers": ["dev.ucp.mock_payment"]
}

# 2. Browse
curl "$B/api/v1/catalog/search?query=keyboard&sort_by=price_asc&limit=3"

# 3. Create a checkout (note the returned "id")
curl -X POST $B/api/v1/checkout -H "Content-Type: application/json" \
  -d '{"items": [{"product_id": "kb-001", "quantity": 1}]}'

# 4. Fill in address, shipping and a discount
curl -X PATCH $B/api/v1/checkout/{id} -H "Content-Type: application/json" -d '{
  "shipping_address": {"full_name": "Ada Lovelace", "line1": "123 Main St", "city": "San Francisco",
                       "state": "CA", "postal_code": "94105", "country": "US"},
  "shipping_option_id": "shipping_standard",
  "discount_code": "SAVE10"
}'

# 5. Get a signed cart mandate (demo mode: the server holds the key)
curl -X POST $B/api/v1/payments/keys                         # -> key_id
curl -X POST $B/api/v1/payments/test-mandate -H "Content-Type: application/json" \
  -d '{"session_id": "{id}", "key_id": "{key_id}"}'

# 6. Complete the checkout with the mandate
curl -X POST $B/api/v1/checkout/{id}/complete -H "Content-Type: application/json" \
  -d '{"cart_mandate": { ...mandate from step 5... }}'

# 7. Track the order
curl $B/api/v1/orders/{order_id}
curl -N $B/api/v1/orders/{order_id}/track                    # SSE stream

# 8. Admin: move the order along (needs the admin key)
curl -X POST $B/api/v1/orders/{order_id}/simulate-fulfillment -H "X-API-Key: dev-admin-key"
```

Discount codes: `SAVE10`, `SAVE20`, `FREESHIP`, `WELCOME`. Shipping options: `shipping_standard`, `shipping_express`, `shipping_next_day`, `shipping_free`.

---

## API reference

Endpoints marked **admin** require the `X-API-Key` header and return `403` when `ADMIN_API_KEY` is not configured.

| Method | Path | Notes |
| --- | --- | --- |
| GET | `/health` | Liveness |
| GET | `/.well-known/ucp` | UCP discovery manifest |
| POST | `/api/v1/negotiate` | Capability negotiation |
| GET | `/api/v1/catalog/search` | `query, category, min_price, max_price, brand, in_stock, sort_by, limit, offset` |
| GET | `/api/v1/catalog/products/{product_id}` | |
| GET | `/api/v1/catalog/categories` | |
| POST | `/api/v1/checkout` | Body: `{"items": [{"product_id", "quantity"}]}` |
| GET | `/api/v1/checkout/{id}` | |
| PATCH | `/api/v1/checkout/{id}` | `add_items, remove_items, shipping_address, billing_address, shipping_option_id, discount_code` |
| POST | `/api/v1/checkout/{id}/complete` | Requires `cart_mandate` unless `AP2_REQUIRE_MANDATE=false`. `402` mandate missing or invalid, `409` mandate already used, `400` checkout cannot be completed (not ready, or already completed) |
| POST | `/api/v1/checkout/{id}/abandon` | |
| GET | `/api/v1/checkout/{id}/shipping` | Shipping options for the cart |
| POST | `/api/v1/checkout/{id}/discount?code=` | |
| POST | `/api/v1/payments/keys/register` | Register an agent's **public** JWK; returns `key_id` |
| GET | `/api/v1/payments/keys/{key_id}` | Public key info |
| GET | `/api/v1/payments/keys` | **admin** |
| POST | `/api/v1/payments/verify/cart` | Check a cart mandate without consuming it |
| POST | `/api/v1/payments/verify/intent` | Check an intent mandate |
| POST | `/api/v1/payments/keys` | Demo mode only: generate a server-held key pair |
| POST | `/api/v1/payments/test-mandate` | Demo mode only |
| POST | `/api/v1/payments/test-intent-mandate` | Demo mode only |
| GET | `/api/v1/orders/{id}` | |
| GET | `/api/v1/orders/{id}/track` | Server-sent events |
| GET | `/api/v1/orders` | **admin** |
| POST | `/api/v1/orders/{id}/advance` | **admin** |
| POST | `/api/v1/orders/{id}/cancel` | **admin** |
| POST | `/api/v1/orders/{id}/simulate-fulfillment` | **admin** |
| GET / POST | `/api/v1/webhooks` | **admin**. URLs must be `https` and publicly routable |
| GET | `/api/v1/mcp/tools` | List MCP tools |
| POST | `/api/v1/mcp/tools/{name}/execute` | Run a tool |

---

## Checkout state machine

```
   INCOMPLETE  <------------->  READY_FOR_COMPLETE  ------>  COMPLETED  ->  ORDER
       ^                              ^                                 (confirmed -> processing
       |                              |                                  -> shipped -> delivered)
       +--> REQUIRES_ESCALATION <-----+
   (any open state) ------------------------------------>  ABANDONED
```

The three open states are *derived* from the cart and re-evaluated after every update, so a cart can move between them in both directions (for example, removing every item from a ready cart sends it back to `incomplete`). `completed` and `abandoned` are final.

| State | Meaning | Where to look |
| --- | --- | --- |
| `incomplete` | Data is missing; the agent can supply it | `missing_fields` |
| `requires_escalation` | A human buyer must step in (e.g. an item is no longer in stock in the requested quantity) | `escalation_reasons` |
| `ready_for_complete` | Everything is present and nothing needs a human | -- |

`POST /checkout/{id}/complete` re-evaluates the cart at that moment instead of trusting the stored state, so a stale `ready_for_complete` can never produce an order.

---

## AP2 payment mandates

A *mandate* is a signed statement that an agent authorises a payment.

1. The agent generates a P-256 key pair and keeps the private key.
2. It registers only the **public JWK** with `POST /payments/keys/register` and gets a `key_id`.
3. For a purchase it signs a **cart mandate** covering: mandate id, checkout session, merchant, amount, currency, validity window and a `cart_hash` (items, total, shipping method and address).
4. The merchant verifies the signature with the registered public key, checks expiry, merchant, amount and cart hash, and marks the mandate **used** before creating the order. If order creation then fails, the mandate is released so the agent can retry.

Any change to the signed fields after signing, an expired or over-long mandate, a different cart, or a replayed mandate is rejected. The exact signing format is documented in [`ap2_verification.py`](src/ucp_merchant/payments/ap2_verification.py); [`tests/ap2_helpers.py`](tests/ap2_helpers.py) contains a working agent-side signer.

---

## Security model

- **Admin endpoints** are closed unless `ADMIN_API_KEY` is set; the key is compared in constant time.
- **Webhooks** are checked against SSRF: https only, no credentials in the URL, and every resolved address must be publicly routable. The check is repeated at delivery time, redirects are not followed.
- **CORS** allows no origin by default.
- **Errors**: unexpected failures return a reference id; details stay in the server log.
- **Mandates** are verified against registered public keys, bound to the cart, expiring and single use.

---

## Testing and quality

```bash
pytest                                  # full suite
pytest --cov=src/ucp_merchant           # with coverage
ruff check . && ruff format --check .   # lint and formatting
mypy src/ --ignore-missing-imports      # type checking
```

CI (`.github/workflows/ci.yml`) runs lint, format check, mypy, the tests and a Docker build on every push and pull request.

---

## Project structure

```
src/
  common/                  shared settings, logging, response models
  ucp_merchant/
    main.py                entry point (uvicorn)
    api.py                 FastAPI app and all routes
    config.py              settings
    models.py              Pydantic models
    utils.py               utcnow() helper
    discovery/             manifest and capability negotiation
    catalog/               products, search
    checkout/              sessions, state machine, extensions, totals
    payments/              key registry, AP2 verification
    orders/                lifecycle, SSE tracking, webhooks
    mcp_binding/           MCP tool definitions and handler
tests/                     pytest suite
k8s/                       Kubernetes manifests
Dockerfile, docker-compose.yml, pyproject.toml
```

---

## Limitations

- All state (sessions, orders, keys, used mandates) is **in memory**: it is lost on restart and not shared between processes. Replay protection therefore only holds within one process; a real deployment needs a shared store.
- No real payment processing and no user accounts. `GET /orders/{id}` is open to anyone who knows the (random) id because the model has no notion of an order owner.
- Not validated against an official UCP conformance suite.

---

## What changed from upstream

Compared with the original project this fork:

- **AP2**: verifies against registered public keys instead of server-held private keys; signs the expiry, validity window and a cart hash; adds single-use replay protection; requires a mandate to complete a checkout.
- **Checkout**: fixes a bug where a ready cart whose items were removed could still be completed into an empty order; adds the missing back-transitions and a clear `incomplete` vs `requires_escalation` meaning.
- **API security**: admin-key protected management endpoints, SSRF-safe webhooks, strict CORS, no internal error details in responses.
- **Reliability**: fixes order tracking (SSE events were never delivered because of a logging-argument clash), the Docker image (missing README at build time, wrong port) and the CI pipeline.
- **Docs and tests**: README matches the code; the test suite grew from 71 to over 230 tests.

---

## License

MIT, see [LICENSE](LICENSE). Original work copyright its upstream author.
