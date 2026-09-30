import logging

from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from integrations.clients.base import IntegrationConfigError
from integrations.clients.registry import available, get_client
from integrations.exceptions import (
    IntegrationNotConfigured,
    IntegrationNotImplemented,
)
from integrations.serializers import (
    CurrencyConversionSerializer,
    ConversionResultSerializer,
    IntegrationSerializer,
)

logger = logging.getLogger(__name__)

TAG = ["integrations"]


@extend_schema(
    tags=TAG,
    summary="List registered external integrations",
    description=(
        "Reports every integration the backend knows about and whether it is "
        "implemented and configured. Does not call the vendors."
    ),
    responses={200: IntegrationSerializer(many=True)},
)
class IntegrationListView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response(IntegrationSerializer(available(), many=True).data)


@extend_schema(
    tags=TAG,
    summary="Convert an amount between currencies",
    description=(
        "Reference implementation of the integration layer: calls the external "
        "exchange-rate provider through the shared HTTP client (timeout, "
        "bounded retries, 1-hour response cache). Returns 502 if the provider "
        "is unreachable or sends something unusable."
    ),
    request=CurrencyConversionSerializer,
    responses={
        200: ConversionResultSerializer,
        502: OpenApiResponse(description="The exchange-rate provider failed."),
    },
)
class CurrencyConversionView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = CurrencyConversionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        client = get_client("exchange-rates")
        try:
            result = client.convert(
                data["amount"], data["source_currency"], data["target_currency"]
            )
        except ValueError as exc:
            # A currency code the field validators let through. Raised (not
            # returned) so it gets the standard error envelope.
            raise ValidationError({"detail": str(exc)}) from exc
        finally:
            client.close()

        return Response(ConversionResultSerializer(result).data)


@extend_schema(
    tags=TAG,
    summary="Probe a single integration",
    description=(
        "Calls the vendor's cheapest endpoint and reports whether it answered. "
        "Live network call — use sparingly, not as a load-balancer health check."
    ),
    responses={
        200: OpenApiResponse(description="Probe result."),
        501: OpenApiResponse(description="Registered but not yet implemented."),
        503: OpenApiResponse(description="Registered but not configured here."),
    },
)
class IntegrationHealthView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, slug):
        try:
            client = get_client(slug)
        except KeyError as exc:
            raise NotFound(f"Unknown integration '{slug}'.") from exc
        except IntegrationConfigError as exc:
            # Missing URL/credentials is a deployment problem, reported as such
            # rather than as a vendor outage.
            raise IntegrationNotConfigured(str(exc)) from exc

        try:
            return Response(client.health_check())
        except NotImplementedError as exc:
            raise IntegrationNotImplemented(str(exc)) from exc
        finally:
            client.close()
