from django.conf import settings
from django.contrib import admin
from django.urls import include, path
from drf_spectacular.views import (
    SpectacularAPIView,
    SpectacularRedocView,
    SpectacularSwaggerView,
)

API_V1 = "api/v1/"

urlpatterns = [
    path(settings.ADMIN_URL, admin.site.urls),
    # Health probes sit outside the versioned API so infrastructure never
    # depends on an API version.
    path("health/", include("core.urls")),
    # --- v1 -------------------------------------------------------------
    path(f"{API_V1}auth/", include("accounts.urls")),
    path(f"{API_V1}integrations/", include("integrations.urls")),
    # --- docs -----------------------------------------------------------
    path(f"{API_V1}schema/", SpectacularAPIView.as_view(), name="schema"),
    path(
        f"{API_V1}docs/",
        SpectacularSwaggerView.as_view(url_name="schema"),
        name="swagger-ui",
    ),
    path(
        f"{API_V1}redoc/",
        SpectacularRedocView.as_view(url_name="schema"),
        name="redoc",
    ),
]
