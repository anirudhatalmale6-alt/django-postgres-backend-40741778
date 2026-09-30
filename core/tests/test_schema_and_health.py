"""Checks on the parts that are easy to break without noticing.

The schema test is the one that matters most day to day: a mistyped
`@extend_schema`, a serializer without a resolvable type or two views sharing an
operation id all render Swagger unusable, and nothing else in the suite would
catch it.
"""

import json

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

User = get_user_model()
PASSWORD = "Tr0ubadour-Kettle-92"


class HealthEndpointTests(TestCase):
    def test_liveness_is_public_and_ok(self):
        response = self.client.get(reverse("core:live"))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.json()["status"], "ok")

    def test_readiness_reports_the_database(self):
        response = self.client.get(reverse("core:ready"))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.json()["database"], "ok")

    def test_every_response_carries_a_request_id(self):
        response = self.client.get(reverse("core:live"))
        self.assertTrue(response.headers.get("X-Request-ID"))

    def test_a_supplied_request_id_is_echoed_back(self):
        response = self.client.get(
            reverse("core:live"), headers={"x-request-id": "trace-me-123"}
        )
        self.assertEqual(response.headers["X-Request-ID"], "trace-me-123")


class SchemaTests(TestCase):
    def setUp(self):
        self.api = APIClient()

    def test_schema_generates_without_errors_or_warnings(self):
        # generate_schema raises on genuine errors; this also asserts there are
        # no spectacular warnings, because warnings are exactly what turn into
        # "Swagger renders but half the endpoints say 'string'".
        from drf_spectacular.generators import SchemaGenerator
        from drf_spectacular.settings import spectacular_settings
        from drf_spectacular.drainage import GENERATOR_STATS

        GENERATOR_STATS.reset()
        generator = SchemaGenerator()
        schema = generator.get_schema(request=None, public=True)

        self.assertTrue(schema["paths"], "schema contains no paths")
        self.assertFalse(
            GENERATOR_STATS._warn_cache,
            f"drf-spectacular emitted warnings: {list(GENERATOR_STATS._warn_cache)}",
        )
        self.assertFalse(
            GENERATOR_STATS._error_cache,
            f"drf-spectacular emitted errors: {list(GENERATOR_STATS._error_cache)}",
        )
        del spectacular_settings  # imported only to fail fast if misconfigured

    def test_schema_endpoint_serves_valid_openapi(self):
        response = self.api.get(reverse("schema"), headers={"accept": "application/json"})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        document = json.loads(response.content)
        self.assertEqual(document["openapi"][:2], "3.")
        # Spot-check that the auth endpoints made it into the document.
        for path in (
            "/api/v1/auth/register/",
            "/api/v1/auth/login/",
            "/api/v1/auth/password-reset/",
            "/api/v1/integrations/exchange-rates/convert/",
        ):
            self.assertIn(path, document["paths"], f"{path} missing from the schema")

    def test_swagger_ui_renders(self):
        response = self.api.get(reverse("swagger-ui"))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn(b"swagger", response.content.lower())

    def test_login_and_register_are_documented_as_public(self):
        response = self.api.get(reverse("schema"), headers={"accept": "application/json"})
        document = json.loads(response.content)
        for path in ("/api/v1/auth/login/", "/api/v1/auth/register/"):
            security = document["paths"][path]["post"].get("security")
            self.assertEqual(
                security, [{}], f"{path} is not documented as unauthenticated"
            )


class ErrorEnvelopeTests(TestCase):
    """The single error shape the front end is told to rely on."""

    def test_unauthenticated_request_uses_the_envelope(self):
        response = APIClient().get(reverse("accounts:me"))
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
        body = response.json()
        self.assertIn("error", body)
        self.assertEqual(body["error"]["type"], "authentication_error")
        self.assertTrue(body["error"]["message"])
        self.assertTrue(body["error"]["request_id"])

    def test_not_found_uses_the_envelope(self):
        api = APIClient()
        User.objects.create_user(email="mia@example.com", password=PASSWORD)
        tokens = api.post(
            reverse("accounts:login"),
            {"email": "mia@example.com", "password": PASSWORD},
        ).data
        api.credentials(HTTP_AUTHORIZATION=f"Bearer {tokens['access']}")
        response = api.get(reverse("integrations:health", kwargs={"slug": "ghost"}))
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(response.json()["error"]["type"], "not_found")

    def test_method_not_allowed_uses_the_envelope(self):
        response = APIClient().get(reverse("accounts:register"))
        self.assertEqual(response.status_code, status.HTTP_405_METHOD_NOT_ALLOWED)
        self.assertEqual(response.json()["error"]["type"], "method_not_allowed")


class SoftDeleteBaseModelTests(TestCase):
    """The abstract bases are exercised through a throwaway concrete model.

    Without this the soft-delete manager would be untested until the first real
    domain model used it, and a queryset-level .delete() that bypasses
    soft-delete is a data-loss bug.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        from django.db import connection

        from core.tests.models import SoftDeletableThing

        cls.model = SoftDeletableThing
        with connection.schema_editor() as editor:
            editor.create_model(SoftDeletableThing)

    @classmethod
    def tearDownClass(cls):
        from django.db import connection

        with connection.schema_editor() as editor:
            editor.delete_model(cls.model)
        super().tearDownClass()

    def test_instance_delete_is_soft(self):
        thing = self.model.objects.create(label="a")
        thing.delete()
        self.assertEqual(self.model.objects.count(), 0)
        self.assertEqual(self.model.all_objects.count(), 1)
        self.assertIsNotNone(self.model.all_objects.get().deleted_at)

    def test_queryset_delete_is_also_soft(self):
        self.model.objects.create(label="b")
        self.model.objects.create(label="c")
        self.model.objects.all().delete()
        self.assertEqual(self.model.objects.count(), 0)
        self.assertEqual(self.model.all_objects.count(), 2)

    def test_hard_delete_really_removes_the_row(self):
        thing = self.model.objects.create(label="d")
        thing.hard_delete()
        self.assertEqual(self.model.all_objects.count(), 0)

    def test_restore_brings_a_row_back(self):
        thing = self.model.objects.create(label="e")
        thing.delete()
        self.model.all_objects.get().restore()
        self.assertEqual(self.model.objects.count(), 1)

    def test_timestamps_and_uuid_pk_are_populated(self):
        import uuid

        thing = self.model.objects.create(label="f")
        self.assertIsInstance(thing.id, uuid.UUID)
        self.assertIsNotNone(thing.created_at)
        self.assertIsNotNone(thing.updated_at)
