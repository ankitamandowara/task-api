import re

from rest_framework import serializers

from .models import Task

ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class StrictBooleanField(serializers.BooleanField):
    """Only real JSON true/false. DRF's default also accepts "yes", 1, "true"..."""

    def to_internal_value(self, data):
        if isinstance(data, bool):
            return data
        self.fail("invalid", input=data)


class StrictDateField(serializers.DateField):
    """Only YYYY-MM-DD, and it has to be a real calendar date (2026-02-30 is rejected)."""

    def __init__(self, **kwargs):
        kwargs.setdefault("input_formats", ["%Y-%m-%d"])
        super().__init__(**kwargs)

    def to_internal_value(self, value):
        if not isinstance(value, str) or not ISO_DATE.match(value):
            self.fail("invalid", format="YYYY-MM-DD")
        return super().to_internal_value(value)


class TaskSerializer(serializers.ModelSerializer):
    completed = StrictBooleanField(default=False)
    due_date = StrictDateField(required=False, allow_null=True)

    class Meta:
        model = Task
        fields = [
            "id",
            "title",
            "description",
            "completed",
            "due_date",
            "priority",
            "recurrence",
            "parent",
            "created_at",
        ]
        read_only_fields = ["id", "parent", "created_at"]
