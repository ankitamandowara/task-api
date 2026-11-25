import django_filters

from .models import Task


class TaskFilter(django_filters.FilterSet):
    due_before = django_filters.DateFilter(field_name="due_date", lookup_expr="lte")
    due_after = django_filters.DateFilter(field_name="due_date", lookup_expr="gte")
    is_subtask = django_filters.BooleanFilter(field_name="parent", lookup_expr="isnull", exclude=True)

    class Meta:
        model = Task
        fields = ["completed", "priority", "due_date", "recurrence", "parent"]
