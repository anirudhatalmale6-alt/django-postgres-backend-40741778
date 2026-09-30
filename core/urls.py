from django.urls import path

from core.views import LivenessView, ReadinessView

app_name = "core"

urlpatterns = [
    path("live/", LivenessView.as_view(), name="live"),
    path("ready/", ReadinessView.as_view(), name="ready"),
]
