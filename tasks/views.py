from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from django.utils import timezone
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from . import idempotency
from .ai import break_down_task, parse_task_text
from .errors import errors_to_details, error_response, not_found_response, validation_error_response
from .filters import TaskFilter
from .models import Task
from .serializers import TaskSerializer


class TaskViewSet(viewsets.ModelViewSet):
    queryset = Task.objects.all()
    serializer_class = TaskSerializer
    filterset_class = TaskFilter
    search_fields = ["title", "description"]
    ordering_fields = ["id", "title", "due_date", "priority", "created_at"]
    ordering = ["id"]

    # ---- helpers -------------------------------------------------------------
    def _find_task(self, pk):
        try:
            return Task.objects.get(pk=pk)
        except (Task.DoesNotExist, ValueError, TypeError, DjangoValidationError, OverflowError):
            return None

    # ---- CRUD ----------------------------------------------------------------
    def create(self, request, *args, **kwargs):
        replay = idempotency.lookup(request)
        if replay is not None:
            return replay

        serializer = self.get_serializer(data=request.data)
        if not serializer.is_valid():
            return validation_error_response(serializer.errors)
        serializer.save()

        response = Response(serializer.data, status=201)
        idempotency.remember(request, response)
        return response

    def retrieve(self, request, *args, **kwargs):
        task = self._find_task(kwargs.get("pk"))
        if task is None:
            return not_found_response()
        return Response(self.get_serializer(task).data)

    def update(self, request, *args, **kwargs):
        partial = kwargs.pop("partial", False)
        task = self._find_task(kwargs.get("pk"))
        if task is None:
            return not_found_response()

        serializer = self.get_serializer(task, data=request.data, partial=partial)
        if not serializer.is_valid():
            return validation_error_response(serializer.errors)
        serializer.save()
        return Response(serializer.data)

    def destroy(self, request, *args, **kwargs):
        task = self._find_task(kwargs.get("pk"))
        if task is None:
            return not_found_response()
        task.delete()  # subtasks go with it (on_delete=CASCADE)
        return Response(status=204)

    # ---- natural language ------------------------------------------------------
    @action(detail=False, methods=["post"], url_path="from-text")
    def from_text(self, request):
        replay = idempotency.lookup(request)
        if replay is not None:
            return replay

        body = request.data if isinstance(request.data, dict) else {}
        text = body.get("text")
        if "text" not in body:
            return validation_error_response({"text": ["This field is required."]})
        if not isinstance(text, str):
            return error_response(
                "VALIDATION_ERROR", "Validation failed", [{"field": "text", "issue": "must be a string"}], 400
            )
        if not text.strip():
            return validation_error_response({"text": ["This field may not be blank."]})

        try:
            parsed = parse_task_text(text, timezone.localdate())
        except Exception:
            return error_response("PARSER_ERROR", "The text could not be turned into a task", [], 502)

        if not isinstance(parsed, dict):
            return error_response(
                "INVALID_PARSER_OUTPUT", "Parser returned something that is not a task", [], 502
            )

        # "Never trust the output": parsed data is validated exactly like user input.
        serializer = TaskSerializer(data=parsed)
        if not serializer.is_valid():
            return validation_error_response(
                serializer.errors,
                message="Parser output failed validation",
                code="INVALID_PARSER_OUTPUT",
                status=502,
            )
        serializer.save()

        response = Response(serializer.data, status=201)
        idempotency.remember(request, response)
        return response

    # ---- subtasks --------------------------------------------------------------
    @action(detail=True, methods=["get", "post"], url_path="subtasks")
    def subtasks(self, request, pk=None):
        task = self._find_task(pk)
        if task is None:
            return not_found_response()

        if request.method == "GET":
            data = TaskSerializer(task.subtasks.all(), many=True).data
            return Response(data)

        if task.parent_id is not None:
            return error_response(
                "SUBTASK_NOT_ALLOWED", "A subtask cannot have subtasks", [{"field": "id", "issue": "task is already a subtask"}], 400
            )
        serializer = TaskSerializer(data=request.data)
        if not serializer.is_valid():
            return validation_error_response(serializer.errors)
        serializer.save(parent=task)
        return Response(serializer.data, status=201)

    @action(detail=True, methods=["post"], url_path="breakdown")
    def breakdown(self, request, pk=None):
        replay = idempotency.lookup(request)
        if replay is not None:
            return replay

        task = self._find_task(pk)
        if task is None:
            return not_found_response()
        if task.parent_id is not None:
            return error_response(
                "SUBTASK_NOT_ALLOWED", "A subtask cannot be broken down", [{"field": "id", "issue": "task is already a subtask"}], 400
            )

        try:
            items = break_down_task(task)
        except Exception:
            return error_response("BREAKDOWN_ERROR", "The task could not be broken down", [], 502)
        if not isinstance(items, list) or not items:
            return error_response("INVALID_BREAKDOWN_OUTPUT", "Breakdown returned no usable subtasks", [], 502)

        serializers_ = [TaskSerializer(data=item) for item in items]
        problems = []
        for index, serializer in enumerate(serializers_):
            if not serializer.is_valid():
                problems.extend(errors_to_details(serializer.errors, f"items[{index}]"))
        if problems:  # one bad item -> save none
            return error_response("INVALID_BREAKDOWN_OUTPUT", "Breakdown output failed validation", problems, 502)

        with transaction.atomic():
            for serializer in serializers_:
                serializer.save(parent=task)

        response = Response([s.data for s in serializers_], status=201)
        idempotency.remember(request, response)
        return response
