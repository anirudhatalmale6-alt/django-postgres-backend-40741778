from django.db import connections
from django.db.utils import OperationalError
from drf_spectacular.utils import OpenApiExample, OpenApiResponse, extend_schema
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView


@extend_schema(
    tags=["health"],
    summary="Liveness probe",
    description="Returns 200 as long as the process is up. Does not touch the database.",
    auth=[{}],
    responses={200: OpenApiResponse(description="Process is alive.")},
)
class LivenessView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []

    def get(self, request):
        return Response({"status": "ok"})


@extend_schema(
    tags=["health"],
    summary="Readiness probe",
    description=(
        "Verifies the process can actually serve traffic by executing a real "
        "query against PostgreSQL. Returns 503 when the database is unreachable "
        "so a load balancer stops sending traffic to this instance."
    ),
    auth=[{}],
    responses={
        200: OpenApiResponse(
            description="Ready.",
            examples=[OpenApiExample("ready", value={"status": "ok", "database": "ok"})],
        ),
        503: OpenApiResponse(description="A dependency is unavailable."),
    },
)
class ReadinessView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []

    def get(self, request):
        # A real round trip, not `connection.is_usable()` — a pooled handle can
        # look usable and still fail on the next statement.
        try:
            with connections["default"].cursor() as cursor:
                cursor.execute("SELECT 1")
                cursor.fetchone()
        except OperationalError as exc:
            return Response(
                {"status": "unavailable", "database": "error", "detail": str(exc)},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        return Response({"status": "ok", "database": "ok"})
