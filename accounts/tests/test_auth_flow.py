"""End-to-end tests for the authentication flow.

These hit the real URLs through DRF's test client, so a broken route or a
serializer rename fails the test rather than passing on a mocked stand-in.
"""

import re

from django.contrib.auth import get_user_model
from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

User = get_user_model()

VALID_PASSWORD = "Tr0ubadour-Kettle-92"
OTHER_PASSWORD = "Gl4cier-Pumpkin-Rail-77"

# DRF keeps throttle history in the cache, so a dummy cache means nothing is
# ever remembered and throttling cannot fire. These suites fire many auth
# requests in a row and would otherwise start failing with 429 for reasons
# unrelated to what they assert. Throttling itself is covered by
# test_throttling.py, which switches the cache back on.
NO_THROTTLE = override_settings(
    CACHES={"default": {"BACKEND": "django.core.cache.backends.dummy.DummyCache"}},
)


@NO_THROTTLE
class RegistrationTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.url = reverse("accounts:register")

    def test_registers_user_and_hashes_password(self):
        response = self.client.post(
            self.url,
            {
                "email": "Alice@Example.COM",
                "username": "alice",
                "password": VALID_PASSWORD,
                "password_confirm": VALID_PASSWORD,
            },
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)

        user = User.objects.get(username="alice")
        # Email must be normalised to lowercase on the way in.
        self.assertEqual(user.email, "alice@example.com")
        # The stored value must be a hash, and must verify.
        self.assertNotEqual(user.password, VALID_PASSWORD)
        self.assertTrue(user.password.startswith("argon2$"))
        self.assertTrue(user.check_password(VALID_PASSWORD))
        # The response must never echo the password or its hash.
        self.assertNotIn("password", response.data)

    def test_rejects_duplicate_email_case_insensitively(self):
        User.objects.create_user(email="bob@example.com", password=VALID_PASSWORD)
        response = self.client.post(
            self.url,
            {
                "email": "BOB@EXAMPLE.COM",
                "password": VALID_PASSWORD,
                "password_confirm": VALID_PASSWORD,
            },
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("email", response.data["error"]["detail"])
        self.assertEqual(User.objects.count(), 1)

    def test_rejects_mismatched_confirmation(self):
        response = self.client.post(
            self.url,
            {
                "email": "carol@example.com",
                "password": VALID_PASSWORD,
                "password_confirm": OTHER_PASSWORD,
            },
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("password_confirm", response.data["error"]["detail"])
        self.assertFalse(User.objects.exists())

    def test_rejects_weak_password(self):
        for weak in ("short1A", "password", "12345678901"):
            with self.subTest(password=weak):
                response = self.client.post(
                    self.url,
                    {
                        "email": f"weak-{weak}@example.com",
                        "password": weak,
                        "password_confirm": weak,
                    },
                )
                self.assertEqual(
                    response.status_code,
                    status.HTTP_400_BAD_REQUEST,
                    f"{weak!r} was accepted",
                )
        self.assertFalse(User.objects.exists())


@NO_THROTTLE
class LoginTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.url = reverse("accounts:login")
        self.user = User.objects.create_user(
            email="dave@example.com", password=VALID_PASSWORD
        )

    def test_login_returns_token_pair_and_user(self):
        response = self.client.post(
            self.url, {"email": "dave@example.com", "password": VALID_PASSWORD}
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertIn("access", response.data)
        self.assertIn("refresh", response.data)
        self.assertEqual(response.data["user"]["email"], "dave@example.com")

    def test_login_is_case_insensitive_on_email(self):
        response = self.client.post(
            self.url, {"email": "DAVE@Example.com", "password": VALID_PASSWORD}
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)

    def test_wrong_password_is_rejected(self):
        response = self.client.post(
            self.url, {"email": "dave@example.com", "password": OTHER_PASSWORD}
        )
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
        self.assertNotIn("access", response.data)

    def test_inactive_user_cannot_log_in(self):
        self.user.is_active = False
        self.user.save(update_fields=["is_active"])
        response = self.client.post(
            self.url, {"email": "dave@example.com", "password": VALID_PASSWORD}
        )
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_access_token_reaches_a_protected_endpoint(self):
        tokens = self.client.post(
            self.url, {"email": "dave@example.com", "password": VALID_PASSWORD}
        ).data
        me = reverse("accounts:me")

        # Negative control: without the header the same endpoint must refuse.
        self.assertEqual(
            self.client.get(me).status_code, status.HTTP_401_UNAUTHORIZED
        )

        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {tokens['access']}")
        response = self.client.get(me)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["email"], "dave@example.com")

    def test_garbage_token_is_rejected(self):
        self.client.credentials(HTTP_AUTHORIZATION="Bearer not-a-real-token")
        response = self.client.get(reverse("accounts:me"))
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)


@NO_THROTTLE
class RefreshAndLogoutTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        User.objects.create_user(email="erin@example.com", password=VALID_PASSWORD)
        self.tokens = self.client.post(
            reverse("accounts:login"),
            {"email": "erin@example.com", "password": VALID_PASSWORD},
        ).data

    def test_refresh_returns_a_new_pair_and_retires_the_old_refresh(self):
        url = reverse("accounts:token-refresh")
        first = self.client.post(url, {"refresh": self.tokens["refresh"]})
        self.assertEqual(first.status_code, status.HTTP_200_OK, first.data)
        self.assertIn("access", first.data)
        # ROTATE_REFRESH_TOKENS + BLACKLIST_AFTER_ROTATION: the original refresh
        # token must be single-use.
        replay = self.client.post(url, {"refresh": self.tokens["refresh"]})
        self.assertEqual(replay.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_logout_blacklists_the_refresh_token(self):
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {self.tokens['access']}")
        response = self.client.post(
            reverse("accounts:logout"), {"refresh": self.tokens["refresh"]}
        )
        self.assertEqual(response.status_code, status.HTTP_205_RESET_CONTENT)

        refreshed = self.client.post(
            reverse("accounts:token-refresh"), {"refresh": self.tokens["refresh"]}
        )
        self.assertEqual(refreshed.status_code, status.HTTP_401_UNAUTHORIZED)


@NO_THROTTLE
class ChangePasswordTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(
            email="frank@example.com", password=VALID_PASSWORD
        )
        tokens = self.client.post(
            reverse("accounts:login"),
            {"email": "frank@example.com", "password": VALID_PASSWORD},
        ).data
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {tokens['access']}")
        self.url = reverse("accounts:password-change")

    def test_changes_password(self):
        response = self.client.post(
            self.url,
            {"current_password": VALID_PASSWORD, "new_password": OTHER_PASSWORD},
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(OTHER_PASSWORD))
        self.assertFalse(self.user.check_password(VALID_PASSWORD))

    def test_wrong_current_password_leaves_password_untouched(self):
        response = self.client.post(
            self.url,
            {"current_password": "definitely-not-it", "new_password": OTHER_PASSWORD},
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(VALID_PASSWORD))

    def test_new_password_must_differ(self):
        response = self.client.post(
            self.url,
            {"current_password": VALID_PASSWORD, "new_password": VALID_PASSWORD},
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)


@NO_THROTTLE
@override_settings(
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    FRONTEND_PASSWORD_RESET_URL="http://frontend.test/reset-password",
)
class PasswordResetTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(
            email="grace@example.com", password=VALID_PASSWORD
        )
        self.request_url = reverse("accounts:password-reset")
        self.confirm_url = reverse("accounts:password-reset-confirm")
        mail.outbox.clear()

    def _request_reset(self, email="grace@example.com"):
        return self.client.post(self.request_url, {"email": email})

    @staticmethod
    def _extract_credentials(body: str):
        match = re.search(r"\?uid=([^&\s]+)&token=([^\s]+)", body)
        assert match, f"no reset link found in email body:\n{body}"
        return match.group(1), match.group(2)

    def test_request_sends_email_with_a_link(self):
        response = self._request_reset()
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["grace@example.com"])
        self.assertIn("http://frontend.test/reset-password?uid=", mail.outbox[0].body)

    def test_unknown_address_gives_an_identical_response_and_sends_nothing(self):
        known = self._request_reset()
        mail.outbox.clear()
        unknown = self._request_reset("nobody@example.com")

        # Same status AND same body — any difference is a user-enumeration oracle.
        self.assertEqual(known.status_code, unknown.status_code)
        self.assertEqual(known.data, unknown.data)
        self.assertEqual(len(mail.outbox), 0)

    def test_full_reset_cycle_lets_the_user_log_in_with_the_new_password(self):
        self._request_reset()
        uid, token = self._extract_credentials(mail.outbox[0].body)

        response = self.client.post(
            self.confirm_url,
            {"uid": uid, "token": token, "new_password": OTHER_PASSWORD},
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)

        # The real check is not "the hash changed" but "the user can log in".
        login = self.client.post(
            reverse("accounts:login"),
            {"email": "grace@example.com", "password": OTHER_PASSWORD},
        )
        self.assertEqual(login.status_code, status.HTTP_200_OK, login.data)

        stale = self.client.post(
            reverse("accounts:login"),
            {"email": "grace@example.com", "password": VALID_PASSWORD},
        )
        self.assertEqual(stale.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_token_cannot_be_reused(self):
        self._request_reset()
        uid, token = self._extract_credentials(mail.outbox[0].body)
        self.client.post(
            self.confirm_url,
            {"uid": uid, "token": token, "new_password": OTHER_PASSWORD},
        )
        replay = self.client.post(
            self.confirm_url,
            {"uid": uid, "token": token, "new_password": "Yet-An0ther-Passphrase-31"},
        )
        self.assertEqual(replay.status_code, status.HTTP_400_BAD_REQUEST)
        # And the password set by the first (legitimate) use must survive.
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(OTHER_PASSWORD))

    def test_tampered_token_is_rejected(self):
        self._request_reset()
        uid, token = self._extract_credentials(mail.outbox[0].body)
        response = self.client.post(
            self.confirm_url,
            {"uid": uid, "token": token[:-1] + "x", "new_password": OTHER_PASSWORD},
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(VALID_PASSWORD))

    @override_settings(PASSWORD_RESET_TIMEOUT=-1)
    def test_expired_token_is_rejected(self):
        # Django compares `elapsed > PASSWORD_RESET_TIMEOUT`, so a timeout of 0
        # would still pass at elapsed=0 within the same second. -1 makes the
        # token unambiguously stale the moment it is issued.
        self._request_reset()
        uid, token = self._extract_credentials(mail.outbox[0].body)
        response = self.client.post(
            self.confirm_url,
            {"uid": uid, "token": token, "new_password": OTHER_PASSWORD},
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)


@NO_THROTTLE
class ProfileTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        User.objects.create_user(email="heidi@example.com", password=VALID_PASSWORD)
        tokens = self.client.post(
            reverse("accounts:login"),
            {"email": "heidi@example.com", "password": VALID_PASSWORD},
        ).data
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {tokens['access']}")

    def test_patch_updates_name_but_cannot_change_email(self):
        response = self.client.patch(
            reverse("accounts:me"),
            {"first_name": "Heidi", "email": "attacker@example.com"},
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        user = User.objects.get()
        self.assertEqual(user.first_name, "Heidi")
        self.assertEqual(user.email, "heidi@example.com")
