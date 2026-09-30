"""Integration-specific API exceptions.

These are raised rather than returned as plain `Response` objects on purpose:
only exceptions pass through DRF's exception handler, which is what wraps every
error in the single `{"error": {...}}` envelope the front end relies on. A
hand-built `Response(..., status=404)` silently escapes that envelope.
"""

from rest_framework import status
from rest_framework.exceptions import APIException


class IntegrationNotImplemented(APIException):
    """The integration is registered as a stub and has no implementation yet."""

    status_code = status.HTTP_501_NOT_IMPLEMENTED
    default_detail = "This integration has not been implemented yet."
    default_code = "not_implemented"


class IntegrationNotConfigured(APIException):
    """Implemented, but its URL or credentials are missing from the environment."""

    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    default_detail = "This integration is not configured on this environment."
    default_code = "not_configured"
