"""Working reference integration: currency exchange rates.

Chosen because it needs no vendor account, so the endpoint is demonstrably live
end-to-end today. It is here as the *pattern*, not because the app needs FX —
swap the URL, the auth header and the two parse methods and the same class talks
to Stripe, a CRM, a shipping API or whatever the real first integration turns
out to be.
"""

from __future__ import annotations

import logging
from decimal import Decimal, InvalidOperation

from integrations.clients.base import BaseAPIClient
from core.exceptions import UpstreamServiceError

logger = logging.getLogger(__name__)


class ExchangeRatesClient(BaseAPIClient):
    config_key = "EXCHANGE_RATES"
    requires_api_key = False
    # FX reference rates move slowly; an hour of caching removes almost all
    # outbound calls and keeps us inside free-tier rate limits.
    cache_ttl = 3600

    def auth_headers(self) -> dict[str, str]:
        # This particular vendor is keyless. Shown so the override point is
        # obvious for the next integration:
        #   return {"Authorization": f"Bearer {self.api_key}"}
        return {}

    def fetch_rates(self, base_currency: str) -> dict:
        base = self._normalise_currency(base_currency)
        payload = self.get(f"latest/{base}")

        if not isinstance(payload, dict):
            raise UpstreamServiceError("Exchange rate response was not an object.")
        # The vendor signals failure inside a 200 body, so the status code alone
        # is not enough to trust the payload.
        if payload.get("result") == "error":
            raise UpstreamServiceError(
                f"Exchange rate provider rejected base currency {base}."
            )
        rates = payload.get("rates")
        if not isinstance(rates, dict) or not rates:
            raise UpstreamServiceError("Exchange rate response contained no rates.")

        return {
            "base": payload.get("base_code", base),
            "updated_at": payload.get("time_last_update_utc"),
            "next_update_at": payload.get("time_next_update_utc"),
            "rates": rates,
        }

    def convert(self, amount, source: str, target: str) -> dict:
        source = self._normalise_currency(source)
        target = self._normalise_currency(target)

        try:
            amount = Decimal(str(amount))
        except (InvalidOperation, TypeError) as exc:
            raise ValueError("amount must be numeric") from exc

        if source == target:
            return {
                "amount": amount,
                "source_currency": source,
                "target_currency": target,
                "rate": Decimal("1"),
                "converted": amount,
                "rate_updated_at": None,
            }

        data = self.fetch_rates(source)
        raw_rate = data["rates"].get(target)
        if raw_rate is None:
            raise UpstreamServiceError(
                f"Provider returned no {source}->{target} rate."
            )
        try:
            rate = Decimal(str(raw_rate))
        except InvalidOperation as exc:
            raise UpstreamServiceError(
                f"Provider returned an unusable {source}->{target} rate: {raw_rate!r}."
            ) from exc
        if rate <= 0:
            # A zero or negative rate would silently produce a nonsense total.
            raise UpstreamServiceError(
                f"Provider returned a non-positive {source}->{target} rate: {rate}."
            )

        return {
            "amount": amount,
            "source_currency": source,
            "target_currency": target,
            "rate": rate,
            "converted": (amount * rate).quantize(Decimal("0.0001")),
            "rate_updated_at": data.get("updated_at"),
        }

    def health_check(self) -> dict:
        try:
            data = self.fetch_rates("USD")
        except UpstreamServiceError as exc:
            return {"service": self.service_name, "healthy": False, "detail": str(exc)}
        return {
            "service": self.service_name,
            "healthy": True,
            "detail": f"{len(data['rates'])} rates available",
        }

    @staticmethod
    def _normalise_currency(code: str) -> str:
        code = (code or "").strip().upper()
        if len(code) != 3 or not code.isalpha():
            raise ValueError(f"{code!r} is not a 3-letter ISO 4217 currency code")
        return code
