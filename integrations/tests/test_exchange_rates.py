"""Integration-layer tests.

Every outbound HTTP call is intercepted with `responses`. That is deliberate:
a test that really called the vendor would go red whenever their free tier
rate-limited us, and would prove nothing about our error handling because we
cannot make a third party time out on demand. The live provider is exercised
separately by the /integrations/exchange-rates/health/ endpoint.
"""

from decimal import Decimal

import requests
import responses
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from core.exceptions import UpstreamServiceError
from integrations.clients.base import IntegrationConfigError
from integrations.clients.exchange_rates import ExchangeRatesClient
from integrations.clients.stubs import PaymentClientStub

User = get_user_model()
PASSWORD = "Tr0ubadour-Kettle-92"

BASE_URL = "https://fx.test/v6"
LATEST_USD = f"{BASE_URL}/latest/USD"

OK_PAYLOAD = {
    "result": "success",
    "base_code": "USD",
    "time_last_update_utc": "Tue, 30 Sep 2026 00:00:01 +0000",
    "time_next_update_utc": "Wed, 01 Oct 2026 00:00:01 +0000",
    "rates": {"USD": 1, "EUR": 0.85, "INR": 83.5},
}

FX_SETTINGS = override_settings(
    INTEGRATIONS={
        "EXCHANGE_RATES": {"BASE_URL": BASE_URL, "API_KEY": "", "TIMEOUT": 2}
    },
    # No retry sleeping in tests, and no cross-test cache bleed.
    HTTP_CLIENT_MAX_RETRIES=0,
    CACHES={
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "fx-tests",
        }
    },
)


@FX_SETTINGS
class ExchangeRatesClientTests(TestCase):
    def setUp(self):
        cache.clear()
        self.client = ExchangeRatesClient()

    def tearDown(self):
        self.client.close()
        cache.clear()

    @responses.activate
    def test_convert_multiplies_by_the_providers_rate(self):
        responses.add(responses.GET, LATEST_USD, json=OK_PAYLOAD, status=200)
        result = self.client.convert("100", "usd", "eur")

        self.assertEqual(result["source_currency"], "USD")
        self.assertEqual(result["target_currency"], "EUR")
        self.assertEqual(result["rate"], Decimal("0.85"))
        # The number itself, not just "a number was returned".
        self.assertEqual(result["converted"], Decimal("85.0000"))

    @responses.activate
    def test_same_currency_short_circuits_without_calling_the_provider(self):
        result = self.client.convert("42.5", "EUR", "EUR")
        self.assertEqual(result["converted"], Decimal("42.5"))
        self.assertEqual(result["rate"], Decimal("1"))
        self.assertEqual(
            len(responses.calls), 0, "a same-currency conversion hit the network"
        )

    @responses.activate
    def test_successful_response_is_cached(self):
        responses.add(responses.GET, LATEST_USD, json=OK_PAYLOAD, status=200)
        self.client.fetch_rates("USD")
        self.client.fetch_rates("USD")
        self.assertEqual(
            len(responses.calls), 1, "the second identical call was not served from cache"
        )

    @responses.activate
    def test_error_inside_a_200_body_is_treated_as_a_failure(self):
        # This provider reports bad input with HTTP 200 and result="error", so
        # trusting the status code alone would hand the caller a broken payload.
        responses.add(
            responses.GET,
            f"{BASE_URL}/latest/ZZZ",
            json={"result": "error", "error-type": "unsupported-code"},
            status=200,
        )
        with self.assertRaises(UpstreamServiceError):
            self.client.fetch_rates("ZZZ")

    @responses.activate
    def test_html_body_with_a_200_is_a_failure_not_an_empty_result(self):
        responses.add(
            responses.GET,
            LATEST_USD,
            body="<html><body>Maintenance</body></html>",
            status=200,
            content_type="text/html",
        )
        with self.assertRaises(UpstreamServiceError):
            self.client.fetch_rates("USD")

    @responses.activate
    def test_missing_target_rate_raises_instead_of_returning_none(self):
        responses.add(
            responses.GET,
            LATEST_USD,
            json={**OK_PAYLOAD, "rates": {"USD": 1, "EUR": 0.85}},
            status=200,
        )
        with self.assertRaises(UpstreamServiceError):
            self.client.convert("10", "USD", "JPY")

    @responses.activate
    def test_non_positive_rate_is_refused(self):
        # A 0 rate would quietly turn every conversion into 0.00 and look like a
        # legitimate answer.
        responses.add(
            responses.GET,
            LATEST_USD,
            json={**OK_PAYLOAD, "rates": {"EUR": 0}},
            status=200,
        )
        with self.assertRaises(UpstreamServiceError):
            self.client.convert("10", "USD", "EUR")

    @responses.activate
    def test_http_500_becomes_an_upstream_error(self):
        responses.add(responses.GET, LATEST_USD, json={"oops": True}, status=500)
        with self.assertRaises(UpstreamServiceError):
            self.client.fetch_rates("USD")

    @responses.activate
    def test_timeout_becomes_an_upstream_error(self):
        responses.add(responses.GET, LATEST_USD, body=requests.Timeout("too slow"))
        with self.assertRaises(UpstreamServiceError):
            self.client.fetch_rates("USD")

    @responses.activate
    def test_connection_error_becomes_an_upstream_error(self):
        responses.add(
            responses.GET, LATEST_USD, body=requests.ConnectionError("refused")
        )
        with self.assertRaises(UpstreamServiceError):
            self.client.fetch_rates("USD")

    def test_bad_currency_code_is_rejected_before_any_request(self):
        for code in ("US", "USDD", "12", "", "US$"):
            with self.subTest(code=code):
                with self.assertRaises(ValueError):
                    self.client.convert("1", code, "EUR")


