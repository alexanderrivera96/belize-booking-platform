"""Fail closed (OWASP A10:2025): unexpected errors return a generic message with a request id,
never a stack trace, and never fall through to an "allowed" branch."""
import logging
import uuid

from rest_framework.response import Response
from rest_framework.views import exception_handler

log = logging.getLogger("api.errors")


def fail_closed_handler(exc, context):
    response = exception_handler(exc, context)
    if response is not None:
        return response
    request_id = uuid.uuid4().hex[:12]
    log.exception("Unhandled API error %s", request_id, exc_info=exc)
    return Response({"detail": "Something went wrong.", "request_id": request_id}, status=500)
