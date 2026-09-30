"""Proof that the rate limits are actually wired up.

A throttle that is configured but never attached to a view is invisible until
someone brute-forces the login endpoint, so it gets its own test.

These run against the REAL configured rates rather than tightened test-only
ones. That is deliberate twice over:

1. `SimpleRateThrottle.THROTTLE_RATES` is bound as a class attribute when
   `rest_framework.throttling` is first imported, so
   `override_settings(REST_FRAMEWORK=...)` does NOT change the rate actually in
   force. An earlier version of this file overrode the rates to 3/min, saw five
   401s and looked like it proved throttling was broken — it was measuring
   something that could not respond.
2. Testing the production numbers means the test also fails if someone later
   loosens a rate to something useless.

The limits are read from settings, so tightening a rate does not require
touching this file.
"""

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

User = get_user_model()

PASSWORD = "Tr0ubadour-Kettle-92"

RATES = settings.REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]


def limit_for(scope: str) -> int:
    """Number of requests the configured rate allows before a 429."""
    return int(RATES[scope].split("/")[0])


# Throttle history lives in the cache. The auth-flow suites swap in a dummy
# cache to switch throttling off; this one needs a real cache back.
REAL_CACHE = override_settings(
    CACHES={
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "throttle-tests",
        }
    }
)


@REAL_CACHE
class ThrottleTests(TestCase):
    def setUp(self):
        cache.clear()
        self.client = APIClient()
        User.objects.create_user(email="ivan@example.com", password=PASSWORD)

    def tearDown(self):
        cache.clear()

    def test_login_is_throttled_at_the_configured_limit(self):
        limit = limit_for("auth")
        self.assertLessEqual(
            limit, 30, f"the auth rate {RATES['auth']} is too loose to be useful"
        )

        url = reverse("accounts:login")
        statuses = [
            self.client.post(
                url, {"email": "ivan@example.com", "password": "wrong-password"}
            ).status_code
            for _ in range(limit + 2)
        ]

        # Everything up to the limit is let through (and rejected on
        # credentials); everything after it is refused by the throttle.
        self.assertEqual(
            statuses[:limit],
            [status.HTTP_401_UNAUTHORIZED] * limit,
            f"a request was blocked before the limit of {limit}: {statuses}",
        )
        self.assertEqual(
            statuses[limit:],
            [status.HTTP_429_TOO_MANY_REQUESTS] * 2,
            f"login was not throttled after {limit} attempts: {statuses}",
        )

    def test_throttled_response_uses_the_error_envelope(self):
        url = reverse("accounts:login")
        for _ in range(limit_for("auth") + 1):
            response = self.client.post(
                url, {"email": "ivan@example.com", "password": "wrong-password"}
            )
        self.assertEqual(response.status_code, status.HTTP_429_TOO_MANY_REQUESTS)
        self.assertEqual(response.data["error"]["type"], "throttled")

    def test_password_reset_is_throttled_at_the_configured_limit(self):
        limit = limit_for("password_reset")
        url = reverse("accounts:password-reset")
        statuses = [
            self.client.post(url, {"email": "ivan@example.com"}).status_code
            for _ in range(limit + 2)
        ]
        self.assertEqual(statuses[:limit], [status.HTTP_200_OK] * limit, statuses)
        self.assertEqual(
            statuses[limit:],
            [status.HTTP_429_TOO_MANY_REQUESTS] * 2,
            f"password reset was not throttled after {limit} attempts: {statuses}",
        )

    def test_throttles_are_per_scope_not_global(self):
        """Exhausting the login scope must not lock out registration."""
        login_url = reverse("accounts:login")
        for _ in range(limit_for("auth") + 1):
            self.client.post(
                login_url, {"email": "ivan@example.com", "password": "wrong-password"}
            )

        response = self.client.post(
            reverse("accounts:register"),
            {
                "email": "judy@example.com",
                "password": "Cantilever-Mango-4417",
                "password_confirm": "Cantilever-Mango-4417",
            },
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)

    def test_throttling_is_off_when_the_cache_is_a_dummy(self):
        """Documents why the other suites can disable throttling the way they do.

        If this ever starts throttling, the `NO_THROTTLE` trick in
        test_auth_flow.py has stopped working and those suites will go red with
        unexplained 429s.
        """
        url = reverse("accounts:login")
        with override_settings(
            CACHES={
                "default": {"BACKEND": "django.core.cache.backends.dummy.DummyCache"}
            }
        ):
            statuses = [
                self.client.post(
                    url, {"email": "ivan@example.com", "password": "wrong-password"}
                ).status_code
                for _ in range(limit_for("auth") + 2)
            ]
        self.assertNotIn(status.HTTP_429_TOO_MANY_REQUESTS, statuses)