class ClientConfigurationTests(TestCase):
    @override_settings(INTEGRATIONS={"EXCHANGE_RATES": {"BASE_URL": ""}})
    def test_missing_base_url_fails_loudly_at_construction(self):
        with self.assertRaises(IntegrationConfigError):
            ExchangeRatesClient()

    @override_settings(INTEGRATIONS={"PAYMENTS": {"BASE_URL": "https://pay.test"}})
    def test_missing_required_api_key_fails_loudly(self):
        with self.assertRaises(IntegrationConfigError):
            PaymentClientStub()

    @override_settings(
        INTEGRATIONS={"PAYMENTS": {"BASE_URL": "https://pay.test", "API_KEY": "k"}}
    )
    def test_stub_methods_refuse_to_pretend(self):
        # A stub must never return a plausible-looking empty result.
        client = PaymentClientStub()
        with self.assertRaises(NotImplementedError):
            client.create_charge(100, "USD", "tok", "idem-1")
        client.close()


@FX_SETTINGS
class ConversionEndpointTests(TestCase):
    def setUp(self):
        cache.clear()
        self.api = APIClient()
        User.objects.create_user(email="kim@example.com", password=PASSWORD)
        tokens = self.api.post(
            reverse("accounts:login"),
            {"email": "kim@example.com", "password": PASSWORD},
        ).data
        self.api.credentials(HTTP_AUTHORIZATION=f"Bearer {tokens['access']}")
        self.url = reverse("integrations:convert")

    def tearDown(self):
        cache.clear()

    def test_endpoint_requires_authentication(self):
        anon = APIClient()
        response = anon.post(
            self.url,
            {"amount": "10", "source_currency": "USD", "target_currency": "EUR"},
        )
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    @responses.activate
    def test_returns_the_converted_amount(self):
        responses.add(responses.GET, LATEST_USD, json=OK_PAYLOAD, status=200)
        response = self.api.post(
            self.url,
            {"amount": "200", "source_currency": "usd", "target_currency": "inr"},
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertEqual(Decimal(str(response.data["converted"])), Decimal("16700.0000"))
        self.assertEqual(response.data["target_currency"], "INR")

    @responses.activate
    def test_upstream_failure_surfaces_as_502_not_500(self):
        responses.add(responses.GET, LATEST_USD, body=requests.Timeout("slow"))
        response = self.api.post(
            self.url,
            {"amount": "1", "source_currency": "USD", "target_currency": "EUR"},
        )
        self.assertEqual(response.status_code, status.HTTP_502_BAD_GATEWAY)
        self.assertEqual(response.data["error"]["type"], "upstream_error")

    def test_invalid_payload_is_a_400_with_field_errors(self):
        response = self.api.post(
            self.url,
            {"amount": "-5", "source_currency": "US", "target_currency": "EUR"},
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        detail = response.data["error"]["detail"]
        self.assertIn("amount", detail)
        self.assertIn("source_currency", detail)


class IntegrationListEndpointTests(TestCase):
    def setUp(self):
        self.api = APIClient()
        User.objects.create_user(email="liam@example.com", password=PASSWORD)
        tokens = self.api.post(
            reverse("accounts:login"),
            {"email": "liam@example.com", "password": PASSWORD},
        ).data
        self.api.credentials(HTTP_AUTHORIZATION=f"Bearer {tokens['access']}")

    def test_lists_every_registered_integration_and_flags_the_stubs(self):
        response = self.api.get(reverse("integrations:list"))
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)

        by_slug = {row["slug"]: row for row in response.data}
        self.assertIn("exchange-rates", by_slug)
        self.assertTrue(by_slug["exchange-rates"]["implemented"])
        for stub in ("payments", "crm", "notifications"):
            self.assertIn(stub, by_slug)
            self.assertFalse(
                by_slug[stub]["implemented"], f"{stub} is reported as implemented"
            )

    def test_unknown_slug_is_a_404(self):
        response = self.api.get(
            reverse("integrations:health", kwargs={"slug": "nope"})
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
