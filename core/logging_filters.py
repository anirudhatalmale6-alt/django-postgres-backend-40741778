import logging

from core.middleware import request_id_var


class RequestIDFilter(logging.Filter):
    """Make `%(request_id)s` usable in any formatter.

    Records emitted outside a request (management commands, startup) still get
    a value, so the formatter never blows up with a KeyError.
    """

    def filter(self, record):
        record.request_id = request_id_var.get()
        return True
