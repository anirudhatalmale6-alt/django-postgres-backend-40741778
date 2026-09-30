"""One predictable error envelope for the whole API.

DRF's default error body changes shape depending on what failed: a validation
error is a dict of lists, a permission error is `{"detail": "..."}`, a parse
error is something else again. The front-end then needs three code paths. This
handler normalises everything to:

    {
      "error": {
        "type": "validation_error",
        "message": "Invalid input.",
        "detail": {"email": ["This field is required."]},
        "request_id": "a1b2c3..."
      }
    }
"""

import logging

from django.core.exceptions import PermissionDenied as DjangoPermissionDenied
from django.http import Http404
from rest_framework import exceptions, status
from rest_framework.response import Response
from rest_framework.views import exception_handler as drf_exception_handler

from core.middleware import request_id_var

logger = logging.getLogger(__name__)

_TYPE_BY_STATUS = {
    400: "validation_error",
    401: "authentication_error",
    403: "permission_denied",
    404: "not_found",
    405: "method_not_allowed",
    409: "conflict",
    415: "unsupported_media_type",
    429: "throttled",
    501: "not_implemented",
    502: "upstream_error",
    503: "service_unavailable",
}

_MESSAGE_BY_STATUS = {
    400: "Invalid input.",
    401: "Authentication credentials were not valid.",
    403: "You do not have permission to perform this action.",
    404: "Not found.",
    405: "Method not allowed.",
    409: "Conflict with the current state of the resource.",
    415: "Unsupported media type.",
    429: "Request was throttled.",
    501: "Not implemented.",
    502: "An upstream service failed.",
    503: "Service temporarily unavailable.",
}


class UpstreamServiceError(exceptions.APIException):
    """An external API we depend on failed or timed out.

    Raised by the integrations layer. 502 (not 500) so monitoring can tell
    "their fault" apart from "our bug".
    """

    status_code = status.HTTP_502_BAD_GATEWAY
    default_detail = "An upstream service failed."
    default_code = "upstream_error"


def api_exception_handler(exc, context):
    if isinstance(exc, Http404):
        exc = exceptions.NotFound()
    elif isinstance(exc, DjangoPermissionDenied):
        exc = exceptions.PermissionDenied()

    response = drf_exception_handler(exc, context)

    if response is None:
        # Nothing DRF recognises — a genuine unhandled bug. Log the traceback
        # (with request id) and return an opaque 500; never leak internals.
        logger.exception("Unhandled exception in %s", context.get("view"))
        return Response(
            {
                "error": {
                    "type": "server_error",
                    "message": "An unexpected error occurred.",
                    "detail": None,
                    "request_id": request_id_var.get(),
                }
            },
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    code = response.status_code
    detail = response.data

    # Unwrap DRF's single-key {"detail": "..."} so `message` carries the text
    # and `detail` stays reserved for field-level errors.
    message = _MESSAGE_BY_STATUS.get(code, "Request failed.")
    if isinstance(detail, dict) and set(detail) == {"detail"}:
        message = str(detail["detail"])
        detail = None

    response.data = {
        "error": {
            "type": _TYPE_BY_STATUS.get(code, "error"),
            "message": message,
            "detail": detail,
            "request_id": request_id_var.get(),
        }
    }
    return response
