"""Placeholders for the integrations that are coming but not yet specified.

Each stub is a real subclass with the method names the app will call, raising
`NotImplementedError` in the body. That is deliberate:

* the call sites and serializers can be written now,
* the registry can already list them as "declared but not implemented",
* and nothing can silently succeed with fake data — a stub that returned {}
  would let a bug ship looking like a working feature.

Filling one in means setting its BASE_URL/API_KEY in settings.INTEGRATIONS,
flipping `is_stub` off and writing the two or three parse methods.
"""

from __future__ import annotations

from integrations.clients.base import BaseAPIClient


class StubMixin:
    #: Read by the registry to mark the integration as not-yet-implemented.
    is_stub = True

    def _not_implemented(self, what: str):
        raise NotImplementedError(
            f"{type(self).__name__}.{what} is a stub: the vendor has not been "
            f"chosen yet. Add a config block to settings.INTEGRATIONS and "
            f"implement this method."
        )


class PaymentClientStub(StubMixin, BaseAPIClient):
    """e.g. Stripe / Razorpay / PayPal."""

    config_key = "PAYMENTS"
    requires_api_key = True

    def create_charge(self, amount, currency, source_token, idempotency_key):
        self._not_implemented("create_charge")

    def refund(self, charge_id, amount=None):
        self._not_implemented("refund")

    def verify_webhook(self, payload: bytes, signature: str) -> bool:
        self._not_implemented("verify_webhook")


class CrmClientStub(StubMixin, BaseAPIClient):
    """e.g. HubSpot / Salesforce / Zoho."""

    config_key = "CRM"
    requires_api_key = True

    def upsert_contact(self, email, attributes):
        self._not_implemented("upsert_contact")

    def log_activity(self, contact_id, activity):
        self._not_implemented("log_activity")


class NotificationClientStub(StubMixin, BaseAPIClient):
    """e.g. Twilio / Firebase / OneSignal."""

    config_key = "NOTIFICATIONS"
    requires_api_key = True

    def send(self, recipient, template, context):
        self._not_implemented("send")
