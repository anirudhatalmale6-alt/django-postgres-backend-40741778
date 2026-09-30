"""Registry of external integrations.

Views ask the registry for a client instead of importing a concrete class, so
adding an integration is a one-line change here and swapping a client for a fake
in tests is a one-line override.
"""

from __future__ import annotations

from typing import Callable

from integrations.clients.base import BaseAPIClient, IntegrationConfigError
from integrations.clients.exchange_rates import ExchangeRatesClient
from integrations.clients.stubs import (
    CrmClientStub,
    NotificationClientStub,
    PaymentClientStub,
)

# slug -> factory. The slug is the value that appears in URLs and in the
# /integrations/ listing, so keep it stable once the front end uses it.
REGISTRY: dict[str, Callable[[], BaseAPIClient]] = {
    "exchange-rates": ExchangeRatesClient,
    "payments": PaymentClientStub,
    "crm": CrmClientStub,
    "notifications": NotificationClientStub,
}


def get_client(slug: str) -> BaseAPIClient:
    try:
        factory = REGISTRY[slug]
    except KeyError as exc:
        raise KeyError(f"No integration registered under {slug!r}") from exc
    return factory()


def available() -> list[dict]:
    """Describe every registered integration without calling out to the network."""
    out = []
    for slug, factory in REGISTRY.items():
        entry = {"slug": slug, "client": factory.__name__, "implemented": True,
                 "configured": True, "detail": ""}
        if getattr(factory, "is_stub", False):
            entry["implemented"] = False
            entry["detail"] = "Stub — awaiting vendor choice and credentials."
        try:
            factory()
        except IntegrationConfigError as exc:
            entry["configured"] = False
            entry["detail"] = str(exc)
        out.append(entry)
    return out
