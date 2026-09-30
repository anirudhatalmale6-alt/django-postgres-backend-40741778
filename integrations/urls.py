from django.urls import path

from integrations.views import (
    CurrencyConversionView,
    IntegrationHealthView,
    IntegrationListView,
)

app_name = "integrations"

urlpatterns = [
    path("", IntegrationListView.as_view(), name="list"),
    path("exchange-rates/convert/", CurrencyConversionView.as_view(), name="convert"),
    path("<slug:slug>/health/", IntegrationHealthView.as_view(), name="health"),
]
