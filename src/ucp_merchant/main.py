"""Entry point for the UCP Merchant Server.

Configures structured logging, creates the FastAPI application via the
factory, and launches it with Uvicorn.
"""

from __future__ import annotations

import uvicorn

from common import setup_logging

from ucp_merchant.api import create_app
from ucp_merchant.config import get_settings


def main() -> None:
    """Launch the UCP Merchant Server."""
    settings = get_settings()
    setup_logging(settings.log_level)

    app = create_app(settings)

    uvicorn.run(
        app,
        host=settings.host,
        port=settings.port,
        log_level=settings.log_level.lower(),
    )


if __name__ == "__main__":
    main()
