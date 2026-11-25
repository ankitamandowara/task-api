"""One consistent error shape for the whole API.

    {"error": {"code": "...", "message": "...", "details": [{"field": "...", "issue": "..."}]}}

`code` and `message` are always strings, `details` is always a list.
"""
from django.http import Http404
from rest_framework import exceptions
from rest_framework.response import Response
from rest_framework.views import exception_handler as drf_exception_handler


def error_response(code, message, details=None, status=400):
    return Response(
        {"error": {"code": str(code), "message": str(message), "details": list(details or [])}},
        status=status,
    )


def _friendly(message):
    """Turn DRF's default wording into short 'issue' phrases."""
    text = str(message)
    low = text.lower()
    if "may not be blank" in low:
        return "must not be empty"
    if "field is required" in low:
        return "is required"
    if "may not be null" in low:
        return "must not be null"
    if "valid boolean" in low:
        return "must be a boolean"
    if "wrong format" in low or "date has wrong" in low:
        return "must be a valid date in YYYY-MM-DD format"
    if "not a valid choice" in low:
        return "must be one of: low, medium, high"
    if "invalid data" in low and "dictionary" in low:
        return "request body must be a JSON object"
    return text.rstrip(".")


def errors_to_details(errors, prefix=""):
    """Flatten serializer.errors (dicts / lists / strings) into [{"field", "issue"}]."""
    details = []
    if isinstance(errors, dict):
        for field, value in errors.items():
            name = f"{prefix}.{field}" if prefix else str(field)
            details.extend(errors_to_details(value, name))
    elif isinstance(errors, (list, tuple)):
        for index, item in enumerate(errors):
            if isinstance(item, (dict, list, tuple)):
                name = f"{prefix}[{index}]" if prefix else f"[{index}]"
                details.extend(errors_to_details(item, name))
            else:
                details.append({"field": prefix or "non_field_errors", "issue": _friendly(item)})
    else:
        details.append({"field": prefix or "non_field_errors", "issue": _friendly(errors)})
    return details


def validation_error_response(errors, message="Validation failed", code="VALIDATION_ERROR", status=400, prefix=""):
    return error_response(code, message, errors_to_details(errors, prefix), status)


def not_found_response(message="Task not found"):
    return error_response("NOT_FOUND", message, [], 404)


def custom_exception_handler(exc, context):
    """Safety net: anything DRF raises on its own still comes out in our shape."""
    response = drf_exception_handler(exc, context)
    if response is None:
        return None  # real server errors stay as Django's 500

    if isinstance(exc, (Http404, exceptions.NotFound)):
        return not_found_response("Resource not found")
    if isinstance(exc, exceptions.ValidationError):
        return validation_error_response(exc.detail)
    if isinstance(exc, exceptions.ParseError):
        return error_response(
            "VALIDATION_ERROR", "Validation failed", [{"field": "body", "issue": "malformed JSON"}], 400
        )
    if isinstance(exc, exceptions.MethodNotAllowed):
        return error_response("METHOD_NOT_ALLOWED", "Method not allowed", [], 405)
    if isinstance(exc, exceptions.UnsupportedMediaType):
        return error_response("UNSUPPORTED_MEDIA_TYPE", "Unsupported media type", [], 415)
    return error_response("ERROR", str(getattr(exc, "detail", "Request failed")), [], response.status_code)
