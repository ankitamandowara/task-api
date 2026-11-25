from django.db import models


class Task(models.Model):
    PRIORITY_CHOICES = [
        ("low", "low"),
        ("medium", "medium"),
        ("high", "high"),
    ]
    RECURRENCE_CHOICES = [
        ("daily", "daily"),
        ("weekly", "weekly"),
        ("monthly", "monthly"),
        ("yearly", "yearly"),
    ]

    title = models.CharField(max_length=255)
    description = models.TextField()
    completed = models.BooleanField(default=False)
    due_date = models.DateField(null=True, blank=True)
    priority = models.CharField(max_length=10, choices=PRIORITY_CHOICES, default="medium")
    recurrence = models.CharField(max_length=10, choices=RECURRENCE_CHOICES, null=True, blank=True)
    # A subtask points at its parent. Deleting the parent deletes its subtasks.
    parent = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="subtasks",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["id"]

    def __str__(self):
        return self.title
