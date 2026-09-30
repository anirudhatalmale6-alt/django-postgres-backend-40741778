"""Request-scoped plumbing shared by every app."""

import contextvars
import uuid

# Holds the current request's id so the logging filter can reach it without
# threading a logger through every function signature.
request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar(
    "request_id", default="-"
)

HEADER = "HTTP_X_REQUEST_ID"
RESPONSE_HEADER = "X-Request-ID"


class RequestIDMiddleware:
    """Attach a request id to every request, log line and response.

    If the caller (or an upstream proxy) already supplied X-Request-ID we keep
    it, so a front-end bug report can be traced straight to the server log.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        incoming = request.META.get(HEADER, "")
        # Never trust an arbitrary-length client header into our log lines.
        request_id = incoming[:64] if incoming else uuid.uuid4().hex[:16]
        request.request_id = request_id
        token = request_id_var.set(request_id)
        try:
            response = self.get_response(request)
        finally:
            request_id_var.reset(token)
        response[RESPONSE_HEADER] = request_id
        return response
